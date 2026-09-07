import { PAGES, useStore } from "../store";
import { int } from "../api/format";

export default function Topbar() {
  const { page, corpus } = useStore();
  const meta = PAGES.find((p) => p.key === page) ?? PAGES[0];

  return (
    <div className="topbar">
      <div>
        <h1>{meta.title}</h1>
        <p>{meta.sub}</p>
      </div>
      <div className="topbar-stats">
        <div>
          {int(corpus?.claims ?? 0)} CLAIMS · {int(corpus?.grounded ?? 0)} GROUNDED
        </div>
        <div>
          {corpus?.quarantined ?? 0} QUARANTINED · {corpus?.relationships ?? 0} PAIRS
        </div>
      </div>
    </div>
  );
}
