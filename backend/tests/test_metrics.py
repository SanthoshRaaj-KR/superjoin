"""Metric resolution.

Offline throughout: embeddings and the adjudicator are both stubbed, with the
stub returning the *measured* similarities from this corpus rather than
convenient ones. The numbers in `SIMILARITY` were produced by embedding these
exact pairs with text-embedding-3-small, and the awkward one — Adjusted EBITDA
scoring higher against EBITDA than two genuine synonyms score against each
other — is the reason the resolution order looks the way it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl import metrics as metrics_module  # noqa: E402
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.metrics import MetricJudgement, normalize_predicate, resolve_metric  # noqa: E402

# Measured, not invented. See the module docstring.
SIMILARITY = {
    frozenset({"Revenue from contracts with customers", "Revenue from services"}): 0.687,
    frozenset({"Revenue from operations", "Revenue from services"}): 0.747,
    frozenset({"Express Parcel revenue", "Express parcel service revenue"}): 0.944,
    frozenset({"Express Parcel revenue", "PTL freight revenue"}): 0.621,
    frozenset({"Adjusted EBITDA", "EBITDA"}): 0.846,
    frozenset({"Express Parcel revenue", "Express Parcel shipments"}): 0.801,
}


@pytest.fixture
def session(tmp_path, monkeypatch):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")

    def fake_embed(_session, texts, model=None):
        # A one-hot-ish stand-in is not enough: the code ranks by cosine, so the
        # stub has to reproduce the *relative* ordering that was measured.
        return {t.strip(): t.strip() for t in texts if t.strip()}

    def fake_cosine(a, b):
        if a == b:
            return 1.0
        return SIMILARITY.get(frozenset({a, b}), 0.30)

    monkeypatch.setattr(metrics_module, "embed_texts", fake_embed)
    monkeypatch.setattr(metrics_module, "cosine", fake_cosine)
    with session_scope() as s:
        yield s
    reset_engine()


def judge(same: bool, reason: str = "stub"):
    def _judge(_a, _b, _dim):
        return MetricJudgement(same_metric=same, confidence=0.9, reason=reason)

    return _judge


# --- keys -------------------------------------------------------------------


def test_punctuation_and_case_do_not_make_a_new_metric():
    assert normalize_predicate("Revenue from services") == "revenue from services"
    assert normalize_predicate("Revenue From Services.") == "revenue from services"


# --- resolution order -------------------------------------------------------


def test_a_dimension_difference_blocks_a_match_without_a_model_call(
    session, monkeypatch
):
    """`Express Parcel revenue` and `Express Parcel shipments` score 0.801 — well
    inside the band where names look alike.

    One is rupees and one is a count, so they can never be the same metric, and
    the filter runs before similarity is even considered. If a model call is
    reached here the adjudicator stub fails the test.
    """
    monkeypatch.setattr(
        metrics_module, "_adjudicate",
        lambda *a: pytest.fail("adjudicated across dimensions"),
    )
    revenue = resolve_metric(session, "Express Parcel revenue", dimension="currency")
    shipments = resolve_metric(session, "Express Parcel shipments", dimension="count")
    assert revenue.metric_id != shipments.metric_id
    assert shipments.method == "new"


def test_a_near_identical_restatement_links_without_adjudication(session, monkeypatch):
    """0.944 is above the auto-link line, which exists only for cases like this
    — the same words rearranged."""
    monkeypatch.setattr(
        metrics_module, "_adjudicate", lambda *a: pytest.fail("should not adjudicate")
    )
    first = resolve_metric(session, "Express Parcel revenue", dimension="currency")
    second = resolve_metric(
        session, "Express parcel service revenue", dimension="currency"
    )
    assert second.metric_id == first.metric_id
    assert second.method == "embedding"


def test_genuine_synonyms_are_linked_by_the_adjudicator(session, monkeypatch):
    """0.687 is far too low to link on similarity alone, and these are the same
    metric. This is the case a threshold cannot reach."""
    monkeypatch.setattr(metrics_module, "_adjudicate", judge(True, "same line item"))
    a = resolve_metric(session, "Revenue from services", dimension="currency")
    b = resolve_metric(
        session, "Revenue from contracts with customers", dimension="currency"
    )
    assert b.metric_id == a.metric_id
    assert b.method == "adjudicated"


def test_adjusted_ebitda_does_not_absorb_ebitda(session, monkeypatch):
    """The measurement that shaped this module.

    0.846 is higher than two genuine synonyms score against each other, so any
    auto-link threshold loose enough to catch the synonyms merges these. The
    adjudicator says no and they stay apart.
    """
    monkeypatch.setattr(
        metrics_module, "_adjudicate", judge(False, "one is adjusted, one is not")
    )
    ebitda = resolve_metric(session, "EBITDA", dimension="currency")
    adjusted = resolve_metric(session, "Adjusted EBITDA", dimension="currency")
    assert adjusted.metric_id != ebitda.metric_id
    assert adjusted.method == "new"


def test_two_segments_of_one_company_stay_separate(session, monkeypatch):
    """Both are revenue in rupees, so the dimension filter cannot help.

    At 0.621 they sit just above the candidate floor, which is where the floor
    is meant to be: low enough that the lowest true match (0.683) is never
    missed, which necessarily lets some false pairs through to be refused. The
    adjudicator is what keeps two business segments apart.
    """
    monkeypatch.setattr(
        metrics_module, "_adjudicate", judge(False, "different business segments")
    )
    express = resolve_metric(session, "Express Parcel revenue", dimension="currency")
    ptl = resolve_metric(session, "PTL freight revenue", dimension="currency")
    assert express.metric_id != ptl.metric_id
    assert ptl.method == "new"


def test_a_resolved_alias_costs_nothing_the_second_time(session, monkeypatch):
    """One adjudication per distinct predicate string, not per occurrence.

    This is what bounds the cost by vocabulary size instead of page count.
    """
    calls = []

    def counting(a, b, dim):
        calls.append((a, b))
        return MetricJudgement(same_metric=True, confidence=0.9, reason="s")

    monkeypatch.setattr(metrics_module, "_adjudicate", counting)
    resolve_metric(session, "Revenue from services", dimension="currency")
    resolve_metric(session, "Revenue from operations", dimension="currency")
    assert len(calls) == 1

    again = resolve_metric(session, "Revenue from operations", dimension="currency")
    assert len(calls) == 1  # served from the alias table
    assert again.method == "alias"


def test_without_a_model_the_fallback_is_a_new_metric_not_a_guess(session):
    """`adjudicate=False` must not degrade into "link anyway above some score".

    A similarity number is not evidence of identity — that is the whole finding
    — so the offline path refuses to merge rather than merging on a threshold.
    """
    resolve_metric(session, "Revenue from services", dimension="currency")
    other = resolve_metric(
        session, "Revenue from contracts with customers", dimension="currency",
        adjudicate=False,
    )
    assert other.method == "new"


def test_an_empty_predicate_is_refused(session):
    with pytest.raises(ValueError):
        resolve_metric(session, "  ")
