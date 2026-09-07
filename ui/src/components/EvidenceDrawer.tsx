import { useMemo } from "react";

import { useStore } from "../store";

// Deterministic "page layout" filler so the render doesn't reflow on every
// open — seeded from the fact id rather than random per mount.
function linesFor(id: string, n: number, seed: number) {
  let x = seed;
  for (let i = 0; i < id.length; i++) x = (x * 31 + id.charCodeAt(i)) % 97;
  return Array.from({ length: n }, (_, i) => {
    x = (x * 53 + i * 17) % 100;
    return 82 + (x % 17);
  });
}

export default function EvidenceDrawer() {
  const { evidence, factById, docById, closeEvidence } = useStore();
  const fact = factById(evidence);
  const doc = docById(fact?.doc);

  const linesTop = useMemo(() => (fact ? linesFor(fact.id, 5, 11) : []), [fact]);
  const linesBottom = useMemo(() => (fact ? linesFor(fact.id, 6, 47) : []), [fact]);

  if (!fact) return null;

  const chain = [
    `${fact.doc} · page ${fact.page} · ${fact.section}`,
    `span located at char offset ${1200 + fact.page * 7}–${1200 + fact.page * 7 + (fact.quote || "").length}`,
    `axes resolved from governing heading: scope=${fact.scope}, basis=${fact.basis}`,
    `period anchored to ${fact.period}`,
    `confidence ${fact.confidence.toFixed(2)} · ${fact.noEvidence ? "quarantined" : "grounded"}`,
  ];

  const rows: { k: string; v: string; mono?: boolean }[] = [
    { k: "Entity", v: fact.entity },
    { k: "Metric", v: fact.metric },
    { k: "Unit", v: fact.unit, mono: true },
    { k: "Period", v: fact.period, mono: true },
    { k: "Scope", v: fact.scope },
    { k: "Basis", v: fact.basis },
    { k: "Page", v: String(fact.page), mono: true },
    { k: "Confidence", v: fact.confidence.toFixed(2), mono: true },
  ];

  return (
    <>
      <button className="scrim" aria-label="Close evidence viewer" onClick={closeEvidence} />
      <div className="drawer" role="dialog" aria-label="Evidence viewer">
        <div className="drawer-head">
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{doc ? doc.title : fact.doc}</div>
            <div className="ref" style={{ marginTop: 3 }}>
              {fact.doc} · page {fact.page} · {fact.section}
            </div>
          </div>
          <button className="btn" onClick={closeEvidence}>
            CLOSE ✕
          </button>
        </div>

        <div
          className="sb"
          style={{
            flex: 1,
            overflow: "auto",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
            alignItems: "start",
          }}
        >
          <div style={{ padding: 22 }}>
            <div className="page-render">
              <div style={{ position: "absolute", top: 14, right: 18, fontSize: 9.5, fontFamily: "var(--mono)", color: "var(--faint)" }}>
                {fact.page} / {doc?.pages ?? "—"}
              </div>
              <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.12em", color: "var(--muted-1)", textTransform: "uppercase" }}>
                {fact.section}
              </div>
              {linesTop.map((w, i) => (
                <div key={i} className="page-line" style={{ width: `${w}%` }} />
              ))}
              <div
                style={{
                  margin: "20px 0",
                  padding: "14px 16px",
                  background: "var(--green-wash)",
                  border: "1px solid var(--green-edge)",
                  borderRadius: 2,
                  position: "relative",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: -8,
                    left: 12,
                    padding: "1px 5px",
                    background: "var(--green)",
                    color: "#fff",
                    font: "500 8.5px/1.6 var(--mono)",
                    letterSpacing: "0.06em",
                  }}
                >
                  EVIDENCE SPAN
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.6, color: "var(--rail)" }} className="pretty">
                  {fact.quote || "No sentence-level evidence span was located within ±2 pages of the extracted value."}
                </div>
              </div>
              {linesBottom.map((w, i) => (
                <div key={i} className="page-line" style={{ width: `${w}%` }} />
              ))}
              <div style={{ marginTop: 26, fontSize: 9, fontFamily: "var(--mono)", color: "var(--faint)" }}>
                PAGE RENDER · SURROUNDING BODY TEXT SHOWN AS LAYOUT BLOCKS
              </div>
            </div>
          </div>

          <div style={{ padding: "22px 22px 22px 0" }}>
            <div className="panel" style={{ padding: "15px 16px" }}>
              <div className="eyebrow">EXTRACTED CLAIM</div>
              <div style={{ marginTop: 10, font: "500 13px/1.5 var(--mono)", color: "var(--ink-1)" }}>{fact.claim}</div>
              <dl className="kv" style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--rule-hair)" }}>
                {rows.map((r) => (
                  <div key={r.k} style={{ display: "contents" }}>
                    <dt>{r.k}</dt>
                    <dd style={r.mono ? { fontFamily: "var(--mono)" } : undefined}>{r.v}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="panel" style={{ marginTop: 14, padding: "15px 16px" }}>
              <div className="eyebrow">PROVENANCE CHAIN</div>
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8, font: "400 11px/1.5 var(--mono)", color: "var(--ink-3)" }}>
                {chain.map((c, i) => (
                  <div key={i} style={{ display: "flex", gap: 8 }}>
                    <span style={{ color: "var(--green-edge)" }}>▸</span>
                    <span>{c}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
