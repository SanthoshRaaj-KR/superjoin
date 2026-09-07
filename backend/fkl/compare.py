"""L5 — the comparability gate.

The centre of the project. Everything before this exists to make this function
possible, and it contains no model call at all: given two typed claims it is a
pure, deterministic function returning a verdict, the axis responsible, and an
explanation templated from the difference rather than written by a model.

**The order of the tests is the argument.** A system that asks "do these
disagree?" first will find disagreements everywhere, because most pairs of
numbers differ. This asks, in order: are they about the same thing, is anything
material missing, do they cover the same time, and only then, do the values
agree. By the time a value comparison happens, every reason two figures could
legitimately differ has already been excluded and named.

**INSUFFICIENT_EVIDENCE is a real verdict, not a failure.** The corpus makes the
case better than an argument could. The earnings deck reports FY24 revenue as
₹8,142 Cr and the annual report's *consolidated* figure is ₹81,415.38 Mn — the
same number, 0.006% apart. It is still refused, because the deck never says
whether it is reporting standalone or consolidated, and the annual report's two
bases for that very figure differ by 9.2%. An analyst concludes the deck is
consolidated *because* the numbers match, which is circular; the gate must not.
Naming the missing axis is more useful than a confident guess, and supplying the
axis flips the verdict to CORROBORATES — which is what makes the counterfactual
toggle worth showing.

**Absent is not equal.** An axis one claim states and the other never mentions
is a difference between them, reported as CONTEXTUAL with the axis named. An
axis a claim explicitly could not determine blocks the comparison outright. The
difference between those two is exactly what ``unknown_qualifiers`` is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .periods import Period, compare_periods
from .units import Unit, compare_units
from .values import ValueComparison, compare_text_values, compare_values

# Verdicts this gate can return. The temporal ones — CLOSES_INTERVAL,
# SUCCESSION — belong to the interval engine and are not produced here.
CORROBORATES = "CORROBORATES"
CONTRADICTS = "CONTRADICTS"
CONTEXTUAL = "CONTEXTUAL"
CONTEXTUAL_TEMPORAL = "CONTEXTUAL_TEMPORAL"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
INCOMPARABLE = "INCOMPARABLE"

# When several axes differ, this is the order in which one is named as primary.
# Specific beats general on purpose: an Economic Survey figure and an RBI figure
# for the same year differ on `modality` (estimate against actual) *and* on
# `estimate_vintage` (first advance estimates against none). Both are true, and
# "these are different vintages of the same estimate" explains the 6.4/6.5 gap
# in a way that "one is an estimate" does not.
AXIS_PRIORITY = (
    "consolidation",
    "estimate_vintage",
    "price_base",
    "pro_forma",
    "restated",
    "org_scope",
    "period",
    "modality",
)


@dataclass
class Comparable:
    """A claim reduced to the fields a comparison needs.

    Deliberately not the ORM row. The gate is a pure function over this shape,
    which means it can be tested against the hand-labelled gold set without a
    database, an extraction run or an API key — and the gold set is the thing it
    has to satisfy.
    """

    ref: str
    entity: str
    metric: str
    unit: Unit
    period: Period
    value_canonical: float | None = None
    precision: float | None = None
    value_text: str | None = None
    qualifiers: dict[str, str] = field(default_factory=dict)
    unknown_qualifiers: list[str] = field(default_factory=list)
    modality: str = "actual"
    claim_type: str = "measurement"
    document: str | None = None
    page_no: int | None = None
    evidence_quote: str | None = None
    # Validity interval, for state claims. Read here only to recognise that two
    # states never held at the same time, which is a question about *when* and
    # not about whether they conflict.
    valid_from: "date | None" = None
    valid_to: "date | None" = None
    valid_to_is_open: bool = False

    def axis_values(self) -> dict[str, str]:
        """Every axis this claim states, including modality."""
        axes = {k: str(v) for k, v in (self.qualifiers or {}).items()}
        if self.modality:
            axes["modality"] = self.modality
        return axes


@dataclass
class Verdict:
    verdict: str
    axis: str | None
    explanation: str
    a: str
    b: str
    differing_axes: list[str] = field(default_factory=list)
    missing_axes: list[str] = field(default_factory=list)
    value: ValueComparison | None = None
    period_relation: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_conflict(self) -> bool:
        return self.verdict == CONTRADICTS


def _material_axes(a: Comparable, b: Comparable) -> set[str]:
    """Axes that matter to *this* comparison.

    Derived from the two claims rather than declared globally, which is what
    keeps the gate domain-independent. `consolidation` is material when a
    financial statement declares it or a deck admits it does not know it; it is
    simply absent from an IMF country report, where a nation has no
    consolidation basis, and so it never blocks one.
    """
    return (
        set(a.axis_values())
        | set(b.axis_values())
        | set(a.unknown_qualifiers or [])
        | set(b.unknown_qualifiers or [])
    )


def compare(a: Comparable, b: Comparable) -> Verdict:
    """Compare two claims. Pure, deterministic, and explains itself."""

    # 1. Blocking. Different subjects or different quantities are not a
    #    disagreement about anything.
    if a.entity != b.entity:
        return Verdict(
            INCOMPARABLE, "entity",
            f"different subjects: {a.entity} and {b.entity}",
            a.ref, b.ref,
        )
    if a.metric != b.metric:
        return Verdict(
            INCOMPARABLE, "metric",
            f"different metrics: {a.metric!r} and {b.metric!r}. Both may be "
            "revenue, in one currency, for one company and one year, and still "
            "be two business lines that must never be compared",
            a.ref, b.ref,
        )

    # 2. Units, for measurements. A currency mismatch stops here rather than
    #    being converted at an invented rate.
    if a.claim_type == "measurement":
        units = compare_units(a.unit, b.unit)
        if not units.comparable:
            verdict = (
                INSUFFICIENT_EVIDENCE if units.axis == "unit" and (
                    not a.unit.known or not b.unit.known
                ) else INCOMPARABLE
            )
            return Verdict(verdict, units.axis, units.reason, a.ref, b.ref)

    # 3. Sufficiency. An axis one claim could not determine, which the other
    #    states, blocks the comparison and is named.
    material = _material_axes(a, b)
    missing = sorted(
        axis for axis in material
        if (axis in (a.unknown_qualifiers or []) and axis in b.axis_values())
        or (axis in (b.unknown_qualifiers or []) and axis in a.axis_values())
    )
    if missing:
        axis = _primary(missing)
        stated_by = b if axis in b.axis_values() else a
        return Verdict(
            INSUFFICIENT_EVIDENCE, axis,
            f"one claim states {axis}={stated_by.axis_values()[axis]!r} and the "
            f"other could not determine it. Absent is not equal, so this is "
            f"refused rather than assumed",
            a.ref, b.ref, missing_axes=missing,
        )

    # 4. Time. Two different years are a trend, not a disagreement, and this is
    #    the single most common way a naive comparison manufactures conflicts.
    #
    #    State claims are exempt. A registered office or a registration number
    #    has validity intervals, not a reporting period, and demanding one turns
    #    every role, address and identifier in the corpus into
    #    INSUFFICIENT_EVIDENCE. Their temporal handling belongs to the interval
    #    engine, which reads valid_from and valid_to rather than a period label.
    period = compare_periods(a.period, b.period)
    if a.claim_type == "state" and not (a.period.known and b.period.known):
        # Two states that never held at once are a sequence, not a
        # disagreement — and the question of what that sequence means (a
        # succession, a vacancy, a redesignation) belongs to the interval
        # engine, which reads the dates. Comparing their text here produced
        # exactly one finding in the corpus and it was wrong: a director
        # redesignated from "Non Executive - Nominee Director" to
        # "Non-Executive Director" the following day, reported as a
        # contradiction because the two strings differ.
        if _intervals_disjoint(a, b):
            return Verdict(
                CONTEXTUAL_TEMPORAL, "valid_time",
                "the two states held over intervals that do not overlap — a "
                "sequence in time, resolved by the interval engine rather than "
                "by comparing their values",
                a.ref, b.ref, period_relation="disjoint",
            )
        period = type(period)("same", "state claims are not periodic", None, False)
    if period.relation == "unknown":
        return Verdict(
            INSUFFICIENT_EVIDENCE, "period", period.reason, a.ref, b.ref,
            missing_axes=["period"],
        )
    if period.relation == "disjoint":
        return Verdict(
            CONTEXTUAL_TEMPORAL, "period",
            f"{period.reason} — a change over time rather than a disagreement",
            a.ref, b.ref, period_relation=period.relation,
        )

    differing = sorted(_axis_differences(a, b))
    if period.relation == "overlapping":
        return Verdict(
            CONTEXTUAL, "period", period.reason, a.ref, b.ref,
            differing_axes=sorted(set(differing) | {"period"}),
            period_relation=period.relation,
        )

    # 5. Context. If the claims differ on any axis they are not answering the
    #    same question, whatever their values happen to be.
    value = _compare_values(a, b)
    if differing:
        axis = _primary(differing)
        return Verdict(
            CONTEXTUAL, axis,
            _explain_contextual(a, b, axis, differing, value),
            a.ref, b.ref, differing_axes=differing, value=value,
            period_relation=period.relation,
        )

    # 6. Same entity, same metric, same period, same context. Only now do the
    #    values get to speak.
    notes = list(period.ambiguous and ["one period was read from a bare "
                                       "year-end date with no declaration to "
                                       "resolve it"] or [])
    if value.relation == "unknown":
        return Verdict(
            INSUFFICIENT_EVIDENCE, "value", value.reason, a.ref, b.ref,
            value=value, period_relation=period.relation, notes=notes,
        )
    if value.agree:
        return Verdict(
            CORROBORATES, None,
            f"same {a.metric} for {a.entity} in {a.period.label}, and the "
            f"{value.reason}",
            a.ref, b.ref, value=value, period_relation=period.relation, notes=notes,
        )
    return Verdict(
        CONTRADICTS, None,
        f"same {a.metric} for {a.entity} in {a.period.label}, comparable on "
        f"every stated axis, and the values are {value.reason}",
        a.ref, b.ref, value=value, period_relation=period.relation, notes=notes,
    )


def _intervals_disjoint(a: Comparable, b: Comparable) -> bool:
    """Whether two state claims' validity intervals share no time at all.

    Requires both sides to carry a real interval. A state with no dates at all
    is "true as far as this document knows", which overlaps everything — and
    treating an absence of dates as disjointness would excuse every genuine
    conflict between two undated assertions.
    """
    if not any((a.valid_from, a.valid_to)) or not any((b.valid_from, b.valid_to)):
        return False
    start_a, start_b = a.valid_from or date.min, b.valid_from or date.min
    end_a = date.max if (a.valid_to is None or a.valid_to_is_open) else a.valid_to
    end_b = date.max if (b.valid_to is None or b.valid_to_is_open) else b.valid_to
    return not (start_a <= end_b and start_b <= end_a)


def _compare_values(a: Comparable, b: Comparable) -> ValueComparison:
    if a.claim_type == "state":
        return compare_text_values(a.value_text, b.value_text)
    return compare_values(
        a.value_canonical, b.value_canonical,
        a_precision=a.precision, b_precision=b.precision,
    )


def _axis_differences(a: Comparable, b: Comparable) -> set[str]:
    """Axes on which the two claims say different things.

    An axis stated by one and never mentioned by the other counts. That is the
    "absent is not equal" rule: the Economic Survey attaches
    `estimate_vintage=first advance estimates` to its GDP figure and the RBI
    attaches nothing, and that difference is the entire explanation of the gap.
    """
    left, right = a.axis_values(), b.axis_values()
    return {
        axis
        for axis in set(left) | set(right)
        if left.get(axis) != right.get(axis)
    }


def _primary(axes: list[str] | set[str]) -> str:
    ordered = sorted(
        axes,
        key=lambda axis: (
            AXIS_PRIORITY.index(axis) if axis in AXIS_PRIORITY else len(AXIS_PRIORITY),
            axis,
        ),
    )
    return ordered[0]


def _explain_contextual(
    a: Comparable, b: Comparable, axis: str, differing: list[str],
    value: ValueComparison,
) -> str:
    """Templated from the difference, so the same inputs always explain the same.

    A model could write a nicer sentence. It could also write a different one
    next time, and a verdict whose reasoning changes between runs is not a
    verdict.
    """
    left = a.axis_values().get(axis, "not stated")
    right = b.axis_values().get(axis, "not stated")
    gap = f"Values differ by {value.relative:.2%}" if value.relative else "The values"
    others = [x for x in differing if x != axis]
    also = f" They also differ on {', '.join(others)}." if others else ""
    return (
        f"Both claim {a.metric} for {a.entity} in {a.period.label}. {gap}, but "
        f"the claims differ on {axis} ({left} against {right}) — not directly "
        f"comparable.{also}"
    )
