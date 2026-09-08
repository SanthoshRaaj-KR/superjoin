"""Entity resolution.

The failure this guards against is asymmetric. A missed merge costs one
comparison; a wrong merge fuses two different subjects permanently and then
reports confident contradictions between claims that were never about the same
thing. So the tests here care more about what must *not* merge than about what
must.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.db import init_db, reset_engine, session_scope  # noqa: E402
from fkl.entities import (  # noqa: E402
    Identifier,
    cin_key,
    find_identifiers,
    normalize_name,
    resolve_entity,
)


# --- names ------------------------------------------------------------------


def test_legal_forms_carry_no_identity():
    assert normalize_name("Delhivery Limited") == "delhivery"
    assert normalize_name("Delhivery Ltd.") == "delhivery"
    assert normalize_name("Delhivery Private Limited") == "delhivery"
    assert normalize_name("  DELHIVERY  LIMITED ") == "delhivery"


def test_honorifics_are_stripped_from_people():
    assert normalize_name("Mr. Sunil Kumar Bansal") == "sunil kumar bansal"
    assert normalize_name("Ms Madhulika Rawat") == "madhulika rawat"


def test_a_middle_name_is_not_silently_discarded():
    """`Suvir Suren Sujan` and `Suvir Sujan` are the same director, and the DIN
    proves it. Name normalisation must *not*, because two people can share a
    first and last name and a wrong merge is unrecoverable."""
    assert normalize_name("Suvir Suren Sujan") != normalize_name("Suvir Sujan")


# --- identifiers ------------------------------------------------------------


def test_a_cin_survives_the_listing_prefix_change():
    """Delhivery's CIN in the 2022 prospectus and the FY24 annual report.

    The `U` to `L` flip happened at IPO. Same company, and the registration
    number says so.
    """
    unlisted = cin_key("U63090DL2011PLC221234")
    listed = cin_key("L63090DL2011PLC221234")
    assert unlisted == listed == "DL2011221234"


def test_a_different_registration_number_is_a_different_company():
    """`U63090DL2011PTC409002` shares the industry code, the state and the year
    with Delhivery's own CIN and is a different company entirely.

    This is why the key cannot be "the CIN minus its first letter".
    """
    assert cin_key("U63090DL2011PTC409002") != cin_key("L63090DL2011PLC221234")


def test_identifiers_are_found_in_running_text():
    found = find_identifiers(
        "CIN: L63090DL2011PLC221234 ... (DIN: 01173669) ... ISIN INE148O01028"
    )
    kinds = {i.kind: i.value for i in found}
    assert kinds["cin"] == "L63090DL2011PLC221234"
    assert kinds["din"] == "01173669"
    assert kinds["isin"] == "INE148O01028"


# --- resolution -------------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    reset_engine()
    init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    with session_scope() as s:
        yield s
    reset_engine()


def test_two_spellings_of_one_company_resolve_to_one_entity(session):
    a = resolve_entity(session, "Delhivery Limited")
    b = resolve_entity(session, "Delhivery Ltd.")
    assert a.entity_id == b.entity_id
    assert a.created and not b.created


def test_an_identifier_merges_names_that_do_not_look_alike(session):
    """The case string similarity cannot reach.

    The prospectus names a director in full and the annual report abbreviates
    him. Nothing about the two strings forces a match — the DIN does.
    """
    din = [Identifier("din", "01173669", "01173669")]
    full = resolve_entity(session, "Suvir Suren Sujan", entity_type="person",
                          identifiers=din)
    short = resolve_entity(session, "Suvir Sujan", entity_type="person",
                           identifiers=din)

    assert short.entity_id == full.entity_id
    assert short.method == "identifier"
    assert not short.created


def test_the_registry_learns_the_new_spelling(session):
    """After an identifier match, the new name resolves on its own.

    This is what makes the registry cheapen over time rather than re-deriving
    the same match on every document.
    """
    din = [Identifier("din", "01173669", "01173669")]
    first = resolve_entity(session, "Suvir Suren Sujan", entity_type="person",
                           identifiers=din)
    resolve_entity(session, "Suvir Sujan", entity_type="person", identifiers=din)

    again = resolve_entity(session, "Suvir Sujan", entity_type="person")
    assert again.entity_id == first.entity_id
    assert again.method == "identifier"  # how the alias was originally learned


def test_the_cin_change_at_ipo_does_not_split_the_company(session):
    """The prospectus and the annual report disagree about Delhivery's CIN.

    Without identifier normalisation this produces two Delhiverys, and every
    cross-document comparison in the project silently returns nothing.
    """
    prospectus = find_identifiers("CIN: U63090DL2011PLC221234")
    report = find_identifiers("CIN: L63090DL2011PLC221234")

    a = resolve_entity(session, "Delhivery Limited", identifiers=prospectus)
    b = resolve_entity(session, "Delhivery Limited", identifiers=report)
    assert a.entity_id == b.entity_id


def test_unrelated_names_stay_unrelated(session):
    """With the candidate step off, two names that are not normalised-equal are
    two entities. That is the floor the adjudicated path builds on."""
    a = resolve_entity(session, "Delhivery Limited", adjudicate=False)
    b = resolve_entity(session, "Spoton Logistics Private Limited", adjudicate=False)
    assert a.entity_id != b.entity_id


def test_an_empty_name_is_refused_rather_than_creating_a_null_entity(session):
    with pytest.raises(ValueError):
        resolve_entity(session, "   ")


# --- the adjudicated path ---------------------------------------------------
#
# Embeddings nominate and a model decides, exactly as the metric registry does.
# `_nearest` is patched rather than called, so these run with no key: the part
# worth testing is the gate around the judgement, not the judgement.


class _Says:
    """A stand-in adjudicator with a fixed answer, which records what it saw."""

    def __init__(self, same: bool):
        self.same = same
        self.asked: list[tuple] = []

    def __call__(self, name, candidate, entity_type):
        from fkl.entities import EntityJudgement

        self.asked.append((name, candidate, entity_type))
        return EntityJudgement(same_entity=self.same, confidence=0.9,
                               reason="test")


def _nominate(monkeypatch, entity, score=0.8):
    monkeypatch.setattr("fkl.entities._nearest",
                        lambda session, name, entity_type: (entity, score))


def test_the_case_this_exists_for(session, monkeypatch):
    """`Indian economy` and `India`.

    One claim in the RBI annual report, forty in the IMF report. The gate
    blocks on entity before it looks at anything else, so until these resolve
    together the corpus's one genuine contradiction — the FY26 GDP projection —
    cannot form at all.
    """
    from fkl.models import Entity

    india = resolve_entity(session, "India", adjudicate=False)
    _nominate(monkeypatch, session.get(Entity, india.entity_id), 0.83)
    says_yes = _Says(True)

    got = resolve_entity(session, "Indian economy", adjudicator=says_yes)
    assert got.entity_id == india.entity_id
    assert got.method == "adjudicated"
    assert got.similarity == 0.83
    assert says_yes.asked == [("Indian economy", "India", "organisation")]

    # And the registry learned it: the same wording in the next document
    # resolves on the alias, without another model call. A *different* wording
    # is a different surface form and is asked about again — aliases are
    # learned per form, not guessed from one.
    quiet = _Says(False)
    again = resolve_entity(session, "Indian economy", adjudicator=quiet)
    assert again.entity_id == india.entity_id
    assert not quiet.asked


def test_a_refusal_leaves_two_entities(session, monkeypatch):
    """The Reserve Bank of India is not India, however close the names look.
    A refused match costs one comparison; a wrong one fuses two subjects
    permanently and reports confident nonsense about them forever after."""
    from fkl.models import Entity

    india = resolve_entity(session, "India", adjudicate=False)
    _nominate(monkeypatch, session.get(Entity, india.entity_id), 0.79)
    says_no = _Says(False)

    got = resolve_entity(session, "Reserve Bank of India", adjudicator=says_no)
    assert got.entity_id != india.entity_id
    assert got.created
    assert says_no.asked


def test_people_are_never_nominated_however_close_the_names(session, monkeypatch):
    """The guard that matters most. Two people can share a name and no prompt
    makes merging them safe — a person merges on a DIN or not at all."""
    from fkl.models import Entity

    first = resolve_entity(session, "Suvir Suren Sujan", entity_type="person")
    _nominate(monkeypatch, session.get(Entity, first.entity_id), 0.99)
    eager = _Says(True)

    second = resolve_entity(session, "Suvir Sujan", entity_type="person",
                            adjudicator=eager)
    assert second.entity_id != first.entity_id
    assert not eager.asked, "a person was put to the adjudicator"


def test_people_still_merge_on_a_registration_number(session):
    """Which is how that pair is supposed to join, and does."""
    din = [Identifier("din", "01173669", "01173669")]
    a = resolve_entity(session, "Suvir Suren Sujan", entity_type="person",
                       identifiers=din)
    b = resolve_entity(session, "Suvir Sujan", entity_type="person",
                       identifiers=din)
    assert a.entity_id == b.entity_id
    assert b.method == "identifier"


def test_a_person_is_never_a_candidate_for_an_organisation(session):
    """`_nearest` filters by kind before similarity is considered, which
    removes the whole class of failure where a company named after its founder
    absorbs the founder."""
    from fkl.entities import _nearest

    resolve_entity(session, "Suvir Sujan", entity_type="person")
    best, score = _nearest(session, "Suvir Sujan Holdings", "organisation")
    assert best is None and score == 0.0


def test_no_credentials_means_no_candidates_rather_than_a_failed_claim(
    session, monkeypatch
):
    """A network problem must not look like an extraction problem. Proposing
    nothing degrades to the behaviour without this step at all."""
    from fkl.llm.client import LLMUnavailable

    resolve_entity(session, "India", adjudicate=False)

    def unavailable(*_a, **_k):
        raise LLMUnavailable("no key")

    monkeypatch.setattr("fkl.entities.embed_texts", unavailable)
    got = resolve_entity(session, "Indian economy")
    assert got.created and got.method == "new"


def test_the_adjudicator_is_asked_only_once_per_new_surface_form(session, monkeypatch):
    """Cost control, and the reason aliases are written on a refusal too — a
    name that was rejected becomes its own entity, and resolves there next
    time without asking again."""
    from fkl.models import Entity

    india = resolve_entity(session, "India", adjudicate=False)
    _nominate(monkeypatch, session.get(Entity, india.entity_id))
    says_no = _Says(False)

    resolve_entity(session, "Bharat Heavy Electricals", adjudicator=says_no)
    resolve_entity(session, "Bharat Heavy Electricals", adjudicator=says_no)
    assert len(says_no.asked) == 1


def test_a_split_learned_before_the_evidence_existed_can_be_repaired(session):
    """Resolution is decided once and recorded as an alias, which is what makes
    the second document using a name free — and what stops a registry from
    unlearning a split it made before it could do better. `Indian economy` and
    `India` were recorded apart by a version of this module that merged only on
    exact names, and every later claim then resolved to that split, correctly
    and unhelpfully."""
    from fkl.entities import merge_entities
    from fkl.models import Claim, Document, Entity, EntityAlias

    session.add(Document(id=1, sha256="a" * 64, filename="rbi.pdf",
                         source_path="rbi.pdf", n_pages=1))
    session.flush()
    india = resolve_entity(session, "India", adjudicate=False)
    economy = resolve_entity(session, "Indian economy", adjudicate=False)
    assert india.entity_id != economy.entity_id

    session.add(Claim(
        document_id=1, page_no=7, claim_type="measurement",
        subject="Indian economy", predicate="real GDP growth",
        entity_id=economy.entity_id, evidence_quote="q", qualifiers={},
        unknown_qualifiers=[], confidence_reasons=[], modality="estimate",
    ))
    session.flush()

    moved = merge_entities(session, india.entity_id, economy.entity_id)
    assert moved == 1
    assert session.get(Entity, economy.entity_id) is None
    assert session.query(Claim).one().entity_id == india.entity_id

    # The alias moves rather than being dropped, so the merge is learned: the
    # absorbed name still resolves, now to the survivor.
    again = resolve_entity(session, "Indian economy", adjudicate=False)
    assert again.entity_id == india.entity_id
    assert session.query(EntityAlias).filter_by(
        alias_key="indian economy").one().method == "merged"


def test_merging_an_entity_into_itself_is_a_no_op(session):
    from fkl.entities import merge_entities

    india = resolve_entity(session, "India", adjudicate=False)
    assert merge_entities(session, india.entity_id, india.entity_id) == 0
