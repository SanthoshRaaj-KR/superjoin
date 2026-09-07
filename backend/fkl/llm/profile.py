"""L0 — document profiling.

One cheap call that establishes the context every later claim inherits: who is
speaking, about whom, as of when, in what units.

The most important field is ``as_of_date``. It is the *assertion time* axis:
when this document claimed something, as distinct from when that something was
true. Without it, a 2022 prospectus listing a director and a 2024 annual report
recording their resignation are two conflicting strings. With it, they are two
assertions made two years apart, and the later one closes an interval opened by
the earlier one — a refinement, not a contradiction.

Defaults are deliberately allowed to be null. An annual report contains both
standalone and consolidated statements, so a document-level consolidation basis
would be a lie that quietly mislabels half the claims in the file. Where a
document is mixed, this layer says so and lets section scope decide.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from pydantic import BaseModel, Field

from ..config import SETTINGS
from ..pdf.extract import ExtractedDocument, profiling_sample
from .client import structured

log = logging.getLogger(__name__)


class DocumentProfile(BaseModel):
    """What a document is, and the context its claims inherit."""

    doc_type: str = Field(
        description=(
            "Generic kind of document in lowercase snake_case, e.g. annual_report, "
            "prospectus, earnings_presentation, policy_report, statistical_report. "
            "Describe the genre, not this specific file."
        )
    )
    publisher: str | None = Field(
        default=None, description="Organisation that issued the document."
    )
    primary_entity: str | None = Field(
        default=None,
        description=(
            "The main subject the document reports on: a company, a country, an "
            "institution. Use the full formal name as printed."
        ),
    )
    as_of_date: str | None = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) at which this document asserts its contents: "
            "its publication or signing date, or the closing date of the period "
            "it reports. Null if the document does not state one."
        ),
    )
    as_of_date_basis: str | None = Field(
        default=None,
        description=(
            "The exact text from the sample that the date was read from, quoted "
            "verbatim. Null if no date was found."
        ),
    )
    reporting_period: str | None = Field(
        default=None,
        description="Period covered, as printed, e.g. FY2023-24, 2024-25, Q4 FY24.",
    )
    default_currency: str | None = Field(
        default=None, description="ISO code of the dominant currency, e.g. INR, USD."
    )
    default_scale: str | None = Field(
        default=None,
        description=(
            "Dominant numeric scale for monetary figures: units, thousand, lakh, "
            "million, crore, billion. Null if the document does not declare one."
        ),
    )
    default_consolidation: str | None = Field(
        default=None,
        description=(
            "consolidated or standalone, ONLY if the whole document is on one "
            "basis. Return null if it contains both or does not say. Null is the "
            "right answer far more often than not."
        ),
    )
    language: str | None = Field(default=None, description="ISO 639-1 code, e.g. en.")
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence in this profile, 0-1."
    )
    notes: str | None = Field(
        default=None, description="Anything ambiguous a later layer should know."
    )


SYSTEM = """You profile documents for a fact-extraction system.

You are shown PDF metadata and a sample of pages from one document. Identify \
what kind of document it is and the context that its facts should inherit.

Rules:
- Report only what the sample supports. Null is a valid and often correct answer.
- Never guess a consolidation basis. Many financial reports contain both \
standalone and consolidated statements; when that is possible, return null and \
let section-level context decide.
- The date you return is when the DOCUMENT speaks, not the period it describes. \
A 2024 annual report reporting on FY2023-24 has an as_of_date in 2024.
- Quote as_of_date_basis verbatim from the sample. Do not paraphrase it.
- Describe the genre of document generically. Do not invent a category that \
only this file could belong to."""


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _ISO.match(value.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def pdf_creation_date(metadata: dict[str, str]) -> date | None:
    """Parse a PDF ``D:YYYYMMDD...`` timestamp into a date."""
    raw = metadata.get("creationDate") or metadata.get("modDate") or ""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date()
    except ValueError:
        return None


def profile_document(doc: ExtractedDocument, model: str | None = None) -> DocumentProfile:
    """Run L0 profiling over a sampled slice of the document."""
    sample = profiling_sample(doc)
    profile = structured(
        response_model=DocumentProfile,
        system=SYSTEM,
        user=f"Profile this document.\n\n{sample}",
        model=model or SETTINGS.model_reason,
    )

    # Fall back to the PDF timestamp only when the document itself is silent,
    # and record that the date came from metadata rather than from the text.
    # An inferred assertion time is still better than none: without one, every
    # temporal relation in the corpus degrades to an unresolvable disagreement.
    if parse_iso_date(profile.as_of_date) is None:
        fallback = pdf_creation_date(doc.pdf_metadata)
        if fallback:
            profile.as_of_date = fallback.isoformat()
            profile.as_of_date_basis = "PDF metadata creationDate (not stated in text)"
            profile.confidence = min(profile.confidence, 0.6)
            log.info("as_of_date taken from PDF metadata for %s", doc.path.name)

    return profile
