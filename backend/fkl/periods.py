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
# `FY2024/25` notation — so it is the default, not a law.
#
# It is a *default* and a parameter rather than a constant because getting this
# wrong is silent. A filer whose year ends in September, read as April-March,
# produces intervals that are confidently wrong by six months, and every
# comparison against them looks fine. `Q3` is worse: on an April year it is
# October-December and on a calendar year it is July-September, and nothing in
# the output distinguishes the two readings.
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

# A year-end declaration in the document's own words. "year ended March 31"
# says the fiscal year opens in April; "year ended December 31" says January.
_YEAR_END = re.compile(
    r"\b(?:year|period)\s+end(?:ed|ing)\s+"
    r"(?:the\s+)?(?:\d{1,2}\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)",
    re.I,
)


def infer_fy_start_month(text: str | None, default: int = FY_START_MONTH) -> int:
    """The month a document's fiscal year starts in, read from its own wording.

    "year ended March 31" means the year opened in April; "year ended
    December 31" means it opened in January. Falls back to the default when
    the document never says, which is the honest behaviour — guessing from
    silence would be the same silent error, just better hidden.
    """
    m = _YEAR_END.search(text or "")
    if not m:
        return default
    return _MONTHS[m.group(1).lower()] % 12 + 1

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


def fiscal_year_bounds(end_year: int,
                       fy_start_month: int = FY_START_MONTH) -> tuple[date, date]:
    """The interval of the fiscal year *named* for ``end_year``.

    A fiscal year starting in January is the calendar year of the same name;
    any other start month means the year opens in the preceding calendar year.
    """
    if fy_start_month == 1:
        return (date(end_year, 1, 1), date(end_year, 12, 31))
    return (
        date(end_year - 1, fy_start_month, 1),
        date(end_year, fy_start_month, 1) - timedelta(days=1),
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



def _fy_naming(day: date, fy_start_month: int = FY_START_MONTH) -> int:
    """Which fiscal year a date belongs to, by the year the fiscal year ends in.

    A date that *is* the year end names its own year: 31 March 2024 is FY2024 on
    an April calendar, 31 December 2024 is FY2024 on a January one, 30 September
    2024 is FY2024 on an October one. Any other date names the year its own
    fiscal year closes in.

    Written out because the shortcut — "before the start month, so it is this
    year" — is right for April and wrong for January, where the year end is
    December and every date is on or after the start month.
    """
    close_month = (fy_start_month - 2) % 12 + 1
    if day.month == close_month:
        return day.year
    return day.year + 1 if day.month >= fy_start_month else day.year

def _fy(end_year: int, raw: str, confidence: float = 1.0, ambiguous: bool = False,
        notes: tuple[str, ...] = (),
        fy_start_month: int = FY_START_MONTH) -> Period:
    start, end = fiscal_year_bounds(end_year, fy_start_month)
    return Period(start, end, "fiscal_year", f"FY{end_year}", raw, confidence,
                  ambiguous, notes)


def parse_period(raw: str | None, hint: str | None = None, *,
                 fy_start_month: int = FY_START_MONTH) -> Period:
    """Parse a period string into an interval.

    ``hint`` is the surrounding wording — the section declaration a period was
    inherited from, or the sentence it sits in. It exists for exactly one job:
    deciding whether ``March 31, 2024`` names the year that ended then or the
    instant itself. Nothing else needs it.

    ``fy_start_month`` is the document's own fiscal calendar, inferred by
    ``infer_fy_start_month`` from wording like "year ended December 31" and
    defaulting to April. It is a parameter rather than a constant because a
    wrong fiscal calendar fails silently: a September filer read as an April
    one produces intervals confidently wrong by six months, and `Q3` means
    October-December on one calendar and July-September on another with
    nothing in the output to tell them apart.
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
        fy_start, _ = fiscal_year_bounds(end_year, fy_start_month)
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
        return _fy(end_year, text, fy_start_month=fy_start_month)

    m = _ISO_DATE.search(text) or _DATE.search(text)
    if m:
        if m.re is _ISO_DATE:
            day_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            day_date = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))

        if _SPANS.search(hint_text):
            return _fy(
                _fy_naming(day_date, fy_start_month), text,
                notes=("resolved to a year by the section declaration",),
                fy_start_month=fy_start_month,
            )
        if _INSTANTS.search(hint_text):
            return Period(day_date, day_date, "instant", day_date.isoformat(), text, 1.0)

        # No declaration reached us. A date on a year end is the ambiguous case
        # described in the module docstring: the same column header means the
        # year on one page and the instant on another. Return the year, because
        # that is what a period column on a measurement almost always labels,
        # but say plainly that it was not resolved.
        # Whether this date falls on the document's own year end — the last
        # day of the month before its fiscal year opens. Hardcoding 31 March
        # made this test true only for April filers, so a December filer's
        # year-end column was read as an instant rather than a year.
        close_month = (fy_start_month - 2) % 12 + 1
        year_end = (
            day_date.month == close_month
            and day_date.day == calendar.monthrange(day_date.year, close_month)[1]
        )
        if year_end:
            return _fy(
                _fy_naming(day_date, fy_start_month),
                text, confidence=0.6, ambiguous=True,
                notes=("a bare fiscal year-end date: read as the year ending then, "
                       "but the document did not say whether it means the year or "
                       "the balance at that instant",),
                fy_start_month=fy_start_month,
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
            return _fy(second, text, fy_start_month=fy_start_month)
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
