"""L5.5 — ContextRank: which context is most likely to explain a disagreement.

The second look already worked. What it did not have was a *reason* for looking
where it looked. Three scouts each carried a hand-set strength — 0.9 for a
shared span, 0.8 for a row label, 0.3 for a neighbourhood — and those numbers
were chosen by a person and never revisited. They ranked the scouts. Nothing
ranked the *axes*.

This ranks the axes, from the structure of the corpus rather than from a
constant, and hands the investigator a shortlist instead of three windows of
page text and a hope.

**The boundary, stated first because it is the whole risk.** This ranks
*contextual relevance*, never truthfulness. Popularity is not truth: a figure
repeated across ten documents is not thereby correct, and a graph that scored
sources by centrality would say it was. So nothing here produces a verdict,
nothing here changes a value, and no score can withdraw a contradiction. The
output is a list of axis names worth asking about. The model still has to find
the values on the page, ``ground`` still has to locate them, and ``compare``
still decides. A wrong ranking costs a wasted lead. It cannot cost a wrong
answer.

**Why personalized, and why two vectors.** Plain PageRank over the corpus
answers "what is important in general", which is precisely the popularity
signal that must not be used. Personalized PageRank seeded on one claim answers
"what is important *near this claim*", which is what a context question needs.

But a single vector seeded on both claims ranks what is central to the pair —
and what is central to the pair is usually what they have in common, which by
definition cannot distinguish them. Both claims are about GDP growth in FY2026;
that is why they are being compared, not why they differ. So two vectors are
computed, one per claim, and two different quantities are read off them:

    connection  min(r_a[n], r_b[n])        n sits near both claims
    divergence  |r_a[n] - r_b[n]| / sum    n sits near one and not the other

An **axis** is a good candidate when it is *connected* — the concept is live in
this neighbourhood, other claims around here carry it. A **value** is a good
candidate when it *diverges* — it is near one claim and not the other. An axis
that both claims already agree on explains nothing, and this is the arithmetic
that says so.

**What the graph is made of.** Claims, and the things claims share: documents,
sections, metrics, periods, the qualifier bindings they already carry, the
axis those bindings belong to, and the distinctive words in their evidence.
Section nodes are what earn the multi-hop walk — a claim inherits nothing from
a section it never declared, but the *other* claims in that section did declare
things, and `claim → section → sibling claim → qualifier → axis` is a four-hop
path that no amount of looking at two pages will find.

**Cost.** About 120 ms per pair over a 1,600-node graph, and no key. That
matters because it runs *before* the model call rather than after: a ranking
computed afterwards would be a statistic about the answer, not a mechanism that
shaped the question.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Claim, Document, Metric, Page

log = logging.getLogger(__name__)

# Standard damping. At 0.85 a walk restarts at the seed roughly every seven
# steps, which over a graph this shallow means the fourth hop still carries
# usable mass and the tenth does not — about right for "near this claim".
DAMPING = 0.85
ITERATIONS = 40
TOLERANCE = 1e-7

# How far from the seeds to walk. Four hops, because the path that finds an axis
# neither claim states is `claim → section → sibling claim → qualifier → axis`,
# and that is four — the first version said three in the comment and in the
# constant, and reached the sibling's qualifier but never the axis above it.
#
# On this corpus the number turns out not to matter: the graph is small-world
# enough that three hops already reach 933 of its 1,611 nodes, and four, five
# and six reach exactly the same set. **The radius is not what keeps the walk
# local — the restart probability is.** It is a guard for a corpus large enough
# that the reachable set would not fit in memory, and on this one it does
# nothing. Worth stating plainly, because "we only look at a small neighbourhood"
# would be a comfortable claim and it is not true here.
RADIUS = 4

# Below this a pair has no structural candidate worth a model call. It is a
# floor on attention, not on truth: a pair under it is reported as *not
# investigated*, which is a different and more honest thing to say than "no
# context was found".
ATTENTION_FLOOR = 0.004

MAX_TERMS = 12
MAX_CANDIDATES = 6

# Words that appear near every figure in a financial document and distinguish
# nothing. Not a domain vocabulary — a stop list of the grammar that holds
# numbers together.
_NOISE = {
    "the", "and", "for", "with", "from", "was", "were", "has", "have", "had",
    "that", "this", "which", "per", "cent", "percent", "year", "years", "crore",
    "million", "billion", "total", "net", "gross", "value", "values", "figure",
    "figures", "table", "page", "note", "notes", "above", "below", "during",
    "compared", "versus", "respectively", "including", "excluding", "under",
    "over", "into", "than", "been", "also", "its", "their", "our",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")


@dataclass
class Candidate:
    """One axis the structure suggests is worth asking about."""

    axis: str
    connection: float          # how strongly the axis links both claims
    divergence: float          # how one-sided its values are across the pair
    score: float
    values: list[str] = field(default_factory=list)
    why: str = ""

    def __str__(self) -> str:  # pragma: no cover - console convenience
        return f"{self.axis} {self.score:.3f}"


@dataclass
class Ranking:
    """What ContextRank concluded about one suspicious pair."""

    candidates: list[Candidate] = field(default_factory=list)
    terms: list[tuple[str, float]] = field(default_factory=list)
    attention: float = 0.0
    nodes: int = 0
    edges: int = 0

    # The merged shortlist, once the local page windows have been folded in.
    shortlist: list[str] = field(default_factory=list)

    @property
    def worth_investigating(self) -> bool:
        """Advisory, and deliberately not enforced by default.

        Measured before it was believed: claims 59 and 154 — the pair the deck
        distinguishes as `EBITDA margin` against `Adj. EBITDA margin` — score
        **zero** on structural vocabulary, because both were read out of the
        same sentence and share every word in it. A floor that gated model
        calls would have skipped a pair the system demonstrably explains.

        So this reports rather than decides. `relate --budget` can act on it
        when the corpus is large enough that investigating everything is not an
        option, and the trade it makes is recall for cost, stated in those
        terms.
        """
        return self.attention >= ATTENTION_FLOOR

    @property
    def axes(self) -> list[str]:
        return [c.axis for c in self.candidates]

    def with_local(self, a_window: str, b_window: str) -> "Ranking":
        """Fold in the page windows and settle the shortlist.

        Attention is recomputed here rather than in the walk, because a pair
        whose two claims share every word in one sentence has no structural
        vocabulary at all and is still perfectly explainable from the line
        above it.
        """
        local = local_terms(a_window, b_window)
        self.shortlist = merge_terms(self.terms, local)
        self.attention = max(
            self.attention,
            sum(score for _, score in local[:5]) / 5 if local else 0.0,
        )
        return self

    def certificate(self) -> str:
        """One line for the stored reasoning. Templated, never generated."""
        parts = []
        if self.candidates:
            top = self.candidates[0]
            parts.append(f"ContextRank ranked {top.axis} first ({top.score:.2f}) "
                         f"among axes this corpus has already learned")
        words = self.shortlist or [w for w, _ in self.terms]
        if words:
            parts.append("distinguishing vocabulary: " + ", ".join(words[:6]))
        if not parts:
            return ""
        return f"over {self.nodes} nodes — " + "; ".join(parts)


# --- the graph ---------------------------------------------------------------


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")} - _NOISE


class Graph:
    """An undirected weighted graph, built once per corpus and walked per pair.

    Held as plain dicts. The corpus is hundreds of claims, the subgraph around
    any pair is dozens of nodes, and a sparse-matrix library would add a
    dependency to make milliseconds into fewer milliseconds.
    """

    def __init__(self) -> None:
        self.adj: dict[str, dict[str, float]] = defaultdict(dict)

    def link(self, a: str, b: str, weight: float = 1.0) -> None:
        if a == b:
            return
        self.adj[a][b] = self.adj[a].get(b, 0.0) + weight
        self.adj[b][a] = self.adj[b].get(a, 0.0) + weight

    def __len__(self) -> int:
        return len(self.adj)

    def neighbourhood(self, seeds: list[str], radius: int = RADIUS) -> set[str]:
        """Every node within ``radius`` hops of a seed."""
        seen = {s for s in seeds if s in self.adj}
        frontier = set(seen)
        for _ in range(radius):
            nxt: set[str] = set()
            for node in frontier:
                nxt |= set(self.adj[node]) - seen
            if not nxt:
                break
            seen |= nxt
            frontier = nxt
        return seen


def build(session: Session, document_ids: list[int] | None = None,
          *, exclude_relations: set[int] | None = None) -> Graph:
    """The claim-context graph, from what the store already holds.

    Nothing here is computed for the purpose. Every edge is a relationship the
    pipeline recorded on its way past — which page a claim came from, which
    section frame governed it, which qualifier the extractor read off the text,
    which axis a previous review recovered. The graph is a second reading of
    the same facts.

    **Edges are weighted by how rare the thing they connect to is**, and the
    first version of this was useless without it. `consolidation` is recorded
    or declared undetermined on 366 of 666 claims, so on structure alone it is
    adjacent to everything and it won every ranking — including for a pair the
    documents distinguish by sign convention, and for a pair they distinguish
    by which measure of cash flow is meant. That is the popularity signal this
    module exists to avoid, arriving through the side door: a node connected to
    most of the corpus is not *about* any particular pair of claims, and
    weighting by inverse frequency is the statement of exactly that.
    """
    graph = Graph()

    stmt = select(Claim)
    if document_ids:
        stmt = stmt.where(Claim.document_id.in_(document_ids))
    claims = list(session.scalars(stmt))

    metric_names = {m.id: m.canonical_name for m in session.scalars(select(Metric))}
    sections = _section_index(session)
    rare = _inverse_frequency(claims, sections)

    def link(claim_node: str, other: str, base: float = 1.0) -> None:
        graph.link(claim_node, other, base * rare(other))

    for claim in claims:
        node = f"claim:{claim.id}"
        link(node, f"doc:{claim.document_id}", 0.3)

        if claim.metric_id:
            link(node, f"metric:{metric_names.get(claim.metric_id, claim.metric_id)}")
        if claim.period_label:
            link(node, f"period:{claim.period_label}", 0.8)

        section = sections.get((claim.document_id, claim.page_no))
        if section:
            link(node, f"sect:{claim.document_id}:{section}", 0.9)

        # A qualifier is a binding — `consolidation=standalone` — and the axis
        # it belongs to is a separate node above it. That two-level shape is
        # what lets two claims be near the same *axis* while sitting on
        # opposite *values* of it, which is exactly the situation worth finding.
        for axis, value in (claim.qualifiers or {}).items():
            binding = f"qual:{axis}={value}"
            link(node, binding)
            graph.link(binding, f"axis:{axis}", rare(f"axis:{axis}"))

        # An axis the extractor declared it could not determine is a *live*
        # question about this claim, not an absent one. Linking it keeps
        # "absent is not equal" visible to the walk — and the inverse-frequency
        # weight is what stops the three axes that are undetermined nearly
        # everywhere from swamping it.
        for axis in (claim.unknown_qualifiers or []):
            link(node, f"axis:{axis}", 0.4)

        for term in list(_terms(claim.evidence_quote))[:MAX_TERMS]:
            link(node, f"term:{term}", 0.5)

    _add_recovered_axes(session, graph, rare, exclude_relations or set())
    log.debug("context graph: %s nodes", len(graph))
    return graph


def _inverse_frequency(claims: list[Claim], sections: dict):
    """How much a node's presence tells you, given how often it appears.

    A context node adjacent to two claims out of six hundred says something
    about those two. One adjacent to half the corpus says almost nothing about
    any of them, and left at equal weight it dominates every walk simply by
    having more edges. The weight is the usual `log(N / n)`, floored so a
    ubiquitous node is quiet rather than absent.
    """
    import math

    counts: dict[str, int] = defaultdict(int)
    for claim in claims:
        touched = {f"doc:{claim.document_id}"}
        if claim.metric_id:
            touched.add(f"metric:{claim.metric_id}")
        if claim.period_label:
            touched.add(f"period:{claim.period_label}")
        section = sections.get((claim.document_id, claim.page_no))
        if section:
            touched.add(f"sect:{claim.document_id}:{section}")
        for axis, value in (claim.qualifiers or {}).items():
            touched.add(f"qual:{axis}={value}")
            touched.add(f"axis:{axis}")
        for axis in (claim.unknown_qualifiers or []):
            touched.add(f"axis:{axis}")
        for term in list(_terms(claim.evidence_quote))[:MAX_TERMS]:
            touched.add(f"term:{term}")
        for node in touched:
            counts[node] += 1

    total = max(len(claims), 1)

    def weight(node: str) -> float:
        # Metric nodes are keyed by id when counted and by name when linked;
        # a node never counted is by definition rare.
        seen = counts.get(node, 1)
        return max(0.05, math.log(total / seen)) if seen else 1.0

    return weight


def _add_recovered_axes(session: Session, graph: Graph, rare,
                        exclude: set[int]) -> None:
    """Put the axes earlier reviews found into the graph.

    They live on relations, not on claims — a recovery is recorded beside the
    verdict it changed rather than written back onto the claim, because the
    store is append-only and the page never said it in the first place. The
    consequence is that the axes most relevant to contradictions were the ones
    the graph had never heard of: `measure_basis` and `sign_convention` had no
    structure at all, so they could not be ranked however obviously right they
    were. Reading them back is what makes the corpus's learned vocabulary
    available to the walk.
    """
    from .models import Relation

    for relation in session.scalars(
            select(Relation).where(Relation.recovery_axis.is_not(None))):
        # `exclude` is what makes the evaluation honest. Scoring a pair against
        # a graph that contains that pair's own recovery is reading back the
        # answer key: `measure_basis` ranks first for claims 59 and 154 because
        # a previous run wrote `measure_basis` onto claims 59 and 154. Held out,
        # the question becomes the real one — can the structure around a pair
        # name the axis when nothing has told it about *this* pair?
        if relation.id in exclude:
            continue
        axis = f"axis:{relation.recovery_axis}"
        for claim_id, value in ((relation.claim_a_id, relation.recovery_a_value),
                                (relation.claim_b_id, relation.recovery_b_value)):
            node = f"claim:{claim_id}"
            if node not in graph.adj:
                continue
            if value:
                binding = f"qual:{relation.recovery_axis}={value[:60]}"
                graph.link(node, binding, rare(binding))
                graph.link(binding, axis, rare(axis))
            else:
                graph.link(node, axis, 0.4 * rare(axis))


def _section_index(session: Session) -> dict[tuple[int, int], str]:
    """The governing heading for each page, as the context frames recorded it."""
    out: dict[tuple[int, int], str] = {}
    for page in session.scalars(select(Page)):
        for frame in reversed(page.context_json or []):
            path = frame.get("heading_path") or []
            if path:
                out[(page.document_id, page.page_no)] = " › ".join(path)
                break
    return out


# --- the walk ----------------------------------------------------------------


def personalized(graph: Graph, seed: str, within: set[str]) -> dict[str, float]:
    """Personalized PageRank from one seed, restricted to a subgraph.

    Power iteration, restarting at the seed with probability ``1 - DAMPING``.
    The restart is what makes it *personalized* and what keeps the corpus's
    generally-popular nodes from dominating: mass that wanders away comes back
    to this claim, not to the graph's centre of gravity.
    """
    if seed not in graph.adj:
        return {}
    rank = {node: 0.0 for node in within}
    rank[seed] = 1.0

    for _ in range(ITERATIONS):
        nxt = {node: 0.0 for node in within}
        nxt[seed] += 1.0 - DAMPING
        for node, mass in rank.items():
            if mass <= 0.0:
                continue
            edges = {n: w for n, w in graph.adj[node].items() if n in within}
            total = sum(edges.values())
            if not total:
                nxt[seed] += DAMPING * mass    # a dead end restarts
                continue
            share = DAMPING * mass / total
            for neighbour, weight in edges.items():
                nxt[neighbour] += share * weight
        delta = sum(abs(nxt[n] - rank[n]) for n in within)
        rank = nxt
        if delta < TOLERANCE:
            break
    return rank


def rank_pair(graph: Graph, a_id: int, b_id: int) -> Ranking:
    """Rank the axes most likely to explain a disagreement between two claims.

    Two walks, two readings. An axis scores on *connection* — it is live near
    both claims — and its values score on *divergence* — they sit near one and
    not the other. An axis both claims already agree on is connected and not
    divergent, and explains nothing; a stray word near one claim alone is
    divergent and not connected, and is noise. What is worth investigating is
    the axis that is both.
    """
    seed_a, seed_b = f"claim:{a_id}", f"claim:{b_id}"
    within = graph.neighbourhood([seed_a, seed_b])
    ranking = Ranking(nodes=len(within),
                      edges=sum(len(graph.adj[n]) for n in within) // 2)
    if seed_a not in within or seed_b not in within:
        return ranking

    ra = personalized(graph, seed_a, within)
    rb = personalized(graph, seed_b, within)

    def connection(node: str) -> float:
        return min(ra.get(node, 0.0), rb.get(node, 0.0))

    def divergence(node: str) -> float:
        x, y = ra.get(node, 0.0), rb.get(node, 0.0)
        return abs(x - y) / (x + y) if (x + y) > 0 else 0.0

    # Values first: a binding that sits near one claim and not the other is the
    # shape of an explanation, and its axis is what to ask about.
    by_axis: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for node in within:
        if not node.startswith("qual:"):
            continue
        axis, _, value = node[len("qual:"):].partition("=")
        mass = ra.get(node, 0.0) + rb.get(node, 0.0)
        by_axis[axis].append((value, mass, divergence(node)))

    candidates: list[Candidate] = []
    for node in within:
        if not node.startswith("axis:"):
            continue
        axis = node[len("axis:"):]
        conn = connection(node)
        if conn <= 0.0:
            continue
        bindings = sorted(by_axis.get(axis, []), key=lambda v: -v[1])
        # The axis's divergence is its most one-sided binding. An axis whose
        # values are shared cannot be what separates the pair, however central
        # the axis itself is — which is the guard against ranking `period`
        # first on every pair in the corpus.
        spread = max((d for _, _, d in bindings), default=divergence(node))
        candidates.append(Candidate(
            axis=axis,
            connection=conn,
            divergence=spread,
            # Geometric-ish: an axis has to score on both to score at all.
            score=conn * (0.25 + 0.75 * spread),
            values=[v for v, _, _ in bindings[:4]],
            why=_why(axis, bindings, conn, spread),
        ))

    candidates.sort(key=lambda c: -c.score)
    ranking.candidates = candidates[:MAX_CANDIDATES]

    # Distinguishing words: near one claim, not the other. These are what a
    # *new* axis is made of — the corpus has no node for an axis it has never
    # seen, so the only trace of one is vocabulary that sits on one side.
    terms = [
        (node[len("term:"):], divergence(node) * (ra.get(node, 0) + rb.get(node, 0)))
        for node in within if node.startswith("term:")
    ]
    ranking.terms = sorted((t for t in terms if t[1] > 0), key=lambda t: -t[1])[:MAX_TERMS]

    # Attention is the *vocabulary* mass, not the axis score, and getting this
    # the other way round would have been the worst bug in the module. An axis
    # the corpus has never recorded scores zero by construction — and a pair
    # whose explanation is an axis nobody has seen before is precisely the pair
    # most worth spending a model call on. Ranking by known axes and then
    # skipping what scores low would have skipped every genuine discovery while
    # confidently re-investigating the cases already solved.
    ranking.attention = sum(score for _, score in ranking.terms[:5])
    return ranking


def local_terms(a_window: str, b_window: str, keep: int = MAX_TERMS
                ) -> list[tuple[str, float]]:
    """Words that sit near one claim's value and not the other's.

    The graph indexes evidence quotes, which is what the store holds and what
    stays stable across runs. It is not always enough: the IMF distinguishes
    two deficit figures with the phrase "per the authorities' definition",
    which is in the sentence *around* the number rather than in the span the
    extractor captured, and no amount of corpus structure recovers a word that
    was never recorded.

    So the page windows the scouts are already loading get the same treatment,
    at rank time and without entering the graph. Same principle — a word on one
    side and not the other is a candidate distinction — applied to text this
    module does not have to store.
    """
    a, b = _terms(a_window), _terms(b_window)
    only_a, only_b = a - b, b - a
    if not only_a and not only_b:
        return []
    # A word unique to one side scores by how much of that side is unique: a
    # single distinguishing word among twenty shared ones is a sharper signal
    # than twenty unrelated words on unrelated pages.
    shared = len(a & b) or 1
    scored = [(w, 1.0 / (1.0 + len(only_a) / shared)) for w in sorted(only_a)]
    scored += [(w, 1.0 / (1.0 + len(only_b) / shared)) for w in sorted(only_b)]
    return sorted(scored, key=lambda t: -t[1])[:keep]


def merge_terms(structural: list[tuple[str, float]],
                local: list[tuple[str, float]],
                keep: int = MAX_TERMS) -> list[str]:
    """One shortlist from both readings, corpus structure ranked first.

    A word the graph found is one that distinguishes these claims *and* is rare
    across the corpus. A word only the local windows found distinguishes them
    here and may be common elsewhere. Both are worth showing the investigator;
    the first is worth showing first.
    """
    order: list[str] = []
    for word, _ in structural:
        if word not in order:
            order.append(word)
    for word, _ in local:
        if word not in order:
            order.append(word)
    return order[:keep]


def _why(axis: str, bindings: list[tuple[str, float, float]],
         connection: float, spread: float) -> str:
    """The sentence that goes in the certificate. Templated, never generated."""
    values = [v for v, _, _ in bindings[:3]]
    if len(values) >= 2 and spread > 0.2:
        return (f"{axis} is live around both claims and its values divide them "
                f"({' / '.join(values)})")
    if values:
        return f"{axis} is live around both claims; recorded values {', '.join(values)}"
    return (f"{axis} is undetermined on one or both claims and connects them "
            f"structurally")


def rank_claims(graph: Graph, a: "Claim | int", b: "Claim | int") -> Ranking:
    """Convenience for callers holding claims rather than ids."""
    return rank_pair(graph,
                     a if isinstance(a, int) else a.id,
                     b if isinstance(b, int) else b.id)
