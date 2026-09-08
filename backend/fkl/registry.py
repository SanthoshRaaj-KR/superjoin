"""L5b — the axis registry, and the cardinality register beside it.

Both of these existed as behaviour before they existed as tables, and that was
the problem. Axes *were* discovered: a contradiction went back to its pages,
the second look found the distinction printed there, and the relation came back
carrying a name — `sign_convention`, `measure_basis`, `definition` — that no
vocabulary in this repository contains. Cardinality *was* inferred: a plural
head noun means a list, a singular one means a seat.

But a fact that is recomputed on every call is a fact nobody can inspect. You
cannot ask which axes this corpus taught the system, or how many pairs had to
agree before one was believed, or why this predicate is treated as a single
seat when a false 1 is exactly what manufactures a contradiction out of two
people doing different jobs. Writing them down is what turns both from an
implementation detail into something a reader can argue with.

**Promotion is the honest part.** A candidate seen on one pair is an anecdote —
possibly one mis-read page. The same candidate arriving independently from
``PROMOTION_THRESHOLD`` separate pairs is a property of the corpus rather than
of any single reading of it, and only then does it become part of the
vocabulary. The count is stored, so the threshold is auditable rather than a
number in someone's head.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Axis, Predicate, Relation

log = logging.getLogger(__name__)

# How many independent pairs must name a candidate before it is believed.
PROMOTION_THRESHOLD = 2

# Axes the system was born knowing. They are written to the registry too, so
# the table answers "what does this system compare on" in one query rather than
# forcing a reader to hold half the answer in code and half in data.
SEEDED = (
    "entity", "metric", "period", "unit", "consolidation", "modality",
)

MAX_VALUES = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _upsert(session: Session, name: str, origin: str) -> Axis:
    row = session.scalar(select(Axis).where(Axis.name == name))
    if row is None:
        row = Axis(name=name, origin=origin,
                   status="active" if origin == "seeded" else "candidate")
        session.add(row)
        session.flush()
    return row


def seed_axes(session: Session) -> None:
    for name in SEEDED:
        row = _upsert(session, name, "seeded")
        row.origin = "seeded"
        row.status = "active"


def record_axes(session: Session, generation: int) -> dict[str, int]:
    """Fold one relate run's axes into the registry and promote what recurs.

    Counts are recomputed from the relations of this generation rather than
    incremented, because the store is append-only and a re-run over the same
    corpus would otherwise inflate every candidate to the threshold on its own.
    An axis is believed because several *pairs* named it, not because the
    command was typed several times.
    """
    seed_axes(session)

    relations = session.scalars(
        select(Relation).where(Relation.generation == generation)).all()

    occurrences: dict[str, int] = {}
    resolves: dict[str, int] = {}
    values: dict[str, set[str]] = {}
    example: dict[str, tuple[int, str]] = {}
    origin: dict[str, str] = {}

    for relation in relations:
        # An axis reached the registry one of two ways, and they are counted
        # separately on purpose. `axis` is what the gate named from the
        # qualifier vectors it already had; `recovery_axis` is what the second
        # look brought back from the page. Only the second is a discovery.
        if relation.axis:
            name = relation.axis
            occurrences[name] = occurrences.get(name, 0) + 1
            origin.setdefault(name, "seeded" if name in SEEDED else "discovered")
            if relation.values_differ and relation.verdict not in (
                    "CONTRADICTS", "INSUFFICIENT_EVIDENCE"):
                resolves[name] = resolves.get(name, 0) + 1

        recovered = relation.recovery_axis
        if recovered:
            occurrences[recovered] = occurrences.get(recovered, 0) + 1
            origin[recovered] = "discovered"
            resolves[recovered] = resolves.get(recovered, 0) + 1
            for value in (relation.recovery_a_value, relation.recovery_b_value):
                if value:
                    values.setdefault(recovered, set()).add(value[:80])
            if recovered not in example and relation.recovery_reason:
                example[recovered] = (relation.id, relation.recovery_reason)

    out: dict[str, int] = {}
    for name, count in occurrences.items():
        row = _upsert(session, name, origin.get(name, "discovered"))
        row.occurrences = count
        row.resolves = resolves.get(name, 0)
        row.updated_at = _now()
        if name in values:
            row.values_seen = sorted(values[name])[:MAX_VALUES]
        if name in example:
            row.first_seen_relation_id, row.example = example[name]

        if row.origin == "seeded":
            row.status = "active"
        elif count >= PROMOTION_THRESHOLD:
            if row.status != "promoted":
                log.info("axis %r promoted on %s independent pair(s)", name, count)
                row.promoted_at = _now()
            row.status = "promoted"
        else:
            row.status = "candidate"
        out[name] = count

    session.flush()
    return out


def affected_pairs(session: Session, axis: str, generation: int) -> list[int]:
    """Relations a newly promoted axis has something to say about.

    The plan calls for re-running these. What it means in practice is narrower
    than it sounds: a promoted axis changes a verdict only where the pair is
    still an unexplained contradiction, because everything else already has an
    explanation that this axis would not displace. Returning the ids keeps the
    decision to re-run with the caller, which is right — re-running costs model
    calls and the API should not spend them on a page load.
    """
    return [
        r.id for r in session.scalars(
            select(Relation).where(
                Relation.generation == generation,
                Relation.verdict == "CONTRADICTS",
                Relation.recovery_axis.is_(None),
            )).all()
    ]


def rebuild(session: Session, generation: int | None = None) -> tuple[int, int]:
    """Recompute both registries from what is already stored.

    Needed because both tables arrived after the corpus did. Everything they
    hold is derivable from the relations and the state claims already in the
    store, so a rebuild is a read of existing data rather than a re-run of the
    pipeline — no model calls, no spend, and the answer is identical to what a
    fresh pass would have written.
    """
    from .models import Claim, Document, Metric
    from .relate import to_holding
    from .temporal import group_slots, infer_cardinality, observe_cardinality

    if generation is None:
        generation = session.scalar(
            select(Relation.generation).order_by(Relation.generation.desc())
            .limit(1)) or 1

    axes = record_axes(session, generation)

    primary = {d.id: d.primary_entity for d in session.scalars(select(Document))}
    metric_names = {m.id: m.canonical_name for m in session.scalars(select(Metric))}
    holdings = [
        to_holding(c, primary.get(c.document_id), metric_names.get(c.metric_id))
        for c in session.scalars(select(Claim).where(Claim.claim_type == "state"))
    ]

    predicates = 0
    for members in group_slots(holdings).values():
        seeded = infer_cardinality(members[0].predicate)
        cardinality, note = observe_cardinality(members, seeded)
        record_predicate(
            session,
            members[0].predicate,
            inferred=1 if seeded == 1 else 0,
            observed=None if cardinality == seeded else (1 if cardinality == 1 else 0),
            evidence=note,
            holders=len({m.filler for m in members}),
        )
        predicates += 1

    return len(axes), predicates


def record_predicate(
    session: Session,
    name: str,
    *,
    inferred: int,
    observed: int | None = None,
    evidence: str | None = None,
    holders: int = 0,
) -> Predicate:
    """Write down one predicate's cardinality and how it was arrived at.

    ``inferred`` is the grammar rule's answer; ``observed`` is what the corpus
    proved by showing two holders overlapping inside a *single* document. When
    they disagree the observation wins — one document asserting both at once is
    telling us the shape of the thing, and the head noun is only guessing at it.
    """
    row = session.scalar(select(Predicate).where(Predicate.name == name))
    if row is None:
        row = Predicate(name=name)
        session.add(row)

    row.inferred = inferred
    row.observed = observed
    row.cardinality = observed if observed is not None else inferred
    row.basis = "observation" if observed is not None else "grammar"
    row.holders = max(holders, row.holders or 0)
    if evidence:
        row.evidence = evidence
    row.updated_at = _now()
    session.flush()
    return row
