"""Corpus report: what the pipeline actually produced, in numbers.

Written now, before there is anything impressive to report, because a metric
added after the fact tends to be the one that flatters the result. Every figure
here is regenerable with one command and moves when the pipeline changes.

The four that matter:

**Grounding precision** — of the claims the model proposed, how many could be
verified against the source page. This is extraction quality with the
self-assessment removed.

**Context coverage** — of the figures extracted, how many arrived with a unit
and a period attached. This is what the layout and inheritance work is for, and
it is the number that separates a comparable fact from an unlabelled float.

**Declared unknowns** — how many claims name an axis they could not determine.
A healthy number here is a sign of honesty, not of failure. Zero would mean the
extractor is asserting complete context for every figure on every page, which
for these documents cannot be true.

**Quarantine** — what was refused, by reason. Recall loss stated rather than
hidden.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Claim, Document, Quarantine


@dataclass
class DocumentReport:
    document_id: int
    filename: str
    n_pages: int
    profiled: bool
    claims: int = 0
    measurements: int = 0
    states: int = 0
    refused: int = 0  # claims quarantined by grounding
    failed_pages: int = 0  # pages whose model call raised
    with_unit: int = 0
    with_period: int = 0
    with_unknown_axes: int = 0
    grounding_methods: Counter = field(default_factory=Counter)
    quarantine_reasons: Counter = field(default_factory=Counter)
    unknown_axes: Counter = field(default_factory=Counter)

    @property
    def proposed(self) -> int:
        """Claims the model put forward. Failed pages proposed nothing, so they
        are counted separately rather than depressing this denominator."""
        return self.claims + self.refused

    @property
    def grounding_precision(self) -> float:
        return self.claims / self.proposed if self.proposed else 0.0

    @property
    def unit_coverage(self) -> float:
        return self.with_unit / self.measurements if self.measurements else 0.0

    @property
    def period_coverage(self) -> float:
        return self.with_period / self.measurements if self.measurements else 0.0


def _is_present(value: str | None) -> bool:
    """Whether a field carries real content rather than a placeholder.

    Models fill a required string field with something. "unknown", "n/a" and
    "-" are all ways of saying nothing, and counting them as a declared unit
    would inflate exactly the number this report exists to measure.
    """
    if not value:
        return False
    cleaned = value.strip().lower().strip(".-")
    return cleaned not in {"", "unknown", "unspecified", "n/a", "na", "none", "not stated"}


def document_report(session: Session, document_id: int) -> DocumentReport:
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError(f"no document with id {document_id}")

    report = DocumentReport(
        document_id=document.id,
        filename=document.filename,
        n_pages=document.n_pages,
        profiled=document.profile_json is not None,
    )

    for claim in session.scalars(select(Claim).where(Claim.document_id == document_id)):
        report.claims += 1
        report.grounding_methods[claim.grounding_method or "unrecorded"] += 1
        if claim.unknown_qualifiers:
            report.with_unknown_axes += 1
            for axis in claim.unknown_qualifiers:
                report.unknown_axes[axis] += 1

        if claim.claim_type == "measurement":
            report.measurements += 1
            if _is_present(claim.unit_raw):
                report.with_unit += 1
            if _is_present(claim.period_raw):
                report.with_period += 1
        else:
            report.states += 1

    for stage, reason, count in session.execute(
        select(Quarantine.stage, Quarantine.reason_code, func.count())
        .where(Quarantine.document_id == document_id)
        .group_by(Quarantine.stage, Quarantine.reason_code)
    ):
        report.quarantine_reasons[reason] += count
        if stage == "grounding":
            report.refused += count
        else:
            report.failed_pages += count

    return report


def corpus_report(session: Session) -> list[DocumentReport]:
    ids = session.scalars(select(Document.id).order_by(Document.id)).all()
    return [document_report(session, i) for i in ids]


def format_reports(reports: list[DocumentReport]) -> str:
    if not reports:
        return "no documents ingested"

    lines: list[str] = []
    total = DocumentReport(0, "TOTAL", 0, True)

    lines.append(
        f"{'doc':<3} {'file':<40} {'claims':>7} {'grounded':>9} "
        f"{'unit':>7} {'period':>7} {'unknown':>8}"
    )
    lines.append("-" * 86)

    for r in reports:
        total.claims += r.claims
        total.measurements += r.measurements
        total.states += r.states
        total.refused += r.refused
        total.failed_pages += r.failed_pages
        total.with_unit += r.with_unit
        total.with_period += r.with_period
        total.with_unknown_axes += r.with_unknown_axes
        total.grounding_methods += r.grounding_methods
        total.quarantine_reasons += r.quarantine_reasons
        total.unknown_axes += r.unknown_axes

        lines.append(
            f"{r.document_id:<3} {r.filename[:40]:<40} {r.claims:>7} "
            f"{r.grounding_precision:>8.0%} {r.unit_coverage:>6.0%} "
            f"{r.period_coverage:>6.0%} {r.with_unknown_axes:>8}"
        )

    lines.append("-" * 86)
    lines.append(
        f"{'':<3} {'all documents':<40} {total.claims:>7} "
        f"{total.grounding_precision:>8.0%} {total.unit_coverage:>6.0%} "
        f"{total.period_coverage:>6.0%} {total.with_unknown_axes:>8}"
    )

    lines.append("")
    lines.append(f"proposed by the model      {total.proposed}")
    lines.append(f"survived grounding         {total.claims}")
    lines.append(f"refused by grounding       {total.refused}")
    lines.append(f"pages whose call failed    {total.failed_pages}")
    if total.grounding_methods:
        lines.append("")
        lines.append("how evidence was matched")
        for method, count in total.grounding_methods.most_common():
            share = count / total.claims if total.claims else 0
            lines.append(f"  {method:<16} {count:>6}  {share:>5.0%}")
    if total.quarantine_reasons:
        lines.append("")
        lines.append("why claims were refused")
        for reason, count in total.quarantine_reasons.most_common():
            lines.append(f"  {reason:<26} {count:>6}")
    if total.unknown_axes:
        lines.append("")
        lines.append("axes the extractor declared it could not determine")
        for axis, count in total.unknown_axes.most_common(10):
            lines.append(f"  {axis:<26} {count:>6}")
    return "\n".join(lines)
