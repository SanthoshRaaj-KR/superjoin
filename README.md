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
| 5 | React UI | |
| 6 | Macro corpus, zero code changes | |
| 7 | Axis discovery, eval harness | |

## Setup and Run Instructions

```bash
pip install -r backend/requirements.txt
cp .env.example .env      # then add your OPENAI_API_KEY
cd backend
```

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
      2 genuinely unresolved
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
  second look: 8 contradiction(s) sent back to the page, 6 withdrawn
    context recovered on review: estimate_vintage 2 · sign_convention 1
                                 measure_basis 1 · time_reference 1 · area 1
```

Five of the six became CONTEXTUAL with the axis named. The sixth became
INSUFFICIENT_EVIDENCE, and it is the most interesting of them. Asked what
separated *"Core inflation increased to 4.6 percent (from 3.5 percent FY2024/25
average)"*, the investigator answered `time_reference`, labelling one side
`"3.5 percent FY2024/25 average"` — a real label, printed — and the other
`"4.6 percent"`, which is the figure wearing a label's clothes. That passes a
naive "the value must appear on the page" check perfectly, because of course it
does; it *is* the value. The circular half is dropped, which leaves one real
label and one absence — an undetermined axis, so the pair is blocked rather
than explained. Explaining a disagreement by restating one of the two numbers is
not an explanation.

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
                         51 days with no Company Secretary
```

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

## Approach

See [PLAN.md](PLAN.md) for the full architecture and the reasoning behind it.

## Limitations and Next Steps

To be written against what actually ships.

## Additional Notes

To be written.
