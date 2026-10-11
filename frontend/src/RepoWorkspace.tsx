import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import Canvas from "./Canvas";
import DetailPanel from "./DetailPanel";
import FolderMap from "./FolderMap";
import RepoDetail from "./RepoDetail";
import RepoTree from "./RepoTree";
import { base } from "./graphParts";
import { saveRecent } from "./OpenFolder";
import { parseRepoHash, repoHash, ROOT, type Loc } from "./route";
import type { Analysis, RepoIndex, RepoStatus } from "./types";

type Props = {
  id: string;
  ai: boolean;
  onIndex: (index: RepoIndex | null) => void;
  onFail: (message: string) => void;
};

function ScanProgress({ status }: { status: RepoStatus | null }) {
  const pct = status && status.total ? Math.min(100, Math.round((100 * status.done) / status.total)) : null;
  return (
    <main className="scan">
      <div className="scan-card" role="status">
        <h2>Reading {status?.name ?? "the project"}…</h2>
        <p>{status?.phase ?? "Starting"}{status?.total ? `: ${status.done.toLocaleString()} of ${status.total.toLocaleString()}` : ""}</p>
        <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
          <i style={{ width: `${pct ?? 6}%` }} />
        </div>
        <p className="hint">The first read of a big project takes a little while. After that it's cached, and opening it again is quick.</p>
      </div>
    </main>
  );
}

export default function RepoWorkspace({ id, ai, onIndex, onFail }: Props) {
  const [status, setStatus] = useState<RepoStatus | null>(null);
  const [index, setIndex] = useState<RepoIndex | null>(null);
  const [scanKey, setScanKey] = useState(0);
  const [loc, setLoc] = useState<Loc>(() => {
    const p = parseRepoHash(location.hash);
    return p && p.id === id ? p.loc : ROOT;
  });
  const [files, setFiles] = useState<Record<string, Analysis>>({});
  const [fileError, setFileError] = useState<{ path: string; message: string } | null>(null);
  const [lines, setLines] = useState<[number, number] | null>(null);
  const [depth, setDepth] = useState<number>(() => (window.history.state?.readerDepth as number) ?? 0);
  const locRef = useRef(loc);
  locRef.current = loc;
  const visits = useRef<Loc[]>([loc]);
  const findRef = useRef<HTMLInputElement>(null);

  // ---- wait for the project to be read
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const st = await api.repoStatus(id);
        if (!live) return;
        setStatus(st);
        if (st.state === "ready") {
          const idx = await api.repoIndex(id);
          if (live) {
            setIndex(idx);
            onIndex(idx);
            document.title = `${idx.name} — The Reader`;
            saveRecent({ id: idx.id, path: idx.root, name: idx.name }); // also projects opened from the terminal
          }
        } else if (st.state === "scanning") {
          timer = window.setTimeout(tick, 250);
        } else {
          onFail(st.error ?? "Couldn't read this project.");
        }
      } catch (e: any) {
        if (live) onFail(e.message);
      }
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, scanKey]);

  // ---- the address bar is the source of truth for Back / Forward
  useEffect(() => {
    if (location.hash !== repoHash(id, loc)) window.history.replaceState({ readerDepth: depth }, "", repoHash(id, loc));
    const sync = () => {
      const p = parseRepoHash(location.hash);
      if (p && p.id === id) {
        setLoc(p.loc);
        visits.current = [...visits.current.slice(-60), p.loc];
        setDepth((window.history.state?.readerDepth as number) ?? 0);
        setLines(null);
      }
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const go = useCallback((next: Partial<Loc>, replace = false) => {
    const cur = locRef.current;
    const path = next.path ?? cur.path;
    const symbol = next.symbol !== undefined ? next.symbol : path === cur.path ? cur.symbol : null;
    const view = next.view ?? (symbol && cur.view === "flow" ? "flow" : "map");
    const l: Loc = { path, symbol, view };
    if (l.path === cur.path && l.symbol === cur.symbol && l.view === cur.view) return;
    const h = repoHash(id, l);
    if (replace) {
      window.history.replaceState({ readerDepth: window.history.state?.readerDepth ?? 0 }, "", h);
    } else {
      const d = ((window.history.state?.readerDepth as number) ?? 0) + 1;
      window.history.pushState({ readerDepth: d }, "", h);
      setDepth(d);
    }
    visits.current = [...visits.current.slice(-60), l];
    setLoc(l);
    setLines(null);
  }, [id]);

  // ---- what kind of place we're at
  const isDir = !!index && index.dirs[loc.path] !== undefined;
  const fileInfo = index?.files[loc.path];
  const isPyFile = !!fileInfo?.symbols;
  const analysis = isPyFile ? files[loc.path] : undefined;

  const ownerOf = useMemo(() => {
    const m = new Map<string, string>();
    if (index) for (const d of Object.values(index.dirs)) for (const f of d.files) m.set(f, d.path);
    return m;
  }, [index]);

  useEffect(() => {
    if (!index || isDir || isPyFile) return;
    go({ path: fileInfo ? ownerOf.get(loc.path) ?? "" : "", symbol: null }, true); // not something we can show
  }, [index, loc.path, isDir, isPyFile, fileInfo, ownerOf, go]);

  useEffect(() => {
    if (!index || !isPyFile || files[loc.path]) return;
    let live = true;
    setFileError(null);
    api.repoFile(id, loc.path)
      .then((a) => live && setFiles((f) => ({ ...f, [loc.path]: a })))
      .catch((e) => live && setFileError({ path: loc.path, message: e.message }));
    return () => { live = false; };
  }, [id, index, isPyFile, loc.path, files]);

  // the path you followed through this file, for the call map highlight
  const trail = useMemo(() => {
    const t: string[] = [];
    for (let i = visits.current.length - 1; i >= 0; i--) {
      const v = visits.current[i];
      if (v.path !== loc.path || !v.symbol) break;
      if (t[0] !== v.symbol) t.unshift(v.symbol);
    }
    return [...new Set(t)];
  }, [loc]);

  // ---- moving around
  const up = useCallback(() => {
    const cur = locRef.current;
    if (!index) return;
    if (cur.symbol) go({ symbol: null });
    else if (index.dirs[cur.path] !== undefined) {
      const parent = index.dirs[cur.path].parent;
      if (parent !== null) go({ path: parent, symbol: null });
    } else go({ path: ownerOf.get(cur.path) ?? "", symbol: null });
  }, [index, ownerOf, go]);

  const step = useCallback((dir: 1 | -1) => {
    if (!analysis) return;
    const order = analysis.reading_order;
    const i = loc.symbol ? order.indexOf(loc.symbol) : -1;
    const next = order[Math.min(order.length - 1, Math.max(0, i + dir))];
    if (next) go({ symbol: next });
  }, [analysis, loc.symbol, go]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = !!el.closest("input, textarea, select");
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        findRef.current?.focus();
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "m") go({ view: "map" });
      else if (e.key === "f" && analysis) {
        go({ view: "flow", symbol: locRef.current.symbol ?? analysis.reading_order.find((s) => analysis.flowcharts[s]) ?? null });
      } else if (e.key === "j") step(1);
      else if (e.key === "k") step(-1);
      else if (e.key === "[" || e.key === "Backspace") { if (depth > 0) window.history.back(); }
      else if (e.key === "u") up();
      else if (e.key === "Escape") setLines(null);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, step, up, analysis, depth]);

  // ---- `paths` and names in explanations become links
  const symbolHomes = useMemo(() => {
    const m = new Map<string, { path: string; id: string }[]>();
    if (index) {
      for (const [path, f] of Object.entries(index.files)) {
        for (const r of f.symbols ?? []) (m.get(r[0]) ?? m.set(r[0], []).get(r[0])!).push({ path, id: r[0] });
      }
    }
    return m;
  }, [index]);

  const linkPath = useCallback((raw: string) => {
    if (!index) return null;
    const name = raw.trim().replace(/\(\)$/, "").replace(/^\.\//, "").replace(/\/$/, "");
    let target: { path: string; symbol: string | null } | null = null;
    const pyFiles = Object.keys(index.files).filter((f) => index.files[f].symbols);
    if (name && index.dirs[name] !== undefined) target = { path: name, symbol: null };
    else if (index.files[name]?.symbols) target = { path: name, symbol: null };
    else {
      const m = /^(.+?\.pyi?)\s*[:,]\s*([\w.<>]+)$/.exec(name);
      const filePart = m ? m[1] : /\.pyi?$/.test(name) ? name : null;
      if (filePart) {
        const hits = pyFiles.filter((f) => f === filePart || f.endsWith(`/${filePart}`));
        if (hits.length === 1) target = { path: hits[0], symbol: m ? m[2] : null };
      } else if (/^[\w.]+$/.test(name)) {
        const homes = symbolHomes.get(name) ?? [];
        const local = analysis && analysis.symbols.some((s) => s.id === name);
        if (local) target = { path: loc.path, symbol: name };
        else if (homes.length === 1) target = { path: homes[0].path, symbol: homes[0].id };
      }
    }
    if (!target) return null;
    const t = target;
    return <button className="sym-link" onClick={() => go({ path: t.path, symbol: t.symbol })} title={t.path}>{raw}</button>;
  }, [index, symbolHomes, analysis, loc.path, go]);

  const crumbs = useMemo(() => {
    if (!index) return [];
    const out: { label: string; path: string; symbol: string | null }[] = [];
    const dir = isDir ? loc.path : ownerOf.get(loc.path) ?? "";
    const chain: string[] = [];
    for (let d: string | null = dir; d !== null && index.dirs[d]; d = index.dirs[d].parent) chain.unshift(d);
    for (const d of chain) out.push({ label: d === "" ? index.name : index.dirs[d].name, path: d, symbol: null });
    if (!isDir && isPyFile) out.push({ label: base(loc.path), path: loc.path, symbol: null });
    if (loc.symbol && isPyFile) out.push({ label: loc.symbol === "<module>" ? "module code" : loc.symbol, path: loc.path, symbol: loc.symbol });
    return out;
  }, [index, isDir, isPyFile, loc, ownerOf]);

  const rescan = useCallback(async () => {
    if (!index) return;
    try {
      await api.repoOpen(index.root);
      setIndex(null);
      onIndex(null);
      setFiles({});
      setScanKey((k) => k + 1);
    } catch (e: any) {
      onFail(e.message);
    }
  }, [index, onIndex, onFail]);

  if (!index) return <ScanProgress status={status} />;

  return (
    <main className="workspace repo">
      <RepoTree ref={findRef} index={index} loc={loc} onGo={(path, symbol) => go({ path, symbol: symbol ?? null })} />
      <section className="stage">
        <div className="stage-bar">
          {isPyFile && (
            <div className="seg" role="tablist" aria-label="View">
              <button role="tab" aria-selected={loc.view === "map"} onClick={() => go({ view: "map" })}>Call map</button>
              <button role="tab" aria-selected={loc.view === "flow"}
                onClick={() => go({ view: "flow", symbol: loc.symbol ?? analysis?.reading_order.find((s) => analysis.flowcharts[s]) ?? null })}>
                Flowchart
              </button>
            </div>
          )}
          <ol className="trail crumbs" aria-label="Where you are">
            {crumbs.map((c, i) => (
              <li key={`${c.path}::${c.symbol}`}>
                <button className={i === crumbs.length - 1 ? "here" : ""} onClick={() => go({ path: c.path, symbol: c.symbol })}>{c.label}</button>
                {i < crumbs.length - 1 && <span className="sep" aria-hidden>›</span>}
              </li>
            ))}
          </ol>
          {depth > 0 && <button className="ghost small" onClick={() => window.history.back()} title="Back ([)">Back</button>}
        </div>
        <div className="canvas">
          {isDir ? (
            <FolderMap index={index} dir={loc.path} onGo={(p) => go({ path: p, symbol: null })} />
          ) : analysis ? (
            <Canvas analysis={analysis} view={loc.view} selected={loc.symbol} trail={trail} lines={lines}
              onOpen={(sid) => { if (loc.view === "map" && sid === loc.symbol) go({ view: "flow" }); else go({ symbol: sid }); }}
              onLines={setLines} onOpenExternal={(f, s) => go({ path: f, symbol: s })} />
          ) : fileError?.path === loc.path ? (
            <div className="canvas-empty"><p className="err">{fileError.message}</p></div>
          ) : (
            <div className="canvas-empty"><p>Reading {base(loc.path)}…</p></div>
          )}
        </div>
      </section>
      {isDir ? (
        <RepoDetail index={index} dir={loc.path} ai={ai} linkPath={linkPath} onRescan={loc.path === "" ? rescan : undefined} />
      ) : analysis ? (
        <DetailPanel analysis={analysis} selected={loc.symbol} lines={lines} ai={ai}
          onOpen={(sid) => go({ symbol: sid })}
          explain={(sym, useAi) => api.repoExplain(id, sym ? "symbol" : "file", analysis.file.path ?? loc.path, sym, useAi)}
          onOpenExternal={(f, s) => go({ path: f, symbol: s })}
          linkPath={linkPath} />
      ) : (
        <aside className="detail" />
      )}
    </main>
  );
}
