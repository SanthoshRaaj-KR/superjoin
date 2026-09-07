"""Period parsing and temporal comparability.

Every string tested here was taken from the corpus, by collecting the period
declarations the context layer finds across all 511 pages. None of them is
invented, and the awkward ones — `2000-19`, `1986-87` — are awkward because real
documents wrote them that way.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.periods import compare_periods, parse_period  # noqa: E402


# --- one year, four publishers ----------------------------------------------


def test_four_documents_spell_one_fiscal_year_four_ways():
    """The reason this module exists.

    The RBI writes `2024-25`, the IMF writes `FY2024/25`, the earnings deck
    writes `FY25`, and the annual report writes `for the year ended March 31,
    2025`. Without normalisation these are four different strings, and the
    cross-document macro cases cannot be made at all.
    """
    spellings = [
        parse_period("2024-25"),
        parse_period("FY2024/25"),
        parse_period("FY25"),
        parse_period("March 31, 2025", "for the year ended March 31, 2025"),
    ]
    assert {p.label for p in spellings} == {"FY2025"}
    assert {(p.start, p.end) for p in spellings} == {
        (date(2024, 4, 1), date(2025, 3, 31))
    }


def test_the_indian_fiscal_year_is_named_for_the_year_it_ends_in():
    fy24 = parse_period("FY24")
    assert fy24.start == date(2023, 4, 1)
    assert fy24.end == date(2024, 3, 31)


def test_quarters_are_placed_inside_the_fiscal_year_not_the_calendar_year():
    """Q4 FY24 is January to March 2024, not October to December."""
    q4 = parse_period("Q4 FY24")
    assert (q4.start, q4.end) == (date(2024, 1, 1), date(2024, 3, 31))
    q1 = parse_period("Q1 FY23")
    assert (q1.start, q1.end) == (date(2022, 4, 1), date(2022, 6, 30))


# --- the pair that looks like a fiscal year and is not ----------------------


def test_a_hyphenated_pair_is_a_fiscal_year_only_when_the_halves_are_adjacent():
    """All four of these appear in the corpus, written identically.

    `2023-24` and `1986-87` are single fiscal years. `2016-23` and `2000-19` are
    multi-year spans in RBI and Economic Survey trend tables. Reading `2000-19`
    as a fiscal year would compress two decades into twelve months.
    """
    assert parse_period("2023-24").label == "FY2024"
    assert parse_period("1986-87").label == "FY1987"

    span = parse_period("2016-23")
    assert span.granularity == "multi_year"
    assert (span.start, span.end) == (date(2016, 1, 1), date(2023, 12, 31))

    long_span = parse_period("2000-19")
    assert long_span.granularity == "multi_year"
    assert long_span.end.year == 2019
    assert "not a fiscal year" in " ".join(long_span.notes)


# --- the ambiguity that must not be hidden ----------------------------------


def test_a_bare_year_end_date_is_flagged_rather_than_silently_resolved():
    """Annual report page 35 heads its columns `March 31, 2024` above revenue,
    where it means the year; page 90 heads them identically above lease
    liabilities, where it means the balance at that instant.

    The header cannot distinguish them, so guessing silently is the one thing
    this must not do.
    """
    bare = parse_period("March 31, 2024")
    assert bare.ambiguous
    assert bare.confidence < 0.7
    assert "did not say" in " ".join(bare.notes)


def test_a_section_declaration_resolves_the_ambiguity_and_restores_confidence():
    span = parse_period("March 31, 2024", "for the year ended March 31, 2024")
    assert span.granularity == "fiscal_year"
    assert not span.ambiguous
    assert span.confidence == 1.0

    instant = parse_period("March 31, 2024", "as at March 31, 2024")
    assert instant.granularity == "instant"
    assert instant.start == instant.end == date(2024, 3, 31)


def test_an_unreadable_period_is_unknown_rather_than_guessed():
    assert not parse_period(None).known
    assert not parse_period("whenever").known


# --- comparing periods ------------------------------------------------------


def test_different_spellings_of_one_year_compare_as_the_same_period():
    result = compare_periods(parse_period("FY24"), parse_period("2023-24"))
    assert result.relation == "same"


def test_two_different_years_are_disjoint_and_that_is_not_a_disagreement():
    """The most common way a naive comparison invents conflicts.

    FY23 revenue and FY24 revenue differ, and they are supposed to. `disjoint`
    exists so the gate above can call that a trend rather than a contradiction.
    """
    result = compare_periods(parse_period("FY23"), parse_period("FY24"))
    assert result.relation == "disjoint"
    assert result.axis == "period"


def test_a_quarter_inside_a_year_overlaps_without_being_the_same():
    result = compare_periods(parse_period("Q4 FY24"), parse_period("FY24"))
    assert result.relation == "overlapping"


def test_an_unresolved_ambiguity_is_carried_into_the_comparison():
    """It must not vanish just because both sides happen to agree."""
    result = compare_periods(parse_period("March 31, 2024"), parse_period("FY24"))
    assert result.relation == "same"
    assert result.ambiguous


def test_an_iso_date_is_not_read_as_a_year_pair():
    """`2024-03-31` matches the `2023-24` pattern as `2024-03`.

    Read that way it becomes a 79-year span ending in 2103 — a silent, enormous
    error that no downstream check would catch, because a period that wide
    overlaps everything and would make unrelated claims look comparable.
    """
    iso = parse_period("2024-03-31")
    assert iso.granularity == "fiscal_year"
    assert iso.end == date(2024, 3, 31)

    day = parse_period("2023-06-01")
    assert day.granularity == "instant"
    assert day.start == date(2023, 6, 1)
