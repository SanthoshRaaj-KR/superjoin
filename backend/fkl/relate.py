"""L5b — running the gate over a whole corpus, and reporting what it dissolved.

The gate compares two claims. This decides which two, and turns the results into
the one number the project is actually arguing about:

    N pairs whose raw values disagree.
    M of those explained by a named context axis.
    K left genuinely unresolved.

That reduction is the whole claim. Three hand-picked examples prove nothing —
any approach can find three. Showing that most apparent disagreements across a
511-page corpus dissolve into named axes, and then showing the handful that
survive, is a different kind of statement.

**Blocking is what makes this tractable and is also the scaling story.** Claims
are grouped by ``(entity, metric)`` and only compared within a block. Comparing
every pair of 10,000 claims is 50 million comparisons; comparing within blocks
is a few thousand. Nothing is lost by it, because two claims about different
entities or different metrics were never going to be comparable — the gate would
have said INCOMPARABLE, and it can say so without being asked.

**A pair is only interesting if something about it differs.** Two identical rows
from two pages of one document corroborate trivially and there are thousands of
them; they are counted but not dwelt on. The pairs worth a human's attention are
the ones where the numbers differ and the question is why.
"""

from __future__ import annotations

import itertools
import logging
from collections import defaultdict
from datetime import date
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .compare import (
    CONTRADICTS,
    CORROBORATES,
    INSUFFICIENT_EVIDENCE,
    Comparable,
    compare,
)
from .models import Claim, Entity, Metric, Page, Relation
from .reconcile import reconcile
from .periods import UNKNOWN as UNKNOWN_PERIOD
from .periods import Period
from .units import UNKNOWN as UNKNOWN_UNIT
from .units import Unit
from .values import compare_values, written_precision

log = logging.getLogger(__name__)

# Blocks larger than this are almost always an extraction artefact — one metric
# that absorbed a whole table — and comparing them pairwise costs more than the
# result is worth. The cap is reported rather than applied silently.
MAX_BLOCK = 400


def to_comparable(
    claim: Claim, metric_name: str | None = None, entity_name: str | None = None
) -> Comparable:
    """A stored claim in the shape the gate reads.

    Canonical fields are used where they exist and raw ones only as a fallback,
    because a claim that failed normalisation should be visibly uncomparable
    rather than quietly compared on its raw number.
    """
    unit = (
        Unit(
            dimension=claim.unit_dimension,
            scale=claim.unit_scale or 1.0,
            currency=claim.unit_currency,
            raw=claim.unit_raw or "",
            confidence=claim.conf_normalization,
        )
        if claim.unit_dimension
        else UNKNOWN_UNIT
    )
    period = (
        Period(
            start=claim.period_start,
            end=claim.period_end,
            granularity=claim.period_granularity or "unknown",
            label=claim.period_label or "unknown",
            raw=claim.period_raw or "",
            confidence=claim.conf_normalization,
            ambiguous=bool(claim.period_ambiguous),
        )
        if claim.period_start
        else UNKNOWN_PERIOD
    )
    return Comparable(
        ref=str(claim.id),
        entity=entity_name or (str(claim.entity_id) if claim.entity_id
                               else f"?{claim.subject}"),
        metric=metric_name or (str(claim.metric_id) if claim.metric_id
                               else f"?{claim.predicate}"),
        unit=unit,
        period=period,
        value_canonical=claim.value_canonical,
        precision=written_precision(claim.value_raw, unit.scale)
        if claim.value_raw
        else None,
        value_text=claim.value_text,
        value_raw=claim.value_raw,
        qualifiers=claim.qualifiers or {},
        unknown_qualifiers=claim.unknown_qualifiers or [],
        modality=claim.modality or "actual",
        claim_type=claim.claim_type,
        document=str(claim.document_id),
        page_no=claim.page_no,
        evidence_quote=claim.evidence_quote,
        valid_from=claim.valid_from,
        valid_to=claim.valid_to,
        valid_to_is_open=bool(claim.valid_to_is_open),
    )


@dataclass
class RelateRun:
    blocks: int = 0
    claims: int = 0
    pairs: int = 0
    # The generation these verdicts were written under. Carried back so a caller
    # can list *this* run's survivors rather than every run's: the store is
    # append-only, so an unfiltered query returns one row per pair per run, and
    # the duplicates are silent — they look like independent findings.
    generation: int = 0
    oversized_blocks: list[tuple[str, int]] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    axes: dict[str, int] = field(default_factory=dict)
    # Axes that dissolved an apparent disagreement, as opposed to axes named on
    # any verdict at all. This is the list the headline points at.
    explaining_axes: dict[str, int] = field(default_factory=dict)
    # The reduction, over pairs whose raw values actually differ.
    raw_disagreements: int = 0
    explained: int = 0
    unresolved: int = 0
    blocked: int = 0
    cross_document: int = 0
    # The second look. `investigated` is how many contradictions were sent back
    # to the page; `withdrawn` is how many did not survive it. Reported apart
    # from the rest of the reduction on purpose — a contradiction the system
    # raised and then withdrew is a claim about its own first pass, and folding
    # it into the headline would hide that.
    investigated: int = 0
    withdrawn: int = 0
    recovered_axes: dict[str, int] = field(default_factory=dict)

    @property
    def reduction(self) -> float:
        """Share of apparent disagreements that a named axis dissolved."""
        if not self.raw_disagreements:
            return 0.0
        return self.explained / self.raw_disagreements


def relate_corpus(
    session: Session,
    *,
    document_ids: list[int] | None = None,
    generation: int | None = None,
    investigator=None,
    reconcile_signs: bool = True,
) -> RelateRun:
    """Compare every comparable pair of claims and store the verdicts.

    ``investigator`` turns on the second look: every pair the gate calls a
    contradiction is sent back to its pages to see whether the distinction was
    printed there and missed. Off by default, because it costs one model call
    per surviving contradiction and because the gate has to remain runnable —
    and testable — with no credentials at all.

    ``reconcile_signs`` runs only the deterministic sign-convention scout, which
    needs no model and no key. It is on by default for that reason.
    """
    stmt = select(Claim)
    if document_ids:
        stmt = stmt.where(Claim.document_id.in_(document_ids))
    claims = list(session.scalars(stmt))

    names = {
        m.id: m.canonical_name for m in session.scalars(select(Metric))
    }
    entity_names = {
        e.id: e.canonical_name for e in session.scalars(select(Entity))
    }

    if generation is None:
        current = session.scalar(select(Relation.generation).order_by(
            Relation.generation.desc()).limit(1))
        generation = (current or 0) + 1

    blocks: dict[tuple, list[Claim]] = defaultdict(list)
    for claim in claims:
        # Claims that never resolved to an entity and a metric cannot be
        # blocked, and comparing them by raw strings would invent matches the
        # registries deliberately refused to make.
        if claim.entity_id is None or claim.metric_id is None:
            continue
        blocks[(claim.entity_id, claim.metric_id)].append(claim)

    run = RelateRun(claims=len(claims), generation=generation)
    pages: dict = {}

    for (entity_id, metric_id), members in blocks.items():
        if len(members) < 2:
            continue
        run.blocks += 1
        if len(members) > MAX_BLOCK:
            run.oversized_blocks.append((names.get(metric_id, str(metric_id)),
                                         len(members)))
            continue

        metric_name = names.get(metric_id, str(metric_id))
        entity_name = entity_names.get(entity_id, str(entity_id))
        comparables = {
            c.id: to_comparable(c, metric_name, entity_name) for c in members
        }

        for left, right in itertools.combinations(members, 2):
            verdict = compare(comparables[left.id], comparables[right.id])
            run.pairs += 1

            # A contradiction is a hypothesis, not a conclusion. Before it is
            # recorded, go back to the two pages and look for the distinction
            # the extraction may have dropped. The gate is what re-decides;
            # this only supplies it with context it did not have.
            recovery = None
            original = verdict.verdict
            if verdict.verdict == CONTRADICTS and (investigator or reconcile_signs):
                run.investigated += 1
                result = reconcile(
                    comparables[left.id], comparables[right.id],
                    a_page=_page_text(session, pages, left),
                    b_page=_page_text(session, pages, right),
                    investigator=investigator,
                )
                if result.changed:
                    verdict, recovery = result.verdict, result.recovery
                    run.withdrawn += 1
                    run.recovered_axes[recovery.axis] = (
                        run.recovered_axes.get(recovery.axis, 0) + 1)

            run.verdicts[verdict.verdict] = run.verdicts.get(verdict.verdict, 0) + 1
            if verdict.axis:
                run.axes[verdict.axis] = run.axes.get(verdict.axis, 0) + 1

            # Computed independently of the gate, and this matters. The gate
            # short-circuits: two claims in different periods return
            # CONTEXTUAL_TEMPORAL without the values ever being compared. Using
            # the gate's own value result as the denominator would therefore
            # count only the pairs it could not explain, and the reduction would
            # read 0% no matter how much context resolved — which is exactly
            # what the first run of this reported.
            #
            # The denominator has to be the population a context-blind system
            # would have flagged: same entity, same metric, values differ. That
            # is the baseline this project is arguing against.
            naive = compare_values(
                comparables[left.id].value_canonical,
                comparables[right.id].value_canonical,
                a_precision=comparables[left.id].precision,
                b_precision=comparables[right.id].precision,
            )
            values_differ = naive.relation == "disagree"
            cross = left.document_id != right.document_id
            if cross:
                run.cross_document += 1

            # The reduction is counted over pairs that *look* like
            # disagreements: the values differ, and something had to decide
            # whether that mattered. A pair the gate refused before ever
            # comparing values is not an explained disagreement — it is a pair
            # nobody could have called a disagreement in the first place.
            if values_differ:
                run.raw_disagreements += 1
                if verdict.verdict == CONTRADICTS:
                    run.unresolved += 1
                elif verdict.verdict == INSUFFICIENT_EVIDENCE:
                    run.blocked += 1
                else:
                    run.explained += 1
                    if verdict.axis:
                        run.explaining_axes[verdict.axis] = (
                            run.explaining_axes.get(verdict.axis, 0) + 1)

            session.add(
                Relation(
                    claim_a_id=left.id,
                    claim_b_id=right.id,
                    verdict=verdict.verdict,
                    axis=verdict.axis,
                    explanation=verdict.explanation,
                    differing_axes=verdict.differing_axes,
                    missing_axes=verdict.missing_axes,
                    values_differ=values_differ,
                    value_difference=naive.difference,
                    value_relative=naive.relative,
                    period_relation=verdict.period_relation,
                    cross_document=cross,
                    generation=generation,
                    reconsidered=recovery is not None,
                    original_verdict=original if recovery is not None else None,
                    recovery_axis=recovery.axis if recovery else None,
                    recovery_method=recovery.method if recovery else None,
                    recovery_reason=recovery.reason if recovery else None,
                    recovery_confidence=recovery.confidence if recovery else None,
                )
            )

    session.flush()
    log.info("compared %s pairs across %s blocks", run.pairs, run.blocks)
    return run


def _page_text(session: Session, cache: dict, claim: Claim) -> str:
    """The laid-out reading of a claim's page, loaded once and reused.

    ``rendered_text`` rather than ``text``, because the distinctions this is
    searched for live in table structure: a scenario table's row label is only
    beside its figures once reading order has been restored. Raw text is
    appended so that a span found only in the original still grounds.
    """
    key = (claim.document_id, claim.page_no)
    if key not in cache:
        page = session.scalar(
            select(Page).where(Page.document_id == claim.document_id,
                               Page.page_no == claim.page_no)
        )
        cache[key] = "\n".join(
            filter(None, [page.rendered_text, page.text])) if page else ""
    return cache[key]


def format_run(run: RelateRun) -> str:
    """The headline, in the shape the README and the demo both use."""
    lines = [
        f"{run.claims} claims -> {run.blocks} comparable blocks -> {run.pairs} pairs "
        f"({run.cross_document} cross-document)",
        "",
        f"  {run.raw_disagreements} pairs whose raw values disagree",
        f"    {run.explained} explained by a named context axis",
        f"    {run.blocked} blocked — a material axis was undetermined",
        f"    {run.unresolved} genuinely unresolved",
    ]
    if run.investigated:
        lines.append("")
        lines.append(
            f"  second look: {run.investigated} contradiction(s) sent back to "
            f"the page, {run.withdrawn} withdrawn"
        )
        if run.recovered_axes:
            lines.append("    context recovered on review: " + " · ".join(
                f"{k} {v}" for k, v in
                sorted(run.recovered_axes.items(), key=lambda kv: -kv[1])))
    if run.raw_disagreements:
        lines.append(f"  reduction: {run.reduction:.0%} of apparent disagreements "
                     "dissolved by context")
    if run.verdicts:
        lines.append("")
        lines.append("  verdicts: " + " · ".join(
            f"{k} {v}" for k, v in sorted(run.verdicts.items(), key=lambda kv: -kv[1])))
    if run.explaining_axes:
        lines.append("  axes that dissolved a disagreement: " + " · ".join(
            f"{k} {v}" for k, v in
            sorted(run.explaining_axes.items(), key=lambda kv: -kv[1])))
    if run.axes:
        lines.append("  axes named on any verdict: " + " · ".join(
            f"{k} {v}" for k, v in sorted(run.axes.items(), key=lambda kv: -kv[1])))
    if run.oversized_blocks:
        lines.append("")
        lines.append(f"  {len(run.oversized_blocks)} block(s) skipped as oversized: "
                     + ", ".join(f"{n} ({c})" for n, c in run.oversized_blocks[:5]))
    return "\n".join(lines)


def conflicts(session: Session, limit: int = 20, generation: int | None = None):
    """The pairs that survived — what a reader should actually look at."""
    stmt = select(Relation).where(Relation.verdict == CONTRADICTS)
    if generation is not None:
        stmt = stmt.where(Relation.generation == generation)
    return list(session.scalars(stmt.order_by(Relation.value_relative.desc()).limit(limit)))


def withdrawn(session: Session, limit: int = 20, generation: int | None = None):
    """Contradictions the system raised and then took back.

    Worth listing separately from the rest of the reduction. These are the pairs
    where the first pass was wrong and the second caught it, and each one names
    the context the extraction dropped — which is a to-do list for the extractor
    as much as it is a result.
    """
    stmt = select(Relation).where(Relation.reconsidered.is_(True))
    if generation is not None:
        stmt = stmt.where(Relation.generation == generation)
    return list(session.scalars(stmt.limit(limit)))


def corroborations(session: Session, limit: int = 20, cross_document_only: bool = True):
    stmt = select(Relation).where(Relation.verdict == CORROBORATES)
    if cross_document_only:
        stmt = stmt.where(Relation.cross_document.is_(True))
    return list(session.scalars(stmt.limit(limit)))


# --- state claims: the interval engine over a corpus ------------------------


def to_holding(claim: Claim, primary_entity: str | None = None,
               metric_name: str | None = None):
    """A stored state claim as an interval assertion.

    Two shapes arrive and the discriminator is whether the claim's subject *is*
    the organisation:

    - ``Mr. Sunil Kumar Bansal | Company Secretary`` is a role. The seat belongs
      to the company, so scope is the company and the person is the filler.
    - ``Delhivery Limited | CIN | L63090...`` is an attribute. The company is
      the scope and the identifier fills it.

    ``org_scope`` is null in the common case, because the extraction schema uses
    it only for a *different* organisation — a named subsidiary or segment. So
    the document's primary entity is the fallback, which is what makes every
    role on the annual report's KMP table block into one company's seats rather
    than into nothing.

    ``metric_name`` is the canonical role, and passing it is what keeps the two
    engines agreeing about what one seat is. The gate blocks on the resolved
    ``metric_id``; this blocked on the raw predicate string. So Donald
    Colleran's "Non Executive - Nominee Director (till May 23, 2022)" and his
    "Non-Executive Director (w.e.f. May 24, 2022)" — one man, one redesignation,
    contiguous to the day — were one seat to the gate, which compared their text
    and reported a contradiction, and two seats to the interval engine, which
    therefore said nothing. A redesignation announced as a conflict is the exact
    failure this project exists to remove.
    """
    from .temporal import Holding

    org = claim.org_scope or primary_entity or claim.subject
    if (claim.subject or "").strip().lower() == org.strip().lower():
        filler = claim.value_text or ""  # an attribute of the organisation
    else:
        filler = claim.subject  # a person, or another company, in a slot

    return Holding(
        ref=str(claim.id),
        scope=org,
        predicate=metric_name or claim.predicate,
        filler=filler,
        valid_from=claim.valid_from,
        valid_to=claim.valid_to,
        valid_to_is_open=bool(claim.valid_to_is_open),
        asserted=claim.assertion_time,
        document=str(claim.document_id),
        page_no=claim.page_no,
        evidence_quote=claim.evidence_quote,
    )


def relate_states(
    session: Session,
    *,
    document_ids: list[int] | None = None,
    generation: int | None = None,
) -> RelateRun:
    """Run the interval engine over every stored state claim."""
    from .models import Document
    from .temporal import (group_slots, infer_cardinality, observe_cardinality,
                           relate_holdings, succession_pairs)

    stmt = select(Claim).where(Claim.claim_type == "state")
    if document_ids:
        stmt = stmt.where(Claim.document_id.in_(document_ids))
    claims = {c.id: c for c in session.scalars(stmt)}

    primary = {
        d.id: d.primary_entity for d in session.scalars(select(Document))
    }
    metric_names = {m.id: m.canonical_name for m in session.scalars(select(Metric))}
    holdings = [
        to_holding(c, primary.get(c.document_id), metric_names.get(c.metric_id))
        for c in claims.values()
    ]

    if generation is None:
        current = session.scalar(select(Relation.generation).order_by(
            Relation.generation.desc()).limit(1))
        generation = (current or 0) + 1

    run = RelateRun(claims=len(claims), generation=generation)
    for slot, members in group_slots(holdings).items():
        if len(members) < 2:
            continue
        run.blocks += 1
        seeded = infer_cardinality(members[0].predicate)
        cardinality, note = observe_cardinality(members, seeded)

        candidates = (
            succession_pairs(members) if cardinality == 1
            else [(a, b) for a, b in itertools.combinations(members, 2)
                  if a.filler == b.filler]
        )
        for left, right in candidates:
            verdict = relate_holdings(left, right, cardinality, note)
            run.pairs += 1
            if verdict is None:
                # Cardinality allows both. Two of many directors serving at
                # once is not a finding, and emitting it would bury the ones
                # that are under every pair of board members who overlapped.
                run.verdicts["NO_RELATION"] = run.verdicts.get("NO_RELATION", 0) + 1
                continue

            run.verdicts[verdict.verdict] = run.verdicts.get(verdict.verdict, 0) + 1
            if verdict.axis:
                run.axes[verdict.axis] = run.axes.get(verdict.axis, 0) + 1
            if verdict.verdict == CONTRADICTS:
                run.raw_disagreements += 1
                run.unresolved += 1
            elif verdict.verdict in {"CLOSES_INTERVAL", "SUCCESSION",
                                     "SUCCESSION_WITH_VACANCY", "CONTINUES",
                                     "CONTEXTUAL"}:
                run.raw_disagreements += 1
                run.explained += 1
                run.explaining_axes[verdict.axis] = (
                    run.explaining_axes.get(verdict.axis, 0) + 1)

            a, b = claims[int(verdict.a)], claims[int(verdict.b)]
            session.add(
                Relation(
                    claim_a_id=a.id,
                    claim_b_id=b.id,
                    verdict=verdict.verdict,
                    axis=verdict.axis,
                    explanation=verdict.explanation,
                    differing_axes=[],
                    missing_axes=[],
                    values_differ=a.value_text != b.value_text,
                    value_difference=float(verdict.gap_days)
                    if verdict.gap_days is not None else None,
                    period_relation=None,
                    cross_document=a.document_id != b.document_id,
                    generation=generation,
                )
            )
    session.flush()
    return run


def as_of(session: Session, on: "date", document_ids: list[int] | None = None):
    """Who held what on a given date. A query, never a stored field."""
    from .models import Document
    from .temporal import roster

    stmt = select(Claim).where(Claim.claim_type == "state")
    if document_ids:
        stmt = stmt.where(Claim.document_id.in_(document_ids))
    primary = {d.id: d.primary_entity for d in session.scalars(select(Document))}
    metric_names = {m.id: m.canonical_name for m in session.scalars(select(Metric))}
    holdings = [
        to_holding(c, primary.get(c.document_id), metric_names.get(c.metric_id))
        for c in session.scalars(stmt)
    ]
    return roster(holdings, on)
