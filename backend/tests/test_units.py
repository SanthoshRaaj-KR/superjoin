"""Unit parsing and unit comparability.

The cases here are the ones that decide real verdicts elsewhere. A scale error
of 10^7 does not look like an error downstream — it looks like a contradiction,
and it would be reported as one with full confidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.units import compare_units, parse_unit  # noqa: E402


# --- reading the unit -------------------------------------------------------


def test_indian_and_international_scales_land_on_one_number():
    """The corroboration case the whole project is built to demonstrate.

    The earnings deck prints FY24 revenue as `₹8,142 Cr`; the annual report
    prints it as `81,415.38` under `(All amounts in Indian Rupees in million)`.
    Those are the same fact, and only the scale table makes them look like it.
    """
    deck = parse_unit("₹ Cr")
    report = parse_unit("Mn", {"currency": "INR"})

    a = deck.canonical(8142)
    b = report.canonical(81415.38)
    assert a is not None and b is not None
    assert abs(a - b) / b < 0.001  # 0.006%, which is rounding, not disagreement


def test_scale_comes_from_section_context_when_the_cell_omits_it():
    """A table cell holds `81,415.38` and nothing else. Currency and scale are
    declared once at the top of the page, and that is the only place they
    appear."""
    unit = parse_unit(None, {"currency": "INR", "scale": "million"})
    assert unit.dimension == "currency"
    assert unit.currency == "INR"
    assert unit.scale == 1e6
    # Inherited context is advisory, so it costs confidence and says so.
    assert unit.confidence < 1.0
    assert any("section context" in n for n in unit.notes)


def test_a_percentage_in_a_millions_table_is_still_a_percentage():
    """The most destructive normalisation error available.

    Financial pages declare `(₹ in million)` at page scope and then print
    margins as percentages in the same table. Inheriting the scale onto them
    multiplies a 62% margin by a million.
    """
    unit = parse_unit("%", {"currency": "INR", "scale": "million"})
    assert unit.dimension == "percent"
    assert unit.scale == 1.0
    assert unit.canonical(62.0) == 62.0


def test_percentage_points_are_not_percent():
    """A margin moving 4% -> 6% rose by 2 percentage points and by 50 percent.

    Both statements are true, and a system that treats `pp` as `%` will read
    them as a contradiction.
    """
    pp = parse_unit("pp")
    pct = parse_unit("%")
    assert pp.dimension == "percentage_points"
    assert pct.dimension == "percent"
    assert not compare_units(pp, pct).comparable


def test_basis_points_are_percent_scaled():
    bps = parse_unit("bps")
    assert bps.dimension == "percent"
    assert bps.canonical(50) == 0.5  # 50bps = 0.5%


def test_a_dimension_word_beats_a_scale_word():
    """`'000 Tons` names a scale and a dimension and no currency at all."""
    unit = parse_unit("'000 Tons")
    assert unit.dimension == "mass_tonnes"
    assert unit.scale == 1e3
    assert unit.currency is None


def test_an_absent_unit_is_unknown_rather_than_assumed():
    unit = parse_unit(None, {})
    assert not unit.known
    assert unit.canonical(5.0) is None
    assert unit.confidence == 0.0


# --- comparing units --------------------------------------------------------


def test_different_scales_of_one_currency_are_comparable():
    result = compare_units(parse_unit("₹ Cr"), parse_unit("₹ Mn"))
    assert result.comparable
    # Named by what they share. Reporting "crore" here would misdescribe the
    # very thing that makes the comparison work.
    assert result.reason == "both in INR"


def test_currencies_are_never_converted():
    """A refusal, on purpose.

    An FX rate has a date, a source and a spread, none of which these documents
    state. Applying an invented one turns a correct refusal into a wrong answer.
    """
    result = compare_units(parse_unit("₹ Cr"), parse_unit("US$ bn"))
    assert not result.comparable
    assert result.axis == "currency"
    assert "no exchange rate" in result.reason


def test_an_unknown_unit_blocks_comparison_rather_than_permitting_it():
    """Absent is not equal — the rule the whole comparability gate turns on."""
    result = compare_units(parse_unit(None, {}), parse_unit("₹ Cr"))
    assert not result.comparable
    assert result.axis == "unit"
