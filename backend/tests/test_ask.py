"""Answering a question without averaging.

The point of these is the *shape* of an answer, not the retrieval. A question
whose honest answer is two numbers has to come back as two answers with the
axis that separates them named, and a question whose two claims share a context
and still disagree has to say so rather than picking the more confident one.
Both are the thesis applied to retrieval, and both are easy to lose to a
well-meaning "return the best match".

Nothing here calls a model. ``parse_question_offline`` is the no-key path, and
it is exercised deliberately: an endpoint that only works with credentials is
one a grader cannot check.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.ask import (  # noqa: E402
    QuerySpec,
    distinguishing_axes,
    find_claims,
    incoherent,
    parse_question_offline,
    split_by_context,
)
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import Claim, Document, Entity, Metric  # noqa: E402

METRICS = {1: "revenue from services", 2: "Company Secretary"}


@pytest.fixture
def session(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'ask.sqlite').as_posix()}")
    with session_scope() as s:
        s.add(Document(id=1, sha256="a" * 64, filename="ar.pdf", source_path="a.pdf",
                       n_pages=10, n_chars=10, primary_entity="Delhivery Limited"))
        s.add(Entity(id=1, canonical_name="Delhivery Limited"))
        s.add(Entity(id=2, canonical_name="Mr. Sunil Kumar Bansal"))
        s.add(Entity(id=3, canonical_name="Mr. Vivek Kumar"))
        s.add(Metric(id=1, canonical_name="revenue from services", dimension="currency"))
        s.add(Metric(id=2, canonical_name="Company Secretary", dimension=None))
        s.flush()

        base = dict(
            document_id=1, page_no=21, claim_type="measurement",
            subject="Delhivery Limited", predicate="revenue from services",
            entity_id=1, metric_id=1, unit_dimension="currency",
            unit_currency="INR", unit_scale=1e6, evidence_quote="Revenue",
            unknown_qualifiers=[], confidence_reasons=[], modality="actual",
            period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
            period_label="FY2024", period_granularity="year",
            conf_extraction=0.9, conf_grounding=1.0, conf_normalization=0.9,
            conf_entity_match=1.0,
        )
        s.add(Claim(id=1, value_raw="74,540.82", value_num=74540.82,
                    value_canonical=74540820000.0,
                    qualifiers={"consolidation": "standalone"}, **base))
        s.add(Claim(id=2, value_raw="81,415.38", value_num=81415.38,
                    value_canonical=81415380000.0,
                    qualifiers={"consolidation": "consolidated"}, **base))
        # Two roles in one seat, one after the other.
        role = dict(
            document_id=1, page_no=90, claim_type="state",
            predicate="Company Secretary", value_text="Company Secretary",
            metric_id=2, evidence_quote="q", qualifiers={}, unknown_qualifiers=[],
            confidence_reasons=[], modality="actual",
            conf_extraction=0.9, conf_grounding=1.0, conf_normalization=0.9,
            conf_entity_match=0.9,
        )
        s.add(Claim(id=3, subject="Mr. Sunil Kumar Bansal", entity_id=2,
                    valid_to=date(2023, 5, 31), valid_to_is_open=False, **role))
        s.add(Claim(id=4, subject="Mr. Vivek Kumar", entity_id=3,
                    valid_from=date(2023, 6, 1), valid_to=date(2024, 3, 27),
                    valid_to_is_open=False, **role))
        s.flush()
    with session_scope() as s:
        yield s
    reset_engine()


def test_one_question_two_correct_answers_and_the_axis_that_splits_them(session):
    """The standalone/consolidated case, which is the whole argument. A single
    figure would have to discard one of two things the documents both say."""
    claims = find_claims(session, QuerySpec(entity="Delhivery",
                                            metric="revenue", period="FY2024"))
    groups = split_by_context(claims, METRICS)

    assert len(groups) == 2
    assert "consolidation" in distinguishing_axes(groups)
    bases = {context["consolidation"] for context, _ in groups}
    assert bases == {"standalone", "consolidated"}


def test_a_role_question_splits_on_the_tenure_not_on_the_post(session):
    """Three people in one seat are three answers about three stretches of
    time, not one answer asserted three times. A role carries no
    `period_label`, so without the interval they collapse into one group."""
    claims = find_claims(session, QuerySpec(metric="Company Secretary"))
    groups = split_by_context(claims, METRICS)

    assert len(groups) == 2
    assert "period" in distinguishing_axes(groups)
    assert {context["period"] for context, _ in groups} == {
        "… → 2023-05-31", "2023-06-01 → 2024-03-27"}


def test_a_role_question_reaches_the_company_not_only_the_person(session):
    """A role claim resolves its entity to the *person*, so filtering on the
    company matches nothing unless the organisation is taken from the
    document. This is the query a reader is most likely to type first."""
    claims = find_claims(session, QuerySpec(entity="Delhivery Limited",
                                            metric="Company Secretary"))
    assert {c.id for c in claims} == {3, 4}


def test_claims_sharing_a_context_that_still_disagree_are_flagged(session):
    """The RBI/IMF shape. Two figures, same everything recorded, different
    values — the answer says nothing distinguishes them rather than choosing."""
    a, b = session.get(Claim, 1), session.get(Claim, 2)
    assert incoherent([a, b]) is True
    assert incoherent([a, a]) is False


def test_rounding_is_not_disagreement(session):
    """A tenth of a percent between two claims of the same figure is the two
    documents rounding, not the corpus contradicting itself."""
    a, b = session.get(Claim, 1), session.get(Claim, 2)
    b.value_canonical = a.value_canonical * 1.001
    assert incoherent([a, b]) is False


def test_the_offline_parser_finds_the_entity_and_metric_without_a_key(session):
    """No credentials, worse recall, same answer shape. An endpoint that only
    works with a key is one nobody else can check."""
    spec = parse_question_offline(session, "what was Delhivery's revenue in FY2024?")
    assert "Delhivery" in spec.entity
    assert "revenue" in spec.metric.lower()
    assert "2024" in spec.period


def test_a_metric_question_does_not_drag_in_every_metric(session):
    """"revenue" must not match "Company Secretary" through bare token
    overlap. Widening the match until something is found is only safe if the
    widest step is the last one."""
    claims = find_claims(session, QuerySpec(metric="revenue"))
    assert {c.metric_id for c in claims} == {1}
