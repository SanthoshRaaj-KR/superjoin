"""ContextRank, and the boundary it must not cross.

Two kinds of test here. The first kind checks that the ranking finds the thing
it should: a word that sits near one claim and not the other, an axis that is
live in a neighbourhood, a section that connects two claims neither of which
mentions the other.

The second kind is more important and there is more of it. This module is the
one place in the system where a graph statistic touches the reasoning, and the
failure it must never have is the obvious one — ranking a claim highly because
many documents repeat it, and calling that truth. So there are tests that a
popular node does not win, that the ranking cannot change a verdict, and that
an axis both claims agree on scores below one that divides them.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.contextrank import (  # noqa: E402
    Graph,
    build,
    local_terms,
    merge_terms,
    personalized,
    rank_pair,
)
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import Claim, Document, Metric, Page  # noqa: E402


def claim(cid, **kw):
    base = dict(
        id=cid, document_id=1, page_no=0, claim_type="measurement",
        subject="Delhivery Limited", predicate="revenue", metric_id=1,
        evidence_quote="", qualifiers={}, unknown_qualifiers=[],
        confidence_reasons=[], modality="actual",
        period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
        period_label="FY2024",
    )
    base.update(kw)
    return Claim(**base)


@pytest.fixture
def session(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'cr.sqlite').as_posix()}")
    with session_scope() as s:
        s.add(Document(id=1, sha256="a" * 64, filename="ar.pdf",
                       source_path="a.pdf", n_pages=3, n_chars=10))
        s.add(Metric(id=1, canonical_name="revenue", dimension="currency"))
        s.add(Page(document_id=1, page_no=0, sha256="p" * 64, text="t",
                   rendered_text="t", n_chars=1,
                   context_json=[{"heading_path": ["Financial Statements"]}]))
        s.flush()
    with session_scope() as s:
        yield s
    reset_engine()


# --- the boundary ------------------------------------------------------------


def test_a_node_adjacent_to_everything_does_not_win(session):
    """The failure this module exists to avoid, and the one it had.

    `consolidation` is recorded or declared undetermined on more than half the
    real corpus. On raw structure it is adjacent to everything and it won every
    ranking — including for a pair the pages distinguish by sign convention.
    Inverse frequency is the statement that a node connected to most of the
    corpus is not *about* any particular pair.
    """
    for i in range(1, 21):
        # Every claim carries `basis`; only two carry `rare_axis`.
        quals = {"basis": "audited"}
        if i in (1, 2):
            quals["rare_axis"] = f"value-{i}"
        session.add(claim(i, qualifiers=quals, value_num=float(i)))
    session.flush()

    ranking = rank_pair(build(session), 1, 2)
    assert ranking.candidates, "expected at least one candidate"
    assert ranking.candidates[0].axis == "rare_axis"
    ubiquitous = next((c for c in ranking.candidates if c.axis == "basis"), None)
    if ubiquitous is not None:
        assert ubiquitous.score < ranking.candidates[0].score


def test_an_axis_both_claims_agree_on_scores_below_one_that_divides_them(session):
    """Both claims being consolidated is why they are comparable, not why they
    differ. An axis with one shared value across the pair explains nothing,
    however central it is."""
    session.add(claim(1, qualifiers={"shared": "same", "split": "left"}))
    session.add(claim(2, qualifiers={"shared": "same", "split": "right"}))
    for i in range(3, 12):
        session.add(claim(i, qualifiers={"shared": "same"}))
    session.flush()

    ranking = rank_pair(build(session), 1, 2)
    scores = {c.axis: c.score for c in ranking.candidates}
    assert scores["split"] > scores.get("shared", 0.0)


def test_the_ranking_carries_no_verdict_and_no_value(session):
    """Structurally, not by convention: there is nothing on a Ranking that
    could be mistaken for an answer. It names axes to ask about."""
    session.add(claim(1, qualifiers={"a": "x"}, value_num=1.0))
    session.add(claim(2, qualifiers={"a": "y"}, value_num=2.0))
    session.flush()

    ranking = rank_pair(build(session), 1, 2)
    fields = set(vars(ranking))
    assert not fields & {"verdict", "value", "truth", "correct", "winner"}
    for candidate in ranking.candidates:
        assert not set(vars(candidate)) & {"verdict", "value_canonical", "truth"}


# --- what it should find -----------------------------------------------------


def test_a_word_near_one_claim_and_not_the_other_outranks_a_shared_one(session):
    session.add(claim(1, evidence_quote="EBITDA margin was 6.3 percent"))
    session.add(claim(2, evidence_quote="Adjusted EBITDA margin was 5.6 percent"))
    session.flush()

    ranking = rank_pair(build(session), 1, 2)
    words = [w for w, _ in ranking.terms]
    assert "adjusted" in words
    assert "ebitda" not in words[:1]     # shared, so it cannot distinguish


def test_a_section_connects_claims_that_never_mention_each_other(session):
    """The multi-hop path that reading two pages cannot find:
    claim → section → sibling claim → qualifier → axis."""
    session.add(Page(document_id=1, page_no=1, sha256="q" * 64, text="t",
                     rendered_text="t", n_chars=1,
                     context_json=[{"heading_path": ["Financial Statements"]}]))
    session.flush()
    session.add(claim(1, page_no=0))
    session.add(claim(2, page_no=1))
    session.add(claim(3, page_no=0, qualifiers={"consolidation": "standalone"}))
    session.add(claim(4, page_no=1, qualifiers={"consolidation": "consolidated"}))
    session.flush()

    graph = build(session)
    within = graph.neighbourhood(["claim:1", "claim:2"])
    assert "sect:1:Financial Statements" in within
    assert "axis:consolidation" in within, "should reach an axis via its siblings"


def test_local_windows_find_what_the_evidence_span_never_recorded(session):
    """The IMF distinguishes two deficit figures with "per the authorities'
    definition", which sits in the sentence around the number rather than in
    the span the extractor captured. No amount of corpus structure recovers a
    word that was never stored, so the page windows get the same treatment at
    rank time."""
    terms = local_terms(
        "the general government deficit was 7.8 percent of GDP",
        "the deficit was 4.5 percent of GDP per the authorities own definition")
    words = [w for w, _ in terms]
    assert "definition" in words
    assert "authorities" in words
    assert "deficit" not in words        # shared


def test_the_shortlist_puts_corpus_structure_before_local_text(session):
    merged = merge_terms([("adjusted", 0.9), ("ebitda", 0.4)],
                         [("local", 0.8), ("adjusted", 0.1)])
    assert merged[:3] == ["adjusted", "ebitda", "local"]


def test_the_walk_stays_near_its_seed(session):
    """Personalized, not global. Mass that wanders returns to this claim rather
    than to the graph's centre of gravity — which is what stops the ranking
    becoming a popularity contest before inverse frequency even applies."""
    graph = Graph()
    graph.link("claim:1", "near", 1.0)
    for i in range(2, 30):
        graph.link(f"claim:{i}", "hub", 1.0)
    graph.link("near", "hub", 0.05)

    within = graph.neighbourhood(["claim:1"], radius=3)
    rank = personalized(graph, "claim:1", within)
    assert rank["claim:1"] > rank["near"] > rank["hub"]


def test_attention_comes_from_vocabulary_not_from_known_axes(session):
    """Getting this the wrong way round would have been the worst bug here. An
    axis nobody has recorded scores zero by construction, and a pair explained
    by an axis nobody has seen is exactly the pair most worth a model call."""
    session.add(claim(1, evidence_quote="growth in rural areas was 16 percent"))
    session.add(claim(2, evidence_quote="growth in urban areas was 26 percent"))
    session.flush()

    ranking = rank_pair(build(session), 1, 2)
    assert not ranking.candidates          # no axis has ever been recorded
    assert ranking.attention > 0.0         # and yet it is worth looking at
    words = [w for w, _ in ranking.terms]
    assert "rural" in words and "urban" in words


def test_a_pair_the_graph_does_not_hold_ranks_nothing_rather_than_crashing(session):
    session.add(claim(1))
    session.flush()
    ranking = rank_pair(build(session), 1, 999)
    assert ranking.candidates == []
    assert ranking.attention == 0.0
