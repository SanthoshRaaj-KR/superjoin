"""Running extraction across a document and writing the results down.

Two things happen here that are worth stating plainly.

**The extractor reads the rendered page, the validator checks the raw one.**
The model is shown reading order restored, table rows rebuilt and the context
in force stated inline, because that is what lets it read a financial table
correctly. Grounding then checks the quote against the text the PDF actually
contains. Validating against the same rendering the model was shown would make
the check circular: any layout mistake would be confirmed by the very artefact
that contains it.

**Nothing fails silently.** A page whose model call raises is quarantined with a
reason code and the run continues, because aborting on page 47 of 100 tells you
nothing about the other 53. A claim that cannot be grounded is quarantined too,
which is why grounding runs before the insert rather than after: the claims
table stays append-only and holds only claims that proved themselves.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .grounding import GroundingResult, validate
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
    proposed: int = 0
    refused: int = 0  # claims that failed grounding
    grounding_methods: dict[str, int] = field(default_factory=dict)
    # Every reason code written to the quarantine table, of both kinds: claims
    # refused by grounding, and pages whose model call raised. Kept together
    # because it mirrors what is actually in the table, and kept separate from
    # `refused` because a failed page is not a refused claim — conflating them
    # makes `proposed = kept + refused` stop adding up.
    quarantine_reasons: dict[str, int] = field(default_factory=dict)
    notes: list[tuple[int, str]] = field(default_factory=list)

    @property
    def claims(self) -> int:
        return self.measurements + self.states

    @property
    def grounding_precision(self) -> float:
        """Share of proposed claims that survived grounding.

        The headline extraction-quality number, and the one that moves when the
        prompt or the page rendering changes.
        """
        return self.claims / self.proposed if self.proposed else 0.0


def _claimed_value(claim: MeasurementClaim | StateClaim) -> str:
    return claim.value_raw if isinstance(claim, MeasurementClaim) else claim.value_text


def _to_row(
    claim: MeasurementClaim | StateClaim,
    document: Document,
    page_no: int,
    grounding: GroundingResult,
) -> Claim:
    reasons = list(claim.confidence_reasons)
    if grounding.notes:
        reasons.extend(grounding.notes)

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
        evidence_start=grounding.start,
        evidence_end=grounding.end,
        grounding_method=grounding.method,
        # Assertion time is inherited from the document, never read off the
        # page. A page can say "March 31, 2024" while the document asserting it
        # speaks from August 2024, and conflating the two collapses the whole
        # temporal model.
        assertion_time=document.as_of_date,
        conf_extraction=claim.confidence_extraction,
        conf_grounding=grounding.score,
        confidence_reasons=reasons,
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
    """Extract, ground and persist claims for a document.

    ``pages`` restricts the run to specific page numbers, which keeps iterating
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

    def work(page: Page) -> tuple[Page, PageExtraction | None, str | None]:
        try:
            # The rendered page when there is one, falling back to raw text so a
            # document ingested before layout analysis still extracts.
            source = page.rendered_text or page.text
            return page, extract_page(source, page.page_no, profile), None
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            log.warning("page %s failed: %s", page.page_no, exc)
            return page, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, page_rows))

    for page, extraction, error in results:
        if error is not None:
            run.pages_failed += 1
            run.quarantine_reasons["extraction_call_failed"] = (
                run.quarantine_reasons.get("extraction_call_failed", 0) + 1
            )
            session.add(
                Quarantine(
                    document_id=document_id,
                    page_no=page.page_no,
                    stage="extraction",
                    reason_code="extraction_call_failed",
                    detail=error,
                )
            )
            continue

        assert extraction is not None
        if extraction.page_notes:
            run.notes.append((page.page_no, extraction.page_notes))

        for claim in list(extraction.measurements) + list(extraction.states):
            run.proposed += 1
            grounding = validate(
                quote=claim.evidence_quote,
                value=_claimed_value(claim),
                page_text=page.text,
                rendered_text=page.rendered_text,
            )

            if not grounding.ok:
                run.refused += 1
                reason = grounding.reason_code or "unknown"
                run.quarantine_reasons[reason] = run.quarantine_reasons.get(reason, 0) + 1
                session.add(
                    Quarantine(
                        document_id=document_id,
                        page_no=page.page_no,
                        stage="grounding",
                        reason_code=reason,
                        detail=grounding.detail,
                        payload=claim.model_dump(),
                    )
                )
                continue

            run.grounding_methods[grounding.method] = (
                run.grounding_methods.get(grounding.method, 0) + 1
            )
            session.add(_to_row(claim, document, page.page_no, grounding))
            if isinstance(claim, MeasurementClaim):
                run.measurements += 1
            else:
                run.states += 1

    session.flush()
    return run
