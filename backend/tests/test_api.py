"""The HTTP surface, and the contract the UI was built against.

The UI is a separate artefact with its own vocabulary — `scope`, `basis`,
`kind: "tenure"` — and these tests pin the translation. A field renamed here
without the UI knowing does not raise anything; a screen just renders blanks,
which is the failure mode worth having tests for.

Everything runs against a small in-memory corpus rather than the real store, so
the shape is asserted rather than the contents of one particular extraction run.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.models import (  # noqa: E402
    Claim,
    Document,
    Entity,
    Metric,
    Page,
    Quarantine,
    Relation,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'api.sqlite').as_posix()}"
    init_db(url)
    monkeypatch.setattr("fkl.db.init_db", lambda *a, **k: None)

    with session_scope() as s:
        s.add(Document(id=1, sha256="a" * 64, filename="02-annual-report-fy24.pdf",
                       source_path="a.pdf", n_pages=100, n_chars=1000,
                       primary_entity="Delhivery Limited", doc_type="annual_report",
                       as_of_date=date(2024, 8, 8)))
        s.add(Document(id=2, sha256="b" * 64, filename="03-earnings-deck.pdf",
                       source_path="b.pdf", n_pages=27, n_chars=500,
                       primary_entity="Delhivery Limited", doc_type="deck"))
        s.add(Entity(id=1, canonical_name="Delhivery Limited"))
        s.add(Entity(id=2, canonical_name="Mr. Sunil Kumar Bansal"))
        s.add(Metric(id=1, canonical_name="revenue", dimension="currency"))
        s.add(Metric(id=2, canonical_name="Company Secretary", dimension=None))
        s.add(Page(document_id=1, page_no=21, sha256="p" * 64, text="t",
                   rendered_text="t", n_chars=1,
                   context_json=[{"heading_path": ["Financials", "Standalone"]}]))
        s.flush()

        base = dict(
            document_id=1, page_no=21, claim_type="measurement",
            subject="Delhivery Limited", predicate="revenue from services",
            entity_id=1, metric_id=1, unit_dimension="currency",
            unit_currency="INR", unit_scale=1e6, evidence_quote="Revenue | 74,540.82",
            unknown_qualifiers=[], confidence_reasons=[], modality="actual",
            period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
            period_label="FY2024", period_granularity="year",
            conf_extraction=0.9, conf_grounding=1.0, conf_normalization=0.8,
            conf_entity_match=1.0,
        )
        s.add(Claim(id=1, value_raw="74,540.82", value_num=74540.82,
                    value_canonical=74540820000.0,
                    qualifiers={"consolidation": "standalone"}, **base))
        s.add(Claim(id=2, value_raw="81,415.38", value_num=81415.38,
                    value_canonical=81415380000.0,
                    qualifiers={"consolidation": "consolidated"}, **base))
        s.add(Claim(
            id=3, document_id=1, page_no=90, claim_type="state",
            subject="Mr. Sunil Kumar Bansal", predicate="Company Secretary",
            value_text="Company Secretary", entity_id=2, metric_id=2,
            evidence_quote="Mr. Sunil Kumar Bansal | Company Secretary",
            qualifiers={}, unknown_qualifiers=[], confidence_reasons=[],
            modality="actual", valid_to=date(2023, 5, 31), valid_to_is_open=False,
            conf_extraction=0.9, conf_grounding=1.0, conf_normalization=0.5,
            conf_entity_match=0.9,
        ))
        s.add(Quarantine(document_id=2, page_no=5, stage="grounding",
                         reason_code="quote_not_found", detail="not on page",
                         payload={"predicate": "shipments"}))
        s.flush()

    from fkl.api import create_app

    yield TestClient(create_app())
    reset_engine()


def test_facts_carry_the_fields_every_screen_reads(client):
    facts = {f["id"]: f for f in client.get("/api/v1/facts").json()}
    assert len(facts) == 3
    fact = facts["f-1"]
    for key in ("entity", "metric", "value", "unit", "period", "scope", "basis",
                "kind", "doc", "page", "confidence", "quote", "claim", "section"):
        assert key in fact, key
    assert fact["unit"] == "INR_M"
    assert fact["scope"] == "standalone"
    assert fact["doc"] == "D-01"
    assert fact["section"] == "Financials › Standalone"


def test_confidence_is_the_weakest_stage_and_keeps_its_breakdown(client):
    """Decomposed confidence is the point, and the UI has one slot for it. A
    mean would let a badly normalised claim hide behind perfect grounding."""
    fact = next(f for f in client.get("/api/v1/facts").json() if f["id"] == "f-1")
    assert fact["confidence"] == 0.8  # the lowest of 0.9 / 1.0 / 0.8 / 1.0
    assert fact["confidenceParts"] == {
        "extraction": 0.9, "grounding": 1.0,
        "normalization": 0.8, "entity_match": 1.0,
    }


def test_a_role_claim_is_a_tenure_not_a_measurement(client):
    """The discriminator is the document's organisation, not the claim's
    resolved entity — a role resolves its entity to the person, so comparing
    the subject against that matches every time and the timeline shows
    nothing."""
    fact = next(f for f in client.get("/api/v1/facts").json() if f["id"] == "f-3")
    assert fact["kind"] == "tenure"
    assert fact["role"] == "Company Secretary"
    assert fact["holder"] == "Mr. Sunil Kumar Bansal"
    assert fact["end"] == "2023-05-31"
    assert fact["start"] is None      # the page records only the resignation
    assert fact["entity"] == "Delhivery Limited"


def test_compare_recomputes_rather_than_reading_a_stored_verdict(client):
    body = {"a": "f-1", "b": "f-2", "maskedAxes": []}
    result = client.post("/api/v1/compare", json=body).json()
    assert result["verdict"] == "CONTEXTUAL"
    assert result["differingAxis"]["key"] == "scope"
    assert result["differingAxis"]["a"] == "standalone"


def test_masking_an_axis_flips_the_verdict_through_the_api(client):
    """The counterfactual, over HTTP. The UI's `scope` is the corpus's
    `consolidation`, and the translation lives in the API rather than in the
    gate — putting a fixed axis vocabulary into ``compare`` would defeat the
    open qualifier dict it is built on."""
    off = client.post("/api/v1/compare",
                      json={"a": "f-1", "b": "f-2", "maskedAxes": []}).json()
    on = client.post("/api/v1/compare",
                     json={"a": "f-1", "b": "f-2", "maskedAxes": ["scope"]}).json()
    assert off["verdict"] == "CONTEXTUAL"
    assert on["verdict"] == "CONTRADICTS"
    assert [x for x in on["axes"] if x["key"] == "scope"][0]["status"] == "masked"


def test_compare_applies_context_a_review_recovered(client):
    """Otherwise the pair list and the panel it opens disagree: one says
    CONTEXTUAL because the corpus run recovered an axis, the other recomputes
    from the bare claims and says CONTRADICTS."""
    with session_scope() as s:
        s.add(Relation(
            claim_a_id=1, claim_b_id=2, verdict="CONTEXTUAL",
            axis="measure_basis", explanation="…", differing_axes=[],
            missing_axes=[], values_differ=True, generation=1,
            reconsidered=True, original_verdict="CONTRADICTS",
            recovery_axis="measure_basis", recovery_method="investigated",
            recovery_reason="the page prints EBITDA and Adj. EBITDA",
            recovery_confidence=0.9,
            recovery_a_value="EBITDA margin", recovery_b_value="Adj. EBITDA margin",
        ))
        s.flush()

    result = client.post("/api/v1/compare",
                         json={"a": "f-1", "b": "f-2", "maskedAxes": []}).json()
    assert result["recovered"]["axis"] == "measure_basis"
    assert result["recovered"]["originalVerdict"] == "CONTRADICTS"
    assert result["verdict"] == "CONTEXTUAL"
    # and the recovered axis is a row in the table like any other
    assert any(x["key"] == "measure_basis" for x in result["axes"])

    # Masking the recovered axis puts the contradiction back, which is the
    # honest way to show what the review actually did.
    masked = client.post(
        "/api/v1/compare",
        json={"a": "f-1", "b": "f-2", "maskedAxes": ["measure_basis", "scope"]},
    ).json()
    assert masked["verdict"] == "CONTRADICTS"


def test_a_recovery_stored_the_other_way_round_is_not_swapped(client):
    """Relations are stored with a fixed a/b order and the UI may ask either
    way. Handing back the wrong side's label would silently mislabel both."""
    with session_scope() as s:
        s.add(Relation(
            claim_a_id=1, claim_b_id=2, verdict="CONTEXTUAL", axis="measure_basis",
            explanation="…", differing_axes=[], missing_axes=[],
            values_differ=True, generation=1, reconsidered=True,
            original_verdict="CONTRADICTS", recovery_axis="measure_basis",
            recovery_method="investigated", recovery_reason="r",
            recovery_confidence=0.9,
            recovery_a_value="EBITDA margin", recovery_b_value="Adj. EBITDA margin",
        ))
        s.flush()

    forward = client.post("/api/v1/compare",
                          json={"a": "f-1", "b": "f-2"}).json()["recovered"]
    backward = client.post("/api/v1/compare",
                           json={"a": "f-2", "b": "f-1"}).json()["recovered"]
    assert forward["a"] == "EBITDA margin"
    assert backward["a"] == "Adj. EBITDA margin"


def test_pairs_lead_with_the_contradictions_the_system_withdrew(client):
    """The one thing in the corpus that shows the pipeline correcting itself."""
    with session_scope() as s:
        s.add(Relation(claim_a_id=1, claim_b_id=2, verdict="CORROBORATES",
                       axis=None, explanation="…", differing_axes=[],
                       missing_axes=[], values_differ=False, generation=1))
        s.add(Relation(claim_a_id=2, claim_b_id=1, verdict="CONTEXTUAL",
                       axis="measure_basis", explanation="…", differing_axes=[],
                       missing_axes=[], values_differ=True, generation=1,
                       reconsidered=True, original_verdict="CONTRADICTS",
                       recovery_axis="measure_basis"))
        s.flush()

    pairs = client.get("/api/v1/pairs").json()
    assert pairs[0]["reconsidered"] is True
    assert pairs[0]["originalVerdict"] == "CONTRADICTS"


def test_corpus_reports_the_reduction_and_the_axes_in_ui_names(client):
    with session_scope() as s:
        s.add(Relation(claim_a_id=1, claim_b_id=2, verdict="CONTEXTUAL",
                       axis="consolidation", explanation="…", differing_axes=[],
                       missing_axes=[], values_differ=True, generation=1))
        s.flush()

    body = client.get("/api/v1/corpus").json()
    assert body["documents"] == 2
    assert body["claims"] == 3
    assert body["quarantined"] == 1
    assert body["contextual"] == 1
    assert [a["axis"] for a in body["axes"]] == ["scope"]
    assert body["axes"][0]["values"] == ["consolidated", "standalone"]


def test_quarantine_is_served_with_its_reason_code(client):
    rows = client.get("/api/v1/quarantine").json()
    assert rows[0]["reason"] == "quote_not_found"
    assert rows[0]["doc"] == "D-02"


def test_as_of_is_a_query_not_a_stored_field(client):
    assert client.get("/api/v1/as-of", params={"date": "2023-01-01"}).json()
    assert client.get("/api/v1/as-of", params={"date": "2024-01-01"}).json() == []
    assert client.get("/api/v1/as-of", params={"date": "nonsense"}).status_code == 400


def test_an_unknown_claim_is_a_404_not_a_crash(client):
    assert client.post("/api/v1/compare",
                       json={"a": "f-999", "b": "f-1"}).status_code == 404
    assert client.post("/api/v1/compare",
                       json={"a": "nonsense", "b": "f-1"}).status_code == 404


def test_a_pair_that_is_half_tenure_does_not_break_the_listing(client):
    """One document records a role with dates, another records the same role
    with none, so a pair is routinely half tenure and half plain state.
    Reading the second side's holder off a fact that has none took out the
    whole listing with a 500 — and it only appeared when a document the system
    had not seen was ingested."""
    with session_scope() as s:
        s.add(Claim(
            id=4, document_id=2, page_no=29, claim_type="state",
            subject="Mr. Sunil Kumar Bansal", predicate="Company Secretary",
            value_text="Company Secretary", entity_id=2, metric_id=2,
            evidence_quote="q", qualifiers={}, unknown_qualifiers=[],
            confidence_reasons=[], modality="actual", valid_to_is_open=True,
        ))
        s.add(Relation(claim_a_id=3, claim_b_id=4, verdict="CORROBORATES",
                       axis=None, explanation="…", differing_axes=[],
                       missing_axes=[], values_differ=False, generation=1))
        s.flush()

    rows = client.get("/api/v1/pairs")
    assert rows.status_code == 200
    labels = [p["label"] for p in rows.json()]
    assert any("Company Secretary" in l for l in labels)
