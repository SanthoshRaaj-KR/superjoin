"""L5a — deciding whether two numbers agree.

This looks like the easy part and is not. The gold set contains a pair that
defeats every fixed tolerance:

    annual report   Revenues from sale of traded goods FY23    16.54 ₹Mn
    earnings deck   Revenue from traded goods FY23                 2 ₹Cr

Those are the same fact and they are 21% apart. One crore is the deck's entire
precision at that magnitude, so `2` is what `16.54 million` looks like when you
round it. A 1% tolerance calls this a contradiction; a 25% tolerance calls
almost everything corroboration, including the standalone-versus-consolidated
pair that differs by 9.2% and genuinely needs explaining.

**So tolerance comes from how the numbers were written, not from a constant.**
A figure printed as ``2`` in crore asserts a true value somewhere in
[1.5, 2.5] crore. A figure printed as ``16.54`` in million asserts one in
[16.535, 16.545] million. Two claims agree when a single true value could have
produced both roundings:

    |a - b|  <  (precision(a) + precision(b)) / 2

Precision is read off the raw string — its decimal places, scaled by its unit.
That is the only place the information exists: ``value_num`` has lost it, and
``2.0`` and ``2.00`` mean different things about how much the writer knew.

Two consequences worth stating, because both are load-bearing:

- **Adjacent roundings disagree.** RBI's 6.5% and the IMF's 6.6% each have
  precision 0.1, so the threshold is exactly 0.1 and the comparison is strict.
  They are the nearest two values that could have been printed, and they still
  are not the same forecast. That pair is the project's one genuine
  contradiction and it must survive.
- **Precision is not confidence.** A number printed to two decimals is a claim
  about how finely it was measured, nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Digits after the decimal point, in a string that may carry thousands
# separators, a currency symbol, brackets for negatives, or a trailing percent.
_DECIMALS = re.compile(r"[.,](\d+)\s*%?\s*\)?\s*$")
_DIGITS = re.compile(r"\d")

# Floating point makes an exact boundary comparison unreliable: 6.6 - 6.5 is
# 0.10000000000000053, not 0.1. The comparison is strict by design (adjacent
# roundings disagree), so the threshold is shaved by a hair to keep a genuine
# tie on the "disagree" side rather than letting rounding error decide it.
_SLACK = 1 - 1e-9


def written_precision(value_raw: str | None, scale: float = 1.0) -> float | None:
    """The granularity a printed figure asserts, in base units.

    ``"2"`` in crore is precise to 1 crore; ``"16.54"`` in million is precise to
    0.01 million. Returns None when the string carries no digits at all, which
    is how a nil marker like ``"-"`` arrives.
    """
    if not value_raw:
        return None
    text = value_raw.strip()
    if not _DIGITS.search(text):
        return None

    match = _DECIMALS.search(text)
    # A trailing group of exactly three digits after a comma is a thousands
    # separator, not a decimal fraction: `72,253` is precise to units, not to
    # thousandths. A dot is unambiguous.
    if match and not (match.group(0)[0] == "," and len(match.group(1)) == 3):
        places = len(match.group(1))
    else:
        places = 0
    return (10.0**-places) * scale


@dataclass
class ValueComparison:
    relation: str  # agree | disagree | unknown
    reason: str
    difference: float | None = None
    relative: float | None = None
    threshold: float | None = None

    @property
    def agree(self) -> bool:
        return self.relation == "agree"


def compare_values(
    a_canonical: float | None,
    b_canonical: float | None,
    *,
    a_precision: float | None = None,
    b_precision: float | None = None,
) -> ValueComparison:
    """Whether two canonical values could be roundings of one true value.

    Both values must already be in the same base unit; placing them there is
    the unit layer's job and this makes no attempt to check it.
    """
    if a_canonical is None or b_canonical is None:
        return ValueComparison("unknown", "one or both values could not be placed")

    difference = abs(a_canonical - b_canonical)
    largest = max(abs(a_canonical), abs(b_canonical))
    relative = difference / largest if largest else 0.0

    # With no precision on either side, fall back to exact equality rather than
    # to a guessed tolerance. Inventing one here would be the same mistake as
    # inventing an exchange rate.
    if a_precision is None and b_precision is None:
        if difference == 0:
            return ValueComparison("agree", "identical values", 0.0, 0.0)
        return ValueComparison(
            "disagree",
            "values differ and neither states its precision",
            difference,
            relative,
        )

    threshold = ((a_precision or 0.0) + (b_precision or 0.0)) / 2
    if difference < threshold * _SLACK:
        return ValueComparison(
            "agree",
            _describe_agreement(relative, threshold, largest),
            difference,
            relative,
            threshold,
        )
    return ValueComparison(
        "disagree",
        _describe_disagreement(difference, relative, threshold),
        difference,
        relative,
        threshold,
    )


def _num(value: float) -> str:
    """Enough significant figures to be readable at any magnitude."""
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:,.0f}"
    if magnitude >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:.3g}"


def _pct(part: float, whole: float) -> str:
    if not whole:
        return "0%"
    share = part / whole
    # A financial table's precision is often a millionth of its values, and
    # "0.00%" says nothing useful about how tight the tolerance really was.
    if share >= 0.0001:
        return f"{share:.2%}"
    return f"{share:.4%}" if share >= 1e-6 else f"{share:.1e} of the value"


def _describe_agreement(relative: float, threshold: float, largest: float) -> str:
    if relative == 0:
        return "identical values"
    return (
        f"{relative:.2%} apart, within the {_pct(threshold, largest)} that rounding "
        "at the coarser stated precision would explain"
    )


def _describe_disagreement(difference: float, relative: float, threshold: float) -> str:
    """Say *why* the gap is too big, in the terms that make it obvious.

    The adjacency case gets its own wording because it is the project's single
    genuine contradiction and the percentage form is actively confusing there:
    the RBI's 6.5% and the IMF's 6.6% differ by 1.52% and rounding explains up
    to 1.52%, which reads like a tie rather than the deliberate strictness it
    is. Said plainly — the nearest two figures either could have printed, and
    still not the same forecast — it reads as the finding it is.
    """
    if threshold and difference < threshold * 2:
        return (
            f"{_num(difference)} apart — the smallest gap two figures printed at "
            "this precision can have. Adjacent, and still not the same value"
        )
    return (
        f"{relative:.2%} apart, more than rounding at the stated precision can "
        f"explain (at most {_num(threshold)})"
    )
