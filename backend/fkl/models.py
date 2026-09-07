"""Append-only storage schema.

Two invariants hold across every phase of this project:

1. **Claims are insert-only.** Nothing in this schema is ever updated to reflect
   a newer document. A later assertion *closes an interval* or *contradicts* an
   earlier one; it never overwrites it. "What is true now" is a query with an
   as-of clause, not a mutable column.

2. **Nothing is silently dropped.** A claim that fails validation moves to
   ``quarantine`` with a reason code. Recall loss is visible, not invisible.

Tables arrive with the phase that needs them. Phase 0 defines documents, pages,
claims and quarantine; the document tree, the registries and the relation table
follow in later phases.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    """One ingested PDF, plus the L0 profile that gives its claims a context.

    ``as_of_date`` is the assertion-time axis. Without it, "director" and
    "resigned" are two conflicting strings; with it they are two assertions made
    at different times, which is a different problem with a different answer.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str] = mapped_column(Text)
    n_pages: Mapped[int] = mapped_column(Integer)
    n_chars: Mapped[int] = mapped_column(Integer, default=0)

    # --- L0 profile: nullable until profiling runs ---
    doc_type: Mapped[str | None] = mapped_column(String(128))
    publisher: Mapped[str | None] = mapped_column(String(256))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    as_of_date_basis: Mapped[str | None] = mapped_column(Text)
    primary_entity: Mapped[str | None] = mapped_column(String(256))
    reporting_period: Mapped[str | None] = mapped_column(String(128))
    default_currency: Mapped[str | None] = mapped_column(String(16))
    default_scale: Mapped[str | None] = mapped_column(String(32))
    default_consolidation: Mapped[str | None] = mapped_column(String(32))
    profile_json: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    """Verbatim text of one page.

    The grounding validator needs exact source text to locate an evidence quote,
    so page text is stored rather than re-derived on demand. The per-page
    ``sha256`` is the incremental-ingest key: an unchanged page is never re-sent
    to the model.
    """

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)  # 0-indexed, matches PyMuPDF
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    n_chars: Mapped[int] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="pages")


class Claim(Base):
    """One typed assertion, carrying the context needed to judge comparability.

    Measurement and state claims share a table with a ``claim_type``
    discriminator. They differ in three columns but pass through the same gate,
    and a flat table keeps every retrieval a single indexed scan.

    ``qualifiers`` is an *open* dict, not an enum. The axis vocabulary is
    discovered from the corpus, so freezing it into the schema would defeat the
    point. ``unknown_qualifiers`` is the honest half of that pair: the axes the
    extractor could not determine. Absent is not the same as equal, and this
    column is what stops the comparison layer from pretending otherwise.
    """

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer, index=True)

    claim_type: Mapped[str] = mapped_column(String(16))  # measurement | state

    subject: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text)  # verbatim, not normalised here
    org_scope: Mapped[str | None] = mapped_column(Text)
    qualifiers: Mapped[dict] = mapped_column(JSON, default=dict)
    unknown_qualifiers: Mapped[list] = mapped_column(JSON, default=list)
    modality: Mapped[str] = mapped_column(String(32), default="actual")

    # --- measurement claims ---
    value_raw: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[float | None] = mapped_column(Float)
    unit_raw: Mapped[str | None] = mapped_column(Text)
    period_raw: Mapped[str | None] = mapped_column(Text)

    # --- state claims: roles, addresses, identifiers, statuses ---
    value_text: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    valid_to_is_open: Mapped[bool | None] = mapped_column(Boolean)

    # --- grounding ---
    evidence_quote: Mapped[str] = mapped_column(Text)
    assertion_time: Mapped[date | None] = mapped_column(Date, index=True)

    # --- decomposed confidence; never blended into a single float ---
    conf_extraction: Mapped[float] = mapped_column(Float, default=0.0)
    conf_grounding: Mapped[float] = mapped_column(Float, default=0.0)
    conf_normalization: Mapped[float] = mapped_column(Float, default=0.0)
    conf_entity_match: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_reasons: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


Index("ix_claims_subject_predicate", Claim.subject, Claim.predicate)


class Quarantine(Base):
    """Claims that failed validation, kept with a reason code.

    Deleting a bad extraction hides the failure rate. Keeping it makes recall
    loss measurable, and turns the assignment's required "extraction failure"
    case into a queryable view rather than an anecdote.
    """

    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_no: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))  # extraction | grounding | ...
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)  # the rejected claim
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
