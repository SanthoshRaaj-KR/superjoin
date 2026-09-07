"""Phase 0 tests: the pipeline runs end to end with the model stubbed out.

Stubbing the one LLM call keeps these tests free, fast and deterministic, and it
checks the thing that actually breaks in practice — that a model response is
persisted and re-exported without losing a field. Prompt quality is not testable
here and is measured against the gold set in a later phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl import pipeline  # noqa: E402
from fkl.cli import parse_page_spec  # noqa: E402
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.export import export_document  # noqa: E402
from fkl.ingest import ingest_pdf  # noqa: E402
from fkl.models import Claim, Page, Quarantine  # noqa: E402
from fkl.pdf.extract import extract_document, has_text_layer, profiling_sample  # noqa: E402
from fkl.schemas import MeasurementClaim, PageExtraction, StateClaim  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
AR_PDF = REPO / "starter-datasets/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf"


@pytest.fixture()
def db(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    yield
    reset_engine()


def test_extract_reads_pages_and_hashes_them():
    doc = extract_document(AR_PDF)
    assert doc.n_pages == 100
    assert has_text_layer(doc)
    assert len({p.sha256 for p in doc.pages}) > 90  # pages are genuinely distinct
    assert doc.sha256 == extract_document(AR_PDF).sha256  # stable


def test_profiling_sample_reaches_past_the_contents_page():
    """The annual report opens with a table of contents.

    Sampling only the first pages would hand the profiler a list of section
    headings, which is why the sample also pulls from the body.
    """
    doc = extract_document(AR_PDF)
    sample = profiling_sample(doc)
    assert "[PAGE 0]" in sample
    assert "[PAGE 25]" in sample
    assert AR_PDF.name not in sample  # filename must never leak into the prompt


def test_ingest_is_idempotent(db):
    with session_scope() as session:
        first = ingest_pdf(session, AR_PDF, use_llm=False)
        assert first.reused is False
    with session_scope() as session:
        second = ingest_pdf(session, AR_PDF, use_llm=False)
        assert second.reused is True
        assert second.document_id == first.document_id
        assert session.query(Page).count() == 100


def test_page_spec_parsing():
    assert parse_page_spec("0-3,7") == [0, 1, 2, 3, 7]
    assert parse_page_spec(None) is None


def test_pipeline_persists_claims_and_survives_a_failing_page(db, monkeypatch):
    calls: list[int] = []

    def fake_extract(page_text, page_no, profile=None, model=None):
        calls.append(page_no)
        if page_no == 1:
            raise RuntimeError("simulated model failure")
        return PageExtraction(
            measurements=[
                MeasurementClaim(
                    subject="Delhivery Limited",
                    predicate="revenue from services",
                    qualifiers={"consolidation": "consolidated"},
                    unknown_qualifiers=["segment"],
                    evidence_quote="81,415.38",
                    value_raw="81,415.38",
                    value_num=81415.38,
                    unit_raw="INR million",
                    period_raw="FY2023-24",
                    confidence_extraction=0.9,
                )
            ],
            states=[
                StateClaim(
                    subject="Suvir Suren Sujan",
                    predicate="Director",
                    evidence_quote="Suvir Suren Sujan",
                    value_text="Suvir Suren Sujan",
                    valid_to_is_open=True,
                )
            ],
        )

    monkeypatch.setattr(pipeline, "extract_page", fake_extract)

    with session_scope() as session:
        doc_id = ingest_pdf(session, AR_PDF, use_llm=False).document_id

    with session_scope() as session:
        run = pipeline.extract_document_claims(session, doc_id, pages=[0, 1, 2], workers=2)

    assert sorted(calls) == [0, 1, 2]
    assert run.pages_failed == 1
    assert run.measurements == 2 and run.states == 2  # pages 0 and 2 succeeded

    with session_scope() as session:
        # A failed page is recorded, not lost.
        q = session.query(Quarantine).one()
        assert q.reason_code == "extraction_call_failed"
        assert q.page_no == 1

        # unknown_qualifiers survives the round trip. If it silently became an
        # empty list, the comparison layer would later treat missing context as
        # matching context, which is the bug this whole design exists to avoid.
        claim = session.query(Claim).filter(Claim.claim_type == "measurement").first()
        assert claim.unknown_qualifiers == ["segment"]
        assert claim.qualifiers == {"consolidation": "consolidated"}

        payload = export_document(session, doc_id)

    assert payload["counts"]["claims"] == 4
    assert payload["counts"]["with_unknown_qualifiers"] == 2
    assert payload["counts"]["quarantined"] == 1
    exported = payload["claims"][0]
    assert exported["confidence"]["extraction"] == 0.9
    assert exported["confidence"]["grounding"] == 0.0  # not yet earned; set in Phase 1
