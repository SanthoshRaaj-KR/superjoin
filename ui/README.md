# Collation — UI

React interface for the Fact Knowledge Layer. Lives entirely under this
folder with its own git history of commits (see below) so it never mixes
with the Python backend's commits in the parent repo.

Built from the `PDF Fact Reconciliation System/Reconcile.dc.html` design
canvas — same palette, type scale and layout, reimplemented as a real,
stateful React app rather than a static mock.

## Run it

```bash
npm install
npm run dev       # http://localhost:5173, mock service, no backend needed
```

```bash
npm run build     # tsc -b && vite build -> dist/
npm run typecheck
```

## Talking to the real backend

By default the app runs entirely against an in-memory mock (`src/api/client.ts`)
that seeds a small corpus and runs the same comparability gate
(`src/api/engine.ts`) the backend runs, so every screen — including the
counterfactual mask toggles on the Compare page — behaves the way the real
service will. To point it at a running backend instead:

```bash
VITE_API_BASE=/api/v1 npm run dev
```

The dev server proxies `/api/*` to `FKL_API` (default
`http://127.0.0.1:8000`) — see `vite.config.ts`. With `VITE_API_BASE` unset,
`api/client.ts` never touches the network.

Expected routes, matching `fkl.export`'s shape (`src/api/types.ts`):

| Method | Path                | Returns                                   |
|---|---|---|
| GET  | `/corpus`            | `Corpus` — aggregate counts               |
| GET  | `/documents`         | `Doc[]`                                   |
| GET  | `/facts`             | `Fact[]`                                  |
| GET  | `/pairs`             | `Pair[]` with `verdict`                   |
| GET  | `/quarantine`        | `Quarantined[]`                           |
| POST | `/compare`           | `{a, b, maskedAxes}` → `CompareResult`    |
| POST | `/documents/upload`  | multipart `file` → `Doc`                  |

## Layout

```
src/
  api/        types, the comparability gate, mock fixtures, client, formatting
  components/ Rail, Topbar, EvidenceDrawer, Dropzone
  pages/      Overview, Documents, Facts, Compare, Timeline, Quality
  hooks/      useCompare — drives the Compare page's /compare calls
  store.tsx   app state: hash routing, ingest polling, selection, drawer
  theme.css   palette and primitives lifted from the design canvas
```

## The four required cases

All reachable from the seed corpus without touching a real PDF:

| Case | Where |
|---|---|
| Corroborated across documents | Compare → `r-01` (₹81,415M vs ₹8,142 Cr, same fact) |
| Explained by context | Compare → `r-02` (consolidated vs standalone scope) |
| Genuine contradiction | Compare → `r-03` (every axis matches, values still differ) |
| Extraction failure, handled | Compare → `r-06` / Corpus quality → quarantine list (chart label, no evidence span, withheld) |

## Git

Every commit that touches this app is scoped to `ui/` — staged and
committed from inside this directory so the backend's own commit history
stays untouched.
