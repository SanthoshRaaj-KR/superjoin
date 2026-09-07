"""L1b — context declarations and inheritance.

The single most damaging thing a naive pipeline does to these documents is
separate a number from the words that say what it means. Those words are almost
never beside the number. They are declared once, at section scope, and then
assumed for pages afterwards:

    Consolidated Balance Sheet as at March 31, 2024
    (All amounts in Indian Rupees in million, unless otherwise stated)

Everything below that is printed bare. Chunk it, embed it, and ``81,415.38``
becomes a dimensionless float that will happily be compared against a standalone
figure, or a figure in crore, or one from a different year.

So declarations are detected as declarations, attached to the scope they govern,
and inherited downward. Three rules keep this honest:

**Detection is by shape, not vocabulary.** A parenthetical containing a currency
and a scale token is a unit declaration in any document; a heading containing
"Consolidated" narrows basis wherever it appears. Nothing here matches a phrase
that only appears in one publisher's house style.

**Inheritance is advisory, not binding.** An inherited frame is shown to the
extractor as the context in force, and local text on the page can override it.
A heading's scope is the standard document-outline rule — it runs until the next
heading of equal or higher rank — and that rule sometimes reaches further than a
human would read it. Presenting the frame as evidence rather than stamping it
onto claims is what keeps that error recoverable.

**Silence is recorded.** Axes with no declaration anywhere in scope are listed,
by name, as unknown. That list is what the extractor turns into
``unknown_qualifiers``, and it is the difference between "this figure is
standalone" and "nobody said".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf.layout import PageLayout, Row, heading_levels, is_data_label

# Axes that materially change what a monetary or statistical figure means. When
# one of these is undeclared, the figure is not comparable — it is unlabelled.
MATERIAL_AXES = ("currency", "scale", "consolidation", "period")


@dataclass(frozen=True)
class Declaration:
    """A context axis, its value, and the text it was read from."""

    axis: str
    value: str
    source_text: str
    page_no: int
    scope: str  # heading | note | region

    def as_evidence(self) -> str:
        return f'{self.axis}={self.value} (from "{self.source_text.strip()[:80]}")'


@dataclass
class ContextFrame:
    """The declarations in force at a point in the document."""

    declarations: dict[str, Declaration] = field(default_factory=dict)

    def merged_with(self, others: list[Declaration]) -> "ContextFrame":
        """Return a new frame with ``others`` layered on top. Nearest wins."""
        merged = dict(self.declarations)
        for decl in others:
            merged[decl.axis] = decl
        return ContextFrame(merged)

    def get(self, axis: str) -> str | None:
        decl = self.declarations.get(axis)
        return decl.value if decl else None

    def unknown_axes(self, axes: tuple[str, ...] = MATERIAL_AXES) -> list[str]:
        return [a for a in axes if a not in self.declarations]

    def as_dict(self) -> dict[str, str]:
        return {a: d.value for a, d in self.declarations.items()}

    def as_evidence_lines(self) -> list[str]:
        return [d.as_evidence() for d in self.declarations.values()]


# --- Detection -------------------------------------------------------------
#
# Every pattern below keys on document-neutral shape. Currency symbols, scale
# words, "consolidated"/"standalone", date phrasings and price-base notes are
# conventions of financial and statistical publishing generally, not of any
# document in the starter corpus.

_CURRENCIES = [
    (r"₹|\bRs\.?\b|\bINR\b|\bRupees?\b", "INR"),
    (r"US\$|\bUSD\b|\bU\.S\. dollars?\b", "USD"),
    (r"€|\bEUR\b|\bEuros?\b", "EUR"),
    (r"£|\bGBP\b|\bPounds? sterling\b", "GBP"),
]

_SCALES = [
    (r"\bcrores?\b|\bcr\.?\b", "crore"),
    (r"\blakhs?\b|\blacs?\b", "lakh"),
    (r"\bmillions?\b|\bmn\b|\bmio\b", "million"),
    (r"\bbillions?\b|\bbn\b", "billion"),
    (r"\btrillions?\b|\btn\b", "trillion"),
    (r"\bthousands?\b|'000\b", "thousand"),
]

_PERCENT = re.compile(r"\bper\s?cent\b|\bpercentage\b|\(\s*%\s*\)", re.I)

_CONSOLIDATION = re.compile(r"\b(consolidated|standalone|unconsolidated)\b", re.I)

_PRICE_BASE = re.compile(
    r"\bat\s+(?P<base>\d{4}(?:[-–]\d{2,4})?)\s+prices\b|\b(?P<kind>constant|current)\s+prices\b",
    re.I,
)

_VINTAGE = re.compile(
    r"\b((?:first|second|third|1st|2nd|3rd)\s+)?"
    r"(advance|provisional|revised|quick|budget|interim)\s+estimates?\b",
    re.I,
)

_PRO_FORMA = re.compile(r"\bpro[\s-]?forma\b", re.I)
_RESTATED = re.compile(r"\brestated\b", re.I)

# "Year ended <date>" and "as at <date>" name a period explicitly enough to be
# trusted from an ordinary line. A bare "FY24" or "2024-25" does not — it occurs
# constantly inside prose — so those are read only from headings and notes.
_PERIOD_EXPLICIT = [
    re.compile(
        r"\b(?:for the )?(?:year|period|quarter|half[\s-]year)\s+ended\s+"
        r"(?P<v>\w+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+\w+\s+\d{4})",
        re.I,
    ),
    re.compile(r"\bas\s+(?:at|of|on)\s+(?P<v>\w+\s+\d{1,2},?\s+\d{4})", re.I),
]
_PERIOD_BARE = [
    re.compile(r"\b(?P<v>Q[1-4]\s*FY\s?\d{2,4})\b", re.I),
    re.compile(r"\b(?P<v>FY\s?\d{2,4}(?:[-–]\d{2,4})?)\b", re.I),
    re.compile(r"\b(?P<v>(?:19|20)\d{2}[-–](?:\d{2}|\d{4}))\b"),
]

# The line that separates a declaration from prose. A declaration names a unit
# with no value attached — "(₹ in Million)", "(All amounts in Indian Rupees in
# million)". Prose attaches it to a figure — "increased to ₹85,942.34 million".
# Typography does not distinguish the two: in the annual report's notes, the
# unit declaration is set in the same size and weight as the body text beside
# it. The presence of a value is what distinguishes them, and it is a property
# of financial writing generally rather than of any one document.
_VALUE_USAGE = re.compile(
    r"[₹$€£]\s?\d"
    r"|\d[\d,. ]*\s*%"
    r"|\d[\d,.]*\s+(?:million|billion|crore|lakh|thousand|trillion|mn|bn)\b",
    re.I,
)

# A declaration is a label, not a sentence. Anything longer than this is prose
# that merely mentions the word, and treating prose as a declaration is how a
# whole page gets stamped with the wrong basis.
MAX_DECLARATION_CHARS = 140


def _parentheticals(text: str) -> list[str]:
    return re.findall(r"\(([^()]{1,200})\)", text)


def detect_declarations(
    text: str,
    page_no: int,
    scope: str,
    is_heading: bool = False,
    is_table_row: bool = False,
) -> list[Declaration]:
    """Read every context declaration a single line makes.

    A table row is data, never a declaration: ``Annual Report 2023-24 | 261`` is
    a footer and ``Revenue | 81,415.38 | 72,253.01`` is a fact, and reading a
    reporting period out of either would poison everything below it.
    """
    if is_table_row:
        return []

    found: list[Declaration] = []
    parens = _parentheticals(text)
    declarative = is_heading or len(text) <= MAX_DECLARATION_CHARS

    def add(axis: str, value: str, source: str) -> None:
        if not any(d.axis == axis for d in found):
            found.append(Declaration(axis, value, source, page_no, scope))

    for source in parens + ([text] if declarative else []):
        is_paren = source is not text
        if not _VALUE_USAGE.search(source):
            for pattern, code in _CURRENCIES:
                if re.search(pattern, source, re.I):
                    add("currency", code, source)
                    break
            for pattern, code in _SCALES:
                if re.search(pattern, source, re.I):
                    add("scale", code, source)
                    break
            # Percent is only ever declared in a table note or a heading —
            # "(Per cent)", "(In per cent)". Prose says "as a percentage of"
            # constantly without declaring anything about the numbers nearby.
            if (is_paren or is_heading) and _PERCENT.search(source):
                add("unit_dimension", "percent", source)

        m = _PRICE_BASE.search(source)
        if m:
            add("price_base", (m.group("base") or m.group("kind") or "").lower(), source)

    if not declarative:
        return found

    m = _CONSOLIDATION.search(text)
    # A line naming both bases is discussing them, not choosing one. The
    # directors' report opens with exactly such a sentence, immediately above
    # two paragraphs that each state a different basis.
    both = len({g.lower() for g in _CONSOLIDATION.findall(text)}) > 1
    if m and not both:
        value = m.group(1).lower()
        add("consolidation", "standalone" if value == "unconsolidated" else value, text)

    m = _VINTAGE.search(text)
    if m:
        add("estimate_vintage", " ".join(m.group(0).lower().split()), text)

    if _PRO_FORMA.search(text):
        add("pro_forma", "yes", text)
    if _RESTATED.search(text):
        add("restated", "yes", text)

    patterns = _PERIOD_EXPLICIT + (_PERIOD_BARE if (is_heading or parens) else [])
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            add("period", " ".join(m.group("v").split()), text)
            break

    return found


# --- Inheritance -----------------------------------------------------------


@dataclass
class ScopedRow:
    """One row with the context frame in force where it sits."""

    row: Row
    frame: ContextFrame
    heading_path: list[str]
    region_index: int


def document_frame(profile: dict | None, page_no: int) -> ContextFrame:
    """The base frame: what the document as a whole declares.

    Only fields the profiler actually asserted. A null default stays null —
    filling it with a plausible guess would make an invented context
    indistinguishable from a printed one.
    """
    frame = ContextFrame()
    if not profile:
        return frame

    mapping = {
        "default_currency": "currency",
        "default_scale": "scale",
        "default_consolidation": "consolidation",
        "reporting_period": "period",
    }
    decls = [
        Declaration(axis, str(profile[key]), f"document profile: {key}", page_no, "document")
        for key, axis in mapping.items()
        if profile.get(key)
    ]
    return frame.merged_with(decls)


def scope_page(
    layout: PageLayout, profile: dict | None, body_size: float
) -> list[ScopedRow]:
    """Walk a page in reading order, carrying context down.

    Headings follow the ordinary outline rule: a heading of level L stays in
    force until a heading of level L or higher. Note-style declarations — the
    parenthetical unit lines — attach at the point they appear and hold until
    something overrides them.

    ``body_size`` is measured across the whole document, not this page. A page
    that happens to be all table or all heading has no meaningful internal mode,
    and deriving the threshold from it would invent a hierarchy that is not
    there.
    """
    base = document_frame(profile, layout.page_no)
    levels = heading_levels(body_size, [row.size for _region, row in layout.iter_rows()])

    scoped: list[ScopedRow] = []
    # Each open heading owns the notes seen underneath it. Notes belong to a
    # section, so a *subsection* must inherit them: "(All amounts in Indian
    # Rupees in million)" is printed once under the page title and governs every
    # numbered note below it. Clearing notes whenever any heading appears throws
    # the unit away at the first subheading, which is most of the page.
    stack: list[dict] = []
    # The document-level frame sits at the bottom and is never popped.
    stack.append({"level": 0, "text": "", "decls": [], "notes": []})

    region_index = -1
    last_region = None
    for region, row in layout.iter_rows():
        if region is not last_region:
            region_index += 1
            last_region = region

        text = row.text
        if _is_page_furniture(region, layout):
            # Running headers and footers repeat on every page and belong to
            # none of them. "Annual Report 2023-24 | 261" is a footer, and
            # reading a reporting period out of it would stamp that period on
            # whatever section happens to end near the bottom of the page.
            scoped.append(
                ScopedRow(row, _frame_from(base, stack), _path(stack), region_index)
            )
            continue

        # A bare figure is never a heading, whatever size it is set in. The
        # earnings deck sets 6pt body text and 8pt chart labels, so a purely
        # size-based rule turns every number on a chart page into a section
        # heading — and then inherits context from it.
        level = (
            None
            if row.is_table_row or is_data_label(text)
            else levels.get(row.size)
        )

        if level is not None:
            while len(stack) > 1 and stack[-1]["level"] >= level:
                stack.pop()
            stack.append(
                {
                    "level": level,
                    "text": text,
                    "decls": detect_declarations(
                        text, layout.page_no, "heading", is_heading=True
                    ),
                    "notes": [],
                }
            )
        else:
            stack[-1]["notes"].extend(
                detect_declarations(
                    text, layout.page_no, "note", is_table_row=row.is_table_row
                )
            )

        scoped.append(ScopedRow(row, _frame_from(base, stack), _path(stack), region_index))

    return scoped


def _frame_from(base: ContextFrame, stack: list[dict]) -> ContextFrame:
    inherited: list[Declaration] = []
    for entry in stack:
        inherited.extend(entry["decls"])
        inherited.extend(entry["notes"])
    return base.merged_with(inherited)


def _path(stack: list[dict]) -> list[str]:
    return [e["text"] for e in stack if e["text"]]


# Fraction of page height at the top and bottom treated as running header and
# footer territory.
FURNITURE_MARGIN = 0.08
FURNITURE_MAX_ROWS = 2


def _is_page_furniture(region, layout: PageLayout) -> bool:
    """Whether a region is a running header or footer rather than content.

    Judged by position and size, not by matching known header text: a region of
    one or two short rows pinned to the very top or bottom of the page.
    """
    if len(region.rows) > FURNITURE_MAX_ROWS:
        return False
    margin = layout.height * FURNITURE_MARGIN
    return region.y1 <= margin or region.y0 >= layout.height - margin
