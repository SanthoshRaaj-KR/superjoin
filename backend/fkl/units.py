"""L4a — turning a unit string into something two claims can be compared in.

A measurement is not a number. ``8,142`` and ``81,415.38`` are the same fact
about the same company in the same year, and ``6.4`` and ``6.4`` may be two
different facts. The difference is entirely in the unit, so the unit has to
become structure rather than remaining a string.

Three decisions shape this module.

**Scale is separated from currency.** ``crore`` and ``million`` are not currency
units; they are multipliers that Indian and international documents apply to the
same rupee. Keeping them apart is what lets ``₹8,142 Cr`` and ``₹81,415.38 Mn``
normalise to the same number of rupees while ``$8,142 Mn`` stays a different
fact.

**Currencies are never converted.** There is no FX rate in this system and there
will not be one. A rate has a date, a source and a spread, none of which the
documents state, and inventing one turns a refusal to compare into a wrong
answer. USD and INR claims come back ``incomparable`` with the reason attached,
which is the honest verdict and also the cheap one.

**Percent and percentage points are different dimensions.** A margin that moves
from 4% to 6% has risen by 2 percentage points and by 50 percent. Documents in
this corpus use ``pp`` and ``bps`` deliberately, and collapsing them into
``percent`` would manufacture contradictions out of correct statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Multipliers, all relative to the base unit (one rupee, one shipment, one
# tonne). `lakh` and `crore` are the reason this table exists: an Indian annual
# report and an international deck describe one company in units that differ by
# 10^7, and no amount of string similarity notices that.
SCALES: dict[str, float] = {
    "unit": 1.0,
    "hundred": 1e2,
    "thousand": 1e3,
    "lakh": 1e5,
    "million": 1e6,
    "crore": 1e7,
    "billion": 1e9,
    "trillion": 1e12,
}

# Spellings observed in the corpus, matched per token so that `mn` inside
# `million` cannot win.
_SCALE_WORDS: dict[str, str] = {
    "hundred": "hundred",
    "thousand": "thousand",
    "thousands": "thousand",
    "k": "thousand",
    "lakh": "lakh",
    "lakhs": "lakh",
    "lac": "lakh",
    "million": "million",
    "millions": "million",
    "mn": "million",
    "mln": "million",
    "crore": "crore",
    "crores": "crore",
    "cr": "crore",
    "billion": "billion",
    "billions": "billion",
    "bn": "billion",
    "trillion": "trillion",
    "trillions": "trillion",
    "tn": "trillion",
}

_CURRENCY_WORDS: dict[str, str] = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "sterling": "GBP",
}

# Non-currency dimensions, matched against the unit string as a whole. Order
# matters: `bps` and `pp` must be tested before the bare percent sign.
_DIMENSION_WORDS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"\bbps\b|\bbasis\s+points?\b", re.I), "percent", 0.01),
    (re.compile(r"\bpp\b|\bppts?\b|\bpercentage\s+points?\b", re.I),
     "percentage_points", 1.0),
    (re.compile(r"%|\bper\s?cent(age)?\b|\bpct\b", re.I), "percent", 1.0),
    (re.compile(r"\bdays?\b|\bdso\b", re.I), "days", 1.0),
    (re.compile(r"\bton(ne)?s?\b|\bmt\b", re.I), "mass_tonnes", 1.0),
    (re.compile(r"\bkgs?\b|\bkilograms?\b", re.I), "mass_tonnes", 0.001),
    (re.compile(r"\bx\b|\btimes\b", re.I), "ratio", 1.0),
    (re.compile(r"\bshipments?\b|\bparcels?\b|\bunits?\b|\bcount\b|\bnos?\.?\b", re.I),
     "count", 1.0),
]

_TOKEN = re.compile(r"[₹$€£]|us\$|[a-z']+\.?|%", re.I)

# Dimensions where a magnitude multiplier makes no sense. A percentage sitting
# in a table headed "(₹ in million)" is still a percentage, and applying 10^6 to
# it is the most destructive normalisation error available here.
_UNSCALED = {"percent", "percentage_points", "ratio"}


@dataclass(frozen=True)
class Unit:
    """A parsed unit: what is being measured, in what, times how much."""

    dimension: str  # currency | percent | percentage_points | ratio | count | ...
    scale: float = 1.0
    currency: str | None = None
    scale_name: str | None = None
    raw: str = ""
    confidence: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.dimension != "unknown"

    def canonical(self, value: float | None) -> float | None:
        """The value in base units, or None when it cannot be placed."""
        if value is None or not self.known:
            return None
        return value * self.scale

    def describe(self) -> str:
        if self.dimension == "currency":
            return f"{self.currency or '?'} {self.scale_name or 'units'}"
        if self.scale_name:
            return f"{self.dimension} ({self.scale_name})"
        return self.dimension


UNKNOWN = Unit(dimension="unknown", raw="", confidence=0.0)


def parse_unit(raw: str | None, qualifiers: dict | None = None) -> Unit:
    """Parse a unit string, falling back to section context for what it omits.

    The fallback is the point. A financial table prints ``81,415.38`` in a cell
    and declares ``(All amounts in Indian Rupees in million)`` once at the top of
    the page, so currency and scale reach the value through the context frame
    rather than through the cell. ``unit_raw`` is frequently ``Mn`` alone, or
    empty, and reading that as "no unit" would discard the page's own statement
    of what its numbers mean.

    Anything taken from context rather than from the value's own unit string is
    recorded in ``notes`` and costs confidence, because a section declaration is
    advisory — the sentence beside a number can override it.
    """
    qualifiers = qualifiers or {}
    text = (raw or "").strip()
    notes: list[str] = []

    currency: str | None = None
    scale_name: str | None = None

    lowered = text.lower()
    for token in _TOKEN.findall(lowered):
        token = token.strip()
        if token in _CURRENCY_WORDS and currency is None:
            currency = _CURRENCY_WORDS[token]
        elif token in _SCALE_WORDS and scale_name is None:
            scale_name = _SCALE_WORDS[token]
    # `'000` does not survive word tokenisation, and it is how the deck writes
    # thousands: "'000 Tons".
    if scale_name is None and re.search(r"'?\b0{3}s?\b", lowered):
        scale_name = "thousand"

    # A dimension word wins over currency: `'000 Tons` names a scale and a
    # dimension and no currency at all, while `₹ Cr` names no dimension because
    # money is the dimension.
    dimension: str | None = None
    dim_scale = 1.0
    for pattern, dim, mult in _DIMENSION_WORDS:
        if pattern.search(text):
            dimension, dim_scale = dim, mult
            break

    if dimension is None:
        if currency is None and qualifiers.get("currency"):
            currency = str(qualifiers["currency"]).upper()
            notes.append("currency taken from section context")
        if currency is not None:
            dimension = "currency"
        elif str(qualifiers.get("unit_dimension", "")).lower() == "percent":
            dimension = "percent"
            notes.append("percent taken from section context")

    if dimension is None:
        return Unit(
            dimension="unknown",
            raw=text,
            confidence=0.0,
            notes=("no unit stated and none declared in scope",),
        )

    if scale_name is None and dimension not in _UNSCALED and qualifiers.get("scale"):
        candidate = str(qualifiers["scale"]).lower()
        if candidate in SCALES:
            scale_name = candidate
            notes.append("scale taken from section context")

    if dimension in _UNSCALED:
        scale_name = None

    scale = SCALES.get(scale_name or "unit", 1.0) * dim_scale

    confidence = 1.0 if not notes else (0.8 if len(notes) == 1 else 0.7)
    if dimension == "currency" and scale_name is None:
        notes.append("no scale stated; read as base units")
        confidence = min(confidence, 0.6)

    return Unit(
        dimension=dimension,
        scale=scale,
        currency=currency,
        scale_name=scale_name,
        raw=text,
        confidence=confidence,
        notes=tuple(notes),
    )


@dataclass
class UnitComparison:
    comparable: bool
    reason: str
    axis: str | None = None
    notes: list[str] = field(default_factory=list)


def compare_units(a: Unit, b: Unit) -> UnitComparison:
    """Whether two units place their values on the same axis.

    A reason comes back in every case, success included, because the comparison
    layer explains itself from these strings rather than re-deriving them.
    """
    if not a.known or not b.known:
        return UnitComparison(False, "one or both units could not be determined", "unit")
    if a.dimension != b.dimension:
        return UnitComparison(
            False, f"different dimensions: {a.describe()} vs {b.describe()}", "unit"
        )
    if a.dimension == "currency" and a.currency != b.currency:
        # Deliberate; see the module docstring. No FX rate is invented here.
        return UnitComparison(
            False,
            f"different currencies ({a.currency} vs {b.currency}); "
            "no exchange rate is applied",
            "currency",
        )
    # Named by what they share, not by either one's scale: `crore` and `million`
    # are comparable *because* the scale difference has already been absorbed
    # into the canonical value, so reporting one of them here would misdescribe
    # the very thing that makes the comparison work.
    shared = a.currency if a.dimension == "currency" else a.dimension
    return UnitComparison(True, f"both in {shared}", None)
