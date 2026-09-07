"""L4b — turning a period string into an interval.

Two facts about the same company and the same metric are only the same fact if
they cover the same stretch of time, and this corpus writes that stretch six
different ways. The RBI writes ``2024-25``, the IMF writes ``FY2024/25``, the
earnings deck writes ``FY25``, and the annual report writes ``for the year ended
March 31, 2025``. All four are the same twelve months. Without this module they
are four different strings and the corroboration case cannot be made.

**The Indian fiscal year is the anchor.** It runs 1 April to 31 March and is
named for the year it ends in, so FY24 is 2023-04-01 to 2024-03-31 and Q4 FY24
is the January-to-March quarter of 2024. That convention is a property of the
documents, not of the world, which is why it lives in one constant here rather
than being spread through the parsing.

**A hyphenated pair is only a fiscal year when the halves are one year apart.**
The corpus contains ``2023-24`` (one fiscal year), ``1986-87`` (one fiscal year),
``2016-23`` (a seven-year span) and ``2000-19`` (a nineteen-year span), written
identically. Reading ``2000-19`` as a fiscal year would place a two-decade trend
inside twelve months.

**A bare year-end date is genuinely ambiguous and is recorded as such.** Annual
report page 35 heads its columns ``March 31, 2024 | March 31, 2023`` above
revenue, where it means the year ending then; page 90 heads its columns
identically above lease liabilities, where it means the balance at that instant.
The header cannot tell them apart — only the section declaration above it can.
When that declaration reaches us the period is resolved and confident; when it
does not, the interval is still returned but flagged, so the comparability gate
can name the ambiguity instead of silently picking one reading.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# The Indian fiscal year starts in April and is named for the calendar year it
# ends in. Every document in this corpus follows it, including the IMF's
# `FY2024/25` notation.
FY_START_MONTH = 4

# Quarter n of a fiscal year, as an offset in months from the fiscal year start.
# Q1 = Apr-Jun, so Q4 = Jan-Mar of the *naming* year.
_QUARTER_MONTHS = 3

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(calendar.month_name)
    if m
}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_DATE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+"
    r"(\d{1,2})\s*,?\s*(\d{4})\b",
    re.I,
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_QUARTER = re.compile(r"\bQ([1-4])\b[\s/-]*(?:FY)?\s*(\d{2,4})(?:\s*[-/]\s*(\d{2,4}))?", re.I)
_FY = re.compile(r"\bFY\s*(\d{2,4})(?:\s*[-/]\s*(\d{2,4}))?", re.I)
_PAIR = re.compile(r"\b(\d{4})\s*[-/]\s*(\d{2,4})\b")
_BARE_YEAR = re.compile(r"\b(\d{4})\b")

# Phrasing that resolves a bare year-end date. These are the words the annual
# report and the prospectus actually use in their section declarations.
_SPANS = re.compile(
    r"\bfor\s+the\s+(year|quarter|period|half[\s-]?year)\s+end(ed|ing)\b"
    r"|\byear\s+end(ed|ing)\b"
    r"|\btwelve\s+months?\s+end(ed|ing)\b",
    re.I,
)
_INSTANTS = re.compile(r"\bas\s+(at|on|of)\b|\bbalance\s+sheet\s+date\b", re.I)


def _expand_year(token: str, anchor: int | None = None) -> int:
    """`24` -> 2024. With an anchor, take the century from it: 2023 + `24` -> 2024."""
    value = int(token)
    if len(token) == 4:
        return value
    if anchor is not None:
        century = (anchor // 100) * 100
        candidate = century + value
        # `2099-00` means 2100, not 2000.
        return candidate + 100 if candidate < anchor else candidate
    return 2000 + value if value <= 50 else 1900 + value


def fiscal_year_bounds(end_year: int) -> tuple[date, date]:
    """The interval of the fiscal year *named* for ``end_year``."""
    return (
        date(end_year - 1, FY_START_MONTH, 1),
        date(end_year, FY_START_MONTH, 1) - timedelta(days=1),
    )


@dataclass(frozen=True)
class Period:
    """A period as an interval, plus how confidently it was read."""

    start: date | None
    end: date | None
    granularity: str  # instant | quarter | fiscal_year | multi_year | unknown
    label: str  # canonical, e.g. "FY2024", "Q4 FY2024", "2016-2023"
    raw: str = ""
    confidence: float = 0.0
    ambiguous: bool = False
    notes: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.granularity != "unknown" and self.start is not None

    def overlaps(self, other: "Period") -> bool:
        if not (self.known and other.known):
            return False
        return self.start <= other.end and other.start <= self.end


UNKNOWN = Period(None, None, "unknown", "unknown", confidence=0.0)


def _fy(end_year: int, raw: str, confidence: float = 1.0, ambiguous: bool = False,
        notes: tuple[str, ...] = ()) -> Period:
    start, end = fiscal_year_bounds(end_year)
    return Period(start, end, "fiscal_year", f"FY{end_year}", raw, confidence,
                  ambiguous, notes)


def parse_period(raw: str | None, hint: str | None = None) -> Period:
    """Parse a period string into an interval.

    ``hint`` is the surrounding wording — the section declaration a period was
    inherited from, or the sentence it sits in. It exists for exactly one job:
    deciding whether ``March 31, 2024`` names the year that ended then or the
    instant itself. Nothing else needs it.
    """
    text = (raw or "").strip()
    if not text:
        return Period(None, None, "unknown", "unknown", text, 0.0,
                      notes=("no period stated",))

    hint_text = hint or ""

    # Quarters first: `Q4 FY24` also matches the fiscal-year pattern.
    m = _QUARTER.search(text)
    if m:
        quarter = int(m.group(1))
        end_year = _expand_year(m.group(3) or m.group(2),
                                _expand_year(m.group(2)) if m.group(3) else None)
        fy_start, _ = fiscal_year_bounds(end_year)
        offset = (quarter - 1) * _QUARTER_MONTHS
        month = (fy_start.month - 1 + offset) % 12 + 1
        year = fy_start.year + (fy_start.month - 1 + offset) // 12
        last_month = (month - 1 + _QUARTER_MONTHS - 1) % 12 + 1
        last_year = year + (month - 1 + _QUARTER_MONTHS - 1) // 12
        return Period(
            date(year, month, 1),
            date(last_year, last_month, calendar.monthrange(last_year, last_month)[1]),
            "quarter",
            f"Q{quarter} FY{end_year}",
            text,
            1.0,
        )

    m = _FY.search(text)
    if m:
        first = m.group(1)
        if m.group(2):  # FY2023-24 / FY2024/25 — named by the closing year
            end_year = _expand_year(m.group(2), _expand_year(first))
        else:
            end_year = _expand_year(first)
        return _fy(end_year, text)

    m = _ISO_DATE.search(text) or _DATE.search(text)
    if m:
        if m.re is _ISO_DATE:
            day_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            day_date = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))

        if _SPANS.search(hint_text):
            return _fy(
                day_date.year if day_date.month <= FY_START_MONTH else day_date.year + 1,
                text,
                notes=("resolved to a year by the section declaration",),
            )
        if _INSTANTS.search(hint_text):
            return Period(day_date, day_date, "instant", day_date.isoformat(), text, 1.0)

        # No declaration reached us. A date on a year end is the ambiguous case
        # described in the module docstring: the same column header means the
        # year on one page and the instant on another. Return the year, because
        # that is what a period column on a measurement almost always labels,
        # but say plainly that it was not resolved.
        year_end = day_date.month == 3 and day_date.day == 31
        if year_end:
            return _fy(
                day_date.year, text, confidence=0.6, ambiguous=True,
                notes=("a bare fiscal year-end date: read as the year ending then, "
                       "but the document did not say whether it means the year or "
                       "the balance at that instant",),
            )
        return Period(day_date, day_date, "instant", day_date.isoformat(), text, 0.9,
                      notes=("a bare date, read as an instant",))

    # `2023-24` vs `2016-23`: a fiscal year only when the halves are adjacent.
    # Tested after full dates on purpose: `2024-03-31` matches this pattern as
    # `2024-03` and would otherwise be read as a 79-year span ending in 2103.
    m = _PAIR.search(text)
    if m:
        first = int(m.group(1))
        second = _expand_year(m.group(2), first)
        if second - first == 1:
            return _fy(second, text)
        if second > first:
            return Period(
                date(first, 1, 1), date(second, 12, 31), "multi_year",
                f"{first}-{second}", text, 1.0,
                notes=(f"a {second - first + 1}-year span, not a fiscal year",),
            )

    m = _BARE_YEAR.search(text)
    if m:
        year = int(m.group(1))
        return Period(date(year, 1, 1), date(year, 12, 31), "multi_year",
                      str(year), text, 0.7,
                      notes=("a bare calendar year; no fiscal convention stated",))

    return Period(None, None, "unknown", "unknown", text, 0.0,
                  notes=("period string not recognised",))


@dataclass
class PeriodComparison:
    relation: str  # same | overlapping | disjoint | unknown
    reason: str
    axis: str | None = None
    ambiguous: bool = False


def compare_periods(a: Period, b: Period) -> PeriodComparison:
    """How two periods relate in time.

    ``disjoint`` is not a contradiction and must never be treated as one: two
    different years reporting two different revenues is a trend, not a
    disagreement. That distinction is the single most common way a naive
    comparison invents conflicts.
    """
    if not a.known or not b.known:
        return PeriodComparison("unknown", "one or both periods could not be read",
                                "period")
    ambiguous = a.ambiguous or b.ambiguous
    if a.start == b.start and a.end == b.end:
        return PeriodComparison("same", f"both cover {a.label}", None, ambiguous)
    if a.overlaps(b):
        return PeriodComparison(
            "overlapping",
            f"{a.label} and {b.label} overlap without being the same period",
            "period",
            ambiguous,
        )
    return PeriodComparison(
        "disjoint", f"{a.label} and {b.label} do not overlap", "period", ambiguous
    )
