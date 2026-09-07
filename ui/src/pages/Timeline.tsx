import { useMemo, useRef, useState } from "react";

import { useStore } from "../store";

const T0 = new Date("2017-01-01").getTime();
const T1 = new Date("2026-12-31").getTime();
const pos = (d: string) => ((new Date(d).getTime() - T0) / (T1 - T0)) * 100;
const clampPct = (p: number) => Math.min(100, Math.max(0, p));

export default function Timeline() {
  const { facts, pairs, select, openEvidence } = useStore();
  const [asOf, setAsOf] = useState("2023-09-15");
  const trackRef = useRef<HTMLDivElement>(null);

  const tenures = useMemo(() => facts.filter((f) => f.kind === "tenure"), [facts]);
  const roles = useMemo(() => Array.from(new Set(tenures.map((t) => t.role!))), [tenures]);

  const asOfT = new Date(asOf).getTime();
  const inOffice = (start: string, end: string | null | undefined) =>
    asOfT >= new Date(start).getTime() && (!end || asOfT <= new Date(end).getTime());

  const tracks = roles.map((role) => {
    const ts = tenures
      .filter((t) => t.role === role)
      .sort((a, b) => new Date(a.start!).getTime() - new Date(b.start!).getTime());
    const gaps: { left: number; width: number; label: string }[] = [];
    for (let i = 0; i < ts.length - 1; i++) {
      if (!ts[i].end) continue;
      const gapDays = Math.round((new Date(ts[i + 1].start!).getTime() - new Date(ts[i].end!).getTime()) / 864e5) - 1;
      if (gapDays > 0) gaps.push({ left: pos(ts[i].end!), width: pos(ts[i + 1].start!) - pos(ts[i].end!), label: `${gapDays}d vacant` });
    }
    return {
      role,
      count: `${ts.length} holder${ts.length === 1 ? "" : "s"}`,
      gaps,
      bars: ts.map((t) => {
        const on = inOffice(t.start!, t.end);
        return {
          id: t.id,
          holder: t.holder!,
          left: pos(t.start!),
          width: pos(t.end ?? "2026-09-07") - pos(t.start!),
          on,
        };
      }),
    };
  });

  const holdersNow = tenures.filter((t) => inOffice(t.start!, t.end));
  const vacantRoles = roles.filter((role) => !holdersNow.some((h) => h.role === role));
  const asOfSummary = holdersNow.length
    ? holdersNow.map((h) => `${h.role}: ${h.holder}`).join(" · ") + (vacantRoles.length ? ` · vacant: ${vacantRoles.join(", ")}` : "")
    : "No office holders recorded at this date.";

  const ticks: { label: string; left: number }[] = [];
  for (let y = 2017; y <= 2026; y++) ticks.push({ label: String(y), left: pos(`${y}-01-01`) });

  const successions = pairs
    .filter((p) => p.verdict === "SUCCESSION")
    .map((p) => {
      const a = facts.find((f) => f.id === p.a);
      const b = facts.find((f) => f.id === p.b);
      if (!a || !b) return null;
      const gapDays = a.end ? Math.round((new Date(b.start!).getTime() - new Date(a.end).getTime()) / 864e5) - 1 : 0;
      return {
        id: p.id, a, b, role: a.role, from: a.holder, to: b.holder,
        reason: gapDays > 0
          ? `Office vacant ${gapDays} days between ${a.end} and ${b.start}. Both spans grounded in ${a.doc}.`
          : `Continuous handover with no recorded vacancy. Both spans grounded in ${a.doc}.`,
      };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);

  const dragTo = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pct = clampPct(((clientX - rect.left) / rect.width) * 100);
    const ms = T0 + (pct / 100) * (T1 - T0);
    setAsOf(new Date(ms).toISOString().slice(0, 10));
  };

  const onScrubStart = (e: React.PointerEvent) => {
    e.preventDefault();
    dragTo(e.clientX);
    const move = (ev: PointerEvent) => dragTo(ev.clientX);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 12px", background: "var(--panel)", border: "1px solid var(--rule)", borderRadius: 3 }}>
          <span className="eyebrow">AS OF</span>
          <input
            type="date"
            value={asOf}
            min="2017-01-01"
            max="2026-12-31"
            onChange={(e) => setAsOf(e.target.value)}
            style={{ border: "none", outline: "none", background: "transparent", font: "500 12.5px/1 var(--mono)", color: "var(--ink)" }}
          />
        </div>
        <div className="muted">{asOfSummary}</div>
        <div className="muted" style={{ marginLeft: "auto", fontSize: 11 }}>drag the red line, or the date field</div>
      </div>

      <div className="panel" style={{ padding: "18px 20px 12px" }}>
        <div style={{ position: "relative" }}>
          <div style={{ paddingLeft: "var(--track-label)" }}>
            <div ref={trackRef} onPointerDown={onScrubStart} style={{ position: "relative", height: 18, borderBottom: "1px solid var(--rule-mid)", cursor: "ew-resize" }}>
              {ticks.map((t) => (
                <div key={t.label} style={{ position: "absolute", top: 0, left: `${t.left}%`, font: "400 9.5px/1 var(--mono)", color: "var(--muted-3)", transform: "translateX(-50%)" }}>
                  {t.label}
                </div>
              ))}
            </div>
          </div>

          <div
            className="asof-line"
            onPointerDown={onScrubStart}
            style={{ left: `calc(var(--track-label) + ${pos(asOf)}% - ${(pos(asOf) / 100) * 150}px)` }}
          />
          <div
            className="asof-grab"
            onPointerDown={onScrubStart}
            style={{ left: `calc(var(--track-label) + ${pos(asOf)}% - ${(pos(asOf) / 100) * 150}px)` }}
          />

          {tracks.map((tr) => (
            <div key={tr.role} style={{ display: "grid", gridTemplateColumns: "150px minmax(0,1fr)", padding: "11px 0", borderBottom: "1px solid var(--rule-soft)", alignItems: "center" }}>
              <div style={{ paddingRight: 14 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--ink-1)" }}>{tr.role}</div>
                <div className="ref" style={{ marginTop: 2 }}>{tr.count}</div>
              </div>
              <div className="track">
                {tr.gaps.map((g, i) => (
                  <div key={i} title={g.label} className="track-gap" style={{ left: `${g.left}%`, width: `${g.width}%` }}>
                    <span style={{ position: "absolute", top: 30, left: "50%", transform: "translateX(-50%)", font: "500 9px/1 var(--mono)", color: "#93231a", whiteSpace: "nowrap" }}>
                      {g.label}
                    </span>
                  </div>
                ))}
                {tr.bars.map((b) => (
                  <button
                    key={b.id}
                    className="track-bar"
                    style={{
                      left: `${b.left}%`,
                      width: `${b.width}%`,
                      background: b.on ? "var(--green-wash)" : "var(--wash)",
                      border: `1px solid ${b.on ? "var(--green-edge)" : "#d2d0c6"}`,
                      color: b.on ? "var(--green)" : "#5a6055",
                    }}
                    onClick={() => openEvidence(b.id)}
                  >
                    <span className="mono" style={{ fontSize: 11, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {b.holder}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="legend">
          <span><span className="swatch" style={{ background: "var(--green-wash)", border: "1px solid var(--green-edge)" }} />IN OFFICE AT AS-OF DATE</span>
          <span><span className="swatch" style={{ background: "var(--wash)", border: "1px solid #d2d0c6" }} />TENURE</span>
          <span><span className="swatch" style={{ background: "repeating-linear-gradient(135deg,#f7e5e2 0 4px,#fbf1ef 4px 8px)", border: "1px dashed #c98a80" }} />VACANCY</span>
          <span><span style={{ width: 1, height: 11, background: "var(--red-mark)", display: "inline-block" }} />AS OF</span>
        </div>
      </div>

      {successions.length > 0 && (
        <div className="grid auto-330" style={{ marginTop: 20 }}>
          {successions.map((s) => (
            <div key={s.id} className="panel" style={{ padding: "15px 17px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="badge" style={{ background: "#e4e9f5", color: "#2c3e75" }}>SUCCESSION</span>
                <span className="muted">{s.role}</span>
              </div>
              <div style={{ marginTop: 11, font: "500 13.5px/1.5 var(--mono)", color: "var(--ink-1)" }}>{s.from} → {s.to}</div>
              <div className="pretty" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.55, color: "var(--ink-3)" }}>{s.reason}</div>
              <button
                className="btn link"
                style={{ marginTop: 12 }}
                onClick={() => select({ id: s.id, a: s.a.id, b: s.b.id, label: `${s.role} · ${s.from} → ${s.to}` })}
              >
                INSPECT PAIR →
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
