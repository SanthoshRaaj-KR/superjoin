import EvidenceDrawer from "./components/EvidenceDrawer";
import Rail from "./components/Rail";
import Topbar from "./components/Topbar";
import Compare from "./pages/Compare";
import Documents from "./pages/Documents";
import Facts from "./pages/Facts";
import Overview from "./pages/Overview";
import Quality from "./pages/Quality";
import Timeline from "./pages/Timeline";
import { useStore } from "./store";

const SCREENS = {
  overview: Overview,
  documents: Documents,
  facts: Facts,
  compare: Compare,
  timeline: Timeline,
  quality: Quality,
} as const;

export default function App() {
  const { page, loading, error, reload } = useStore();
  const Screen = SCREENS[page];

  return (
    <div className="shell">
      <Rail />
      <div className="main">
        <Topbar />
        <div className="sb body">
          {error && (
            <div className="panel" style={{ padding: "14px 16px", marginBottom: 16, borderColor: "var(--red)", background: "var(--red-wash)", color: "var(--red)" }}>
              {error}{" "}
              <button className="btn link" style={{ color: "var(--red)" }} onClick={reload}>
                RETRY
              </button>
            </div>
          )}
          {loading ? <div className="empty">Loading corpus…</div> : <Screen />}
        </div>
      </div>
      <EvidenceDrawer />
    </div>
  );
}
