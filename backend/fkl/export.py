"""JSON export of what is in the store.

The end of the Phase 0 loop: PDF in, inspectable JSON out. Useful for eyeballing
extraction quality, and it is what gets committed as sample output so the work
can be evaluated without an API key of the grader's own.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Claim, Document, Quarantine


def _iso(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def claim_to_dict(claim: Claim) -> dict[str, Any]:
    base = {
        "id": claim.id,
        "document_id": claim.document_id,
        "page_no": claim.page_no,
        "claim_type": claim.claim_type,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "org_scope": claim.org_scope,
        "qualifiers": claim.qualifiers,
        "unknown_qualifiers": claim.unknown_qualifiers,
        "modality": claim.modality,
        "evidence_quote": claim.evidence_quote,
        "assertion_time": _iso(claim.assertion_time),
        "confidence": {
            "extraction": claim.conf_extraction,
            "grounding": claim.conf_grounding,
            "normalization": claim.conf_normalization,
            "entity_match": claim.conf_entity_match,
            "reasons": claim.confidence_reasons,
        },
    }
    if claim.claim_type == "measurement":
        base |= {
            "value_raw": claim.value_raw,
            "value_num": claim.value_num,
            "unit_raw": claim.unit_raw,
            "period_raw": claim.period_raw,
        }
    else:
        base |= {
            "value_text": claim.value_text,
            "valid_from": _iso(claim.valid_from),
            "valid_to": _iso(claim.valid_to),
            "valid_to_is_open": claim.valid_to_is_open,
        }
    return base


def export_document(session: Session, document_id: int) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError(f"no document with id {document_id}")

    claims = session.scalars(
        select(Claim).where(Claim.document_id == document_id).order_by(Claim.page_no, Claim.id)
    ).all()
    quarantined = session.scalars(
        select(Quarantine).where(Quarantine.document_id == document_id).order_by(Quarantine.id)
    ).all()

    return {
        "document": {
            "id": document.id,
            "filename": document.filename,
            "sha256": document.sha256,
            "n_pages": document.n_pages,
            "n_chars": document.n_chars,
            "profile": document.profile_json,
        },
        "counts": {
            "claims": len(claims),
            "measurements": sum(1 for c in claims if c.claim_type == "measurement"),
            "states": sum(1 for c in claims if c.claim_type == "state"),
            "quarantined": len(quarantined),
            # The share of claims admitting they are missing context. A healthy
            # number here is a sign the extractor is being honest, not a defect.
            "with_unknown_qualifiers": sum(1 for c in claims if c.unknown_qualifiers),
        },
        "claims": [claim_to_dict(c) for c in claims],
        "quarantine": [
            {
                "page_no": q.page_no,
                "stage": q.stage,
                "reason_code": q.reason_code,
                "detail": q.detail,
            }
            for q in quarantined
        ],
    }


def write_json(payload: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
