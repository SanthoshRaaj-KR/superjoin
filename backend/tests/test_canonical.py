"""Canonicalisation: a grounded claim becoming a comparable one.

The demonstration case is built here end to end. Two claims taken verbatim from
two different documents — an earnings deck writing `₹8,142 Cr` and an annual
report writing `81,415.38` under a page-level `(₹ in million)` declaration —
have to arrive at one entity, one metric, one interval and one number. Each
normaliser is tested on its own elsewhere; this checks they compose.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl import metrics as metrics_module  # noqa: E402
from fkl.canonical import canonicalize, context_hint  # noqa: E402
from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.metrics import MetricJudgement  # noqa: E402
from fkl.models import Claim  # noqa: E402


@pytest.fixture
def session(tmp_path, monkeypatch):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    monkeypatch.setattr(
        metrics_module, "embed_texts",
        lambda _s, texts, model=None: {t.strip(): t.strip() for t in texts if t.strip()},
    )
    monkeypatch.setattr(metrics_module, "cosine", lambda a, b: 1.0 if a == b else 0.70)
    monkeypatch.setattr(
        metrics_module, "_adjudicate",
        lambda a, b, dim: MetricJudgement(
            same_metric=True, confidence=0.9, reason="same line item"
        ),
    )
    with session_scope() as s:
        yield s
    reset_engine()


def make(**kwargs) -> Claim:
    base = dict(
        document_id=1,
        page_no=0,
        claim_type="measurement",
        subject="Delhivery Limited",
        predicate="Revenue from services",
        qualifiers={},
        unknown_qualifiers=[],
        evidence_quote="Revenue from services 8,142",
        confidence_reasons=[],
    )
    base.update(kwargs)
    return Claim(**base)


def test_two_documents_two_units_one_number(session):
    """The corroboration case, composed.

    The deck says `₹8,142 Cr`. The annual report says `81,415.38` in a table
    under `(All amounts in Indian Rupees in million)`, so its unit reaches it
    through the context frame rather than the cell. Both must land on the same
    entity, the same metric and the same quantity of rupees.
    """
    deck = make(value_raw="8,142", value_num=8142.0, unit_raw="₹ Cr", period_raw="FY24")
    report = make(
        predicate="Revenue from contracts with customers",
        value_raw="81,415.38",
        value_num=81415.38,
        unit_raw=None,
        qualifiers={"currency": "INR", "scale": "million"},
        period_raw="March 31, 2024",
        evidence_quote="Revenue from contracts with customers | 81,415.38",
    )
    for claim in (deck, report):
        canonicalize(session, claim, hint="for the year ended March 31, 2024")

    assert deck.entity_id == report.entity_id
    assert deck.metric_id == report.metric_id
    assert deck.period_label == report.period_label == "FY2024"
    assert (deck.period_start, deck.period_end) == (date(2023, 4, 1), date(2024, 3, 31))

    # 0.006% apart — rounding in the deck, not a disagreement.
    assert abs(deck.value_canonical - report.value_canonical) / report.value_canonical < 0.001


def test_the_raw_strings_survive_canonicalisation(session):
    """A comparison is made in base units and explained in the document's own.

    Overwriting `value_raw` with the canonical number would make the verdict
    unquotable back to the reader.
    """
    claim = make(value_raw="8,142", value_num=8142.0, unit_raw="₹ Cr", period_raw="FY24")
    canonicalize(session, claim)
    assert claim.value_raw == "8,142"
    assert claim.unit_raw == "₹ Cr"
    assert claim.period_raw == "FY24"
    assert claim.value_canonical == 8142 * 1e7


def test_an_unparseable_unit_lowers_confidence_rather_than_failing_the_claim(session):
    """Grounding asks whether a claim came from the page — a no means it should
    not exist. Normalisation asks whether it can be compared, and a no there is
    a perfectly good claim that simply will not join a comparison."""
    claim = make(value_raw="5", value_num=5.0, unit_raw=None, period_raw="FY24")
    canonicalize(session, claim)
    assert claim.value_canonical is None
    assert claim.unit_dimension is None
    assert claim.conf_normalization == 0.0
    assert claim.entity_id is not None  # the claim is intact and still resolvable


def test_a_bare_year_end_date_carries_its_ambiguity_into_storage(session):
    """Without a section declaration the period is a reading, not a fact, and the
    flag is what lets a later verdict say so."""
    claim = make(value_raw="1", value_num=1.0, unit_raw="₹ Cr",
                 period_raw="March 31, 2024")
    canonicalize(session, claim, hint="")
    assert claim.period_label == "FY2024"
    assert claim.period_ambiguous

    resolved = make(value_raw="1", value_num=1.0, unit_raw="₹ Cr",
                    period_raw="March 31, 2024")
    canonicalize(session, resolved, hint="for the year ended March 31, 2024")
    assert not resolved.period_ambiguous


def test_a_state_claim_is_not_penalised_for_having_no_unit(session):
    """Roles and addresses have no unit. Demanding one would report every state
    claim in the corpus as unnormalised."""
    claim = make(
        claim_type="state",
        subject="Sunil Kumar Bansal",
        predicate="Company Secretary",
        value_text="resigned",
        value_raw=None,
        period_raw=None,
        evidence_quote="Sunil Kumar Bansal resigned w.e.f. May 31, 2023",
    )
    canonicalize(session, claim)
    assert claim.conf_normalization == 1.0
    assert claim.entity_id is not None


def test_the_page_context_supplies_the_hint():
    """The wording that resolves a period is already captured by the context
    layer, so it is reused rather than detected again."""
    frames = [
        {"evidence": ['period=March 31, 2024 (from "To the Consolidated Financial '
                      'Statements for the year ended March 31, 2024")']},
        {"evidence": ['scale=million (from "(₹ in Million)")']},
    ]
    hint = context_hint(frames)
    assert "year ended" in hint
    assert context_hint(None) == ""
