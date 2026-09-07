"""Rendering a scoped page into the text the extractor reads.

This is the join between layout analysis and the model. The output is ordinary
readable text with two additions: headings are marked, and wherever the context
in force changes, the new context is stated inline with the wording it was read
from.

Stating the source wording matters. ``scale=million`` on its own is an assertion
the model must take on trust; ``scale=million (from "(₹ in Million)")`` is
evidence it can check against the page in front of it, and override when the
local text disagrees. The frame is advisory — it is what the section says, not
a fact about every number underneath it.

The rendered text is stored alongside the raw page text rather than replacing
it. The grounding validator checks quotes against the *raw* page, because that
is what the document actually says; the rendered form exists to help the model
read it, and a quote that only matches the rendering is a quote that was
assembled rather than found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import MATERIAL_AXES, ContextFrame, ScopedRow, scope_page
from .pdf.layout import PageLayout, is_data_label


@dataclass
class RenderedPage:
    page_no: int
    text: str
    frames: list[dict]  # one entry per context change, for storage and the UI
    unbound_numbers: int = 0
    bound_numbers: int = 0

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @property
    def unbound_ratio(self) -> float:
        total = self.unbound_numbers + self.bound_numbers
        return self.unbound_numbers / total if total else 0.0


def _frame_line(frame: ContextFrame) -> str:
    """One line describing the context in force, and what is missing from it.

    The wording of the "missing" half matters more than it looks. Saying an axis
    is "not stated anywhere in scope" reads as *unknown, full stop*, and a model
    that believes that will report the axis as unknown even while the sentence
    in front of it says "on a standalone basis" — which is exactly what happened
    on the directors' report page, where the basis is declared inline in prose
    rather than as a section heading.

    That page carries the clearest reconciliation case in the corpus, standalone
    against consolidated revenue for the same year. Losing the basis there does
    not lose a claim; it turns a resolvable difference into an unresolvable one.
    So the line says what is actually true — that *no section declares it* — and
    points at where to look instead.
    """
    parts = frame.as_evidence_lines()
    unknown = frame.unknown_axes()
    if unknown:
        parts.append(
            "no section-level declaration for: "
            + ", ".join(unknown)
            + " — read these from the sentence or table column if stated there, "
            "and only record them as unknown if they are stated nowhere"
        )
    return "[CONTEXT IN FORCE] " + "; ".join(parts) if parts else ""


def render_scoped(page_no: int, scoped: list[ScopedRow]) -> RenderedPage:
    """Turn scoped rows into annotated text plus the frames it went through."""
    lines: list[str] = []
    frames: list[dict] = []
    last_key: tuple | None = None
    last_path: list[str] = []

    for item in scoped:
        key = tuple(sorted(item.frame.as_dict().items()))
        if key != last_key:
            line = _frame_line(item.frame)
            if line:
                lines.append("")
                lines.append(line)
            frames.append(
                {
                    "context": item.frame.as_dict(),
                    "evidence": item.frame.as_evidence_lines(),
                    "unknown": item.frame.unknown_axes(),
                    "heading_path": list(item.heading_path),
                }
            )
            last_key = key

        if item.heading_path != last_path:
            depth = len(item.heading_path)
            if depth and item.heading_path[-1] == item.row.text:
                lines.append("")
                lines.append(f"{'#' * min(depth, 6)} {item.row.text}")
                last_path = list(item.heading_path)
                continue
            last_path = list(item.heading_path)

        lines.append(item.row.text)

    text = "\n".join(lines).strip()
    return RenderedPage(page_no=page_no, text=text, frames=frames)


def count_number_binding(layout: PageLayout) -> tuple[int, int]:
    """Count figures on the page that are attached to something, and figures
    that are floating free.

    A number inside a reconstructed row sits beside its label and its column
    header, so it means something. A number alone on its own line means nothing
    yet: it is a chart data label whose series and period were lost when the
    page was flattened into text.

    This is the honest way to ask whether a page needs more than text
    extraction. It does not try to recognise a chart — recognising charts from
    vector paths does not work here, because a ruled financial table draws more
    paths than a bar chart does. It measures the failure directly instead:
    numbers successfully read but impossible to bind.
    """
    unbound = bound = 0
    for _region, row in layout.iter_rows():
        if row.is_table_row:
            bound += sum(1 for cell in row.cells if is_data_label(cell.text))
        elif is_data_label(row.text):
            unbound += 1
    return unbound, bound


def render_page(layout: PageLayout, profile: dict | None, body_size: float) -> RenderedPage:
    rendered = render_scoped(layout.page_no, scope_page(layout, profile, body_size))
    rendered.unbound_numbers, rendered.bound_numbers = count_number_binding(layout)
    return rendered


def page_unknown_axes(rendered: RenderedPage) -> list[str]:
    """Axes left undeclared in every frame on the page.

    Reported per page so the extractor can be told plainly what nobody stated,
    and so the improvement from adding context inheritance is measurable rather
    than asserted.
    """
    if not rendered.frames:
        return list(MATERIAL_AXES)
    unknown = set(MATERIAL_AXES)
    for frame in rendered.frames:
        unknown &= set(frame["unknown"])
    return sorted(unknown)


# A page needs at least this many numeric tokens before its context coverage
# means anything.
FIGURE_THRESHOLD = 5
_FIGURE = re.compile(r"\d[\d,]*\.?\d*")


def has_figures(text: str) -> bool:
    """Whether a page carries enough numbers for unit context to matter.

    Coverage measured over every page is a misleading number: most pages of a
    prospectus are prose, where "consolidation basis" is not undeclared so much
    as inapplicable. The denominator that means something is pages with figures
    on them.
    """
    return len(_FIGURE.findall(text)) >= FIGURE_THRESHOLD


def declared_axes(rendered: RenderedPage) -> set[str]:
    """Axes declared somewhere on the page."""
    declared: set[str] = set()
    for frame in rendered.frames:
        declared |= set(frame["context"])
    return declared


def figure_context_coverage(rendered: RenderedPage, raw_text: str) -> set[str] | None:
    """Which material axes this page declares, or None if it carries no figures.

    Reported per axis rather than as a single pass/fail. An all-or-nothing score
    asks the wrong question: `consolidation` is an axis of financial statements,
    and an IMF country report has no consolidation basis to declare. Counting it
    as a miss there would make the metric measure document genre rather than
    extraction quality.
    """
    if not has_figures(raw_text):
        return None
    return declared_axes(rendered) & set(MATERIAL_AXES)
