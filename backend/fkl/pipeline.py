"""Running extraction across a document and writing the results down.

Pages are independent, so they run concurrently. The interesting part is what
happens to a page that fails: the exception is caught, the page is recorded in
``quarantine`` with a reason code, and the run continues. A pipeline that aborts
on page 47 of 100 tells you nothing about the other 53, and a pipeline that
swallows the failure silently is worse — it reports a clean run over a corpus it
never finished reading.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.claims import extract_page
from .llm.profile import parse_iso_date
from .models import Claim, Document, Page, Quarantine
from .schemas import MeasurementClaim, PageExtraction, StateClaim

log = logging.getLogger(__name__)

DEFAULT_WORKERS = 6


@dataclass
class ExtractionRun:
    document_id: int
    pages_attempted: int = 0
    pages_failed: int = 0
    measurements: int = 0
    states: int = 0
    notes: list[tuple[int, str]] = field(default_factory=list)

    @property
    def claims(self) -> int:
        return self.measurements + self.states


def _to_row(
    claim: MeasurementClaim | StateClaim,
    document: Document,
    page_no: int,
) -> Claim:
    row = Claim(
        document_id=document.id,
        page_no=page_no,
        claim_type="measurement" if isinstance(claim, MeasurementClaim) else "state",
        subject=claim.subject,
        predicate=claim.predicate,
        org_scope=claim.org_scope,
        qualifiers=claim.qualifiers,
        unknown_qualifiers=claim.unknown_qualifiers,
        modality=claim.modality,
        evidence_quote=claim.evidence_quote,
        # Assertion time is inherited from the document, never read off the page.
        # A page can say "March 31, 2024" while the document asserting it speaks
        # from August 2024, and conflating the two collapses the temporal model.
        assertion_time=document.as_of_date,
        conf_extraction=claim.confidence_extraction,
        confidence_reasons=list(claim.confidence_reasons),
    )
    if isinstance(claim, MeasurementClaim):
        row.value_raw = claim.value_raw
        row.value_num = claim.value_num
        row.unit_raw = claim.unit_raw
        row.period_raw = claim.period_raw
    else:
        row.value_text = claim.value_text
        row.valid_from = parse_iso_date(claim.valid_from)
        row.valid_to = parse_iso_date(claim.valid_to)
        row.valid_to_is_open = claim.valid_to_is_open
    return row


def extract_document_claims(
    session: Session,
    document_id: int,
    *,
    pages: list[int] | None = None,
    workers: int = DEFAULT_WORKERS,
) -> ExtractionRun:
    """Extract claims for a document and persist them.

    ``pages`` restricts the run to specific page numbers, which keeps iteration
    on prompts cheap and makes a targeted re-run possible after a change.
    """
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError(f"no document with id {document_id}")

    stmt = select(Page).where(Page.document_id == document_id).order_by(Page.page_no)
    if pages is not None:
        stmt = stmt.where(Page.page_no.in_(pages))
    page_rows = session.scalars(stmt).all()

    profile = document.profile_json
    run = ExtractionRun(document_id=document_id, pages_attempted=len(page_rows))

    def work(page: Page) -> tuple[int, PageExtraction | None, str | None]:
        try:
            return page.page_no, extract_page(page.text, page.page_no, profile), None
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            log.warning("page %s failed: %s", page.page_no, exc)
            return page.page_no, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, page_rows))

    for page_no, extraction, error in results:
        if error is not None:
            run.pages_failed += 1
            session.add(
                Quarantine(
                    document_id=document_id,
                    page_no=page_no,
                    stage="extraction",
                    reason_code="extraction_call_failed",
                    detail=error,
                )
            )
            continue

        assert extraction is not None
        if extraction.page_notes:
            run.notes.append((page_no, extraction.page_notes))

        for claim in extraction.measurements:
            session.add(_to_row(claim, document, page_no))
            run.measurements += 1
        for claim in extraction.states:
            session.add(_to_row(claim, document, page_no))
            run.states += 1

    session.flush()
    return run
