"""The gold set is checked by the suite, not just by hand.

A gold set is the one file in a project like this whose errors are invisible.
Everything else is measured against it, so a wrong page number or a mistyped
figure does not fail — it silently redefines what "correct" means. Two entries
in the first draft of `claims.yaml` were wrong, and `verify()` is what caught
them, so `verify()` runs in CI rather than only when someone remembers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.gold import load, verify  # noqa: E402


@pytest.fixture(scope="module")
def gold():
    return load()


def test_every_labelled_quote_is_on_the_page_it_cites(gold):
    """Re-reads all six PDFs. Slow, and worth it."""
    result = verify(gold)
    assert result.checked >= 25
    assert result.ok, "gold set does not match the source documents:\n" + "\n".join(
        result.failures
    )


def test_every_relation_names_claims_that_exist(gold):
    """Enforced by `load()`; asserted here so the guarantee is visible."""
    ids = set(gold.by_id)
    for relation in gold.relations:
        assert relation["a"] in ids
        assert relation["b"] in ids


def test_the_gold_set_contains_negatives(gold):
    """A set of only true matches measures nothing.

    A system that links every pair would score perfectly on it, so the pairs
    that must *not* be linked have to be labelled too — two business segments,
    two periods, two lines of one statement.
    """
    negative = {"INCOMPARABLE", "CONTEXTUAL_TEMPORAL", "INSUFFICIENT_EVIDENCE"}
    negatives = [r for r in gold.relations if r["verdict"] in negative]
    assert len(negatives) >= 3


def test_every_required_case_from_the_brief_is_covered(gold):
    """The four cases the assignment asks to be demonstrated, plus the
    reconciliations that make them interesting."""
    verdicts = {r["verdict"] for r in gold.relations}
    axes = {r.get("axis") for r in gold.relations}

    assert "CORROBORATES" in verdicts       # case 1
    assert "CONTRADICTS" in verdicts        # case 2
    assert "CONTEXTUAL" in verdicts         # case 3
    assert "CLOSES_INTERVAL" in verdicts    # case 3c, the bitemporal one
    assert "SUCCESSION" in verdicts

    # Each reconciliation needs a *different* axis. That is the argument for a
    # comparability gate over a similarity threshold: one cutoff cannot express
    # five different reasons two numbers legitimately differ.
    assert {"consolidation", "estimate_vintage", "period", "valid_time"} <= axes


def test_a_contextual_verdict_always_names_its_axis(gold):
    """A CONTEXTUAL verdict without a named axis is a shrug, not an explanation.

    `load()` refuses one; this states the rule where it can be read.
    """
    for relation in gold.relations:
        if relation["verdict"].startswith("CONTEXTUAL"):
            assert relation.get("axis"), relation["id"]


def test_the_corroboration_case_spans_documents_and_scales(gold):
    """The pair the whole unit layer exists for."""
    by_id = gold.by_id
    a = by_id["deck-p5-revenue-fy24"]
    b = by_id["ar-p35-revenue-consolidated"]
    assert a["doc"] != b["doc"]
    assert a["unit"]["scale"] != b["unit"]["scale"]
    assert abs(a["canonical"] - b["canonical"]) / b["canonical"] < 0.001


def test_the_rounding_case_defeats_a_fixed_relative_tolerance(gold):
    """16.54 Mn against 2 Cr is the same fact and 21% apart.

    Recorded as gold so the tolerance model cannot be written as a percentage
    without this failing.
    """
    by_id = gold.by_id
    a = by_id["ar-p35-traded-goods-fy23"]["canonical"]
    b = by_id["deck-p16-traded-goods-fy23"]["canonical"]
    assert abs(a - b) / max(a, b) > 0.15

    relation = next(
        r for r in gold.relations if r["id"] == "rel-traded-goods-rounding"
    )
    assert relation["verdict"] == "CORROBORATES"
