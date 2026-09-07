import { VERDICT, int } from "../api/format";
import { useStore } from "../store";

export default function Overview() {
  const { corpus, pairs, select } = useStore();
  const c = corpus;

  const tiles = [
    { label: "DOCUMENTS", value: int(c?.documents ?? 0), note: `${int(c?.pages ?? 0)} pages ingested`, fg: "var(--ink)" },
    { label: "CLAIMS EXTRACTED", value: int(c?.claims ?? 0), note: "sentence-level candidates", fg: "var(--ink)" },
    { label: "GROUNDED", value: int(c?.grounded ?? 0), note: "carry an evidence span", fg: "var(--green)" },
    { label: "QUARANTINED", value: int(c?.quarantined ?? 0), note: "withheld from comparison", fg: "var(--amber-fg)" },
    { label: "RELATIONSHIPS", value: int(c?.relationships ?? 0), note: "fact pairs reconciled", fg: "var(--ink)" },
    { label: "CONTRADICTIONS", value: int(c?.contradictions ?? 0), note: "unexplained by any axis", fg: "var(--red)" },
  ];

  const raw = c?.raw ?? 0;
  const funnel = [
    { label: "Raw disagreements", value: raw, pct: 100, fg: "var(--ink-3)", note: "pairs whose values did not match on first pass" },
    { label: "Contextual resolutions", value: c?.contextual ?? 0, pct: raw ? ((c?.contextual ?? 0) / raw) * 100 : 0, fg: "var(--amber-fg)", note: "explained by a differing context axis" },
    { label: "Genuine contradictions", value: c?.contradictions ?? 0, pct: raw ? ((c?.contradictions ?? 0) / raw) * 100 : 0, fg: "var(--red)", note: "all axes matched, values still conflict" },
  ];

  return (
    <>
      <div className="grid auto-158">
        {tiles.map((t) => (
          <div key={t.label} className="panel" style={{ padding: "15px 16px" }}>
            <div className="eyebrow" style={{ lineHeight: 1.4 }}>{t.label}</div>
            <div className="num" style={{ marginTop: 10, color: t.fg }}>{t.value}</div>
            <div className="muted" style={{ marginTop: 6 }}>{t.note}</div>
          </div>
        ))}
      </div>

      <div className="grid auto-330" style={{ marginTop: 20, alignItems: "start" }}>
        <div className="panel pad">
          <div className="eyebrow">RECONCILIATION FUNNEL</div>
          <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {funnel.map((f) => (
              <div key={f.label}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
                  <span style={{ fontSize: 13, color: "var(--ink-1)" }}>{f.label}</span>
                  <span className="mono" style={{ fontSize: 13, fontWeight: 500, color: f.fg }}>{f.value}</span>
                </div>
                <div className="bar" style={{ marginTop: 6 }}>
                  <i style={{ width: `${f.pct}%`, background: f.fg }} />
                </div>
                <div className="muted" style={{ marginTop: 5 }}>{f.note}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel flush">
          <div className="panel-head">
            <span className="eyebrow">RELATIONSHIPS</span>
          </div>
          {pairs.length === 0 && <div className="empty">No relationships yet.</div>}
          {pairs.map((p) => {
            const v = VERDICT[p.verdict ?? "INSUFFICIENT_EVIDENCE"];
            return (
              <button
                key={p.id}
                className="row click"
                style={{ padding: "11px 14px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}
                onClick={() => select({ id: p.id, a: p.a, b: p.b, label: p.label })}
              >
                <span style={{ fontSize: 12.5, color: "var(--ink-1)" }}>{p.label}</span>
                <span className="badge" style={{ background: v.bg, color: v.fg }}>{p.verdict}</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}
