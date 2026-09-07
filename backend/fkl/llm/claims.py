"""L2 — claim extraction. The only place in the system where the model decides.

The model turns a page into typed claims carrying their context. It never
decides whether two claims agree; that is a deterministic step downstream. The
division matters because "do these contradict?" is a question a language model
will always answer, confidently, whether or not the two things are comparable.

What the model is shown is not the raw PDF text. It is the page after layout
analysis: reading order restored, table rows rebuilt from separately drawn
cells, headings marked, and the context in force stated inline with the wording
it was read from. A financial table reaches the model as

    [CONTEXT IN FORCE] consolidation=consolidated (from "Consolidated financial
    performance"); currency=INR (from "(₹ in Million)"); scale=million (...);
    not stated anywhere in scope: period
    Particulars | March 31, 2024 | March 31, 2023
    Revenue from contracts with customers | 81,415.38 | 72,253.01

rather than as a column of loose numbers with their labels elsewhere on the
page. The context lines are evidence, not instructions: they quote what the
document said and where, so the model can override them from local text — and
the axes named as unstated are what it should report as unknown rather than
guess.
"""

from __future__ import annotations

import logging

from ..config import SETTINGS
from ..schemas import PageExtraction
from .client import structured

log = logging.getLogger(__name__)

MAX_PAGE_CHARS = 14_000
MIN_PAGE_CHARS = 60


SYSTEM = """You extract facts from one page of a document, for a system that \
later decides whether facts from different sources agree.

Extract every specific, checkable assertion on the page: numbers with units and \
periods, and states that hold over time such as roles, addresses and identifiers.

What matters most is CONTEXT, not coverage. A number without its unit, period \
and basis is worthless downstream — worse than worthless, because it will be \
compared against something it is not comparable to. Fifteen claims with their \
context intact are far better than forty bare numbers.

HOW THE PAGE IS PRESENTED

The page has been laid out for you, not given to you raw:

- Lines beginning `[CONTEXT IN FORCE]` state the context declared by the \
section you are inside, quoting the exact wording it was read from. This is \
evidence, not instruction. Use it when the page gives you nothing closer, and \
override it when the local text says otherwise — a sentence saying "on a \
standalone basis" beats an inherited "consolidated" every time.
- A `[CONTEXT IN FORCE]` line saying "no section-level declaration for: X" \
means no heading or note declared X. It does NOT mean X is unknown. Look for X \
in the sentence or the table column first. Only if X is stated nowhere on the \
page does it belong in unknown_qualifiers.
- Lines beginning with `#` are headings; more hashes means deeper.
- Cells within one table row are separated by ` | `. The row above a run of \
such lines is usually the column header, and it usually carries the period.

RULES

- Copy the document's own wording for the predicate. Do not translate it into a \
standard metric name.
- When something material is genuinely not on the page, name that axis in \
unknown_qualifiers. An unstated consolidation basis is itself a fact about the \
page, and the system needs it. Do not guess, and do not omit.
- Never put the same axis in both qualifiers and unknown_qualifiers. If you \
found a value for it, it is known — even when no section declared it.
- evidence_quote must be copied exactly from the page text as shown. When you \
quote a table row, copy the WHOLE row including every cell and separator, even \
cells you did not use — a row with cells silently removed is not a quotation of \
it. The quote is verified against the source document, and the claim is \
discarded if the value is not inside it.
- Lower confidence_extraction and say why when a table's columns do not line \
up, when a value sits far from its label, or when the text order looks \
scrambled. Flagging a doubtful read is more useful than a confident wrong one.
- Skip page furniture: running headers and footers, page numbers, contents \
lists, and marketing statements with nothing measurable in them.

DATES IN ROLE AND STATUS CLAIMS

These documents record every appointment and departure inside a parenthetical beside the role. Those dates are the claim's interval and they must be parsed out, never left sitting in value_text:

    Mr. Sunil Kumar Bansal   Company Secretary (Resigned w.e.f. May 31, 2023)
    Mr. Vivek Kumar          Company Secretary (w.e.f June 01, 2023 resigned
                             w.e.f. March 27, 2024)
    Mrs. Madhulika Rawat     Company Secretary (w.e.f. May 17, 2024)

gives three state claims with value_text "Company Secretary" and:

    valid_from null,       valid_to 2023-05-31, valid_to_is_open false
    valid_from 2023-06-01, valid_to 2024-03-27, valid_to_is_open false
    valid_from 2024-05-17, valid_to null,       valid_to_is_open true

Read together those say one person succeeded another cleanly, and that the role then sat vacant for 51 days. A resignation date left inside value_text cannot close an interval, so every succession and every vacancy in the document becomes invisible. "w.e.f." means with effect from; "till", "upto" and "ceased" mark an ending.

Give the role as printed but WITHOUT the dates. Where one line records two roles ("Head - New Ventures (w.e.f. August 02 2021) and Chief People Officer (w.e.f. January 15, 2024)"), emit one claim per role with its own dates.

THE MISTAKE THAT MATTERS MOST

A page whose context line says "no section-level declaration for: \
consolidation" contains these two sentences:

    The revenue from operations on standalone basis for FY24 stood at
    Rs 74,540.82 million as against Rs 66,586.61 million for FY23.
    The revenue from operations on consolidated basis for FY24 stood at
    Rs 81,415.38 million as against Rs 72,253.01 million for FY23.

The correct extraction gives the first claim qualifiers \
{"consolidation": "standalone"} and the second {"consolidation": \
"consolidated"}, with consolidation in NEITHER unknown_qualifiers list. The \
words "on standalone basis" and "on consolidated basis" ARE the declaration for \
those claims, and they outrank the absence of a section-level one.

Calling consolidation unknown here is the most damaging error available to \
you. The two figures differ by 8%, and the basis is the only thing that \
explains why. Without it the system cannot tell a difference that reconciles \
from a genuine contradiction.

If the page has no extractable facts, return empty lists and say why in \
page_notes."""


def _context_header(profile: dict | None, page_no: int) -> str:
    """Document-level context handed to the extractor along with the page.

    Only what the profiler was confident enough to assert. A null default is
    passed through as null rather than filled with a plausible guess: an
    invented default is indistinguishable from a stated one once it has been
    written into a claim.
    """
    if not profile:
        return f"[PAGE {page_no}] Document context: unknown (document not profiled)."

    fields = [
        ("document type", profile.get("doc_type")),
        ("publisher", profile.get("publisher")),
        ("primary entity", profile.get("primary_entity")),
        ("document date (assertion time)", profile.get("as_of_date")),
        ("reporting period", profile.get("reporting_period")),
        ("default currency", profile.get("default_currency")),
        ("default scale", profile.get("default_scale")),
        ("default consolidation", profile.get("default_consolidation")),
    ]
    lines = [f"- {k}: {v}" for k, v in fields if v]
    unknown = [k for k, v in fields if not v]
    body = "\n".join(lines) if lines else "- (nothing established)"
    tail = (
        f"\nNot established at document level: {', '.join(unknown)}. "
        "Do not assume these; read them from the page or record them as unknown."
        if unknown
        else ""
    )
    return f"[PAGE {page_no}] Document context:\n{body}{tail}"


def extract_page(
    page_text: str,
    page_no: int,
    profile: dict | None = None,
    model: str | None = None,
) -> PageExtraction:
    """Extract claims from one page. Returns empty for pages with no real text."""
    text = page_text.strip()
    if len(text) < MIN_PAGE_CHARS:
        return PageExtraction(page_notes="page has too little text to extract from")

    if len(text) > MAX_PAGE_CHARS:
        log.warning("page %s truncated from %s to %s chars", page_no, len(text), MAX_PAGE_CHARS)
        text = text[:MAX_PAGE_CHARS]

    user = f"{_context_header(profile, page_no)}\n\n[PAGE TEXT]\n{text}"
    return structured(
        response_model=PageExtraction,
        system=SYSTEM,
        user=user,
        model=model or SETTINGS.model_extract,
    )
