import { useStore } from "../store";

export default function Quality() {
  const { corpus, quarantine } = useStore();
  const c = corpus;
  const total = c?.claims || 1;
  const groundPct = c ? (c.grounded / total) * 100 : 0;
  const quarPct = c ? (c.quarantined / total) * 100 : 0;
  const groundRate = c ? Math.round(groundPct) + "%" : "—";

  const conf = c?.confidence ?? [];
  const cmax = Math.max(1, ...conf.map((x) => x.count));
  const bandColour = ["#1F4D36", "#3E7A55", "#C6A34B", "#93231A"];

  return (
    <>
      <div className="grid auto-300" style={{ alignItems: "start" }}>
        <div className="panel pad">
          <div className="eyebrow">GROUNDING</div>
          <div style={{ marginTop: 12, display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ font: "500 32px/1 var(--mono)", letterSpacing: "-0.02em", color: "var(--green)" }}>{groundRate}</span>
            <span className="muted">of claims carry a sentence-level evidence span</span>
          </div>
          <div style={{ marginTop: 14, height: 10, display: "flex", borderRadius: 2, overflow: "hidden", background: "var(--wash)" }}>
            <div style={{ width: `${groundPct}%`, background: "var(--green)" }} />
            <div style={{ width: `${quarPct}%`, background: "var(--amber)" }} />
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 16, font: "400 11px/1 var(--mono)", color: "var(--ink-3)", flexWrap: "wrap" }}>
            <span>{c?.grounded ?? 0} GROUNDED</span>
            <span>{c?.quarantined ?? 0} QUARANTINED</span>
          </div>
        </div>

        <div className="panel pad">
          <div className="eyebrow">CONFIDENCE BREAKDOWN</div>
          <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 9 }}>
            {conf.map((x, i) => (
              <div key={x.band} style={{ display: "grid", gridTemplateColumns: "84px minmax(0,1fr) 52px", gap: 10, alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>{x.band}</span>
                <div className="bar">
                  <i style={{ width: `${(x.count / cmax) * 100}%`, background: bandColour[i] ?? "var(--ink-3)" }} />
                </div>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-1)", textAlign: "right" }}>{x.count.toLocaleString("en-IN")}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel flush" style={{ marginTop: 16 }}>
        <div className="panel-head"><span className="eyebrow">DISCOVERED CONTEXTUAL AXES</span></div>
        {(c?.axes ?? []).map((a) => (
          <div key={a.axis} className="row" style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto auto", gap: 14, padding: "11px 14px", alignItems: "center" }}>
            <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-1)" }}>{a.axis}</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>{a.values.join(" · ")}</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--muted-2)", whiteSpace: "nowrap" }}>{a.occurrences.toLocaleString("en-IN")} spans</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--green)", whiteSpace: "nowrap" }}>{a.resolves} resolutions</div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>QUARANTINED CLAIMS · {quarantine.length}</div>
        <div className="grid auto-300">
          {quarantine.map((q) => (
            <div key={q.id} className="panel" style={{ borderLeft: "2px solid var(--amber)", padding: "14px 16px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span className="badge" style={{ background: "var(--amber-wash)", color: "var(--amber-fg)" }}>
                  {q.reason.replace(/_/g, " ").toUpperCase()}
                </span>
                <span className="ref">{q.doc} · p.{q.page}</span>
              </div>
              <div style={{ marginTop: 11, font: "500 12.5px/1.45 var(--mono)", color: "var(--ink-1)" }}>{q.claim}</div>
              <div className="pretty" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.55, color: "var(--ink-3)" }}>{q.note}</div>
              <div style={{ marginTop: 10, font: "400 10.5px/1 var(--mono)", color: "var(--muted-1)" }}>
                CONFIDENCE {q.confidence.toFixed(2)} · WITHHELD FROM COMPARISON
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
