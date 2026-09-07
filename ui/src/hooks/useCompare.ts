import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { AxisKey, CompareResult } from "../api/types";

interface UseCompare {
  result: CompareResult | null;
  baseline: CompareResult | null;
  busy: boolean;
  masked: AxisKey[];
  toggleMask: (key: AxisKey) => void;
}

/** Evaluates a pair through the same /compare route the backend will serve,
 *  re-running whenever the pair or the masked-axis set changes. `baseline` is
 *  the unmasked verdict, fetched alongside so the counterfactual chain can
 *  show what changed. */
export function useCompare(a: string | undefined, b: string | undefined): UseCompare {
  const [masked, setMasked] = useState<AxisKey[]>([]);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [baseline, setBaseline] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMasked([]);
  }, [a, b]);

  useEffect(() => {
    if (!a || !b) {
      setResult(null);
      setBaseline(null);
      return;
    }
    let live = true;
    setBusy(true);
    Promise.all([api.compare(a, b, masked), masked.length ? api.compare(a, b, []) : Promise.resolve(null)])
      .then(([r, base]) => {
        if (!live) return;
        setResult(r);
        setBaseline(masked.length ? base : r);
      })
      .catch(() => {
        if (live) {
          setResult(null);
          setBaseline(null);
        }
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [a, b, masked]);

  const toggleMask = (key: AxisKey) =>
    setMasked((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : cur.concat(key)));

  return { result, baseline, busy, masked, toggleMask };
}
