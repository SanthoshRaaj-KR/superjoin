# Fact Knowledge Layer

**A system that reads PDFs, pulls out the facts, and works out whether two facts
actually disagree — or whether they were only ever answering different
questions.**

No chunking. No embedding-similarity contradiction detection. No graph
database. And a use of **PageRank nobody else is making**: not to rank which
source to trust, but to rank *where a language model should look* before a
contradiction is reported. It's stage ⑥ below, and it's the one part of this
system without an obvious precedent.

<sub>Python 3.13 · FastAPI · SQLite · 272 tests · 16/16 on the hand-labelled gold set</sub>

---

## The problem

When two documents report different numbers for what looks like the same thing,
the obvious conclusion is that one of them is wrong.

Usually neither is.

Two figures can differ because they cover different **reporting scopes**,
different **time periods**, different **definitions**, different **estimate
vintages**, or because one is a forecast and the other is an actual. The numbers
disagree; the facts do not.

Most systems miss this for a structural reason. They chop the document into
chunks, embed them, retrieve the ones that look similar, and ask a model *"do
these conflict?"* — but the information needed to tell the two figures apart is
almost never in the same chunk as the number. It's in a section heading, a
column header, a footnote, a parenthetical. Chunking throws it away, and after
that no amount of model quality can recover it.

## The idea

> ### Don't ask whether two facts contradict.
> ### Ask whether they are comparable at all.

That single change reorders the whole system.

The language model does the part it is genuinely good at: reading messy prose
and tables and turning them into typed facts **with their context attached**.

It never issues a verdict. The verdict comes from a deterministic comparison
step that checks the context first, and **names the axis** responsible for any
difference it finds.

This matters beyond correctness. A model that says *"these contradict"* gives you
an opinion you have to trust. This system says *"these differ on reporting
scope"* — a statement you can check against the page yourself.

## What makes this different

Five things here don't have an obvious off-the-shelf equivalent. Each gets its
own explanation, because each is load-bearing — remove any one and a real case
in the corpus breaks.

### 1 · The model is structurally never allowed to issue a verdict

Almost every "AI fact-checking" system asks a language model *"do these two
things conflict?"* and treats the answer as the answer. That question is a
category error: a model asked it will pattern-match toward "yes" or "no" even
when the honest response is *"these aren't answering the same question."*
There is no token for that in a yes/no prompt.

So the model here is **never asked that question, ever, at any stage.** Its
only jobs are (a) turn a page into typed claims with their context attached,
and (b) when a contradiction is being double-checked, propose *a fact about
what the document says* — never a verdict about the two claims. The actual
decision — `CORROBORATES` / `CONTRADICTS` / `CONTEXTUAL` / `INSUFFICIENT_EVIDENCE`
— is computed by a plain Python function over typed fields, the same way every
time, with no sampling temperature and no prompt to word carefully. You could
delete every model call from this codebase, hand-type twenty claims into the
database, and the verdicts would come out identically.

### 2 · No chunks, no embeddings deciding what "belongs together"

There's no text-splitter anywhere in this codebase, and no vector similarity
search deciding which two claims are worth comparing. That's a deliberate
absence, not a missing feature. Embedding similarity answers *"do these two
passages sound alike"* — and `₹74,540.82 million` sounds exactly as similar to
`₹81,415.38 million` whether they're the same fact restated or two different
reporting scopes for the same year. The similarity score cannot see the
difference, because the difference isn't in how the numbers sound — it's in a
heading three lines above one of them.

Instead, every fact is placed *inside a tree it was extracted from* — document
→ section → subsection → row — and the comparison layer blocks candidate pairs
by **canonical entity + canonical metric**, computed after the tree has already
told the extractor what a number means. See the [document hierarchy](#the-document-hierarchy-a-pdf-becomes-a-tree-that-remembers)
below for exactly how that tree gets built.

### 3 · PageRank, aimed at a target PageRank was never built for

This is the one with no template to copy from. **Personalized PageRank** — the
algorithm behind ranking which web page is authoritative — runs here not to
decide which *source* is more trustworthy, but to decide which *sentence a
language model should be pointed at* before a contradiction is written down as
real. Ranking sources by centrality would be exactly the bug this design is
built to avoid: a figure repeated across ten documents is not thereby correct,
and nothing in this codebase is allowed to treat repetition as truth. There's a
test that asserts, structurally, that the object this stage returns has no
field that could be mistaken for a verdict.

Measured against a genuine before/after, with the same corpus and the same
number of model calls either way: **5 contradictions withdrawn without it, 8
with it.** Full mechanics — the graph, the two-vector trick, the two things
that broke on the first attempt — in [its own deep-dive section](#the-claim-context-graph-and-the-personalized-pagerank-that-walks-it)
below.

### 4 · The axis vocabulary teaches itself — and can un-teach itself

The system doesn't ship knowing that `consolidation` or `estimate_vintage` are
things two documents might disagree about. It starts with a small seed and
**discovers the rest from the corpus**: when a proposed distinction — some
phrase a model pointed at while explaining away a contradiction — recurs across
enough *independent* pairs, it's promoted from a one-off guess into a real,
reusable axis that the comparability gate can name from then on. An axis that
stops explaining anything **lapses back out**, rather than sitting in the
registry forever as dead weight. On the shipped corpus, 11 axes were discovered
this way and 7 were promoted to real status — none of them typed in by hand.

### 5 · A correction has to survive being re-examined, not just be believed once

When the second look proposes a reason two claims aren't really contradicting,
that reason doesn't get accepted just because a model said it convincingly.
It's checked for **circularity** — did the proposed distinction just quote the
very figure it's supposed to be explaining? — and it's checked against a
confidence floor, every single time it's used, including on a later run that
remembers it. This mattered in practice: an early version of this system
resurrected a stale, pre-fix recovery and used it to silently dissolve the
corpus's one genuine cross-institution contradiction. The fix — *"remember what
was verified against the document; re-run what was a judgement"* — is now
enforced code, not a lesson kept only in commit history.

---

## How it works

```
   PDF
    │
    ▼
 ① EXTRACT ──── page by page, with document structure and inherited context
    │
    ▼
 ② GROUND ───── every fact traced back to the exact text that supports it
    │
    ▼
 ③ CANONICALIZE  entities · units · periods · metrics → one internal form
    │
    ▼
 ④ BLOCK ────── only compare facts that could possibly be about the same thing
    │
    ▼
 ⑤ THE GATE ─── are these two comparable?  ──── no, an axis differs → CONTEXTUAL
    │                                      └─── no, context missing → INSUFFICIENT
    │ yes
    ▼
   compare values  ──── agree → CORROBORATES
                   └─── differ → ⑥ ContextRank ── PageRank picks WHERE to look
                                       │
                                  model looks there ── found it → CONTEXTUAL
                                                    └─ nothing → CONTRADICTS
```

### ① Extraction — page by page, not one big prompt

The PDF is parsed **locally, one page at a time**. The whole document is never
thrown at the model in a single call.

That's deliberate. Even when a document fits inside the context window, very long
contexts suffer from attention dilution — the well-known *lost-in-the-middle*
problem, where information far from the edges of the prompt gets used less
reliably. Sending 500 pages at once is a good way to get a confident, average,
slightly wrong answer.

But there's a catch: **a fact on one page often depends on information from
another page.** A number in a financial table means nothing without the
`(All amounts in millions)` note at the top of the section and the
`Consolidated · year ended March 31` in the heading above it.

So instead of chunking, the system builds a **document tree** —
document → section → subsection → table → row — and each node carries a *context
frame*: the units, the reporting basis, the period, the entity in force at that
point. Context flows downward and merges. By the time the extractor reaches a
cell, it already knows the unit, the basis and the period, even though none of
those words are anywhere near the number.

The model then returns **structured claims**, not prose:

```jsonc
{
  "subject":    "…",              // who or what the fact is about
  "predicate":  "…",              // the document's own wording, not a standard name
  "value_raw":  "…",              // the number exactly as printed
  "unit_raw":   "…",              // including scale
  "period_raw": "…",
  "modality":   "actual",         // actual | estimate | projection | target | restated
  "qualifiers": { … },            // open dictionary — the context that applies
  "unknown_qualifiers": [ … ],    // context it could NOT determine
  "evidence_quote": "…"           // copied verbatim from the page
}
```

Two fields do most of the work here.

`qualifiers` is an **open dictionary**, not a fixed schema. The system doesn't
decide in advance which kinds of context exist, because the next document will
have one nobody thought of.

`unknown_qualifiers` is the extractor **declaring what it could not determine**.
This is the difference between a system that guesses and one that knows it is
guessing.

### ② Grounding — the model proposes, the document proves

Nothing the model says is taken on trust.

Every claim must survive two checks against the real page:

1. the `evidence_quote` must actually be **locatable** in the source text, and
2. the value must actually appear **inside that quote**.

If either fails, the claim is **quarantined with a reason code** — not silently
dropped, and not silently stored. It goes into a visible list you can read.

This catches the failure mode that matters most: a model that read the numbers
correctly but *invented the citation* — stitching a header row onto a data row,
say, producing a quotation that exists nowhere on the page. The numbers look
fine. The proof doesn't exist. Those claims don't get stored.

The cost is real and it's stated plainly: some genuine facts get thrown away
because layout mangled their quote. The quarantine view makes that loss visible
rather than pretending it didn't happen.

### ③ Canonicalization — so facts can actually meet

Facts extracted independently need to be brought into a common form before they
can be compared at all:

| | |
|---|---|
| **Entities** | `Acme Limited`, `Acme Ltd.`, `Acme` → one entity. Where the document prints a registration number or an official identifier, that pins the match far better than name similarity ever could. |
| **Units** | lakh / crore / million / billion → one base scale, with the currency and dimension kept separate. |
| **Periods** | `FY24`, `2023-24`, `FY2023/24`, `year ended March 31, 2024` → one interval. Different institutions write the same fiscal year in different ways. |
| **Metrics** | The document's own wording is preserved, then matched against a registry of canonical metrics that grows as new documents arrive. |

One deliberate refusal: **currencies are never converted.** A figure in USD and a
figure in INR are marked `INCOMPARABLE` rather than joined at an invented
exchange rate. Correct-by-refusal beats plausible-and-wrong.

### ④ Blocking — not every pair, only the plausible ones

Comparing every fact against every other fact is O(n²) and mostly wasted work.

Instead, claims are grouped into blocks by **canonical entity + canonical
metric**. Facts about unrelated things never reach the expensive comparison
stage at all. This is also the scaling story: the blocking key is what carries
this design from hundreds of pages to hundreds of thousands.

### ⑤ The comparability gate — the core

For each candidate pair, checks run in a fixed order:

1. **Sufficiency** — is any axis that matters for this metric *undetermined* on
   either side?
2. **Axis difference** — on which axes do the two context vectors disagree?
3. **Time** — same period, overlapping, or disjoint?
4. **Value** — after normalization, do the numbers agree within a tolerance
   derived from how precisely each was printed?

And the verdict follows from the answers:

| Situation | Verdict | What it means |
|---|---|---|
| A material axis is undetermined | `INSUFFICIENT_EVIDENCE` | We can't tell. Says which axis is missing. |
| Comparable, values agree | `CORROBORATES` | Two sources, one fact. |
| Comparable, values differ | `CONTRADICTS` | A real disagreement. |
| Values differ, **but an axis differs too** | `CONTEXTUAL` | Not a conflict — names the axis. |
| Different periods | `CONTEXTUAL_TEMPORAL` | A trend, not a conflict. |
| Different units of meaning | `INCOMPARABLE` | Different questions entirely. |

Every explanation is **templated from the difference itself**, so it is
reproducible and says exactly what was decided and why.

Two consequences worth stating, because they're easy to get wrong:

**Absent is not equal.** If one claim states its reporting scope and the other
simply doesn't know, they do **not** match on that axis. Missing context *blocks*
a comparison rather than permitting it. This is why `INSUFFICIENT_EVIDENCE` is a
first-class verdict and not an error state — sometimes the honest answer is that
the documents don't say.

**The axis vocabulary is open.** Some axes are built in. Others are **discovered**:
when the same distinguishing phrase turns up in enough independent pairs, it is
promoted into the registry as a real axis, and affected pairs are re-compared. An
axis that stops explaining anything lapses back out. The schema grows from the
documents rather than from a list someone wrote in advance.

### ⑥ ContextRank — PageRank, pointed at a problem PageRank doesn't usually solve

> **This is the part of the system with no obvious template to copy.**
> Everything above this stage has a recognisable shape — extraction, grounding,
> canonicalization, a rules engine. This stage doesn't. It takes the algorithm
> behind Google's original web ranking and asks it a question it was never
> designed for: *not* "which page is authoritative", but **"which sentence on
> this page is worth a language model's attention before we accuse two
> documents of disagreeing."**

<sub>This section is the *why* — the problem it solves and what it measurably
changed. The exact graph structure, edge weights and the PageRank math itself
are in **[Architecture, in depth](#the-claim-context-graph-and-the-personalized-pagerank-that-walks-it)**
further down, kept separate so this stays readable end to end.</sub>

#### The honest limit this stage exists to cover

Everything up through the gate can only reason about context that **reached
it.** If two claims arrive with the same entity, the same metric, the same
period, and nothing on record to tell them apart, the gate *must* call it a
contradiction — it's right, given what it was handed. The question is whether
what it was handed was complete.

Often it isn't. The distinguishing detail was printed on the page — in the
sentence around the number, in a row label, in a footnote — and simply didn't
survive extraction. Auditing the very first contradictions this corpus
produced, **every single one** turned out to be exactly this: a real
distinction, printed on the page, that the extractor walked past.

So before a contradiction is *reported*, it is sent back to its own pages for a
second look. Two things happen, in order.

**First, free deterministic scouts.** Some distinctions need no model at all.
The same magnitude printed once in parentheses and once under a `Less:` row
label is a sign convention, not a disagreement — that's decidable by a rule,
costs nothing, and runs before anything expensive does.

**Second — for what the rules can't catch — ContextRank decides where to point
the model.** This is the new part.

#### Why PageRank, of all things

A contradiction usually involves two long documents. Sending both pages to a
model and asking "what's different here?" works, but it's expensive per pair,
and on a real corpus there can be hundreds of candidate pairs. Something has to
decide, cheaply, *which* pairs are worth the call and *which sentence or
qualifier* the model should be pointed at — without that decision being allowed
to touch the verdict itself.

The pipeline had already built, as a side effect of everything above, a graph
it never intended to use for reasoning: claims are linked to the documents that
contain them, the sections they sit in, the metric and period they were
resolved to, every qualifier the extractor bound to them, every axis a past
review has recovered, and the distinctive words in their own evidence quotes.
That graph is exactly the kind of structure PageRank was built to walk — the
only twist is what gets asked of it.

**Personalized PageRank**, not the global kind: the walk restarts at the two
claims in question rather than wandering the whole graph, so what comes back is
*local* to this specific pair, not a popularity contest across the whole
corpus. Two independent walks run — one seeded on each claim — over up to 4
hops (claim → section → sibling claim → qualifier → axis), for 40 iterations
with a 0.85 damping factor, and two numbers are read off every node the walks
touch:

```
connection(n)  =  min( r_a[n], r_b[n] )        n sits near BOTH claims
divergence(n)  =  |r_a[n] − r_b[n]| / sum       n sits near ONE and not the other
```

That distinction is the whole idea, and it's easy to get backwards. A single
walk seeded on both claims together would surface what's *central to the pair*
— and what's central to a pair is usually what they have **in common**, which
by definition cannot be what separates them. Two claims about FY2026 GDP growth
are both, overwhelmingly, about FY2026 GDP growth — that's *why* they were
compared, not why they disagree. Running two walks and *subtracting* them is
what turns "what is this pair about" into "what tells these two apart."

An axis is a candidate for explaining the difference when it scores high on
**connection** (the concept is genuinely live in this neighbourhood) *and*
**divergence** (the two claims land on different sides of it). An axis both
claims already agree on scores high on the first and near-zero on the second —
correctly, because it explains nothing.

#### Two things that had to be measured before they could be trusted

Both of these were wrong on the first attempt, and both failures were only
visible by testing against a held-out answer, not by reading the code:

**Inverse frequency isn't a tuning knob — it's the difference between working
and useless.** The first version weighted every edge in the graph equally, and
it was useless: on this corpus, `consolidation` is either recorded or declared
undetermined on 366 of 666 claims, so on raw structure it's adjacent to nearly
everything and it won *every* ranking — including for a pair the two pages
actually distinguish by sign convention, nothing to do with consolidation at
all. That's the exact popularity signal this whole approach exists to avoid,
walking back in through the side door. Weighting each edge by `log(N / n)`
fixes it: a node connected to most of the corpus is, by construction, not
*about* any one pair.

**The axis ranker looked perfect and was cheating.** Scored against the six
context recoveries this corpus already had on file, ranking by *known axis
name* got 6 out of 6 — and then, re-tested with each pair's own recovery
removed from the graph before ranking, it got **0 out of 6.** It wasn't
finding the answer; it was reading its own answer key, because an axis only
becomes a node in the graph *after* something has already recorded it, so
ranking known axes can surface a past recurrence but never a genuine
discovery. What survived the held-out test was ranking **distinctive words**
in the evidence instead: `less` came first for the pair the pages separate by
sign convention, `adjusted` first for the EBITDA pair, `generated` and
`operations` first and second for two cash-flow claims — five correct out of
six, with zero knowledge of the right answer baked in. So the shortlist handed
to the model is words first, known axes second, and the two are never
presented as if they carry equal weight.

#### What it measurably changes

The fair test is a **cold run** — no remembered recoveries from any earlier
pass, so every contradiction is genuinely investigated from scratch — with the
ranked shortlist in the prompt, and without it. Same corpus, same candidate
pairs, same number of model calls either way:

| | contradictions withdrawn | left unresolved | reduction |
|---|---|---|---|
| without ContextRank | 5 | 5 | 78.7% |
| **with ContextRank** | **8** | **2** | **79.0%** |

Same twelve investigations, same cost, three more pairs correctly explained
instead of left standing as unresolved contradictions. One corpus and twelve
pairs is a real, reproducible result and not a large one — it's reported here
as exactly that, not oversold as proof of anything at scale.

#### The boundary, enforced in the code, not just in prose

**ContextRank ranks contextual relevance. It is never asked, and structurally
cannot be asked, what is true.** A figure repeated across ten documents is not
thereby correct, and a graph that scored sources by centrality would say it
was — that's the failure mode this whole design refuses. So:

- nothing it returns is a verdict, a value, or a confidence in either claim
- masking or reading the ranking cannot change what `compare()` decides
- the model still has to find the value on the page, grounding still has to
  verify it, and the same deterministic gate re-runs on whatever comes back
- a wrong ranking costs exactly one wasted model call — never a wrong verdict

There's a test asserting, structurally, that the object this stage returns
carries no field that could be mistaken for an answer — not a convention,
enforced.

#### Where you can see it disagree with itself, in public

On this corpus's one surviving cross-institution contradiction, ContextRank
ranks `scenario` first — which is precisely the axis a model investigator once
proposed to explain the disagreement away, and which a separate circularity
check rejected, because the proposed distinction quoted the very figure it was
trying to explain. The ranking layer suggested a way out. The deterministic
layer checked it and refused it. **That disagreement, left visible rather than
hidden, is the architecture doing its job** — a panel that only ever agreed
with the verdict would be far less informative than one that shows its
suggestion being overruled on the record.

Every relation stores this exchange — its certificate — whether or not
anything came of it. A suggestion that led nowhere is still part of the
reasoning trail, not thrown away because it didn't pan out.

A recovery is also treated as knowledge about a **claim**, not a story about
one pair: it propagates to every other comparison either claim takes part in,
and it's remembered across future runs — re-judged for circularity each time,
never simply replayed.

### ⑦ Time — states are intervals, not values

Facts like *"who holds this role"* or *"what is the registered address"* aren't
numbers. They're **states that hold over an interval**, and treating a change
over time as a disagreement is a classic false positive.

So the system tracks two independent clocks:

- **valid time** — when the fact was true in the world
- **assertion time** — when a document claimed it

A later document that closes an earlier open-ended interval is a **refinement**,
not a contradiction. Two documents giving *different end dates* for the same
interval **is** a contradiction.

It also tracks how many holders a role can have at once. That turns "is this a
conflict?" into a constraint check:

| Holders allowed | Intervals | Verdict |
|---|---|---|
| one | back-to-back | `SUCCESSION` — a clean handover |
| one | with a gap | `SUCCESSION_WITH_VACANCY` — and the gap is reported |
| one | overlapping, different holders | `CONTRADICTS` |
| many | overlapping | no conflict — both simply hold |

That count is **inferred from grammar and corrected by observation**: if the same
document shows two people genuinely holding a role at once, the system learns
the role admits many, rather than manufacturing a contradiction from its own
assumption. It's a learned property, not a hard-coded list.

---

## Architecture, in depth

The two structures everything above depends on, mechanically: the tree a PDF
gets turned into on the way in, and the graph + algorithm that decide what a
model should look at before a contradiction is reported.

### The document hierarchy: a PDF becomes a tree that remembers

A PDF has no headings, no notes, no tables as far as the file format is
concerned — it has positioned glyphs. Turning that into a structure worth
reasoning over happens entirely **locally**, before any model ever sees the
page, in four passes:

**1. Lines become rows, rows become regions.** PyMuPDF gives back individual
text runs with a font size, a bounding box and a bold flag. Runs on the same
baseline are merged into a `Row`; rows are grouped by vertical proximity and
column alignment into a `Region` — this is what turns three separately-drawn
columns of numbers back into one logical table row, in reading order, before
anything downstream ever sees them as text.

**2. Font sizes become a heading hierarchy — relative to the document, not to
a fixed number.** Every size that appears meaningfully larger than the page's
own body-text size gets ranked, largest first:

```python
def heading_levels(body_size, sizes):
    distinct = sorted({s for s in sizes if s > body_size + 0.4}, reverse=True)
    return {size: i + 1 for i, size in enumerate(distinct)}
```

That word *"relative"* is the entire point: a corporate annual report and an
IMF policy report have completely different body sizes, and a hard-coded
threshold like "16pt is a heading" would misread one of the two documents
guaranteed. There's also a guard against the opposite failure — a bare number
is never treated as a heading no matter what size it's set in, because the Q4
earnings deck sets chart labels in 8pt against 6pt body text, and a purely
size-based rule would promote every number on a chart page into a section
heading and then inherit context *from* it.

**3. A stack walks the page in reading order, and headings push and pop it like
scopes in a program.** A heading of level *L* stays open until the next heading
of level *L or shallower* — exactly the ordinary rule for a document outline.
Anything that isn't a heading is either page furniture (detected by
**position**, not by matching known header text — a one- or two-row block
pinned to the top or bottom 8% of the page) or a **note**, which attaches to
whichever heading is currently on top of the stack. This is why a currency
declaration printed once, directly under a page title, still governs a number
four subheadings later: the frame in force at any row is every declaration
still open on the stack, nearest scope wins on conflict, and popping a heading
only discards what that heading itself introduced.

**4. Every line is scanned for a context declaration, by shape — never by
matching a specific document's wording.** A parenthetical containing a
currency symbol or a scale word (`crore`, `million`, `lakh`, `Rs.`, `₹`) is a
unit declaration wherever it appears, in any document; a heading naming
`consolidated` or `standalone` narrows reporting scope from that point down.
The one deliberately tricky guard: a sentence mentioning **both** words is
*discussing* the distinction, not declaring one — the annual report opens a
paragraph with exactly that sentence, immediately above two other paragraphs
that each pick a different basis, and without the guard the whole page would
be stamped with whichever basis the discussion happened to mention first. A
declaration is also capped at 140 characters: past that length, a line is
prose that merely *contains* the word, not a declaration of it — the
difference between `(Consolidated, ₹ in million)` and three sentences that
happen to use the word "consolidated" in a footnote about accounting policy.

**A worked example.** A page under the heading `Consolidated Balance Sheet as
at March 31, 2024`, with a note directly beneath it reading `(All amounts in
Indian Rupees in million)`, reaches a bare table cell — `81,415.38` — three
subheadings deeper with a context frame already carrying:

```
consolidation = consolidated   (from the heading)
currency      = INR            (from the note)
scale         = million        (from the note)
period        = FY2024         (from the document profile, if nothing closer overrides it)
```

None of those four words are anywhere near the number `81,415.38` on the
page. **That inherited frame is what the extractor sees**, printed inline
above the raw text as `[CONTEXT IN FORCE]` lines — presented as *evidence it
can override*, never as ground truth stamped onto the claim, because the
ordinary outline rule sometimes reaches further than a human reader would
extend a heading's scope by eye. And when an axis has no declaration in force
anywhere on the stack, that absence is recorded and handed to the extractor by
name — the mechanical origin of `unknown_qualifiers`, and the reason
"nobody said" and "it's standalone" are never allowed to collapse into the
same thing.

### The claim-context graph and the Personalized PageRank that walks it

This is the mechanism behind stage ⑥. Two things need explaining: what the
graph is actually built from, and exactly how the algorithm decides what to
rank.

#### What's a node, and what's an edge

Nothing here is computed specially for this purpose — every edge is a
relationship the pipeline already recorded on its way past. For every claim, a
node `claim:<id>` is linked to:

| Target | Base weight | What it represents |
|---|---|---|
| `doc:<id>` | 0.3 | the document it came from |
| `metric:<name>` | 1.0 | the canonical metric it resolved to |
| `period:<label>` | 0.8 | the canonical period it resolved to |
| `sect:<doc>:<heading path>` | 0.9 | the section frame governing its page |
| `qual:<axis>=<value>` | 1.0 | one specific qualifier binding it carries |
| `axis:<name>` (via unknown) | 0.4 | an axis the extractor explicitly could **not** determine |
| `term:<word>` | 0.5 | a distinctive word from its own evidence quote |

A qualifier binding is itself linked onward to its axis node
(`qual:consolidation=standalone` → `axis:consolidation`), which is what
creates the multi-hop path that makes this worth building at all:
`claim → section → sibling claim → qualifier → axis` reaches an axis that
**neither claim in the pair ever mentions**, because a *different* claim in the
same section carried it. Reading two isolated pages can never find that path;
walking the graph does.

Every one of those base weights above is then **multiplied by the inverse
frequency of what it connects to** — and this multiplication is not an
optimisation, it's the difference between the algorithm working and being
actively wrong. The very first version weighted every edge equally, and it was
useless: `consolidation` is recorded, or explicitly flagged as undetermined, on
**366 of this corpus's 666 claims**, so at equal weight it's structurally
adjacent to more than half the graph and it won *every* ranking it competed
in — including for a pair the two source pages actually distinguish by sign
convention, nothing to do with consolidation at all. Weighting by
`log(N / n)` — the number of claims in the corpus over the number that touch
this particular node — turns that around: a node touching 366 of 666 claims
scores a weight of about **0.60**; a node touching only 2 claims scores about
**5.81**, nearly **ten times** as much say in the outcome. That ratio is the
whole mechanism, stated as one number: *a node connected to most of the corpus
is not meaningfully **about** any one pair of claims it happens to touch.*

#### The walk itself: Personalized PageRank, restart-based

Plain PageRank answers *"what is generally important in this graph"* — which
is precisely the popularity signal that must never decide anything here.
**Personalized** PageRank answers a different question: *"what is important
specifically near this one claim"* — by injecting the restart probability back
at a single seed node instead of spreading it uniformly across the whole
graph. This is standard power iteration, restarting at the seed with
probability `1 − d`:

```python
rank = {seed: 1.0}                         # all mass starts at the seed
for _ in range(40):                        # ITERATIONS
    nxt = {seed: 1 - DAMPING}              # restart mass, injected every step
    for node, mass in rank.items():
        edges = {n: w for n, w in graph[node].items() if n in neighbourhood}
        if not edges:
            nxt[seed] += DAMPING * mass    # a dead end returns fully to the seed
            continue
        share = DAMPING * mass / sum(edges.values())
        for neighbour, weight in edges.items():
            nxt[neighbour] += share * weight
    rank = nxt
    if total_change < 1e-7: break          # TOLERANCE — usually converges early
```

`DAMPING = 0.85`, so a unit of mass restarts back at the seed roughly every
seven steps on average — which over a graph this shallow means the fourth hop
out still carries a usable amount of signal and the tenth essentially doesn't.
The walk is restricted first to a **4-hop neighbourhood** around the two
claims being compared (a plain breadth-first search, `RADIUS = 4`), because
that's exactly the path length of `claim → section → sibling → qualifier →
axis`. Measured, and worth stating plainly because the tidy version of this
claim would be false: **the radius turns out not to be what keeps this local
on this corpus.** Three hops already reach 933 of the graph's 1,611 nodes, and
four, five and six hops reach exactly the same set — the graph is small-world
enough that the radius stops mattering almost immediately. What actually keeps
each walk personal to its seed is the **restart probability**: mass that
wanders off always finds its way back to the claim that seeded it, never to
the graph's overall centre of gravity, regardless of how far the neighbourhood
technically extends.

#### Two walks, not one — and reading two answers instead of one

A single walk seeded on **both** claims at once would rank whatever is most
central to the pair — and what's central to a pair of claims being compared is
almost always what they **already have in common**, which by definition cannot
be what separates them. Two claims are both about FY2026 GDP growth precisely
*because* that's why they were placed in the same comparison; that fact
explains nothing about why their values differ. So two independent walks run,
one seeded on each claim (`r_a`, `r_b`), and two different quantities get read
off every node either walk touches:

```
connection(n)  =  min( r_a[n], r_b[n] )          n sits near BOTH claims
divergence(n)  =  |r_a[n] − r_b[n]| / (r_a[n] + r_b[n])    n sits near ONE, not the other
```

An axis is only a real candidate when it scores on **both** properties at
once: connected (the concept is genuinely alive in this neighbourhood — other
claims nearby carry it) *and* divergent (its actual recorded values split
across the two sides of the pair, rather than both claims agreeing on it).
Concretely, each candidate axis is scored as:

```
score(axis) = connection(axis) × (0.25 + 0.75 × spread)
```

where `spread` is the sharpest divergence found among *that axis's own
qualifier bindings* — `consolidation=standalone` versus `consolidation=consolidated`
scores very differently from an axis whose one recorded value both claims
happen to share. An axis both sides already agree on is fully connected and
has zero spread, so it scores near the floor of that range **however central
it looks to the pair** — which is precisely the guard against the walk
confidently ranking `period` first on every single pair in the corpus, since
period is connected to almost everything.

#### The vocabulary sidecar, and why "attention" deliberately isn't the axis score

Words from each claim's own evidence quote are indexed into the graph as
`term:` nodes the same way axes are, and ranked by the same divergence
formula — this is what finds a **new** distinction the corpus has never named
as an axis before, since a word can appear in the evidence long before anyone
has promoted a concept into the registry. There's also a second, independent
pass — `local_terms` — run directly over the two claims' raw page-text windows
at the moment of ranking, for the case where the distinguishing phrase was
never captured inside the extracted evidence quote at all (the IMF separates
two deficit figures with *"per the authorities' definition"*, sitting in the
sentence around the number rather than inside either quote). The two
vocabularies are merged with corpus structure ranked ahead of the local
reading, since a word the graph independently confirms as rare *and*
divergent is a stronger signal than one only found in this one instance.

The **attention score** — the number that decides whether a pair is even worth
a model call under `relate --budget` — is deliberately computed from this
**vocabulary mass**, not from the axis score, and getting this backwards would
have been the single worst bug this module could contain: an axis the corpus
has genuinely never seen before scores **zero**, by construction, simply
because no node exists for a concept nobody has recorded yet. Ranking
attention by known-axis score and skipping whatever scores low would silently
skip *every future genuine discovery* while confidently re-investigating
whatever the system had already solved. Attention low enough to fall below
`ATTENTION_FLOOR = 0.004` is recorded as *not investigated* rather than *no
context found* — a deliberately different and more honest claim.

#### What this cost, and what it measurably bought

The whole walk — both directions, over a graph with roughly 1,600 nodes — runs
in about **120 milliseconds, with no model call and no API key**, and it runs
*before* the investigator is asked anything, which is what makes it a
mechanism that shapes the question rather than a statistic computed about an
answer already given. Scored the only honest way — a cold run with no
remembered recoveries, so every contradiction is investigated from a blank
slate, same corpus, same twelve candidate pairs, same number of model calls
either way:

| | contradictions withdrawn | left unresolved | reduction |
|---|---|---|---|
| without this stage | 5 | 5 | 78.7% |
| **with this stage** | **8** | **2** | **79.0%** |

Three more genuine disagreements correctly explained, for the identical cost.
And on the one contradiction that still survives review in this corpus, the
ranking surfaces `scenario` as its top suggestion — which is precisely the
axis an earlier model investigation once proposed to explain the disagreement
away, and which a separate circularity check rejected because that proposed
distinction quoted the very figure it was supposed to be explaining. **The
ranking layer suggests. The deterministic layer still has the only vote that
counts**, and the two are left visibly disagreeing in the stored record rather
than quietly reconciled.

---

## What comes out

Run over a six-document corpus of 511 pages, spanning corporate filings and
macroeconomic reports:

| | |
|---|---|
| Facts extracted and grounded | **666** of 797 proposed (**83.6%**) |
| Claims quarantined, with reasons | 131 |
| Comparable blocks | 129 |
| Pairs compared by the gate | 1,505 (297 cross-document) |
| **Raw disagreements** (what a context-blind system would flag) | **1,373** |
| → explained by a named context axis | **1,084** |
| → blocked: a material axis was undetermined | 287 |
| → **genuinely unresolved** | **2** |
| **Reduction** | **79%** of apparent disagreements dissolved by context |
| Contradictions raised and then withdrawn on review | 8 |
| Context axes the system **learned** (not built in) | 11 discovered, 7 promoted |
| Hand-labelled gold set | **16 / 16** |

Every one of those numbers is one command away, against the database that ships
with this repository and with no API key:

```bash
cd backend
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli relate
```

<sub>Both serving and re-relating write to the file — a new generation of
verdicts, a startup housekeeping row. The store is append-only, so nothing is
overwritten and every earlier run is still there, but `git status` will show
the file as modified. `git checkout data/snapshot.sqlite` puts it back.</sub>

<sub>**Why the Overview screen shows 1,516 and not 1,505.** The interval engine
runs beside the gate and writes 11 more relations — the successions, the
vacancy, the interval closures. They are relations between *states*, not
between values, so they belong in the store and in the corpus totals, but not
in a reduction measured over numbers that disagree. The table above is the
gate's own slice; the screen shows everything stored.</sub>

The denominator is chosen deliberately: it's what a system with no notion of
context would have flagged — same entity, same metric, values differ. That's the
baseline this design is arguing against.

**The measure of success here is how many apparent disagreements the system
dissolves, not how many it flags.**

---

## Getting started

### 1. Requirements

Python **3.11+** (developed on 3.13). That's the whole list.

No database server, no vector store, no message queue, no Node, no build step
for the interface. The store is a single SQLite file and the interface is plain
HTML and JavaScript served by the same process as the API.

### 2. Install

```bash
git clone <this-repo>
cd SuperJoin

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r backend/requirements.txt
```

### 3. Start the service

The API and the interface are **one process on one port**. There is nothing to
build, nothing to serve separately, and no CORS to configure:

```bash
cd backend
python -m fkl.cli serve
```

```
  UI   http://127.0.0.1:8000/
  API  http://127.0.0.1:8000/api/v1/corpus
  docs http://127.0.0.1:8000/docs
```

Open **http://127.0.0.1:8000/** and the interface is there.

| Flag | Default | |
|---|---|---|
| `--host` | `127.0.0.1` | bind address — use `0.0.0.0` to reach it from another machine |
| `--port` | `8000` | |
| `--reload` | off | restart on code changes, for development |

<sub>**How the interface gets served.** `fkl serve` mounts the folder
`PDF Fact Reconciliation System/` at `/` and serves `Reconcile.dc.html` as the
index. It's a single-page app with two plain script files beside it — no npm
install, no bundler, no dist folder. The only requirement is that the folder
stays next to `backend/` where it is in the repository; if it's missing, the
server logs a warning and serves the API alone.</sub>

### 4. See it working — with no API key at all

A pre-computed database ships with the repository, so you can explore the whole
system — every screen, every verdict, every piece of evidence — before setting
up any credentials and without spending anything:

```bash
cd backend
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli serve
```

Open **http://127.0.0.1:8000/** and every screen is populated.

This is a *live* database, not a set of screenshots. The page text is kept, so
the comparability gate, the interval engine and the deterministic scouts all
re-run against it and produce the same numbers with no key:

```bash
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli relate
```

**Start here.** It's the fastest way to understand what the system does, and it
costs nothing.

### 5. Add your API key

Only extraction needs a model. Everything else — the gate, the interval engine,
canonicalization, the deterministic scouts — runs without one.

```bash
cp .env.example .env
```

Then open `.env` and fill in:

```ini
OPENAI_API_KEY=sk-...

FKL_MODEL_EXTRACT=gpt-4.1-mini              # one call per page: favour cost
FKL_MODEL_REASON=gpt-4.1                    # rare calls: favour capability
FKL_MODEL_EMBED=text-embedding-3-small      # alias and metric matching
FKL_DB_URL=sqlite:///data/fkl.sqlite        # where the store lives
```

`.env` is gitignored and must never be committed. Check the key works:

```bash
cd backend
python -m fkl.cli models --prefix gpt
```

### 6. Run it on your own PDFs

Restart the service so it picks up the key, then use the interface:

```bash
cd backend
python -m fkl.cli serve
```

Go to **Add documents** → drop in a folder of PDFs (or pick files) → set how
many pages per document to read → **Ingest**.

Ingest, extraction and a corpus-wide comparison then run behind a job whose
progress bar, live page counter and log you can watch as it happens. When it
finishes, every other screen refreshes with the new corpus.

> **A note on cost and time.** Extraction is the only part of this system that
> spends money: one structured model call per page, six pages in flight at a
> time. **Start with a small page cap** — 10 or 20 — to see the whole pipeline
> run in a couple of minutes. The page cap exists so an unattended upload of a
> 400-page filing can't spend on its own.

The comparison at the end runs over the **whole corpus**, not just what you
uploaded — because the value of a new document is what it agrees and disagrees
with.

Re-uploading a PDF already in the store is recognised by content hash and
reused rather than re-ingested. A scanned PDF with no text layer is named and
skipped rather than failing the batch.

### Or use the command line

```bash
cd backend

# 1. Ingest. --no-llm does layout analysis only: no key, no spend.
python -m fkl.cli ingest ../path/to/*.pdf --no-llm

# 2. See a page exactly as the extractor will see it, and as the PDF stores it.
python -m fkl.cli page 1 12
python -m fkl.cli page 1 12 --raw

# 3. Extract facts (needs a key). Page ranges keep iteration cheap.
python -m fkl.cli ingest ../path/to/*.pdf
python -m fkl.cli extract 1 --pages 0-19

# 4. Compare everything, with the second look enabled.
python -m fkl.cli relate --review

# 5. Look at the results.
python -m fkl.cli report
python -m fkl.cli docs
python -m fkl.cli export 1 -o ../out/doc1.json
```

Extraction is **idempotent by page** — re-running skips pages that already have
claims, so a repeat costs nothing and can't create duplicates. `--force` redoes
them properly, replacing rather than appending.

### Command reference

| Command | What it does |
|---|---|
| `ingest` | Parse, profile and store PDFs. `--no-llm` for layout only |
| `page` | Print one page as the extractor sees it (`--raw` for the source text) |
| `extract` | Extract, ground and store claims. `--pages`, `--force` |
| `relate` | Run the comparability gate over the corpus. `--review` for the second look |
| `as-of` | Time-travel: what was true on a given date |
| `gold` | The hand-labelled evaluation set. `--verify`, `--score` |
| `report` | Extraction quality across the corpus |
| `export` | Dump a document and its claims as JSON |
| `snapshot` | Rebuild the shippable database |
| `serve` | Run the UI and the API together |
| `models` | List the model ids your key can actually see |

### The API

The UI and the API are one process on one port, so there's no CORS setup and
nothing to configure. **http://127.0.0.1:8000/docs** gives the full OpenAPI
surface if you'd rather read the data than the screens.

```bash
# Upload a folder and watch the job
curl -F files=@a.pdf -F files=@b.pdf \
     "http://127.0.0.1:8000/api/v1/documents?maxPages=20"
curl http://127.0.0.1:8000/api/v1/jobs/1

# Ask a question in English
curl "http://127.0.0.1:8000/api/v1/ask?q=..."

# Re-run one comparison with an axis held out of the reasoning
curl -X POST http://127.0.0.1:8000/api/v1/compare \
     -d '{"a": "f-1", "b": "f-2", "mask_axes": ["consolidation"]}'
```

---

## The interface

Eight screens. Three carry most of the idea:

**Compare** — takes any pair apart: both facts side by side, every context axis
with a ✓ or a difference, the actual source page image from each document with
the evidence span highlighted, and the verdict with its named axis. Below it,
**mask** buttons hold an axis out of the reasoning and the verdict recomputes
live. Watching `CONTEXTUAL` flip to `CONTRADICTS` when you hide the axis — and
flip back when you restore it — is the clearest proof that the reasoning is
*computed*, not stored.

**Ask** — a question in English, answered **without averaging**. If a question
has two correct answers in two different contexts, you get both, each with its
own evidence and the axis that separates them. The model parses the question and
then stops; retrieval is an exact query over typed claims.

**Timeline** — role and state histories rendered as intervals, with successions
and vacancies derived rather than extracted.

The rest: **Overview** (the reduction number across the corpus), **Documents**,
**Add documents** (the folder upload), **Facts** (every grounded claim with its
full context), and **Corpus quality** — the quarantine, with a reason for every
claim that didn't make it in, alongside the vocabulary the corpus taught itself.

---

## Project layout

```
backend/
  fkl/
    ingest.py      pdf/       PDF parsing, layout analysis
    context.py     render.py  the document tree and context inheritance
    llm/           schemas.py structured extraction
    grounding.py              quote and value verification
    canonical.py   units.py   canonicalization
    periods.py     entities.py  metrics.py
    compare.py                THE COMPARABILITY GATE
    relate.py                 corpus-wide comparison
    reconcile.py              the second look
    contextrank.py            ranking where to look
    temporal.py               intervals, succession, vacancy
    registry.py               learned axes and cardinality
    ask.py         api.py     question answering, HTTP
  tests/                      272 tests
PDF Fact Reconciliation System/
  Reconcile.dc.html           the interface — this is what `fkl serve` mounts
  api.js  support.js          its two script files; no build step
ui/                           an earlier React build of the same design,
                              standalone on mock data — see ui/README.md
gold/                         the hand-labelled evaluation set
starter-datasets/             the PDFs the shipped corpus was built from
data/snapshot.sqlite          a pre-computed corpus, so this runs with no key
docs/ENGINEERING.md           the long version: every decision and what it cost
```

The served interface is `PDF Fact Reconciliation System/`. `ui/` is a separate
React implementation of the same screens that runs against an in-memory mock;
it needs `npm install` and is not part of the running service.

## Testing

```bash
cd backend
python -m pytest -q          # 272 tests, no API key needed
```

The whole comparison layer — the gate, the interval engine, canonicalization,
the deterministic scouts — is testable with no credentials, because none of it
calls a model. That's a property of the design, not an accident of the tests.

```bash
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli gold --score
```

runs the gate against every hand-labelled relation and scores it.

---

## What this deliberately does not build

Restraint is a design decision, so it's stated:

- **No agent swarm.** Five agents passing messages is a meeting, not an
  architecture.
- **No graph database.** The system builds a graph of claims and context, but
  the interesting part is the reasoning over it, not a visualization of it.
- **No RAG over raw chunks.** Chunking is precisely what discards the
  section-scope context that makes any of this decidable. Facts are extracted
  *with* context attached, then queried exactly.
- **No currency conversion.** Cross-currency pairs are `INCOMPARABLE`, not
  joined at an invented rate.
- **No verdict from the model.** It proposes; structure decides.

## Known limitations

Stated up front rather than discovered:

- **Scanned PDFs are not supported.** There's no OCR. A scan is named and
  skipped rather than failing the batch.
- **Chart-heavy pages are the weak point.** When a chart's text layer has no
  reading order, values and their labels can't be reliably bound. An optional
  image-based pass exists; grounding still refuses anything it can't verify, so
  the failure shows up as quarantine rather than as bad data.
- **Inferred role cardinality is a heuristic** and can over-correct. The inferred
  value is shown in the interface so it can be audited.
- **Half-year periods widen to a full year** in the current period parser.
- **A small number of duplicate claims survive** — the same fact read twice on
  one page with different period readings.
- **Identifier-based entity merging is currently tuned to one jurisdiction's
  identifier formats.**
- **SQLite with brute-force similarity** is right for this scale. The blocking
  key is what carries the design further; the store is what would need to change.

The full reasoning behind each of these, plus the experiments that produced the
numbers above, is in **[docs/ENGINEERING.md](docs/ENGINEERING.md)**.

---

<sub>Built for the Superjoin engineering assignment. Credentials are never
committed; `.env` is gitignored and `.env.example` holds placeholders only.</sub>
