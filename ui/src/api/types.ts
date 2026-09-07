// The wire shapes. These mirror what the backend exports today (fkl.export)
// closely enough that swapping the mock for the service is a base-URL change.

export type Unit =
  | "INR_M"
  | "INR_CR"
  | "USD_M"
  | "PCT"
  | "COUNT"
  | "TCO2E"
  | "TENURE";

export type DocStatus = "indexed" | "extracting" | "queued" | "failed";

export interface Doc {
  id: string;
  title: string;
  org: string;
  pages: number;
  status: DocStatus;
  claims: number;
  grounded: number;
  quarantined: number;
  uploaded: string;
  bytes: number;
  /** 0–1, present while status is "extracting". */
  progress?: number;
}

export interface Fact {
  id: string;
  entity: string;
  metric: string;
  value: number;
  unit: Unit;
  period: string;
  scope: string;
  basis: string;
  doc: string;
  page: number;
  section: string;
  confidence: number;
  claim: string;
  quote: string;
  kind: "measure" | "tenure";
  /** Set when no sentence-level span backed the value; withheld from comparison. */
  noEvidence?: boolean;
  // tenure-only
  role?: string;
  holder?: string;
  start?: string;
  end?: string | null;
}

export type Verdict =
  | "CORROBORATES"
  | "CONTRADICTS"
  | "CONTEXTUAL"
  | "INSUFFICIENT_EVIDENCE"
  | "SUCCESSION";

export interface Pair {
  id: string;
  a: string;
  b: string;
  label: string;
  verdict?: Verdict;
}

export type AxisKey = "entity" | "metric" | "period" | "unit" | "scope" | "basis";
export type AxisStatus = "match" | "normalized" | "differs" | "masked";

export interface AxisReading {
  key: AxisKey;
  label: string;
  status: AxisStatus;
  a: string;
  b: string;
}

export interface Normalized {
  dim: string;
  value: number;
  unit: string;
}

export interface CompareResult {
  verdict: Verdict;
  reason: string;
  axes: AxisReading[];
  differingAxis: AxisReading | null;
  normalized: { a: Normalized | null; b: Normalized | null };
  vacancyDays?: number;
}

export interface Quarantined {
  id: string;
  doc: string;
  page: number;
  reason: string;
  claim: string;
  note: string;
  confidence: number;
}

export interface AxisSummary {
  axis: string;
  values: string[];
  occurrences: number;
  resolves: number;
}

export interface Corpus {
  documents: number;
  pages: number;
  claims: number;
  grounded: number;
  quarantined: number;
  relationships: number;
  raw: number;
  contextual: number;
  contradictions: number;
  insufficient: number;
  confidence: { band: string; count: number }[];
  axes: AxisSummary[];
}
