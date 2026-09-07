"""The interval engine.

The distinction everything here turns on is between *not knowing yet* and
*being wrong*. A 2022 document that lists a serving director with no end date is
not contradicted by a 2024 document that says he resigned in 2023 — it was
correct about its own moment. Treating that as a conflict is not strictness, it
is an error, and it fires for every officer, director and address in any corpus
that spans time.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fkl.temporal import (  # noqa: E402
    CLOSES_INTERVAL,
    CONTEXTUAL,
    CONTRADICTS,
    CORROBORATES,
    SUCCESSION,
    SUCCESSION_WITH_VACANCY,
    Holding,
    group_slots,
    infer_cardinality,
    observe_cardinality,
    relate_holdings,
    roster,
)

ORG = "Delhivery Limited"


def cs(ref, person, **kwargs) -> Holding:
    return Holding(ref=ref, scope=ORG, predicate="Company Secretary",
                   filler=person, **kwargs)


# --- cardinality ------------------------------------------------------------


def test_cardinality_is_seeded_from_generic_vocabulary():
    """Never from a list of this corpus's roles. `Managing Director` has to come
    out singular while `Non-Executive Director` comes out plural, which is why
    the singular markers are checked first."""
    assert infer_cardinality("Company Secretary") == 1
    assert infer_cardinality("Company Secretary and Compliance Officer") == 1
    assert infer_cardinality("Chief Financial Officer") == 1
    assert infer_cardinality("Managing Director") == 1
    assert infer_cardinality("CIN") == 1
    assert infer_cardinality("registered office") == 1

    assert infer_cardinality("Non-Executive Director") == 0
    assert infer_cardinality("Independent Director") == 0
    assert infer_cardinality("material subsidiary") == 0


def test_cardinality_is_corrected_by_what_the_documents_show():
    """A wrongly-inferred single-holder role manufactures contradictions, so the
    seed has to be correctable.

    One document listing two people in the same seat at the same time is not a
    disagreement to be adjudicated — it is the document telling us the seat
    holds more than one.
    """
    concurrent = [
        Holding(ref="a", scope=ORG, predicate="Head of Region", filler="A",
                valid_from=date(2023, 1, 1), document="ar"),
        Holding(ref="b", scope=ORG, predicate="Head of Region", filler="B",
                valid_from=date(2023, 1, 1), document="ar"),
    ]
    corrected, note = observe_cardinality(concurrent, seeded=1)
    assert corrected == 0
    assert "concurrently" in note


def test_two_documents_disagreeing_does_not_change_cardinality():
    """Only same-document evidence corrects the seed. Two documents showing
    overlapping holders is exactly the conflict this project exists to report,
    not evidence about the shape of the role."""
    across = [
        cs("a", "Vivek Kumar", valid_from=date(2023, 6, 1), document="ar"),
        cs("b", "Madhulika Rawat", valid_from=date(2023, 6, 1), document="prospectus"),
    ]
    corrected, _ = observe_cardinality(across, seeded=1)
    assert corrected == 1


# --- the two clocks ---------------------------------------------------------


def test_a_later_document_closes_an_open_interval_rather_than_contradicting_it():
    """The prospectus lists Suvir Sujan as a serving director in May 2022. The
    FY24 annual report says he resigned in August 2023.

    The prospectus could not have known. Reporting CONTRADICTS here is the
    classic bitemporal mistake.
    """
    prospectus = Holding(
        ref="p", scope=ORG, predicate="Non-Executive Director",
        filler="Suvir Suren Sujan", valid_to_is_open=True,
        asserted=date(2022, 5, 14), document="prospectus",
    )
    report = Holding(
        ref="ar", scope=ORG, predicate="Non-Executive Director",
        filler="Suvir Suren Sujan", valid_from=date(2022, 5, 24),
        valid_to=date(2023, 8, 24), asserted=date(2024, 8, 8), document="ar",
    )
    verdict = relate_holdings(prospectus, report, cardinality=0)
    assert verdict.verdict == CLOSES_INTERVAL
    assert verdict.axis == "valid_time"


def test_an_end_date_before_the_earlier_assertion_is_a_real_contradiction():
    """The other side of the same rule, and what keeps it from excusing
    everything: a document claiming someone still serves *after* they had
    already left is wrong about something it could have known."""
    stale = Holding(ref="a", scope=ORG, predicate="Non-Executive Director",
                    filler="X", valid_to_is_open=True, asserted=date(2024, 1, 1))
    correction = Holding(ref="b", scope=ORG, predicate="Non-Executive Director",
                         filler="X", valid_to=date(2023, 3, 1),
                         asserted=date(2024, 6, 1))
    assert relate_holdings(stale, correction, 0).verdict == CONTRADICTS


def test_two_documents_asserting_the_same_interval_corroborate():
    a = cs("a", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27), asserted=date(2024, 8, 8))
    b = cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27), asserted=date(2025, 1, 1))
    assert relate_holdings(a, b, 1).verdict == CORROBORATES


def test_two_documents_giving_different_end_dates_contradict():
    a = cs("a", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27), asserted=date(2024, 8, 8))
    b = cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 4, 30), asserted=date(2025, 1, 1))
    assert relate_holdings(a, b, 1).verdict == CONTRADICTS


# --- succession -------------------------------------------------------------


def test_a_contiguous_handover_is_a_succession_not_a_conflict():
    """Bansal to 31 May 2023, Vivek from 1 June 2023. Inclusive dates, so a
    one-day difference is a clean handover with no vacancy."""
    verdict = relate_holdings(
        cs("a", "Sunil Kumar Bansal", valid_to=date(2023, 5, 31)),
        cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27)),
        cardinality=1,
    )
    assert verdict.verdict == SUCCESSION
    assert verdict.gap_days == 1


def test_a_gap_in_a_single_holder_role_is_reported_as_a_vacancy():
    """Vivek to 27 March 2024, Rawat from 17 May 2024.

    Fifty-one days with no Company Secretary, in a role the company is
    statutorily required to fill. The gap is the finding, and it exists only
    because intervals are modelled rather than overwritten — a store that kept
    "current Company Secretary" as a mutable field would show Rawat and nothing
    else.
    """
    verdict = relate_holdings(
        cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27)),
        cs("c", "Madhulika Rawat", valid_from=date(2024, 5, 17),
           valid_to_is_open=True),
        cardinality=1,
    )
    assert verdict.verdict == SUCCESSION_WITH_VACANCY
    assert verdict.gap_days == 51
    assert "unfilled" in verdict.explanation


def test_two_holders_at_once_in_a_single_holder_role_is_a_conflict():
    verdict = relate_holdings(
        cs("a", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27)),
        cs("b", "Madhulika Rawat", valid_from=date(2023, 9, 1),
           valid_to_is_open=True),
        cardinality=1,
    )
    assert verdict.verdict == CONTRADICTS


def test_two_of_many_directors_serving_at_once_is_not_a_relation():
    """Emitting anything here would bury the real findings under every pair of
    board members who ever served together."""
    a = Holding(ref="a", scope=ORG, predicate="Non-Executive Director",
                filler="A", valid_from=date(2022, 1, 1), valid_to_is_open=True)
    b = Holding(ref="b", scope=ORG, predicate="Non-Executive Director",
                filler="B", valid_from=date(2022, 1, 1), valid_to_is_open=True)
    assert relate_holdings(a, b, cardinality=0) is None


# --- attributes that change -------------------------------------------------


def test_an_identifier_that_changed_between_assertions_is_not_a_contradiction():
    """The CIN flips from unlisted to listed at IPO.

    Neither document gives a validity period, because neither expects the value
    to change. With no valid time anywhere, assertion time is the only ordering
    available, and two values two years apart are a change.
    """
    prospectus = Holding(ref="a", scope=ORG, predicate="CIN",
                         filler="U63090DL2011PLC221234", asserted=date(2022, 5, 14))
    report = Holding(ref="b", scope=ORG, predicate="CIN",
                     filler="L63090DL2011PLC221234", asserted=date(2024, 8, 8))
    verdict = relate_holdings(prospectus, report, cardinality=1)
    assert verdict.verdict == CONTEXTUAL
    assert verdict.axis == "valid_time"


def test_two_values_asserted_at_the_same_moment_do_contradict():
    """Without differing assertion dates there is nothing to order them by, and
    the rule above must not become a blanket excuse."""
    a = Holding(ref="a", scope=ORG, predicate="CIN", filler="U1",
                asserted=date(2024, 1, 1))
    b = Holding(ref="b", scope=ORG, predicate="CIN", filler="L1",
                asserted=date(2024, 1, 1))
    assert relate_holdings(a, b, 1).verdict == CONTRADICTS


# --- blocking and time travel -----------------------------------------------


def test_the_slot_is_the_organisations_not_the_holders():
    """A succession is a constraint on Delhivery's Company Secretary seat.

    Blocking by holder would put Bansal, Vivek and Rawat in three separate
    groups, compare each with themselves, and find nothing at all.
    """
    slots = group_slots([
        cs("a", "Sunil Kumar Bansal", valid_to=date(2023, 5, 31)),
        cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1)),
        cs("c", "Madhulika Rawat", valid_from=date(2024, 5, 17)),
    ])
    assert len(slots) == 1
    assert len(next(iter(slots.values()))) == 3


def test_the_roster_differs_by_date_and_both_answers_are_correct():
    """Current state is a query with an as-of clause, never a stored field."""
    chain = [
        cs("a", "Sunil Kumar Bansal", valid_to=date(2023, 5, 31)),
        cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1),
           valid_to=date(2024, 3, 27)),
        cs("c", "Madhulika Rawat", valid_from=date(2024, 5, 17),
           valid_to_is_open=True),
    ]
    assert [h.filler for h in roster(chain, date(2023, 1, 1))] == ["Sunil Kumar Bansal"]
    assert [h.filler for h in roster(chain, date(2023, 8, 1))] == ["Vivek Kumar"]
    assert [h.filler for h in roster(chain, date(2024, 6, 1))] == ["Madhulika Rawat"]
    # And the vacancy is visible as an empty seat, not as a gap nobody notices.
    assert roster(chain, date(2024, 4, 15)) == []


# --- which pairs are worth comparing ----------------------------------------


def test_a_succession_is_between_neighbours_not_between_every_pair():
    """Comparing every pair in a chain invents vacancies.

    With Bansal, Vivek and Rawat in sequence, the all-pairs version reported
    that Bansal was succeeded by Rawat after a 352-day vacancy. That is false —
    Vivek held the role for almost all of it. A handover is a relation between
    neighbours.
    """
    from fkl.temporal import succession_pairs

    chain = [
        cs("a", "Sunil Kumar Bansal", valid_to=date(2023, 5, 31)),
        cs("b", "Vivek Kumar", valid_from=date(2023, 6, 1), valid_to=date(2024, 3, 27)),
        cs("c", "Madhulika Rawat", valid_from=date(2024, 5, 17), valid_to_is_open=True),
    ]
    pairs = {(a.ref, b.ref) for a, b in succession_pairs(chain)}
    assert pairs == {("a", "b"), ("b", "c")}
    assert ("a", "c") not in pairs


def test_the_same_person_is_compared_across_any_distance():
    """An open interval gets closed by a later document however far apart the
    two assertions sort."""
    from fkl.temporal import succession_pairs

    holdings = [
        cs("open", "X", valid_to_is_open=True, asserted=date(2022, 1, 1)),
        cs("mid", "Y", valid_from=date(2023, 1, 1), valid_to=date(2023, 6, 1)),
        cs("closed", "X", valid_from=date(2024, 1, 1), valid_to=date(2024, 6, 1),
           asserted=date(2025, 1, 1)),
    ]
    pairs = {(a.ref, b.ref) for a, b in succession_pairs(holdings)}
    assert ("open", "closed") in pairs


def test_overlapping_holders_are_compared_however_far_apart_they_sort():
    """The constraint violation has to be reported even when the two are not
    neighbours in start order."""
    from fkl.temporal import succession_pairs

    holdings = [
        cs("a", "A", valid_from=date(2020, 1, 1), valid_to_is_open=True),
        cs("b", "B", valid_from=date(2021, 1, 1), valid_to=date(2021, 6, 1)),
        cs("c", "C", valid_from=date(2022, 1, 1), valid_to=date(2022, 6, 1)),
    ]
    pairs = {(a.ref, b.ref) for a, b in succession_pairs(holdings)}
    assert ("a", "c") in pairs  # A's open interval covers C's


def test_one_person_holding_a_seat_twice_is_not_a_conflict():
    """AR p90, and the same pair that the measurement gate used to mislabel.

    Donald Colleran is "Non Executive - Nominee Director (till May 23, 2022)"
    and then "Non-Executive Director (w.e.f. May 24, 2022)". Once both titles
    resolve to one canonical role, the two holdings land in one seat with the
    same holder and unequal intervals — and the fallback for that case assumed
    two accounts of a single spell that disagree. These are two spells that fit
    together to the day. Reading them as a conflict requires ignoring that.
    """
    from fkl.temporal import CONTINUES, Holding, relate_holdings

    a = Holding(ref="a", scope="Delhivery Limited", predicate="non-executive director",
                filler="Mr. Donald Francis Colleran", valid_to=date(2022, 5, 23))
    b = Holding(ref="b", scope="Delhivery Limited", predicate="non-executive director",
                filler="Mr. Donald Francis Colleran", valid_from=date(2022, 5, 24),
                valid_to=date(2023, 9, 27))

    verdict = relate_holdings(a, b, cardinality=0)
    assert verdict.verdict == CONTINUES
    assert verdict.gap_days == 1
    assert "redesignation" in verdict.explanation


def test_two_accounts_of_one_spell_that_disagree_still_contradict():
    """The fallback narrowed, not removed. Overlapping-but-unequal intervals
    for one person are two documents disagreeing about when they served, which
    is a real conflict and has to keep being reported."""
    from fkl.temporal import CONTRADICTS, Holding, relate_holdings

    a = Holding(ref="a", scope="Delhivery Limited", predicate="company secretary",
                filler="Mr. Vivek Kumar", valid_from=date(2023, 6, 1),
                valid_to=date(2024, 3, 27))
    b = Holding(ref="b", scope="Delhivery Limited", predicate="company secretary",
                filler="Mr. Vivek Kumar", valid_from=date(2023, 6, 1),
                valid_to=date(2024, 9, 30))

    verdict = relate_holdings(a, b)
    assert verdict.verdict == CONTRADICTS
    assert "overlapping" in verdict.explanation


def test_the_vacancy_is_a_day_shorter_than_the_gap_between_the_dates():
    """An off-by-one in the most visible finding the engine produces.

    Vivek's last day is 2024-03-27 and Rawat's first is 2024-05-17. The dates
    are 51 days apart; the seat is empty on 50 of them — 28 March through 16
    May. `gap_days` has to stay the date difference because that is what the
    contiguity test reads (a gap of 1 is a clean handover, not a one-day
    vacancy), so the two numbers are kept apart rather than reconciled.
    """
    from fkl.temporal import SUCCESSION_WITH_VACANCY, Holding, relate_holdings

    a = Holding(ref="a", scope="Delhivery Limited", predicate="company secretary",
                filler="Mr. Vivek Kumar", valid_from=date(2023, 6, 1),
                valid_to=date(2024, 3, 27))
    b = Holding(ref="b", scope="Delhivery Limited", predicate="company secretary",
                filler="Mrs. Madhulika Rawat", valid_from=date(2024, 5, 17),
                valid_to_is_open=True)

    verdict = relate_holdings(a, b)
    assert verdict.verdict == SUCCESSION_WITH_VACANCY
    assert verdict.gap_days == 51
    assert verdict.vacant_days == 50
    assert "unfilled for 50 days" in verdict.explanation


def test_a_clean_handover_reports_no_vacancy_at_all():
    """Bansal to 31 May, Vivek from 1 June: one day apart by subtraction, and
    zero days unfilled. The distinction is the whole reason the two numbers
    are not the same field."""
    from fkl.temporal import SUCCESSION, Holding, relate_holdings

    a = Holding(ref="a", scope="Delhivery Limited", predicate="company secretary",
                filler="Mr. Sunil Kumar Bansal", valid_to=date(2023, 5, 31))
    b = Holding(ref="b", scope="Delhivery Limited", predicate="company secretary",
                filler="Mr. Vivek Kumar", valid_from=date(2023, 6, 1),
                valid_to=date(2024, 3, 27))

    verdict = relate_holdings(a, b)
    assert verdict.verdict == SUCCESSION
    assert verdict.vacant_days == 0
