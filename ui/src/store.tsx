import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { LIVE, api } from "./api/client";
import type { Corpus, Doc, Fact, Pair, Quarantined } from "./api/types";

export const PAGES = [
  { key: "overview", label: "Overview", title: "Corpus overview", sub: "Extraction, grounding and reconciliation state across the loaded document set." },
  { key: "documents", label: "Documents", title: "Documents", sub: "Ingested PDFs with per-document extraction and grounding counts. Drop a file to add one." },
  { key: "facts", label: "Facts", title: "Facts", sub: "Every grounded claim as a passport: entity, metric, value, unit, period, scope, source." },
  { key: "compare", label: "Compare", title: "Compare", sub: "Two facts, their context axes and the verdict the engine returns for the pair." },
  { key: "timeline", label: "Timeline", title: "Timeline", sub: "Time-bound facts, office succession and recorded vacancies at a chosen date." },
  { key: "quality", label: "Corpus quality", title: "Corpus quality", sub: "Grounding rate, confidence distribution, quarantined claims and discovered axes." },
] as const;

export type PageKey = (typeof PAGES)[number]["key"];
const KEYS = PAGES.map((p) => p.key) as PageKey[];

/** A pair under inspection. Either one the backend proposed, or one the reader
 *  built by picking two facts, which is why `id` is optional. */
export interface PairRef {
  id?: string;
  a: string;
  b: string;
  label: string;
}

interface Store {
  live: boolean;
  loading: boolean;
  error: string | null;
  corpus: Corpus | null;
  docs: Doc[];
  facts: Fact[];
  pairs: Pair[];
  quarantine: Quarantined[];
  ingesting: boolean;
  reload: () => void;
  upload: (files: FileList | File[]) => Promise<void>;
  factById: (id: string | null | undefined) => Fact | undefined;
  docById: (id: string | null | undefined) => Doc | undefined;

  page: PageKey;
  goto: (page: PageKey) => void;

  selection: PairRef | null;
  select: (ref: PairRef) => void;

  docFilter: string;
  setDocFilter: (id: string) => void;

  evidence: string | null;
  openEvidence: (factId: string) => void;
  closeEvidence: () => void;
}

const Ctx = createContext<Store | null>(null);

function readHash(): PageKey {
  const raw = window.location.hash.replace(/^#\/?/, "");
  return (KEYS as string[]).includes(raw) ? (raw as PageKey) : "overview";
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [pairs, setPairs] = useState<Pair[]>([]);
  const [quarantine, setQuarantine] = useState<Quarantined[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [page, setPage] = useState<PageKey>(readHash);
  const [selection, setSelection] = useState<PairRef | null>(null);
  const [docFilter, setDocFilter] = useState("all");
  const [evidence, setEvidence] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [c, d, f, p, q] = await Promise.all([
        api.corpus(),
        api.documents(),
        api.facts(),
        api.pairs(),
        api.quarantine(),
      ]);
      setCorpus(c);
      setDocs(d);
      setFacts(f);
      setPairs(p);
      setQuarantine(q);
      setError(null);
      setSelection((cur) => cur ?? (p[0] ? { id: p[0].id, a: p[0].a, b: p[0].b, label: p[0].label } : null));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Hash routing, so a screen is linkable and the back button works.
  useEffect(() => {
    const onHash = () => setPage(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goto = useCallback((next: PageKey) => {
    window.location.hash = "/" + next;
    setPage(next);
  }, []);

  // While anything is mid-ingest the document and corpus counts are moving, so
  // poll them. Facts and pairs only change when a document finishes.
  const ingesting = docs.some((d) => d.status === "extracting" || d.status === "queued");
  const wasIngesting = useRef(ingesting);
  useEffect(() => {
    if (!ingesting) {
      if (wasIngesting.current) void load(); // a document just landed — refetch everything
      wasIngesting.current = false;
      return;
    }
    wasIngesting.current = true;
    const t = window.setInterval(async () => {
      try {
        const [d, c] = await Promise.all([api.documents(), api.corpus()]);
        setDocs(d);
        setCorpus(c);
      } catch {
        /* a dropped poll is not worth surfacing; the next one retries */
      }
    }, 900);
    return () => window.clearInterval(t);
  }, [ingesting, load]);

  const upload = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files).filter((f) => /\.pdf$/i.test(f.name));
    if (!list.length) return;
    try {
      const added = await Promise.all(list.map((f) => api.upload(f)));
      setDocs((cur) => {
        const known = new Set(cur.map((d) => d.id));
        return cur.concat(added.filter((d) => !known.has(d.id)));
      });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const factById = useCallback(
    (id: string | null | undefined) => (id ? facts.find((f) => f.id === id) : undefined),
    [facts],
  );
  const docById = useCallback(
    (id: string | null | undefined) => (id ? docs.find((d) => d.id === id) : undefined),
    [docs],
  );

  const select = useCallback((ref: PairRef) => {
    setSelection(ref);
    window.location.hash = "/compare";
    setPage("compare");
  }, []);

  const value = useMemo<Store>(
    () => ({
      live: LIVE,
      loading,
      error,
      corpus,
      docs,
      facts,
      pairs,
      quarantine,
      ingesting,
      reload: () => void load(),
      upload,
      factById,
      docById,
      page,
      goto,
      selection,
      select,
      docFilter,
      setDocFilter,
      evidence,
      openEvidence: setEvidence,
      closeEvidence: () => setEvidence(null),
    }),
    [
      loading, error, corpus, docs, facts, pairs, quarantine, ingesting, load, upload,
      factById, docById, page, goto, selection, select, docFilter, evidence,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}
