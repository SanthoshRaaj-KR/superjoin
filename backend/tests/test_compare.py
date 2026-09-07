"""The comparability gate.

Two kinds of test. The first half scores the gate against the hand-labelled
gold set — the target it was built to hit, written before it existed. The second
half pins the individual rules, so a regression says *which* rule broke rather
than only that the score moved.

No database, no API key, no extraction run: the gate is a pure function over a
small dataclass, and that is most of why it can be tested this way at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.compare import (  # noqa: E402
    CONTEXTUAL,
    CONTEXTUAL_TEMPORAL,
    CONTRADICTS,
    CORROBORATES,
    INCOMPARABLE,
    INSUFFICIENT_EVIDENCE,
    Comparable,
    compare,
)
from fkl.gold import load, score, to_comparable  # noqa: E402
from fkl.periods import parse_period  # noqa: E402
from fkl.units import parse_unit  # noqa: E402
from fkl.values import written_precision  # noqa: E402


# --- scored against the gold set --------------------------------------------


def test_the_gate_matches_every_label_it_is_responsible_for():
    """The headline number, and the reason the gold set was written first.

    Covers both engines. The comparability gate answers the measurement
    relations; the interval engine answers the ones resolved by valid_time.
    """
    result = score()
    failures = [
        f"{r.id}: expected {r.expected}[{r.expected_axis}], "
        f"got {r.actual}[{r.actual_axis}] — {r.explanation}"
        for r in result.scored
        if not r.ok
    ]
    assert not failures, "\n".join(failures)
    assert len(result.scored) >= 12


def test_the_interval_relations_are_no_longer_deferred():
    """They were, until the interval engine existed. All sixteen labelled
    relations are now answered by one engine or the other, routed by what the
    claims carry rather than by what the label expects — routing on the answer
    would make the score meaningless."""
    result = score()
    assert result.deferred == []
    assert len(result.scored) == 16
    assert {r.actual for r in result.scored} >= {
        "CLOSES_INTERVAL", "SUCCESSION", "SUCCESSION_WITH_VACANCY"
    }


def test_every_verdict_that_names_an_axis_actually_names_one():
    """A CONTEXTUAL verdict without an axis explains nothing."""
    from fkl.gold import is_temporal_claim

    gold = load()
    by_id = gold.by_id
    for relation in gold.relations:
        claim_a, claim_b = by_id[relation["a"]], by_id[relation["b"]]
        if is_temporal_claim(claim_a) and is_temporal_claim(claim_b):
            continue  # the interval engine's, not the gate's
        verdict = compare(to_comparable(claim_a), to_comparable(claim_b))
        if verdict.verdict in {CONTEXTUAL, CONTEXTUAL_TEMPORAL, INCOMPARABLE,
                               INSUFFICIENT_EVIDENCE}:
            assert verdict.axis, f"{relation['id']} gave {verdict.verdict} with no axis"
        assert verdict.explanation


# --- the individual rules ---------------------------------------------------


def measurement(ref, metric, value, unit_raw, period, **kwargs) -> Comparable:
    unit = parse_unit(unit_raw)
    number = float(str(value).replace(",", ""))
    return Comparable(
        ref=ref,
        entity=kwargs.pop("entity", "delhivery limited"),
        metric=metric,
        unit=unit,
        period=parse_period(period),
        value_canonical=unit.canonical(number),
        precision=written_precision(str(value), unit.scale),
        **kwargs,
    )


def test_an_explicitly_unknown_axis_blocks_rather_than_assumes():
    """The project's thesis, in one assertion.

    The deck's ₹8,142 Cr and the annual report's ₹81,415.38 Mn are the same
    number to within rounding. The deck never says whether it is standalone or
    consolidated, and the annual report's two bases for that figure differ by
    9.2%, so the match is refused and the missing axis is named. Concluding the
    deck is consolidated *because* the numbers agree is circular.
    """
    deck = measurement("deck", "revenue", "8,142", "₹ Cr", "FY24",
                       unknown_qualifiers=["consolidation"])
    report = measurement("ar", "revenue", "81,415.38", "₹ Mn", "FY24",
                         qualifiers={"consolidation": "consolidated"})

    result = compare(deck, report)
    assert result.verdict == INSUFFICIENT_EVIDENCE
    assert result.axis == "consolidation"


def test_supplying_the_missing_axis_turns_the_refusal_into_corroboration():
    """The counterfactual. The verdict is computed, not stored, so filling the
    gap changes it — which is what makes the toggle worth showing."""
    deck = measurement("deck", "revenue", "8,142", "₹ Cr", "FY24",
                       qualifiers={"consolidation": "consolidated"})
    report = measurement("ar", "revenue", "81,415.38", "₹ Mn", "FY24",
                         qualifiers={"consolidation": "consolidated"})
    assert compare(deck, report).verdict == CORROBORATES


def test_a_differing_axis_is_contextual_and_names_the_axis():
    standalone = measurement("a", "revenue", "74,540.82", "₹ Mn", "FY24",
                             qualifiers={"consolidation": "standalone"})
    consolidated = measurement("b", "revenue", "81,415.38", "₹ Mn", "FY24",
                               qualifiers={"consolidation": "consolidated"})
    result = compare(standalone, consolidated)
    assert result.verdict == CONTEXTUAL
    assert result.axis == "consolidation"
    assert "standalone against consolidated" in result.explanation


def test_absent_is_not_equal():
    """An axis one document declares and the other never mentions is a
    difference between them, not a licence to compare."""
    with_axis = measurement("a", "gdp growth", "6.4", "%", "FY25",
                            entity="india",
                            qualifiers={"estimate_vintage": "first advance estimates"})
    without = measurement("b", "gdp growth", "6.5", "%", "FY25", entity="india")
    result = compare(with_axis, without)
    assert result.verdict == CONTEXTUAL
    assert result.axis == "estimate_vintage"


def test_different_periods_are_a_trend_not_a_disagreement():
    """The most common way a naive comparison manufactures conflicts. It would
    do it thousands of times over this corpus."""
    fy23 = measurement("a", "revenue", "72,253.01", "₹ Mn", "FY23")
    fy24 = measurement("b", "revenue", "81,415.38", "₹ Mn", "FY24")
    result = compare(fy23, fy24)
    assert result.verdict == CONTEXTUAL_TEMPORAL
    assert result.axis == "period"


def test_a_real_contradiction_survives_the_gate():
    """Everything above exists to let this one through cleanly.

    Same entity, same metric, same period, both projections, nothing
    undetermined. 6.5 against 6.6, and no axis explains it.
    """
    rbi = measurement("rbi", "real gdp growth", "6.5", "%", "FY26",
                      entity="india", modality="projection")
    imf = measurement("imf", "real gdp growth", "6.6", "%", "FY26",
                      entity="india", modality="projection")
    result = compare(rbi, imf)
    assert result.verdict == CONTRADICTS
    assert result.axis is None


def test_different_metrics_never_reach_a_value_comparison():
    express = measurement("a", "express parcel revenue", "50,765.87", "₹ Mn", "FY24")
    ptl = measurement("b", "ptl freight revenue", "15,174.05", "₹ Mn", "FY24")
    result = compare(express, ptl)
    assert result.verdict == INCOMPARABLE
    assert result.axis == "metric"
    assert result.value is None  # the values were never even compared


def test_currencies_are_incomparable_rather_than_converted():
    inr = measurement("a", "revenue", "8,142", "₹ Cr", "FY24")
    usd = measurement("b", "revenue", "1,000", "US$ Mn", "FY24")
    result = compare(inr, usd)
    assert result.verdict == INCOMPARABLE
    assert result.axis == "currency"


def test_a_state_claim_is_not_required_to_have_a_reporting_period():
    """An address has validity intervals, not a fiscal year. Demanding a period
    turned every role, address and identifier in the corpus into
    INSUFFICIENT_EVIDENCE."""
    a = Comparable(
        ref="a", entity="delhivery limited", metric="registered office",
        unit=parse_unit(None), period=parse_period(None), claim_type="state",
        value_text="IGI Airport, New Delhi 110037",
    )
    b = Comparable(
        ref="b", entity="delhivery limited", metric="registered office",
        unit=parse_unit(None), period=parse_period(None), claim_type="state",
        value_text="Indira Gandhi International Airport, New Delhi 110037",
    )
    result = compare(a, b)
    assert result.verdict == CORROBORATES
    assert "abbreviation" in result.explanation


def test_the_explanation_is_templated_and_therefore_stable():
    """A model could write a nicer sentence and a different one next time. A
    verdict whose reasoning changes between runs is not a verdict."""
    a = measurement("a", "revenue", "74,540.82", "₹ Mn", "FY24",
                    qualifiers={"consolidation": "standalone"})
    b = measurement("b", "revenue", "81,415.38", "₹ Mn", "FY24",
                    qualifiers={"consolidation": "consolidated"})
    assert compare(a, b).explanation == compare(a, b).explanation
