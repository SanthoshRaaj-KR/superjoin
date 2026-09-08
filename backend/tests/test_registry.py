"""The axis registry and the cardinality register.

Two things are worth pinning here, and both are about honesty rather than
mechanism. A discovered axis must not be *believed* on one sighting, because
one sighting may be one mis-read page — and an axis that stops naming anything
must stop reporting that it explains things, which is a subtler failure and one
the interface showed for a while before anyone noticed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import Axis, Claim, Document, Relation  # noqa: E402
from fkl.registry import (  # noqa: E402
    PROMOTION_THRESHOLD,
    record_axes,
    record_predicate,
    seed_axes,
)


def relation(a, b, *, generation=1, axis=None, recovery=None, verdict="CONTEXTUAL"):
    return Relation(
        claim_a_id=a, claim_b_id=b, verdict=verdict, axis=axis,
        explanation="", differing_axes=[], missing_axes=[], values_differ=True,
        generation=generation, reconsidered=recovery is not None,
        recovery_axis=recovery, recovery_a_value="left", recovery_b_value="right",
        recovery_reason="because the page says so",
    )


@pytest.fixture
def session(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'reg.sqlite').as_posix()}")
    with session_scope() as s:
        s.add(Document(id=1, sha256="a" * 64, filename="x.pdf", source_path="x.pdf",
                       n_pages=1, n_chars=1))
        s.flush()
        base = dict(document_id=1, page_no=0, claim_type="measurement",
                    subject="s", predicate="p", evidence_quote="q",
                    qualifiers={}, unknown_qualifiers=[], confidence_reasons=[])
        for i in range(1, 7):
            s.add(Claim(id=i, **base))
        s.flush()
    with session_scope() as s:
        yield s
    reset_engine()


def test_one_sighting_is_a_candidate_and_two_is_a_vocabulary(session):
    """A candidate seen once may be one mis-read page. The same name arriving
    from independent pairs is a property of the corpus rather than of any
    single reading of it, and only then is it part of the vocabulary."""
    assert PROMOTION_THRESHOLD == 2
    session.add(relation(1, 2, recovery="sign_convention"))
    session.flush()
    record_axes(session, 1)
    assert session.query(Axis).filter_by(name="sign_convention").one().status \
        == "candidate"

    session.add(relation(3, 4, recovery="sign_convention"))
    session.flush()
    record_axes(session, 1)
    row = session.query(Axis).filter_by(name="sign_convention").one()
    assert row.status == "promoted"
    assert row.occurrences == 2
    assert row.promoted_at is not None


def test_counts_are_recomputed_not_incremented(session):
    """The store is append-only, so re-running the same corpus must not walk a
    candidate up to the threshold on its own. Typing the command twice is not
    two pairs agreeing."""
    session.add(relation(1, 2, recovery="segment"))
    session.flush()
    for _ in range(4):
        record_axes(session, 1)
    row = session.query(Axis).filter_by(name="segment").one()
    assert row.occurrences == 1
    assert row.status == "candidate"


def test_an_axis_that_stops_explaining_anything_says_so(session):
    """The counts belong to the current generation. Leaving an older run's
    numbers in place reports an axis dissolving disagreements the corpus no
    longer has, and a reader who checks finds nothing behind the claim."""
    session.add(relation(1, 2, generation=1, recovery="scenario"))
    session.add(relation(3, 4, generation=1, recovery="scenario"))
    session.flush()
    record_axes(session, 1)
    assert session.query(Axis).filter_by(name="scenario").one().status == "promoted"

    session.add(relation(5, 6, generation=2, axis="period"))
    session.flush()
    record_axes(session, 2)
    row = session.query(Axis).filter_by(name="scenario").one()
    assert row.status == "lapsed"
    assert (row.occurrences, row.resolves) == (0, 0)


def test_seeded_axes_are_listed_even_when_nothing_used_them(session):
    """So the table answers "what does this system compare on" in one query,
    rather than half in data and half in code."""
    seed_axes(session)
    names = {a.name for a in session.query(Axis)}
    assert {"period", "consolidation", "unit", "entity", "metric"} <= names
    assert all(a.status == "active" for a in session.query(Axis)
               if a.origin == "seeded")


def test_observation_outranks_grammar_and_both_are_kept(session):
    """A wrongly inferred single-holder role manufactures a contradiction, so
    the guess and the evidence that overruled it both have to be visible."""
    row = record_predicate(
        session, "Chief People Officer", inferred=1, observed=0,
        evidence="two concurrent holders in one document", holders=2)
    assert row.cardinality == 0            # many
    assert row.inferred == 1               # grammar said one
    assert row.basis == "observation"

    plain = record_predicate(session, "Registered Office", inferred=1)
    assert plain.cardinality == 1
    assert plain.basis == "grammar"
    assert plain.observed is None
