import Dropzone from "../components/Dropzone";
import { DOC_STATUS, mb } from "../api/format";
import { useStore } from "../store";

export default function Documents() {
  const { docs, goto, setDocFilter } = useStore();

  return (
    <>
      <div className="panel flush">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "2.2fr 0.8fr 0.5fr 0.6fr 0.6fr 0.7fr auto",
            gap: 12,
            padding: "9px 16px",
            borderBottom: "1px solid var(--rule-mid)",
          }}
          className="eyebrow"
        >
          <div>DOCUMENT</div>
          <div>STATUS</div>
          <div>PAGES</div>
          <div>CLAIMS</div>
          <div>GROUNDED</div>
          <div>QUARANTINE</div>
          <div />
        </div>

        {docs.length === 0 && <div className="empty">No documents ingested yet.</div>}

        {docs.map((d) => {
          const st = DOC_STATUS[d.status] ?? DOC_STATUS.queued;
          return (
            <div
              key={d.id}
              className="row"
              style={{
                display: "grid",
                gridTemplateColumns: "2.2fr 0.8fr 0.5fr 0.6fr 0.6fr 0.7fr auto",
                gap: 12,
                padding: "12px 16px",
                alignItems: "center",
              }}
            >
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>{d.title}</div>
                <div className="ref" style={{ marginTop: 3 }}>
                  {d.id} · {d.org} · {mb(d.bytes)} · {d.uploaded}
                </div>
              </div>
              <div>
                <span className="badge" style={{ background: st.bg, color: st.fg }}>{d.status.toUpperCase()}</span>
                {d.status === "extracting" && (
                  <div className="bar" style={{ height: 3, marginTop: 6 }}>
                    <i style={{ width: `${Math.round((d.progress ?? 0) * 100)}%`, background: "var(--amber)" }} />
                  </div>
                )}
              </div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>{d.pages}</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>{d.claims}</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--green)" }}>{d.grounded}</div>
              <div className="mono" style={{ fontSize: 12, color: d.quarantined > 20 ? "var(--red)" : "var(--amber-fg)" }}>
                {d.quarantined}
              </div>
              <button
                className="btn link"
                style={{ fontSize: 10.5, whiteSpace: "nowrap" }}
                onClick={() => {
                  setDocFilter(d.id);
                  goto("facts");
                }}
              >
                FACTS →
              </button>
            </div>
          );
        })}
      </div>

      <Dropzone />
    </>
  );
}
