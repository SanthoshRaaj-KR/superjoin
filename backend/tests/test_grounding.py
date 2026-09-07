"""Grounding validator tests.

The tests that matter are the rejections. Accepting a correct claim is easy;
the value of this layer is entirely in what it refuses, and in refusing for a
reason specific enough to act on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.grounding import (  # noqa: E402
    REASON_EMPTY_QUOTE,
    REASON_QUOTE_NOT_FOUND,
    REASON_VALUE_NOT_IN_QUOTE,
    locate,
    validate,
    value_in_quote,
)
from fkl.pdf.layout import analyze_page, body_font_size, page_lines  # noqa: E402
from fkl.render import render_page  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
AR = REPO / "starter-datasets/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf"

PAGE = (
    "y The revenue from operations on consolidated basis for FY24 stood at "
    "₹ 81,415.38 million as against ₹72,253.01 million for FY23, "
    "registering a growth of 12.68%."
)


# --- acceptance -------------------------------------------------------------


def test_exact_quote_scores_highest():
    result = validate(
        quote="stood at ₹ 81,415.38 million",
        value="81,415.38",
        page_text=PAGE,
    )
    assert result.ok and result.method == "exact" and result.score == 1.0
    assert PAGE[result.start : result.end] == "stood at ₹ 81,415.38 million"


def test_whitespace_and_dash_differences_are_forgiven():
    """PDF text varies freely in space width and dash style. Rejecting a claim
    over a non-breaking space would discard correct extractions by the hundred."""
    page = "revenue from operations — ₹81,415.38 million"
    result = validate(quote="revenue from operations - ₹81,415.38 million",
                      value="81,415.38", page_text=page)
    assert result.ok and result.method == "normalized"


def test_line_break_hyphenation_is_joined():
    page = "the depreci-\nation and amortisation expense was 7,215.50"
    result = validate(quote="the depreciation and amortisation expense was 7,215.50",
                      value="7,215.50", page_text=page)
    assert result.ok and result.method == "dehyphenated"


def test_offsets_point_at_the_real_characters():
    """The span is reported against the original text, not the folded copy, so a
    UI highlight lands on what the document actually prints."""
    page = "Total income   85,942.34 for the year"
    result = locate("Total income 85,942.34", page)
    assert result.ok
    assert page[result.start : result.end] == "Total income   85,942.34"


# --- rejection --------------------------------------------------------------


def test_a_quote_not_on_the_page_is_rejected():
    result = validate(quote="revenue on a standalone basis was 74,540.82",
                      value="74,540.82", page_text=PAGE)
    assert not result.ok and result.reason_code == REASON_QUOTE_NOT_FOUND


def test_a_value_absent_from_its_own_quote_is_rejected():
    """The check that earns this layer its place.

    A model asked for a quote will nearly always produce a plausible one, and a
    plausible quote beside a hallucinated number is indistinguishable from a
    correct extraction unless the number is required to be inside the quote.
    """
    result = validate(quote="registering a growth of 12.68%.", value="81,415.38",
                      page_text=PAGE)
    assert not result.ok and result.reason_code == REASON_VALUE_NOT_IN_QUOTE
    assert "81,415.38" in (result.detail or "")


def test_a_transposed_digit_is_not_forgiven():
    """Separators are folded; digits never are."""
    assert value_in_quote("81,415.38", "stood at ₹ 81,415.38 million")
    assert value_in_quote("81415.38", "stood at ₹ 81,415.38 million")
    assert not value_in_quote("81,415.83", "stood at ₹ 81,415.38 million")


def test_empty_quote_is_rejected():
    assert validate(quote="   ", value="1", page_text=PAGE).reason_code == REASON_EMPTY_QUOTE


def test_a_bare_value_is_accepted_but_scored_down():
    result = validate(quote="12.68%", value="12.68", page_text=PAGE)
    assert result.ok and result.score < 1.0
    assert any("little more than the value" in n for n in result.notes)


# --- reconstructed rows -----------------------------------------------------


def test_a_rebuilt_table_row_grounds_at_a_lower_tier():
    """A table row is never contiguous in the PDF.

    ``Revenue from contracts with customers | 81,415.38 | 72,253.01`` was
    assembled by this project's layout pass from three separately drawn pieces.
    The words are on the page, so the claim stands — but the evidence was built
    rather than found, and the result says so instead of quietly scoring it 1.0.
    """
    doc = fitz.open(AR)
    body = body_font_size([ln for page in doc for ln in page_lines(page)])
    page_text = doc[35].get_text()
    rendered = render_page(analyze_page(doc[35], body), None, body)
    doc.close()

    row = "Revenue from contracts with customers | 81,415.38 | 72,253.01"
    assert row not in page_text  # the premise of this test

    result = validate(quote=row, value="81,415.38", page_text=page_text,
                      rendered_text=rendered.text)
    assert result.ok
    assert result.method == "reconstructed"
    assert result.score < 0.9
    assert any("layout-rebuilt" in n for n in result.notes)


def test_grounding_against_a_real_page_of_prose():
    doc = fitz.open(AR)
    page_text = doc[21].get_text()
    doc.close()

    result = validate(
        quote="on consolidated basis for FY24 stood at ₹ 81,415.38 million",
        value="81,415.38",
        page_text=page_text,
    )
    assert result.ok, result.detail


# --- partially quoted table rows -------------------------------------------
#
# All of these come from a live extraction run. The model, quoting a wide table,
# writes back the cells it used and drops the ones it did not: given
# `Revenues from express parcel services | 50,765.87 | 62.35% | 45,522.22 |
# 63.00%` it returns the label and the two revenue figures. Fifteen real claims
# were refused this way before this tier existed.

ROW_A = "Revenues from express parcel services | 50,765.87 | 62.35% | 45,522.22 | 63.00%"
ROW_B = "Revenues from part truckload services | 15,174.05 | 18.63% | 11,565.38 | 16.01%"
TABLE = f"{ROW_A}\n{ROW_B}"


def test_a_row_with_middle_cells_dropped_is_accepted_at_its_own_tier():
    result = validate(
        quote="Revenues from express parcel services | 50,765.87 | 45,522.22",
        value="50,765.87",
        page_text="the raw page, where no row is contiguous",
        rendered_text=TABLE,
    )
    assert result.ok
    assert result.method == "row_subset"
    assert result.score == 0.6  # below every tier that saw the whole row
    assert any("omits cells" in n for n in result.notes)


def test_a_cell_that_is_not_in_the_row_is_refused():
    result = validate(
        quote="Revenues from express parcel services | 99,999.99",
        value="99,999.99",
        page_text="x",
        rendered_text=TABLE,
    )
    assert not result.ok and result.reason_code == REASON_QUOTE_NOT_FOUND


def test_cells_from_two_different_rows_cannot_be_stitched_together():
    """The failure mode this tier has to avoid, and it is easy to hit.

    Folding collapses newlines into spaces, so folding the table before
    splitting it would merge every row into one and let this pass. Rows are
    split first for exactly that reason.
    """
    result = validate(
        quote="Revenues from express parcel services | 50,765.87 | 11,565.38",
        value="50,765.87",
        page_text="x",
        rendered_text=TABLE,
    )
    assert not result.ok, "cells from different rows must never satisfy one quote"


def test_cells_quoted_out_of_order_are_refused():
    result = validate(
        quote="Revenues from express parcel services | 63.00% | 50,765.87",
        value="50,765.87",
        page_text="x",
        rendered_text=TABLE,
    )
    assert not result.ok


def test_a_complete_row_still_scores_above_a_partial_one():
    complete = validate(quote=ROW_B, value="15,174.05", page_text="x", rendered_text=TABLE)
    partial = validate(
        quote="Revenues from part truckload services | 15,174.05 | 11,565.38",
        value="15,174.05",
        page_text="x",
        rendered_text=TABLE,
    )
    assert complete.method == "reconstructed"
    assert partial.method == "row_subset"
    assert complete.score > partial.score


def test_hyphenation_is_folded_symmetrically():
    """A live failure. The page breaks `fuel-efficient` across two lines; the
    model reads it correctly and writes it back unbroken.

    Joining only where whitespace follows normalises the page and not the
    quote, so the two never meet and a correct claim is refused.
    """
    page = (
        "The share of load carried through fuel-\n"
        "efficient 46-ft tractor trailers crossed \n"
        "70% by the end of FY24."
    )
    quote = (
        "The share of load carried through fuel-efficient 46-ft tractor "
        "trailers crossed 70% by the end of FY24."
    )
    result = validate(quote=quote, value="70%", page_text=page)
    assert result.ok and result.method == "dehyphenated"
