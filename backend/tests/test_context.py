"""Context detection and inheritance.

Two failure modes are worth more than the happy path here, and both have their
own tests: missing a real declaration (every figure below it loses its unit),
and reading a declaration out of prose (a whole section gets stamped with a
basis or period nobody claimed). The second is worse — a wrong context is not
recoverable downstream, whereas a missing one is recorded as unknown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.context import detect_declarations, scope_page  # noqa: E402
from fkl.pdf.layout import analyze_page, body_font_size, page_lines  # noqa: E402
from fkl.render import (  # noqa: E402
    declared_axes,
    has_figures,
    page_unknown_axes,
    render_page,
)

REPO = Path(__file__).resolve().parents[2]
AR = REPO / "starter-datasets/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf"
RBI = REPO / "starter-datasets/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf"


def decl_dict(text, **kwargs):
    return {d.axis: d.value for d in detect_declarations(text, 0, "note", **kwargs)}


# --- what a declaration is --------------------------------------------------


def test_unit_declarations_are_read_from_notes_and_headings():
    assert decl_dict("(₹ in Million)") == {"currency": "INR", "scale": "million"}
    assert decl_dict("(All amounts in Indian Rupees in million, unless otherwise stated)") == {
        "currency": "INR",
        "scale": "million",
    }
    assert decl_dict("(Rs. in crore)") == {"currency": "INR", "scale": "crore"}
    assert decl_dict("(Per cent)")["unit_dimension"] == "percent"
    assert decl_dict("(At 2011-12 Prices)")["price_base"] == "2011-12"


def test_prose_that_uses_a_unit_does_not_declare_one():
    """The distinction the whole detector turns on.

    "increased to ₹85,942.34 million" states a value; "(₹ in Million)" states a
    convention. Typography does not separate them — in the annual report's notes
    the declaration is set in the same size and weight as the prose beside it —
    so the presence of an attached figure is what does.
    """
    assert decl_dict("y Total income increased by 14.13% to ₹85,942.34 million") == {}
    assert decl_dict("from ₹10,077.79 million for FY23.") == {}
    assert decl_dict("employee benefits expense decreased as a percentage of") == {}


def test_a_line_naming_both_bases_declares_neither():
    """The directors' report opens with exactly this sentence, immediately above
    two paragraphs that each state a different basis. Picking whichever word
    came first would mislabel one of them."""
    text = "The Standalone and Consolidated Financial Statements of your Company for FY24"
    assert "consolidation" not in decl_dict(text)
    assert decl_dict("Consolidated financial performance")["consolidation"] == "consolidated"
    assert decl_dict("Standalone Balance Sheet")["consolidation"] == "standalone"


def test_bare_period_tokens_need_a_heading_or_a_parenthetical():
    """`FY23` appears in ordinary sentences constantly. `year ended <date>` does
    not, so that phrasing is trusted from any short line."""
    assert "period" not in decl_dict("a reduction of loss by 79.32% over FY23")
    assert decl_dict("Financial highlights FY2023-24", is_heading=True)["period"] == "FY2023-24"
    assert (
        decl_dict("To the Consolidated Financial Statements for the year ended March 31, 2024")[
            "period"
        ]
        == "March 31, 2024"
    )


def test_table_rows_never_declare_context():
    """`Annual Report 2023-24 | 261` is a page footer and `Revenue | 81,415.38`
    is data. Reading a period out of either would poison the page."""
    assert decl_dict("Annual Report 2023-24 | 261", is_table_row=True) == {}
    assert decl_dict("Particulars | March 31, 2024", is_table_row=True) == {}


def test_long_prose_is_never_a_declaration():
    long_prose = (
        "The consolidated financial statements have been prepared on a going concern "
        "basis in accordance with the applicable provisions of the Companies Act, 2013 "
        "and the rules made thereunder, as amended from time to time by the ministry."
    )
    assert decl_dict(long_prose) == {}


# --- inheritance ------------------------------------------------------------


@pytest.fixture(scope="module")
def annual_report():
    doc = fitz.open(AR)
    body = body_font_size([ln for page in doc for ln in page_lines(page)])
    yield doc, body
    doc.close()


def test_unit_declared_once_reaches_the_table_below_it(annual_report):
    """Page 35 prints `(₹ in Million)` once, above a table of bare numbers.

    This is the case the layer exists for: without inheritance, 81,415.38 is a
    dimensionless float, and comparing it to a figure in crore is a silent error
    rather than a caught one.
    """
    doc, body = annual_report
    scoped = scope_page(analyze_page(doc[35], body), None, body)

    row = next(s for s in scoped if s.row.text.startswith("Revenue from contracts"))
    assert row.frame.get("currency") == "INR"
    assert row.frame.get("scale") == "million"
    assert row.frame.get("consolidation") == "consolidated"


def test_a_subsection_inherits_its_parents_notes(annual_report):
    """Page 90 declares its unit under the page title, then runs a dozen
    numbered subsections beneath it.

    Clearing notes at every heading loses the unit at the first subheading,
    which is most of the page — so notes belong to the section that owns them
    and survive into its children.
    """
    doc, body = annual_report
    scoped = scope_page(analyze_page(doc[90], body), None, body)

    deep = [s for s in scoped if len(s.heading_path) >= 2 and s.frame.get("scale")]
    assert deep, "no subsection inherited the page-level unit declaration"
    assert deep[0].frame.get("currency") == "INR"
    assert deep[0].frame.get("consolidation") == "consolidated"


def test_running_footers_do_not_declare_a_period(annual_report):
    """`Annual Report 2023-24 | 261` sits at the foot of every page."""
    doc, body = annual_report
    for page_no in (35, 90):
        scoped = scope_page(analyze_page(doc[page_no], body), None, body)
        for item in scoped:
            decl = item.frame.declarations.get("period")
            if decl:
                assert "Annual Report 2023-24" not in decl.source_text


def test_document_defaults_are_the_base_but_never_invented(annual_report):
    doc, body = annual_report
    profile = {"default_currency": "INR", "default_scale": "million", "reporting_period": None}
    scoped = scope_page(analyze_page(doc[3], body), profile, body)
    assert scoped[0].frame.get("currency") == "INR"
    # A null profile field stays absent rather than becoming a guess.
    assert scoped[0].frame.get("period") is None


def test_estimate_vintage_is_discovered_in_a_footnote():
    """The RBI report qualifies its GDP figures in a footnote.

    That axis is what separates a 6.4% estimate from a 6.5% one without either
    being wrong, and nothing in the code names this document or this phrase.
    """
    doc = fitz.open(RBI)
    body = body_font_size([ln for page in doc for ln in page_lines(page)])
    rendered = render_page(analyze_page(doc[7], body), None, body)
    doc.close()
    assert "estimate_vintage" in declared_axes(rendered)


def test_rendered_page_states_context_and_keeps_the_table(annual_report):
    doc, body = annual_report
    rendered = render_page(analyze_page(doc[35], body), None, body)

    assert "[CONTEXT IN FORCE]" in rendered.text
    assert "Revenue from contracts with customers | 81,415.38 | 72,253.01" in rendered.text

    # Whatever is still undeclared must be named, not left implicit — and named
    # as a missing *section-level* declaration rather than as an unknown. A live
    # run showed the difference is not cosmetic: told an axis was "not stated
    # anywhere in scope", the extractor reported consolidation as unknown on
    # both the standalone and the consolidated revenue figure, while the
    # sentence beside each one said which basis it was.
    assert "no section-level declaration for" in rendered.text
    assert "read these from the sentence or table column" in rendered.text

    # `period` is correctly *not* declared at section scope on this page. The
    # table carries two periods side by side in its column headers, so a single
    # section-level period would be wrong. The header row is preserved directly
    # above the data, which is where that distinction has to be read from.
    assert page_unknown_axes(rendered) == ["period"]
    lines = rendered.text.splitlines()
    header = lines.index("Particulars | March 31, 2024 | March 31, 2023")
    assert lines[header + 1].startswith("Revenue from contracts with customers")
    assert declared_axes(rendered) >= {"currency", "scale", "consolidation"}


def test_has_figures_separates_prose_pages_from_data_pages(annual_report):
    doc, _body = annual_report
    assert has_figures(doc[35].get_text())
    assert not has_figures("A page of prose with no measurements on it at all.")
