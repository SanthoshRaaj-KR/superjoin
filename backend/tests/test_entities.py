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
    a = resolve_entity(session, "Delhivery Limited")
    b = resolve_entity(session, "Spoton Logistics Private Limited")
    assert a.entity_id != b.entity_id


def test_an_empty_name_is_refused_rather_than_creating_a_null_entity(session):
    with pytest.raises(ValueError):
        resolve_entity(session, "   ")
