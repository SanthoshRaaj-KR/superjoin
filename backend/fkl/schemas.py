"""The claim schema — the contract between the model and everything after it.

Every field here exists to make a later comparison decidable. Two are load
bearing in a way that is easy to miss:

``qualifiers`` is an open dict rather than an enum. The axes that separate two
claims — consolidation basis, estimate vintage, pro forma, restated, price base
— are not knowable in advance for an arbitrary PDF, and a fixed vocabulary would
put a ceiling on what the system can ever distinguish.

``unknown_qualifiers`` is the field most systems do not have. It records the
axes the extractor looked for and could not determine. Without it, a claim whose
consolidation basis is unknown looks identical to one that matches, and the
comparison silently produces a wrong verdict with full confidence. Absent is not
equal.

Confidence is decomposed. The extractor sets only the score it is entitled to
set; grounding, normalisation and entity matching are filled in by the layers
that own them. Blending them into one float destroys exactly the signal needed
to work out which stage went wrong.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Modality = Literal["actual", "estimate", "projection", "target", "restated"]


class ClaimBase(BaseModel):
    subject: str = Field(
        description=(
            "Who or what the claim is about, as printed: a company, a country, a "
            "person. Use the fullest form available on the page."
        )
    )
    predicate: str = Field(
        description=(
            "What is asserted about the subject, copied from the document's own "
            "wording, e.g. 'revenue from services', 'real GDP growth', "
            "'Company Secretary'. Do NOT map it to a standard metric name — "
            "normalisation happens later and needs the original phrasing."
        )
    )
    org_scope: str | None = Field(
        default=None,
        description=(
            "The organisation the claim applies to, when it differs from the "
            "document's main subject: a named subsidiary, a segment, a region. "
            "Null when it is the main subject."
        ),
    )
    qualifiers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Context that changes what the claim means, as axis -> value. Invent "
            "axis names that fit what the page actually says; there is no fixed "
            "list. Examples: consolidation=standalone, price_base=2011-12, "
            "estimate_vintage=first advance estimate, pro_forma=yes, "
            "segment=express parcel. Only include what the page supports."
        ),
    )
    unknown_qualifiers: list[str] = Field(
        default_factory=list,
        description=(
            "Axes that MATTER for this claim but which the page does not state. "
            "If a monetary figure appears with no indication of whether it is "
            "standalone or consolidated, put 'consolidation' here. This is not "
            "optional bookkeeping: leaving it empty asserts the context is fully "
            "known, and a later comparison will act on that."
        ),
    )
    modality: Modality = Field(
        default="actual",
        description=(
            "actual for a realised figure; estimate for an estimate of a past or "
            "current period; projection for a future period; target for a stated "
            "goal; restated for a figure explicitly labelled restated."
        ),
    )
    evidence_quote: str = Field(
        description=(
            "A short verbatim span from the page text that contains the value and "
            "enough surrounding words to identify it. Copy characters exactly, "
            "including punctuation and currency symbols. Do not paraphrase, do "
            "not join text from separate parts of the page. This is checked "
            "against the source and the claim is rejected if it does not match."
        )
    )
    confidence_extraction: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are that you read this off the page correctly. "
            "Lower it when a table's columns are hard to align, when a number "
            "sits far from its label, or when the reading order looks scrambled."
        ),
    )
    confidence_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Short reasons for a confidence below ~0.8, e.g. 'column alignment "
            "unreliable', 'label and value separated by a page break'."
        ),
    )


class MeasurementClaim(ClaimBase):
    """A claim whose value is a number with a unit and a period."""

    value_raw: str = Field(
        description="The number exactly as printed, e.g. '81,415.38', '6.5', '(2,489)'."
    )
    value_num: float = Field(
        description=(
            "The same number parsed, sign included. Parentheses mean negative in "
            "financial statements. Do NOT rescale it — leave crore as crore; "
            "scaling is a later step that needs the unit to be honest."
        )
    )
    unit_raw: str = Field(
        description=(
            "The unit as the document expresses it, including scale, e.g. "
            "'INR million', 'Rs crore', 'per cent', 'days', 'million tonnes'. "
            "Take it from a column header or section note when the value itself "
            "carries none."
        )
    )
    period_raw: str = Field(
        description=(
            "The period the value covers, as printed, e.g. 'FY2023-24', "
            "'year ended March 31, 2024', 'Q4 FY24', 'as at March 31, 2024'."
        )
    )


class StateClaim(ClaimBase):
    """A claim whose value is a state that holds over an interval.

    Roles, addresses, identifiers, statuses. ``valid_to_is_open`` means the
    document gives no end date — the state is still true as far as this document
    knows. That is different from knowing it is still true, and the distinction
    is what lets a later document close the interval instead of contradicting it.
    """

    value_text: str = Field(
        description="The state itself, e.g. a person's name, an address, a CIN."
    )
    valid_from: str | None = Field(
        default=None, description="ISO date the state began, if stated. Else null."
    )
    valid_to: str | None = Field(
        default=None, description="ISO date the state ended, if stated. Else null."
    )
    valid_to_is_open: bool = Field(
        default=True,
        description=(
            "True when the document states no end date. False only when an end "
            "date or an explicit cessation is given."
        ),
    )


class PageExtraction(BaseModel):
    """Everything one page yields.

    Split by type rather than returned as a discriminated union: the two kinds
    differ in three fields, and asking for them separately gets noticeably
    cleaner output than asking a model to tag each item.
    """

    measurements: list[MeasurementClaim] = Field(default_factory=list)
    states: list[StateClaim] = Field(default_factory=list)
    page_notes: str | None = Field(
        default=None,
        description=(
            "Set this when the page itself was a problem: scrambled reading "
            "order, a chart with no underlying numbers, a table whose columns "
            "could not be aligned. Null when the page read cleanly."
        ),
    )
