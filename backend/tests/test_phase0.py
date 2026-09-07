"""End-to-end pipeline tests with the model stubbed out.

Stubbing the one LLM call keeps these free, fast and deterministic, and it
isolates what actually breaks in practice: whether a model response survives
grounding, persistence and export without a field being quietly lost or a bad
claim quietly kept. Prompt quality is not testable here and is measured against
the gold set in a later phase.
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
from fkl.report import document_report  # noqa: E402
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


def _measurement(quote: str, value: str = "81,415.38", **kwargs) -> MeasurementClaim:
    return MeasurementClaim(
        subject="Delhivery Limited",
        predicate="revenue from contracts with customers",
        qualifiers={"consolidation": "consolidated"},
        unknown_qualifiers=["segment"],
        evidence_quote=quote,
        value_raw=value,
        value_num=81415.38,
        unit_raw="INR million",
        period_raw="FY2023-24",
        confidence_extraction=0.9,
        **kwargs,
    )


def test_the_extractor_is_shown_the_rendered_page_not_the_raw_text(db, monkeypatch):
    """The model reads the laid-out page; grounding checks the raw one.

    Validating against the same rendering the model was shown would make the
    check circular — a layout mistake would be confirmed by the artefact that
    contains it.
    """
    seen: dict[int, str] = {}

    def fake_extract(page_text, page_no, profile=None, model=None):
        seen[page_no] = page_text
        return PageExtraction()

    monkeypatch.setattr(pipeline, "extract_page", fake_extract)
    with session_scope() as session:
        doc_id = ingest_pdf(session, AR_PDF, use_llm=False).document_id
    with session_scope() as session:
        pipeline.extract_document_claims(session, doc_id, pages=[35], workers=1)

    assert "[CONTEXT IN FORCE]" in seen[35]
    assert "Revenue from contracts with customers | 81,415.38 | 72,253.01" in seen[35]


def test_grounding_gates_the_insert(db, monkeypatch):
    """Claims that cannot prove themselves never reach the claims table.

    Grounding runs before the insert rather than after, so the append-only
    store holds only claims that verified, and everything refused is in
    quarantine with a reason.
    """

    def fake_extract(page_text, page_no, profile=None, model=None):
        if page_no == 1:
            raise RuntimeError("simulated model failure")
        return PageExtraction(
            measurements=[
                # Grounds against the raw page: this sentence is really on p21.
                _measurement("on consolidated basis for FY24 stood at ₹ 81,415.38 million"),
                # Plausible quote, but the value is not inside it. This is the
                # shape a hallucinated figure takes, and it must be refused.
                _measurement("registering a growth of 12.68%", value="99,999.99"),
                # Quote is not on this page at all.
                _measurement("a sentence that appears nowhere in this document"),
            ],
            states=[
                # A real quote from the wrong page. `CIN: L63090DL2011PLC221234`
                # is printed in this document, but on page 90, not page 21. It
                # must still be refused: a claim cites a page, and evidence that
                # is not on the cited page is not evidence for it.
                StateClaim(
                    subject="Delhivery Limited",
                    predicate="Corporate Identity Number",
                    evidence_quote="CIN: L63090DL2011PLC221234",
                    value_text="L63090DL2011PLC221234",
                    valid_to_is_open=True,
                )
            ],
        )

    monkeypatch.setattr(pipeline, "extract_page", fake_extract)
    with session_scope() as session:
        doc_id = ingest_pdf(session, AR_PDF, use_llm=False).document_id
    with session_scope() as session:
        run = pipeline.extract_document_claims(session, doc_id, pages=[1, 21], workers=1)

    assert run.pages_failed == 1
    assert run.proposed == 4
    assert run.measurements == 1
    assert run.refused == 3
    assert run.quarantine_reasons["value_not_in_quote"] == 1
    assert run.quarantine_reasons["quote_not_found"] == 2  # invented one, and misplaced one
    assert run.quarantine_reasons["extraction_call_failed"] == 1
    assert run.grounding_precision == 0.25

    with session_scope() as session:
        claims = session.query(Claim).all()
        assert len(claims) == 1
        claim = claims[0]
        assert claim.value_raw == "81,415.38"
        assert claim.conf_grounding > 0
        assert claim.grounding_method in {"exact", "normalized"}

        # The span points at the real characters, so a viewer can highlight the
        # document rather than searching it again. It is compared with
        # whitespace folded because the sentence wraps across a line in the PDF:
        # the span covers the newline the document really contains, while the
        # quote has the space the model wrote. Folding is the point — the span
        # must cover the true characters, not a cleaned-up copy of them.
        page = session.query(Page).filter(
            Page.document_id == doc_id, Page.page_no == 21
        ).one()
        span = page.text[claim.evidence_start : claim.evidence_end]
        assert "\n" in span  # the wrap is real
        assert " ".join(span.split()) == " ".join(claim.evidence_quote.split())

        # unknown_qualifiers survives the round trip. Were it silently emptied,
        # the comparison layer would read missing context as matching context —
        # the exact bug this design exists to prevent.
        assert claim.unknown_qualifiers == ["segment"]

        reasons = {q.reason_code for q in session.query(Quarantine).all()}
        assert reasons == {"value_not_in_quote", "quote_not_found", "extraction_call_failed"}

        report = document_report(session, doc_id)
        assert report.claims == 1
        assert report.refused == 3
        # A failed page proposed nothing, so it is counted apart from refused
        # claims rather than depressing the grounding-precision denominator.
        assert report.failed_pages == 1
        assert report.proposed == 4
        assert report.unknown_axes["segment"] == 1

        payload = export_document(session, doc_id)

    assert payload["counts"]["claims"] == 1
    assert payload["counts"]["quarantined"] == 4
    assert payload["claims"][0]["confidence"]["grounding"] > 0


def test_a_rebuilt_table_row_is_kept_but_scored_lower(db, monkeypatch):
    """Evidence assembled by our own layout pass is weaker than evidence found.

    The row is genuinely on page 35 — just not as a contiguous string, because
    the label and the two figures are drawn separately.
    """

    def fake_extract(page_text, page_no, profile=None, model=None):
        return PageExtraction(
            measurements=[
                _measurement("Revenue from contracts with customers | 81,415.38 | 72,253.01")
            ]
        )

    monkeypatch.setattr(pipeline, "extract_page", fake_extract)
    with session_scope() as session:
        doc_id = ingest_pdf(session, AR_PDF, use_llm=False).document_id
    with session_scope() as session:
        pipeline.extract_document_claims(session, doc_id, pages=[35], workers=1)

    with session_scope() as session:
        claim = session.query(Claim).one()
        assert claim.grounding_method == "reconstructed"
        assert claim.conf_grounding < 0.9
        assert any("layout-rebuilt" in r for r in claim.confidence_reasons)
