"""L4d — resolving the many names of one metric.

The annual report says "Revenue from contracts with customers". The earnings
deck says "Revenue from services". The management discussion says "Revenue from
operations". These are one metric, and until they collapse into one, the
corroboration case cannot even be attempted.

**Embeddings are a candidate generator here, not a decider.** That is a
measurement, not a preference. Embedding fifteen predicate pairs drawn from this
corpus with ``text-embedding-3-small`` produced two ranges that overlap heavily:

    should match      0.683 ....................... 0.944
    should NOT match  0.574 ............. 0.846

    0.687  Revenue from contracts with customers :: Revenue from services   SAME
    0.846  Adjusted EBITDA :: EBITDA                                        DIFFERENT
    0.801  Express Parcel revenue :: Express Parcel shipments               DIFFERENT
    0.683  Profit for the year :: Net profit                                SAME

No single threshold separates those. A cutoff loose enough to catch "Revenue
from contracts with customers" fuses "Adjusted EBITDA" with "EBITDA" — a real
distinction worth several billion rupees, since the adjustment is the whole
point of the line. So similarity retrieves candidates and something else
decides.

**What decides, in order of cost:**

1. An alias already on file. Free, and the reason a registry is worth keeping.
2. Dimension. Two metrics measured in different units are never the same
   metric however alike their names read — this alone separates "Express Parcel
   revenue" from "Express Parcel shipments" at 0.801 without a model call.
3. A model adjudication, on candidates above a similarity floor. One call per
   *distinct new predicate string*, and the answer is stored as an alias, so the
   cost is bounded by vocabulary size rather than by page count.
4. Below the floor, a new metric. Refusing to merge is always safe: the worst
   outcome is a comparison not made, whereas a wrong merge reports confident
   contradictions between two different quantities.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import SETTINGS
from .llm.client import structured
from .llm.embed import cosine, embed_texts
from .models import Metric, MetricAlias

log = logging.getLogger(__name__)

# Below this, two names are not worth a model call. Set under the lowest
# true-match observed (0.683) with room to spare, because a missed candidate is
# a silently unmade comparison and the call is cheap.
CANDIDATE_FLOOR = 0.60

# Above this, link without adjudicating. Deliberately above the highest observed
# false positive (0.846, `Adjusted EBITDA` :: `EBITDA`) rather than below the
# lowest true match, and calibrated on fifteen pairs — a thin sample, which is
# why the adjudicator remains the real decider and this only skips restatements
# that are obviously the same words rearranged.
AUTO_LINK = 0.92

# How many candidates to put in front of the adjudicator.
TOP_K = 5

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalize_predicate(predicate: str) -> str:
    """The exact-match key for a predicate string."""
    text = _PUNCT.sub(" ", (predicate or "").lower())
    return _SPACE.sub(" ", text).strip()


class MetricJudgement(BaseModel):
    """The adjudicator's answer. Structured so it cannot come back as prose."""

    same_metric: bool = Field(
        description="True only if the two names denote the same measured quantity"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, naming the distinction if any")


SYSTEM = """You decide whether two names refer to the same measured quantity.

Say the same metric ONLY when a financial analyst would put both numbers in one \
row of one table. Different wording for one quantity is the same metric: \
"Revenue from contracts with customers" and "Revenue from services" are the same.

These are NOT the same metric, and they are the mistakes that matter:
- an adjusted or normalised figure against its unadjusted parent \
("Adjusted EBITDA" is not "EBITDA" — the adjustment is the point)
- two segments or business lines of one company \
("Express Parcel revenue" is not "PTL freight revenue")
- a level against a change in that level ("Revenue" is not "Revenue growth")
- two different bases for one quantity ("Real GDP" is not "Nominal GDP")
- gross against net, or a component against its total

When unsure, answer false. A refused match costs one comparison; a wrong match \
merges two different quantities permanently and then reports confident \
contradictions between them."""


@dataclass
class MetricResolution:
    metric_id: int
    canonical_name: str
    method: str  # alias | embedding | adjudicated | new
    confidence: float
    similarity: float | None = None
    created: bool = False
    reason: str | None = None


def resolve_metric(
    session: Session,
    predicate: str,
    *,
    dimension: str | None = None,
    adjudicate: bool = True,
) -> MetricResolution:
    """Find or create the metric a predicate names.

    ``dimension`` comes from the parsed unit and is a hard filter rather than a
    hint: candidates measured in something else are removed before similarity is
    even considered.

    ``adjudicate=False`` disables the model call, which is what an offline run
    uses. It does not fall back to "link anyway above some threshold" — it falls
    back to creating a new metric, because the measurement above says a
    similarity score is not evidence of identity.
    """
    key = normalize_predicate(predicate)
    if not key:
        raise ValueError("cannot resolve an empty predicate")

    # 1. Seen before, in the same dimension.
    alias = session.scalars(
        select(MetricAlias).where(
            MetricAlias.alias_key == key, MetricAlias.dimension == dimension
        )
    ).first()
    if alias is not None:
        metric = session.get(Metric, alias.metric_id)
        return MetricResolution(
            metric.id, metric.canonical_name, "alias", alias.confidence,
            alias.similarity
        )

    # 2. Candidates of the same dimension only.
    pool = list(
        session.scalars(select(Metric).where(Metric.dimension == dimension))
    )
    if pool:
        names = [m.canonical_name for m in pool]
        vectors = embed_texts(session, names + [predicate])
        target = vectors.get(predicate.strip())
        scored = sorted(
            (
                (cosine(target, vectors[m.canonical_name]), m)
                for m in pool
                if target and m.canonical_name in vectors
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        candidates = [(s, m) for s, m in scored[:TOP_K] if s >= CANDIDATE_FLOOR]

        if candidates:
            best_score, best = candidates[0]
            if best_score >= AUTO_LINK:
                _learn(session, best, predicate, key, dimension, "embedding",
                       best_score)
                return MetricResolution(
                    best.id, best.canonical_name, "embedding", best_score,
                    best_score
                )
            if adjudicate:
                verdict = _adjudicate(predicate, best.canonical_name, dimension)
                if verdict.same_metric:
                    _learn(session, best, predicate, key, dimension,
                           "adjudicated", best_score, verdict.confidence)
                    return MetricResolution(
                        best.id, best.canonical_name, "adjudicated",
                        verdict.confidence, best_score, reason=verdict.reason
                    )
                log.info(
                    "kept apart: %r vs %r (%.3f) — %s",
                    predicate, best.canonical_name, best_score, verdict.reason,
                )

    # 3. New.
    metric = Metric(canonical_name=predicate.strip(), dimension=dimension)
    session.add(metric)
    session.flush()
    _learn(session, metric, predicate, key, dimension, "new", None)
    return MetricResolution(
        metric.id, metric.canonical_name, "new", 1.0, created=True
    )


def _adjudicate(a: str, b: str, dimension: str | None) -> MetricJudgement:
    unit = f"\nBoth are measured in: {dimension}." if dimension else ""
    return structured(
        response_model=MetricJudgement,
        system=SYSTEM,
        user=f"A: {a}\nB: {b}{unit}\n\nAre A and B the same metric?",
        model=SETTINGS.model_extract,
    )


def _learn(
    session: Session,
    metric: Metric,
    alias: str,
    key: str,
    dimension: str | None,
    method: str,
    similarity: float | None,
    confidence: float = 1.0,
) -> None:
    existing = session.scalars(
        select(MetricAlias).where(
            MetricAlias.alias_key == key, MetricAlias.dimension == dimension
        )
    ).first()
    if existing is not None:
        return
    session.add(
        MetricAlias(
            metric_id=metric.id,
            alias=alias.strip(),
            alias_key=key,
            dimension=dimension,
            method=method,
            similarity=similarity,
            confidence=confidence,
        )
    )
    session.flush()
