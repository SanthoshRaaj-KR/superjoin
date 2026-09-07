"""Value comparison, driven entirely by cases from the gold set.

Every pair here is one the comparability gate has to get right, and between
them they rule out any fixed tolerance: the same-fact pair is 17% apart and the
must-explain pair is 8% apart, so no constant separates them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.units import parse_unit  # noqa: E402
from fkl.values import compare_values, written_precision  # noqa: E402


def canonical(raw: str, unit_raw: str) -> tuple[float, float | None]:
    """A printed figure as (base-unit value, base-unit precision)."""
    unit = parse_unit(unit_raw)
    number = float(raw.replace(",", "").replace("(", "-").replace(")", ""))
    return unit.canonical(number), written_precision(raw, unit.scale)


def relate(a_raw, a_unit, b_raw, b_unit):
    a_value, a_precision = canonical(a_raw, a_unit)
    b_value, b_precision = canonical(b_raw, b_unit)
    return compare_values(
        a_value, b_value, a_precision=a_precision, b_precision=b_precision
    )


# --- precision is read off the string ---------------------------------------


def test_precision_comes_from_the_written_figure_not_the_number():
    """`value_num` has already lost this. 2.0 and 2.00 say different things
    about how finely the writer measured."""
    assert written_precision("2", 1e7) == 1e7  # 2 crore -> nearest crore
    assert written_precision("16.54", 1e6) == 1e4  # 16.54 million -> nearest 10k
    assert written_precision("6.5", 1.0) == 0.1


def test_a_thousands_separator_is_not_a_decimal_point():
    """`72,253` is precise to units. Read as three decimal places it becomes
    precise to thousandths, and the tolerance collapses to nothing."""
    assert written_precision("72,253", 1e6) == 1e6
    assert written_precision("8,142", 1e7) == 1e7


def test_a_figure_with_no_digits_has_no_precision():
    """`-` is how these tables write nil."""
    assert written_precision("-", 1e6) is None
    assert written_precision(None) is None


# --- the pair that rules out a fixed tolerance ------------------------------


def test_the_same_fact_can_be_seventeen_percent_apart():
    """`Revenues from sale of traded goods` FY23: 16.54 ₹Mn in the annual
    report, 2 ₹Cr in the deck.

    One crore is the deck's entire precision at this magnitude, so `2` is what
    `16.54 million` looks like rounded. Any tolerance below 17% calls this a
    contradiction.
    """
    result = relate("2", "₹ Cr", "16.54", "₹ Mn")
    assert result.agree
    assert result.relative > 0.15


def test_a_nine_percent_gap_that_must_not_be_excused():
    """Standalone against consolidated revenue, both to two decimals in
    millions. Any tolerance above 9% — which the case above would require of a
    constant — silently excuses the project's clearest reconciliation case."""
    result = relate("74,540.82", "₹ Mn", "81,415.38", "₹ Mn")
    assert result.relation == "disagree"
    assert 0.08 < result.relative < 0.09


# --- corroboration across scales --------------------------------------------


def test_crore_and_million_agree_on_one_fact():
    result = relate("8,142", "₹ Cr", "81,415.38", "₹ Mn")
    assert result.agree
    assert "rounding" in result.reason


def test_total_income_agrees_across_documents():
    assert relate("8,594", "₹ Cr", "85,942.34", "₹ Mn").agree


# --- the contradiction that has to survive ----------------------------------


def test_adjacent_roundings_disagree():
    """The RBI's 6.5% and the IMF's 6.6% for FY26.

    Both printed to one decimal, so the threshold is exactly 0.1 and the
    comparison is strict. They are the nearest two figures either could have
    printed and they are still not the same forecast. This is the project's one
    genuine contradiction; a non-strict comparison would dissolve it.
    """
    result = relate("6.5", "%", "6.6", "%")
    assert result.relation == "disagree"
    assert "Adjacent" in result.reason


def test_the_estimate_vintage_pair_also_disagrees_on_value():
    """6.4 against 6.5 for FY25.

    The gate resolves this by naming `estimate_vintage`, not by calling the
    numbers equal. Getting the right verdict for the wrong reason would be
    worse than failing.
    """
    assert relate("6.4", "%", "6.5", "%").relation == "disagree"


# --- refusals ---------------------------------------------------------------


def test_a_value_that_could_not_be_placed_is_unknown_not_equal():
    assert compare_values(None, 5.0).relation == "unknown"
    assert compare_values(5.0, None).relation == "unknown"


def test_without_any_stated_precision_only_exact_equality_agrees():
    """Falling back to a guessed tolerance here would be the same mistake as
    inventing an exchange rate."""
    assert compare_values(100.0, 100.0).agree
    assert compare_values(100.0, 101.0).relation == "disagree"
