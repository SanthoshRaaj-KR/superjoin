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
| 2 | Units, periods, metric and entity registries | |
| 2.5 | Hand-labelled gold set | |
| 3 | Comparability gate | |
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
