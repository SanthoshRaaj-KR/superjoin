"""The figure pass: recovering what a chart says when the text layer cannot.

A chart in these PDFs is not a picture of numbers. The numbers are real text,
drawn on top of vector shapes, and text extraction reads them perfectly. What it
cannot do is say which number belongs to which series and which year. Deck page
8 flattens to

    59% 63% 62% 24% 16% 19% 4% 6% 7% 8% 11% 10%
    7,054 7,224 8,142
    FY22 FY23 FY24

Every figure survives; every relationship is gone. Twelve shares, five series,
three years, and nothing to say which is which.

So this pass renders the page as an image and asks a vision model to read the
chart the way a person does. It is not OCR — the characters were never in doubt.
It recovers geometry that flattening destroyed.

**The part that makes this safe.** Vision extraction is normally unverifiable:
if a model reads 5,077 off a bar, nothing can check it. Here something can. The
numbers are in the text layer, so a claim from the figure pass goes through the
same grounding validator as every other claim, against the same raw page text.
A value the model invented is not on the page, so it is quarantined. Vision is
therefore allowed to propose *bindings* but never to introduce *values* — it
can be wrong about which bar a number sits on, and that is a bounded error, but
it cannot conjure a number that was never printed.

That is why this is a second pass rather than a replacement. The text pass reads
prose and tables, where it is stronger and cheaper. The figure pass runs only on
pages whose numbers came back unbound, and its output is held to exactly the
same standard of proof.
"""

from __future__ import annotations

import base64
import logging

import fitz

from ..config import SETTINGS
from ..schemas import PageExtraction
from .client import structured

log = logging.getLogger(__name__)

# Enough to read an axis label on a dense slide, small enough to stay cheap.
# A full deck page at this setting costs roughly 3,800 input tokens.
RENDER_DPI = 170

# Below this share of unbound figures, the text pass has bound the page well
# enough that a second look is not worth its cost.
UNBOUND_THRESHOLD = 0.55

# A page with almost no numbers has nothing for this pass to recover.
MIN_UNBOUND_NUMBERS = 6


SYSTEM = """You read charts and figures from one page of a document.

The page has already been read as text, and that reading recovered every number \
correctly but lost which number belongs to which series and which period. Your \
job is only to restore those relationships.

The single rule that matters: NEVER report a number that is not printed on the \
page. Do not read a value off the height of a bar, do not interpolate against \
an axis, do not compute a total, a share or a growth rate that is not printed. \
If a segment carries no printed label, omit it. An omitted data point costs \
nothing; an invented one is a fabricated fact.

For each data point you can read:
- subject: what the chart is about, e.g. the company or country named on the page
- predicate: the series as the chart labels it, e.g. "Express Parcel revenue", \
copied from the chart title or legend rather than reworded
- value_raw: the printed number, exactly as printed
- unit_raw: from the axis label or the chart title, e.g. "₹ Cr", "Mn", "%"
- period_raw: from the axis label, e.g. "FY24"
- qualifiers: anything the chart states that changes the meaning, including \
footnotes attached to the title such as pro forma or restated
- unknown_qualifiers: axes that matter here and are not stated anywhere
- evidence_quote: the printed number together with the nearby printed text you \
used to identify it — its series label, axis label or chart title. Copy those \
characters exactly as they appear.

Percentages that are shares of a stacked bar should carry the series as the \
predicate and the share as the value, with unit "%".

Set confidence_extraction below 0.7 when segments are unlabelled, when the \
legend order is ambiguous, or when two series could plausibly be swapped, and \
say so in confidence_reasons. Guessing confidently is the one thing worse than \
omitting a point."""


def should_run(unbound: int | None, bound: int | None) -> bool:
    """Whether a page's figures are unbound enough to be worth a second look."""
    unbound = unbound or 0
    bound = bound or 0
    total = unbound + bound
    if unbound < MIN_UNBOUND_NUMBERS or total == 0:
        return False
    return (unbound / total) >= UNBOUND_THRESHOLD


def render_page_png(pdf_path: str, page_no: int, dpi: int = RENDER_DPI) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        return doc[page_no].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def extract_figures(
    pdf_path: str,
    page_no: int,
    profile: dict | None = None,
    model: str | None = None,
) -> PageExtraction:
    """Read a page's charts as an image, into the same schema as the text pass.

    Returning ``PageExtraction`` rather than a figure-specific type is
    deliberate: grounding, storage, comparison and the API stay unaware that a
    second extraction path exists. A claim from a chart is a claim, and it has
    to prove itself the same way.
    """
    png = render_page_png(pdf_path, page_no)
    encoded = base64.b64encode(png).decode()

    context = ""
    if profile:
        bits = [
            f"{k}: {profile[k]}"
            for k in ("primary_entity", "reporting_period", "default_currency")
            if profile.get(k)
        ]
        if bits:
            context = "Document context — " + "; ".join(bits) + "\n"

    return structured(
        response_model=PageExtraction,
        system=SYSTEM,
        user=[
            {
                "type": "text",
                "text": (
                    f"{context}Read every chart on page {page_no}. "
                    "Report only numbers printed on the page."
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ],
        model=model or SETTINGS.model_extract,
    )
