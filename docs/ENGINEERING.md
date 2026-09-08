# Engineering notes

> This is the long version — every decision, what it cost, and the experiments
> that produced the numbers. **Start with [the README](../README.md)** for what
> the system is and how to run it.
>
> Some sections were written as each phase landed and quote the corpus as it
> stood at the time. Where a figure here disagrees with the README, the README
> is current, and `python -m fkl.cli relate` is the arbiter.

---

## Fact Knowledge Layer

Extracts numerical and semantic facts from PDFs, grounds every fact in the exact
text that supports it, and decides whether two facts corroborate, contradict, or
merely answer different questions.

The central idea:

> **Don't ask whether two facts contradict. Ask whether they are comparable at all.**

`₹81,415M` and `₹74,541M` are not in disagreement. One is consolidated and one is
standalone — they answer two different questions, and no amount of model quality
fixes that, because the information needed to tell them apart is usually discarded
before the comparison is ever made.

So the LLM never issues a verdict here. It does the fuzzy work: turning prose and
tables into typed claims with their context attached. The verdict comes from a
deterministic gate that names the axis responsible for a difference.

---

## Status

Under construction, phase by phase. This README is filled in as each layer lands.

| Phase | Scope | State |
|---|---|---|
| 0 | Skeleton, storage, one PDF end-to-end | done |
| 1 | Layout analysis, context inheritance, grounding validator | done |
| 1b | Figure pass: charts read as images, values still grounded | done, opt-in |
| 2 | Units, periods, metric and entity registries | done |
| 2.5 | Hand-labelled gold set | done |
| 3 | Comparability gate | done |
| 4 | Temporal engine, cross-document | done |
| 5 | Reconciliation review, API, UI wired to it | done |
| 6 | Macro corpus, zero code changes | done |
| 7 | Axis registry, `/ask`, upload-a-folder, evidence page images | done |
| 7.5 | ContextRank: personalized PageRank over the claim-context graph | done |

## Setup and Run Instructions

```bash
pip install -r backend/requirements.txt
cp .env.example .env      # then add your OPENAI_API_KEY
cd backend
python -m fkl.cli serve   # UI and API on http://127.0.0.1:8000/
```

The UI and the API are one process on one port, so there is no CORS story and
nothing to configure. `/docs` gives the OpenAPI surface if you would rather
read the data than the screens.

The interface has seven screens. Two are worth opening first.

**Ask** takes a question in English and answers it without averaging. "What was Delhivery's revenue in FY24?" comes back as ₹81,415.38M consolidated *and* ₹74,540.82M standalone, each with its own evidence, because both are correct and the axis that separates them is named. "How fast is India's economy growing in FY26?" comes back as **6.5% · 6.6%**, flagged — two institutions, same period, same modality, and nothing recorded distinguishes them. The model parses the question and stops; retrieval is an exact query over typed claims, and the split comes from the qualifier vectors the extractor attached.

**Compare** is the other one.
It lists candidate pairs with the contradictions the engine *withdrew on
review* at the top, and each one can be taken apart: the two facts, every
context axis with a ✓ or a difference, the evidence span from each source page,
and the verdict. Below that is a row of **mask** buttons — hold an axis out of
the reasoning set and the verdict recomputes live against `POST /compare`:

```
INSUFFICIENT_EVIDENCE  →  Mask Time_reference  →  CONTRADICTS
```

`time_reference` is not a built-in axis. The system found it on a second look
at IMF page 9, and it is maskable for the same reason every other axis is —
the vocabulary is open all the way through, including in the UI.

Ingest and inspect a document. `--no-llm` runs layout analysis and context
inheritance with no API key and no spend:

```bash
python -m fkl.cli ingest ../starter-datasets/delhivery/*.pdf --no-llm
python -m fkl.cli page 2 35          # page 35 as the extractor will see it
python -m fkl.cli page 2 35 --raw    # the same page as the PDF stores it
```

The difference between those two commands is most of Phase 1. With a key set:

```bash
python -m fkl.cli models --prefix gpt   # what your key can actually see
python -m fkl.cli ingest ../starter-datasets/delhivery/02-*.pdf
python -m fkl.cli extract 1 --pages 20-24,33-36
python -m fkl.cli report
python -m fkl.cli export 1 -o ../out/ar-fy24.json
```

Extraction is idempotent by page — re-running skips pages that already have
claims, so a repeat costs nothing and cannot duplicate. `--force` redoes them.

**Or drop a folder on it.** The *Add documents* screen accepts a folder of
PDFs, filters out everything that is not one, and runs ingest, extraction and a
corpus-wide comparison pass behind a job whose log you can watch as it goes.
The comparison at the end is over the *whole* corpus rather than the upload,
because the value of a new document is what it disagrees with. The same thing
over HTTP:

```bash
curl -F files=@report.pdf -F files=@deck.pdf      "http://127.0.0.1:8000/api/v1/documents?maxPages=20"
# {"id": 3, "status": "queued", ...}
curl http://127.0.0.1:8000/api/v1/jobs/3
```

A PDF already in the store is recognised by content hash and reused rather than
re-ingested; a scanned one is named and skipped rather than failing the batch.

### Browsing the results without an API key

The store ships. `data/snapshot.sqlite` is the corpus as this README describes
it — 6 documents, 511 pages, 666 grounded claims, 1,505 compared pairs, every
verdict, every withdrawn contradiction and both survivors:

```bash
cd backend
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli serve
```

No key, no spend, every screen populated. This is here because the brief asks
for enough output to evaluate the work without the author's account, and that
is a stronger requirement than it first looks: everything interesting this
system produces exists only after a full extraction pass, and a full extraction
pass costs money. Without a shipped database a grader sees an empty interface
and has to take this file's word for all of it.

It is a *live* database, not a screenshot. Page text and rendered text are kept,
which is most of its 8.6 MB, so the comparability gate, the interval engine and
the deterministic sign scout all re-run against it and produce the same numbers
with no credentials at all:

```bash
FKL_DB_URL=sqlite:///data/snapshot.sqlite python -m fkl.cli relate
```

What is trimmed is only what no screen reads: relations from superseded
generations — the store is append-only, so thirteen runs leave thirteen copies
of every pair — and job rows, which describe runs on a machine you do not have.
Regenerate it with `python -m fkl.cli snapshot`.

The evidence panels render the actual source page with the located span
highlighted, and that needs the PDFs at the paths they were ingested from. With
the starter dataset checked out beside the repository they resolve; without it
the panel falls back to the stored quote and says which it is showing.

### The headline number

```bash
python -m fkl.cli relate
```

Runs the gate over every comparable pair of stored claims and reports the
reduction. Over all six documents (666 claims, 129 blocks, 1,505 pairs):

```
  1373 pairs whose raw values disagree
    1084 explained by a named context axis
     287 blocked — a material axis was undetermined
       2 genuinely unresolved
  reduction: 79% of apparent disagreements dissolved by context
```

The denominator is deliberately what a *context-blind* system would flag: same
entity, same metric, values differ. That is the baseline being argued against.

### A contradiction has to survive an investigation

The gate is careful and it is still not careful enough, because it can only
reason about context that reached it. Two claims arriving with the same entity,
the same metric, the same period and no distinguishing qualifier *must* be
called a contradiction — the gate is right, given what it was handed. The
question is whether what it was handed was complete.

It usually is not. Auditing the eight contradictions the gate first produced
across this corpus, **not one was a real disagreement.** Every single one was a
qualifier printed on the page that did not survive extraction:

```
"Real hourly wages have grown by 16 and 26 percent since 2018
 in rural and urban areas, respectively"     -> one sentence, two facts     [area]

July WEO | 6.4 | 6.4                         -> a scenario table whose row
Current  | 6.6 | 6.2                            labels are the distinction   [estimate_vintage]

Less: Exceptional Items | 224.10             -> the same figure as (224.10),
                                                sign carried by a row label  [sign_convention]
```

So `relate --review` sends every contradiction back to its pages before
reporting it:

```
  second look: 4 contradiction(s) sent back to the page, 8 withdrawn
    context recovered on review: measure_basis 6 · sign_convention 1 · definition 1
```

Over six documents: 666 claims, 1,505 relations, 131 quarantined. The two
survivors are one real disagreement and one traceable defect — the RBI/IMF
projection below, and one core-inflation pair whose two figures were read out
of a single sentence, the parenthetical's period binding to both.

**The survivor that matters is the one it did not withdraw.** The RBI projects
6.5% real GDP growth for 2025-26; the IMF projects 6.6% for the same year. Two
institutions disagreeing about the same future is the corpus's one genuine
contradiction, and the second look was asked about it and left it standing:

```
claims 394 vs 420: same real GDP growth for India in FY2026, comparable on every
stated axis, and the values are 0.1 apart — the smallest gap two figures printed
at this precision can have. Adjacent, and still not the same value.
```

That took three fixes to become visible at all, and it was invisible in three
independent ways: the RBI sentence was never extracted, `Indian economy` and
`India` were separate entities, and the IMF's forecasts were stored as `actual`
so modality separated them anyway. Each one alone was enough to hide it.

**A label never contains the number it labels**, and learning that took two
attempts. The first guard stripped the figure from a proposed label and asked
whether anything substantive remained — enough to catch `"4.6 percent"` offered
as a label for the figure 4.6, which passes "the value must appear on the page"
perfectly because it *is* the value.

It was not enough. Asked what separated the RBI's *"real GDP growth for 2025-26
is projected at 6.5 per cent"* from the IMF's 6.6%, the investigator proposed a
`scenario` axis with the value `"2025-26 is projected at 6.5 per cent, with
risks"` — the claim's own sentence, padded with enough words to look like a
description once the figure was removed. It grounded, because the sentence
really is on the page. And it dissolved the one genuine disagreement in the
corpus, which is the worst thing this layer can do.

So the test is containment, not residue: `July WEO`, `urban areas`, `Adj. EBITDA
margin`, `first advance estimate` — not one names its own value, because a label
says which *kind* of measurement this is and the measurement is the other half
of the pair. And a *rejected* label is not an *absent* one: dropping only the
bad half left a one-sided recovery, which blocks the pair on an "undetermined"
axis and takes it out of the residual set just as effectively as explaining it.
A side we deleted is evidence the model was reaching, and the whole proposal
goes with it.

**The investigator recovers context. It never issues a verdict.** It is asked
one question — is there a qualifier on this page these two claims differ on? —
and its answer is a proposed *fact about the document*, grounded against the
page exactly like any other claim and then fed back through the same
deterministic `compare()`. The gate decides, twice. Three things constrain it:

- **Every recovered value must be found on the page.** The quoted span has to
  locate under the grounding validator, and the axis value has to appear inside
  that span. Two correct proposals were rejected on this rule before the prompt
  was taught to cite the label rather than the figure — the rule did not move.
- **It can only ever make claims less comparable.** CONTRADICTS becomes
  CONTEXTUAL or INSUFFICIENT_EVIDENCE, never CORROBORATES. "Look harder until
  they match" is the failure mode this layer would otherwise introduce, and the
  path is closed by assertion rather than by hope.
- **An axis stated for only one side blocks rather than explains.** If the page
  labels one figure and says nothing about the other, that is an undetermined
  material axis, not a resolution — absent is not equal, applied to an axis
  nobody knew to look for until the second pass found it.

**What is reproducible and what is not.** The gate is a pure function and the
sign-convention scout is arithmetic plus a lexical check, so both give the same
answer on every run — `relate` without `--review` is byte-identical run to run.
The review pass calls a model, and a model at temperature 0 is still not a
guarantee. Its output is therefore constrained rather than trusted: nothing it
proposes takes effect unless it grounds, and the verdict is always recomputed by
the deterministic gate. The residual set can move by a pair between runs; the
verdicts themselves cannot be written by the model at all.

`sign_convention` is resolved with no model call at all: matching magnitudes,
opposing signs, and a sign-carrying row label found on the page. `area` was
never in any list — it was discovered, which is what the axis vocabulary being
open is for.

Withdrawn contradictions are stored beside their original verdict rather than
replacing it, so the record shows what the first pass concluded, what the
second found, and on what evidence. A contradiction the system raised and then
took back is a more interesting object than one it never raised.

### ContextRank: ranking where to look, never what is true

A contradiction is sent back to its pages before it is reported. The question
this layer answers is *where on those pages to look*, and until now the answer
was three hand-set constants — 0.9 for a shared span, 0.8 for a row label, 0.3
for a neighbourhood. Those numbers ranked the scouts. Nothing ranked the axes.

ContextRank is a Personalized PageRank over a graph the pipeline has already
built without meaning to: claims, and the things claims share — documents,
sections, metrics, periods, the qualifier bindings the extractor read off the
text, the axes earlier reviews recovered, and the distinctive words in each
evidence span. Seeded on the two claims in a suspicious pair, it returns a
ranked shortlist of what might separate them.

**The boundary is the whole point, and it is enforced structurally.** This
ranks *contextual relevance*, never truthfulness. A figure repeated across ten
documents is not thereby correct, and a graph that scored sources by centrality
would say it was. So nothing here produces a verdict, nothing changes a value,
and no score can withdraw a contradiction. The output is a list of axis names
and words worth asking about; the model still has to find the values on the
page, grounding still has to locate them, and the same deterministic
`compare()` still decides. A wrong ranking costs a wasted suggestion. There is
a test asserting that a `Ranking` carries no field that could be mistaken for
an answer.

#### Two vectors, not one

A single walk seeded on both claims ranks what is central to the pair — and
what is central to a pair is usually what they have in common, which by
definition cannot distinguish them. Both claims are about FY2026 GDP growth;
that is why they are being compared, not why they differ. So two walks are run,
one per claim, and two quantities are read off them:

```
connection   min(r_a[n], r_b[n])            n sits near both claims
divergence   |r_a[n] − r_b[n]| / (sum)      n sits near one and not the other
```

An **axis** is a candidate when it is connected — the concept is live in this
neighbourhood. A **value** is a candidate when it diverges. An axis both claims
already agree on scores on the first and not the second, and explains nothing.

#### What it measurably does

The honest experiment is a cold run — no remembered recoveries, so every
contradiction is genuinely investigated — with the structural shortlist in the
prompt and without it. Same corpus, same pairs, same number of model calls:

| | contradictions withdrawn | left unresolved | reduction |
|---|---|---|---|
| without ContextRank | 5 | 5 | 78.7% |
| with ContextRank | **8** | **2** | 79.0% |

Same twelve investigations, same three axes in the recovered vocabulary,
three more pairs correctly explained. One corpus and twelve pairs is
suggestive, not conclusive, and it is the number this repository has.

#### Two things that had to be measured before they could be believed

**Inverse frequency is not an optimisation, it is the whole thing.** The first
version weighted every edge equally and was useless: `consolidation` is
recorded or declared undetermined on 366 of 666 claims, so on raw structure it
is adjacent to everything and won every ranking — including for a pair the
pages distinguish by sign convention. That is the popularity signal this module
exists to avoid, arriving through the side door. Weighting each edge by
`log(N / n)` is the statement that a node connected to most of the corpus is
not *about* any particular pair of claims.

**The axis ranker cannot discover an axis; the term ranker can.** Scored
against the six recoveries this corpus already had, the axis ranking got 6 of 6
— and held out, with each pair's own recovery removed from the graph, it got
**0 of 6**. It was reading back its own answer key. The reason is structural
and worth stating: an axis only exists as a node because something already
recorded it, so ranking known axes can find a *recurrence* and never a
discovery. What survives the holdout is the vocabulary: `less` ranked first for
the sign-convention pair, `adjusted` second for the EBITDA pair, `generated`
and `operations` first and second for the two cash-flow pairs — five of six,
with no knowledge of the answer. So the shortlist handed to the investigator is
words first and known axes second, and the two are labelled differently in the
prompt.

#### Attention allocation, and why it is off by default

ContextRank scores how much structural signal a pair has, which makes it a
natural way to decide where to spend model calls. `relate --budget` does
exactly that. It is **off by default**, and that is measured rather than
cautious: the pair the deck distinguishes as `EBITDA margin` against `Adj.
EBITDA margin` scores **zero** on structural vocabulary, because both claims
were read out of one sentence and share every word in it. A budgeted run skips
a pair this system demonstrably explains. The flag exists for a corpus too
large to investigate exhaustively, it trades recall for cost, and a pair it
skips is recorded as *not investigated* rather than *unexplained* — which is a
different and more honest thing to say.

#### Where it shows up

The comparison view carries a **ContextRank** panel under the verdict, headed
*where to look, not what is true*: the ranked axes with their scores and a
templated sentence each, and the words that sit near one claim and not the
other. On the corpus's one surviving cross-institution contradiction it ranks
`scenario` first — which is precisely the axis an investigator once proposed to
explain that disagreement away, and which the circularity guard rejected
because the proposed label quoted its own figure. The two layers disagree in
public, and the deterministic one wins. That is the architecture working, and
it is more informative than a panel that only ever agreed with the verdict.

Every relation stores its certificate in `context_rank`, whether or not
anything came of it. A suggestion that led nowhere is part of the reasoning.

### Time, and the difference between not knowing and being wrong

State claims — who holds a role, what the CIN is — assert intervals, and they
need a different engine. Two clocks: *valid time* is when something was true,
*assertion time* is when a document said so.

The 2022 prospectus lists Suvir Sujan as a serving director with no end date.
The FY24 annual report says he resigned in August 2023. Those do not contradict
— the prospectus was correct about its own moment. Reporting a conflict there
is not strictness, it is an error, and it fires for every officer and address in
any corpus that spans time.

Cardinality turns "is this a conflict?" into a constraint check. Seeded from
generic vocabulary (singular markers tested first, so `Managing Director` comes
out 1 while `Non-Executive Director` comes out N) and corrected by evidence —
one document listing two people in a seat at once is telling us the seat holds
more than one. From the annual report's KMP table:

```
SUCCESSION               Bansal to 2023-05-31, then Vivek from 2023-06-01
SUCCESSION_WITH_VACANCY  Vivek to 2024-03-27, then Rawat from 2024-05-17
                         50 days with no Company Secretary
```

Fifty, not fifty-one. The two dates are 51 days apart and the seat is empty on
50 of them — 28 March through 16 May. The engine keeps both numbers because
they answer different questions: the date difference is what the contiguity
test reads (a gap of 1 is a clean handover, not a one-day vacancy), and the
vacancy is what a reader should be told. Conflating them put an off-by-one in
the most visible finding in the corpus, and the UI's timeline is what caught it.

That vacancy exists only because intervals are modelled rather than
overwritten. A store keeping "current Company Secretary" as a mutable field
shows Rawat and nothing else.

```bash
python -m fkl.cli as-of 2024-04-15   # the seat is empty
python -m fkl.cli as-of 2024-06-01   # Rawat
```

Current state is a query with an as-of clause over immutable claims, never a
stored field, which is what lets two dates give two different rosters and both
be right.

### The gold set

~28 claims and 16 relations, hand-labelled from the source pages **before** the
comparability gate exists, so the gate is built against a target rather than
scored afterwards. No key needed:

```bash
python -m fkl.cli gold --verify
```

`--verify` re-reads all six PDFs and checks that every labelled quote is on the
page it cites. That check is not ceremony: a gold set with a wrong page number
does not fail loudly, it quietly becomes the definition of correct. Two entries
in the first draft were wrong and this is what caught them.

About a third of the labelled relations are pairs that must **not** be linked.
A gold set of only true matches measures nothing, because a system that links
everything scores perfectly on it.

### Comparing values: what the numbers taught us

Three findings from measuring this corpus, each of which changed a design:

**No similarity threshold separates metric names.** Embedding fifteen predicate
pairs with `text-embedding-3-small`:

```
should match      0.683 ─────────────────────── 0.944
should NOT match  0.574 ─────────────── 0.846

0.687  Revenue from contracts with customers :: Revenue from services   SAME
0.846  Adjusted EBITDA :: EBITDA                                        DIFFERENT
```

Any cutoff loose enough to catch the first fuses the second. So embeddings
generate candidates and an adjudicator decides, with dimension as a free
pre-filter — that alone separates `Express Parcel revenue` from `Express Parcel
shipments` at 0.801 without a model call.

**Rounding tolerance cannot be a percentage.** `Revenues from sale of traded
goods` FY23 is `16.54 ₹Mn` in the annual report and `2 ₹Cr` in the deck. That is
21% apart and both are correct, because one crore is the deck's entire precision
at that magnitude. Tolerance has to come from the coarser unit's granularity.

**A year-end date is ambiguous and the ambiguity has to survive.** Annual report
page 35 heads its columns `March 31, 2024` above revenue, meaning the year; page
90 heads them identically above lease liabilities, meaning the instant. Only the
section declaration separates them, so a period read without one is stored
flagged rather than silently resolved.

### Do we need to read the images?

Mostly no, and the measurement is worth more than the answer.

Charts and scanned pages are the obvious worry in a corpus like this, so the
first thing built was a router that finds pages carrying numbers the layout pass
could not attach to anything, and a vision pass to read them. The router
selected 92 of 511 pages. Three things then turned up, in order:

1. **43% of those pages were a bug of ours, not a chart.** The XY-cut was
   slicing financial tables down their own column gutters, so labels landed in
   one region and values in another and nothing could rejoin them. Fixing that
   took the corpus from 70.1% to 86.4% of figures bound to a label, and the
   router from 92 pages to 52. A vision call would have hidden the defect at
   about a thousand tokens a page.
2. **15% of what remains is axis furniture.** `56 54 52 50 48` down the side of
   a PMI chart are tick marks, not facts.
3. **Financial documents restate themselves.** The earnings deck charts FY24
   revenue as `8,142` on pages 8 and 9 — and prints `₹8,142 Cr` in text on page
   5, and in reconstructed table rows on pages 13, 16 and 22. Every case this
   project has to demonstrate is reachable from the text layer, including the
   cross-document ₹Cr-to-₹Mn corroboration that looked like it needed a chart.

Only one page in the whole corpus is genuinely image-only (the IMF cover), and
it carries no data. There is no OCR here because there is nothing to OCR.

So the pass ships **off by default**. It stays because redundancy is a property
of these six PDFs rather than a promise about the seventh, and because the
router is worth running either way — it reports what text extraction did not
reach instead of leaving that unmeasured:

```bash
python -m fkl.cli extract 3 --pages 8 --figures   # read the charts
python -m fkl.cli extract 3 --pages 8             # text only; says what it skipped
```

A figure claim is held to the same standard as any other: the value must be
locatable in the page's own text layer, so vision may propose which bar a number
sits on but can never introduce a number that was not printed. What it cannot
prove is the binding itself, so those claims are stored with `source=figure` and
a confidence ceiling of 0.75.

Run the tests. They stub the single model call, so the suite needs no key:

```bash
python -m pytest tests/ -q
```

Fuller instructions land with the API in a later phase.

### Does this generalise, and where exactly does it stop?

Worth answering with an audit rather than a claim, because "no document-specific
logic" is easy to say and easy to get wrong.

**What is genuinely open.** There is no metric whitelist and no entity
whitelist anywhere in the codebase — `grep` finds zero. `qualifiers` is an open
dict, and `AXIS_PRIORITY` in the gate is only a tie-break ordering for naming a
primary axis; an axis missing from it still works, it just sorts last. The
proof is in the store: five axes now in use appear in no list anywhere —
`sign_convention`, `measure_basis`, `scenario`, `time_reference`, `definition`
— all recovered from the documents themselves.

The corpus already spans two domains that share nothing structurally: a
company's annual report, prospectus and earnings deck, and macroeconomic
reports from three different institutions. The same pipeline reads both, and
the discovered axes come from both halves.

**The cold test.** The 2022 prospectus had never been ingested — a different
document type, from a different year, with sections nothing else in the corpus
has. Run cold, with no code changes:

```
ingest   100 pages · doc_type "prospectus" · as_of_date 2022-05-14 read from
         "Dated May 14, 2022" · default_consolidation left null, with the note
         "contains both consolidated and proforma financials; do not assume a
         single consolidation basis"
extract  46 claims from 2 pages, grounding precision 100%
relate   2 new cross-document corroborations, 1 new discovered axis (nominee_of)
```

Sahil Barua and Deepak Kapoor each resolved to one person across the prospectus
and the annual report, and their roles corroborated across two documents two
years apart. Nothing was configured for any of that.

It also found two bugs, which is the more useful half of a generalisation test:

- **Touching intervals read as overlapping.** The company's own renamings —
  SSN Logistics, then Delhivery Private, then Delhivery Limited — are recorded
  with correct consecutive dates, because a renaming happens *on* its date. The
  annual report writes handovers the other way, "to 31 May, from 1 June".
  Treating the shared boundary as an overlap turned a correctly extracted name
  history into two contradictions about what the company is called.
- **The gate never checked cardinality.** "Other Directorships: Spoton
  Logistics" and "Other Directorships: Vave Health Inc" are both true of the
  same person on the same day. The interval engine has always checked this
  before reporting a conflict; the gate had not, because until a document
  listed someone's other directorships nothing reached it. Cardinality is now
  inferred from the predicate's grammar rather than a vocabulary — a plural
  head noun asks for a list — with the head taken from before any dash, since
  "Head - New Ventures" is one post and not a list of ventures.

Both fixed, both regression-tested. Contradictions went 11 to 6, and the one
that matters stayed.

**Where the vocabulary is tuned, and it is worth being precise.** Parsers
probed with inputs this corpus does not contain:

```
US$ million   -> USD million       ✓      FY2023-24                -> 2023-04-01..2024-03-31  ✓
$bn           -> USD billion       ✓      year ended March 31 2024 -> 2023-04-01..2024-03-31  ✓
EUR thousand  -> EUR thousand      ✓      calendar year 2023       -> 2023-01-01..2023-12-31  ✓
GBP million   -> GBP million       ✓      FY2024 (Oct-Sep)         -> 2023-04-01..2024-03-31  ✗
bps           -> percent           ✓      Q3 2024                  -> 2023-10-01..2023-12-31  ✗
million tonnes-> mass_tonnes       ✓      H1 2024                  -> the whole year          ✗
JPY billion   -> UNKNOWN           ✗
```

Three real limits, stated rather than discovered later:

- **The fiscal calendar is now the document's, not ours.** It used to be a
  module constant, which is the *quiet* kind of wrong: a September filer read as
  April–March gives intervals confidently off by six months and comparisons that
  all look fine. `infer_fy_start_month` reads it from the document's own wording
  — "year ended December 31" says the year opened in January — and it is
  threaded through as a parameter, defaulting to April when a document never
  says. Quarters follow: `Q3` is October–December on an April year and
  July–September on a calendar one, and that was the reading that differed
  silently.

  ```
  FY2024, calendar filer      -> 2024-01-01 .. 2024-12-31
  Q3 2024, calendar filer     -> 2024-07-01 .. 2024-09-30
  FY2024, September filer     -> 2023-10-01 .. 2024-09-30
  Q3 FY24, April filer        -> 2023-10-01 .. 2023-12-31   (unchanged)
  ```
- **Half-years are still not modelled.** `H1 2024` widens to the full year.
- **Identifiers are Indian.** CIN, DIN and ISIN are recognised; a US filing's
  CIK and EIN are not. This one degrades safely rather than failing: no
  identifier means resolution falls back to names and adjudication, which is
  the path everything without a registration number already takes.

Legal-form stripping is not India-specific — `Acme Corporation`, `Acme Corp.`
and `Acme Inc` all normalise to `acme`.

## Approach

See [PLAN.md](PLAN.md) for the full architecture and the reasoning behind it.

## Limitations and Next Steps

Written against what actually shipped, and in the order that matters.

**Extraction is the binding constraint, not the gate.** The gate scores 16/16
on the hand-labelled gold set. Every failure this project actually hit was on
the other side of it: a sentence the extractor never read, a company name that
did not resolve, a forecast stored as an actual. The flagship contradiction in
this corpus — the RBI's 6.5% against the IMF's 6.6% for FY26 GDP growth — was
invisible three separate ways, and none of them was a reasoning error. If there
is one honest summary of where the remaining risk lives, it is that the
comparability argument is sound and the reading is where it breaks.

**The extractor emits some facts twice.** Around 0.6% of stored claims are one
fact read twice on one page with different period readings — a stake described
once as *July 2023* and once with no period, a tonnage read once as FY24 and
once as *since inception*, which is what the page actually says. Nothing
deduplicates within a page. Both copies are grounded and both are visible; the
effect is a slightly inflated claim count and one spurious pair. The fix is a
within-page identity check on `(subject, predicate, value, period)`, deciding
which period reading the evidence span supports.

**Chart pages need the figure pass, and it is off by default.** The text layer
of the earnings deck preserves every number on a chart and destroys which
series and year each belongs to. Page 8 is in the corpus as the required
extraction-failure case, quarantined with a reason. `--figures` reads such
pages as images and recovers the bindings; it is opt-in because on this corpus
the charts restate figures the tables already carry, so every demonstration
case is reachable without it. That is a property of these documents, not a
guarantee about the next deck.

**No FX conversion, ever.** A USD claim and an INR claim about the same metric
are `INCOMPARABLE`. Converting them would need a rate, and a rate needs a date
and a source that no document supplies. Correct-by-refusal, and stated rather
than hidden.

**Cardinality is inferred, and the inference is visible because it is sometimes
wrong.** A predicate wrongly read as single-holder manufactures a contradiction
out of two people who held different posts. Grammar seeds it, and
same-document observation corrects it — the corpus corrects several, including
`Chief People Officer`, where one document names two concurrent holders. It
also over-corrects: `Managing Director and Chief Executive Officer` reads as
multi-holder because director biographies list that post for several *other*
companies, and the org scope is not always recovered from a biography. The
`/api/v1/predicates` endpoint and the Corpus quality screen show the grammar's
guess beside the corpus's evidence for exactly this reason.

**People are never merged automatically.** Organisations, places and
institutions can be merged by an adjudicated decision; `person` is deliberately
excluded, because two people with similar names are a different and much worse
error than two records for one person. The cost is visible in the corpus: the
same Company Secretary appears under two spellings until an identifier ties
them together.

**Periods below a year are widened.** A half-year reference with no other
signal resolves to the enclosing fiscal year, which makes an H1 figure look
comparable to a full-year one. Quarters parse correctly; half-years are the
gap.

**Identifier kinds are Indian.** CIN, DIN and ISIN are recognised; an SEC CIK
or a UK company number is not. This degrades safely — resolution falls back to
names and adjudication, which is the path every entity without a registration
number already takes — but the strongest resolution signal is unavailable
outside India until the kinds are extended.

**The axis promotion threshold is set for a small corpus.** A discovered axis is
believed at two independent pairs. That is defensible over 511 pages and would
be far too low over fifty thousand; the count is stored per axis, so the
threshold is a number to raise rather than a rule to rewrite.

**Remembered recoveries are re-judged but not re-grounded.** A recovery carried
forward from an earlier run has its circularity and confidence checks re-run —
which matters, and was found the hard way when replaying a pre-fix recovery
silently dissolved the corpus's one genuine cross-institution disagreement.
What is not re-run is the page-location check, because the evidence spans are
not stored on the relation. The claims and pages are immutable, so this is safe
today; storing the spans would make it verifiable rather than merely safe.

**Evidence page images need the source PDFs.** The comparison and evidence
views render the actual page with the located span highlighted, which requires
the file at the path it was ingested from. A database browsed without its
documents falls back to the stored quote and says so on the panel.

**SQLite, and similarity by brute force over stored vectors.** Right for 511
pages, and the blocking key on `(entity, metric)` is what carries the design
past it. At fifty thousand pages the store becomes Postgres and the embedding
scan becomes an index; nothing above the storage layer changes, which is the
point of blocking being a design decision rather than an optimisation.

**ContextRank ranks recurrence, not discovery.** Held out, its axis ranking
scores zero: an axis is only a node in the graph because something already
recorded it, so the ranking can find an axis applying *again* and can never
name one for the first time. The vocabulary ranking is what survives a holdout,
and it is words rather than axes — turning a ranked word list into a proposed
axis name is still the model's job. A/B'd over twelve pairs on one corpus it
explains three more of them; that is one experiment, not a result.

### Next, in priority order

1. Deduplicate within a page, and decide the period from the evidence span.
2. Store recovery evidence spans on the relation so a remembered recovery can
   be re-grounded rather than trusted.
3. Recover `org_scope` from biography sections, which is what would stop
   `Managing Director and Chief Executive Officer` being read as multi-holder.
4. Half-year period parsing.
5. Non-Indian identifier kinds.
6. OCR, for the scanned documents this system currently names and skips.
7. A larger A/B for ContextRank, on a corpus where twelve pairs is not
   the whole sample.

## Additional Notes

**On the LLM's role.** It is used in five places and issues a verdict in none of
them: profiling a document, turning a page into typed claims, adjudicating a
name or a metric in the grey band between "clearly the same" and "clearly not",
parsing a question into `(entity, metric, period)`, and proposing a *fact about
the document* when a contradiction is sent back to its pages. That last one is
the most constrained: the proposal must locate on the page, must appear inside
its own evidence, and must not quote the figure it claims to label — and then
the same deterministic gate re-decides. It can supply context. It cannot supply
a conclusion.

**On answering questions without averaging.** `/api/v1/ask` parses the question
and stops. Retrieval is a SQL query over typed claims, and the answer is grouped
by the qualifier vectors the extractor already attached, so "Delhivery's FY24
revenue" comes back as ₹81,415.38M consolidated *and* ₹74,540.82M standalone,
with the axis that separates them named. Where two claims share a context and
still disagree, the answer says so rather than choosing the more confident one —
which is how the RBI/IMF disagreement surfaces from a plain English question.

**On the parts that were measured rather than designed.** The entity
adjudication prompt refused all nine test pairs on its first version; the
current one separates *an entity referred to through an aspect* from *a distinct
body associated with it*, and gets 9 of 9. The circularity guard was written
twice, because the first version tested for residue and the second for
containment, and only the second stops a claim's own sentence being proposed as
the label that explains it away. Both are recorded in comments where they
happened.

**On what is deliberately absent.** No agent swarm, no GraphRAG, no graph
database, no RAG over raw chunks, no FX conversion. The reasoning for each is in
[PLAN.md](PLAN.md); the short version is that chunking discards the
section-scope context that makes any of this decidable, and summarisation
destroys the exact values, units and page-level evidence that are the things
being graded.
