// Centralised API layer. Every screen goes through these functions, so pointing
// the app at the real service is one environment variable:
//
//   VITE_API_BASE=/api/v1 npm run dev
//
// With it unset the module answers from an in-memory corpus that runs the same
// comparability gate the backend runs. The mock is stateful on purpose —
// uploading a PDF really does move a document through queued → extracting →
// indexed — so the interface is exercised, not mimed.

import { evaluate } from "./engine";
import { AXES, CONFIDENCE_BANDS, DOCUMENTS, FACTS, PAIRS, QUARANTINE } from "./fixtures";
import type { AxisKey, CompareResult, Corpus, Doc, Fact, Pair, Quarantined } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";
export const LIVE = BASE !== "";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  if (LIVE) {
    const res = await fetch(BASE + path, {
      headers: init?.body instanceof FormData ? undefined : { "content-type": "application/json" },
      ...init,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText} · ${path}`);
    return res.json() as Promise<T>;
  }
  return mock(path, init) as Promise<T>;
}

/* ------------------------------- mock state ------------------------------- */

// Cloned so an upload mutates the session, not the module the fixtures export.
const docs: Doc[] = DOCUMENTS.map((d) => ({ ...d }));
const facts: Fact[] = FACTS.map((f) => ({ ...f }));
const pairs: Pair[] = PAIRS.map((p) => ({ ...p }));
const quarantine: Quarantined[] = QUARANTINE.map((q) => ({ ...q }));

const byId = (id: string) => facts.find((f) => f.id === id);

/** Advance every unfinished document a little. Called on each /documents read,
 *  which is what makes the ingest bar move while the page is open. */
function tick() {
  const now = Date.now();
  for (const d of docs) {
    if (d.status === "queued" && now - (started.get(d.id) ?? now) > 1500) {
      d.status = "extracting";
      d.progress = 0;
    }
    if (d.status === "extracting") {
      d.progress = Math.min(1, (d.progress ?? 0) + 0.055 + Math.random() * 0.04);
      // Claims accrue as pages are read, the way the CLI reports them.
      const target = pageBudget.get(d.id) ?? Math.round(d.pages * 6.4);
      d.claims = Math.round(target * d.progress);
      d.quarantined = Math.round(d.claims * 0.06);
      d.grounded = d.claims - d.quarantined;
      if (d.progress >= 1) {
        d.status = "indexed";
        delete d.progress;
      }
    }
  }
}

const started = new Map<string, number>();
const pageBudget = new Map<string, number>();
docs.forEach((d) => {
  started.set(d.id, Date.now());
  if (d.status !== "indexed") pageBudget.set(d.id, Math.round(d.pages * 6.4));
});

function corpus(): Corpus {
  const claims = docs.reduce((s, d) => s + d.claims, 0);
  const quarantined = docs.reduce((s, d) => s + d.quarantined, 0);
  const verdicts = pairs.map((p) => {
    const a = byId(p.a);
    const b = byId(p.b);
    return a && b ? evaluate(a, b).verdict : "INSUFFICIENT_EVIDENCE";
  });
  return {
    documents: docs.length,
    pages: docs.reduce((s, d) => s + d.pages, 0),
    claims,
    grounded: claims - quarantined,
    quarantined,
    relationships: pairs.length,
    raw: verdicts.filter((v) => v !== "CORROBORATES").length,
    contextual: verdicts.filter((v) => v === "CONTEXTUAL").length,
    contradictions: verdicts.filter((v) => v === "CONTRADICTS").length,
    insufficient: verdicts.filter((v) => v === "INSUFFICIENT_EVIDENCE").length,
    confidence: CONFIDENCE_BANDS,
    axes: AXES,
  };
}

async function mock(path: string, init?: RequestInit): Promise<unknown> {
  await wait(120 + Math.random() * 80);
  const body = init?.body && typeof init.body === "string" ? JSON.parse(init.body) : null;

  if (path === "/corpus") {
    tick();
    return corpus();
  }
  if (path === "/documents") {
    tick();
    return docs.map((d) => ({ ...d }));
  }
  if (path === "/facts") return facts.map((f) => ({ ...f }));
  if (path === "/pairs") {
    return pairs.map((p) => {
      const a = byId(p.a);
      const b = byId(p.b);
      return { ...p, verdict: a && b ? evaluate(a, b).verdict : undefined };
    });
  }
  if (path === "/quarantine") return quarantine.map((q) => ({ ...q }));
  if (path === "/compare") {
    const a = byId(body.a);
    const b = byId(body.b);
    if (!a || !b) throw new Error(`unknown fact in pair ${body.a} / ${body.b}`);
    return evaluate(a, b, body.maskedAxes ?? []);
  }
  if (path === "/documents/upload") {
    const file = (init?.body as FormData).get("file") as File;
    const n = docs.length + 1;
    const id = `D-${String(n).padStart(2, "0")}`;
    // Page count is unknown until the ingest pass reads the file; the mock
    // guesses from size the way a queued row would show an estimate.
    const pages = Math.max(4, Math.round(file.size / 88_000));
    const doc: Doc = {
      id,
      title: file.name.replace(/\.pdf$/i, ""),
      org: "Pending entity resolution",
      pages,
      status: "queued",
      claims: 0,
      grounded: 0,
      quarantined: 0,
      uploaded: new Date().toISOString().slice(0, 10),
      bytes: file.size,
    };
    docs.push(doc);
    started.set(id, Date.now());
    pageBudget.set(id, Math.round(pages * 6.4));
    return { ...doc };
  }
  throw new Error("no route " + path);
}

/* --------------------------------- routes --------------------------------- */

export const api = {
  corpus: () => call<Corpus>("/corpus"),
  documents: () => call<Doc[]>("/documents"),
  facts: () => call<Fact[]>("/facts"),
  pairs: () => call<Pair[]>("/pairs"),
  quarantine: () => call<Quarantined[]>("/quarantine"),
  compare: (a: string, b: string, maskedAxes: AxisKey[] = []) =>
    call<CompareResult>("/compare", {
      method: "POST",
      body: JSON.stringify({ a, b, maskedAxes }),
    }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<Doc>("/documents/upload", { method: "POST", body: form });
  },
};
