"""Upload a folder of PDFs and let the whole pipeline run behind a job id.

Everything this module does was already possible from the command line. What it
adds is the ability to hand the system a folder and get an answer, which is the
difference between a project that has been run and a project that can be run —
by a grader, on their own documents, without reading the CLI's help text.

**Why a job and not a request.** Ingest is fast; extraction is not. A hundred
pages is a hundred structured model calls, and no HTTP client waits minutes for
a response without timing out somewhere in the middle. So the upload returns
immediately with an id, and the work happens on a worker thread whose progress
is written to the ``jobs`` table as it goes.

**Why one worker.** Two concurrent uploads would interleave writes to one
SQLite file and, worse, run two relate passes that each believe they own the
newest generation. Serialising them costs a queued job a few minutes and buys
the property that a generation always means one complete pass over the corpus.

**Why the whole pipeline and not just ingest.** A document that is stored but
never compared has nothing to say. The interesting output of this system is the
relation set, and that only exists after every new claim has been placed beside
every comparable claim already in the store. So the relate pass at the end is
corpus-wide, not upload-wide: the point of adding a document is what it
disagrees with.
"""

from __future__ import annotations

import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from .config import REPO_ROOT, SETTINGS
from .db import init_db, session_scope
from .models import Claim, Job, Quarantine

log = logging.getLogger(__name__)

UPLOAD_ROOT = REPO_ROOT / "data" / "uploads"

# Serialised on purpose - see the module docstring.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fkl-job")
_lock = threading.Lock()

# A ceiling on how much of a long document one upload will read. Extraction is
# the only part of this system that costs money, and an unattended upload of a
# 400-page filing should not be able to spend without anyone having chosen to.
# Raise it per request; the CLI has never had a limit and still does not.
DEFAULT_MAX_PAGES = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append(job: Job, line: str) -> None:
    job.log = (job.log or "") + line.rstrip() + "\n"
    log.info("job %s: %s", job.id, line)


def create_job(filenames: list[str]) -> int:
    init_db()
    with session_scope() as session:
        job = Job(filenames=filenames, files_total=len(filenames),
                  status="queued", stage="queued")
        session.add(job)
        session.flush()
        return job.id


def save_uploads(job_id: int, files: list[tuple[str, bytes]]) -> list[Path]:
    """Write the uploaded bytes under this job's own directory.

    A folder upload arrives with paths like ``reports/2024/annual.pdf``, and
    those separators are the caller's, not ours. Only the basename is kept, and
    it is kept per-job so two uploads of ``report.pdf`` never overwrite each
    other - the store already deduplicates on content hash, so a genuine
    re-upload is caught there, by what the file *is* rather than what it is
    called.
    """
    target = UPLOAD_ROOT / str(job_id)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, blob in files:
        safe = Path(name.replace("\\", "/")).name or "upload.pdf"
        path = target / safe
        path.write_bytes(blob)
        written.append(path)
    return written


def submit(job_id: int, paths: list[Path], *, max_pages: int = DEFAULT_MAX_PAGES,
           review: bool = True) -> None:
    _pool.submit(_run, job_id, [str(p) for p in paths], max_pages, review)


def _run(job_id: int, paths: list[str], max_pages: int, review: bool) -> None:
    """The whole pipeline for one upload, with every step logged as it lands."""
    with _lock:
        try:
            _ingest_and_extract(job_id, paths, max_pages)
            _relate(job_id, review)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("job %s failed", job_id)
            with session_scope() as session:
                job = session.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.finished_at = _now()
                    _append(job, f"failed: {exc}")


def _ingest_and_extract(job_id: int, paths: list[str], max_pages: int) -> None:
    from .ingest import NoTextLayer, ingest_pdf
    from .pipeline import extract_document_claims

    use_llm = SETTINGS.has_llm

    for path in paths:
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "running"
            job.stage = "ingest"
            job.detail = Path(path).name
            try:
                result = ingest_pdf(session, path, use_llm=use_llm)
            except NoTextLayer as exc:
                # Not a crash. A scanned PDF is a document this system has said
                # all along it cannot read, and the honest response is to name
                # it and carry on with the rest of the folder.
                _append(job, f"skipped {Path(path).name}: {exc}")
                job.files_done += 1
                continue

            document_id = result.document_id
            n_pages = result.n_pages
            job.document_ids = sorted(set((job.document_ids or []) + [document_id]))
            job.pages_total += n_pages
            verb = "reused" if result.reused else "ingested"
            _append(job, f"{verb} {result.filename}: {n_pages} pages, "
                         f"{result.n_chars:,} chars")

        if not use_llm:
            with session_scope() as session:
                job = session.get(Job, job_id)
                _append(job, "no API key configured - stored and profiled only, "
                             "no claims extracted")
                job.files_done += 1
            continue

        with session_scope() as session:
            job = session.get(Job, job_id)
            job.stage = "extract"
            pages = list(range(min(max_pages, n_pages)))
            run = extract_document_claims(session, document_id, pages=pages)
            job.pages_done += run.pages_attempted
            job.claims += run.claims
            job.files_done += 1
            _append(
                job,
                f"extracted {Path(path).name}: {run.pages_attempted} page(s) read, "
                f"{run.proposed} claim(s) proposed -> {run.claims} grounded, "
                f"{run.refused} quarantined "
                f"[grounding precision {run.grounding_precision:.0%}]"
            )


def _relate(job_id: int, review: bool) -> None:
    """Compare across the whole corpus, not just the upload.

    The value of a new document is what it agrees and disagrees with, and that
    is a statement about every other document already stored. Restricting the
    pass to the new ids would find nothing but the document arguing with
    itself.
    """
    from .relate import relate_corpus, relate_states

    investigator = None
    if review and SETTINGS.has_llm:
        from .reconcile import investigate

        investigator = investigate

    with session_scope() as session:
        job = session.get(Job, job_id)
        job.stage = "relate"
        job.detail = "comparing every comparable pair across the corpus"

        run = relate_corpus(session, investigator=investigator)
        states = relate_states(session, generation=run.generation)

        job.relations = run.pairs + states.pairs
        job.claims = session.scalar(select(func.count(Claim.id))) or 0
        job.quarantined = session.scalar(select(func.count(Quarantine.id))) or 0
        _append(
            job,
            f"compared {run.pairs} pair(s) across {run.blocks} block(s): "
            f"{run.raw_disagreements} raw disagreement(s), "
            f"{run.explained} explained by a named axis, "
            f"{run.unresolved} unresolved"
        )
        if run.withdrawn:
            _append(job, f"{run.withdrawn} contradiction(s) withdrawn on review: "
                         + ", ".join(sorted(run.recovered_axes)))
        if states.pairs:
            _append(job, f"interval engine: {states.blocks} slot(s), "
                         f"{states.pairs} pair(s)")

        job.status = "done"
        job.stage = "done"
        job.detail = None
        job.finished_at = _now()


def job_json(job: Job) -> dict:
    total = max(job.files_total, 1)
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "detail": job.detail,
        "files": job.filenames or [],
        "documents": [f"D-{d:02d}" for d in (job.document_ids or [])],
        "filesTotal": job.files_total,
        "filesDone": job.files_done,
        "progress": round(job.files_done / total, 3),
        "pagesRead": job.pages_done,
        "claims": job.claims,
        "quarantined": job.quarantined,
        "relations": job.relations,
        "log": [line for line in (job.log or "").splitlines() if line],
        "error": job.error,
        "startedAt": job.created_at.isoformat() if job.created_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }


def reap_stale(session) -> int:
    """Mark jobs that were running when the process died.

    A job row outlives the thread that owns it. Without this a crashed run
    stays "running" forever and the interface reports progress that stopped
    happening some time last week.
    """
    stale = session.scalars(
        select(Job).where(Job.status.in_(("queued", "running")))).all()
    for job in stale:
        job.status = "failed"
        job.error = "interrupted - the server restarted while this job was running"
        job.finished_at = _now()
    return len(stale)


def clear_uploads(job_id: int) -> None:  # pragma: no cover - housekeeping
    shutil.rmtree(UPLOAD_ROOT / str(job_id), ignore_errors=True)
