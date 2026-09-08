# Fact Knowledge Layer

**A system that reads PDFs, pulls out the facts, and works out whether two facts
actually disagree — or whether they were only ever answering different
questions.**

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
                   └─── differ → ⑥ SECOND LOOK ──── context found → CONTEXTUAL
                                                └── nothing found → CONTRADICTS
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

### ⑥ The second look — recovering context that extraction missed

Here's the honest limit of everything above: **the gate can only reason about
context that reached it.**

If two claims arrive with the same entity, the same metric, the same period and
nothing to tell them apart, the gate *must* call it a contradiction. It's right,
given what it was handed. The question is whether what it was handed was
complete.

Often it isn't. The distinguishing detail was printed on the page — in the
sentence around the number, in a row label, in a footnote — and simply didn't
survive extraction.

So before a contradiction is reported, it is **sent back to its pages**:

1. **Free deterministic scouts run first.** Some distinctions need no model at
   all — the same magnitude printed once in parentheses and once under a `Less:`
   label is a sign convention, not a disagreement, and that is decidable by rule.

2. **ContextRank decides where to look.** The system builds a graph of the
   claims and the things they share — sections, metrics, periods, qualifiers,
   known axes, distinctive words in the evidence — and runs a personalized
   PageRank seeded on the two claims. It reads off two signals: what sits near
   **both** claims (a live concept in this neighbourhood) and what sits near
   **one and not the other** (a candidate distinction). Edges are weighted by
   inverse frequency, so a term attached to half the corpus can't win by being
   popular.

3. **The model investigates, and proposes a fact about the document** — never a
   verdict. "This page labels one figure X and the other Y."

4. **The recovered context is grounded like any other claim**, and then the same
   deterministic gate re-decides.

The boundary is the whole point, and it's enforced in the code and in the tests:
**ContextRank ranks contextual relevance, never truthfulness.** A figure repeated
across ten documents is not thereby correct, and a graph that scored sources by
centrality would say it was. Nothing here produces a verdict, changes a value, or
withdraws a contradiction on its own. A wrong ranking costs one wasted
suggestion.

And a recovery has to be *knowledge about a claim*, not a story about a pair —
so it propagates to every other comparison those claims take part in, and it
carries across runs, re-judged rather than merely replayed.

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

## What comes out

Run over a six-document corpus of 511 pages, spanning corporate filings and
macroeconomic reports:

| | |
|---|---|
| Facts extracted and grounded | **666** of 797 proposed (**83.6%**) |
| Claims quarantined, with reasons | 131 |
| Comparable blocks | 129 |
| Pairs compared | 1,505 (297 cross-document) |
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

<sub>That writes a new generation of verdicts into the store. It is append-only,
so nothing is overwritten and every earlier run is still there —
`git checkout data/snapshot.sqlite` puts the file back if you'd rather it
stayed untouched.</sub>

The denominator is chosen deliberately: it's what a system with no notion of
context would have flagged — same entity, same metric, values differ. That's the
baseline this design is arguing against.

**The measure of success here is how many apparent disagreements the system
dissolves, not how many it flags.**

---

## Getting started

### Requirements

Python 3.11+ (developed on 3.13). No database server, no vector store, no
external services.

### Install

```bash
git clone <this-repo>
cd SuperJoin
pip install -r backend/requirements.txt
```

### Try it with no API key at all

A pre-computed database ships with the repository, so the whole system can be
explored — every screen, every verdict, every piece of evidence — without
credentials and without spending anything:

```bash
cd backend
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli serve
```

Open **http://127.0.0.1:8000/**.

This is a *live* database, not a set of screenshots. The page text is kept, so
the comparability gate, the interval engine and the deterministic scouts all
re-run against it and produce the same numbers with no key:

```bash
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli relate
```

### Run it on your own PDFs

Add your API key:

```bash
cp .env.example .env      # then fill in OPENAI_API_KEY
```

Then start the server and use the interface:

```bash
cd backend
python -m fkl.cli serve   # UI and API together on http://127.0.0.1:8000/
```

Go to **Add documents**, drop in a folder of PDFs, and set how many pages per
document to read. Ingest, extraction and a corpus-wide comparison run behind a
job whose progress and log you can watch live.

> **Note on cost and time.** Extraction is the only part of this system that
> spends money — one structured model call per page, six pages in flight at a
> time. Start with a small page cap. Everything else, including the entire
> comparison layer, is free and deterministic.

The comparison at the end runs over the **whole corpus**, not just the upload —
because the value of a new document is what it agrees and disagrees with.

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
                              the single-page interface
gold/                         the hand-labelled evaluation set
data/snapshot.sqlite          a pre-computed corpus, so this runs with no key
docs/ENGINEERING.md           the long version: every decision and what it cost
```

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
