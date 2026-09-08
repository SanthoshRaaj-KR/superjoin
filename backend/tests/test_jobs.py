"""Upload a folder, get a job, watch it.

The pipeline itself is tested elsewhere. What matters here is what an upload
does around it: a folder arrives with files that are not PDFs and a scan that
cannot be read, and neither should take the batch down with it. And a job row
outlives the thread that owns it, so a run interrupted by a restart has to come
back as failed rather than as forever-running.

Nothing here spends: the endpoint is exercised with a rejected upload, and the
worker with a stub.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl import jobs  # noqa: E402
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import Document, Job  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'jobs.sqlite').as_posix()}")
    monkeypatch.setattr("fkl.db.init_db", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "UPLOAD_ROOT", tmp_path / "uploads")
    # Nothing is actually run: the queue is what is being tested, not the work.
    monkeypatch.setattr(jobs, "submit", lambda *a, **k: None)

    from fkl.api import create_app

    yield TestClient(create_app())
    reset_engine()


def test_a_folder_of_mixed_files_keeps_the_pdfs_and_ignores_the_rest(client):
    """A folder picker sends everything it finds, spreadsheets and hidden
    files included. Failing the upload over a `.DS_Store` is the difference
    between "drag your folder in" and "tidy your folder, then drag it in"."""
    response = client.post("/api/v1/documents", files=[
        ("files", ("report.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("notes.md", b"not a pdf", "text/markdown")),
        ("files", ("deck.PDF", b"%PDF-1.4 stub", "application/pdf")),
    ])
    assert response.status_code == 200
    body = response.json()
    assert body["files"] == ["report.pdf", "deck.PDF"]
    assert body["status"] == "queued"


def test_an_upload_with_no_pdfs_is_refused_rather_than_queued(client):
    response = client.post("/api/v1/documents", files=[
        ("files", ("notes.md", b"nope", "text/markdown"))])
    assert response.status_code == 400


def test_a_job_can_be_watched_and_an_unknown_one_is_a_404(client):
    job_id = client.post("/api/v1/documents", files=[
        ("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))]).json()["id"]

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["id"] == job_id
    assert body["filesTotal"] == 1
    assert body["log"] == []
    assert client.get("/api/v1/jobs/9999").status_code == 404


def test_uploaded_names_are_flattened_and_kept_per_job(client, tmp_path):
    """A folder upload sends paths like `reports/2024/annual.pdf`, and those
    separators are the caller's. Per-job directories mean two uploads of
    `report.pdf` cannot overwrite each other — a genuine re-upload is caught
    by content hash, which is what the file *is* rather than what it is
    called."""
    written = jobs.save_uploads(7, [("reports/2024/annual.pdf", b"%PDF-1.4")])
    assert written[0].name == "annual.pdf"
    assert written[0].parent.name == "7"
    assert written[0].read_bytes() == b"%PDF-1.4"


def test_a_job_interrupted_by_a_restart_comes_back_failed(client):
    """A job row outlives the thread running it. Without this a crashed run
    stays "running" forever and the panel reports progress on work that
    stopped happening some time last week."""
    with session_scope() as s:
        s.add(Job(id=50, status="running", stage="extract", filenames=["a.pdf"],
                  files_total=1))
        s.flush()

    with session_scope() as s:
        assert jobs.reap_stale(s) == 1

    with session_scope() as s:
        job = s.get(Job, 50)
        assert job.status == "failed"
        assert "restarted" in job.error
        assert job.finished_at is not None


def test_a_scanned_pdf_is_named_and_skipped_rather_than_failing_the_batch(
        client, tmp_path, monkeypatch):
    """OCR is out of scope and always has been. The honest response to a scan
    is to say which file it was and carry on with the rest of the folder."""
    from fkl.ingest import NoTextLayer

    def refuse(session, path, **kwargs):
        raise NoTextLayer(f"{Path(path).name} has little or no extractable text")

    monkeypatch.setattr("fkl.ingest.ingest_pdf", refuse)
    monkeypatch.setattr("fkl.config.SETTINGS", type(
        "S", (), {"has_llm": False, "db_url": ""})())

    with session_scope() as s:
        s.add(Job(id=60, status="queued", stage="queued",
                  filenames=["scan.pdf"], files_total=1))
        s.flush()

    jobs._ingest_and_extract(60, [str(tmp_path / "scan.pdf")], 5)

    with session_scope() as s:
        job = s.get(Job, 60)
        assert job.files_done == 1
        assert any("skipped" in line for line in (job.log or "").splitlines())


def test_page_progress_is_committed_while_the_extraction_is_still_running(
        client, tmp_path, monkeypatch):
    """The bug this exists to stop coming back.

    Extraction held one session open for the whole document, so nothing it
    counted reached the table until the last page returned. The panel polled
    every two seconds for ten minutes and was told the same thing every time —
    stage "extract", zero pages, zero claims — which from the outside is
    indistinguishable from a hung process. Progress is only progress if a
    reader can see it happen.

    So the assertion is made from a *different* session, mid-run: what the
    poller would see, at the moment it would see it.
    """
    from fkl.pipeline import ExtractionRun

    seen = []

    def fake_ingest(session, path, **kwargs):
        session.add(Document(id=1, sha256="c" * 64, filename="a.pdf",
                             source_path=path, n_pages=3, n_chars=99,
                             as_of_date=date(2024, 3, 31)))
        session.flush()
        return type("R", (), {"document_id": 1, "n_pages": 3, "n_chars": 99,
                              "filename": "a.pdf", "reused": False})()

    def fake_extract(session, document_id, *, pages, on_page=None, **kwargs):
        for done in (1, 2, 3):
            on_page(done, len(pages))
            # A reader's view, not the worker's.
            with session_scope() as other:
                row = other.get(Job, 70)
                seen.append((row.pages_done, row.detail))
        return ExtractionRun(document_id=document_id, pages_attempted=3,
                             measurements=5, proposed=6, refused=1)

    monkeypatch.setattr("fkl.ingest.ingest_pdf", fake_ingest)
    monkeypatch.setattr("fkl.pipeline.extract_document_claims", fake_extract)
    monkeypatch.setattr(jobs, "SETTINGS", type("S", (), {"has_llm": True})())

    with session_scope() as s:
        s.add(Job(id=70, status="queued", stage="queued",
                  filenames=["a.pdf"], files_total=1))
        s.flush()

    jobs._ingest_and_extract(70, [str(tmp_path / "a.pdf")], 3)

    assert [n for n, _ in seen] == [1, 2, 3]
    assert "1/3" in seen[0][1] and "3/3" in seen[2][1]

    with session_scope() as s:
        job = s.get(Job, 70)
        assert job.pages_done == 3      # not 6: the final write sets, not adds
        assert job.claims == 5
        assert job.pages_total == 3     # pages this job reads, not pages it has


def test_the_progress_bar_measures_pages_once_ingest_knows_how_many(client):
    """Files are the coarse unit and a one-file upload has exactly two
    file-level progress values, 0 and 1. A bar drawn from those does not move
    for the entire run, which is the same failure as not drawing one."""
    job = Job(id=80, status="running", stage="extract", filenames=["a.pdf"],
              files_total=1, files_done=0, pages_total=40, pages_done=10)
    assert jobs.job_json(job)["progress"] == 0.25

    # Before ingest has said how many pages there are, files are all there is.
    job.pages_total = 0
    assert jobs.job_json(job)["progress"] == 0.0

    # And a finished job is finished. Pages can come in under the estimate — a
    # document shorter than the cap, a scan skipped, pages already extracted on
    # an earlier run — and a completed run reporting 82% is describing the
    # estimate rather than the work.
    job.pages_total, job.pages_done, job.status = 40, 33, "done"
    assert jobs.job_json(job)["progress"] == 1.0
