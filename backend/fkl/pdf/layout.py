"""L1a — page layout analysis.

Phase 0 fed the model ``page.get_text()``. That is a single string in whatever
order the PDF happens to store its drawing operations, and for these documents
it is actively misleading: the annual report is typeset as a two-page landscape
spread with two text columns per side, so plain text extraction interleaves four
unrelated columns, and it flattens a financial table into a column of loose
numbers with their row labels somewhere else entirely.

Three things are recovered here, in order.

**Regions.** A recursive XY-cut alternates vertical cuts at whitespace gutters
with horizontal cuts at large vertical gaps. It is the classical algorithm and
it is the right one: a full-width intro paragraph sitting above two columns
cannot be separated by a single vertical scan, because the paragraph bridges the
gutter. Cutting horizontally first, then vertically inside each band, handles it.

**Rows.** Within a region, lines that share a baseline are one row. This is the
piece that makes tables readable. PyMuPDF reports a table row as several
unrelated "lines" — the label at x=53, then a number at x=438, then another at
x=509 — and grouping them by y puts ``Revenue from contracts with customers``
back beside ``81,415.38`` and ``72,253.01``, in that order, with the column
header row directly above it.

**Hierarchy.** Font size is measured, not assumed. The body size is the
character-weighted mode across the whole document, so heading levels are derived
per document rather than from a threshold that happens to suit one publisher.

No pixel measurement or font name here is document-specific. The inputs are
gutters, gaps, baselines and relative font size, which every typeset PDF has.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

import fitz

# A gutter must be at least this multiple of the body font size to count as a
# column boundary. Relative rather than absolute: the annual report sets 9pt
# body text with a 10pt gutter, which a fixed 12pt threshold silently rejects,
# and a document set at 14pt would need a wider one. The floor guards against a
# pathologically small measured body size.
GUTTER_RATIO = 0.75
MIN_GUTTER_FLOOR = 5.0
# A vertical gap this many times the body line height splits stacked regions.
HGAP_RATIO = 1.9
# Baselines within this many points are the same row.
ROW_TOLERANCE = 2.5
# A gutter is only believable once this many lines are in play. Two lines near
# the top of a page always have space between them, and that space is not a
# column boundary.
MIN_BAND_LINES = 4
# Depth of the XY-cut recursion. A two-page landscape spread needs more levels
# than is obvious: halves, then bands, then columns, then bands again inside a
# column. The recursion terminates on its own when no cut is found, so this is a
# guard against pathological input rather than a tuning knob.
MAX_CUT_DEPTH = 8


@dataclass
class Line:
    """One PyMuPDF line: a run of text on a single baseline."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    block_no: int

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Row:
    """Lines sharing a baseline, left to right.

    A prose row has one cell. A table row has several, and rendering them joined
    by a pipe is what turns a scattered set of numbers back into a readable row.
    """

    y0: float
    cells: list[Line]

    @property
    def text(self) -> str:
        if len(self.cells) == 1:
            return self.cells[0].text
        return " | ".join(c.text for c in self.cells)

    @property
    def size(self) -> float:
        return max(c.size for c in self.cells)

    @property
    def bold(self) -> bool:
        return all(c.bold for c in self.cells)

    @property
    def x0(self) -> float:
        return min(c.x0 for c in self.cells)

    @property
    def x1(self) -> float:
        return max(c.x1 for c in self.cells)

    @property
    def is_table_row(self) -> bool:
        return len(self.cells) > 1


@dataclass
class Region:
    """A rectangular block of the page that reads as one column.

    ``lines`` is populated by the cut itself rather than re-derived from the
    rectangle afterwards. Recovering membership from coordinates looks
    equivalent but is not: a line whose start sits exactly on a cut boundary
    lands in both regions or neither, and those are precisely the lines that
    bridge columns.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    lines: list[Line] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


@dataclass
class PageLayout:
    page_no: int
    width: float
    height: float
    regions: list[Region]

    def iter_rows(self):
        for region in self.regions:
            for row in region.rows:
                yield region, row


def page_lines(page: fitz.Page) -> list[Line]:
    """Flatten a page into text lines with geometry and font attributes."""
    lines: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # skip images
            continue
        for line in block["lines"]:
            spans = line.get("spans") or []
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            # Size and weight of the longest span: a line with a bold lead-in
            # should be classified by its dominant run, not its first character.
            lead = max(spans, key=lambda s: len(s["text"]))
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                Line(
                    text=text,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    size=round(lead["size"], 1),
                    bold="bold" in lead["font"].lower(),
                    block_no=block["number"],
                )
            )
    return lines


def body_font_size(lines: list[Line]) -> float:
    """The character-weighted modal font size: what body text is set in.

    Weighting by characters rather than counting lines matters — a document with
    many short bold headings and few long paragraphs would otherwise report the
    heading size as its body size and find no headings at all.
    """
    counter: Counter[float] = Counter()
    for line in lines:
        counter[line.size] += len(line.text)
    if not counter:
        return 10.0
    return counter.most_common(1)[0][0]


def _find_gutter_band(
    lines: list[Line], x0: float, x1: float, body_size: float
) -> tuple[float, float] | None:
    """Find a column gutter and the depth to which it survives.

    Requiring a gutter to run a region's full height is too strict for real
    documents. On the annual report's financial pages two prose columns sit above
    a single full-width sentence, which sits above a table: the gutter is real
    for the top third and then closes. A full-height scan finds nothing and the
    columns get read interleaved.

    The question is not "does some gutter exist" but "how far down does *this*
    gutter survive", and the two are easy to confuse — as lines are added, the
    widest gap keeps changing identity, so tracking the widest gap at each step
    measures nothing.

    Instead, record for each x the topmost y at which anything covers it. A
    candidate gutter spanning ``[a, b]`` then dies at ``min(first_cover[a:b])``,
    and the best gutter is the one that survives deepest.

    Returns ``(gutter_x, death_y)``, where an infinite ``death_y`` means the
    gutter holds for the whole region and a plain vertical cut is correct.
    """
    min_gutter = max(MIN_GUTTER_FLOOR, body_size * GUTTER_RATIO)
    width = int(x1 - x0) + 1
    if width < 3 * min_gutter or len(lines) < MIN_BAND_LINES:
        return None

    inf = float("inf")
    first_cover = [inf] * width
    for line in lines:
        a = max(0, int(line.x0 - x0))
        b = min(width - 1, int(line.x1 - x0))
        for i in range(a, b + 1):
            if line.y0 < first_cover[i]:
                first_cover[i] = line.y0

    w = max(1, int(min_gutter))

    def sides_have_content(a: int, b: int, death: float) -> bool:
        """Text on both sides above the closing point, else it is a margin."""
        left = min(first_cover[:a], default=inf)
        right = min(first_cover[b + 1 :], default=inf)
        return left < death and right < death

    def widen(a: int, b: int, death: float) -> tuple[int, int]:
        while a > 0 and first_cover[a - 1] >= death:
            a -= 1
        while b < width - 1 and first_cover[b + 1] >= death:
            b += 1
        return a, b

    # Pass 1: gutters that are never crossed anywhere in the region. Handled
    # separately because every one of them "dies" at infinity, so a single scan
    # cannot rank them against each other — and the page's left margin is one of
    # them, which is how a naive scan ends up cutting off the margin instead of
    # splitting the columns.
    best_full: tuple[int, int, int] | None = None  # (run width, a, b)
    start: int | None = None
    for i in range(width + 1):
        never = i < width and first_cover[i] == inf
        if never and start is None:
            start = i
        elif not never and start is not None:
            a, b = start, i - 1
            run = b - a + 1
            if (
                run >= w
                and sides_have_content(a, b, inf)
                and (best_full is None or run > best_full[0])
            ):
                best_full = (run, a, b)
            start = None
    if best_full is not None:
        _, a, b = best_full
        return x0 + (a + b) / 2, inf

    # Pass 2: no gutter survives the whole region, so find the one that reaches
    # deepest before something bridges it.
    best_death = 0.0
    best_span: tuple[int, int] | None = None
    for a in range(1, width - w):
        death = min(first_cover[a : a + w])
        if death != inf and death > best_death:
            best_death, best_span = death, (a, a + w - 1)

    if best_span is None or best_death <= 0:
        return None

    a, b = widen(*best_span, best_death)
    if not sides_have_content(a, b, best_death):
        return None

    return x0 + (a + b) / 2, best_death


def _find_hgap(lines: list[Line], body_size: float) -> float | None:
    """The y of the largest vertical gap, if it is big enough to split regions."""
    if len(lines) < 4:
        return None
    ordered = sorted(lines, key=lambda ln: ln.y0)
    line_height = statistics.median(ln.height for ln in ordered) or body_size
    threshold = line_height * HGAP_RATIO

    best_gap = 0.0
    best_y = None
    bottom = ordered[0].y1
    for line in ordered[1:]:
        gap = line.y0 - bottom
        if gap > best_gap:
            best_gap, best_y = gap, (bottom + line.y0) / 2
        bottom = max(bottom, line.y1)
    return best_y if best_gap >= threshold else None


def xy_cut(
    lines: list[Line],
    region: tuple[float, float, float, float],
    body_size: float,
    depth: int = MAX_CUT_DEPTH,
) -> list[Region]:
    """Split a page into reading-order regions by recursive XY-cut.

    Vertical cuts are tried first, then horizontal. A page whose columns are
    bridged by a full-width paragraph yields no vertical cut, falls through to a
    horizontal one, and the columns are then found inside the lower band — which
    is exactly the layout of the annual report pages that matter here.
    """
    x0, y0, x1, y1 = region
    if not lines:
        return []
    if depth <= 0:
        return [Region(x0, y0, x1, y1, list(lines))]

    found = _find_gutter_band(lines, x0, x1, body_size)
    if found is not None:
        gutter, death_y = found
        above = [ln for ln in lines if ln.y0 < death_y]
        below = [ln for ln in lines if ln.y0 >= death_y]

        if not below:
            # The gutter holds throughout: a straight vertical cut.
            left = [ln for ln in lines if ln.x1 <= gutter]
            right = [ln for ln in lines if ln.x1 > gutter]
            if left and right:
                return xy_cut(left, (x0, y0, gutter, y1), body_size, depth - 1) + xy_cut(
                    right, (gutter, y0, x1, y1), body_size, depth - 1
                )
        elif len(above) >= MIN_BAND_LINES:
            # The gutter closes partway down. Cut horizontally where it closes;
            # the columns are then found above, and whatever bridged the gutter
            # is handled below on its own terms.
            return xy_cut(above, (x0, y0, x1, death_y), body_size, depth - 1) + xy_cut(
                below, (x0, death_y, x1, y1), body_size, depth - 1
            )

    split_y = _find_hgap(lines, body_size)
    if split_y is not None:
        top = [ln for ln in lines if ln.y0 < split_y]
        bottom = [ln for ln in lines if ln.y0 >= split_y]
        if top and bottom:
            return xy_cut(top, (x0, y0, x1, split_y), body_size, depth - 1) + xy_cut(
                bottom, (x0, split_y, x1, y1), body_size, depth - 1
            )

    return [Region(x0, y0, x1, y1, list(lines))]


def group_rows(lines: list[Line]) -> list[Row]:
    """Group lines sharing a baseline into rows, each ordered left to right."""
    rows: list[Row] = []
    for line in sorted(lines, key=lambda ln: (ln.y0, ln.x0)):
        if rows and abs(line.y0 - rows[-1].y0) <= ROW_TOLERANCE:
            rows[-1].cells.append(line)
        else:
            rows.append(Row(y0=line.y0, cells=[line]))
    for row in rows:
        row.cells.sort(key=lambda c: c.x0)
    return rows


def analyze_page(page: fitz.Page, body_size: float) -> PageLayout:
    """Full layout pass over one page."""
    lines = page_lines(page)
    rect = page.rect
    regions = xy_cut(lines, (rect.x0, rect.y0, rect.x1, rect.y1), body_size)

    # Rows are formed after the cut, never before. Grouping by baseline across
    # column boundaries is exactly what fabricates rows joining a table value to
    # unrelated prose from the next column.
    for region in regions:
        region.rows = group_rows(region.lines)

    # Regions are already in reading order: the XY-cut emits left before right
    # and top before bottom at every level, so the recursion order is the order a
    # human reads the page. Re-sorting by coordinate would undo that and
    # interleave the two halves of a spread.
    return PageLayout(page_no=page.number, width=rect.width, height=rect.height, regions=regions)


def heading_levels(body_size: float, sizes: list[float]) -> dict[float, int]:
    """Map each above-body font size to a heading level, largest as level 1."""
    distinct = sorted({s for s in sizes if s > body_size + 0.4}, reverse=True)
    return {size: i + 1 for i, size in enumerate(distinct)}
