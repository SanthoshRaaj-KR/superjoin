"""L2 — claim extraction. The only place in the system where the model decides.

The model turns a page into typed claims carrying their context. It never
decides whether two claims agree; that is a deterministic step downstream. The
division matters because "do these contradict?" is a question a language model
will always answer, confidently, whether or not the two things are comparable.

At this phase the unit of extraction is a single page in isolation. That is a
known weakness, not an oversight. Units and bases in these documents are
declared at section scope — an annual report page says
``(All amounts in Indian Rupees in million)`` once, in a header, and then prints
bare numbers for pages afterwards. Page-at-a-time extraction cannot see that,
so a fraction of the claims here will carry an unknown unit. The context tree
that fixes it is the next phase, and the gap between the two runs is the
argument for building it.
"""

from __future__ import annotations

import logging

from ..config import SETTINGS
from ..schemas import PageExtraction
from .client import structured

log = logging.getLogger(__name__)

MAX_PAGE_CHARS = 12_000
MIN_PAGE_CHARS = 60


SYSTEM = """You extract facts from one page of a document, for a system that \
later decides whether facts from different sources agree.

Extract every specific, checkable assertion on the page: numbers with units and \
periods, and states that hold over time such as roles, addresses and identifiers.

What matters most is CONTEXT, not coverage. A number without its unit, period \
and basis is worthless downstream — worse than worthless, because it will be \
compared against something it is not comparable to. Getting fifteen claims right \
with their context intact is far better than getting forty with bare numbers.

So:

- Copy the document's own wording for the predicate. Do not translate it into a \
standard metric name.
- Read units, periods, currencies and bases from wherever the page declares \
them: column headers, table titles, section notes, parenthetical remarks at the \
top of the page. They are usually not next to the number.
- When something that matters is genuinely not on the page, name that axis in \
unknown_qualifiers. Do not guess it, and do not omit it. An unstated \
consolidation basis is a fact about the page, and the system needs it.
- evidence_quote must be an exact substring of the page text. It is verified \
against the source, and a claim whose quote does not match is discarded.
- Lower confidence_extraction and say why when a table's columns do not line up, \
when a value is far from its label, or when the text order looks scrambled. \
Flagging a doubtful read is more useful than a confident wrong one.
- Skip page furniture: headers, footers, page numbers, contents lists, \
boilerplate, marketing statements with no measurable content.

If the page has no extractable facts, return empty lists and say why in \
page_notes."""


def _context_header(profile: dict | None, page_no: int) -> str:
    """Document-level context handed to the extractor along with the page.

    Only what the profiler was confident enough to assert. A null default is
    passed through as null rather than filled with a plausible guess: an
    invented default is indistinguishable from a stated one once it is written
    into a claim.
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
