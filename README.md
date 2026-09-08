# Fact Knowledge Layer

## Two documents just told you two different numbers. Which one is lying?

### Neither. You asked the wrong question.

> ## "Don't ask whether two facts contradict. Ask whether they are comparable at all."

That's not a tagline. That's the entire architecture, compressed into one
sentence, and every algorithm in this repository exists to make that sentence
computable instead of just quotable.

**This system reads PDFs, pulls out every checkable fact, and — instead of
asking a language model to referee a disagreement — runs each pair of facts
through a graph algorithm most people have only ever heard of in the context
of ranking websites.** PageRank. The one Google was built on. Repurposed here
to do something it was never designed to do: decide **where a fact-checker
should look**, and **which difference in two documents actually matters**,
before anyone gets accused of contradicting anyone else.

No chunking. No "these two paragraphs are 87% similar, must be about the same
thing." No LLM sitting in judgment, handing down verdicts it can't show its
work for. Just facts, evidence, a graph, and a walk across that graph that
decides what's worth investigating.

<sub>Python 3.13 · FastAPI · SQLite · 272 tests · 16/16 on the hand-labelled gold set · 79% of raw disagreements dissolved by context</sub>

---

## The problem, stated the way it actually shows up

A company's annual report says revenue was **₹74,540.82 million**. Its
earnings deck says **₹8,142 crore**. A regulator's report says GDP grew
**6.5%**. The IMF says **6.6%**. Someone's board register says a director
resigned in May 2023; someone else's says a different person held that same
role starting June 2023.

The lazy conclusion is that one of these is wrong. **Usually none of them
are.** One revenue figure is standalone, the other consolidated. One GDP
number is a first estimate, the other a second revision. The two director
records aren't disagreeing — they're describing a **succession**, one person's
term ending exactly where the next one's begins.

Most systems that claim to catch "contradictions" chunk the document, embed
the chunks, and ask a model to eyeball two retrieved passages for semantic
similarity. That approach is broken at the root: **the information that tells
two numbers apart almost never lives next to the numbers.** It's in a section
heading two paragraphs up. It's in a footnote. It's in a column header. Chunk
the document and that context is the first thing thrown away — and once it's
gone, no model, however good, can put it back.

---

## Every algorithm in this pipeline, briefly

Seven stages turn a folder of PDFs into a verdict. Here's what each one
actually does, in a paragraph:

**① Extraction.** The PDF is read **page by page, locally**, never dumped
whole into one giant prompt — long contexts suffer from the well-known
*lost-in-the-middle* problem, and 500 pages in one call is a fast way to get a
confidently wrong answer. Before any model sees a page, a **document tree**
is built from font sizes and layout — document → section → subsection → row —
and every node inherits the unit, period and reporting basis declared above
it. The model then returns typed claims, not prose: entity, metric, value,
unit, period, and an open dictionary of *qualifiers* — the context that
applies — plus a field for what it **couldn't** determine. That second field
is the whole game: a system that admits what it doesn't know is trustworthy
in a way one that guesses never is.

**② Grounding.** Nothing the model says is trusted on its word. Every claim's
quoted evidence has to actually exist, verbatim, on the source page, and the
value has to actually appear inside that quote. Fail either check and the
claim is quarantined with a reason code — never silently dropped, never
silently kept. This is what catches a model that read a number correctly but
**invented the citation** for it.

**③ Canonicalization.** `Acme Ltd.` and `Acme Limited` become one entity.
`crore`, `lakh`, `million` and `billion` become one comparable scale. `FY24`,
`2023-24` and "year ended March 31, 2024" become one interval. Nothing gets
compared until it's speaking the same language — and currencies are never
converted at an invented exchange rate; cross-currency pairs are simply marked
incomparable.

**④ Blocking.** Comparing every fact against every other fact is O(n²) and
almost entirely wasted work. Claims are grouped into buckets by
**canonical entity + canonical metric** first, so a claim about Delhivery's
revenue never gets anywhere near a claim about India's GDP.

**⑤ The comparability gate.** The actual decision-maker, and it is **pure,
boring, deterministic Python** — no model, no sampling, no prompt. It checks,
in order: is a material axis undetermined on either side? Do the two claims'
context vectors differ on anything? Is the time period the same, overlapping,
or disjoint? Only after all of that does it compare the numbers. The output
is never just `CONTRADICTS` or `CORROBORATES` — it's `CONTEXTUAL`, with the
**exact axis named**, or `INSUFFICIENT_EVIDENCE`, naming what's missing.
Nothing here is a language model's opinion.

**⑥ ContextRank.** *(This is the one with no template to copy from — full
explanation below, because it deserves one.)*

**⑦ Temporal reasoning.** Facts about roles, addresses and identifiers aren't
numbers — they're **states that hold over an interval**. The system tracks two
independent clocks (when something was *true*, and when a document *asserted*
it), and it tracks how many people a role can have at once, learned from
observation rather than assumed. That's the difference between correctly
spotting a **succession** and incorrectly screaming "contradiction" every time
someone's job title changes.

---

## 🔥 How PageRank solves two problems it was never built for

Here's the part of this system that doesn't have an off-the-shelf answer.

Once the pipeline above has extracted, grounded and canonicalized every fact,
it's still left with two genuinely hard problems that a comparability gate
*alone* cannot solve:

### Problem 1 — The Comparison Problem: *"Out of everything these two facts could differ on, which difference is the one that actually matters?"*

Two claims about the same metric can carry a dozen qualifiers each —
consolidation, period, scale, currency, estimate vintage, price base, scope,
and whatever else the extractor picked up. Most of those qualifiers are
**shared** between the two claims, because that's *why* they were compared in
the first place — two claims about "FY2026 GDP growth" are both, trivially,
about FY2026 GDP growth. The question isn't what they have in common. It's
**which one axis, out of all of them, is the one where the two claims
actually split.**

This is a ranking problem, and ranking problems are exactly what PageRank is
built for — just never over *this* kind of graph before. The system builds a
graph out of the facts it already extracted: every claim gets linked to its
document, its section, its metric, its period, every qualifier it carries, and
the distinctive words in its own evidence. Then it runs **two separate
Personalized PageRank walks** — a "random surfer" that starts at one claim,
wanders the graph, and keeps teleporting back to where it started — one walk
seeded at claim A, one seeded at claim B.

Where the magic happens is in what gets read off those two walks, side by
side:

```
connection(node)  =  how strongly BOTH walks agree this node matters
divergence(node)  =  how much the two walks DISAGREE about this node
```

An axis is a serious suspect only when it's **both**: strongly connected (it's
a live, relevant concept near this specific pair — not just central to the
whole corpus) **and** strongly divergent (the two claims actually land on
different values of it). An axis both claims already agree on lights up on
connection and goes completely dark on divergence — correctly, because
agreeing on something can't be what makes two facts disagree. That single
piece of arithmetic is the entire trick: **it turns "what's this pair about"
into "what's actually tearing this pair apart."**

Measured, not asserted: on a genuine before/after — a cold run with no
remembered recoveries, so every contradiction is investigated from scratch,
same corpus, same twelve candidate pairs, same number of model calls either
way — adding this ranking step took the count of contradictions correctly
withdrawn from **5 to 8**, leaving only 2 genuinely unresolved instead of 5.
Same cost. Three more real disagreements correctly resolved, purely because
the system now asks about the right axis first instead of guessing.

### Problem 2 — The Context-Fetching Problem: *"The document actually said the thing that would explain this — where on the page is it, and how do I find it without re-reading everything?"*

Here's the uncomfortable truth this system had to confront: **most
contradictions aren't real disagreements. They're extraction failures.**
Auditing the very first set of contradictions this corpus produced, every
single one turned out to be a distinction the document actually printed —
in a row label, a footnote, a parenthetical — that simply didn't survive being
turned into a typed claim.

So before any contradiction gets reported, the system goes back to the
document for a second look. But *"go re-read the whole page and see if you
missed anything"* is exactly the kind of vague, expensive, easy-to-get-wrong
instruction that makes LLM pipelines flaky. **A model works far better when
it's told exactly what to hunt for.**

That's the second job PageRank does here, using the *exact same walk* it just
ran for Problem 1. The same two-vector reading — connection and divergence —
doesn't just rank known *axes*. It also ranks **individual words** in each
claim's evidence text, scoring highest the words that sit near one claim and
not the other. So instead of handing an investigator two full pages and
hoping, the system hands over a **shortlist**: *"Look for something to do with
`adjusted`, `generated`, `operations` — these words are structurally rare and
they only appear on one side of this pair."* That shortlist is what turns an
open-ended "find the difference" task into a targeted lookup — the difference
between asking someone to solve a crossword blind and pointing at the exact
clue.

**And this is the part that matters most, enforced by both the code and the
tests, not just by good intentions: ranking never becomes deciding.**
PageRank here can suggest an axis or a word is worth checking. It cannot
change a value, cannot issue a verdict, and cannot withdraw a contradiction on
its own — that decision always goes back through the exact same deterministic
gate from Problem 1. A figure repeated across ten documents is not thereby
correct, and a graph that scored *sources* by popularity would say it was.
This graph never scores sources. It only ever scores *where to look next*.
Get the ranking wrong, and the system wastes one model call. It can never,
structurally, get a fact wrong because the ranking was wrong — there's a test
that exists purely to guarantee the object this stage returns has no field
that could ever be mistaken for an answer.

**The full graph structure, every edge weight, the exact PageRank iteration
and both scoring formulas — worked through against the real numbers from this
corpus — are documented in [`docs/ENGINEERING.md`](docs/ENGINEERING.md) for
anyone who wants to see precisely how the mechanism works, not just what it
achieves.**

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
| Contradictions raised and then withdrawn on review (PageRank-assisted) | 8 |
| Context axes the system **learned** (not built in) | 11 discovered, 7 promoted |
| Hand-labelled gold set | **16 / 16** |

Every one of those numbers is one command away, against the database that
ships with this repository and with no API key:

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
context would have flagged — same entity, same metric, values differ. That's
the baseline this design is arguing against.

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
