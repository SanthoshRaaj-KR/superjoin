import type { AxisStatus, Fact, Verdict } from "./types";

export const VERDICT: Record<Verdict, { bg: string; fg: string; edge: string; dark: string; tag: string }> = {
  CORROBORATES: { bg: "#E7EFE9", fg: "#1F4D36", edge: "#C3DACB", dark: "#7FB894", tag: "AGREEMENT" },
  CONTRADICTS: { bg: "#F7E5E2", fg: "#93231A", edge: "#E6C4BE", dark: "#E08A7E", tag: "CONFLICT" },
  CONTEXTUAL: { bg: "#F6EBD4", fg: "#7A5410", edge: "#E0CDA1", dark: "#DDB463", tag: "EXPLAINED BY CONTEXT" },
  INSUFFICIENT_EVIDENCE: { bg: "#E9E8E2", fg: "#4A4F47", edge: "#D6D4CB", dark: "#A2A79A", tag: "WITHHELD" },
  SUCCESSION: { bg: "#E4E9F5", fg: "#2C3E75", edge: "#C6D0E6", dark: "#8FA2CE", tag: "TEMPORAL" },
};

export const AXIS_STATUS: Record<AxisStatus, { mark: string; fg: string; bg: string }> = {
  match: { mark: "✓", fg: "#1F4D36", bg: "#E7EFE9" },
  normalized: { mark: "normalized", fg: "#7A5410", bg: "#F6EBD4" },
  differs: { mark: "differs", fg: "#93231A", bg: "#F7E5E2" },
  masked: { mark: "masked", fg: "#4A4F47", bg: "#E9E8E2" },
};

export const DOC_STATUS: Record<string, { bg: string; fg: string }> = {
  indexed: { bg: "#E7EFE9", fg: "#1F4D36" },
  extracting: { bg: "#F6EBD4", fg: "#7A5410" },
  queued: { bg: "#E9E8E2", fg: "#4A4F47" },
  failed: { bg: "#F7E5E2", fg: "#93231A" },
};

/** Render a fact at the precision its unit was reported in. */
export function fmt(f: Fact): string {
  if (f.kind === "tenure") return f.holder ?? "—";
  const n = f.value;
  switch (f.unit) {
    case "INR_M":
      return "₹" + n.toLocaleString("en-IN") + "M";
    case "INR_CR":
      return "₹" + n.toLocaleString("en-IN") + " Cr";
    case "USD_M":
      return "$" + n.toLocaleString("en-US") + "M";
    case "PCT":
      return n + "%";
    case "TCO2E":
      return n.toLocaleString("en-IN") + " tCO₂e";
    default:
      return n.toLocaleString("en-IN");
  }
}

export const int = (n: number) => n.toLocaleString("en-IN");
export const mb = (bytes: number) => (bytes / 1_048_576).toFixed(1) + " MB";

export const confColour = (c: number) => (c < 0.6 ? "#93231A" : c < 0.85 ? "#7A5410" : "#1F4D36");
