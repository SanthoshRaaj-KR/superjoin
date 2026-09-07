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
from .pdf.layout import PageLayout


@dataclass
class RenderedPage:
    page_no: int
    text: str
    frames: list[dict]  # one entry per context change, for storage and the UI

    @property
    def n_chars(self) -> int:
        return len(self.text)


def _frame_line(frame: ContextFrame) -> str:
    parts = frame.as_evidence_lines()
    unknown = frame.unknown_axes()
    if unknown:
        parts.append("not stated anywhere in scope: " + ", ".join(unknown))
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


def render_page(layout: PageLayout, profile: dict | None, body_size: float) -> RenderedPage:
    return render_scoped(layout.page_no, scope_page(layout, profile, body_size))


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
