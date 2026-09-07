// Seed corpus for the mock service. Carried over from the design canvas so the
// four cases the assignment asks for are all reachable from a cold start:
//
//   corroborated across documents   r-01  (INR_M vs INR_CR, same fact)
//   explained by context            r-02  (consolidated vs standalone)
//   genuine contradiction           r-03  (every axis matches, values differ)
//   extraction failure, handled     r-06  (chart label with no span, quarantined)
//
// Nothing downstream reads these directly — everything goes through api.*, so
// pointing VITE_API_BASE at the real service replaces this file wholesale.

import type { AxisSummary, Doc, Fact, Pair, Quarantined } from "./types";

export const DOCUMENTS: Doc[] = [
  { id: "D-01", title: "Annual Report FY2024", org: "Hindmark Industries", pages: 214, status: "indexed", claims: 1842, grounded: 1791, quarantined: 51, uploaded: "2026-08-19", bytes: 18_442_112 },
  { id: "D-02", title: "Standalone Financial Statements FY2024", org: "Hindmark Industries", pages: 96, status: "indexed", claims: 731, grounded: 719, quarantined: 12, uploaded: "2026-08-19", bytes: 7_104_882 },
  { id: "D-03", title: "Q2 FY2025 Investor Presentation", org: "Hindmark Industries", pages: 42, status: "indexed", claims: 288, grounded: 265, quarantined: 23, uploaded: "2026-08-24", bytes: 5_889_301 },
  { id: "D-04", title: "Board & Committee Disclosure 2023-24", org: "Hindmark Industries", pages: 58, status: "indexed", claims: 402, grounded: 396, quarantined: 6, uploaded: "2026-08-24", bytes: 3_220_774 },
  { id: "D-05", title: "Sustainability Report FY2024", org: "Hindmark Industries", pages: 121, status: "extracting", claims: 214, grounded: 198, quarantined: 16, uploaded: "2026-09-04", bytes: 11_003_664, progress: 0.61 },
  { id: "D-06", title: "Annual Report FY2023", org: "Hindmark Industries", pages: 198, status: "queued", claims: 0, grounded: 0, quarantined: 0, uploaded: "2026-09-06", bytes: 16_774_020 },
];

const F = (o: Partial<Fact> & Pick<Fact, "id" | "entity" | "metric" | "doc" | "page" | "section" | "confidence" | "claim">): Fact =>
  ({
    basis: "audited",
    kind: "measure",
    value: 0,
    unit: "COUNT",
    period: "",
    scope: "",
    quote: "",
    ...o,
  }) as Fact;

export const FACTS: Fact[] = [
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

export const PAIRS: Pair[] = [
  { id: "r-01", a: "f-1041", b: "f-2210", label: "Revenue FY2024 · report vs deck" },
  { id: "r-02", a: "f-2210", b: "f-3307", label: "Revenue FY2024 · consolidated vs standalone" },
  { id: "r-03", a: "f-1188", b: "f-2318", label: "Profit after tax FY2024 · two sources" },
  { id: "r-04", a: "f-1502", b: "f-2401", label: "Headcount · March vs September" },
  { id: "r-05", a: "f-5101", b: "f-5109", label: "Scope 1 emissions · assured vs estimate" },
  { id: "r-06", a: "f-1670", b: "f-2455", label: "EBITDA margin · report vs chart label" },
  { id: "r-07", a: "f-4012", b: "f-4013", label: "CFO · Iyer → Deshpande" },
];

export const QUARANTINE: Quarantined[] = [
  { id: "q-118", doc: "D-03", page: 11, reason: "no_evidence_span", claim: "EBITDA margin = 19.1% · FY2024 · Consolidated", note: "Value read from a chart axis label; no sentence-level span found within ±2 pages.", confidence: 0.34 },
  { id: "q-204", doc: "D-01", page: 77, reason: "unresolved_scope", claim: "Order book = ₹12,700 Cr · FY2024", note: "Scope axis unresolved — neither 'consolidated' nor 'standalone' appears in the governing heading.", confidence: 0.41 },
  { id: "q-231", doc: "D-05", page: 52, reason: "unit_ambiguous", claim: "Water withdrawal = 4.2 million · FY2024", note: "Unit token missing; candidates kL and m³ both present in the table header.", confidence: 0.38 },
  { id: "q-266", doc: "D-03", page: 22, reason: "period_ambiguous", claim: "Capex = ₹910 Cr", note: "Period label 'YTD' not bound to a fiscal anchor on the page.", confidence: 0.46 },
  { id: "q-289", doc: "D-01", page: 152, reason: "entity_ambiguous", claim: "Revenue = ₹1,140 Cr · FY2024 · Subsidiary", note: "Subsidiary name resolves to two entities in the corpus registry.", confidence: 0.52 },
  { id: "q-301", doc: "D-05", page: 61, reason: "no_evidence_span", claim: "Renewable share = 38% · FY2024", note: "Extracted from an infographic without adjacent prose.", confidence: 0.29 },
];

export const AXES: AxisSummary[] = [
  { axis: "Consolidation scope", values: ["Consolidated", "Standalone", "Group"], occurrences: 1204, resolves: 38 },
  { axis: "Reporting basis", values: ["audited", "unaudited", "management-estimate", "assured", "reported"], occurrences: 986, resolves: 21 },
  { axis: "Unit system", values: ["INR_M", "INR_CR", "USD_M", "PCT", "TCO2E"], occurrences: 2311, resolves: 44 },
  { axis: "Period anchor", values: ["FY", "as-of date", "quarter", "YTD"], occurrences: 1877, resolves: 19 },
  { axis: "Restatement", values: ["as-reported", "restated"], occurrences: 142, resolves: 7 },
];

export const CONFIDENCE_BANDS = [
  { band: "0.90 – 1.00", count: 2418 },
  { band: "0.75 – 0.90", count: 812 },
  { band: "0.60 – 0.75", count: 197 },
  { band: "< 0.60", count: 108 },
];
