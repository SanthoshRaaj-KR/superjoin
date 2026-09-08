"""L5d — the second look a contradiction has to survive.

The gate is careful and still not careful enough, because it can only reason
about context that reached it. When two claims arrive with the same entity, the
same metric, the same period and no distinguishing qualifier, the gate is
obliged to call them a contradiction — and it is right to, given what it was
handed. The question this layer asks is whether what it was handed was complete.

Auditing eight surviving contradictions across the corpus, not one was a real
disagreement. Every single one was a qualifier that *was printed on the page*
and did not make it into the claim:

    "Real hourly wages have grown by 16 and 26 percent since 2018 in rural
     and urban areas, respectively"          -> one sentence, two facts, axis `area`

    July WEO  | 6.4 | 6.4                    -> a scenario table whose row labels
    Current   | 6.6 | 6.2                       are the axis, `estimate_vintage`

    Less: Exceptional Items | 224.10         -> the same figure as `(224.10)`,
                                                with the sign in the row label

So a contradiction is not a conclusion the gate is entitled to publish on its
own. It is a **hypothesis that has to survive an investigation**: go back to the
page, look for the distinction, and only report a conflict when the page really
does not draw one.

**The investigator recovers context. It never issues a verdict.** That
distinction is the whole design and it is what keeps the project's thesis
intact. The model is asked one question — "is there a qualifier on this page
that these two claims differ on?" — and its answer is a proposed *fact about
the document*, which is then grounded against the page like any other claim and
fed back through the same deterministic ``compare()``. The gate decides, twice.
Nothing here can write a verdict directly, and a recovered axis that cannot be
found on the page is discarded.

**The asymmetry is deliberate.** A recovered axis can only ever make two claims
*less* comparable — CONTRADICTS becomes CONTEXTUAL or INSUFFICIENT_EVIDENCE, and
never CORROBORATES. There is no path here that turns a disagreement into an
agreement, because "look harder until they match" is precisely the failure mode
this layer would otherwise introduce. Confirmed by assertion, not by hope; see
``reconsider``.

**Scouts run before the model and are free.** Three of them are pure text
analysis over the page the claim came from, and one — the sign-convention scout
— resolves its case with no model call at all. They also pay for themselves in
precision: a model shown the whole page invents distinctions, and a model shown
the specific row that differs mostly does not.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .compare import (
    CONTEXTUAL,
    CONTRADICTS,
    INSUFFICIENT_EVIDENCE,
    Comparable,
    Verdict,
    compare,
)
from .grounding import locate

log = logging.getLogger(__name__)

# A proposal below this is not acted on. Set where it is because the cost is
# asymmetric: a missed recovery leaves a contradiction the reader can still
# inspect, while a wrong one silently erases a real conflict.
MIN_CONFIDENCE = 0.6

# The recovered evidence must ground at least this well on the claim's own page.
# `row_subset` (0.6) is deliberately allowed — a scenario table's row label is
# found by row reconstruction and by nothing stricter.
MIN_GROUNDING = 0.6

# How close two magnitudes must be, relatively, to read as one figure printed
# with two sign conventions rather than as two different numbers.
SIGN_MIRROR_TOLERANCE = 0.005

# Row labels that carry a sign in Indian financial statements. `Less:` before a
# figure means the printed number is subtracted, which is the same information
# parentheses carry — and the annual report uses both, on the same figure, nine
# pages apart.
_SIGN_LABELS = re.compile(
    r"^\s*(less|add|deduct|plus|minus)\s*:", re.IGNORECASE
)

_COORDINATION = re.compile(
    r"\b(respectively|versus|vs\.?|against|compared with|compared to|and)\b",
    re.IGNORECASE,
)


@dataclass
class Lead:
    """Something a scout noticed, and the text a reader would need to judge it.

    A lead is not a finding. It says where to look and why, and carries only the
    span of page text that prompted it — which is what keeps the investigator's
    prompt small enough to stay honest.
    """

    scout: str
    note: str
    axis_hint: str | None = None
    a_context: str = ""
    b_context: str = ""
    strength: float = 0.5


@dataclass
class Recovery:
    """A qualifier recovered from the page, grounded, ready to re-enter the gate."""

    axis: str
    a_value: str | None
    b_value: str | None
    method: str
    confidence: float
    reason: str
    a_evidence: str | None = None
    b_evidence: str | None = None
    grounding: float = 0.0
    leads: list[str] = field(default_factory=list)

    @property
    def one_sided(self) -> bool:
        """Only one claim's value could be determined.

        Not a failure. The page distinguishes the two figures on an axis it
        states for one of them and not the other, which is the definition of an
        undetermined material axis — so the pair is blocked rather than
        explained. Absent is not equal, applied to an axis nobody knew to look
        for until now.
        """
        return bool(self.a_value) != bool(self.b_value)


# --- scouts: deterministic, free, and run first -----------------------------


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


def _rows_containing(page_text: str, needle: str) -> list[str]:
    """Every line of the page that contains a literal string."""
    if not needle:
        return []
    return [
        line.strip()
        for line in (page_text or "").splitlines()
        if needle in line
    ]


def _row_label(row: str) -> str:
    """The leading label of a reconstructed table row.

    Rows are rendered `label | cell | cell`, so the label is what precedes the
    first separator. This is the whole trick behind the scenario-table case:
    `July WEO | 6.4 | 6.4` and `Current | 6.6 | 6.2` differ in exactly one
    place, and it is not the numbers.
    """
    head = row.split("|")[0].strip()
    return head


def scout_sign_convention(a: Comparable, b: Comparable,
                          a_page: str, b_page: str) -> Recovery | None:
    """Two printings of one figure whose sign is carried differently.

    Fully deterministic and needs no model: the magnitudes must match to within
    rounding, the signs must differ, and one side must be printed under a
    sign-carrying row label. The annual report gives `(224.10)` in the
    consolidated statement and `Less: Exceptional Items | ... | 224.10` in the
    summary — the same rupees, 200% "apart".
    """
    x, y = a.value_canonical, b.value_canonical
    if x is None or y is None or x == 0 or y == 0:
        return None
    if (x > 0) == (y > 0):
        return None
    if abs(abs(x) - abs(y)) > SIGN_MIRROR_TOLERANCE * max(abs(x), abs(y)):
        return None

    found: dict[str, tuple[str, str]] = {}
    for side, claim, page in (("a", a, a_page), ("b", b, b_page)):
        rows = _rows_containing(page, (claim.evidence_quote or "").strip()[:40])
        rows += _rows_containing(page, claim.value_text or "")
        for row in rows + [claim.evidence_quote or ""]:
            if _SIGN_LABELS.search(row):
                found[side] = ("subtracted in a labelled row",
                               _SIGN_LABELS.search(row).group(0).strip())
                break
        else:
            if "(" in (claim.evidence_quote or ""):
                found[side] = ("parenthesised", "(…)")

    if len(found) < 2 or found["a"][0] == found["b"][0]:
        # One side's convention could not be identified, or both are the same —
        # in which case the sign difference is real and must stand.
        if len(found) != 1:
            return None
        side, (kind, token) = next(iter(found.items()))
        return Recovery(
            axis="sign_convention",
            a_value=kind if side == "a" else None,
            b_value=kind if side == "b" else None,
            method="scout:sign_convention",
            confidence=0.7,
            reason=(
                f"the magnitudes match to within rounding and the signs differ; "
                f"one figure is {kind} ({token!r}) and the other's convention "
                f"could not be read from the page"
            ),
            a_evidence=a.evidence_quote if side == "a" else None,
            b_evidence=b.evidence_quote if side == "b" else None,
            grounding=1.0,
        )

    return Recovery(
        axis="sign_convention",
        a_value=found["a"][0],
        b_value=found["b"][0],
        method="scout:sign_convention",
        confidence=0.9,
        reason=(
            f"the same magnitude printed two ways: one {found['a'][0]} "
            f"({found['a'][1]!r}), the other {found['b'][0]} "
            f"({found['b'][1]!r}). A row label carries the sign exactly as "
            f"parentheses do"
        ),
        a_evidence=a.evidence_quote,
        b_evidence=b.evidence_quote,
        grounding=1.0,
    )


def _window(page: str, anchor: str, before: int = 400, after: int = 300) -> str:
    """The page text around an anchor, or the anchor alone if it is not found."""
    anchor = (anchor or "").strip()
    if not anchor:
        return ""
    idx = page.find(anchor[:60])
    if idx < 0:
        return anchor
    return page[max(0, idx - before): idx + len(anchor) + after]


def scout_shared_span(a: Comparable, b: Comparable,
                      a_page: str = "", b_page: str = "") -> Lead | None:
    """Both claims were read out of the same sentence or row.

    The strongest signal available. If one span yielded two different values for
    what was recorded as one metric, something almost certainly distinguishes
    them — "16 and 26 percent ... in rural and urban areas, respectively" is two
    facts wearing one sentence.

    The context handed on is the page *around* the span, not the span itself,
    and that is the whole difference between this scout working and not. When
    two claims share a quote, the distinguishing label is by definition the
    thing the quote failed to capture: the deck prints
    ``EBITDA / EBITDA margin | Adj. EBITDA / Adj. EBITDA margin`` on the line
    directly above ``FY23: ₹(452) Cr / (6.3%) | FY23: ₹(404) Cr / (5.6%)``, and
    an investigator shown only the second line has been handed the problem
    without the answer and will either guess or give up.
    """
    qa, qb = _norm(a.evidence_quote), _norm(b.evidence_quote)
    if not qa or not qb:
        return None
    if qa == qb:
        note = ("both values were read out of one span, so whatever separates "
                "them is in the surrounding text")
    elif qa in qb or qb in qa:
        note = "one claim's evidence span contains the other's"
    else:
        return None
    strength = 0.9 if _COORDINATION.search(a.evidence_quote or "") else 0.75
    return Lead(
        scout="shared_span", note=note,
        a_context=_window(a_page, a.evidence_quote) or (a.evidence_quote or ""),
        b_context=_window(b_page, b.evidence_quote) or (b.evidence_quote or ""),
        strength=strength,
    )


def scout_row_label(a: Comparable, b: Comparable,
                    a_page: str, b_page: str) -> Lead | None:
    """The two values sit in table rows whose labels differ.

    The IMF's scenario table is two rows, `July WEO` and `Current`, holding
    forecasts for the same two years. The extractor captured the column (the
    period) and dropped the row (the vintage), so four claims came back looking
    like two contradictions.
    """
    # The figure as printed. `value_text` is null for measurements, and looking
    # for the canonical number finds nothing at all — the page says "(6.3%)",
    # never -6.3.
    needle_a = a.value_raw or a.value_text or ""
    needle_b = b.value_raw or b.value_text or ""
    rows_a = [r for r in _rows_containing(a_page, needle_a) if "|" in r]
    rows_b = [r for r in _rows_containing(b_page, needle_b) if "|" in r]
    if not rows_a or not rows_b:
        return None
    label_a, label_b = _row_label(rows_a[0]), _row_label(rows_b[0])
    if not label_a or not label_b or _norm(label_a) == _norm(label_b):
        return None
    return Lead(
        scout="row_label",
        note=f"the values sit in rows labelled {label_a!r} and {label_b!r}",
        a_context=rows_a[0], b_context=rows_b[0], strength=0.8,
    )


def scout_neighbourhood(a: Comparable, b: Comparable,
                        a_page: str, b_page: str) -> Lead | None:
    """Failing everything else, show the investigator where each value lives.

    Weak on purpose. It produces a lead for almost any pair, so it carries the
    lowest strength and exists to give the model *something* grounded to read
    rather than to assert that a distinction exists.
    """
    ctx_a = _window(a_page, a.evidence_quote, before=240, after=240)
    ctx_b = _window(b_page, b.evidence_quote, before=240, after=240)
    if not ctx_a or not ctx_b or _norm(ctx_a) == _norm(ctx_b):
        return None
    return Lead(
        scout="neighbourhood",
        note="the text surrounding each value differs",
        a_context=ctx_a, b_context=ctx_b, strength=0.3,
    )


def scout(a: Comparable, b: Comparable, a_page: str, b_page: str) -> list[Lead]:
    """Every lead, strongest first."""
    leads = [
        s(a, b, a_page, b_page)
        for s in (scout_shared_span, scout_row_label, scout_neighbourhood)
    ]
    return sorted((x for x in leads if x), key=lambda l: -l.strength)


# --- the investigator: proposes a fact about the page, never a verdict -------


class AxisProposal(BaseModel):
    """What the investigator is allowed to say. Structured so it cannot argue."""

    distinguished: bool = Field(
        description=(
            "True only if the page itself draws a distinction between the two "
            "figures. False when the page really does report two different "
            "values for one thing."
        )
    )
    axis: str | None = Field(
        default=None,
        description=(
            "A short snake_case name for the distinction, e.g. area, "
            "estimate_vintage, scenario, measure_basis, sign_convention. Reuse "
            "an obvious existing name rather than inventing a synonym."
        ),
    )
    a_value: str | None = Field(
        default=None,
        description="The axis value for claim A, in the page's own words. Null if the page does not state one for A.",
    )
    b_value: str | None = Field(
        default=None, description="The same for claim B."
    )
    a_evidence: str | None = Field(
        default=None,
        description=(
            "A short verbatim span from claim A's page proving a_value. Copy "
            "characters exactly. This is checked against the page and the whole "
            "proposal is discarded if it is not found."
        ),
    )
    b_evidence: str | None = Field(default=None, description="The same for claim B.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, in plain language.")


SYSTEM = """Two figures were extracted from a document and recorded as the same \
measurement, for the same entity and the same period. They disagree. Before that \
is reported as a contradiction, your job is to check whether the page itself \
distinguishes them.

You are NOT deciding whether the claims contradict. You are answering one narrow \
question of fact: does the source text draw a distinction that the extraction \
missed? Something else decides what that means.

Distinctions that hide in these documents:
- one sentence carrying two facts \
("grown by 16 and 26 percent ... in rural and urban areas, respectively")
- a table row label the extraction dropped while keeping the column \
("July WEO" against "Current" — a forecast vintage)
- two variants of a measure printed side by side \
(EBITDA against adjusted EBITDA; before and after an exceptional item)
- a sign carried by a row label ("Less: ...") rather than by parentheses
- a different basis, scope, price base, or segment stated in a heading above

Rules that matter more than being helpful:

- QUOTE THE LABEL, NOT THE NUMBER. Your evidence span must be the text where \
the distinguishing words appear. Quoting the row the figure sits in is the \
common mistake and it fails: for "EBITDA margin | Adj. EBITDA margin" above a \
row of figures, the evidence for a_value is the header text, not the figures.
- Every value you give must appear WORD FOR WORD inside the evidence span you \
quote for it, and that span must appear on the page. Both are checked \
mechanically and the whole proposal is discarded if either fails.
- NEVER INVENT A LABEL FOR THE UNLABELLED SIDE. If the page labels one figure \
and says nothing about the other, give the one it labels and leave the other \
null. Writing "current", "actual", "baseline", "normal" or "headline" for a \
side the page does not describe is supplying a label rather than reading one. \
"Core inflation increased to 4.6 percent (from 3.5 percent FY2024/25 average)" \
labels only the second figure: b_value is "FY2024/25 average" and a_value is \
null.
- If the page does not distinguish them, answer distinguished=false. That is a \
useful answer, not a failure — it is how a real disagreement gets reported.
- A wrong axis erases a real contradiction permanently and silently. A missing \
one leaves a pair for a human to read. When unsure, answer false."""


def _prompt(a: Comparable, b: Comparable, leads: list[Lead]) -> str:
    parts = [
        f"Metric: {a.metric}",
        f"Entity: {a.entity}",
        f"Period: {a.period.label}",
        "",
        f"CLAIM A — value {a.value_text or a.value_canonical} "
        f"(page {a.page_no}, document {a.document})",
        f"  quoted: {a.evidence_quote}",
        f"  qualifiers already recorded: {a.qualifiers or '{}'}",
        "",
        f"CLAIM B — value {b.value_text or b.value_canonical} "
        f"(page {b.page_no}, document {b.document})",
        f"  quoted: {b.evidence_quote}",
        f"  qualifiers already recorded: {b.qualifiers or '{}'}",
        "",
        "What a first pass over the pages noticed:",
    ]
    for lead in leads[:3]:
        parts.append(f"- [{lead.scout}] {lead.note}")
        if lead.a_context:
            parts.append(f"    near A: {lead.a_context[:400]}")
        if lead.b_context and _norm(lead.b_context) != _norm(lead.a_context):
            parts.append(f"    near B: {lead.b_context[:400]}")
    return "\n".join(parts)


# A transient network failure is not evidence that the page draws no
# distinction, but it looks exactly like one from the caller's side: the
# investigation returns nothing and the contradiction is reported, and two runs
# over the same corpus disagree about the same pages.
#
# One extra attempt, not three. The SDK already retries twice with backoff
# inside each of these, so three attempts here is nine requests — and with the
# SDK's default ten-minute timeout that is an hour of waiting disguised as a
# slow run. Bounded at the client instead; see llm/client.py.
NETWORK_ATTEMPTS = 2
NETWORK_BACKOFF = 2.0


def investigate(a: Comparable, b: Comparable, leads: list[Lead],
                *, model: str | None = None) -> Recovery | None:
    """Ask the model what the page distinguishes. One call, structured."""
    import time

    from .llm.client import LLMUnavailable, structured

    if not leads:
        return None

    for attempt in range(NETWORK_ATTEMPTS):
        try:
            proposal = structured(
                response_model=AxisProposal,
                system=SYSTEM,
                user=_prompt(a, b, leads),
                model=model,
            )
            break
        except LLMUnavailable:
            raise  # a missing key is not transient and must not be retried
        except Exception:
            if attempt == NETWORK_ATTEMPTS - 1:
                raise
            log.warning("investigation attempt %s failed for %s/%s, retrying",
                        attempt + 1, a.ref, b.ref)
            time.sleep(NETWORK_BACKOFF * (attempt + 1))
    if not proposal.distinguished or not proposal.axis:
        return None
    if not (proposal.a_value or proposal.b_value):
        return None
    return Recovery(
        axis=proposal.axis.strip().lower().replace(" ", "_"),
        a_value=proposal.a_value,
        b_value=proposal.b_value,
        a_evidence=proposal.a_evidence,
        b_evidence=proposal.b_evidence,
        method="investigated",
        confidence=proposal.confidence,
        reason=proposal.reason,
        leads=[l.scout for l in leads[:3]],
    )


# --- grounding a recovery, and re-entering the gate -------------------------


_TRIVIAL_LABEL = re.compile(
    r"^[\s\d.,%()\-–—]*(per ?cent(age)?|%|pts?|bps|percentage points?|"
    r"cr|crore|mn|million|bn|billion|lakh|rs|inr|usd|₹|\$)?[\s.,%)]*$",
    re.IGNORECASE,
)


def _is_circular(value: str | None, claim: Comparable) -> bool:
    """Whether a proposed axis value quotes the figure it is supposed to label.

    **A label never contains the number it labels.** `July WEO`, `Current`,
    `urban areas`, `Adj. EBITDA margin`, `first advance estimate` — not one of
    them names its own value, because a label says which *kind* of measurement
    this is, and the measurement is the other half of the pair.

    Both versions of this guard were written against real damage. The first was
    a proposal of ``a_value="4.6 percent"`` for the figure 4.6: the number
    wearing a label's clothes, and one that satisfies "the value must appear on
    the page" perfectly, because of course it does.

    That version stripped the figure and asked whether anything substantive
    remained, and it was not enough. Asked what separated the RBI's *"real GDP
    growth for 2025-26 is projected at 6.5 per cent"* from the IMF's 6.6 per
    cent for the same year, the investigator proposed a `scenario` axis with
    ``a_value="2025-26 is projected at 6.5 per cent, with risks"`` — the claim's
    own sentence, padded with enough words to look like a description once the
    figure was removed. It grounded, because the sentence really is on the page.
    And it dissolved the one genuine cross-institution disagreement in the
    corpus, which is the single worst thing this layer can do.

    So the test is containment, not residue. A side whose proposed label quotes
    its own figure is treated as unlabelled — which blocks the pair, or leaves
    the contradiction standing, and both are recoverable by a reader in a way
    that a false explanation is not.
    """
    if not value:
        return False
    label = _norm(value)
    if not label:
        return True
    figures = {
        _norm(token).strip("()")
        for token in (claim.value_raw, claim.value_text)
        if token and _norm(token).strip("()")
    }
    if any(figure in label for figure in figures):
        return True
    # Nothing left but the figure and its unit is not a label either.
    residue = label
    for figure in figures:
        residue = residue.replace(figure, " ")
    return bool(_TRIVIAL_LABEL.match(residue.strip()))


def ground(recovery: Recovery, a: Comparable, b: Comparable,
           a_page: str, b_page: str) -> Recovery | None:
    """Hold a recovered axis to the standard every claim is held to.

    The proposal is a statement about the document, so it is checked the same
    way: the quoted span must be locatable on the page it is attributed to, and
    the axis value must appear inside that span. Without this the layer becomes
    a machine for explaining away inconvenient conflicts, which is worse than
    not having it.
    """
    if recovery.method.startswith("scout:"):
        return recovery  # deterministic, derived from the page rather than proposed

    # A rejected label is not the same thing as an absent one, and collapsing
    # the two is how the RBI/IMF disagreement nearly escaped: the investigator
    # proposed a circular label for the RBI side and a real one for the IMF
    # side, and dropping only the bad half left a one-sided recovery — which
    # blocks the pair on an "undetermined" axis and takes it out of the
    # residual set just as effectively as explaining it.
    #
    # A one-sided recovery is legitimate only when the model *declined* to
    # label a side, which is evidence that the page labels one figure and not
    # the other. A side we deleted is evidence the model was reaching, and the
    # whole proposal goes with it.
    if _is_circular(recovery.a_value, a) or _is_circular(recovery.b_value, b):
        log.debug("recovery rejected: a label quoting its own figure")
        return None
    if not (recovery.a_value or recovery.b_value):
        return None

    scores: list[float] = []
    for value, evidence, page in (
        (recovery.a_value, recovery.a_evidence, a_page),
        (recovery.b_value, recovery.b_evidence, b_page),
    ):
        if not value:
            continue
        if not evidence:
            return None
        result = locate(evidence, page, page)
        if not result.ok or result.score < MIN_GROUNDING:
            log.debug("recovery rejected: %r not on page", evidence[:60])
            return None
        if _norm(value) not in _norm(evidence):
            log.debug("recovery rejected: %r not inside its own evidence", value)
            return None
        scores.append(result.score)

    if not scores:
        return None
    recovery.grounding = min(scores)
    return recovery


def apply(a: Comparable, b: Comparable, recovery: Recovery) -> tuple[Comparable, Comparable]:
    """Put the recovered axis onto the claims, as extraction should have.

    When only one side's value is known the axis goes into the other's
    ``unknown_qualifiers`` rather than being left off. That is the difference
    between "these differ on vintage" and "one of these has a vintage and we do
    not know the other's" — the second is not an explanation and must not be
    counted as one.
    """
    from dataclasses import replace

    left = replace(a, qualifiers=dict(a.qualifiers or {}),
                   unknown_qualifiers=list(a.unknown_qualifiers or []))
    right = replace(b, qualifiers=dict(b.qualifiers or {}),
                    unknown_qualifiers=list(b.unknown_qualifiers or []))

    for claim, value in ((left, recovery.a_value), (right, recovery.b_value)):
        if value:
            claim.qualifiers[recovery.axis] = value
        elif recovery.axis not in claim.unknown_qualifiers:
            claim.unknown_qualifiers.append(recovery.axis)
    return left, right


def reconsider(a: Comparable, b: Comparable, recovery: Recovery) -> Verdict:
    """Re-run the same gate on the repaired claims.

    The verdict still comes from ``compare()``. Nothing in this module writes
    one, and the assertion below is not decoration: a recovered axis can only
    ever make two claims less comparable, so a path from CONTRADICTS to
    CORROBORATES would mean context had been invented rather than found.
    """
    left, right = apply(a, b, recovery)
    verdict = compare(left, right)
    if verdict.verdict not in {CONTEXTUAL, INSUFFICIENT_EVIDENCE, CONTRADICTS}:
        raise AssertionError(
            f"reconciliation moved a contradiction to {verdict.verdict}; a "
            f"recovered axis must never make two claims more comparable"
        )
    return verdict


@dataclass
class Reconciliation:
    """What the second look concluded about one pair."""

    verdict: Verdict
    recovery: Recovery | None
    leads: list[Lead]
    changed: bool

    @property
    def survived(self) -> bool:
        return self.verdict.verdict == CONTRADICTS


def reconcile(
    a: Comparable, b: Comparable, *, a_page: str = "", b_page: str = "",
    investigator=investigate, min_confidence: float = MIN_CONFIDENCE,
) -> Reconciliation:
    """The second look, end to end, for one contradicting pair.

    ``investigator`` is injected so the whole path — scouting, grounding,
    applying, re-running the gate — is testable without an API key, and so the
    model can be swapped without touching the logic that constrains it.
    """
    original = compare(a, b)
    if original.verdict != CONTRADICTS:
        return Reconciliation(original, None, [], changed=False)

    sign = scout_sign_convention(a, b, a_page, b_page)
    leads = scout(a, b, a_page, b_page)

    recovery = sign
    if recovery is None and investigator is not None:
        try:
            recovery = investigator(a, b, leads)
        except Exception as exc:  # a failed investigation leaves the finding
            log.warning("investigation failed for %s/%s: %s", a.ref, b.ref, exc)
            recovery = None

    if recovery is None or recovery.confidence < min_confidence:
        return Reconciliation(original, None, leads, changed=False)

    grounded = ground(recovery, a, b, a_page, b_page)
    if grounded is None:
        return Reconciliation(original, None, leads, changed=False)

    verdict = reconsider(a, b, grounded)
    verdict.notes = list(verdict.notes) + [
        f"recovered on a second look: {grounded.axis} "
        f"({grounded.method}, {grounded.reason})"
    ]
    return Reconciliation(
        verdict, grounded, leads, changed=verdict.verdict != CONTRADICTS
    )
