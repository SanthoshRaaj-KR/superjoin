# Fact Knowledge Layer — Superjoin VIT 2026 Assignment

**Architecture & Build Plan**

---

## Context

The brief asks for a system that extracts facts from PDFs, grounds each in source
evidence, and decides whether facts corroborate, contradict, or reconcile through
context. It must accept arbitrary new PDFs and demonstrate four specific cases.

Two constraints shape everything:

> "A graph database or visualization alone is not the solution. The interesting part
> is how facts are discovered, grounded, compared, and explained."

> Demo video: **3 minutes or less.**

The second matters as much as the first. A design decision that cannot be *shown* in
180 seconds is worth less than one that can, however sound it is.

### The thesis

> **Don't ask whether two facts contradict. Ask whether they are comparable at all.**

Most submissions will build chunk → embed → LLM "do these conflict?" → verdict. That
pipeline answers a question it was never entitled to ask. `₹81,415M` and `₹74,541M` are
not in disagreement — they answer two different questions, and no amount of model quality
fixes that, because the information needed to tell them apart was thrown away at chunking.

The system is therefore a **comparability gate**:

```
              two claims
                  │
                  ▼
        ┌───────────────────┐
        │  COMPARABLE?      │   ← deterministic, explainable
        └─────────┬─────────┘
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
  NO — axis   NO — missing   YES
  differs      qualifier      │
     │            │           ▼
     ▼            ▼      compare values
 CONTEXTUAL   INSUFFICIENT   ╱    ╲
 (named axis)  _EVIDENCE  agree  disagree
                            │       │
                            ▼       ▼
                      CORROBORATES CONTRADICTS
```

The LLM never reaches a verdict. It does the hard fuzzy work — turning prose and tables
into typed claims with their context attached. Comparison is structural, reproducible,
and **names the axis** responsible.

Three corollaries most submissions will get wrong:

- **Absent ≠ equal.** A claim with unknown consolidation basis does not "match" a
  consolidated claim on that axis. Missing context blocks comparison rather than
  permitting it. `INSUFFICIENT_EVIDENCE` is a first-class verdict.
- **Facts are immutable; intervals close.** Nothing is ever updated. A later document
  closes an earlier open-ended assertion.
- **Refusing to compare is the product.** Value is measured by how many apparent
  disagreements the system *dissolves*, not how many it flags.

### The headline metric

Run the whole corpus, report one line:

> *N raw value disagreements. M explained by a named context axis. K genuinely unresolved.*

Others will show three hand-picked examples. This shows the reduction across 511 pages,
then drills into the survivors. It goes in the README, the video, and the interview.

---

## Evidence: why this thesis fits this data

All six PDFs were probed before designing. Every required case exists, and each needs a
*different* axis — which is exactly why one similarity threshold cannot work.

| Case | Evidence found | Resolving axis |
|---|---|---|
| **1. Corroboration, expressed differently** | FY24 revenue: `₹81,415.38 million` (AR p35 table) ≡ `₹81,415Mn` (AR p3) ≡ `8,142 ₹Cr` (deck p8 chart) | unit **scale** + rounding tolerance |
| **1b. Non-numeric corroboration** | Registered office `...Indira Gandhi International Airport, New Delhi 110037` (Prospectus p29) ≡ `...IGI Airport, New Delhi 110037` (AR p29/p50) | entity/string normalization |
| **2. Genuine contradiction** | FY26 real GDP growth projected `6.5%` (RBI p16) vs `6.6%` (IMF p12) — same entity, period, modality | none; institutions disagree |
| **3a. Reconciled by scope** | FY24 revenue `₹74,540.82M` **standalone** (AR p21) vs `₹81,415.38M` **consolidated** (AR p35) | `consolidation` |
| **3b. Reconciled by vintage** | India real GDP growth FY25 `6.4%` *"first advance estimates"* (Econ Survey p3/p13) vs `6.5%` (RBI p7, IMF p9) | `estimate_vintage` |
| **3c. Reconciled by time** | Suvir Suren Sujan active director (Prospectus p29/p86/p91) vs *"resigned w.e.f. August 24, 2023"* (AR p23/p90) | interval closure |
| **3d. Identity under change** | CIN `U63090DL2011PLC221234` → `L63090DL2011PLC221234`; same registration number `221234`, prefix flips on listing | identifier semantics + time |
| **4. Extraction failure** | Deck p8: `59% 63% 62% 24% 16% 19% ... 7,054 7,224 8,142 FY22 FY23 FY24` — no reading order; shares mis-bind to years | detected, low confidence, quarantined |
| **Axis discovery** | Deck p8 footnote *"FY22 numbers are on pro forma basis"*; Prospectus p21/p26 `Restated` columns | `pro_forma`, `restated` — **learned, not hard-coded** |

**Temporal evidence (AR p90)** — a cardinality-1 role with a complete succession chain:
Sunil Kumar Bansal (resigned `2023-05-31`) → Vivek Kumar (`2023-06-01` → `2024-03-27`) →
Madhulika Rawat (`2024-05-17`). One clean handover and one **51-day vacancy** in the same
chain. The same page also has Suraj Saharan holding two roles concurrently (not a conflict)
and Abhik Mitra holding a role at a *material subsidiary* (different org scope).

**The extraction lever:** units, basis and period are declared at *section scope*, not
beside the value. AR financial pages carry `(All amounts in Indian Rupees in million)` and
`Consolidated Balance Sheet as at March 31, 2024` as headers; the RBI appendix table
carries `(At 2011-12 Prices)` and `(Per cent)` in its title. Chunk-and-embed discards
these and every number becomes unitless.

**Corpus:** 511 pages, ~1.70M chars ≈ 425k tokens, all with real text layers (no OCR).
A full extraction pass costs well under $1, so cost is not the constraint — correctness
and latency are.

---

## Architecture

Single Python service (FastAPI + SQLite) + React SPA. No Neo4j, no vector-DB service, no
agent swarm. Backend setup must be `pip install -r requirements.txt && uvicorn app:api`.

```
PDF ─► L0 document profile ─► L1 document tree + context inheritance
    ─► L2 claim extraction (LLM, structured) ─► L3 grounding validator
    ─► L4 canonicalization (units | periods | metric & entity registries)
    ─► L5 COMPARABILITY GATE  ─► L5b axis discovery  ─► L5c temporal engine
    ─► L6 append-only store ─► L7 API ─► L8 React
```

### L0 — Document profiling

One cheap LLM call over the first ~3 pages plus filename-independent heuristics →
`doc_type`, `publisher`, **`as_of_date`**, `primary_entity`, `reporting_period`,
`default_currency`, `default_scale`, `default_consolidation`.

`as_of_date` is the assertion-time axis; without it "active" and "resigned" are just two
conflicting strings. Note this is **document-level context, not truth** — it seeds
extraction, it does not become a fact.

### L1 — Document tree and context inheritance

Not chunks. A tree: `document → section → subsection → table → row → cell`, built from
PyMuPDF layout text, block bboxes and font-size hierarchy. Each node carries a **context
frame** derived from *generic* patterns, never document-specific ones:

- unit declarations — `(All amounts in <X>)`, `(₹ in million)`, `(In percent)`, `(Per cent)`
- basis declarations — `Consolidated` / `Standalone`, `At 2011-12 Prices`, `pro forma`, `restated`
- period declarations — `as at <date>`, `for the year ended <date>`, `FY__`, column headers
- section headings from the font hierarchy

Context flows downward and merges, so on reaching cell `81,415.38` the extractor already
knows *INR million + consolidated + FY2024 + revenue* without those words being adjacent.
`page_sha256` computed here is the key for incremental re-ingest.

### L2 — Claim extraction (the only place the LLM decides anything)

`instructor` + Pydantic structured output, per page or per table, with the inherited
context frame injected.

```python
class Confidence(BaseModel):        # decomposed, never one blended float
    extraction: float               # did the LLM read the page correctly?
    grounding: float                # did the quote and value verify? (L3)
    normalization: float            # unit/period parse certainty (L4)
    entity_match: float             # entity + metric resolution certainty (L4)
    comparison: float | None        # set per-relation by L5, not per-claim

class Claim(BaseModel):
    subject: str                    # "Delhivery Limited", "India", "Suvir Suren Sujan"
    predicate: str                  # VERBATIM: "revenue from services"
    org_scope: str | None           # role claims: which org — parent or subsidiary
    qualifiers: dict[str, str]      # OPEN dict — from context frame + local text
    unknown_qualifiers: list[str]   # axes the extractor could NOT determine  ← critical
    modality: Literal["actual","estimate","projection","target","restated"]
    evidence_quote: str
    page_no: int
    assertion_time: date            # inherited from document as_of_date
    confidence: Confidence
    confidence_reasons: list[str]   # e.g. "table column alignment unreliable"

class MeasurementClaim(Claim):
    value_raw: str; value_num: float; unit_raw: str; period_raw: str

class StateClaim(Claim):            # roles, addresses, identifiers, statuses
    value_text: str
    valid_from: date | None
    valid_to: date | None
    valid_to_is_open: bool          # "still true as far as this document knows"
```

Only **two** claim types. Events are derived as a view over interval boundaries (the end
of one interval is the start of the next) rather than stored — storing them duplicates the
information and creates a sync problem for no query that intervals can't already answer.

`unknown_qualifiers` is what makes the gate honest — the extractor must declare what it
could not determine rather than silently omitting it. No metric whitelist in the prompt.

Confidence is **decomposed, never blended into one float**. A claim can be perfectly
grounded but poorly normalized, or cleanly parsed but weakly entity-matched; a single
number hides exactly the distinction you need when debugging. Each sub-score is set by the
layer that owns it, and the UI shows the breakdown rather than an aggregate.

### L3 — Grounding validator

Every claim must survive: `evidence_quote` locatable on its cited page under
normalization (whitespace, ligatures, `₹`, dashes, hyphenation) **and** `value_raw` present
inside the quote. Failures are quarantined **with a reason code**, never dropped.

This yields a measurable extraction-precision number and enforces the key property:
**the LLM can propose a fact, but it cannot make that fact trustworthy by itself.**

### L4 — Canonicalization

- **Units** → `(dimension, currency, scale)`. INR lakh/crore/million/billion → INR base;
  percent, ratio, days, tonnes, count. **Never FX-convert USD↔INR** — cross-currency pairs
  are `incomparable`, stated as a trade-off rather than an invented rate.
- **Periods** → `FY24` / `2023-24` / `FY2023/24` / `year ended March 31, 2024` / `Q4 FY24`
  → `(start, end, granularity)`. India FY = Apr–Mar; the IMF writes `FY2024/25` where the
  RBI writes `2024-25`. This mapping alone unlocks the macro corroboration case.
- **Metric registry** (evolving schema) — canonical metrics with alias sets. A new
  `predicate` is embedded, matched against the registry: high similarity → link; grey band
  → one cheap LLM adjudication; below → new canonical metric. Persists and grows.
- **Entity registry** — same, plus **identifier-based merging**, which beats embeddings:
  DIN `01173669` pins Suvir Sujan across documents regardless of spelling or designation;
  CIN root `221234` pins the company across the `U`→`L` change.

**Registry tuning principle:** in the grey band, prefer to **merge**. A wrong merge is
recoverable — the axis diff marks the pair CONTEXTUAL, or the values simply agree or
disagree correctly. A wrong split is invisible forever. *Merge errors are visible; split
errors are silent.*

### L5 — The comparability gate (deterministic; the centrepiece)

Block on `(canonical_entity, canonical_metric)` — avoids O(n²) and is the scaling story.
Each pair is evaluated in fixed order:

1. **Blocking** — same entity, same canonical metric?
2. **Sufficiency** — does either claim have `unknown_qualifiers` on an axis *material* to
   this metric? → `INSUFFICIENT_EVIDENCE`, naming the missing axis.
3. **Axis diff** — set of axes where the qualifier vectors differ.
4. **Temporal relation** — `same | overlapping | disjoint`.
5. **Value relation** — after unit normalization: `agree | disagree | incomparable`.
   Tolerance is relative and rounding-aware from the declared scale:
   `relative_difference = abs(A-B) / max(abs(A), abs(B))`, compared against an explicit,
   documented tolerance. `8,142 Cr` vs `81,415.38 Mn` differ by 0.006% → **agree**;
   `36,465.27` vs `36,355` differ by 0.3% → **disagree**.

| sufficiency | period | axis diff | value | verdict |
|---|---|---|---|---|
| missing material axis | — | — | — | `INSUFFICIENT_EVIDENCE` |
| ok | same | ∅ | agree | `CORROBORATES` |
| ok | same | ∅ | disagree | `CONTRADICTS` |
| ok | same | ≥1 | disagree | `CONTEXTUAL` — primary axis named |
| ok | disjoint | any | any | `CONTEXTUAL_TEMPORAL` (trend link, not a conflict) |

Explanations are **templated from the diff**, so they are reproducible:

> *"Both claim Delhivery FY2023-24 revenue. Values differ by 8.4%, but the claims differ on
> `consolidation` (standalone vs consolidated) — not directly comparable."*

A genuine contradiction reports what was checked, rather than inventing a story:

```
CONTRADICTION
Difference: ₹11,415.38M
Checked: ✓ entity ✓ metric ✓ period ✓ currency ✓ unit ✓ scope ✓ basis
No material contextual difference found.
```

An **optional LLM adjudicator** runs only on the residual `CONTRADICTS` set, adding a
natural-language rationale and reconciliation hypothesis into a separate `llm_rationale`
field so machine-derived and model-suggested reasoning are never confused. It returns a
*hypothesis with confidence and evidence* — it never rewrites the verdict.
Structure first, model second.

### L5b — Axis discovery (schema evolution, done for real)

When a pair lands in `CONTRADICTS` but the two claims' context frames differ textually,
hypothesize the differing phrase as a **candidate axis**. If the same candidate recurs
across ≥N independent pairs, promote it to the axis registry and re-run affected pairs.

```
observations → repeated qualifier → candidate axis → axis registry
```

The corpus proves itself: the deck footnote *"FY22 numbers are on pro forma basis"* and the
prospectus's `Restated` columns should make `pro_forma` and `restated` **learned** axes,
not hand-typed ones. This is the "schema that evolves dynamically" brownie point
demonstrated rather than claimed.

### L5c — Temporal engine (bitemporal, append-only)

**Nothing is ever updated.** Two independent time axes per claim:

- **valid time** — when it was true in the world (`valid_from`, `valid_to`, `valid_to_is_open`)
- **assertion time** — when a document claimed it (`as_of_date`)

**Interval reconciliation:**

| situation | verdict |
|---|---|
| later assertion closes an earlier OPEN interval, at a date after the earlier `as_of_date` | `CLOSES_INTERVAL` — a refinement, **not** a contradiction |
| two assertions give **different** end dates for the same interval | `CONTRADICTS` |
| both assert the same interval | `CORROBORATES` |

**Predicate cardinality** — each canonical predicate carries `cardinality: 1 | N` per
`(entity, org_scope, time)`. `CEO`, `CFO`, `company_secretary`, `registered_office`, `CIN`
are 1; `director`, `subsidiary` are N. This turns "is this a contradiction?" into a
constraint check:

| cardinality | intervals | verdict |
|---|---|---|
| 1 | disjoint, contiguous | `SUCCESSION` |
| 1 | disjoint with a gap | `SUCCESSION_WITH_VACANCY` (gap reported) |
| 1 | overlapping, different holders | `CONTRADICTS` |
| N | overlapping | both valid — no relation emitted |

Cardinality is seeded by a tiny generic default (roles containing "Chief", "Secretary",
"Officer", singular identifiers → 1) and corrected by observation: if the corpus shows two
holders with genuinely overlapping intervals asserted by the *same* document, the predicate
is N, not 1. It is a learned property, not a hard-coded list.

**Time-travel query** — "board as of 2022-06" vs "as of 2024-03" returns different rosters,
both correct: Suvir and Colleran drop off, Anindya Ghose appears.

### L6 — Storage (append-only)

SQLite via SQLAlchemy: `documents`, `document_pages`, `document_nodes`, `entities`,
`entity_identifiers`, `metrics`, `metric_aliases`, `claims`, `measurements`,
`state_claims`, `evidence`, `axes`, `axis_values`, `predicates` (with cardinality),
`relations`, `quarantine`.

Claims are **insert-only**; current state is always a query with an as-of clause, never a
stored mutable field. Relations are relational rows (`claim_id`, `relation_type`,
`target_claim_id`) — a graph can be *rendered* from that later, but the graph is a
representation, not the reasoning mechanism.

Embeddings as blobs with brute-force numpy similarity — right for this corpus, avoids a
service dependency. `page_sha256` → skip re-extraction on re-upload; incremental ingest
falls out of the cache rather than being a separate feature.

### L7 — API (FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /documents` | upload → job id |
| `GET /jobs/{id}` | ingest progress |
| `GET /documents` | list |
| `GET /facts` | filter by entity / metric / period / doc; returns **all** matching facts, never one "best" answer |
| `GET /facts/{id}` | single fact + evidence + confidence breakdown |
| `GET /relations?type=` | relation list |
| `POST /compare` | two claim ids + optional `mask_axes[]` → verdict (powers the counterfactual toggle) |
| `GET /as-of?date=` | time-travel state query |
| `GET /metrics` | registry + alias merges |
| `GET /axes` | discovered axes |
| `GET /history` | append-only knowledge log |
| `GET /quarantine` | failed extractions with reasons |
| `GET /pages/{doc}/{page}/image` | rendered crop for evidence display |

`/docs` gives graders an inspectable API with no UI knowledge required.

**Retrieval split, stated deliberately:**

| Job | Mechanism |
|---|---|
| Is `revenue from services` the same metric as `revenue from contracts with customers`? | embeddings + LLM adjudication, cached in the registry |
| Is `the Company` the same entity as `Delhivery Limited`? | embeddings, plus identifier merge on DIN/CIN |
| Which claims should I compare? | exact query on `(canonical_entity, canonical_metric)` |
| Are these two comparable? | deterministic axis diff |
| What did canonicalization miss? | offline embedding sweep across blocks |

**The embedding decides what things are called; the structure decides what follows.**
Embeddings appear in exactly two places, both at canonicalization time, plus one offline
audit. There is no RAG over raw chunks and no natural-language query layer anywhere in the
system.

**Context-split responses.** `GET /facts` never picks a winner. When a filter matches
several facts that differ on a material axis, the response carries all of them plus the
axis that separates them — so "Delhivery revenue FY24" returns `₹74,540.82M standalone`
*and* `₹81,415.38M consolidated`, flagged as answers to different questions. This is the
behaviour that would have justified a question-answering endpoint, delivered without one.

**Blind-spot sweep** — offline, not per query: embed all claims and find pairs that are
semantically close but landed in *different* blocks. Those are canonicalization misses.
Surface them as "possible missed links"; each either gets promoted into the registry as a
new alias (permanently improving recall) or is confirmed correctly separate. Turns a silent
failure into a visible one.

### L8 — React SPA: four screens that carry the demo

1. **Comparison view** — field-by-field alignment with ✓/✗ per axis, evidence crops from
   both source pages with the quote highlighted, then the verdict and its named primary
   axis. The screen the interviewer remembers.

   ```
                    FY2024 Revenue
   ┌─────────────────────┐   ┌──────────────────────┐
   │ Annual Report       │   │ Investor Presentation│
   │ ₹81,415.38 million  │   │ ₹8,142 crore         │
   │ Consolidated · FY24 │   │ Consolidated · FY24  │
   │ Page 147            │   │ Page 23              │
   └──────────┬──────────┘   └──────────┬───────────┘
              └────────────┬────────────┘
                           ▼
                    ✓ CORROBORATES
   Same entity, metric, period and scope.
   Values differ only by unit conversion and rounding.
   ```

2. **Counterfactual toggle** — switch an axis off and watch the verdict recompute live:
   `CONTEXTUAL` → `CONTRADICTS` when `period` is masked; back when `consolidation` is
   restored. Cheap, because `compare()` is a pure function. The strongest three seconds
   available in a 3-minute video — it proves the reasoning is computed, not stored.

3. **Timeline / as-of view** — the Company Secretary succession rendered with its clean
   handover and its 51-day vacancy; the board roster scrubber between 2022 and 2024.

4. **Corpus panel** — the headline reduction number, surviving genuine contradictions, the
   discovered-axis registry, and the quarantine with confidence reasons.

Plus the **facts view**, which is a filter, not a search box. Filtering to
`Delhivery · revenue · FY24` returns both the standalone and consolidated facts side by
side under a banner naming the axis that separates them, rather than ranking one above the
other. Refusing to answer with a single number is the point.

**Demo budget** — 180 seconds, allocated in advance: upload + extraction 30s · Case 1
corroboration 25s · Case 2 contradiction 20s · Case 3 contextual + counterfactual toggle
40s · Case 3c timeline and vacancy 30s · Case 4 quarantine 20s · reduction number 15s.
That is 180 with no slack, which is the concrete reason nothing else earns a screen.

---

## Build order

One PDF first, adding documents as pressure tests. Each phase is a meaningful commit
series — the brief asks for git used meaningfully.

| Phase | Scope | Proves |
|---|---|---|
| **0** | `git init`, skeleton, `.env.example`. AR FY24 only: text → LLM → claims → SQLite → JSON | end-to-end skeleton |
| **1** | Document tree + context inheritance (L1) + grounding validator (L3) | extraction quality jumps; quarantine appears |
| **2** | Units, periods, metric + entity registries | canonical comparison becomes possible |
| **2.5** | **Hand-label the gold set** (~40 claims, ~15 relations), before any comparison code exists | gives the engine a target instead of a postscript |
| **3** | Comparability gate (L5) incl. `INSUFFICIENT_EVIDENCE` + templated explanations, scored against the gold set | cases 1/2/3 inside one document (standalone vs consolidated) |
| **4** | Temporal engine (L5c) + add Prospectus 2022 + Q4 deck | cross-document: director timeline, CS succession + vacancy, CIN change, Cr↔Mn corroboration |
| **5** | React: comparison view + counterfactual toggle + timeline + corpus panel | the demo becomes filmable |
| **6** | Add the three macro PDFs — **zero code changes** | generalization to a different domain and entity type |
| **7** | Axis discovery (L5b) · blind-spot sweep · vision fallback for chart pages | schema evolution + quantified precision |

**Phase 6 is the one to protect.** If the macro dataset ingests without touching code, the
README can state: *"the same pipeline handles a corporate prospectus and an IMF Article IV
consultation with no document-specific logic"* — exactly the generalization criterion being
graded. Phase 5 is second priority: an unfilmable system scores badly regardless of its
internals.

---

## What this deliberately does not build

Stated in the README, because restraint is a signal:

- **No agent swarm.** Five agents passing messages is a meeting, not an architecture.
- **No GraphRAG.** It builds an entity graph, clusters it into communities and
  *summarizes* them for global sensemaking. Summarization destroys exact values, units and
  page-level evidence — the three things being graded — and it has no notion of qualifier
  vectors or validity intervals, so it cannot separate standalone from consolidated.
- **No Neo4j / graph visualization.** The brief says explicitly it isn't the solution.
- **No RAG over raw chunks.** Chunking is what discards the section-scope context that
  makes any of this decidable.
- **No chat box / natural-language query layer.** A search box is the most recognisable
  surface in this problem space; a grader who sees one has categorised the project before
  reading anything underneath. The useful part — refusing to collapse differently-scoped
  facts into one answer — is delivered by context-split responses in the facts view, which
  needs no question-answering layer at all.
- **No vector search for numerical comparison.** Embeddings do not know that
  `8,142 crore ≈ 81,420 million`; arithmetic does.
- **No FX conversion.** USD and INR claims are `incomparable`, not converted at an invented
  rate. Correct-by-refusal beats plausible-and-wrong.
- **No hard-coded starter documents.** Graders will add new PDFs.

The division of labour, stated plainly:

```
LLM   → interpret, extract, suggest
Code  → normalize, validate, compare, calculate, decide
```

---

## Trade-offs to state honestly

1. **Determinism over LLM judgement in the verdict.** Cost: a contradiction the axis
   vocabulary cannot express is mislabelled — which is exactly why L5b learns new axes.
   Benefit: every verdict is reproducible and explainable.
2. **Recall sacrificed for grounding.** Quarantining ungrounded claims loses real facts
   whose quotes were mangled by layout. The quarantine view makes the loss visible rather
   than silent.
3. **Cardinality inference is heuristic.** A wrongly-inferred cardinality-1 predicate
   produces a false contradiction. Mitigated by same-document overlap evidence, and the
   inferred value is shown in the UI so it is auditable.
4. **Chart pages are a known weakness.** The text layer cannot recover reading order in the
   Q4 deck. Vision fallback is Phase 7; if time runs out the failure ships documented with
   the exact page as required case 4.
5. **SQLite + brute-force similarity.** Right for 511 pages. The blocking-key design is
   what carries it to 50k; say what changes at that scale.

---

## Verification

- **Grounding precision** — % of claims whose quote and value verify against the source page.
- **Reduction ratio** — raw disagreements → explained → unresolved, over the full corpus.
  The headline number; regenerable by a single command.
- **Temporal correctness** — assert the CS chain yields `SUCCESSION` (Bansal→Vivek) and
  `SUCCESSION_WITH_VACANCY` (Vivek→Rawat, 51 days), and that Suvir's prospectus/AR pair
  yields `CLOSES_INTERVAL` rather than `CONTRADICTS`.
- **Gold set** — built in Phase 2.5, *before* the comparability engine, so the engine is
  developed against a target rather than scored after the fact. ~40 claims and ~15
  relations from the evidence table above. Must cover: unit conversion, rounding, same fact
  worded differently, standalone vs consolidated, estimate vs actual, different periods,
  temporal supersession, genuine contradiction, missing context, and scrambled table
  extraction.
- **Generalization test** — ingest the macro PDFs after building only against Delhivery.
  Any code change required is a design failure worth recording in the README.
- **Unseen-PDF test** — ingest a Delhivery quarterly or RBI bulletin not in the starter set,
  cold. The graders said they may test with additional PDFs.
- **Incremental test** — re-upload an existing PDF; assert zero extraction calls, no dupes.
- **Demo rehearsal** — all four cases reachable in under 3 minutes. Rehearse before Phase 7.

---

## Open items before Phase 0

- OpenAI model choice for bulk extraction vs adjudication — verify current model names and
  structured-output support at build time rather than assuming.
- Whether to commit a pre-computed SQLite database so graders can browse results without an
  API key. Recommended: the brief asks for evaluability without your account.

---

## The one-paragraph pitch

> A context-aware, evidence-grounded fact layer with a deterministic comparability engine.
> Every number is extracted together with the section-scope context that gives it meaning —
> unit, period, consolidation basis, estimate vintage — and no two facts are compared until
> the system has established that they are comparable. The LLM proposes claims; structure
> decides what follows. The result is that apparent contradictions dissolve into named
> context differences, genuine ones survive with a stated list of everything that was
> checked, and both come with the page and quote they came from.
