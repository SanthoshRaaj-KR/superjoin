// Centralized API layer. Swap MOCK for fetch() against the real service by
// changing BASE and flipping USE_MOCK — every screen goes through these fns.
// Served by the FastAPI app in backend/fkl/api.py, from the same origin, so
// there is no CORS hop and no second port to explain.
//
// The mock below is kept deliberately. It is the contract this UI was built
// against, it documents every field the screens read, and it lets the
// interface be opened with no backend running at all — set USE_MOCK to true.
const BASE = "/api/v1";
const USE_MOCK = false;

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function call(path, init) {
  if (!USE_MOCK) {
    const res = await fetch(BASE + path, {
      headers: { "content-type": "application/json" },
      ...init,
    });
    if (!res.ok) throw new Error(`${res.status} ${path}`);
    return res.json();
  }
  return mock(path, init);
}

/* ---------------------------------- corpus --------------------------------- */

export const DOCUMENTS = [
  { id: "D-01", title: "Annual Report FY2024", org: "Hindmark Industries", pages: 214, status: "indexed", claims: 1842, grounded: 1791, quarantined: 51, uploaded: "2026-08-19", bytes: 18_442_112 },
  { id: "D-02", title: "Standalone Financial Statements FY2024", org: "Hindmark Industries", pages: 96, status: "indexed", claims: 731, grounded: 719, quarantined: 12, uploaded: "2026-08-19", bytes: 7_104_882 },
  { id: "D-03", title: "Q2 FY2025 Investor Presentation", org: "Hindmark Industries", pages: 42, status: "indexed", claims: 288, grounded: 265, quarantined: 23, uploaded: "2026-08-24", bytes: 5_889_301 },
  { id: "D-04", title: "Board & Committee Disclosure 2023-24", org: "Hindmark Industries", pages: 58, status: "indexed", claims: 402, grounded: 396, quarantined: 6, uploaded: "2026-08-24", bytes: 3_220_774 },
  { id: "D-05", title: "Sustainability Report FY2024", org: "Hindmark Industries", pages: 121, status: "extracting", claims: 214, grounded: 198, quarantined: 16, uploaded: "2026-09-04", bytes: 11_003_664, progress: 0.61 },
  { id: "D-06", title: "Annual Report FY2023", org: "Hindmark Industries", pages: 198, status: "queued", claims: 0, grounded: 0, quarantined: 0, uploaded: "2026-09-06", bytes: 16_774_020 },
];

const F = (o) => ({ basis: "audited", kind: "measure", ...o });

export const FACTS = [
  F({ id: "f-1041", entity: "Hindmark Industries", metric: "Revenue from operations", value: 81415, unit: "INR_M", period: "FY2024", scope: "Consolidated", doc: "D-01", page: 118, confidence: 0.96,
    quote: "Revenue from operations for the year ended March 31, 2024 stood at ₹ 81,415 million on a consolidated basis.",
    claim: "Revenue from operations = ₹81,415M · FY2024 · Consolidated", section: "Management Discussion & Analysis" }),
  F({ id: "f-2210", entity: "Hindmark Industries", metric: "Revenue from operations", value: 8142, unit: "INR_CR", period: "FY2024", scope: "Consolidated", doc: "D-03", page: 7, confidence: 0.91,
    quote: "FY24 revenue: ₹ 8,142 Cr (consolidated), up 11.4% year on year.",
    claim: "Revenue from operations = ₹8,142 Cr · FY2024 · Consolidated", section: "Financial highlights" }),
  F({ id: "f-3307", entity: "Hindmark Industries", metric: "Revenue from operations", value: 6908, unit: "INR_CR", period: "FY2024", scope: "Standalone", doc: "D-02", page: 12, confidence: 0.94,
    quote: "Revenue from operations of the Company (standalone) for FY2024 was ₹ 6,908 crore.",
    claim: "Revenue from operations = ₹6,908 Cr · FY2024 · Standalone", section: "Statement of Profit and Loss" }),
  F({ id: "f-1188", entity: "Hindmark Industries", metric: "Profit after tax", value: 1204, unit: "INR_CR", period: "FY2024", scope: "Consolidated", doc: "D-01", page: 121, confidence: 0.95,
    quote: "Profit after tax for FY2024 was ₹ 1,204 crore against ₹ 1,032 crore in the previous year.",
    claim: "Profit after tax = ₹1,204 Cr · FY2024 · Consolidated", section: "Management Discussion & Analysis" }),
  F({ id: "f-2318", entity: "Hindmark Industries", metric: "Profit after tax", value: 1187, unit: "INR_CR", period: "FY2024", scope: "Consolidated", doc: "D-03", page: 9, confidence: 0.72,
    quote: "PAT of ₹ 1,187 Cr for FY24 (consolidated).",
    claim: "Profit after tax = ₹1,187 Cr · FY2024 · Consolidated", section: "Financial highlights" }),
  F({ id: "f-1502", entity: "Hindmark Industries", metric: "Permanent headcount", value: 24118, unit: "COUNT", period: "2024-03-31", scope: "Group", basis: "reported", doc: "D-01", page: 63, confidence: 0.93,
    quote: "As on March 31, 2024, the Group employed 24,118 permanent employees across 14 locations.",
    claim: "Permanent headcount = 24,118 · as of 2024-03-31 · Group", section: "Human Capital" }),
  F({ id: "f-2401", entity: "Hindmark Industries", metric: "Permanent headcount", value: 25640, unit: "COUNT", period: "2024-09-30", scope: "Group", basis: "reported", doc: "D-03", page: 18, confidence: 0.88,
    quote: "Headcount as of September 30, 2024: 25,640 permanent employees.",
    claim: "Permanent headcount = 25,640 · as of 2024-09-30 · Group", section: "Operating metrics" }),
  F({ id: "f-1670", entity: "Hindmark Industries", metric: "EBITDA margin", value: 18.4, unit: "PCT", period: "FY2024", scope: "Consolidated", doc: "D-01", page: 119, confidence: 0.9,
    quote: "EBITDA margin expanded 60 basis points to 18.4% in FY2024.",
    claim: "EBITDA margin = 18.4% · FY2024 · Consolidated", section: "Management Discussion & Analysis" }),
  F({ id: "f-2455", entity: "Hindmark Industries", metric: "EBITDA margin", value: 19.1, unit: "PCT", period: "FY2024", scope: "Consolidated", basis: "unspecified", doc: "D-03", page: 11, confidence: 0.34,
    quote: "", claim: "EBITDA margin = 19.1% · FY2024 · Consolidated", section: "Appendix (chart label, no caption text)", noEvidence: true }),
  F({ id: "f-4012", entity: "Hindmark Industries", metric: "Chief Financial Officer", kind: "tenure", role: "Chief Financial Officer",
    holder: "Anuradha Iyer", start: "2019-04-01", end: "2023-08-14", value: 0, unit: "TENURE", period: "2019-04-01 → 2023-08-14", scope: "Group", basis: "reported", doc: "D-04", page: 14, confidence: 0.97,
    quote: "Ms. Anuradha Iyer ceased to be the Chief Financial Officer of the Company with effect from the close of business hours on August 14, 2023.",
    claim: "CFO = Anuradha Iyer · 2019-04-01 → 2023-08-14", section: "Changes in Key Managerial Personnel" }),
  F({ id: "f-4013", entity: "Hindmark Industries", metric: "Chief Financial Officer", kind: "tenure", role: "Chief Financial Officer",
    holder: "Rohan Deshpande", start: "2023-11-01", end: null, value: 0, unit: "TENURE", period: "2023-11-01 → present", scope: "Group", basis: "reported", doc: "D-04", page: 15, confidence: 0.96,
    quote: "Mr. Rohan Deshpande was appointed as Chief Financial Officer with effect from November 1, 2023.",
    claim: "CFO = Rohan Deshpande · 2023-11-01 → present", section: "Changes in Key Managerial Personnel" }),
  F({ id: "f-4020", entity: "Hindmark Industries", metric: "Managing Director", kind: "tenure", role: "Managing Director",
    holder: "Vikram Sahni", start: "2017-07-01", end: null, value: 0, unit: "TENURE", period: "2017-07-01 → present", scope: "Group", basis: "reported", doc: "D-04", page: 9, confidence: 0.98,
    quote: "Mr. Vikram Sahni continues as Managing Director, re-appointed for a term of five years with effect from July 1, 2022.",
    claim: "Managing Director = Vikram Sahni · 2017-07-01 → present", section: "Board of Directors" }),
  F({ id: "f-4031", entity: "Hindmark Industries", metric: "Company Secretary", kind: "tenure", role: "Company Secretary",
    holder: "Priya Nair", start: "2021-01-11", end: "2024-06-30", value: 0, unit: "TENURE", period: "2021-01-11 → 2024-06-30", scope: "Group", basis: "reported", doc: "D-04", page: 16, confidence: 0.95,
    quote: "Ms. Priya Nair resigned as Company Secretary and Compliance Officer with effect from June 30, 2024.",
    claim: "Company Secretary = Priya Nair · 2021-01-11 → 2024-06-30", section: "Changes in Key Managerial Personnel" }),
  F({ id: "f-4032", entity: "Hindmark Industries", metric: "Company Secretary", kind: "tenure", role: "Company Secretary",
    holder: "Kabir Menon", start: "2024-07-01", end: null, value: 0, unit: "TENURE", period: "2024-07-01 → present", scope: "Group", basis: "reported", doc: "D-04", page: 16, confidence: 0.94,
    quote: "Mr. Kabir Menon was appointed as Company Secretary with effect from July 1, 2024.",
    claim: "Company Secretary = Kabir Menon · 2024-07-01 → present", section: "Changes in Key Managerial Personnel" }),
  F({ id: "f-5101", entity: "Hindmark Industries", metric: "Scope 1 GHG emissions", value: 412300, unit: "TCO2E", period: "FY2024", scope: "Group", basis: "assured", doc: "D-05", page: 44, confidence: 0.86,
    quote: "Scope 1 emissions for FY2024 were 412,300 tCO₂e, independently assured by an external agency.",
    claim: "Scope 1 GHG emissions = 412,300 tCO₂e · FY2024 · Group", section: "Climate disclosures" }),
  F({ id: "f-5109", entity: "Hindmark Industries", metric: "Scope 1 GHG emissions", value: 398900, unit: "TCO2E", period: "FY2024", scope: "Group", basis: "management-estimate", doc: "D-03", page: 31, confidence: 0.69,
    quote: "Scope 1: 398.9 ktCO₂e (FY24, management estimate, pending assurance).",
    claim: "Scope 1 GHG emissions = 398,900 tCO₂e · FY2024 · Group", section: "ESG update" }),
];

export const PAIRS = [
  { id: "r-01", a: "f-1041", b: "f-2210", label: "Revenue FY2024 · report vs deck" },
  { id: "r-02", a: "f-2210", b: "f-3307", label: "Revenue FY2024 · consolidated vs standalone" },
  { id: "r-03", a: "f-1188", b: "f-2318", label: "Profit after tax FY2024 · two sources" },
  { id: "r-04", a: "f-1502", b: "f-2401", label: "Headcount · March vs September" },
  { id: "r-05", a: "f-5101", b: "f-5109", label: "Scope 1 emissions · assured vs estimate" },
  { id: "r-06", a: "f-1670", b: "f-2455", label: "EBITDA margin · report vs chart label" },
  { id: "r-07", a: "f-4012", b: "f-4013", label: "CFO · Iyer → Deshpande" },
];

export const QUARANTINE = [
  { id: "q-118", doc: "D-03", page: 11, reason: "no_evidence_span", claim: "EBITDA margin = 19.1% · FY2024 · Consolidated", note: "Value read from a chart axis label; no sentence-level span found within ±2 pages.", confidence: 0.34 },
  { id: "q-204", doc: "D-01", page: 77, reason: "unresolved_scope", claim: "Order book = ₹12,700 Cr · FY2024", note: "Scope axis unresolved — neither 'consolidated' nor 'standalone' appears in the governing heading.", confidence: 0.41 },
  { id: "q-231", doc: "D-05", page: 52, reason: "unit_ambiguous", claim: "Water withdrawal = 4.2 million · FY2024", note: "Unit token missing; candidates kL and m³ both present in the table header.", confidence: 0.38 },
  { id: "q-266", doc: "D-03", page: 22, reason: "period_ambiguous", claim: "Capex = ₹910 Cr", note: "Period label 'YTD' not bound to a fiscal anchor on the page.", confidence: 0.46 },
  { id: "q-289", doc: "D-01", page: 152, reason: "entity_ambiguous", claim: "Revenue = ₹1,140 Cr · FY2024 · Subsidiary", note: "Subsidiary name resolves to two entities in the corpus registry.", confidence: 0.52 },
  { id: "q-301", doc: "D-05", page: 61, reason: "no_evidence_span", claim: "Renewable share = 38% · FY2024", note: "Extracted from an infographic without adjacent prose.", confidence: 0.29 },
];

export const AXES = [
  { axis: "Consolidation scope", values: ["Consolidated", "Standalone", "Group"], occurrences: 1204, resolves: 38 },
  { axis: "Reporting basis", values: ["audited", "unaudited", "management-estimate", "assured", "reported"], occurrences: 986, resolves: 21 },
  { axis: "Unit system", values: ["INR_M", "INR_CR", "USD_M", "PCT", "TCO2E"], occurrences: 2311, resolves: 44 },
  { axis: "Period anchor", values: ["FY", "as-of date", "quarter", "YTD"], occurrences: 1877, resolves: 19 },
  { axis: "Restatement", values: ["as-reported", "restated"], occurrences: 142, resolves: 7 },
];

/* ------------------------------ verdict engine ------------------------------ */
// Runs server-side in production; the counterfactual endpoint calls the same
// routine with the masked axes removed from the reasoning set.

const CANON = { INR_M: ["INR", 0.1], INR_CR: ["INR", 1], USD_M: ["USD", 1], PCT: ["PCT", 1], COUNT: ["COUNT", 1], TCO2E: ["TCO2E", 1], TENURE: ["TENURE", 1] };
const AXIS_LABEL = { entity: "Entity", metric: "Metric", period: "Period", unit: "Unit", scope: "Consolidation scope", basis: "Reporting basis" };

function normalize(f) {
  const [dim, mul] = CANON[f.unit] || ["?", 1];
  return { dim, value: f.value * mul, unit: dim === "INR" ? "INR_CR" : f.unit };
}

function evaluate(a, b, masked) {
  const m = new Set(masked || []);
  const na = normalize(a), nb = normalize(b);
  const axes = ["entity", "metric", "period", "unit", "scope", "basis"].map((key) => {
    let status;
    if (key === "unit") status = a.unit === b.unit ? "match" : na.dim === nb.dim ? "normalized" : "differs";
    else status = (a[key] || "") === (b[key] || "") ? "match" : "differs";
    if (m.has(key)) status = "masked";
    return { key, label: AXIS_LABEL[key], status, a: key === "unit" ? a.unit : a[key], b: key === "unit" ? b.unit : b[key] };
  });

  const differing = axes.filter((x) => x.status === "differs");

  if (a.kind === "tenure" && b.kind === "tenure" && a.role === b.role) {
    const [first, second] = new Date(a.start) <= new Date(b.start) ? [a, b] : [b, a];
    const gap = first.end ? Math.round((new Date(second.start) - new Date(first.end)) / 864e5) : null;
    return {
      verdict: "SUCCESSION",
      reason: gap > 1
        ? `${first.holder} vacated the ${first.role} office on ${first.end}; ${second.holder} took office on ${second.start}. The office was vacant for ${gap - 1} days.`
        : `${second.holder} succeeded ${first.holder} as ${first.role} with no recorded vacancy.`,
      axes, vacancyDays: gap ? gap - 1 : 0, differingAxis: null,
      normalized: { a: null, b: null },
    };
  }

  if (a.noEvidence || b.noEvidence) {
    const which = a.noEvidence ? "A" : "B";
    return {
      verdict: "INSUFFICIENT_EVIDENCE",
      reason: `Fact ${which} carries no sentence-level evidence span. Comparison is withheld until the claim is grounded; the value was recovered from a chart label and is quarantined.`,
      axes, differingAxis: null, normalized: { a: na, b: nb },
    };
  }

  const agree = Math.abs(na.value - nb.value) <= Math.max(Math.abs(na.value), 1) * 0.005 && na.dim === nb.dim;

  if (agree) {
    const norm = axes.find((x) => x.status === "normalized");
    return {
      verdict: "CORROBORATES",
      reason: norm
        ? `Values agree to within 0.5% once ${a.unit} is normalized to ${nb.unit}. All context axes match, so the two spans are independent attestations of the same fact.`
        : "Values agree to within 0.5% and every context axis matches. The two spans are independent attestations of the same fact.",
      axes, differingAxis: null, normalized: { a: na, b: nb },
    };
  }

  if (differing.length) {
    const ax = differing[0];
    return {
      verdict: "CONTEXTUAL",
      reason: `The values differ, but so does the ${ax.label.toLowerCase()} axis (${ax.a} vs ${ax.b}). The difference is explained by context, not by a factual conflict.`,
      axes, differingAxis: ax, normalized: { a: na, b: nb },
    };
  }

  const maskedAxes = axes.filter((x) => x.status === "masked");
  return {
    verdict: "CONTRADICTS",
    reason: maskedAxes.length
      ? `With ${maskedAxes.map((x) => x.label.toLowerCase()).join(" and ")} held out of the reasoning set, no context axis explains the gap of ${Math.abs(na.value - nb.value).toLocaleString("en-IN", { maximumFractionDigits: 1 })} ${na.unit}. The two spans conflict.`
      : `Every context axis matches, yet the values differ by ${Math.abs(na.value - nb.value).toLocaleString("en-IN", { maximumFractionDigits: 1 })} ${na.unit}. No contextual explanation is available; the spans genuinely conflict.`,
    axes, differingAxis: null, normalized: { a: na, b: nb },
  };
}

/* ---------------------------------- routes --------------------------------- */

async function mock(path, init) {
  await wait(140);
  const body = init && init.body ? JSON.parse(init.body) : null;
  if (path === "/corpus") {
    const docs = DOCUMENTS;
    const claims = docs.reduce((s, d) => s + d.claims, 0);
    const quarantined = docs.reduce((s, d) => s + d.quarantined, 0);
    const results = PAIRS.map((p) => evaluate(byId(p.a), byId(p.b), []).verdict);
    return {
      documents: docs.length,
      pages: docs.reduce((s, d) => s + d.pages, 0),
      claims,
      grounded: claims - quarantined,
      quarantined,
      relationships: PAIRS.length,
      raw: results.filter((v) => v !== "CORROBORATES").length,
      contextual: results.filter((v) => v === "CONTEXTUAL").length,
      contradictions: results.filter((v) => v === "CONTRADICTS").length,
      insufficient: results.filter((v) => v === "INSUFFICIENT_EVIDENCE").length,
      confidence: [
        { band: "0.90 – 1.00", count: 2418 },
        { band: "0.75 – 0.90", count: 812 },
        { band: "0.60 – 0.75", count: 197 },
        { band: "< 0.60", count: 108 },
      ],
      axes: AXES,
    };
  }
  if (path === "/documents") return DOCUMENTS;
  if (path === "/facts") return FACTS;
  if (path === "/pairs") return PAIRS.map((p) => ({ ...p, verdict: evaluate(byId(p.a), byId(p.b), []).verdict }));
  if (path === "/quarantine") return QUARANTINE;
  if (path === "/compare") return evaluate(byId(body.a), byId(body.b), body.maskedAxes);
  throw new Error("no route " + path);
}

export const byId = (id) => FACTS.find((f) => f.id === id);

export const api = {
  corpus: () => call("/corpus"),
  documents: () => call("/documents"),
  facts: () => call("/facts"),
  pairs: () => call("/pairs"),
  quarantine: () => call("/quarantine"),
  compare: (a, b, maskedAxes) =>
    call("/compare", { method: "POST", body: JSON.stringify({ a, b, maskedAxes }) }),
};

export function fmt(f) {
  if (f.kind === "tenure") return f.holder;
  const n = f.value;
  switch (f.unit) {
    case "INR_M": return "₹" + n.toLocaleString("en-IN") + "M";
    case "INR_CR": return "₹" + n.toLocaleString("en-IN") + " Cr";
    case "USD_M": return "$" + n.toLocaleString("en-US") + "M";
    case "USD_BN": return "$" + n.toLocaleString("en-US") + "Bn";
    case "PCT": return n + "%";
    case "PP": return n + " pp";
    case "TCO2E": return n.toLocaleString("en-IN") + " tCO₂e";
    case "UNKNOWN": return n.toLocaleString("en-IN") + " (unit not stated)";
    // The unit vocabulary is open, because the corpus is. A dimension the UI
    // has no special formatting for is still shown with its unit rather than
    // as a bare number, which would silently turn tonnes into rupees.
    default: return n.toLocaleString("en-IN") + " " + String(f.unit).toLowerCase().replace(/_/g, " ");
  }
}
