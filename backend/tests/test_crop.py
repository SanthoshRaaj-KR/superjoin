"""Finding the evidence on the page it came from.

These run against a real PDF from the starter set, because the thing being
tested is how this corpus's quotes relate to this corpus's page geometry, and a
synthetic page would agree with whatever the code currently does.

The interesting cases are the ones that are not sentences. Most evidence in
these documents is a reconstructed table row or a chart tile — text that is one
fact and is nowhere contiguous on the page — and the first version of this
searched for the stored string, found nothing, and returned an unhighlighted
page as though the evidence could not be verified.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.crop import _clean, _find, png_or_none, render  # noqa: E402

PDF = (Path(__file__).resolve().parents[2] / "starter-datasets" / "delhivery"
       / "02-delhivery-annual-report-fy24-excerpt.pdf")

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="starter dataset absent")


@pytest.fixture(scope="module")
def page():
    doc = fitz.open(PDF)
    yield doc[35]
    doc.close()


def test_a_reconstructed_table_row_is_located_cell_by_cell(page):
    """The store holds `label | figure | figure`, which exists on the page as a
    label at the left margin and two figures in columns some centimetres away.
    Searching for that string whole finds nothing at all."""
    quote = "Revenue from contracts with customers | 81,415.38 | 72,253.01"
    boxes = _find(page, quote)

    assert len(boxes) == 3
    # All on one line: a bare figure repeats down a financial page, and boxing
    # every occurrence points at the wrong row as confidently as the right one.
    tops = {round(b.y0, 1) for b in boxes}
    assert len(tops) == 1


def test_heading_markers_do_not_make_a_quote_unfindable(page):
    """The renderer marks headings with `###`. Those characters are not on the
    page, and leaving them in looked like a layout failure rather than the
    formatting artefact it was."""
    assert "#" not in _clean("### Express parcel shipments in FY24")
    assert _clean("740 Mn\n\n### Express parcel") == "740 Mn Express parcel"


def test_a_plain_sentence_is_located_whole(page):
    text = page.get_text()
    sentence = next(line.strip() for line in text.splitlines()
                    if len(line.strip()) > 40)
    assert _find(page, sentence)


def test_a_quote_that_is_not_on_the_page_produces_no_box(page):
    assert _find(page, "this sentence appears in no annual report anywhere") == []


def test_the_crop_keeps_the_full_page_width(page):
    """Cropping horizontally to the span plus a margin runs into the next
    column on a two-column page and cuts it mid-word, so the reader is shown a
    fragment of an unrelated sentence beside their evidence."""
    blob, located = render(
        str(PDF), 35, quote="Revenue from contracts with customers | 81,415.38",
        value="81,415.38", crop=True)
    assert located is True
    image = fitz.open("png", blob)
    # Same aspect ratio as the page width implies the full width was kept.
    assert image[0].rect.width / image[0].rect.height > 2


def test_a_missing_source_pdf_is_a_normal_state(tmp_path):
    """A database browsed without its documents is not an error — every claim,
    quote and verdict is still there — so this returns None and the interface
    falls back to the quote."""
    assert png_or_none(str(tmp_path / "gone.pdf"), 0, quote="x") is None
