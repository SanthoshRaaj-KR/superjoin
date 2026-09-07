// The comparability gate, client side.
//
// In production this runs in the backend and /compare is a POST; the routine is
// kept here so the mock service answers with the same reasoning rather than a
// canned verdict, and so the counterfactual (mask an axis, ask again) is a real
// re-evaluation instead of a lookup.

import type { AxisKey, AxisReading, CompareResult, Fact, Normalized, Unit } from "./types";

const CANON: Record<Unit, [string, number]> = {
  INR_M: ["INR", 0.1],
  INR_CR: ["INR", 1],
  USD_M: ["USD", 1],
  PCT: ["PCT", 1],
  COUNT: ["COUNT", 1],
  TCO2E: ["TCO2E", 1],
  TENURE: ["TENURE", 1],
};

export const AXIS_LABEL: Record<AxisKey, string> = {
  entity: "Entity",
  metric: "Metric",
  period: "Period",
  unit: "Unit",
  scope: "Consolidation scope",
  basis: "Reporting basis",
};

const AXIS_ORDER: AxisKey[] = ["entity", "metric", "period", "unit", "scope", "basis"];

/** Axes a reader is allowed to hold out of the reasoning set. */
export const MASKABLE: AxisKey[] = ["scope", "basis", "period", "unit"];

export function normalize(f: Fact): Normalized {
  const [dim, mul] = CANON[f.unit] ?? ["?", 1];
  return { dim, value: f.value * mul, unit: dim === "INR" ? "INR_CR" : f.unit };
}

const day = 864e5;
const days = (from: string, to: string) =>
  Math.round((new Date(to).getTime() - new Date(from).getTime()) / day);

export function evaluate(a: Fact, b: Fact, masked: AxisKey[] = []): CompareResult {
  const held = new Set(masked);
  const na = normalize(a);
  const nb = normalize(b);

  const axes: AxisReading[] = AXIS_ORDER.map((key) => {
    let status: AxisReading["status"];
    if (key === "unit") {
      status = a.unit === b.unit ? "match" : na.dim === nb.dim ? "normalized" : "differs";
    } else {
      status = (a[key] ?? "") === (b[key] ?? "") ? "match" : "differs";
    }
    if (held.has(key)) status = "masked";
    return {
      key,
      label: AXIS_LABEL[key],
      status,
      a: key === "unit" ? a.unit : String(a[key] ?? ""),
      b: key === "unit" ? b.unit : String(b[key] ?? ""),
    };
  });

  const differing = axes.filter((x) => x.status === "differs");

  // Intervals are not measurements. Two spans on one seat are a succession
  // question, and the gap between them is the answer worth reporting.
  if (a.kind === "tenure" && b.kind === "tenure" && a.role === b.role) {
    const [first, second] =
      new Date(a.start ?? 0).getTime() <= new Date(b.start ?? 0).getTime() ? [a, b] : [b, a];
    const gap = first.end && second.start ? days(first.end, second.start) : null;
    const vacant = gap && gap > 1 ? gap - 1 : 0;
    return {
      verdict: "SUCCESSION",
      reason: vacant
        ? `${first.holder} vacated the ${first.role} office on ${first.end}; ${second.holder} took office on ${second.start}. The office was vacant for ${vacant} days.`
        : `${second.holder} succeeded ${first.holder} as ${first.role} with no recorded vacancy.`,
      axes,
      vacancyDays: vacant,
      differingAxis: null,
      normalized: { a: null, b: null },
    };
  }

  if (a.noEvidence || b.noEvidence) {
    const which = a.noEvidence ? "A" : "B";
    return {
      verdict: "INSUFFICIENT_EVIDENCE",
      reason: `Fact ${which} carries no sentence-level evidence span. Comparison is withheld until the claim is grounded; the value was recovered from a chart label and is quarantined.`,
      axes,
      differingAxis: null,
      normalized: { a: na, b: nb },
    };
  }

  const agree =
    na.dim === nb.dim &&
    Math.abs(na.value - nb.value) <= Math.max(Math.abs(na.value), 1) * 0.005;

  if (agree) {
    const norm = axes.find((x) => x.status === "normalized");
    return {
      verdict: "CORROBORATES",
      reason: norm
        ? `Values agree to within 0.5% once ${a.unit} is normalized to ${nb.unit}. All context axes match, so the two spans are independent attestations of the same fact.`
        : "Values agree to within 0.5% and every context axis matches. The two spans are independent attestations of the same fact.",
      axes,
      differingAxis: null,
      normalized: { a: na, b: nb },
    };
  }

  if (differing.length) {
    const ax = differing[0];
    return {
      verdict: "CONTEXTUAL",
      reason: `The values differ, but so does the ${ax.label.toLowerCase()} axis (${ax.a} vs ${ax.b}). The difference is explained by context, not by a factual conflict.`,
      axes,
      differingAxis: ax,
      normalized: { a: na, b: nb },
    };
  }

  const gapText = Math.abs(na.value - nb.value).toLocaleString("en-IN", {
    maximumFractionDigits: 1,
  });
  const heldAxes = axes.filter((x) => x.status === "masked");
  return {
    verdict: "CONTRADICTS",
    reason: heldAxes.length
      ? `With ${heldAxes
          .map((x) => x.label.toLowerCase())
          .join(" and ")} held out of the reasoning set, no context axis explains the gap of ${gapText} ${na.unit}. The two spans conflict.`
      : `Every context axis matches, yet the values differ by ${gapText} ${na.unit}. No contextual explanation is available; the spans genuinely conflict.`,
    axes,
    differingAxis: null,
    normalized: { a: na, b: nb },
  };
}
