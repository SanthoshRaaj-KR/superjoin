"""L7 — the HTTP surface, and the shape the UI reads.

Six routes, and one of them is the point. `/compare` takes two claim ids and a
list of axes to hold out of the reasoning set, and returns a freshly computed
verdict. It is a POST with no side effects because the interesting thing about
it is that the answer is *computed*: mask `consolidation` and the standalone
against consolidated pair stops being explained and becomes a contradiction;
restore it and the contradiction dissolves again. A stored verdict cannot do
that, and no amount of UI can fake it convincingly.

**The translation layer is deliberate and lives here rather than in the gate.**
The UI speaks in `scope` and `basis`; the corpus speaks in `consolidation` and
`modality`. Those are the same axes under different names, and the mapping is a
presentation concern — pushing the UI's vocabulary down into ``compare`` would
put a fixed axis list into the one place in this system that must not have one,
since the whole argument for an open qualifier dict is that the axes worth
distinguishing are not knowable in advance. New axes discovered on review —
`sign_convention`, `measure_basis`, `area` — flow through as themselves.

**Nothing here decides anything.** Every verdict comes from ``compare``, every
number from the store. This module reads, renames and serialises.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .compare import compare
from .db import init_db, session_scope
from .models import Claim, Document, Entity, Metric, Page, Quarantine, Relation
from .relate import to_comparable

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parents[2] / "PDF Fact Reconciliation System"

# The UI's axis vocabulary against the corpus's own. Only these two need
# translating; every other axis is passed through under the name the documents
# gave it, which is the point of the qualifier dict being open.
UI_AXIS = {"scope": "consolidation", "basis": "modality"}
CORPUS_AXIS = {v: k for k, v in UI_AXIS.items()}

# Unit tokens the UI knows how to format. Anything else is sent as its own
# dimension name and renders as a plain number with the unit beside it, which is
# honest — the alternative is pretending tonnes are rupees.
_UNIT_TOKENS = {
    ("currency", "INR", 1e6): "INR_M",
    ("currency", "INR", 1e7): "INR_CR",
    ("currency", "USD", 1e6): "USD_M",
    ("currency", "USD", 1e9): "USD_BN",
}


def unit_token(claim: Claim) -> str:
    if claim.claim_type == "state":
        return "TENURE"
    dimension = claim.unit_dimension
    if dimension == "percent":
        return "PCT"
    if dimension == "percentage_points":
        return "PP"
    if dimension in (None, ""):
        return "UNKNOWN"
    key = (dimension, claim.unit_currency, float(claim.unit_scale or 1.0))
    if key in _UNIT_TOKENS:
        return _UNIT_TOKENS[key]
    if dimension == "currency" and claim.unit_currency:
        return f"{claim.unit_currency}_RAW"
    return dimension.upper()


def confidence_of(claim: Claim) -> float:
    """The weakest stage, not an average.

    Confidence is stored decomposed precisely so it is never blended, and the
    UI has one slot for it. A mean would let a perfectly grounded claim with an
    unresolved metric look like a good claim; the minimum says what it is — this
    claim is only as trustworthy as the stage that went worst. The breakdown
    travels alongside so nothing is actually lost.
    """
    return min(
        claim.conf_extraction or 0.0,
        claim.conf_grounding or 0.0,
        claim.conf_normalization or 0.0,
        claim.conf_entity_match or 0.0,
    )


def _is_role(claim: Claim, organisation: str | None) -> bool:
    """A state claim whose subject is a person or body filling a slot.

    The discriminator is the *document's* organisation, not the claim's
    resolved entity. A role claim resolves its entity to the person — Bansal —
    so comparing the subject against that always matches, and every role in the
    corpus reads as an attribute of itself. This is the same test
    ``to_holding`` makes, and the two have to agree or the timeline shows
    nothing.
    """
    if claim.claim_type != "state":
        return False
    if not (claim.valid_from or claim.valid_to):
        return False
    org = claim.org_scope or organisation or claim.subject
    return (claim.subject or "").strip().lower() != org.strip().lower()


def fact_json(claim: Claim, *, entities: dict, metrics: dict,
              sections: dict, primary: dict | None = None) -> dict:
    entity = entities.get(claim.entity_id) or claim.subject
    metric = metrics.get(claim.metric_id) or claim.predicate
    qualifiers = claim.qualifiers or {}
    organisation = (primary or {}).get(claim.document_id)
    tenure = _is_role(claim, organisation)
    if tenure:
        entity = organisation or entity

    value = claim.value_num if claim.value_num is not None else 0
    unit = unit_token(claim)
    display = claim.value_raw or claim.value_text or ""

    fact = {
        "id": f"f-{claim.id}",
        "entity": entity,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": claim.period_label or (
            _interval_label(claim) if tenure else "—"),
        "scope": qualifiers.get("consolidation") or (
            claim.org_scope or "—"),
        "basis": claim.modality or "actual",
        "kind": "tenure" if tenure else "measure",
        "doc": f"D-{claim.document_id:02d}",
        "page": claim.page_no,
        "confidence": round(confidence_of(claim), 2),
        "confidenceParts": {
            "extraction": round(claim.conf_extraction or 0.0, 2),
            "grounding": round(claim.conf_grounding or 0.0, 2),
            "normalization": round(claim.conf_normalization or 0.0, 2),
            "entity_match": round(claim.conf_entity_match or 0.0, 2),
        },
        # The figure as the page printed it, with its unit — "(224.10)" and
        # not "-224.1". Formatting a float client-side loses the document's own
        # convention, and these documents are consistent about it: a negative
        # is parenthesised. Showing a third notation invented in the interface
        # makes the number harder to find on the source page, which is the one
        # thing a reader checking evidence needs to do.
        "display": _display(claim, display, unit, tenure),
        "quote": claim.evidence_quote or "",
        "claim": _one_liner(claim, entity, metric, display, tenure),
        "section": sections.get((claim.document_id, claim.page_no), ""),
        "qualifiers": qualifiers,
        "unknownQualifiers": claim.unknown_qualifiers or [],
        "noEvidence": not (claim.evidence_quote or "").strip(),
    }
    if tenure:
        fact.update({
            "role": metric,
            "holder": claim.subject,
            "start": claim.valid_from.isoformat() if claim.valid_from else None,
            "end": (claim.valid_to.isoformat()
                    if claim.valid_to and not claim.valid_to_is_open else None),
        })
    return fact



_UNIT_SUFFIX = {
    "INR_M": ("\u20b9", "M"), "INR_CR": ("\u20b9", " Cr"),
    "USD_M": ("$", "M"), "USD_BN": ("$", "Bn"),
    "PCT": ("", "%"), "PP": ("", " pp"),
}


def _display(claim: Claim, printed: str, unit: str, tenure: bool) -> str:
    """The figure as it appears on the page, wearing its unit.

    ``value_raw`` is the document's own notation, so the sign convention comes
    for free: a negative arrives already parenthesised. Two things still have
    to be handled, and both were wrong on the first attempt.

    The parentheses belong *outside* the unit. "\u20b9(224.10)M" is not a notation
    anyone uses; "(\u20b9224.10M)" is.

    And the unit is often already there. Percentages are written "(6.3%)" on
    the page, and appending the sign again gives "(6.3%)%".
    """
    printed = (printed or "").strip()
    if tenure or claim.claim_type == "state" or not printed:
        return printed

    # Financial tables write nil as a dash. Dressing that in a currency and a
    # scale gives "₹-M", which reads as a number that is missing rather than a
    # figure that is zero.
    if printed.strip("-–— ") == "":
        return "—"

    negative = printed.startswith("(") and printed.endswith(")")
    core = printed[1:-1].strip() if negative else printed

    if unit in _UNIT_SUFFIX:
        prefix, suffix = _UNIT_SUFFIX[unit]
        # The page often wrote the unit itself — "8,142 Cr", "(6.3%)" — and
        # adding it again gives "₹8,142 Cr inr cr". Which branch to take is
        # decided by whether the unit is one we know how to write, not by
        # whether anything survived the stripping.
        if suffix and core.lower().rstrip().endswith(suffix.strip().lower()):
            suffix = ""
        if prefix and core.startswith(prefix):
            prefix = ""
        body = f"{prefix}{core}{suffix}"
    else:
        label = unit.replace("_", " ").lower()
        known = label not in ("unknown", "tenure")
        # "7.1 million shipments per day" already says its unit; the label
        # is "days", so the check has to reach the singular stem too.
        stem = label.split()[0].rstrip("s") if label else ""
        already = bool(stem) and stem in core.lower()
        body = f"{core} {label}" if known and label and not already else core
    return f"({body})" if negative else body

def _interval_label(claim: Claim) -> str:
    start = claim.valid_from.isoformat() if claim.valid_from else "?"
    end = ("present" if claim.valid_to_is_open or not claim.valid_to
           else claim.valid_to.isoformat())
    return f"{start} → {end}"


def _one_liner(claim, entity, metric, display, tenure) -> str:
    if tenure:
        return f"{metric} = {claim.subject} · {_interval_label(claim)}"
    parts = [f"{metric} = {display}"]
    if claim.period_label:
        parts.append(claim.period_label)
    for key, value in (claim.qualifiers or {}).items():
        parts.append(f"{key}={value}")
    return " · ".join(parts)


def _title(document: Document) -> str:
    """A readable name. `doc_type` is a slug for routing, not a title."""
    stem = Path(document.filename).stem
    stem = stem.split("-", 1)[-1] if stem[:2].isdigit() else stem
    words = stem.replace("-", " ").replace("_", " ").split()
    # Title-case the words, but a token carrying digits is an identifier —
    # FY24, Q4, 2024-25 — and "Fy24" is just wrong.
    return " ".join(w.upper() if any(c.isdigit() for c in w) else w.capitalize()
                    for w in words)


def _sections(session) -> dict:
    """The deepest heading in force on each page, for the evidence panel.

    Frames carry a ``heading_path`` — the chain of headings a region sits
    under — so the last element is the most specific thing a reader can be
    told about where a quote came from.
    """
    out: dict = {}
    for page in session.scalars(select(Page)):
        best: list = []
        for frame in (page.context_json or []):
            path = frame.get("heading_path") or []
            if len(path) > len(best):
                best = path
        if best:
            out[(page.document_id, page.page_no)] = " › ".join(best[-2:])
    return out


# --- the pairs the UI opens with ---------------------------------------------

# Ordered by how much a reader learns from opening it. A corroboration across
# two documents in two different units is the first thing worth seeing; a pair
# of identical rows from one table is the last.
PAIR_INTEREST = {
    "CONTRADICTS": 0,
    "INSUFFICIENT_EVIDENCE": 1,
    "CONTEXTUAL": 2,
    "SUCCESSION_WITH_VACANCY": 3,
    "SUCCESSION": 4,
    "CLOSES_INTERVAL": 5,
    "CONTINUES": 6,
    "CORROBORATES": 7,
    "CONTEXTUAL_TEMPORAL": 8,
}
MAX_PAIRS = 60


def latest_generation(session) -> int:
    return session.scalar(
        select(func.max(Relation.generation))) or 0


def _spread(rows: list, facts: dict, per_metric: int = 3) -> list:
    """Keep one metric from filling the list.

    Tax expense appears on both the annual report and the deck at four period
    granularities, which is eight legitimate INSUFFICIENT_EVIDENCE pairs about
    one thing. Sorted purely by verdict they crowd out every other kind of
    finding in the corpus, and a reader scrolling the list concludes the system
    only knows about tax. Order is already meaningful, so this thins each metric
    to its most interesting few and leaves the ranking otherwise intact.
    """
    seen: Counter = Counter()
    kept, spare = [], []
    for row in rows:
        fact = facts.get(f"f-{row.claim_a_id}")
        key = (fact or {}).get("metric", row.claim_a_id)
        if seen[key] < per_metric:
            seen[key] += 1
            kept.append(row)
        else:
            spare.append(row)
    return kept + spare


def _pair_label(relation: Relation, facts: dict) -> str:
    a = facts.get(f"f-{relation.claim_a_id}")
    b = facts.get(f"f-{relation.claim_b_id}")
    if not a or not b:
        return f"claims {relation.claim_a_id} / {relation.claim_b_id}"
    where = (f"{a['doc']} vs {b['doc']}" if a["doc"] != b["doc"]
             else f"{a['doc']} p.{a['page']} vs p.{b['page']}")

    # Both sides, not just the first. One document can record a role with dates
    # and another record the same role without any, so a pair is routinely half
    # tenure and half plain state — and reading the second side's holder off a
    # fact that has none is a 500 on the whole listing.
    if a["kind"] == "tenure" and b["kind"] == "tenure":
        return f"{a['role']} · {a['holder']} → {b['holder']}"
    if a["kind"] == "tenure":
        return f"{a['role']} · {a['holder']} · {where}"
    return f"{a['metric']} {a['period']} · {where}"


# --- request models ----------------------------------------------------------


def _stored_recovery(session, a_id: int, b_id: int) -> dict | None:
    """Context a review recovered for this pair, if there was one."""
    generation = latest_generation(session)
    row = session.scalar(
        select(Relation).where(
            Relation.generation == generation,
            Relation.reconsidered.is_(True),
            Relation.claim_a_id.in_([a_id, b_id]),
            Relation.claim_b_id.in_([a_id, b_id]),
        )
    )
    if row is None:
        return None
    flipped = row.claim_a_id != a_id
    return {
        "axis": row.recovery_axis,
        "a": row.recovery_b_value if flipped else row.recovery_a_value,
        "b": row.recovery_a_value if flipped else row.recovery_b_value,
        "method": row.recovery_method,
        "reason": row.recovery_reason,
        "confidence": row.recovery_confidence,
        "originalVerdict": row.original_verdict,
    }


def _with_recovery(a, b, recovery: dict):
    """Put a recovered axis back onto two claims for one comparison.

    A side the review could not label gets the axis in
    ``unknown_qualifiers`` rather than nothing, which is what keeps a one-sided
    recovery blocking instead of explaining — the same rule the review itself
    applies.
    """
    from dataclasses import replace

    axis = recovery.get("axis")
    if not axis:
        return a, b
    out = []
    for claim, value in ((a, recovery.get("a")), (b, recovery.get("b"))):
        qualifiers = dict(claim.qualifiers or {})
        unknown = list(claim.unknown_qualifiers or [])
        if value:
            qualifiers[axis] = value
        elif axis not in unknown:
            unknown.append(axis)
        out.append(replace(claim, qualifiers=qualifiers,
                           unknown_qualifiers=unknown))
    return tuple(out)


class CompareRequest(BaseModel):
    a: str
    b: str
    maskedAxes: list[str] = Field(default_factory=list)


# --- the app -----------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="Fact Knowledge Layer",
        description=__doc__,
        version="1.0",
    )
    # The UI is served from this same app, so this is only for running the two
    # separately during development.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"],
    )
    init_db()

    def facts_index(session) -> dict:
        entities = {e.id: e.canonical_name for e in session.scalars(select(Entity))}
        metrics = {m.id: m.canonical_name for m in session.scalars(select(Metric))}
        primary = {d.id: d.primary_entity for d in session.scalars(select(Document))}
        sections = _sections(session)
        return {
            f"f-{c.id}": fact_json(c, entities=entities, metrics=metrics,
                                   sections=sections, primary=primary)
            for c in session.scalars(select(Claim))
        }

    @app.get("/api/v1/documents")
    def documents():
        with session_scope() as session:
            counts = dict(session.execute(
                select(Claim.document_id, func.count(Claim.id))
                .group_by(Claim.document_id)).all())
            quarantined = dict(session.execute(
                select(Quarantine.document_id, func.count(Quarantine.id))
                .group_by(Quarantine.document_id)).all())
            extracted = dict(session.execute(
                select(Claim.document_id, func.count(func.distinct(Claim.page_no)))
                .group_by(Claim.document_id)).all())
            out = []
            for d in session.scalars(select(Document).order_by(Document.id)):
                claims = counts.get(d.id, 0)
                pages_done = extracted.get(d.id, 0)
                out.append({
                    "id": f"D-{d.id:02d}",
                    "title": _title(d),
                    "org": d.primary_entity or d.publisher or "—",
                    "pages": d.n_pages,
                    # Extraction is page-scoped and run on a selection, so a
                    # document is "indexed" for the pages it has claims for.
                    # Reporting it as fully indexed would overstate the corpus.
                    "status": "indexed" if claims else "queued",
                    "pagesExtracted": pages_done,
                    "claims": claims,
                    "grounded": claims,  # ungrounded claims never reach the table
                    "quarantined": quarantined.get(d.id, 0),
                    "uploaded": d.created_at.date().isoformat()
                    if getattr(d, "created_at", None) else "",
                    "bytes": d.n_chars,
                    "filename": d.filename,
                    "asOf": d.as_of_date.isoformat() if d.as_of_date else None,
                })
            return out

    @app.get("/api/v1/facts")
    def facts():
        with session_scope() as session:
            return list(facts_index(session).values())

    @app.get("/api/v1/pairs")
    def pairs():
        with session_scope() as session:
            index = facts_index(session)
            generation = latest_generation(session)
            rows = session.scalars(
                select(Relation).where(Relation.generation == generation)
            ).all()
            rows = [
                r for r in rows
                if f"f-{r.claim_a_id}" in index and f"f-{r.claim_b_id}" in index
            ]
            rows.sort(key=lambda r: (
                # A contradiction that was withdrawn on review comes first:
                # it is the only pair that shows the system correcting itself.
                0 if r.reconsidered else 1,
                PAIR_INTEREST.get(r.verdict, 9),
                0 if r.cross_document else 1,
                -(r.value_relative or 0),
            ))
            rows = _spread(rows, index)
            return [
                {
                    "id": f"r-{r.id}",
                    "a": f"f-{r.claim_a_id}",
                    "b": f"f-{r.claim_b_id}",
                    "label": _pair_label(r, index),
                    "verdict": r.verdict,
                    "axis": r.axis,
                    "crossDocument": bool(r.cross_document),
                    "reconsidered": bool(r.reconsidered),
                    "originalVerdict": r.original_verdict,
                    "recoveryAxis": r.recovery_axis,
                    "recoveryReason": r.recovery_reason,
                }
                for r in rows[:MAX_PAIRS]
            ]

    @app.get("/api/v1/quarantine")
    def quarantine():
        with session_scope() as session:
            return [
                {
                    "id": f"q-{q.id}",
                    "doc": f"D-{q.document_id:02d}",
                    "page": q.page_no,
                    "reason": q.reason_code,
                    "claim": (q.payload or {}).get("predicate", "")
                    or (q.detail or "")[:80],
                    "note": q.detail or "",
                    "stage": q.stage,
                    "confidence": float((q.payload or {}).get(
                        "confidence_extraction", 0.0) or 0.0),
                }
                for q in session.scalars(select(Quarantine))
            ]

    @app.get("/api/v1/corpus")
    def corpus():
        with session_scope() as session:
            generation = latest_generation(session)
            relations = session.scalars(
                select(Relation).where(Relation.generation == generation)).all()
            verdicts = Counter(r.verdict for r in relations)
            claims = session.scalar(select(func.count(Claim.id))) or 0
            quarantined = session.scalar(select(func.count(Quarantine.id))) or 0
            docs = session.scalars(select(Document)).all()

            bands = Counter()
            for c in session.scalars(select(Claim)):
                score = confidence_of(c)
                bands["0.90 – 1.00" if score >= 0.90 else
                      "0.75 – 0.90" if score >= 0.75 else
                      "0.60 – 0.75" if score >= 0.60 else "< 0.60"] += 1

            axis_counts = Counter(r.axis for r in relations if r.axis)
            resolves = Counter(
                r.axis for r in relations
                if r.axis and r.values_differ
                and r.verdict not in ("CONTRADICTS", "INSUFFICIENT_EVIDENCE")
            )
            values: dict[str, set] = {}
            for c in session.scalars(select(Claim)):
                for key, value in (c.qualifiers or {}).items():
                    values.setdefault(key, set()).add(str(value))

            differing = sum(1 for r in relations if r.values_differ)
            return {
                "documents": len(docs),
                "pages": sum(d.n_pages for d in docs),
                "pagesExtracted": session.scalar(
                    select(func.count(func.distinct(
                        Claim.document_id * 100000 + Claim.page_no)))) or 0,
                "claims": claims,
                # Every stored claim is grounded — an ungrounded one never
                # reaches the table, it goes to quarantine with a reason code.
                # So the honest denominator for a grounding *rate* is what the
                # model proposed, not what survived; dividing the survivors by
                # themselves reports 100% and measures nothing.
                "grounded": claims,
                "proposed": claims + quarantined,
                "quarantined": quarantined,
                "relationships": len(relations),
                "raw": differing,
                "contextual": verdicts["CONTEXTUAL"] + verdicts["CONTEXTUAL_TEMPORAL"],
                "contradictions": verdicts["CONTRADICTS"],
                "insufficient": verdicts["INSUFFICIENT_EVIDENCE"],
                "corroborates": verdicts["CORROBORATES"],
                "withdrawn": sum(1 for r in relations if r.reconsidered),
                "recoveredAxes": dict(Counter(
                    r.recovery_axis for r in relations if r.recovery_axis)),
                "confidence": [
                    {"band": b, "count": bands.get(b, 0)}
                    for b in ("0.90 – 1.00", "0.75 – 0.90", "0.60 – 0.75", "< 0.60")
                ],
                "axes": [
                    {
                        "axis": CORPUS_AXIS.get(axis, axis),
                        "values": sorted(values.get(axis, []))[:6],
                        "occurrences": count,
                        "resolves": resolves.get(axis, 0),
                    }
                    for axis, count in axis_counts.most_common()
                ],
            }

    @app.post("/api/v1/compare")
    def compare_pair(request: CompareRequest):
        with session_scope() as session:
            entities = {e.id: e.canonical_name for e in session.scalars(select(Entity))}
            metrics = {m.id: m.canonical_name for m in session.scalars(select(Metric))}

            def load(ref: str) -> Claim:
                try:
                    claim = session.get(Claim, int(ref.removeprefix("f-")))
                except ValueError:
                    claim = None
                if claim is None:
                    raise HTTPException(404, f"no claim {ref}")
                return claim

            left, right = load(request.a), load(request.b)
            a = to_comparable(left, metrics.get(left.metric_id),
                              entities.get(left.entity_id))
            b = to_comparable(right, metrics.get(right.metric_id),
                              entities.get(right.entity_id))

            # Context recovered when this pair was reviewed is context the
            # system has. Comparing without it would make this endpoint
            # disagree with the verdict the corpus run stored for the same two
            # claims — the pair list would say CONTEXTUAL and the panel it
            # opens would say CONTRADICTS.
            recovery = _stored_recovery(session, left.id, right.id)
            if recovery:
                a, b = _with_recovery(a, b, recovery)

            masked = [UI_AXIS.get(x, x) for x in request.maskedAxes]
            verdict = compare(a, b, mask_axes=masked)
            payload = verdict_json(verdict, left, right, a, b,
                                   request.maskedAxes, entities, metrics)
            payload["recovered"] = recovery
            return payload

    @app.get("/api/v1/as-of")
    def as_of_route(date: str):
        from datetime import date as _date

        from .relate import as_of

        with session_scope() as session:
            try:
                on = _date.fromisoformat(date)
            except ValueError:
                raise HTTPException(400, "date must be ISO, e.g. 2024-04-15")
            return [
                {
                    "ref": f"f-{h.ref}",
                    "scope": h.scope,
                    "role": h.predicate,
                    "holder": h.filler,
                    "start": h.valid_from.isoformat() if h.valid_from else None,
                    "end": h.valid_to.isoformat() if h.valid_to else None,
                }
                for h in as_of(session, on)
            ]

    if FRONTEND.is_dir():
        @app.middleware("http")
        async def revalidate_ui(request, call_next):
            """Make the browser check before reusing the interface.

            An ES module is cached hard and for a long time, and the cache is
            keyed on the URL, so editing `api.js` and reloading gets you the
            old module — silently, with the API returning new data into old
            code. `no-cache` does not mean "do not store", it means "ask
            first": the browser still gets a 304 and the file still comes from
            disk when nothing changed. The cost is one conditional request per
            asset; the alternative is debugging a page that is running code you
            have already deleted.
            """
            response = await call_next(request)
            path = request.url.path
            if path == "/" or path.endswith((".js", ".css", ".html")):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        @app.get("/")
        def index():
            return FileResponse(FRONTEND / "Reconcile.dc.html")

        app.mount("/", StaticFiles(directory=FRONTEND), name="ui")
    else:  # pragma: no cover - only when the UI is not checked out
        log.warning("frontend not found at %s; serving the API only", FRONTEND)

    return app


# --- the verdict, in the shape the comparison view reads ---------------------

_UI_AXIS_LABEL = {
    "entity": "Entity", "metric": "Metric", "period": "Period",
    "unit": "Unit", "scope": "Consolidation scope", "basis": "Reporting basis",
}


def verdict_json(verdict, left: Claim, right: Claim, a, b,
                 masked_ui: list[str], entities: dict, metrics: dict) -> dict:
    """Render a verdict as the comparison view expects it.

    The axis table is built from the two claims rather than from the verdict,
    because the view shows every axis and marks each one — a verdict names only
    the one that decided it. Axes discovered on review are appended, so a
    `sign_convention` or an `area` recovered from a page appears in the table
    beside the fixed six rather than being invisible for not having been
    anticipated.
    """
    masked = set(masked_ui)
    rows = []

    def add(key: str, label: str, av, bv, *, status: str | None = None):
        if status is None:
            status = "match" if str(av or "") == str(bv or "") else "differs"
        if key in masked:
            status = "masked"
        rows.append({"key": key, "label": label, "status": status,
                     "a": av, "b": bv})

    add("entity", "Entity", a.entity, b.entity)
    add("metric", "Metric", a.metric, b.metric)
    add("period", "Period",
        a.period.label if a.period.known else (
            _interval_label(left) if left.claim_type == "state" else "—"),
        b.period.label if b.period.known else (
            _interval_label(right) if right.claim_type == "state" else "—"))

    unit_a, unit_b = unit_token(left), unit_token(right)
    unit_status = (
        "match" if unit_a == unit_b
        else "normalized" if (a.unit.dimension and
                              a.unit.dimension == b.unit.dimension and
                              a.unit.currency == b.unit.currency)
        else "differs"
    )
    add("unit", "Unit", unit_a, unit_b, status=unit_status)

    # Qualifiers come from the comparables rather than the stored rows, because
    # context recovered on review lives on the comparable. Reading the ORM
    # objects here is what made a recovered axis invisible in the table it is
    # the whole explanation for.
    qa, qb = a.qualifiers or {}, b.qualifiers or {}
    add("scope", "Consolidation scope",
        qa.get("consolidation") or "not stated",
        qb.get("consolidation") or "not stated")
    add("basis", "Reporting basis", left.modality or "—", right.modality or "—")

    # Axes the documents themselves introduced, including any recovered on a
    # second look. A fixed table of six would hide exactly the discoveries this
    # project treats as the interesting part.
    extra = (set(qa) | set(qb)) - {"consolidation"}
    for axis in sorted(extra):
        add(axis, axis.replace("_", " ").capitalize(),
            qa.get(axis) or "not stated", qb.get(axis) or "not stated")

    primary = verdict.axis
    primary_ui = CORPUS_AXIS.get(primary, primary)
    differing = next(
        (r for r in rows if r["key"] == primary_ui and r["status"] == "differs"),
        None,
    ) or next((r for r in rows if r["status"] == "differs"), None)

    unresolved = sorted(
        set(a.unknown_qualifiers or []) | set(b.unknown_qualifiers or [])
    )
    return {
        "verdict": verdict.verdict,
        "reason": verdict.explanation,
        "axes": rows,
        "differingAxis": differing,
        "normalized": {
            "a": _normalized(a), "b": _normalized(b),
        },
        "missingAxes": [CORPUS_AXIS.get(x, x) for x in verdict.missing_axes],
        "unknownQualifiers": unresolved,
        "notes": verdict.notes,
        "vacancyDays": 0,
    }


def _normalized(comparable) -> dict | None:
    """The value in base units, labelled by what those units are.

    ``Unit.describe`` names the unit *as the document wrote it* — "INR million"
    — which is the wrong label for a canonical value, because canonicalising
    is precisely what removed the scale. Reporting 2,241,000,000 as "INR
    million" is off by a factor of a million, and the fallback for a scaleless
    currency read as "INR units", which is not a unit at all.
    """
    if comparable.value_canonical is None:
        return None
    unit = comparable.unit
    if not unit.known:
        label = "—"
    elif unit.dimension == "currency":
        label = unit.currency or "currency"
    elif unit.dimension == "percent":
        label = "percent"
    else:
        label = unit.dimension
    return {
        "dim": unit.dimension or "?",
        "value": comparable.value_canonical,
        "unit": label,
    }


api = create_app()
