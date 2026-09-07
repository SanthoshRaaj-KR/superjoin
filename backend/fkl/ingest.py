"""Ingestion: PDF on disk to profiled document rows.

Re-ingesting an unchanged PDF is a no-op. That is not a feature bolted on for a
demo — it falls out of content hashing, and it is what makes the model calls in
later layers affordable to iterate on. The document hash short-circuits the
whole file; the per-page hash will short-circuit individual pages once page-level
extraction lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.profile import DocumentProfile, parse_iso_date, profile_document
from .models import Document, Page
from .pdf.extract import ExtractedDocument, extract_document, has_text_layer
from .render import figure_context_coverage, render_page

log = logging.getLogger(__name__)


class NoTextLayer(RuntimeError):
    """Raised for scanned PDFs, which would need OCR."""


@dataclass
class IngestResult:
    document_id: int
    filename: str
    n_pages: int
    n_chars: int
    reused: bool  # True when the PDF was already ingested and nothing was redone
    profile: DocumentProfile | None
    figure_pages: int = 0
    axis_coverage: dict[str, int] = field(default_factory=dict)


def _store_profile(row: Document, profile: DocumentProfile) -> None:
    row.doc_type = profile.doc_type
    row.publisher = profile.publisher
    row.primary_entity = profile.primary_entity
    row.as_of_date = parse_iso_date(profile.as_of_date)
    row.as_of_date_basis = profile.as_of_date_basis
    row.reporting_period = profile.reporting_period
    row.default_currency = profile.default_currency
    row.default_scale = profile.default_scale
    row.default_consolidation = profile.default_consolidation
    row.profile_json = profile.model_dump()


def ingest_pdf(
    session: Session,
    path: str | Path,
    *,
    use_llm: bool = True,
    force: bool = False,
) -> IngestResult:
    """Extract, profile and store one PDF.

    With ``use_llm=False`` the document is stored unprofiled. That path exists so
    the extraction and storage layers can be exercised, and their behaviour
    verified, without credentials or spend.
    """
    path = Path(path)
    extracted: ExtractedDocument = extract_document(path)

    if not has_text_layer(extracted):
        raise NoTextLayer(
            f"{path.name} has little or no extractable text. It is probably a scan; "
            "OCR is out of scope for this project."
        )

    existing = session.scalar(select(Document).where(Document.sha256 == extracted.sha256))
    if existing is not None and not force:
        log.info("%s already ingested as document %s", path.name, existing.id)
        return IngestResult(
            document_id=existing.id,
            filename=existing.filename,
            n_pages=existing.n_pages,
            n_chars=existing.n_chars,
            reused=True,
            profile=DocumentProfile(**existing.profile_json) if existing.profile_json else None,
        )

    row = existing or Document(
        sha256=extracted.sha256,
        filename=path.name,
        source_path=str(path.resolve()),
        n_pages=extracted.n_pages,
        n_chars=extracted.n_chars,
    )
    row.body_font_size = extracted.body_size

    if existing is None:
        session.add(row)
        session.flush()
        session.add_all(
            Page(
                document_id=row.id,
                page_no=p.page_no,
                sha256=p.sha256,
                text=p.text,
                n_chars=p.n_chars,
            )
            for p in extracted.pages
        )
        session.flush()

    profile = None
    if use_llm:
        profile = profile_document(extracted)
        _store_profile(row, profile)

    # Rendering happens after profiling because document-level defaults form the
    # base of every page frame. A page rendered before the profile exists would
    # report axes as undeclared that the document had in fact declared.
    figure_pages, axis_coverage = _render_pages(session, row, extracted)

    session.flush()
    return IngestResult(
        document_id=row.id,
        filename=row.filename,
        n_pages=row.n_pages,
        n_chars=row.n_chars,
        reused=False,
        profile=profile,
        figure_pages=figure_pages,
        axis_coverage=axis_coverage,
    )


def _render_pages(
    session: Session, row: Document, extracted: ExtractedDocument
) -> tuple[int, dict[str, int]]:
    """Store the layout-aware rendering of every page.

    Returns the number of pages carrying figures, and how many of those declare
    each material axis. The gap is the honest number: it is the share of the
    document where a figure would reach the extractor with no unit, no basis or
    no period attached to it.
    """
    pages = {p.page_no: p for p in session.scalars(
        select(Page).where(Page.document_id == row.id)
    )}
    figure_pages = 0
    coverage: dict[str, int] = {}
    for extracted_page in extracted.pages:
        if extracted_page.layout is None:
            continue
        page_row = pages.get(extracted_page.page_no)
        if page_row is None:
            continue
        rendered = render_page(extracted_page.layout, row.profile_json, extracted.body_size)
        page_row.rendered_text = rendered.text
        page_row.context_json = rendered.frames
        declared = figure_context_coverage(rendered, extracted_page.text)
        if declared is None:
            continue  # no figures on this page; context coverage says nothing
        figure_pages += 1
        for axis in declared:
            coverage[axis] = coverage.get(axis, 0) + 1
    return figure_pages, coverage
