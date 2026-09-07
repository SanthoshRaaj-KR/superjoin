"""Phase 2.5 — the hand-labelled gold set, and the checks that keep it honest.

Built *before* the comparability gate, deliberately. A gold set written after
the engine exists tends to describe what the engine already does; written first,
it is a target the engine has to reach. Every relation in ``relations.yaml`` was
labelled by reading the source pages, and several of them turned out to be
different from what the plan assumed.

**The gold set is verified against the PDFs, not trusted.** Each claim carries a
``quote_contains`` string that must appear in the raw text of the page it cites.
A mistyped figure or an off-by-one page number is the worst possible defect in a
file like this — it silently becomes the standard everything else is measured
against — so ``verify()`` re-reads the documents and checks all of them.

**It contains negatives.** A gold set of only true matches measures nothing,
because a system that links everything scores perfectly on it. Roughly a third
of the relations here are pairs that must *not* be linked: two business
segments, two periods, two lines of one statement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz
import yaml

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO / "gold"

# Verdicts a relation may carry. Checked on load, because a typo here would
# quietly excuse the gate from ever producing the verdict that was meant.
VERDICTS = {
    "CORROBORATES",
    "CONTRADICTS",
    "CONTEXTUAL",
    "CONTEXTUAL_TEMPORAL",
    "CLOSES_INTERVAL",
    "SUCCESSION",
    "SUCCESSION_WITH_VACANCY",
    "INSUFFICIENT_EVIDENCE",
    "INCOMPARABLE",
}


@dataclass
class GoldSet:
    documents: dict[str, str]
    claims: list[dict]
    relations: list[dict]

    @property
    def by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.claims}

    def path_for(self, doc_key: str) -> Path:
        return REPO / self.documents[doc_key]


def load(gold_dir: Path | None = None) -> GoldSet:
    """Read the gold set and check it is internally consistent."""
    gold_dir = gold_dir or GOLD_DIR
    claims_doc = yaml.safe_load((gold_dir / "claims.yaml").read_text(encoding="utf-8"))
    relations_doc = yaml.safe_load(
        (gold_dir / "relations.yaml").read_text(encoding="utf-8")
    )
    gold = GoldSet(
        documents=claims_doc["documents"],
        claims=claims_doc["claims"],
        relations=relations_doc["relations"],
    )

    seen: set[str] = set()
    for claim in gold.claims:
        if claim["id"] in seen:
            raise ValueError(f"duplicate gold claim id: {claim['id']}")
        seen.add(claim["id"])
        if claim["doc"] not in gold.documents:
            raise ValueError(f"{claim['id']}: unknown document key {claim['doc']!r}")

    for relation in gold.relations:
        if relation["verdict"] not in VERDICTS:
            raise ValueError(
                f"{relation['id']}: unknown verdict {relation['verdict']!r}"
            )
        for side in ("a", "b"):
            if relation[side] not in seen:
                raise ValueError(
                    f"{relation['id']}: side {side} names no gold claim "
                    f"({relation[side]!r})"
                )
        # A CONTEXTUAL verdict with no axis is a shrug rather than an
        # explanation, and the whole thesis is that the axis gets named.
        if relation["verdict"].startswith("CONTEXTUAL") and not relation.get("axis"):
            raise ValueError(f"{relation['id']}: CONTEXTUAL without a named axis")

    return gold


@dataclass
class VerifyResult:
    checked: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def verify(gold: GoldSet | None = None) -> VerifyResult:
    """Check every ``quote_contains`` really appears on the page it cites.

    Whitespace is folded before comparing, because a quote spanning a line break
    in the PDF is still the same quote — the same normalisation the grounding
    validator applies, for the same reason.
    """
    gold = gold or load()
    result = VerifyResult()
    cache: dict[str, list[str]] = {}

    for claim in gold.claims:
        needle = claim.get("quote_contains")
        if not needle:
            continue
        key = claim["doc"]
        if key not in cache:
            doc = fitz.open(gold.path_for(key))
            cache[key] = [page.get_text() for page in doc]
            doc.close()
        pages = cache[key]
        page_no = claim["page"]
        result.checked += 1

        if page_no >= len(pages):
            result.failures.append(
                f"{claim['id']}: page {page_no} is past the end of {key}"
            )
            continue

        folded = " ".join(pages[page_no].split())
        if " ".join(needle.split()) not in folded:
            found = [
                i for i, t in enumerate(pages)
                if " ".join(needle.split()) in " ".join(t.split())
            ]
            where = f"; it is on page(s) {found[:5]}" if found else "; not in this document"
            result.failures.append(
                f"{claim['id']}: {needle!r} is not on {key} page {page_no}{where}"
            )
    return result


def summarize(gold: GoldSet) -> str:
    """A short description of what the gold set covers."""
    verdicts: dict[str, int] = {}
    for relation in gold.relations:
        verdicts[relation["verdict"]] = verdicts.get(relation["verdict"], 0) + 1
    axes = sorted({r["axis"] for r in gold.relations if r.get("axis")})
    docs = sorted({c["doc"] for c in gold.claims})

    lines = [
        f"{len(gold.claims)} claims across {len(docs)} documents "
        f"({', '.join(docs)})",
        f"{len(gold.relations)} labelled relations:",
    ]
    for verdict, count in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {count:>2}  {verdict}")
    lines.append("  resolving axes: " + ", ".join(axes))
    return "\n".join(lines)


# --- scoring the gate against the labels ------------------------------------
#
# The gate is scored against the gold *claims* directly rather than against
# extracted ones. That is deliberate: it measures the comparability gate, not
# the extractor, and the two fail for entirely different reasons. End-to-end
# scoring — did extraction find these claims at all — is a separate question
# with a separate number.

# What belongs to the interval engine rather than to the gate: the verdicts only
# it can produce, and `valid_time`, which is its axis. A relation resolved by
# valid_time needs valid_from and valid_to reasoning, not a period label — the
# CIN changing at listing is a difference in *when*, and the gate has no way to
# see that.
#
# These are reported as deferred rather than as failures, and counted
# separately, so the headline number says what it actually measured instead of
# quietly excluding what it cannot do.
TEMPORAL_VERDICTS = {"CLOSES_INTERVAL", "SUCCESSION", "SUCCESSION_WITH_VACANCY"}
TEMPORAL_AXES = {"valid_time"}


def is_temporal(relation: dict) -> bool:
    return (
        relation["verdict"] in TEMPORAL_VERDICTS
        or relation.get("axis") in TEMPORAL_AXES
    )


def to_comparable(claim: dict):
    """Turn a labelled gold claim into the gate's input shape."""
    from .compare import Comparable
    from .periods import parse_period
    from .units import UNKNOWN, Unit
    from .values import written_precision

    spec = claim.get("unit") or {}
    if spec:
        unit = Unit(
            dimension=spec.get("dimension", "unknown"),
            scale=_scale_factor(spec.get("scale")),
            currency=spec.get("currency"),
            scale_name=spec.get("scale"),
            raw=str(spec),
            confidence=1.0,
        )
    else:
        unit = UNKNOWN

    period = parse_period(claim.get("period")) if claim.get("period") else parse_period(None)
    value_raw = claim.get("value_raw")

    return Comparable(
        ref=claim["id"],
        entity=str(claim["subject"]).strip().lower(),
        metric=str(claim["metric"]).strip().lower(),
        unit=unit,
        period=period,
        value_canonical=claim.get("canonical"),
        precision=written_precision(value_raw, unit.scale) if value_raw else None,
        value_text=claim.get("value_text"),
        qualifiers=claim.get("qualifiers") or {},
        unknown_qualifiers=claim.get("unknown_qualifiers") or [],
        modality=claim.get("modality", "actual"),
        claim_type=claim.get("claim_type", "measurement"),
        document=claim.get("doc"),
        page_no=claim.get("page"),
    )


def _scale_factor(name: str | None) -> float:
    from .units import SCALES

    return SCALES.get(str(name).lower(), 1.0) if name else 1.0


@dataclass
class ScoredRelation:
    id: str
    expected: str
    actual: str
    expected_axis: str | None
    actual_axis: str | None
    explanation: str
    deferred: bool = False

    @property
    def verdict_ok(self) -> bool:
        return self.expected == self.actual

    @property
    def axis_ok(self) -> bool:
        # An axis is only required where the label names one.
        return self.expected_axis is None or self.expected_axis == self.actual_axis

    @property
    def ok(self) -> bool:
        return self.verdict_ok and self.axis_ok


@dataclass
class Score:
    results: list[ScoredRelation] = field(default_factory=list)

    @property
    def scored(self) -> list[ScoredRelation]:
        return [r for r in self.results if not r.deferred]

    @property
    def deferred(self) -> list[ScoredRelation]:
        return [r for r in self.results if r.deferred]

    @property
    def correct(self) -> int:
        return sum(1 for r in self.scored if r.ok)

    @property
    def accuracy(self) -> float:
        return self.correct / len(self.scored) if self.scored else 0.0


def score(gold: GoldSet | None = None) -> Score:
    """Run the right engine over every labelled pair and compare with the label.

    Two engines, routed by what the claims carry rather than by what the label
    expects — scoring against the answer would make the number meaningless. A
    pair of state claims with any interval or assertion date goes to the
    interval engine; everything else goes to the comparability gate.
    """
    from .compare import compare
    from .temporal import infer_cardinality, observe_cardinality, relate_holdings

    gold = gold or load()
    by_id = gold.by_id
    result = Score()

    # Cardinality is a property of a slot, inferred from the predicate and then
    # corrected by what the documents show, so it is resolved once over the
    # whole set rather than per pair.
    slots: dict[tuple[str, str], list] = {}
    for claim in gold.claims:
        if is_temporal_claim(claim):
            holding = to_holding(claim)
            slots.setdefault(
                (holding.scope.lower(), holding.predicate.lower()), []
            ).append(holding)

    for relation in gold.relations:
        claim_a, claim_b = by_id[relation["a"]], by_id[relation["b"]]

        if is_temporal_claim(claim_a) and is_temporal_claim(claim_b):
            a, b = to_holding(claim_a), to_holding(claim_b)
            key = (a.scope.lower(), a.predicate.lower())
            seeded = infer_cardinality(a.predicate)
            cardinality, note = observe_cardinality(slots.get(key, []), seeded)
            temporal = relate_holdings(a, b, cardinality, note)
            actual = temporal.verdict if temporal else "NO_RELATION"
            actual_axis = temporal.axis if temporal else None
            explanation = temporal.explanation if temporal else (
                "cardinality allows both to hold at once; no relation emitted")
        else:
            verdict = compare(to_comparable(claim_a), to_comparable(claim_b))
            actual, actual_axis = verdict.verdict, verdict.axis
            explanation = verdict.explanation

        result.results.append(
            ScoredRelation(
                id=relation["id"],
                expected=relation["verdict"],
                actual=actual,
                expected_axis=relation.get("axis"),
                actual_axis=actual_axis,
                explanation=explanation,
            )
        )
    return result


# --- routing to the interval engine -----------------------------------------


def _as_date(value):
    from datetime import date, datetime

    if value is None or isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def to_holding(claim: dict):
    """A state claim as an interval assertion.

    Two shapes reach here and the discriminator is ``org_scope``:

    - A *role*: subject is the person, org_scope is the company. The slot only
      one person can occupy is the company's, so scope is the company and the
      person is the filler.
    - An *attribute*: subject is the company and value_text is the value. The
      company's CIN is the slot; the identifier is what fills it.

    Getting this backwards blocks a succession into per-person groups, where
    each holder is only ever compared with themselves and nothing is found.
    """
    from .temporal import Holding

    org = claim.get("org_scope")
    if org:
        scope, filler = org, claim["subject"]
    else:
        scope, filler = claim["subject"], claim.get("value_text") or ""

    return Holding(
        ref=claim["id"],
        scope=scope,
        predicate=claim["metric"],
        filler=filler,
        valid_from=_as_date(claim.get("valid_from")),
        valid_to=_as_date(claim.get("valid_to")),
        valid_to_is_open=bool(claim.get("valid_to_is_open")),
        asserted=_as_date(claim.get("asserted")),
        document=claim.get("doc"),
        page_no=claim.get("page"),
    )


def is_temporal_claim(claim: dict) -> bool:
    """Whether a claim carries anything the interval engine can use.

    A registered office asserted with no dates has no temporal content and
    belongs to the ordinary gate, which compares its text. A CIN with an
    assertion date does — assertion time alone is enough to order two values.
    """
    return claim.get("claim_type") == "state" and any(
        claim.get(field)
        for field in ("valid_from", "valid_to", "valid_to_is_open", "asserted")
    )
