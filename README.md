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
| 1b | Figure pass: charts read as images, values still grounded | done |
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
