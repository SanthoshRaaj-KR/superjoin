"""Ask a question in English, get an answer that refuses to average.

The interesting thing here is not that a question can be asked. It is what
happens when the honest answer is more than one number.

Ask most systems for Delhivery's FY24 revenue and they return a figure. This
corpus contains two — ``74,540.82`` standalone and ``81,415.38`` consolidated —
and both are correct. A system that picks one is guessing on the reader's
behalf, and a system that averages them has invented a number that appears in
no document. So the answer is **split by the context that separates the
figures**, with the axis named:

    Delhivery Limited - revenue from services - FY2023-24
      consolidation = consolidated   INR 81,415.38M   AR FY24 p35
      consolidation = standalone     INR 74,540.82M   AR FY24 p21

That is the whole thesis applied to retrieval, and it is why this endpoint is
worth having beyond the demo: the comparability gate already knows which axis
distinguishes two claims, so the answer can carry that reasoning rather than
suppressing it.

**Where the model is used, and where it is not.** The model parses the question
into ``(entity, metric, period)`` and stops. Retrieval is a SQL query over
typed claims - exact, complete, and free - and the grouping that produces the
split is the qualifier vectors the extractor already attached. Nothing here
generates prose about the numbers, so there is no path by which the answer can
say something the store does not contain.

Without a key the parser degrades to matching the question against the entity
and metric registries. Worse recall, same answer shape, and it means the
endpoint is demonstrable with no credentials at all.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Claim, Entity, Metric, MetricAlias

log = logging.getLogger(__name__)

MAX_ANSWERS = 8
MAX_SUPPORT = 4

# Axes that are never a reason to split an answer. Two claims from different
# pages of one document are not two answers; two claims with a different
# consolidation basis are.
_IGNORED_AXES = {"page", "source", "document"}

SYSTEM = """You turn a question about a corpus of financial and macroeconomic \
documents into a query specification. You do not answer the question.

Return the entity, the metric and the period the question is asking about, each \
in the words a document would use rather than the words the question used.

  "what was Delhivery's revenue last year"
     entity  Delhivery Limited
     metric  revenue
     period  FY2024

  "how fast is India growing in FY26"
     entity  India
     metric  real GDP growth
     period  FY2026

  "who is the company secretary"
     entity  Delhivery Limited
     metric  Company Secretary

Rules:
- Leave a field empty when the question does not constrain it. An empty period \
means every period, which is usually what "what has revenue been" wants.
- Never invent a period the question did not ask for. "revenue" alone has no \
period; "revenue in FY24" does.
- metric is the measured thing, without the entity and without the period. \
"revenue", not "Delhivery revenue FY24".
- For a question about a role or a post, the metric is the role itself: \
"Company Secretary", "Chief Financial Officer".
"""


class QuerySpec(BaseModel):
    entity: str = Field("", description="the entity, as a document would name it")
    metric: str = Field("", description="the measured thing, alone")
    period: str = Field("", description="the period, or empty for all periods")


@dataclass
class Answer:
    """One reading of the question - a value plus the context it holds under."""

    value: str
    canonical: float | None
    unit: str
    period: str | None
    context: dict[str, str] = field(default_factory=dict)
    support: list[dict] = field(default_factory=list)


def parse_question(question: str) -> QuerySpec:
    """Question to query spec. One structured call, no answering."""
    from .llm.client import structured

    return structured(response_model=QuerySpec, system=SYSTEM, user=question)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def parse_question_offline(session: Session, question: str) -> QuerySpec:
    """The no-key fallback: match the question against the registries.

    Deliberately dumb - token overlap against canonical names and aliases the
    corpus has already learned. It finds "Delhivery" and "revenue" in a
    question that mentions them and gives up on anything subtler, which is the
    right failure: an endpoint that works without credentials is worth more
    than one that works better and cannot be run.
    """
    words = _tokens(question)

    def best(rows: list[tuple[str, str]]) -> str:
        scored = [
            (len(words & _tokens(text)) / (len(_tokens(text)) or 1), canonical)
            for text, canonical in rows
            if words & _tokens(text)
        ]
        return max(scored)[1] if scored else ""

    entities = [(e.canonical_name, e.canonical_name)
                for e in session.scalars(select(Entity))]
    metrics = [(m.canonical_name, m.canonical_name)
               for m in session.scalars(select(Metric))]
    metrics += [(a.alias, a.metric.canonical_name)
                for a in session.scalars(select(MetricAlias)) if a.metric]

    period = ""
    match = re.search(r"\b(?:fy\s?)?(20\d{2})(?:[-/](\d{2,4}))?\b|\bfy\s?(\d{2})\b",
                      question, re.IGNORECASE)
    if match:
        period = match.group(0).upper().replace(" ", "")

    return QuerySpec(entity=best(entities), metric=best(metrics), period=period)


def _matches_period(claim: Claim, wanted: str) -> bool:
    """Does this claim's period answer the one the question asked for?

    Compared on the canonical label first, because that is the whole reason
    periods are canonicalised: the IMF writes ``FY2024/25`` where the RBI writes
    ``2024-25`` and both mean the same twelve months. Falling back to the raw
    text catches the questions whose period never parsed.
    """
    if not wanted:
        return True
    key = re.sub(r"[^0-9a-z]", "", wanted.lower())
    for candidate in (claim.period_label, claim.period_raw):
        if candidate and key in re.sub(r"[^0-9a-z]", "", candidate.lower()):
            return True
    # "FY24" against a stored "FY2023-24": compare the year that ends it.
    years = re.findall(r"\d{4}|\d{2}", key)
    if years and claim.period_end:
        tail = years[-1]
        end = str(claim.period_end.year)
        return end.endswith(tail[-2:]) if len(tail) == 2 else end == tail
    return False


def _context_key(claim: Claim, metric: str) -> tuple:
    """Everything that has to match before two claims are the same answer.

    The first version grouped on the qualifier dict alone, and it produced an
    answer that was wrong in the most embarrassing way available to this
    project: ``12.7%``, ``2,194`` and ``81,415.38`` landed in one group,
    because none of them carried a qualifier, and the group was then reported
    under whichever one happened to sort first. Three unrelated facts averaged
    into one by omission - exactly the failure the whole system exists to
    refuse.

    So the key is the comparability key. Two claims are one answer only if they
    would corroborate: same metric, same period, same dimension, same
    qualifiers. Anything else is a *different* answer, and saying so is the
    point - "revenue" in FY24 has several correct answers and they are
    distinguished by which revenue, on what basis, over which months.
    """
    qualifiers = {
        k: str(v) for k, v in (claim.qualifiers or {}).items()
        if k not in _IGNORED_AXES and v
    }
    qualifiers.setdefault("modality", claim.modality or "actual")
    return (
        metric,
        _period_of(claim),
        claim.unit_dimension or "",
        tuple(sorted(qualifiers.items())),
    )


def _period_of(claim: Claim) -> str:
    """The window a claim speaks about - a reporting period, or a tenure.

    A role has no ``period_label``; it has a validity interval, and that
    interval is what separates one answer from another. Without this the three
    people who have held Delhivery's Company Secretary post come back as one
    answer "asserted three times", which is not three assertions of one fact
    but three facts about three different stretches of time - the succession
    the temporal engine exists to model, flattened at the last step.
    """
    if claim.claim_type == "state" and (claim.valid_from or claim.valid_to):
        start = claim.valid_from.isoformat() if claim.valid_from else "…"
        if claim.valid_to and not claim.valid_to_is_open:
            return f"{start} → {claim.valid_to.isoformat()}"
        return f"{start} → present"
    return claim.period_label or claim.period_raw or ""


def find_claims(session: Session, spec: QuerySpec) -> list[Claim]:
    """Exact retrieval over typed claims. No vectors, no chunks.

    Entity and metric are matched through the registries the corpus built, so
    a question naming "Delhivery" reaches claims stored against "Delhivery
    Limited" without either string having to appear in the other.
    """
    entities = {e.id: e.canonical_name for e in session.scalars(select(Entity))}
    metrics = {m.id: m.canonical_name for m in session.scalars(select(Metric))}

    def resolve(wanted: str, table: dict[int, str]) -> set[int]:
        """Widen the match until something is found, never past overlap.

        Three passes, and the order is the point. An exact name wins outright.
        Failing that, one string containing the other - "Delhivery" against
        "Delhivery Limited", or "revenue from services" against "revenue" -
        which is how a question's vocabulary meets a document's. Only if both
        come back empty does bare token overlap count, because overlap alone
        matches "revenue" to "deferred revenue" and would answer a question
        with a different metric.
        """
        if not wanted:
            return set(table)
        want = _tokens(wanted)
        exact = {i for i, name in table.items() if name.lower() == wanted.lower()}
        if exact:
            return exact
        contained = {
            i for i, name in table.items()
            if want <= _tokens(name) or _tokens(name) <= want
        }
        if contained:
            return contained
        return {i for i, name in table.items() if want & _tokens(name)}

    want_entities = resolve(spec.entity, entities)
    want_metrics = resolve(spec.metric, metrics)

    # A role claim resolves its entity to the *person* - Bansal, not Delhivery -
    # because the person is what the claim is about. So "who is Delhivery's
    # company secretary" filtered on the company matches nothing at all, which
    # is how this endpoint first answered a question the corpus can answer
    # three times over. The organisation for a role is its `org_scope`, or
    # failing that the primary entity of the document it came from.
    from .models import Document

    org_of = {
        d.id: d.primary_entity for d in session.scalars(select(Document))
    }
    wanted_names = {entities[i].lower() for i in want_entities if i in entities}

    def entity_matches(claim: Claim) -> bool:
        if not spec.entity:
            return True
        if claim.entity_id in want_entities:
            return True
        if claim.claim_type != "state":
            return False
        org = claim.org_scope or org_of.get(claim.document_id) or ""
        if not org:
            return False
        org = org.lower()
        return any(org == name or _tokens(name) & _tokens(org)
                   for name in wanted_names)

    stmt = select(Claim)
    if spec.metric:
        stmt = stmt.where(Claim.metric_id.in_(want_metrics))

    return [
        c for c in session.scalars(stmt)
        if entity_matches(c) and _matches_period(c, spec.period)
    ]


def split_by_context(
    claims: list[Claim], metrics: dict[int, str]
) -> list[tuple[dict, list[Claim]]]:
    """Group claims into answers, best supported first.

    Each group is one answer, and the dict returned beside it is the full
    context that answer holds under - the metric, the period and the qualifiers
    together, because all three are things that make two figures different
    answers rather than a disagreement.
    """
    groups: dict[tuple, list[Claim]] = {}
    for claim in claims:
        metric = metrics.get(claim.metric_id) or claim.predicate
        groups.setdefault(_context_key(claim, metric), []).append(claim)

    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0]))
    out = []
    for (metric, period, dimension, qualifiers), members in ordered:
        context = {"metric": metric}
        if period:
            context["period"] = period
        context.update(dict(qualifiers))
        out.append((context, members))
    return out


def distinguishing_axes(groups: list[tuple[dict, list[Claim]]]) -> list[str]:
    """The axes on which the answers actually differ.

    An axis every group agrees on explains nothing. Only the ones that vary
    across groups are what separates one answer from another, and naming them
    is what turns two numbers into one honest answer. An axis that is *absent*
    from some groups counts as varying: a claim with no consolidation basis is
    not thereby consolidated, which is the same "absent is not equal" rule the
    comparability gate runs on.
    """
    seen: dict[str, set[str]] = {}
    for context, _ in groups:
        for axis in {a for c, _ in groups for a in c}:
            seen.setdefault(axis, set()).add(context.get(axis, "—"))
    return sorted(axis for axis, values in seen.items() if len(values) > 1)


def incoherent(members: list[Claim]) -> bool:
    """Do claims sharing one context nevertheless disagree on the value?

    If they do, the corpus asserts two different figures under a context that
    does not distinguish them, and the honest answer names that rather than
    picking the more confident one. It is the ``INSUFFICIENT_EVIDENCE`` verdict
    arriving through a different door.
    """
    values = {
        c.value_canonical if c.value_canonical is not None else c.value_text
        for c in members
    }
    values.discard(None)
    if len(values) < 2:
        return False
    numeric = [v for v in values if isinstance(v, (int, float))]
    if len(numeric) == len(values) and numeric:
        span = max(numeric) - min(numeric)
        scale = max(abs(v) for v in numeric) or 1.0
        return (span / scale) > 0.01  # rounding is not disagreement
    return True
