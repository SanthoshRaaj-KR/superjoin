"""L4 — putting a grounded claim into comparable form.

This is the join between the four normalisers and the claims table. It runs
after grounding and before the insert, so a stored claim already carries its
canonical value, its interval, its entity and its metric, and the comparability
gate above never has to re-derive any of them.

**Raw strings are kept beside the canonical form, never replaced by it.**
``value_raw`` is what the page says; ``value_canonical`` is what it means. A
comparison is made in base units and then explained back to the reader in the
document's own — "₹8,142 Cr and ₹81,415.38 Mn agree to within 0.006%" is a
sentence that needs both halves.

**Normalisation never fails a claim.** A unit that cannot be parsed or a metric
that cannot be resolved lowers ``conf_normalization`` and leaves the canonical
field null; it does not quarantine the claim. The distinction matters: grounding
asks "did this claim come from the page", which is a question about honesty, and
a no there means the claim should not exist. Normalisation asks "can this claim
be compared", and a no there is a perfectly good claim that simply will not join
a comparison — which the gate above already handles as INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .entities import find_identifiers, resolve_entity
from .metrics import resolve_metric
from .models import Claim
from .periods import parse_period
from .units import parse_unit

log = logging.getLogger(__name__)


def context_hint(context_json: list | None) -> str:
    """The page's own declarations, as a hint for period resolution.

    Only one decision needs this: whether ``March 31, 2024`` names the year that
    ended then or the instant itself. The section declaration is the only thing
    on the page that answers it, and the context layer already captured the
    wording it was read from — "To the Consolidated Financial Statements for the
    year ended March 31, 2024" — so it is reused here rather than re-detected.

    Page-scoped rather than claim-scoped, deliberately. A declaration governs its
    section, and attributing it to individual rows would need the row's heading
    path, which is more machinery than one boolean is worth.
    """
    if not context_json:
        return ""
    parts: list[str] = []
    for frame in context_json:
        parts.extend(frame.get("evidence") or [])
    return " ".join(parts)


def canonicalize(
    session: Session,
    claim: Claim,
    *,
    hint: str = "",
    adjudicate: bool = True,
) -> None:
    """Fill a claim's canonical fields in place. Never raises for bad input."""
    # --- unit, and the value it places -------------------------------------
    unit = parse_unit(claim.unit_raw, claim.qualifiers)
    claim.unit_dimension = unit.dimension if unit.known else None
    claim.unit_currency = unit.currency
    claim.unit_scale = unit.scale if unit.known else None
    claim.value_canonical = unit.canonical(claim.value_num)

    # --- period -------------------------------------------------------------
    period = parse_period(claim.period_raw, hint)
    if period.known:
        claim.period_start = period.start
        claim.period_end = period.end
        claim.period_label = period.label
        claim.period_granularity = period.granularity
        claim.period_ambiguous = period.ambiguous

    reasons = list(claim.confidence_reasons or [])
    reasons.extend(unit.notes)
    reasons.extend(period.notes)

    # A state claim has no unit, so demanding one would report every role and
    # address as unnormalised. Its period confidence stands alone.
    if claim.claim_type == "measurement":
        claim.conf_normalization = min(unit.confidence, period.confidence)
    else:
        claim.conf_normalization = period.confidence if claim.period_raw else 1.0

    # --- entity -------------------------------------------------------------
    entity_confidence = 0.0
    try:
        # Identifiers are looked for in the evidence quote rather than the
        # subject: a CIN or DIN sits beside the name in the text, never inside
        # it, and that is precisely the string that survives a rename.
        resolution = resolve_entity(
            session,
            claim.subject,
            entity_type="organisation" if claim.claim_type == "measurement" else "person",
            identifiers=find_identifiers(claim.evidence_quote or ""),
        )
        claim.entity_id = resolution.entity_id
        entity_confidence = resolution.confidence
        if resolution.method == "identifier":
            reasons.append(f"entity matched by registration number to "
                           f"{resolution.canonical_name!r}")
    except ValueError as exc:  # an empty subject
        reasons.append(f"entity not resolved: {exc}")

    # --- metric -------------------------------------------------------------
    metric_confidence = 0.0
    try:
        resolution = resolve_metric(
            session,
            claim.predicate,
            dimension=claim.unit_dimension,
            adjudicate=adjudicate,
        )
        claim.metric_id = resolution.metric_id
        metric_confidence = resolution.confidence
        if resolution.method in {"adjudicated", "embedding"}:
            reasons.append(
                f"metric linked to {resolution.canonical_name!r} "
                f"({resolution.method}, similarity {resolution.similarity:.2f})"
            )
    except ValueError as exc:  # an empty predicate
        reasons.append(f"metric not resolved: {exc}")
    except Exception as exc:  # noqa: BLE001 - a model call can fail; the claim stands
        log.warning("metric resolution failed for %r: %s", claim.predicate, exc)
        reasons.append(f"metric not resolved: {type(exc).__name__}")

    claim.conf_entity_match = min(entity_confidence, metric_confidence)
    claim.confidence_reasons = reasons
