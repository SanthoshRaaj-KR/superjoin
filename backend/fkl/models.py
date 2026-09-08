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
    LargeBinary,
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

    # Measured once per document and reused for every page, so heading levels
    # are consistent throughout rather than re-derived from whatever happens to
    # be on each page.
    body_font_size: Mapped[float | None] = mapped_column(Float)

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

    # The layout-aware reading of the page: reading order restored, table rows
    # rebuilt, and the context in force stated inline. This is what the
    # extractor is shown. It never replaces `text`, which stays the source of
    # truth for grounding — a quote that matches only the rendering is a quote
    # that was assembled rather than found.
    rendered_text: Mapped[str | None] = mapped_column(Text)
    context_json: Mapped[list | None] = mapped_column(JSON)

    # How many figures on this page sit inside a reconstructed row (bound, and
    # therefore meaningful) versus alone on a line (unbound — a chart label
    # whose series and period were lost when the page was flattened). The ratio
    # is what routes a page to the figure pass, and storing it keeps that
    # decision auditable rather than recomputed and forgotten.
    unbound_numbers: Mapped[int | None] = mapped_column(Integer)
    bound_numbers: Mapped[int | None] = mapped_column(Integer)

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
    # Which pass produced this claim: "text" read it from the page's words,
    # "figure" read it off a chart image. Recorded because the two carry
    # different kinds of proof. Both have their VALUE verified against the page
    # text, but a figure claim's BINDING — that 5,077 belongs to Express Parcel
    # in FY24 rather than to the series beside it — rests on the vision model
    # and cannot be checked against the text layer, because the flattening that
    # loses the binding is exactly why the figure pass was needed.
    source: Mapped[str] = mapped_column(String(16), default="text", index=True)

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
    # Character span of the quote within the page text, where the match tier
    # allows exact offsets. Stored so a viewer can highlight the real
    # characters rather than search for the quote again and possibly find a
    # different occurrence of it.
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    # How the quote was matched: exact | normalized | dehyphenated |
    # reconstructed. Kept because "found in the raw page" and "found only in
    # our rebuilt rendering of the page" are different strengths of evidence.
    grounding_method: Mapped[str | None] = mapped_column(String(24))
    assertion_time: Mapped[date | None] = mapped_column(Date, index=True)

    # --- decomposed confidence; never blended into a single float ---
    # --- L4 canonical form
    #
    # Written beside the raw strings, never instead of them. `value_raw` is what
    # the page says and `value_canonical` is what it means; keeping both is what
    # lets a comparison be explained back to the reader in the document's own
    # units after being made in base ones.
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), index=True)
    metric_id: Mapped[int | None] = mapped_column(ForeignKey("metrics.id"), index=True)
    value_canonical: Mapped[float | None] = mapped_column(Float)
    unit_dimension: Mapped[str | None] = mapped_column(String(32), index=True)
    unit_currency: Mapped[str | None] = mapped_column(String(8))
    unit_scale: Mapped[float | None] = mapped_column(Float)
    period_start: Mapped[date | None] = mapped_column(Date, index=True)
    period_end: Mapped[date | None] = mapped_column(Date, index=True)
    period_label: Mapped[str | None] = mapped_column(String(64), index=True)
    period_granularity: Mapped[str | None] = mapped_column(String(24))
    # A period read from a bare year-end date with no section declaration to
    # resolve it. Carried through to the verdict rather than discarded, because
    # "these agree" means something different when one side was a guess.
    period_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)

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


# --- L4 registries ----------------------------------------------------------
#
# Two registries, one shape. A registry holds canonical things and the many
# strings documents use to refer to them, so that "Revenue from contracts with
# customers" and "Revenue from services" can be recognised as one metric without
# either spelling being privileged as the "real" one.
#
# Unlike claims, registries *are* mutable: they accumulate aliases as documents
# arrive. That is the "schema that evolves" part of the brief, and it is safe
# precisely because no fact lives here — only naming.


class Entity(Base):
    """A company, person or country that claims are made about."""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(512))
    entity_type: Mapped[str] = mapped_column(String(32), default="organisation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    identifiers: Mapped[list["EntityIdentifier"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["EntityAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class EntityIdentifier(Base):
    """A registration number that pins an entity across documents and spellings.

    This is the strongest evidence available and it beats every similarity
    measure. DIN 01173669 identifies one director whether a document writes
    "Suvir Suren Sujan" or "Suvir Sujan"; the CIN registration number identifies
    Delhivery across the `U` to `L` prefix change that listing caused, which no
    string comparison would forgive and no embedding should be trusted to.
    """

    __tablename__ = "entity_identifiers"
    __table_args__ = (UniqueConstraint("kind", "value", name="uq_identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # cin | din | isin | ...
    value: Mapped[str] = mapped_column(String(64), index=True)
    # The comparable form: a CIN with its listing prefix and company class
    # removed, so `U...PLC221234` and `L...PLC221234` share a key.
    normalized: Mapped[str] = mapped_column(String(64), index=True)

    entity: Mapped[Entity] = relationship(back_populates="identifiers")


class EntityAlias(Base):
    """A surface form that has been seen referring to an entity."""

    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("alias_key", name="uq_entity_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    alias: Mapped[str] = mapped_column(String(512))
    alias_key: Mapped[str] = mapped_column(String(512), index=True)
    # How the link was made, so a wrong merge can be traced to its cause.
    method: Mapped[str] = mapped_column(String(32), default="exact")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    # Kept distinct from `confidence`, which is the adjudicator's certainty that
    # the two names denote one entity. This is how *close* the names looked —
    # the reason the pair was put to the adjudicator at all. Recording only the
    # first hides whether a bad merge came from a bad candidate or a bad call.
    similarity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    entity: Mapped[Entity] = relationship(back_populates="aliases")


class Metric(Base):
    """A canonical thing that gets measured.

    Metrics are discovered rather than enumerated. There is no whitelist of
    financial line items anywhere in this project: the first document to say
    "Revenue from contracts with customers" creates the metric, and the next
    document saying "Revenue from services" is matched against it. A fixed
    vocabulary would work on the annual report and fail on the IMF report, which
    is the generalisation the brief is actually testing.
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(512))
    # Two metrics with different dimensions are never the same metric, however
    # similar their names. "Revenue growth" (percent) and "Revenue" (currency)
    # read almost identically to an embedding.
    dimension: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    aliases: Mapped[list["MetricAlias"]] = relationship(
        back_populates="metric", cascade="all, delete-orphan"
    )


class MetricAlias(Base):
    """A predicate string that has been resolved to a metric.

    ``method`` and ``similarity`` are kept because a registry that grows by
    matching needs its matches auditable. A wrong alias silently widens a metric
    forever, and the only way to find one later is to be able to see how it was
    admitted.
    """

    __tablename__ = "metric_aliases"
    __table_args__ = (UniqueConstraint("alias_key", "dimension", name="uq_metric_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metrics.id"), index=True)
    alias: Mapped[str] = mapped_column(String(512))
    alias_key: Mapped[str] = mapped_column(String(512), index=True)
    dimension: Mapped[str | None] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(32), default="exact")
    # Kept apart on purpose: `similarity` is how close the names looked,
    # `confidence` is how sure we are they are the same metric. The measurement
    # in `metrics.py` is that those are different questions — 0.846 of
    # similarity between `Adjusted EBITDA` and `EBITDA` carries no confidence at
    # all — so storing one number for both would erase the finding.
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    similarity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    metric: Mapped[Metric] = relationship(back_populates="aliases")


class Embedding(Base):
    """A cached embedding vector, stored as raw float32 bytes.

    Kept in the same SQLite file rather than a vector service. For a corpus this
    size brute-force cosine over a few hundred vectors is microseconds, and the
    blocking key — entity plus metric — is what carries the design to a corpus
    where that stops being true. Adding a vector database here would be
    infrastructure bought against a bottleneck that does not exist.
    """

    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("model", "text_key", name="uq_embedding"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(64), index=True)
    text_key: Mapped[str] = mapped_column(String(512), index=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Relation(Base):
    """A verdict about one pair of claims.

    Derived rather than asserted: nothing here is information the documents
    contain, only what the gate concluded from two claims that do. It is stored
    because recomputing every pair on every request is wasteful, and because the
    corpus-level number — how many apparent disagreements a named axis dissolved
    — is a query over this table.

    Append-only like everything else. Re-running the gate after a change writes
    a new generation rather than editing the old one, so a verdict that moved
    can be seen to have moved.
    """

    __tablename__ = "relations"
    __table_args__ = (
        Index("ix_relations_pair", "claim_a_id", "claim_b_id"),
        # Named for both columns: `verdict` alone collides with the index
        # SQLAlchemy generates for the column's own index=True.
        Index("ix_relations_verdict_axis", "verdict", "axis"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_a_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    claim_b_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)

    verdict: Mapped[str] = mapped_column(String(32), index=True)
    # The field responsible for the verdict. A CONTEXTUAL verdict without one
    # is a shrug rather than an explanation.
    axis: Mapped[str | None] = mapped_column(String(32), index=True)
    explanation: Mapped[str] = mapped_column(Text)
    differing_axes: Mapped[list] = mapped_column(JSON, default=list)
    missing_axes: Mapped[list] = mapped_column(JSON, default=list)

    # Kept so the corpus reduction can be reported over *raw value* differences:
    # how many pairs looked like disagreements before context was consulted.
    values_differ: Mapped[bool | None] = mapped_column(Boolean)
    value_difference: Mapped[float | None] = mapped_column(Float)
    value_relative: Mapped[float | None] = mapped_column(Float)
    period_relation: Mapped[str | None] = mapped_column(String(24))

    cross_document: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # What a second look at the pages found, for pairs the gate first called a
    # contradiction. Kept beside the verdict rather than replacing it: a
    # contradiction that was withdrawn is a different and more interesting
    # object than one that was never raised, and a reader has to be able to see
    # which axis withdrew it and on what evidence.
    reconsidered: Mapped[bool] = mapped_column(Boolean, default=False)
    original_verdict: Mapped[str | None] = mapped_column(String(32))
    recovery_axis: Mapped[str | None] = mapped_column(String(48))
    recovery_method: Mapped[str | None] = mapped_column(String(48))
    recovery_reason: Mapped[str | None] = mapped_column(Text)
    recovery_confidence: Mapped[float | None] = mapped_column(Float)
    # The values themselves, so a later comparison of the same two claims can
    # be made with the context the review recovered instead of without it.
    # Without these the API would re-derive a bare CONTRADICTS on every page
    # view and disagree with its own stored verdict.
    recovery_a_value: Mapped[str | None] = mapped_column(Text)
    recovery_b_value: Mapped[str | None] = mapped_column(Text)


class Axis(Base):
    """The discovered-axis registry (L5b).

    An axis arrives here in one of two ways, and the distinction is the whole
    point of keeping a table rather than a list. A *seeded* axis is one the
    system was born knowing — ``consolidation``, ``period``, ``modality``. A
    *discovered* axis is a phrase the corpus itself supplied: the second look
    at a contradiction found a distinction printed on the page and named it,
    and that name was not in any vocabulary beforehand.

    ``occurrences`` is what makes promotion meaningful. A candidate seen once
    is an anecdote — it may be a mis-reading of a single page. The same
    candidate recurring across independent pairs is a property of the corpus,
    and at ``PROMOTION_THRESHOLD`` it stops being a hypothesis and becomes part
    of the vocabulary the gate reasons with.
    """

    __tablename__ = "axes"
    __table_args__ = (UniqueConstraint("name", name="uq_axis_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    origin: Mapped[str] = mapped_column(String(16), default="discovered")
    status: Mapped[str] = mapped_column(String(16), default="candidate")

    # How many independent pairs produced this axis, and how many disagreements
    # naming it were dissolved rather than upheld. An axis that recurs but never
    # resolves anything is noise worth seeing.
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    resolves: Mapped[int] = mapped_column(Integer, default=0)

    values_seen: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_relation_id: Mapped[int | None] = mapped_column(Integer)
    example: Mapped[str | None] = mapped_column(Text)

    promoted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Predicate(Base):
    """Cardinality per canonical predicate, made auditable (L5c).

    Cardinality was always inferred — a plural head noun means many holders, a
    singular one means a slot — but inferring it on every call made it
    invisible. A wrongly-inferred ``1`` manufactures a contradiction out of two
    people who genuinely held different posts, and the plan's own trade-off
    note says the inferred value has to be *shown* for that to be auditable.

    So it is stored, with how it was arrived at. ``inferred`` is the grammar
    rule's answer; ``observed`` is what the corpus proved by showing two
    holders overlapping inside a single document. Observation wins, because a
    document asserting both at once is stronger evidence than a head noun.
    """

    __tablename__ = "predicates"
    __table_args__ = (UniqueConstraint("name", name="uq_predicate_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    cardinality: Mapped[int] = mapped_column(Integer, default=1)
    inferred: Mapped[int | None] = mapped_column(Integer)
    observed: Mapped[int | None] = mapped_column(Integer)
    basis: Mapped[str] = mapped_column(String(32), default="grammar")
    holders: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Job(Base):
    """One upload's passage through the pipeline.

    Uploading a folder is not a request that can be answered inside an HTTP
    round trip — ingest, extraction and comparison take minutes, and the model
    calls dominate. So the upload returns a job id immediately and the work
    happens on a worker thread, which is why this is a table rather than a
    dictionary: a job that is still running when the process restarts should
    come back as *failed*, visibly, instead of vanishing and leaving a document
    half-extracted with nothing to explain it.

    ``log`` is append-only text, and it is the honest part of the design. A
    percentage tells a reader that something is happening; the log tells them
    which page failed and why.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    detail: Mapped[str | None] = mapped_column(Text)

    filenames: Mapped[list] = mapped_column(JSON, default=list)
    document_ids: Mapped[list] = mapped_column(JSON, default=list)

    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_done: Mapped[int] = mapped_column(Integer, default=0)
    pages_total: Mapped[int] = mapped_column(Integer, default=0)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    claims: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[int] = mapped_column(Integer, default=0)
    relations: Mapped[int] = mapped_column(Integer, default=0)

    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
