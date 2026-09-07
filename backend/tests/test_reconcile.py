"""The second look a contradiction has to survive.

The layer's value depends entirely on it being hard to fool, so most of these
tests are about what it *refuses* to do. A reconciliation pass that accepts
whatever a model proposes does not reduce false contradictions — it converts
them into false explanations, which are worse, because a contradiction is
visible in the output and an explanation is not.

The investigator is injected, so every path here runs without an API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.compare import (  # noqa: E402
    CONTEXTUAL,
    CONTRADICTS,
    INSUFFICIENT_EVIDENCE,
    Comparable,
)
from fkl.periods import parse_period  # noqa: E402
from fkl.reconcile import (  # noqa: E402
    Recovery,
    reconcile,
    scout,
    scout_sign_convention,
)
from fkl.units import parse_unit  # noqa: E402
from fkl.values import written_precision  # noqa: E402


def claim(ref, value, *, metric="real GDP growth", unit="per cent", period="FY26",
          quote="", page=12, **kwargs) -> Comparable:
    u = parse_unit(unit)
    number = float(str(value).replace(",", "").replace("(", "-").replace(")", ""))
    return Comparable(
        ref=ref, entity="india", metric=metric, unit=u, period=parse_period(period),
        value_canonical=u.canonical(number),
        precision=written_precision(str(value), u.scale),
        value_text=str(value), evidence_quote=quote, page_no=page,
        document="3", **kwargs,
    )


def never(*_args, **_kwargs):
    """An investigator that finds nothing."""
    return None


def proposing(**fields):
    """An investigator that always proposes the given recovery."""
    base = dict(method="investigated", confidence=0.9, reason="because")
    base.update(fields)

    def investigator(*_args, **_kwargs):
        return Recovery(**base)

    return investigator


# --- the case that needs no model at all ------------------------------------


SUMMARY_PAGE = """Statement of Profit and Loss (Summary)
Particulars | FY22 | FY23 | FY24
Less: Exceptional Items | 738.99 | 113.11 | 224.10
Profit before tax | (1,891.13) | (1,007.66) | (249.28)
"""

STATEMENT_PAGE = """Consolidated Statement of Profit and Loss
Exceptional items | (224.10) | -
Tax expense | 12.05 | 9.90
"""


def test_a_sign_convention_is_resolved_without_a_model_call():
    """The annual report prints one figure two ways, nine pages apart:
    `(224.10)` in the consolidated statement, and `224.10` under a row labelled
    `Less:` in the summary. Parentheses and the word "Less" carry exactly the
    same information, and the system modelled only the first — so the same
    rupees read as 200% apart.

    Deterministic on purpose: matching magnitudes, opposing signs, and a
    sign-carrying label found on the page. No judgement is required and none is
    asked for.
    """
    a = claim("a", "(224.10)", metric="exceptional items", unit="INR million",
              period="FY24", quote="Exceptional items | (224.10) | -")
    b = claim("b", "224.10", metric="exceptional items", unit="INR million",
              period="FY24", quote="Less: Exceptional Items | 738.99 | 113.11 | 224.10")

    direct = scout_sign_convention(a, b, STATEMENT_PAGE, SUMMARY_PAGE)
    assert direct is not None
    assert direct.axis == "sign_convention"
    assert {direct.a_value, direct.b_value} == {
        "parenthesised", "subtracted in a labelled row"}

    result = reconcile(a, b, a_page=STATEMENT_PAGE, b_page=SUMMARY_PAGE,
                       investigator=never)
    assert result.changed
    assert result.verdict.verdict == CONTEXTUAL
    assert result.verdict.axis == "sign_convention"


def test_two_genuinely_opposed_figures_are_not_a_sign_convention():
    """The scout needs matching magnitudes. A profit of 200 against a loss of
    900 is a disagreement, not a printing convention."""
    a = claim("a", "(900.00)", metric="exceptional items", unit="INR million",
              period="FY24", quote="Exceptional items | (900.00)")
    b = claim("b", "200.00", metric="exceptional items", unit="INR million",
              period="FY24", quote="Less: Exceptional Items | 200.00")
    assert scout_sign_convention(a, b, "", "") is None


# --- scouts -----------------------------------------------------------------


def test_the_shared_span_scout_notices_one_sentence_holding_two_facts():
    """"Real hourly wages have grown by 16 and 26 percent since 2018 in rural
    and urban areas, respectively." Two claims, one quote, and the thing that
    separates them is sitting inside the quote both of them already carry."""
    quote = ("Real hourly wages have grown by 16 and 26 percent since 2018 in "
             "rural and urban areas, respectively.")
    a = claim("a", "16", metric="real hourly wages growth", period="2018", quote=quote)
    b = claim("b", "26", metric="real hourly wages growth", period="2018", quote=quote)

    leads = scout(a, b, quote, quote)
    assert leads[0].scout == "shared_span"
    # The coordinating word is what raises it above a generic overlap.
    assert leads[0].strength >= 0.9


SCENARIO_PAGE = """Table 4. Growth under alternative tariff assumptions
 | FY2025/26 | FY2026/27
July WEO | 6.4 | 6.4
Current | 6.6 | 6.2
Note: This scenario assumes that US tariffs on India are reduced.
"""


def test_the_row_label_scout_finds_the_axis_the_extractor_dropped():
    """The IMF's scenario table is two rows over the same two columns. The
    extraction kept the column — the period — and dropped the row, so four
    claims came back looking like two contradictions. The row label is the
    axis, and it is `estimate_vintage`, which this system already has."""
    a = claim("a", "6.4", quote="July WEO | 6.4 | 6.4")
    b = claim("b", "6.6", quote="Current | 6.6 | 6.2")

    leads = scout(a, b, SCENARIO_PAGE, SCENARIO_PAGE)
    by_scout = {l.scout: l for l in leads}
    assert "row_label" in by_scout
    assert "July WEO" in by_scout["row_label"].note
    assert "Current" in by_scout["row_label"].note


# --- what it refuses to do --------------------------------------------------


def test_an_axis_that_is_not_on_the_page_is_discarded():
    """The failure mode this layer would otherwise introduce. A model asked
    "what distinguishes these?" will find something, and an ungrounded
    explanation is worse than a visible contradiction: the conflict disappears
    from the output and nobody is told it was ever there."""
    a = claim("a", "6.5", quote="growth is projected at 6.5 per cent")
    b = claim("b", "6.6", quote="growth is projected at 6.6 per cent")

    result = reconcile(
        a, b, a_page=a.evidence_quote, b_page=b.evidence_quote,
        investigator=proposing(
            axis="scenario", a_value="baseline", b_value="upside",
            a_evidence="under the baseline scenario",   # nowhere on the page
            b_evidence="under the upside scenario",
        ),
    )
    assert not result.changed
    assert result.verdict.verdict == CONTRADICTS


def test_an_axis_value_must_appear_inside_its_own_evidence():
    """A span that is really on the page, quoted to support a value that is not
    in it. Locating the span is necessary and not sufficient."""
    page = "Real GDP growth is projected at 6.5 per cent for 2025-26."
    a = claim("a", "6.5", quote=page)
    b = claim("b", "6.6", quote="growth of 6.6 per cent")

    result = reconcile(
        a, b, a_page=page, b_page=page,
        investigator=proposing(
            axis="scenario", a_value="downside", b_value=None,
            a_evidence="Real GDP growth is projected at 6.5 per cent",
        ),
    )
    assert not result.changed
    assert result.verdict.verdict == CONTRADICTS


def test_a_page_that_draws_no_distinction_leaves_the_contradiction_standing():
    """The RBI projects 6.5% and the IMF projects 6.6% for the same year. Two
    institutions disagree; no axis explains it and none should be found. This is
    the case the whole layer must not break."""
    a = claim("a", "6.5", quote="real GDP growth for 2025-26 is projected at 6.5 per cent")
    b = claim("b", "6.6", quote="real GDP growth of 6.6 percent in FY2025/26")

    result = reconcile(a, b, a_page=a.evidence_quote, b_page=b.evidence_quote,
                       investigator=never)
    assert not result.changed
    assert result.verdict.verdict == CONTRADICTS
    assert result.verdict.axis is None


def test_a_low_confidence_proposal_is_not_acted_on():
    page = "Growth of 6.5 per cent on a baseline basis and 6.6 per cent otherwise."
    result = reconcile(
        claim("a", "6.5", quote=page), claim("b", "6.6", quote=page),
        a_page=page, b_page=page,
        investigator=proposing(axis="basis", a_value="baseline", b_value=None,
                               a_evidence="on a baseline basis", confidence=0.2),
    )
    assert not result.changed


def test_a_failed_investigation_leaves_the_finding_rather_than_dropping_it():
    """An API error must not silently resolve a contradiction."""
    def explodes(*_a, **_k):
        raise RuntimeError("rate limited")

    a = claim("a", "6.5", quote="projected at 6.5 per cent")
    b = claim("b", "6.6", quote="projected at 6.6 per cent")
    result = reconcile(a, b, a_page=a.evidence_quote, b_page=b.evidence_quote,
                       investigator=explodes)
    assert result.verdict.verdict == CONTRADICTS
    assert not result.changed


# --- what it does do --------------------------------------------------------


def test_a_grounded_axis_moves_the_pair_out_of_contradiction():
    a = claim("a", "6.4", quote="July WEO | 6.4 | 6.4")
    b = claim("b", "6.6", quote="Current | 6.6 | 6.2")

    result = reconcile(
        a, b, a_page=SCENARIO_PAGE, b_page=SCENARIO_PAGE,
        investigator=proposing(
            axis="estimate_vintage", a_value="July WEO", b_value="Current",
            a_evidence="July WEO | 6.4 | 6.4", b_evidence="Current | 6.6 | 6.2",
        ),
    )
    assert result.changed
    assert result.verdict.verdict == CONTEXTUAL
    assert result.verdict.axis == "estimate_vintage"
    assert "July WEO" in result.verdict.explanation
    assert any("second look" in n for n in result.verdict.notes)


def test_an_axis_stated_for_only_one_side_blocks_rather_than_explains():
    """The subtle one, and the reason ``one_sided`` exists.

    If the page labels one figure "first advance estimate" and says nothing
    about the other, the pair is not explained — it is *undetermined* on an axis
    nobody knew to look for until now. Calling that CONTEXTUAL would count it in
    the reduction as a disagreement dissolved by context, which it is not.
    Absent is not equal, applied to a discovered axis.
    """
    page = ("Real GDP growth is estimated at 6.4 per cent as a first advance "
            "estimate. The RBI reported growth of 6.5 per cent.")
    a = claim("a", "6.4", quote=page)
    b = claim("b", "6.5", quote=page)

    result = reconcile(
        a, b, a_page=page, b_page=page,
        investigator=proposing(
            axis="estimate_vintage", a_value="first advance estimate", b_value=None,
            a_evidence="estimated at 6.4 per cent as a first advance estimate",
        ),
    )
    assert result.changed
    assert result.verdict.verdict == INSUFFICIENT_EVIDENCE
    assert result.verdict.axis == "estimate_vintage"


def test_reconciliation_can_never_manufacture_an_agreement():
    """A recovered axis can only make two claims *less* comparable. If this
    ever produced CORROBORATES the layer would have invented context rather
    than found it, and the assertion fires rather than the verdict shipping."""
    from fkl.reconcile import reconsider

    a = claim("a", "6.5", quote="q")
    b = claim("b", "6.6", quote="q")
    verdict = reconsider(a, b, Recovery(
        axis="scenario", a_value="x", b_value="y", method="investigated",
        confidence=1.0, reason="r"))
    assert verdict.verdict != "CORROBORATES"


def test_a_pair_that_was_never_a_contradiction_is_passed_through_untouched():
    """The layer runs only on the residual set. Anything else it sees, it must
    return exactly as the gate decided it."""
    a = claim("a", "6.5", quote="q", qualifiers={"consolidation": "standalone"})
    b = claim("b", "6.6", quote="q", qualifiers={"consolidation": "consolidated"})

    called = []

    def watchful(*args, **kwargs):
        called.append(1)
        return None

    result = reconcile(a, b, investigator=watchful)
    assert result.verdict.verdict == CONTEXTUAL
    assert result.verdict.axis == "consolidation"
    assert not called, "the investigator ran on a pair that was not a contradiction"


def test_a_label_that_is_only_the_figure_restated_is_not_an_explanation():
    """Found in the corpus, not imagined.

    Asked what distinguished "Core inflation increased to 4.6 percent (from 3.5
    percent FY2024/25 average)", the investigator proposed `time_reference`
    with a_value "4.6 percent" and b_value "3.5 percent FY2024/25 average". The
    second is a real label. The first is the number wearing a label's clothes,
    and it satisfies "the value must appear on the page" perfectly — of course
    it does, it *is* the value.

    Left alone this explains the disagreement by restating it: these differ
    because one of them is 4.6. Dropping the circular side leaves one real
    label and one absence, which is an undetermined axis and blocks.
    """
    page = ("Core inflation increased to 4.6 percent (from 3.5 percent "
            "FY2024/25 average), in part due to rising gold and silver prices.")
    a = claim("a", "4.6", metric="core inflation", period="FY25", quote=page)
    b = claim("b", "3.5", metric="core inflation", period="FY25", quote=page)

    result = reconcile(
        a, b, a_page=page, b_page=page,
        investigator=proposing(
            axis="time_reference", a_value="4.6 percent",
            b_value="3.5 percent FY2024/25 average",
            a_evidence="Core inflation increased to 4.6 percent",
            b_evidence="from 3.5 percent FY2024/25 average",
        ),
    )
    assert result.verdict.verdict == INSUFFICIENT_EVIDENCE
    assert result.verdict.axis == "time_reference"
    assert result.recovery.a_value is None
    assert result.recovery.b_value == "3.5 percent FY2024/25 average"


def test_a_proposal_that_is_circular_on_both_sides_is_discarded_entirely():
    page = "Growth was 6.5 per cent and 6.6 per cent."
    result = reconcile(
        claim("a", "6.5", quote=page), claim("b", "6.6", quote=page),
        a_page=page, b_page=page,
        investigator=proposing(axis="rate", a_value="6.5 per cent",
                               b_value="6.6 per cent",
                               a_evidence="Growth was 6.5 per cent",
                               b_evidence="and 6.6 per cent"),
    )
    assert not result.changed
    assert result.verdict.verdict == CONTRADICTS


def test_a_real_label_containing_a_number_still_counts():
    """`price_base=2011-12` and `estimate_vintage=Q2 FY24 estimate` are labels
    with digits in them. The guard strips the claim's *own* figure, not every
    number — otherwise it would reject half the axes that matter."""
    page = "At 2011-12 Prices the figure is 6.5; at 2004-05 Prices it is 6.6."
    result = reconcile(
        claim("a", "6.5", quote=page), claim("b", "6.6", quote=page),
        a_page=page, b_page=page,
        investigator=proposing(axis="price_base", a_value="2011-12 Prices",
                               b_value="2004-05 Prices",
                               a_evidence="At 2011-12 Prices the figure is 6.5",
                               b_evidence="at 2004-05 Prices it is 6.6"),
    )
    assert result.changed
    assert result.verdict.axis == "price_base"
