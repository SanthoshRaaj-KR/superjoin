"""L5c — the interval engine: bitemporal reconciliation over state claims.

The comparability gate handles measurements, where two claims are comparable
when they cover the same period. State claims — who holds a role, what the
registered office is, what the CIN is — do not work that way. They assert
intervals, and the interesting relations are between *adjacent* intervals rather
than overlapping ones.

**Two clocks, and confusing them is the classic mistake.**

- *Valid time* is when something was true in the world: Suvir Sujan was a
  director from 2022-05-24 to 2023-08-24.
- *Assertion time* is when a document said so: the prospectus said it in 2022,
  the annual report said something else in 2024.

The 2022 prospectus lists Suvir as a serving director with no end date. The 2024
annual report says he resigned in August 2023. Those do not contradict. The
prospectus was correct about its own moment and simply could not know the
future; the annual report *closes an interval the prospectus left open*. A
system that reports CONTRADICTS here is not being strict, it is being wrong, and
it will report it for every director, officer and address in any corpus that
spans time.

**Cardinality turns "is this a conflict?" into a constraint check.** A company
has one Company Secretary and many directors. Two people holding the Company
Secretary role over disjoint intervals is a succession; two people holding it at
once is a genuine problem. The same two intervals on `director` are simply two
directors. So the relation depends on a property of the *predicate*, and that
property is inferred and correctable rather than hard-coded to this corpus.

**The blocking key is not the one the measurement gate uses.** A succession is a
constraint on ``(organisation, role)`` — it is Delhivery's Company Secretary
seat that only one person can occupy. Blocking on ``(person, role)`` would
compare Bansal only with himself and find nothing.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

# Verdicts only this engine produces.
CLOSES_INTERVAL = "CLOSES_INTERVAL"
SUCCESSION = "SUCCESSION"
SUCCESSION_WITH_VACANCY = "SUCCESSION_WITH_VACANCY"
# One person, two consecutive spells in one seat. A redesignation reads as
# this: the title changed, the holder did not.
CONTINUES = "CONTINUES"
CONCURRENT = "CONCURRENT"  # cardinality-N, both valid; emitted for visibility
CORROBORATES = "CORROBORATES"
CONTRADICTS = "CONTRADICTS"
CONTEXTUAL = "CONTEXTUAL"

# Two intervals whose gap is at most this are treated as a clean handover. One
# day, because a role ending on 31 May and the next beginning on 1 June is a
# handover with no vacancy — the dates are inclusive on both sides.
CONTIGUOUS_DAYS = 1

# Roles only one person can hold at a time. Matched as words rather than exact
# titles so "Company Secretary and Compliance Officer" and "Company Secretary"
# reach the same conclusion.
#
# Checked before the plural markers below, which is what keeps "Managing
# Director" at cardinality 1 while "Non-Executive Director" is N.
_SINGULAR = re.compile(
    r"\bchief\b|\bsecretary\b|\bchairman\b|\bchairperson\b|\bmanaging\b"
    r"|\bpresident\b|\bcompliance officer\b|\bcfo\b|\bceo\b|\bcoo\b|\bcto\b",
    re.I,
)
_PLURAL = re.compile(
    r"\bdirector\b|\bmember\b|\bsubsidiar(?:y|ies)\b|\bpartner\b"
    r"|\bshareholder\b|\bauditor\b|\bassociates?\b|\baffiliates?\b"
    r"|\bpromoters?\b|\btrustees?\b",
    re.I,
)
# Attributes an entity has exactly one of at a time.
_SINGULAR_ATTRIBUTE = re.compile(
    r"\bcin\b|\bisin\b|\bregistered office\b|\bregistration number\b"
    r"|\bcorporate identity\b|\bpan\b|\bhead ?quarters?\b",
    re.I,
)


def infer_cardinality(predicate: str) -> int:
    """How many fillers a predicate admits at one time: 1 or many.

    Seeded from generic vocabulary, never from a list of this corpus's roles.
    ``observe_cardinality`` below corrects it from evidence, which matters
    because a wrongly-inferred 1 manufactures contradictions.
    """
    text = predicate or ""
    if _SINGULAR_ATTRIBUTE.search(text) or _SINGULAR.search(text):
        return 1
    if _PLURAL.search(text):
        return 0  # many
    # A predicate whose head noun is plural is asking for a list. "Other
    # Directorships" and "Previous roles" name several things at once, and a
    # document that lists three of them is not contradicting itself twice.
    # Generic rather than a vocabulary: it is the grammar that says so.
    #
    # The head is taken from before any dash, because role titles qualify a
    # singular head with a plural complement — "Head - New Ventures" is one
    # post, and reading "Ventures" as the head makes it a list and loses every
    # succession in that seat.
    head = re.split(r"\s[-–—]\s|\bof\b|\bfor\b", text.strip().rstrip(":"))[0]
    words = head.split()
    word = words[-1].lower() if words else ""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return 0
    return 1


def observe_cardinality(holdings: list["Holding"], seeded: int) -> tuple[int, str | None]:
    """Correct a seeded cardinality against what the documents actually show.

    If one document asserts two different fillers whose intervals genuinely
    overlap, the predicate admits more than one and the seed was wrong. Trusting
    the same document is the point: two documents disagreeing is what this whole
    project is about, but one document listing two concurrent holders is simply
    telling us the shape of the thing.
    """
    if seeded != 1:
        return seeded, None
    by_document: dict[str | None, list[Holding]] = defaultdict(list)
    for holding in holdings:
        by_document[holding.document].append(holding)

    for document, group in by_document.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if a.filler != b.filler and a.overlaps(b):
                    return 0, (
                        f"observed {a.filler!r} and {b.filler!r} holding this "
                        f"concurrently in one document, so it is not a "
                        f"single-holder role"
                    )
    return 1, None


@dataclass
class Holding:
    """One assertion that a filler occupied a slot over an interval."""

    ref: str
    scope: str  # the organisation, or the entity an attribute belongs to
    predicate: str  # the role or attribute
    filler: str  # the person, or the attribute's value
    valid_from: date | None = None
    valid_to: date | None = None
    valid_to_is_open: bool = False
    asserted: date | None = None  # assertion time, from the document
    document: str | None = None
    page_no: int | None = None
    evidence_quote: str | None = None

    @property
    def is_open(self) -> bool:
        return self.valid_to is None or self.valid_to_is_open

    def overlaps(self, other: "Holding") -> bool:
        """Whether two intervals share any time, treating open ends as ongoing."""
        start_a = self.valid_from or date.min
        start_b = other.valid_from or date.min
        end_a = date.max if self.is_open else self.valid_to
        end_b = date.max if other.is_open else other.valid_to
        return start_a <= end_b and start_b <= end_a


@dataclass
class TemporalVerdict:
    verdict: str
    axis: str | None
    explanation: str
    a: str
    b: str
    gap_days: int | None = None
    cardinality: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def vacant_days(self) -> int | None:
        """Days the seat was actually empty.

        Not the same number as ``gap_days``, and conflating them is an
        off-by-one in the most visible finding the engine produces. Vivek's
        last day is 2024-03-27 and Rawat's first is 2024-05-17: the dates are
        51 days apart, and the role is unfilled on 50 of them — 28 March
        through 16 May. ``gap_days`` stays the date difference because that is
        what the contiguity test needs (a gap of 1 is a clean handover, not a
        one-day vacancy); this is what a reader should be told.
        """
        if self.gap_days is None:
            return None
        return max(0, self.gap_days - 1)


def relate_holdings(
    a: Holding, b: Holding, cardinality: int = 1, cardinality_note: str | None = None
) -> TemporalVerdict | None:
    """Compare two assertions about the same ``(scope, predicate)`` slot.

    Returns None when there is nothing to say — two of many directors serving
    at once is not a relation worth recording.
    """
    notes = [cardinality_note] if cardinality_note else []

    if a.filler == b.filler:
        return _same_filler(a, b, notes)

    if cardinality != 1:
        # Many fillers allowed, so there is no seat to be handed over and none
        # to fall vacant. Two independent directors who served at different
        # times are simply two directors; reporting a "174-day vacancy" between
        # them describes a constraint the role does not have.
        return None

    # An attribute with no interval on either side. The CIN case: the
    # prospectus prints U63090DL2011PLC221234 and the annual report prints
    # L63090DL2011PLC221234, and neither states a validity period because
    # neither expects the value to change. It did — the prefix flips from
    # unlisted to listed at IPO.
    #
    # With no valid time anywhere, assertion time is the only ordering
    # available, and two different values asserted two years apart are a change
    # rather than a disagreement. Asserted at the *same* time they would be a
    # genuine conflict, which is why the dates have to differ.
    if _no_interval(a) and _no_interval(b):
        if a.asserted and b.asserted and a.asserted != b.asserted:
            earlier, later = (a, b) if a.asserted < b.asserted else (b, a)
            return TemporalVerdict(
                CONTEXTUAL, "valid_time",
                f"{a.scope}'s {a.predicate} is {earlier.filler!r} as of "
                f"{earlier.asserted} and {later.filler!r} as of "
                f"{later.asserted}. Neither document gives a validity period, "
                f"so the only reading is that the value changed between them",
                earlier.ref, later.ref, cardinality=1, notes=notes,
            )
        return TemporalVerdict(
            CONTRADICTS, "valid_time",
            f"{a.scope}'s {a.predicate} is given as {a.filler!r} and "
            f"{b.filler!r} with no validity period and nothing to order them",
            a.ref, b.ref, cardinality=1, notes=notes,
        )

    if a.overlaps(b):
        return TemporalVerdict(
            CONTRADICTS, "valid_time",
            f"{a.filler} and {b.filler} are both recorded as {a.predicate} of "
            f"{a.scope} over overlapping periods, and only one person holds "
            f"that role at a time",
            a.ref, b.ref, cardinality=1, notes=notes,
        )
    return _succession(a, b, notes, cardinality)


def _same_filler(a: Holding, b: Holding, notes: list[str]) -> TemporalVerdict:
    """Two documents describing the same person in the same seat."""
    earlier, later = (a, b) if _asserted_before(a, b) else (b, a)

    if earlier.is_open and not later.is_open:
        # The refinement case. The earlier document did not know the end date
        # because it had not happened yet; that is not an error in it.
        after_assertion = (
            earlier.asserted is None
            or later.valid_to is None
            or later.valid_to >= earlier.asserted
        )
        if after_assertion:
            return TemporalVerdict(
                CLOSES_INTERVAL, "valid_time",
                f"{later.document or 'a later document'} ends {a.filler}'s "
                f"{a.predicate} on {later.valid_to}, closing an interval "
                f"{earlier.document or 'an earlier document'} left open. The "
                f"earlier document was written before that happened, so this "
                f"refines it rather than contradicting it",
                earlier.ref, later.ref, notes=notes,
            )
        # An end date *before* the earlier assertion means the earlier document
        # reported someone as serving after they had already left. That is a
        # real disagreement about a fact both could see.
        return TemporalVerdict(
            CONTRADICTS, "valid_time",
            f"{a.filler} is recorded as still serving as {a.predicate} in a "
            f"document dated {earlier.asserted}, but another says the role "
            f"ended on {later.valid_to}, before that",
            earlier.ref, later.ref, notes=notes,
        )

    if a.is_open and b.is_open:
        if a.valid_from == b.valid_from:
            return TemporalVerdict(
                CORROBORATES, None,
                f"both record {a.filler} as {a.predicate} of {a.scope} from "
                f"{a.valid_from}, with no end date",
                a.ref, b.ref, notes=notes,
            )
        return TemporalVerdict(
            CONTEXTUAL, "valid_time",
            f"both record {a.filler} as {a.predicate}, but from different start "
            f"dates ({a.valid_from} and {b.valid_from})",
            a.ref, b.ref, notes=notes,
        )

    if (a.valid_from, a.valid_to) == (b.valid_from, b.valid_to):
        return TemporalVerdict(
            CORROBORATES, None,
            f"both record {a.filler} as {a.predicate} of {a.scope} from "
            f"{a.valid_from} to {a.valid_to}",
            a.ref, b.ref, notes=notes,
        )

    # Two spells that do not overlap are not two accounts of one spell. The
    # same person held the seat twice — and in this corpus that is a
    # redesignation: Donald Colleran is "Non Executive - Nominee Director (till
    # May 23, 2022)" and "Non-Executive Director (w.e.f. May 24, 2022)", one day
    # apart, on one page of one document. Reading that as a conflict about when
    # he served requires ignoring that the two intervals fit together perfectly.
    if not a.overlaps(b):
        first, second = (a, b) if _starts_before(a, b) else (b, a)
        gap = (
            (second.valid_from - first.valid_to).days
            if first.valid_to and second.valid_from else None
        )
        contiguous = gap is not None and gap <= CONTIGUOUS_DAYS
        return TemporalVerdict(
            CONTINUES, "valid_time",
            f"{a.filler} holds {a.predicate} of {a.scope} over two consecutive "
            f"spells, {first.valid_from}-{first.valid_to} then "
            f"{second.valid_from}-{second.valid_to}"
            + (" — contiguous, so a redesignation or reappointment rather than "
               "a break in service" if contiguous
               else f" — with {gap} days between them" if gap is not None
               else ""),
            first.ref, second.ref, gap_days=gap, notes=notes,
        )

    # Overlapping but unequal: two accounts of one spell that disagree about
    # when it ran. This is the case the fallback is actually for.
    return TemporalVerdict(
        CONTRADICTS, "valid_time",
        f"two assertions give different, overlapping intervals for {a.filler} "
        f"as {a.predicate}: {a.valid_from}-{a.valid_to} against "
        f"{b.valid_from}-{b.valid_to}",
        a.ref, b.ref, notes=notes,
    )


def _succession(
    a: Holding, b: Holding, notes: list[str], cardinality: int
) -> TemporalVerdict:
    """Two different fillers, disjoint in time."""
    first, second = (a, b) if _starts_before(a, b) else (b, a)

    if first.valid_to is None or second.valid_from is None:
        return TemporalVerdict(
            SUCCESSION, "valid_time",
            f"{first.filler} then {second.filler} as {a.predicate} of {a.scope}, "
            "in that order, with at least one boundary date unstated",
            first.ref, second.ref, cardinality=cardinality, notes=notes,
        )

    gap = (second.valid_from - first.valid_to).days
    if gap <= CONTIGUOUS_DAYS:
        return TemporalVerdict(
            SUCCESSION, "valid_time",
            f"{first.filler} to {first.valid_to}, then {second.filler} from "
            f"{second.valid_from} — a clean handover of {a.predicate}",
            first.ref, second.ref, gap_days=gap, cardinality=cardinality, notes=notes,
        )
    return TemporalVerdict(
        SUCCESSION_WITH_VACANCY, "valid_time",
        f"{first.filler} to {first.valid_to}, then {second.filler} from "
        f"{second.valid_from} — {a.predicate} of {a.scope} unfilled for "
        f"{gap - 1} days between them",
        first.ref, second.ref, gap_days=gap, cardinality=cardinality, notes=notes,
    )


def _no_interval(holding: Holding) -> bool:
    """Whether a holding carries no valid-time information at all."""
    return holding.valid_from is None and holding.valid_to is None


def succession_pairs(holdings: list[Holding]) -> list[tuple[Holding, Holding]]:
    """The pairs in a single-holder slot that are worth comparing.

    Comparing every pair reports a handover between every holder and every
    later one: with Bansal, Vivek and Rawat in sequence it claims Bansal was
    succeeded by Rawat after a 352-day vacancy, which is false — Vivek held the
    role throughout. A succession is a relation between *neighbours* in the
    chain.

    Three kinds of pair survive:

    - the same person in two documents, at any distance, since that is how an
      open interval gets closed;
    - overlapping holders, which is the constraint violation and is worth
      reporting however far apart they sort;
    - consecutive holders in start order, which are the real handovers.
    """
    ordered = sorted(holdings, key=lambda h: (h.valid_from or date.min,
                                              h.valid_to or date.max))
    neighbours = {
        (id(a), id(b))
        for a, b in zip(ordered, ordered[1:])
    }
    pairs: list[tuple[Holding, Holding]] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if a.filler == b.filler or a.overlaps(b) or (id(a), id(b)) in neighbours:
                pairs.append((a, b))
    return pairs


def _asserted_before(a: Holding, b: Holding) -> bool:
    if a.asserted and b.asserted:
        return a.asserted <= b.asserted
    # With no assertion dates, the one that leaves its interval open is the
    # earlier statement: a document that knows an end date knows more.
    return a.is_open and not b.is_open


def _starts_before(a: Holding, b: Holding) -> bool:
    return (a.valid_from or date.min) <= (b.valid_from or date.min)


# --- time travel ------------------------------------------------------------


def roster(holdings: list[Holding], as_of: date) -> list[Holding]:
    """Who held what on a given date.

    Nothing is stored for this. Current state is a query with an as-of clause
    over immutable claims, which is what makes "the board in June 2022" and "the
    board in March 2024" both answerable and both correct.
    """
    live = []
    for holding in holdings:
        if holding.valid_from and holding.valid_from > as_of:
            continue
        if not holding.is_open and holding.valid_to and holding.valid_to < as_of:
            continue
        live.append(holding)
    return sorted(live, key=lambda h: (h.predicate, h.filler))


def group_slots(holdings: list[Holding]) -> dict[tuple[str, str], list[Holding]]:
    """Block holdings by the slot a cardinality constraint applies to.

    ``(scope, predicate)`` — Delhivery's Company Secretary seat, not Bansal's.
    Blocking by holder would compare each person only with themselves.
    """
    slots: dict[tuple[str, str], list[Holding]] = defaultdict(list)
    for holding in holdings:
        slots[(holding.scope.lower(), holding.predicate.lower())].append(holding)
    return dict(slots)
