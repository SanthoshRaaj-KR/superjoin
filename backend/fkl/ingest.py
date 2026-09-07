"""Ingestion: PDF on disk to profiled document rows.

Re-ingesting an unchanged PDF is a no-op. That is not a feature bolted on for a
demo — it falls out of content hashing, and it is what makes the model calls in
later layers affordable to iterate on. The document hash short-circuits the
whole file; the per-page hash will short-circuit individual pages once page-level
extraction lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.profile import DocumentProfile, parse_iso_date, profile_document
from .models import Document, Page
from .pdf.extract import ExtractedDocument, extract_document, has_text_layer

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

    profile = None
    if use_llm:
        profile = profile_document(extracted)
        _store_profile(row, profile)

    session.flush()
    return IngestResult(
        document_id=row.id,
        filename=row.filename,
        n_pages=row.n_pages,
        n_chars=row.n_chars,
        reused=False,
        profile=profile,
    )
