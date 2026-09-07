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
| 4 | Temporal engine, cross-document | |
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
reduction. On the annual report plus the earnings deck (195 claims, 391 pairs):

```
  346 pairs whose raw values disagree
    234 explained by a named context axis     (period 216 · consolidation 18)
    108 blocked — a material axis was undetermined
      4 genuinely unresolved
  reduction: 68% of apparent disagreements dissolved by context
```

The denominator is deliberately what a *context-blind* system would flag: same
entity, same metric, values differ. That is the baseline being argued against.

The four survivors are the interesting part, and none is a real disagreement —
two documents from one company mostly should not contradict each other. One is a
sign convention (`Less: Exceptional Items 224.10` against `(224.10)`, the same
figure with the sign carried by a row label); three trace to a single extraction
error where two chart series shared a predicate. A small residual set is useful
precisely because each survivor is traceable.

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
