"""Layout analysis tests, pinned to real pages of the starter corpus.

These assert against actual PDFs rather than synthetic fixtures. Layout analysis
only fails in ways real typesetting produces — a gutter a few points narrower
than expected, a full-width sentence bridging two columns — and none of that
shows up in a hand-built fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.pdf.layout import (  # noqa: E402
    analyze_page,
    body_font_size,
    heading_levels,
    page_lines,
)

REPO = Path(__file__).resolve().parents[2]
AR = REPO / "starter-datasets/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf"
DECK = REPO / "starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf"
RBI = REPO / "starter-datasets/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf"


@pytest.fixture(scope="module")
def annual_report():
    doc = fitz.open(AR)
    lines = [ln for page in doc for ln in page_lines(page)]
    yield doc, body_font_size(lines)
    doc.close()


def test_body_font_size_is_measured_not_assumed(annual_report):
    _doc, body = annual_report
    assert body == 9.0

    # Different publishers set different body sizes, which is the whole reason
    # this is measured. A fixed threshold tuned on one document finds no
    # headings at all in another.
    for path in (DECK, RBI):
        doc = fitz.open(path)
        size = body_font_size([ln for page in doc for ln in page_lines(page)])
        doc.close()
        assert 4.0 <= size <= 16.0
    assert body_font_size([]) == 10.0


def test_financial_table_rows_are_reconstructed(annual_report):
    """The case the whole layer exists for.

    Page 35 prints a table whose label and values PyMuPDF reports as three
    unrelated lines. Read as plain text they arrive as a column of loose numbers
    with their labels elsewhere. Grouped by baseline they become a row again.
    """
    doc, body = annual_report
    layout = analyze_page(doc[35], body)
    texts = [row.text for _region, row in layout.iter_rows()]

    assert "Revenue from contracts with customers | 81,415.38 | 72,253.01" in texts
    assert "Particulars | March 31, 2024 | March 31, 2023" in texts
    assert "Total income | 85,942.34 | 75,302.49" in texts


def test_unit_declaration_stays_with_its_table(annual_report):
    """``(₹ in Million)`` must land in the same region as the table it governs.

    It is printed once, right-aligned, above the table, and never repeated. If
    the cut separates it from the rows below, every value in that table becomes
    a bare number with no unit — which is exactly the failure this phase exists
    to remove.
    """
    doc, body = annual_report
    layout = analyze_page(doc[35], body)

    for region in layout.regions:
        texts = [r.text for r in region.rows]
        if any("(₹ in Million)" in t for t in texts):
            assert any(t.startswith("Revenue from contracts with customers") for t in texts)
            break
    else:
        pytest.fail("unit declaration not found on page 35")


def test_prose_columns_are_not_interleaved(annual_report):
    """Two side-by-side columns must not be merged into one row.

    A merged row reads as ``...harsh working conditions | logistics industry.``
    — two unrelated sentences joined because they share a baseline. Prose rows
    should be single-celled.
    """
    doc, body = annual_report
    layout = analyze_page(doc[35], body)

    merged = [
        row.text
        for _region, row in layout.iter_rows()
        if row.is_table_row and "working conditions" in row.text
    ]
    assert merged == []


def test_reading_order_follows_the_cut_not_the_coordinates(annual_report):
    """A spread is read one page at a time, not in bands across the fold.

    Regions come out in recursion order, which is reading order. Sorting them by
    y afterwards would interleave the left and right halves of the spread.
    """
    doc, body = annual_report
    layout = analyze_page(doc[35], body)
    order = [r.x0 for r in layout.regions]
    fold = doc[35].rect.width / 2

    last_left = max(i for i, x in enumerate(order) if x < fold)
    first_right = min(i for i, x in enumerate(order) if x >= fold)
    assert last_left < first_right


def test_every_line_lands_in_exactly_one_region(annual_report):
    """The cut partitions the page; it neither drops nor duplicates lines."""
    doc, body = annual_report
    for page_no in (3, 21, 35, 90):
        page = doc[page_no]
        expected = len(page_lines(page))
        layout = analyze_page(page, body)
        assigned = sum(len(r.lines) for r in layout.regions)
        assert assigned == expected, f"page {page_no}: {assigned} != {expected}"


def test_heading_levels_rank_by_size():
    levels = heading_levels(9.0, [9.0, 9.0, 11.0, 18.0, 10.0, 8.0])
    assert levels == {18.0: 1, 11.0: 2, 10.0: 3}
    assert heading_levels(9.0, [9.0, 8.0]) == {}
