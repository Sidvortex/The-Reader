import { forwardRef, memo, useEffect, useMemo, useState } from "react";
import type { Loc } from "./route";
import type { RepoIndex, SymRow } from "./types";

type Props = {
  index: RepoIndex;
  loc: Loc;
  onGo: (path: string, symbol?: string | null) => void;
};

type Hit = { path: string; symbol: string | null; name: string; where: string; kind: string; score: number };

const MARK: Record<string, string> = { class: "C", function: "ƒ", method: "m", file: "", dir: "" };

function SymbolRows({ rows, path, depth, current, onGo }: {
  rows: SymRow[]; path: string; depth: number; current: string | null; onGo: Props["onGo"];
}) {
  const children = useMemo(() => {
    const m = new Map<string | null, SymRow[]>();
    for (const r of rows) (m.get(r[4]) ?? m.set(r[4], []).get(r[4])!).push(r);
    return m;
  }, [rows]);
  const render = (parent: string | null, d: number): React.ReactNode[] =>
    (children.get(parent) ?? []).flatMap((r) => [
      <button key={r[0]} className={`trow sym k-${r[1]}${current === r[0] ? " sel" : ""}`} style={{ paddingLeft: 12 + d * 14 }}
        onClick={() => onGo(path, r[0])} title={`${r[0]}, line ${r[3]}`}>
        <span className="mark" aria-hidden>{MARK[r[1]]}</span>
        <span className="tname">{r[2]}</span>
      </button>,
      ...(r[1] === "class" ? render(r[0], d + 1) : []),
    ]);
  return <>{render(null, depth)}</>;
}

export default memo(forwardRef<HTMLInputElement, Props>(function RepoTree({ index, loc, onGo }, findRef) {
  const [open, setOpen] = useState<Set<string>>(() => new Set([""]));
  const [query, setQuery] = useState("");

  // keep the folders around the current location open
  useEffect(() => {
    let d: string | null = index.dirs[loc.path] ? loc.path : null;
    if (d === null) {
      const owner = Object.values(index.dirs).find((x) => x.files.includes(loc.path));
      d = owner?.path ?? "";
    }
    const add: string[] = [];
    for (let x: string | null = d; x !== null; x = index.dirs[x]?.parent ?? null) add.push(x);
    setOpen((o) => (add.every((x) => o.has(x)) ? o : new Set([...o, ...add])));
  }, [index, loc.path]);

  const searchable = useMemo(() => {
    const out: { path: string; symbol: string | null; name: string; where: string; kind: string; hay: string }[] = [];
    for (const [path, f] of Object.entries(index.files)) {
      if (!f.symbols) continue;
      const fname = path.split("/").pop()!;
      out.push({ path, symbol: null, name: fname, where: path, kind: "file", hay: path.toLowerCase() });
      for (const r of f.symbols) out.push({ path, symbol: r[0], name: r[0], where: path, kind: r[1], hay: r[0].toLowerCase() });
    }
    return out;
  }, [index]);

  const hits: Hit[] = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const words = q.split(/\s+/);
    const res: Hit[] = [];
    for (const s of searchable) {
      if (!words.every((w) => s.hay.includes(w))) continue;
      const last = s.name.toLowerCase().split(/[./]/).pop()!;
      const score = (last === q ? 0 : last.startsWith(q) ? 1 : 2) * 1000 + s.hay.length;
      res.push({ ...s, score });
    }
    return res.sort((a, b) => a.score - b.score).slice(0, 150);
  }, [query, searchable]);

  const toggle = (d: string) => setOpen((o) => { const n = new Set(o); n.has(d) ? n.delete(d) : n.add(d); return n; });

  const renderDir = (path: string, depth: number): React.ReactNode[] => {
    const node = index.dirs[path];
    const rows: React.ReactNode[] = [];
    for (const d of node.dirs) {
      const sub = index.dirs[d];
      const isOpen = open.has(d);
      rows.push(
        <div key={`d:${d}`} className={`trow dir${loc.path === d ? " sel" : ""}${sub.stats.py_files ? "" : " dim"}`} style={{ paddingLeft: 6 + depth * 14 }}>
          <button className="chev" onClick={() => toggle(d)} aria-label={isOpen ? `Collapse ${sub.name}` : `Expand ${sub.name}`}
            aria-expanded={isOpen}>{isOpen ? "▾" : "▸"}</button>
          <button className="tlabel" onClick={() => { onGo(d); setOpen((o) => new Set([...o, d])); }} title={sub.doc ?? d}>
            <span className="tname">{sub.name}/</span>
            {sub.stats.py_files > 0 && <span className="tcount">{sub.stats.py_files}</span>}
          </button>
        </div>,
      );
      if (isOpen) rows.push(...renderDir(d, depth + 1));
    }
    let others = 0;
    for (const f of node.files) {
      const info = index.files[f];
      if (!info.symbols) { others++; continue; }
      const here = loc.path === f;
      rows.push(
        <button key={`f:${f}`} className={`trow file${here && !loc.symbol ? " sel" : ""}${here ? " here" : ""}`}
          style={{ paddingLeft: 26 + depth * 14 }} onClick={() => onGo(f)} title={info.doc ?? f}>
          <span className="tname">{f.split("/").pop()}</span>
          {info.main && <span className="tbadge" title="Runs as a script">run</span>}
        </button>,
      );
      if (here) rows.push(<SymbolRows key={`s:${f}`} rows={info.symbols} path={f} depth={depth + 2} current={loc.symbol} onGo={onGo} />);
    }
    if (others) rows.push(<div key={`o:${path}`} className="trow other" style={{ paddingLeft: 26 + depth * 14 }}>+ {others} other file{others === 1 ? "" : "s"}</div>);
    return rows;
  };

  return (
    <nav className="tree" aria-label="Project files">
      <div className="find">
        <input ref={findRef} type="search" value={query} placeholder="Find a file or function" spellCheck={false}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits[0]) { onGo(hits[0].path, hits[0].symbol); setQuery(""); }
            if (e.key === "Escape") { setQuery(""); (e.target as HTMLInputElement).blur(); }
          }} />
        <kbd>/</kbd>
      </div>
      <div className="trows">
        {query.trim() ? (
          hits.length ? hits.map((h) => (
            <button key={`${h.path}::${h.symbol}`} className={`trow hit k-${h.kind}`} onClick={() => { onGo(h.path, h.symbol); setQuery(""); }}>
              <span className="mark" aria-hidden>{MARK[h.kind]}</span>
              <span className="hit-text"><span className="tname">{h.name}</span><span className="where">{h.symbol ? h.where : h.where.split("/").slice(0, -1).join("/") || "project root"}</span></span>
            </button>
          )) : <p className="none">Nothing matches “{query.trim()}”.</p>
        ) : (
          <>
            <button className={`trow root${loc.path === "" ? " sel" : ""}`} onClick={() => onGo("")}>
              <span className="tname">{index.name}</span>
            </button>
            {renderDir("", 0)}
          </>
        )}
      </div>
    </nav>
  );
}));
