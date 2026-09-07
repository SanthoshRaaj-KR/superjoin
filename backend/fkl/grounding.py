"""L3 — the grounding validator.

Every claim has to prove itself against the page it says it came from. Two
things are checked, in order:

1. The evidence quote can be located in the source text of the cited page.
2. The claimed value actually appears inside that quote.

The second check is the one that catches the failure that matters. A model
asked for a quote will nearly always produce a plausible one, and a plausible
quote next to a hallucinated number reads exactly like a correct extraction.
Requiring the value to sit *inside* the located span makes the quote do real
work instead of decorating the claim.

Matching is tiered, and which tier succeeded is recorded rather than discarded:

    exact          the quote is in the raw page text, character for character
    normalized     it matches once whitespace, dashes and quote marks are folded
    dehyphenated   it matches once end-of-line hyphenation is joined up
    reconstructed  it matches only the layout-rebuilt rendering of the page

That last tier is a real distinction, not a technicality. A table row like
``Revenue from contracts with customers | 81,415.38 | 72,253.01`` never appears
in the PDF as a contiguous string — the label and the two figures are drawn
separately and this project reassembled them. The words are genuinely on the
page, so the claim is not rejected, but the evidence was assembled by our own
layout code rather than found, and it is scored lower and labelled as such.

Failures are quarantined with a reason code, never dropped. The quarantine is
what turns extraction quality into a number instead of an impression, and it is
where the assignment's required "extraction failure" case will be read from.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Characters folded together before matching. Confined to marks that PDF
# typesetting varies freely: dash width, quote curliness, and the invisible
# characters used for line breaking. Digits, letters and currency symbols are
# never folded — those carry the meaning being checked.
_FOLD = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", "‘": "'", "’": "'", "‚": "'",
    "“": '"', "”": '"', "„": '"', " ": " ", " ": " ",
    " ": " ", "​": "", "‌": "", "‍": "", "­": "",
    "﻿": "",
}

# Below this, a quote is a restatement of the value rather than evidence for it.
MIN_QUOTE_CHARS = 8

# Scores per tier. Not confidence in the claim — confidence that the evidence
# for it is real.
_TIER_SCORE = {
    "exact": 1.0,
    "normalized": 0.95,
    "dehyphenated": 0.9,
    "reconstructed": 0.75,
}

# Grounding failure reasons. These are the vocabulary of the quarantine view.
REASON_EMPTY_QUOTE = "empty_quote"
REASON_QUOTE_NOT_FOUND = "quote_not_found"
REASON_VALUE_NOT_IN_QUOTE = "value_not_in_quote"
REASON_MISSING_VALUE = "missing_value"


@dataclass
class GroundingResult:
    ok: bool
    score: float
    method: str
    reason_code: str | None = None
    detail: str | None = None
    start: int | None = None
    end: int | None = None
    notes: list[str] | None = None


def _fold(text: str) -> tuple[str, list[int]]:
    """Fold text for matching, keeping a map back to original offsets.

    The map is what makes highlighting possible later: a span found in the
    folded text can be reported as a span in the real page text, so the UI can
    point at the actual characters rather than an approximation of them.
    """
    out: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(unicodedata.normalize("NFKC", text)):
        folded = _FOLD.get(ch, ch)
        if folded == "":
            continue
        if folded.isspace():
            if prev_space:
                continue
            folded, prev_space = " ", True
        else:
            prev_space = False
        out.append(folded)
        index.append(i)
    return "".join(out), index


_HYPHEN_BREAK = re.compile(r"(\w)-\s+(\w)")


def _dehyphenate(text: str) -> str:
    """Join words split across a line break: ``improve- ment`` -> ``improvement``."""
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def _digits_only(text: str) -> str:
    return re.sub(r"[^\d]", "", text)


def locate(quote: str, page_text: str, rendered_text: str | None = None) -> GroundingResult:
    """Find a quote in a page, trying successively more forgiving matches."""
    if not quote or not quote.strip():
        return GroundingResult(False, 0.0, "none", REASON_EMPTY_QUOTE)

    folded_quote, _ = _fold(quote)
    folded_quote = folded_quote.strip()

    # Tier 1 and 2: the raw page, exactly and then folded.
    if quote in page_text:
        start = page_text.index(quote)
        return GroundingResult(True, _TIER_SCORE["exact"], "exact", start=start,
                               end=start + len(quote))

    folded_page, page_index = _fold(page_text)
    at = folded_page.find(folded_quote)
    if at >= 0:
        return GroundingResult(
            True, _TIER_SCORE["normalized"], "normalized",
            start=page_index[at],
            end=page_index[min(at + len(folded_quote) - 1, len(page_index) - 1)] + 1,
        )

    # Tier 3: hyphenation across line breaks. Offsets are no longer exact once
    # characters have been removed, so none are reported rather than reporting
    # ones that would highlight the wrong text.
    if _dehyphenate(folded_quote) in _dehyphenate(folded_page):
        return GroundingResult(True, _TIER_SCORE["dehyphenated"], "dehyphenated")

    # Tier 4: the layout-rebuilt page. The quote may be a table row that this
    # project assembled from separately drawn cells.
    if rendered_text:
        folded_rendered, _ = _fold(rendered_text)
        if folded_quote in folded_rendered or _dehyphenate(folded_quote) in _dehyphenate(
            folded_rendered
        ):
            return GroundingResult(True, _TIER_SCORE["reconstructed"], "reconstructed")

    return GroundingResult(
        False, 0.0, "none", REASON_QUOTE_NOT_FOUND,
        detail=f"quote not present on page: {quote.strip()[:120]!r}",
    )


def value_in_quote(value: str, quote: str) -> bool:
    """Whether a claimed value appears inside its evidence quote.

    Numbers are compared on their digits alone. The same figure is printed as
    ``81,415.38``, ``81 415.38`` and ``(81,415.38)`` depending on the table, and
    a claim should not be rejected over a thousands separator. Digits are never
    folded away, so ``81,415.38`` still cannot match ``81,415.83``.
    """
    if not value:
        return False

    folded_value, _ = _fold(value)
    folded_quote, _ = _fold(quote)
    if folded_value.strip().casefold() in folded_quote.casefold():
        return True

    digits = _digits_only(folded_value)
    return bool(digits) and digits in _digits_only(folded_quote)


def validate(
    *,
    quote: str,
    value: str | None,
    page_text: str,
    rendered_text: str | None = None,
) -> GroundingResult:
    """Run the full check for one claim."""
    result = locate(quote, page_text, rendered_text)
    if not result.ok:
        return result

    notes: list[str] = []
    if value is None or not str(value).strip():
        return GroundingResult(
            False, 0.0, result.method, REASON_MISSING_VALUE,
            detail="claim carries no value to verify",
        )

    if not value_in_quote(str(value), quote):
        return GroundingResult(
            False, 0.0, result.method, REASON_VALUE_NOT_IN_QUOTE,
            detail=f"value {str(value)[:40]!r} does not appear in its own evidence quote",
            start=result.start, end=result.end,
        )

    score = result.score
    if len(quote.strip()) < MIN_QUOTE_CHARS:
        # The quote is barely longer than the value it is supposed to support.
        # Not a rejection — a table cell really can be that terse — but it is
        # weaker evidence and says so.
        score *= 0.8
        notes.append("quote is little more than the value itself")

    if result.method == "reconstructed":
        notes.append("evidence matched the layout-rebuilt page, not the raw text")

    return GroundingResult(
        True, round(score, 3), result.method,
        start=result.start, end=result.end, notes=notes or None,
    )
