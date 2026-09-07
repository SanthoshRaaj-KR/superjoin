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

from .canonical import canonicalize, context_hint
from .grounding import GroundingResult, validate
from .llm.claims import extract_page
from .llm.figures import extract_figures, should_run
from .llm.profile import parse_iso_date
from .models import Claim, Document, Page, Quarantine
from .schemas import MeasurementClaim, PageExtraction, StateClaim

log = logging.getLogger(__name__)

DEFAULT_WORKERS = 6


@dataclass
class ExtractionRun:
    document_id: int
    pages_attempted: int = 0
    pages_skipped: int = 0
    claims_replaced: int = 0
    pages_failed: int = 0
    measurements: int = 0
    states: int = 0
    proposed: int = 0
    refused: int = 0  # claims that failed grounding
    normalized: int = 0
    unresolved_units: int = 0
    unresolved_periods: int = 0
    unresolved_metrics: int = 0
    figure_pages_flagged: int = 0
    figure_pages_read: int = 0
    figure_pages_failed: int = 0
    figure_claims: int = 0
    superseded_by_figures: int = 0
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


# A figure claim's self-reported confidence is about reading the number, which
# is the easy half. The model has no way to know whether it swapped two series
# in a legend, and it reports 1.0 on chart pages where that is exactly the risk.
# Capping it is not pessimism, it is refusing to record a certainty nobody
# measured.
FIGURE_CONFIDENCE_CAP = 0.75


def _to_row(
    claim: MeasurementClaim | StateClaim,
    document: Document,
    page_no: int,
    grounding: GroundingResult,
    source: str = "text",
) -> Claim:
    reasons = list(claim.confidence_reasons)
    if grounding.notes:
        reasons.extend(grounding.notes)

    extraction_confidence = claim.confidence_extraction
    if source == "figure":
        extraction_confidence = min(extraction_confidence, FIGURE_CONFIDENCE_CAP)
        reasons.append(
            "series and period read from the chart image; the value is verified "
            "against the page text but the binding is not"
        )

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
        source=source,
        conf_extraction=extraction_confidence,
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
    use_figures: bool = False,
    adjudicate: bool = True,
    force: bool = False,
) -> ExtractionRun:
    """Extract, ground and persist claims for a document.

    Two passes. The text pass reads every page. The figure pass then looks at
    the pages whose numbers came back unbound — chart pages, where flattening
    preserved every value and destroyed every relationship — and reads them as
    images to recover which value belongs to which series and period.

    The figure pass is **off by default**, and that is a measured decision
    rather than a cost saving. On this corpus the charts restate numbers that
    the tables already carry: the deck prints FY24 revenue as ``8,142`` inside a
    chart on pages 8 and 9, and also as ``₹8,142 Cr`` in text on page 5 and
    inside reconstructed table rows on pages 13, 16 and 22. Every demonstration
    case is reachable without looking at a single image.

    It stays available because that redundancy is a property of these documents,
    not a guarantee about the next one. A deck that only ever charts a figure
    would lose it entirely, and the routing signal makes that visible.

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

    # Extraction is idempotent by page. The claims table is append-only, so
    # without this a second run over the same pages does not correct anything —
    # it silently doubles them, and every downstream count of agreements and
    # disagreements doubles with it. Found the honest way: three accidental
    # re-runs of one command left 53% of the table duplicated.
    if not force:
        already = set(
            session.scalars(
                select(Claim.page_no).where(Claim.document_id == document_id).distinct()
            )
        ) | set(
            session.scalars(
                select(Quarantine.page_no)
                .where(Quarantine.document_id == document_id)
                .distinct()
            )
        )
        skipped = [p for p in page_rows if p.page_no in already]
        page_rows = [p for p in page_rows if p.page_no not in already]
    else:
        # `--force` replaces rather than appends. Append-only is about never
        # silently overwriting what a document said as later documents arrive;
        # it was never meant to make a re-extraction after a prompt change
        # produce two generations of the same claim side by side. Without this,
        # forcing a re-run leaves the old readings in place and every count
        # downstream doubles — the same failure the idempotence guard was added
        # to prevent, arriving through the flag that bypasses it.
        skipped = []
        page_numbers = [p.page_no for p in page_rows]
        replaced = session.query(Claim).filter(
            Claim.document_id == document_id, Claim.page_no.in_(page_numbers)
        ).delete(synchronize_session=False)
        session.query(Quarantine).filter(
            Quarantine.document_id == document_id,
            Quarantine.page_no.in_(page_numbers),
        ).delete(synchronize_session=False)
        run_replaced = replaced

    profile = document.profile_json
    run = ExtractionRun(
        document_id=document_id,
        pages_attempted=len(page_rows),
        pages_skipped=len(skipped),
        claims_replaced=locals().get("run_replaced", 0),
    )
    text_claims: dict[int, PageExtraction] = {}

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
        text_claims[page.page_no] = extraction

    # --- second pass: charts, on pages whose figures came back unbound -------
    figure_pages = [p for p in page_rows if should_run(p.unbound_numbers, p.bound_numbers)]
    run.figure_pages_flagged = len(figure_pages)
    figure_claims: dict[int, PageExtraction] = {}
    if figure_pages and use_figures:
        log.info("figure pass on %s page(s)", len(figure_pages))

        def look(page: Page):
            try:
                return page, extract_figures(document.source_path, page.page_no, profile), None
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                log.warning("figure pass failed on page %s: %s", page.page_no, exc)
                return page, None, f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for page, extraction, error in pool.map(look, figure_pages):
                if error is not None:
                    run.figure_pages_failed += 1
                    run.quarantine_reasons["figure_pass_failed"] = (
                        run.quarantine_reasons.get("figure_pass_failed", 0) + 1
                    )
                    session.add(
                        Quarantine(
                            document_id=document_id,
                            page_no=page.page_no,
                            stage="figures",
                            reason_code="figure_pass_failed",
                            detail=error,
                        )
                    )
                    continue
                figure_claims[page.page_no] = extraction
                run.figure_pages_read += 1

    # --- persist, figure claims first so their bindings win ------------------
    for page in page_rows:
        bound_values: set[str] = set()

        for claim in _claims_of(figure_claims.get(page.page_no)):
            if _persist(session, run, document, page, claim, "figure", adjudicate):
                bound_values.add(_digits(_claimed_value(claim)))

        for claim in _claims_of(text_claims.get(page.page_no)):
            # The text pass on a chart page reports numbers it could not attach
            # to a series or a period. Where the figure pass has bound the same
            # number, its binding is the better record of the same fact, and
            # keeping both would hand the comparison layer two readings of one
            # bar to disagree about.
            if isinstance(claim, MeasurementClaim) and _digits(claim.value_raw) in bound_values:
                run.superseded_by_figures += 1
                continue
            _persist(session, run, document, page, claim, "text", adjudicate)

    session.flush()
    return run


def _claims_of(extraction: PageExtraction | None) -> list[MeasurementClaim | StateClaim]:
    if extraction is None:
        return []
    return list(extraction.measurements) + list(extraction.states)


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _persist(
    session: Session,
    run: ExtractionRun,
    document: Document,
    page: Page,
    claim: MeasurementClaim | StateClaim,
    source: str,
    adjudicate: bool = True,
) -> bool:
    """Ground one claim and store it, or quarantine it. Returns whether it kept.

    Figure claims go through exactly the same validator as text claims, against
    the same raw page text. That is what keeps the second pass honest: a chart
    reading can propose which bar a number sits on, but it cannot introduce a
    number that was never printed on the page.
    """
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
                document_id=document.id,
                page_no=page.page_no,
                stage="grounding",
                reason_code=reason,
                detail=grounding.detail,
                payload=claim.model_dump(),
            )
        )
        return False

    run.grounding_methods[grounding.method] = run.grounding_methods.get(grounding.method, 0) + 1
    row = _to_row(claim, document, page.page_no, grounding, source)
    # After grounding, before the insert. A claim reaches the table already
    # comparable, so nothing above has to re-derive its unit or its interval.
    canonicalize(session, row, hint=context_hint(page.context_json),
                 adjudicate=adjudicate)
    run.normalized += 1
    if row.unit_dimension is None and row.claim_type == "measurement":
        run.unresolved_units += 1
    if row.period_start is None and row.period_raw:
        run.unresolved_periods += 1
    # Counted rather than raised. Metric resolution can make a model call, and a
    # claim whose metric could not be resolved is still a good claim — it simply
    # will not join a comparison. Silence here would hide a broken registry
    # behind a clean-looking run, so the count is reported.
    if row.metric_id is None:
        run.unresolved_metrics += 1
    session.add(row)
    if isinstance(claim, MeasurementClaim):
        run.measurements += 1
    else:
        run.states += 1
    if source == "figure":
        run.figure_claims += 1
    return True
