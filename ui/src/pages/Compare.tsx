import { AXIS_STATUS, VERDICT } from "../api/format";
import { fmt } from "../api/format";
import { MASKABLE, AXIS_LABEL } from "../api/engine";
import type { AxisKey } from "../api/types";
import { useCompare } from "../hooks/useCompare";
import { useStore } from "../store";

export default function Compare() {
  const { pairs, factById, docById, select, selection, openEvidence } = useStore();
  const { result, baseline, busy, masked, toggleMask } = useCompare(selection?.a, selection?.b);

  const A = factById(selection?.a);
  const B = factById(selection?.b);
  const vs = result ? VERDICT[result.verdict] : VERDICT.INSUFFICIENT_EVIDENCE;

  const showChain = masked.length > 0 && baseline && result && baseline.verdict !== result.verdict;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
      <div className="panel flush" style={{ flex: "1 1 240px", maxWidth: 262, minWidth: 220 }}>
        <div className="panel-head">
          <span className="eyebrow">CANDIDATE PAIRS</span>
        </div>
        {pairs.map((p) => {
          const on = p.id === selection?.id;
          const v = VERDICT[p.verdict ?? "INSUFFICIENT_EVIDENCE"];
          return (
            <button
              key={p.id}
              className="row click"
              style={{
                padding: "10px 12px",
                background: on ? "var(--head)" : "var(--panel)",
                borderLeft: `2px solid ${on ? "var(--green)" : "transparent"}`,
                display: "block",
              }}
              onClick={() => select({ id: p.id, a: p.a, b: p.b, label: p.label })}
            >
              <div style={{ fontSize: 12.5, fontWeight: on ? 600 : 400, lineHeight: 1.35, color: "var(--ink-1)" }}>{p.label}</div>
              <div style={{ marginTop: 5, display: "flex", alignItems: "center", gap: 6 }}>
                <span className="badge" style={{ background: v.bg, color: v.fg }}>{p.verdict}</span>
                <span className="ref">{p.id}</span>
              </div>
            </button>
          );
        })}
      </div>

      <div style={{ flex: "1 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
        <div className={`grid auto-300 ${busy ? "busy" : ""}`}>
          {[A, B].map((f, i) =>
            f ? (
              <div key={f.id} className="panel" style={{ padding: "16px 18px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span className="eyebrow" style={{ color: "var(--green)" }}>{i === 0 ? "FACT A" : "FACT B"}</span>
                  <span className="ref">{f.id}</span>
                </div>
                <div className="num" style={{ marginTop: 12 }}>{fmt(f)}</div>
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 3, font: "400 12px/1.5 var(--mono)", color: "var(--ink-3)" }}>
                  <div>{f.period}</div>
                  <div>{f.scope}</div>
                  <div>{f.basis}</div>
                </div>
                <dl className="kv" style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--rule-hair)" }}>
                  <dt>Entity</dt><dd>{f.entity}</dd>
                  <dt>Metric</dt><dd>{f.metric}</dd>
                  <dt>Source</dt><dd>{f.doc} p.{f.page}</dd>
                  <dt>Confidence</dt><dd className="mono">{f.confidence.toFixed(2)}</dd>
                </dl>
              </div>
            ) : (
              <div key={i} className="panel empty">No fact selected.</div>
            ),
          )}
        </div>

        <div className="grid auto-340" style={{ alignItems: "start" }}>
          <div className="panel" style={{ padding: "18px 20px 20px" }}>
            <div style={{ display: "flex", justifyContent: "center", color: "var(--faint)", fontSize: 15 }}>↓</div>
            <div
              style={{
                marginTop: 14,
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "12px 14px",
                borderRadius: 3,
                background: vs.bg,
                border: `1px solid ${vs.edge}`,
              }}
            >
              <div style={{ font: "600 15px/1 var(--mono)", letterSpacing: "0.06em", color: vs.fg }}>
                {result ? result.verdict : "…"}
              </div>
              <div style={{ flex: 1 }} />
              <div style={{ font: "400 10px/1 var(--mono)", color: vs.fg, opacity: 0.7 }}>{result ? vs.tag : ""}</div>
            </div>

            {result?.differingAxis && (
              <div style={{ marginTop: 14, padding: "12px 14px", borderLeft: "2px solid var(--amber)", background: "var(--amber-wash)" }}>
                <div className="eyebrow" style={{ color: "var(--amber-fg)" }}>DIFFERING AXIS</div>
                <div style={{ marginTop: 7, fontSize: 14, fontWeight: 600 }}>{result.differingAxis.label}</div>
                <div className="mono" style={{ marginTop: 4, fontSize: 12.5, color: "var(--ink-3)" }}>
                  {result.differingAxis.a}&nbsp;vs&nbsp;{result.differingAxis.b}
                </div>
              </div>
            )}

            <div className="pretty" style={{ marginTop: 14, fontSize: 13.5, lineHeight: 1.6, color: "var(--ink-2)" }}>
              {result?.reason}
            </div>

            {result?.normalized.a && result.normalized.b && result.normalized.a.dim !== "TENURE" && (
              <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 8, font: "400 11px/1 var(--mono)", color: "var(--ink-3)" }}>
                <span style={{ padding: "5px 8px", background: "var(--wash)", borderRadius: 2 }}>
                  NORMALIZED A &nbsp;{result.normalized.a.value.toLocaleString("en-IN", { maximumFractionDigits: 1 })} {result.normalized.a.unit}
                </span>
                <span style={{ padding: "5px 8px", background: "var(--wash)", borderRadius: 2 }}>
                  NORMALIZED B &nbsp;{result.normalized.b.value.toLocaleString("en-IN", { maximumFractionDigits: 1 })} {result.normalized.b.unit}
                </span>
              </div>
            )}

            <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--rule-hair)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <span className="eyebrow">COUNTERFACTUAL · HOLD AN AXIS OUT OF REASONING</span>
                <span className="ref">POST /compare</span>
              </div>
              <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 7 }}>
                {MASKABLE.map((key: AxisKey) => {
                  const on = masked.includes(key);
                  return (
                    <button key={key} className={`btn ${on ? "on" : ""}`} onClick={() => toggleMask(key)}>
                      {on ? "◼ " : "◻ "}Mask {AXIS_LABEL[key]}
                    </button>
                  );
                })}
              </div>

              {showChain && baseline && result && (
                <div
                  style={{
                    marginTop: 14,
                    padding: 14,
                    background: "var(--rail)",
                    borderRadius: 3,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <div style={{ font: "600 12px/1 var(--mono)", letterSpacing: "0.06em", color: VERDICT[baseline.verdict].dark }}>
                    {baseline.verdict}
                  </div>
                  <div style={{ color: "#565e4c", fontSize: 12 }}>↓</div>
                  <div style={{ font: "400 11px/1 var(--mono)", color: "#9ba391" }}>
                    Mask {masked.map((k) => k[0].toUpperCase() + k.slice(1)).join(" + ")}
                  </div>
                  <div style={{ color: "#565e4c", fontSize: 12 }}>↓</div>
                  <div style={{ font: "600 12px/1 var(--mono)", letterSpacing: "0.06em", color: VERDICT[result.verdict].dark }}>
                    {result.verdict}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="panel flush">
            <div className="panel-head"><span className="eyebrow">AXIS COMPARISON</span></div>
            {(result?.axes ?? []).map((x) => {
              const st = AXIS_STATUS[x.status];
              return (
                <div key={x.key} className="row" style={{ padding: "9px 14px", display: "grid", gridTemplateColumns: "1fr auto", gap: 10, alignItems: "center" }}>
                  <div>
                    <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-1)" }}>{x.label}</div>
                    <div className="mono" style={{ marginTop: 2, fontSize: 10.5, color: "var(--muted-2)" }}>
                      {x.status === "match" ? x.a : `${x.a} → ${x.b}`}
                    </div>
                  </div>
                  <div className="badge" style={{ color: st.fg, background: st.bg }}>{st.mark}</div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="grid auto-300">
          {[A, B].map((f, i) => {
            if (!f) return null;
            const doc = docById(f.doc);
            return (
              <div key={f.id} className="panel" style={{ padding: "15px 17px", display: "flex", flexDirection: "column" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                  <span className="eyebrow">EVIDENCE · {i === 0 ? "FACT A" : "FACT B"}</span>
                  <span className="ref">{f.doc} · p.{f.page}</span>
                </div>
                <div className="pretty" style={{ marginTop: 11, fontSize: 13.5, lineHeight: 1.62, color: "var(--ink-1)" }}>
                  <span className="span-mark">{f.quote || "No sentence-level evidence span was located for this claim."}</span>
                </div>
                <div className="muted" style={{ marginTop: 11 }}>{doc ? doc.title : f.doc} · {f.section}</div>
                <div style={{ flex: 1 }} />
                <button className="btn go" style={{ marginTop: 13, alignSelf: "flex-start" }} onClick={() => openEvidence(f.id)}>
                  OPEN SOURCE PAGE →
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
