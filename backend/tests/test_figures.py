"""The figure pass: reading charts that text extraction flattened.

The vision call is stubbed. What is tested is the contract around it — when it
runs, what happens to its output, and above all that it is held to the same
standard of proof as the text pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl import pipeline  # noqa: E402
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.ingest import ingest_pdf  # noqa: E402
from fkl.llm.figures import render_page_png, should_run  # noqa: E402
from fkl.models import Claim, Quarantine  # noqa: E402
from fkl.schemas import MeasurementClaim, PageExtraction  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DECK = REPO / "starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf"


@pytest.fixture()
def db(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'figures.sqlite').as_posix()}")
    yield
    reset_engine()


def test_routing_targets_unbound_pages_only():
    """A chart page earns the second pass; a table page does not.

    The threshold is on the share of figures left unattached, not on anything
    resembling chart recognition.
    """
    assert should_run(26, 4)      # deck chart page: almost nothing bound
    assert not should_run(2, 40)  # financial table: the layout pass bound it
    assert not should_run(3, 1)   # too few numbers to be worth a call
    assert not should_run(0, 0)
    assert not should_run(None, None)


def test_page_renders_to_a_real_png():
    png = render_page_png(str(DECK), 8, dpi=90)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _chart_claim(predicate: str, value: str, period: str) -> MeasurementClaim:
    return MeasurementClaim(
        subject="Delhivery Limited",
        predicate=predicate,
        evidence_quote=value,
        value_raw=value,
        value_num=float(value.replace(",", "")),
        unit_raw="₹ Cr",
        period_raw=period,
        confidence_extraction=1.0,  # vision reports certainty it cannot have
    )


def test_figure_claims_are_grounded_like_any_other(db, monkeypatch):
    """The property that makes the second pass safe to have.

    Vision may propose which bar a number sits on. It may not introduce a
    number. `9,999` is not printed on page 8, so it is quarantined exactly as a
    hallucinated figure from the text pass would be.
    """
    monkeypatch.setattr(pipeline, "extract_page", lambda *a, **k: PageExtraction())
    monkeypatch.setattr(
        pipeline,
        "extract_figures",
        lambda *a, **k: PageExtraction(
            measurements=[
                _chart_claim("Express Parcel revenue", "5,077", "FY24"),  # really there
                _chart_claim("Invented series", "9,999", "FY24"),         # not there
            ]
        ),
    )

    with session_scope() as session:
        doc_id = ingest_pdf(session, DECK, use_llm=False).document_id
    with session_scope() as session:
        run = pipeline.extract_document_claims(session, doc_id, pages=[8], workers=1)

    assert run.figure_pages_read == 1
    assert run.figure_claims == 1
    assert run.quarantine_reasons["quote_not_found"] == 1

    with session_scope() as session:
        kept = session.query(Claim).one()
        assert kept.value_raw == "5,077"
        assert kept.source == "figure"
        # Self-reported certainty is capped: the model can be sure it read the
        # number and still have swapped two series in the legend.
        assert kept.conf_extraction == pipeline.FIGURE_CONFIDENCE_CAP
        assert any("binding is not" in r for r in kept.confidence_reasons)

        refused = session.query(Quarantine).one()
        assert refused.payload["value_raw"] == "9,999"


def test_a_bound_figure_supersedes_the_unbound_text_reading(db, monkeypatch):
    """Both passes see the same number; only one of them knows what it means.

    On a chart page the text pass reports figures it could not attach to a
    series or a period. Keeping both readings would hand the comparison layer
    two accounts of one bar to disagree about.
    """
    monkeypatch.setattr(
        pipeline,
        "extract_page",
        lambda *a, **k: PageExtraction(
            measurements=[
                MeasurementClaim(
                    subject="Delhivery Limited",
                    predicate="unlabelled figure",
                    evidence_quote="5,077",
                    value_raw="5,077",
                    value_num=5077.0,
                    unit_raw="",
                    period_raw="",
                    unknown_qualifiers=["period"],
                ),
                MeasurementClaim(
                    subject="Delhivery Limited",
                    predicate="Adjusted EBITDA margin",
                    evidence_quote="4.2%",
                    value_raw="4.2",
                    value_num=4.2,
                    unit_raw="%",
                    period_raw="Q4 FY24",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "extract_figures",
        lambda *a, **k: PageExtraction(
            measurements=[_chart_claim("Express Parcel revenue", "5,077", "FY24")]
        ),
    )

    with session_scope() as session:
        doc_id = ingest_pdf(session, DECK, use_llm=False).document_id
    with session_scope() as session:
        run = pipeline.extract_document_claims(session, doc_id, pages=[8, 12], workers=1)

    assert run.superseded_by_figures >= 1

    with session_scope() as session:
        readings = session.query(Claim).filter(Claim.value_raw == "5,077").all()
        assert len(readings) == 1
        assert readings[0].source == "figure"
        assert readings[0].period_raw == "FY24"

        # A text claim the figure pass said nothing about is untouched.
        assert session.query(Claim).filter(Claim.predicate == "Adjusted EBITDA margin").count()


def test_the_figure_pass_can_be_switched_off(db, monkeypatch):
    monkeypatch.setattr(pipeline, "extract_page", lambda *a, **k: PageExtraction())

    def fail(*a, **k):
        raise AssertionError("figure pass ran despite use_figures=False")

    monkeypatch.setattr(pipeline, "extract_figures", fail)

    with session_scope() as session:
        doc_id = ingest_pdf(session, DECK, use_llm=False).document_id
    with session_scope() as session:
        run = pipeline.extract_document_claims(
            session, doc_id, pages=[8], workers=1, use_figures=False
        )
    assert run.figure_pages_read == 0
