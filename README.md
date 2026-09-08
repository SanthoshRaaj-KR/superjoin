# Fact Knowledge Layer

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
| 7 | Axis discovery, eval harness | partly — axes are discovered on review |

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

The interface has five screens, and the one worth opening first is **Compare**.
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

### The headline number

```bash
python -m fkl.cli relate
```

Runs the gate over every comparable pair of stored claims and reports the
reduction. Over all five documents (276 claims, 444 pairs):

```
  391 pairs whose raw values disagree
    272 explained by a named context axis
    117 blocked — a material axis was undetermined
      4 genuinely unresolved
  reduction: 69% of apparent disagreements dissolved by context
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
  second look: 7 contradiction(s) sent back to the page, 3 withdrawn
    context recovered on review: sign_convention 1 · measure_basis 1 · definition 1
```

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

To be written against what actually ships.

## Additional Notes

To be written.
