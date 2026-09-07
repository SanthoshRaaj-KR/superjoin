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


# --- the interval engine over stored claims ---------------------------------


def state(session, subject, role, **kwargs) -> Claim:
    base = dict(
        document_id=1, page_no=90, claim_type="state", subject=subject,
        predicate=role, value_text=role, evidence_quote="q",
        qualifiers={}, unknown_qualifiers=[], confidence_reasons=[],
        modality="actual",
    )
    base.update(kwargs)
    claim = Claim(**base)
    session.add(claim)
    session.flush()
    return claim


def test_role_claims_block_by_the_organisations_seat(session):
    """`org_scope` is null in the common case, because the schema reserves it
    for a *different* organisation. So the document's primary entity is the
    fallback — without it every role blocks alone and no succession is found.
    """
    from datetime import date

    from fkl.relate import relate_states

    session.query(Document).filter_by(id=1).one().primary_entity = "Delhivery Limited"
    state(session, "Mr. Sunil Kumar Bansal", "Company Secretary",
          valid_to=date(2023, 5, 31))
    state(session, "Mr. Vivek Kumar", "Company Secretary",
          valid_from=date(2023, 6, 1), valid_to=date(2024, 3, 27))
    state(session, "Mrs. Madhulika Rawat", "Company Secretary",
          valid_from=date(2024, 5, 17), valid_to_is_open=True)

    run = relate_states(session)
    assert run.blocks == 1  # one seat, three holders
    assert run.verdicts.get("SUCCESSION") == 1
    assert run.verdicts.get("SUCCESSION_WITH_VACANCY") == 1

    vacancy = session.query(Relation).filter_by(
        verdict="SUCCESSION_WITH_VACANCY").one()
    assert vacancy.value_difference == 51


def test_many_holders_of_a_plural_role_produce_no_relations(session):
    """Otherwise every pair of directors who ever served becomes a finding and
    buries the two that matter."""
    from datetime import date

    from fkl.relate import relate_states

    session.query(Document).filter_by(id=1).one().primary_entity = "Delhivery Limited"
    state(session, "A", "Non Executive - Independent Director",
          valid_to=date(2023, 2, 11))
    state(session, "B", "Non Executive - Independent Director",
          valid_from=date(2023, 8, 4), valid_to_is_open=True)

    run = relate_states(session)
    assert run.verdicts.get("SUCCESSION_WITH_VACANCY") is None
    assert session.query(Relation).count() == 0


def test_as_of_answers_different_dates_differently(session):
    """Current state is a query, never a stored field — which is what makes the
    vacancy visible as an empty seat rather than as nothing at all."""
    from datetime import date

    from fkl.relate import as_of

    session.query(Document).filter_by(id=1).one().primary_entity = "Delhivery Limited"
    state(session, "Mr. Vivek Kumar", "Company Secretary",
          valid_from=date(2023, 6, 1), valid_to=date(2024, 3, 27))
    state(session, "Mrs. Madhulika Rawat", "Company Secretary",
          valid_from=date(2024, 5, 17), valid_to_is_open=True)

    assert [h.filler for h in as_of(session, date(2023, 8, 1))] == ["Mr. Vivek Kumar"]
    assert [h.filler for h in as_of(session, date(2024, 6, 1))] == ["Mrs. Madhulika Rawat"]
    assert as_of(session, date(2024, 4, 15)) == []


def test_one_run_is_one_generation_and_lists_only_its_own_survivors(session):
    """The store is append-only, so an unfiltered query over relations returns
    one row per pair *per run*. Listing survivors without the generation filter
    printed every past run's findings as if they were new — and because the
    duplicates sorted adjacently, five doubled pairs filled a top-ten list and
    buried a real one underneath.
    """
    from fkl.relate import conflicts, relate_states

    add(session, value_raw="6.5", value_num=6.5, unit_dimension="percent",
        unit_scale=1.0, unit_currency=None, value_canonical=6.5, **fy("FY26"))
    add(session, document_id=2, value_raw="6.6", value_num=6.6,
        unit_dimension="percent", unit_scale=1.0, unit_currency=None,
        value_canonical=6.6, **fy("FY26"))

    first = relate_corpus(session)
    second = relate_corpus(session)
    assert second.generation == first.generation + 1

    assert len(conflicts(session)) == 2            # both runs, unfiltered
    assert len(conflicts(session, generation=second.generation)) == 1

    # The interval engine shares the generation it is given, so one invocation
    # of the CLI writes one generation across both engines.
    states = relate_states(session, generation=second.generation)
    assert states.generation == second.generation


def test_both_engines_agree_on_what_one_role_is(session):
    """The gate blocks on the resolved metric; the interval engine blocked on
    the raw predicate string. A director redesignated from "Non Executive -
    Nominee Director" to "Non-Executive Director" was therefore one seat to the
    gate and two to the interval engine — so the gate compared the two titles as
    text and reported a contradiction, and the engine that would have read the
    dates never saw a seat with two holdings in it."""
    from datetime import date

    from fkl.relate import to_holding

    session.add(Metric(id=9, canonical_name="non-executive director",
                       dimension=None))
    session.flush()
    a = state(session, "Mr. Donald Francis Colleran", "Non Executive - Nominee Director",
              metric_id=9, valid_to=date(2022, 5, 23))
    b = state(session, "Mr. Donald Francis Colleran", "Non-Executive Director",
              metric_id=9, valid_from=date(2022, 5, 24), valid_to=date(2023, 9, 27))

    ha = to_holding(a, "Delhivery Limited", "non-executive director")
    hb = to_holding(b, "Delhivery Limited", "non-executive director")
    assert ha.predicate == hb.predicate

    from fkl.temporal import group_slots

    assert len(group_slots([ha, hb])) == 1


# --- the second look, over stored claims ------------------------------------


def test_a_contradiction_withdrawn_on_review_counts_as_explained(session):
    """The reduction has to move when a contradiction is withdrawn, or the
    second look is decoration. The pair still had differing raw values — it
    belongs in the denominator — but it is no longer unresolved."""
    from fkl.models import Page
    from fkl.reconcile import Recovery

    page = ("Table 4. Growth under alternative tariff assumptions\n"
            "July WEO | 6.4 | 6.4\nCurrent | 6.6 | 6.2\n")
    for doc in (1, 2):
        session.add(Page(document_id=doc, page_no=0, sha256="x" * 64,
                         text=page, rendered_text=page, n_chars=len(page)))
    session.add(Metric(id=3, canonical_name="real GDP growth", dimension="percent"))
    session.flush()

    common = dict(metric_id=3, unit_dimension="percent", unit_scale=1.0,
                  unit_currency=None, modality="projection")
    add(session, value_raw="6.4", value_num=6.4, value_canonical=6.4,
        evidence_quote="July WEO | 6.4 | 6.4", **common, **fy("FY26"))
    add(session, document_id=2, value_raw="6.6", value_num=6.6, value_canonical=6.6,
        evidence_quote="Current | 6.6 | 6.2", **common, **fy("FY26"))

    def investigator(a, b, leads):
        return Recovery(
            axis="estimate_vintage", a_value="July WEO", b_value="Current",
            a_evidence="July WEO | 6.4 | 6.4", b_evidence="Current | 6.6 | 6.2",
            method="investigated", confidence=0.9,
            reason="the table's rows are two forecast vintages",
        )

    run = relate_corpus(session, investigator=investigator)
    assert run.investigated == 1
    assert run.withdrawn == 1
    assert run.raw_disagreements == 1
    assert run.unresolved == 0
    assert run.explained == 1
    assert run.recovered_axes == {"estimate_vintage": 1}

    stored = session.query(Relation).one()
    assert stored.verdict == "CONTEXTUAL"
    assert stored.reconsidered is True
    assert stored.original_verdict == "CONTRADICTS"
    assert stored.recovery_axis == "estimate_vintage"
    assert "vintages" in stored.recovery_reason


def test_a_contradiction_that_survives_review_is_recorded_as_such(session):
    """And the run says it was checked. "Unresolved after investigation" is a
    stronger statement than "unresolved", and only one of them is true here."""
    from fkl.models import Page

    page = "Real GDP growth is projected at 6.5 per cent for 2025-26."
    for doc in (1, 2):
        session.add(Page(document_id=doc, page_no=0, sha256="y" * 64,
                         text=page, rendered_text=page, n_chars=len(page)))
    session.add(Metric(id=3, canonical_name="real GDP growth", dimension="percent"))
    session.flush()

    common = dict(metric_id=3, unit_dimension="percent", unit_scale=1.0,
                  unit_currency=None, modality="projection", evidence_quote=page)
    add(session, value_raw="6.5", value_num=6.5, value_canonical=6.5,
        **common, **fy("FY26"))
    add(session, document_id=2, value_raw="6.6", value_num=6.6, value_canonical=6.6,
        **common, **fy("FY26"))

    run = relate_corpus(session, investigator=lambda *a, **k: None)
    assert run.investigated == 1
    assert run.withdrawn == 0
    assert run.unresolved == 1
    stored = session.query(Relation).one()
    assert stored.verdict == "CONTRADICTS"
    assert stored.reconsidered is False


def test_the_review_is_off_unless_asked_for(session):
    """The gate has to stay runnable, and testable, with no credentials. Only
    the deterministic sign scout runs by default."""
    add(session, value_raw="6.5", value_num=6.5, unit_dimension="percent",
        unit_scale=1.0, unit_currency=None, value_canonical=6.5, **fy("FY26"))
    add(session, document_id=2, value_raw="6.6", value_num=6.6,
        unit_dimension="percent", unit_scale=1.0, unit_currency=None,
        value_canonical=6.6, **fy("FY26"))

    run = relate_corpus(session)          # no investigator
    assert run.unresolved == 1
    assert run.withdrawn == 0
