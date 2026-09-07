import { PAGES, useStore } from "../store";

export default function Rail() {
  const { page, goto, docs, facts, pairs, quarantine, live, ingesting } = useStore();

  const counts: Record<string, string> = {
    overview: "",
    documents: String(docs.length),
    facts: String(facts.length),
    compare: String(pairs.length),
    timeline: String(new Set(facts.filter((f) => f.kind === "tenure").map((f) => f.role)).size),
    quality: String(quarantine.length),
  };

  return (
    <nav className="rail" aria-label="Sections">
      <div className="rail-head">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="rail-mark" aria-hidden />
          <span className="rail-title">Collation</span>
        </div>
        <div className="rail-sub">
          EVIDENCE-GROUNDED
          <br />
          FACT RECONCILIATION
        </div>
      </div>

      <div className="rail-nav">
        {PAGES.map((p) => (
          <button
            key={p.key}
            className="rail-item"
            aria-current={page === p.key ? "page" : undefined}
            onClick={() => goto(p.key)}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span className="rail-tick" aria-hidden />
              <span>{p.label}</span>
            </span>
            <span className="rail-count">{counts[p.key]}</span>
          </button>
        ))}
      </div>

      <div className="rail-foot">
        <div className="rail-live">
          <span className={`dot ${live ? "" : "idle"}`} aria-hidden />
          <span>{live ? "LIVE API" : "MOCK SERVICE"}</span>
        </div>
        <div style={{ marginTop: 4 }}>{ingesting ? "INGEST RUNNING…" : "ENGINE v0.9.3 · axis-aware"}</div>
      </div>
    </nav>
  );
}
