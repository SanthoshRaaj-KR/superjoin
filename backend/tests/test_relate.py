"""Running the gate across a corpus, and the number that comes out of it.

The headline this project argues with is a reduction:

    N pairs whose raw values disagree
    M explained by a named context axis
    K genuinely unresolved

Which makes the *denominator* the thing most worth testing. It has to be the
population a context-blind system would have flagged, not the population this
one failed to explain — and getting that wrong is silent, because the pipeline
still runs and still prints a number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import Claim, Document, Entity, Metric, Relation  # noqa: E402
from fkl.relate import relate_corpus  # noqa: E402


@pytest.fixture
def session(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    with session_scope() as s:
        s.add(Document(sha256="a" * 64, filename="a.pdf", source_path="a.pdf",
                       n_pages=1))
        s.add(Document(sha256="b" * 64, filename="b.pdf", source_path="b.pdf",
                       n_pages=1))
        s.add(Entity(id=1, canonical_name="Delhivery Limited"))
        s.add(Metric(id=1, canonical_name="revenue", dimension="currency"))
        s.flush()
        yield s
    reset_engine()


def add(session, **kwargs) -> Claim:
    base = dict(
        document_id=1, page_no=0, claim_type="measurement",
        subject="Delhivery Limited", predicate="revenue",
        entity_id=1, metric_id=1,
        unit_dimension="currency", unit_currency="INR", unit_scale=1e6,
        evidence_quote="q", qualifiers={}, unknown_qualifiers=[],
        confidence_reasons=[], modality="actual",
    )
    base.update(kwargs)
    claim = Claim(**base)
    session.add(claim)
    session.flush()
    return claim


def fy(label):
    from fkl.periods import parse_period

    period = parse_period(label)
    return dict(period_start=period.start, period_end=period.end,
                period_label=period.label, period_granularity=period.granularity)


def test_the_denominator_is_what_a_context_blind_system_would_flag(session):
    """The bug this test exists for.

    The gate short-circuits: two claims in different periods come back
    CONTEXTUAL_TEMPORAL without their values ever being compared. Counting only
    the pairs where the gate itself compared values makes the reduction read 0%
    however much context resolved — which is exactly what the first run of this
    reported, with 158 explained pairs sitting in the numerator's blind spot.
    """
    add(session, value_raw="72,253.01", value_num=72253.01,
        value_canonical=72253010000, **fy("FY23"))
    add(session, document_id=2, value_raw="81,415.38", value_num=81415.38,
        value_canonical=81415380000, **fy("FY24"))

    run = relate_corpus(session)
    assert run.pairs == 1
    # The values genuinely differ, so this pair belongs in the denominator even
    # though the gate never needed to compare them.
    assert run.raw_disagreements == 1
    assert run.explained == 1
    assert run.unresolved == 0
    assert run.reduction == 1.0
    assert run.explaining_axes == {"period": 1}


def test_a_named_axis_dissolves_a_disagreement(session):
    add(session, value_raw="74,540.82", value_num=74540.82,
        value_canonical=74540820000, qualifiers={"consolidation": "standalone"},
        **fy("FY24"))
    add(session, value_raw="81,415.38", value_num=81415.38,
        value_canonical=81415380000, qualifiers={"consolidation": "consolidated"},
        **fy("FY24"))

    run = relate_corpus(session)
    assert run.raw_disagreements == 1
    assert run.explained == 1
    assert run.explaining_axes == {"consolidation": 1}


def test_an_undetermined_axis_is_blocked_not_explained(session):
    """Blocked and explained are counted apart on purpose. A comparison refused
    for missing context has not been resolved by context, and rolling the two
    together would flatter the headline."""
    add(session, value_raw="8,142", value_num=8142, unit_scale=1e7,
        value_canonical=81420000000, unknown_qualifiers=["consolidation"],
        **fy("FY24"))
    add(session, document_id=2, value_raw="74,540.82", value_num=74540.82,
        value_canonical=74540820000, qualifiers={"consolidation": "consolidated"},
        **fy("FY24"))

    run = relate_corpus(session)
    assert run.raw_disagreements == 1
    assert run.blocked == 1
    assert run.explained == 0
    assert run.unresolved == 0


def test_a_genuine_disagreement_survives_and_is_counted(session):
    add(session, value_raw="6.5", value_num=6.5, unit_dimension="percent",
        unit_scale=1.0, unit_currency=None, value_canonical=6.5,
        modality="projection", **fy("FY26"))
    add(session, document_id=2, value_raw="6.6", value_num=6.6,
        unit_dimension="percent", unit_scale=1.0, unit_currency=None,
        value_canonical=6.6, modality="projection", **fy("FY26"))

    run = relate_corpus(session)
    assert run.unresolved == 1
    assert run.verdicts["CONTRADICTS"] == 1


def test_blocking_keeps_unrelated_claims_out_of_the_comparison(session):
    """Two claims about different metrics are never compared at all.

    This is both the cost control and the scaling story: comparing every pair of
    10,000 claims is 50 million comparisons, and none of the ones blocking
    removes could have been comparable.
    """
    session.add(Metric(id=2, canonical_name="other income", dimension="currency"))
    session.flush()
    add(session, value_raw="1", value_num=1, value_canonical=1e6, **fy("FY24"))
    add(session, metric_id=2, value_raw="2", value_num=2, value_canonical=2e6,
        **fy("FY24"))

    run = relate_corpus(session)
    assert run.pairs == 0
    assert run.blocks == 0


def test_claims_that_never_resolved_are_not_compared_on_raw_strings(session):
    """A claim with no entity or metric failed the registries. Comparing it by
    its raw predicate would invent exactly the matches they refused to make."""
    add(session, entity_id=None, metric_id=None, value_raw="1", value_num=1,
        value_canonical=1e6, **fy("FY24"))
    add(session, entity_id=None, metric_id=None, value_raw="2", value_num=2,
        value_canonical=2e6, **fy("FY24"))

    assert relate_corpus(session).pairs == 0


def test_verdicts_are_stored_with_the_axis_and_the_explanation(session):
    add(session, value_raw="74,540.82", value_num=74540.82,
        value_canonical=74540820000, qualifiers={"consolidation": "standalone"},
        **fy("FY24"))
    add(session, document_id=2, value_raw="81,415.38", value_num=81415.38,
        value_canonical=81415380000, qualifiers={"consolidation": "consolidated"},
        **fy("FY24"))
    relate_corpus(session)

    stored = session.query(Relation).one()
    assert stored.verdict == "CONTEXTUAL"
    assert stored.axis == "consolidation"
    assert "consolidation" in stored.explanation
    assert stored.cross_document is True
    assert stored.values_differ is True
