"""The source page as an image, with the evidence highlighted on it.

The plan asked for evidence *crops* from both source pages, and until now the
interface showed the evidence *text* instead - the quote, with the value marked
inside it. That is not the same claim. A quote is something this system
produced; a picture of the page is something the document produced, and the
whole argument of the project is that a reader can check a fact against its
source. Rendering the page and drawing a box around the sentence is what makes
that checkable without leaving the interface.

**Why search the page rather than trust stored coordinates.** Claims carry
``evidence_start`` and ``evidence_end``, but those are offsets into the
*normalised* text - ligatures folded, whitespace collapsed, rupee signs
regularised - and PyMuPDF's geometry is indexed against the raw glyphs. Mapping
one onto the other means reconstructing the normalisation in reverse, which
fails silently at exactly the hyphenated and ligatured spots where it matters.
Searching the page for the quote is slower and cannot lie: either the text is
found on the page, in which case the box is where the words are, or it is not,
in which case there is no box and the reader sees the plain page.

**Why the fallback shrinks the quote rather than giving up.** A long quote
spanning a line break often will not match as one string, because the document
hyphenated a word across it. Searching for the longest leading fragment that
does match still lands the reader in the right paragraph, which is the job.
"""

from __future__ import annotations

import io
import logging
import re

import fitz

log = logging.getLogger(__name__)

DEFAULT_DPI = 110
MAX_DPI = 220

# Drawn under the text rather than over it, so the words stay readable. The
# colours are the interface's own accent at low opacity - the quote in amber,
# the value inside it in a stronger tint.
QUOTE_FILL = (0.99, 0.90, 0.62)
VALUE_FILL = (0.98, 0.78, 0.30)
QUOTE_OPACITY = 0.40
VALUE_OPACITY = 0.65

MIN_FRAGMENT = 24
PAD = 14


# Heading markers the renderer adds. They are not on the page, so a quote that
# carries them is unfindable until they come off - which is how four of the
# corpus's evidence spans went unhighlighted while looking like page-layout
# failures rather than the formatting artefact they were.
_MARKUP = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKUP.sub("", text or "")).strip()


def _find_phrase(page: "fitz.Page", needle: str) -> list["fitz.Rect"]:
    """Locate a phrase, shortening it until it is found.

    ``search_for`` matches across line breaks but not across a hyphenation the
    document inserted, so a quote that reads cleanly in the store may exist on
    the page in two pieces. Backing off to the longest leading fragment that
    does match keeps the highlight honest: it covers the part of the sentence
    that was actually located, and never a region guessed at.
    """
    needle = _clean(needle)
    if len(needle) < MIN_FRAGMENT:
        return page.search_for(needle, quads=False) if needle else []
    for length in (len(needle), 160, 120, 90, 60, 40, MIN_FRAGMENT):
        if length > len(needle):
            continue
        fragment = needle[:length].rsplit(" ", 1)[0] if length < len(needle) else needle
        if len(fragment) < MIN_FRAGMENT:
            break
        rects = page.search_for(fragment, quads=False)
        if rects:
            return rects
    return []


# How far from the anchor a second piece of the same evidence may sit before it
# is a different part of the page. Generous enough for a table row and the
# caption under it; short of the next section.
NEIGHBOURHOOD = 120.0


def _find(page: "fitz.Page", needle: str) -> list["fitz.Rect"]:
    """Locate the evidence, whether it is a sentence, a table row or a tile.

    A quote taken from prose is a contiguous string and searching for it works.
    Much of this corpus is not prose. The store holds
    ``Revenue from contracts with customers | 81,415.38 | 72,253.01``, which is
    a *reconstruction*: the label sits at the left margin and the two figures
    sit in columns some centimetres away, with nothing between them that any
    search would match. It holds ``740 Mn`` above ``Express parcel shipments in
    FY24``, which is a chart tile - two pieces of text that are one fact and
    are nowhere adjacent. Searching for either string whole finds nothing, and
    the first version of this returned an unhighlighted page as though the
    evidence could not be verified, which is a far worse thing to show than a
    missing box.

    So the evidence is highlighted piece by piece. The most distinctive piece -
    the longest, which in practice is always the label rather than the figure -
    anchors it, and every other piece is taken at its occurrence *nearest that
    anchor*. The nearness rule is what keeps this honest: a bare ``81,415.38``
    can appear several times on a financial page, and boxing every occurrence
    would point at the wrong row with exactly the same confidence as the right
    one.
    """
    pieces = [_clean(p) for p in re.split(r"[|\n]+", needle or "")]
    pieces = [p for p in pieces if p]
    if not pieces:
        return []
    if len(pieces) == 1:
        return _find_phrase(page, pieces[0])

    order = sorted(pieces, key=len, reverse=True)
    anchor: "fitz.Rect | None" = None
    for piece in order:
        hits = _find_phrase(page, piece)
        if hits:
            anchor, pieces = hits[0], [p for p in pieces if p != piece]
            break
    if anchor is None:
        return _find_phrase(page, " ".join(order))

    found = [anchor]
    for piece in pieces:
        near = [
            rect for rect in page.search_for(piece, quads=False)
            if abs(rect.y0 - anchor.y0) <= NEIGHBOURHOOD
        ]
        if near:
            found.append(min(near, key=lambda r: abs(r.y0 - anchor.y0)
                             + abs(r.x0 - anchor.x0) / 10))
    return found


def _shade(page: "fitz.Page", rects: list["fitz.Rect"], fill, opacity: float) -> None:
    for rect in rects:
        annot = page.add_highlight_annot(rect)
        annot.set_colors(stroke=fill)
        annot.set_opacity(opacity)
        annot.update()


def render(
    pdf_path: str,
    page_no: int,
    *,
    quote: str = "",
    value: str = "",
    dpi: int = DEFAULT_DPI,
    crop: bool = False,
) -> tuple[bytes, bool]:
    """One page as PNG. Returns the bytes and whether the quote was located.

    ``crop=True`` returns just the neighbourhood of the evidence rather than
    the whole page, which is what the comparison view wants: two full A4 pages
    side by side reduce the sentence under discussion to a few illegible
    pixels. When the quote cannot be found there is nothing to crop to, so the
    full page comes back instead - visibly the whole page, rather than an
    arbitrary region presented as if it were the evidence.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_no]
        found = _find(page, quote) if quote else []
        if found:
            _shade(page, found, QUOTE_FILL, QUOTE_OPACITY)
            if value:
                inner = [r for r in page.search_for(_clean(value), quads=False)
                         if any(r in box for box in found)]
                _shade(page, inner, VALUE_FILL, VALUE_OPACITY)

        clip = None
        if crop and found:
            box = found[0]
            for rect in found[1:]:
                box |= rect
            # Vertically only, and across the whole page width. Cropping
            # horizontally to the span plus a margin looks tidier in isolation
            # and reads badly: on a two-column page the margin runs into the
            # next column and cuts it mid-word, so the reader is shown a
            # fragment of an unrelated sentence beside their evidence. The
            # height is where the saving is anyway — a full page reduces the
            # line under discussion to a few pixels; a full-width band does
            # not.
            clip = fitz.Rect(
                page.rect.x0,
                max(page.rect.y0, box.y0 - PAD * 2),
                page.rect.x1,
                min(page.rect.y1, box.y1 + PAD * 2),
            )

        pixmap = page.get_pixmap(dpi=min(dpi, MAX_DPI), clip=clip)
        return pixmap.tobytes("png"), bool(found)
    finally:
        doc.close()


def png_or_none(pdf_path: str, page_no: int, **kwargs) -> tuple[bytes, bool] | None:
    """Render, or return None when the file is gone.

    Source PDFs are referenced by the path they were ingested from, and a
    grader browsing a database that shipped with the repository will not have
    them. That is a normal state, not an error - the claims, the quotes and
    every verdict are still there. The interface falls back to the quote text.
    """
    try:
        return render(pdf_path, page_no, **kwargs)
    except Exception as exc:  # pragma: no cover - depends on the local filesystem
        log.info("cannot render %s p%s: %s", pdf_path, page_no, exc)
        return None


def as_stream(blob: bytes) -> io.BytesIO:
    return io.BytesIO(blob)
