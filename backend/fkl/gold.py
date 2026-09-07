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
