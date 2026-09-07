import { useMemo, useState } from "react";

import { confColour, fmt } from "../api/format";
import type { Fact } from "../api/types";
import { useStore } from "../store";

function uniq<T, K extends keyof T>(rows: T[], key: K) {
  return Array.from(new Set(rows.map((r) => String(r[key])))).sort();
}

export default function Facts() {
  const { facts, pairs, docFilter, setDocFilter, openEvidence, select } = useStore();
  const [query, setQuery] = useState("");
  const [entity, setEntity] = useState("all");
  const [metric, setMetric] = useState("all");
  const [period, setPeriod] = useState("all");
  const [factId, setFactId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return facts.filter(
      (f) =>
        (entity === "all" || f.entity === entity) &&
        (metric === "all" || f.metric === metric) &&
        (period === "all" || f.period === period) &&
        (docFilter === "all" || f.doc === docFilter) &&
        (!q || `${f.metric} ${f.claim} ${f.quote} ${f.id}`.toLowerCase().includes(q)),
    );
  }, [facts, query, entity, metric, period, docFilter]);

  const passport: Fact | undefined = factById(filtered, factId) ?? filtered[0];

  const linkedPair = passport ? pairs.find((p) => p.a === passport.id || p.b === passport.id) : undefined;

  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <input
          className="field"
          style={{ flex: 1, minWidth: 200 }}
          placeholder="Search metric, value, quote…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select className="field" value={entity} onChange={(e) => setEntity(e.target.value)}>
          <option value="all">All entities</option>
          {uniq(facts, "entity").map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <select className="field" value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="all">All metrics</option>
          {uniq(facts, "metric").map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <select className="field" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="all">All periods</option>
          {uniq(facts, "period").map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <select className="field" value={docFilter} onChange={(e) => setDocFilter(e.target.value)}>
          <option value="all">All documents</option>
          {uniq(facts, "doc").map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
        <div className="panel flush" style={{ flex: "1 1 480px", minWidth: 0 }}>
          <div
            style={{ display: "grid", gridTemplateColumns: "1.7fr 0.9fr 0.7fr 0.9fr auto", gap: 12, padding: "9px 14px", borderBottom: "1px solid var(--rule-mid)" }}
            className="eyebrow"
          >
            <div>METRIC</div><div>VALUE</div><div>PERIOD</div><div>SCOPE</div><div>CONF</div>
          </div>
          {filtered.length === 0 && <div className="empty">No facts match this filter.</div>}
          {filtered.map((f) => (
            <button
              key={f.id}
              className="row click"
              style={{
                display: "grid",
                gridTemplateColumns: "1.7fr 0.9fr 0.7fr 0.9fr auto",
                gap: 12,
                padding: "10px 14px",
                alignItems: "baseline",
                background: f.id === passport?.id ? "var(--head)" : "var(--panel)",
              }}
              onClick={() => setFactId(f.id)}
            >
              <div>
                <div style={{ fontSize: 12.5, color: "var(--ink-1)", fontWeight: 500 }}>{f.metric}</div>
                <div className="ref" style={{ marginTop: 2 }}>{f.id} · {f.doc} p.{f.page}</div>
              </div>
              <div className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{fmt(f)}</div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{f.period}</div>
              <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{f.scope}</div>
              <div className="mono" style={{ fontSize: 11.5, color: confColour(f.confidence) }}>{f.confidence.toFixed(2)}</div>
            </button>
          ))}
          <div className="ref" style={{ padding: "10px 14px" }}>{filtered.length} of {facts.length} facts</div>
        </div>

        <div
          className="panel flush"
          style={{ flex: "1 1 300px", maxWidth: 340, minWidth: 272, position: "sticky", top: 104 }}
        >
          <div className="panel-head">
            <span className="eyebrow">FACT PASSPORT</span>
            <span className="ref">{passport?.id ?? ""}</span>
          </div>
          {!passport ? (
            <div className="empty">Select a fact to inspect its full record.</div>
          ) : (
            <div style={{ padding: "16px 16px 18px" }}>
              <div style={{ font: "500 24px/1.15 var(--mono)", letterSpacing: "-0.02em" }}>{fmt(passport)}</div>
              <div style={{ marginTop: 5, fontSize: 13, color: "var(--ink-2)" }}>{passport.metric}</div>
              <dl className="kv" style={{ marginTop: 14 }}>
                <dt>Entity</dt><dd>{passport.entity}</dd>
                <dt>Metric</dt><dd>{passport.metric}</dd>
                <dt>Value</dt><dd className="mono">{fmt(passport)}</dd>
                <dt>Unit</dt><dd className="mono">{passport.unit}</dd>
                <dt>Period</dt><dd className="mono">{passport.period}</dd>
                <dt>Scope</dt><dd>{passport.scope}</dd>
                <dt>Basis</dt><dd>{passport.basis}</dd>
                <dt>Source</dt><dd>{passport.doc}</dd>
                <dt>Page</dt><dd className="mono">{passport.page}</dd>
                <dt>Confidence</dt><dd className="mono">{passport.confidence.toFixed(2)}</dd>
              </dl>
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--rule-hair)" }}>
                <div className="eyebrow">EVIDENCE SPAN</div>
                <div className="pretty" style={{ marginTop: 9, fontSize: 12.5, lineHeight: 1.6, color: "var(--ink-1)" }}>
                  <span className="span-mark">
                    {passport.quote || "No evidence span — claim is quarantined."}
                  </span>
                </div>
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn go" onClick={() => openEvidence(passport.id)}>SOURCE PAGE →</button>
                <button
                  className="btn"
                  onClick={() => {
                    if (linkedPair) select({ id: linkedPair.id, a: linkedPair.a, b: linkedPair.b, label: linkedPair.label });
                  }}
                  disabled={!linkedPair}
                >
                  COMPARE
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function factById(rows: Fact[], id: string | null) {
  return id ? rows.find((f) => f.id === id) : undefined;
}
