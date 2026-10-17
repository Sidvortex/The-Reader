import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import Code from "./Code";
import Markdown from "./Markdown";
import { useExplanation } from "./explanations";
import { base, stem } from "./graphParts";
import type { Analysis, Explanation } from "./types";

type Props = {
  analysis: Analysis;
  selected: string | null;
  lines: [number, number] | null;
  ai: boolean;
  onOpen: (id: string) => void;
  /** repo mode: explanations that know about the rest of the project */
  explain?: (symbol: string | null, ai: boolean) => Promise<Explanation>;
  /** repo mode: open a file (and optionally a function in it) */
  onOpenExternal?: (file: string, symbol: string | null) => void;
  /** repo mode: turn a `path` in an explanation into a link */
  linkPath?: (name: string) => ReactNode | null;
};

const label = (id: string) => (id === "<module>" ? "module code" : id);

export default memo(function DetailPanel({ analysis, selected, lines, ai, onOpen, explain, onOpenExternal, linkPath }: Props) {
  const sym = selected ? analysis.symbols.find((s) => s.id === selected) ?? null : null;
  const [wholeFile, setWholeFile] = useState(false);
  const symbolKey = sym ? sym.id : "<file>";
  const { structural, ai: aiEx, loading, error } = useExplanation(
    `${analysis.file.path ?? ""}#${analysis.file.hash}#${symbolKey}`,
    (useAi) => (explain ? explain(sym ? sym.id : null, useAi) : api.explain(analysis.file.hash, symbolKey, useAi)),
    ai,
  );
  const ex = aiEx ?? structural;
  useEffect(() => setWholeFile(false), [selected]);

  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of analysis.symbols) {
      if (s.kind === "module") continue;
      m.set(s.qualname, s.id);
      if (!m.has(s.name)) m.set(s.name, s.id);
    }
    return m;
  }, [analysis]);

  const link = (name: string) => {
    const id = names.get(name.replace(/\(.*\)$/, ""));
    if (id) return <button className="sym-link" onClick={() => onOpen(id)}>{name}</button>;
    return linkPath?.(name) ?? null;
  };

  const from = !sym || wholeFile || sym.kind === "module" ? 1 : sym.start_line;
  const to = !sym || wholeFile || sym.kind === "module" ? analysis.file.lines : sym.end_line;
  const internal = sym ? [...new Set(sym.calls.filter((c) => c.target).map((c) => c.target!))] : [];
  const outward = sym ? [...new Map(sym.calls.filter((c) => c.xfile).map((c) => [`${c.xfile}::${c.xsymbol}`, c])).values()] : [];
  const inward = sym?.called_by_ext ?? [];
  const own = new Set(analysis.repo?.own_packages ?? []);
  const libs = sym ? [...new Set(sym.calls.filter((c) => c.kind === "external" && !c.xfile && c.module && !c.module.startsWith("."))
    .map((c) => c.module!.split(".")[0]))].filter((m) => !own.has(m)) : [];
  const repo = analysis.repo;
  const title = analysis.file.path ?? analysis.file.name;

  const fileChip = (f: string) => (
    <button key={f} className="chip" title={f} onClick={() => onOpenExternal?.(f, null)}>{base(f) === "__init__.py" ? f.split("/").slice(-2).join("/") : base(f)}</button>
  );

  return (
    <aside className="detail" aria-label="Explanation and code">
      <header className="detail-head">
        {sym ? (
          <>
            <div className="kicker">{sym.kind === "module" ? "Module code" : `${sym.is_async ? "async " : ""}${sym.kind}`}{sym.kind !== "module" && <span>, lines {sym.start_line}–{sym.end_line}</span>}</div>
            <h2>
              {sym.kind === "module" ? analysis.file.name : sym.qualname}
              {(sym.kind === "function" || sym.kind === "method") && <span className="sig">({sym.params.join(", ")}){sym.returns ? ` → ${sym.returns}` : ""}</span>}
            </h2>
            {sym.decorators.length > 0 && <div className="decos">{sym.decorators.map((d) => <code key={d}>@{d}</code>)}</div>}
          </>
        ) : (
          <>
            <div className="kicker">File overview</div>
            <h2 title={title}>{analysis.file.name}</h2>
            {repo && title !== analysis.file.name && <div className="path-line">{title}</div>}
          </>
        )}
      </header>

      <section className="explain">
        {ex && <Markdown md={ex.markdown} link={link} />}
        {loading && <p className="thinking">Writing a plain-English explanation…</p>}
        {error && <p className="err">{error}</p>}
        {ex && !loading && <p className="source-note">{ex.source === "ai" ? "Written by AI" : "Summary worked out from the code"}</p>}
      </section>

      {sym && (internal.length > 0 || sym.called_by.length > 0 || outward.length > 0 || inward.length > 0 || libs.length > 0) && (
        <section className="relations">
          {(sym.called_by.length > 0 || inward.length > 0) && (
            <div>
              <span className="rel-label">Called by</span>
              {sym.called_by.map((id) => <button key={id} className="chip" onClick={() => onOpen(id)}>{label(id)}</button>)}
              {inward.slice(0, 12).map((c) => (
                <button key={`${c.file}::${c.symbol}`} className="chip xf" title={`${c.symbol} in ${c.file}`}
                  onClick={() => onOpenExternal?.(c.file, c.symbol)}>
                  {stem(c.file)}.{label(c.symbol)}
                </button>
              ))}
              {inward.length > 12 && <span className="more">and {inward.length - 12} more</span>}
            </div>
          )}
          {(internal.length > 0 || outward.length > 0) && (
            <div>
              <span className="rel-label">Calls</span>
              {internal.map((id) => <button key={id} className="chip" onClick={() => onOpen(id)}>{id}</button>)}
              {outward.map((c) => (
                <button key={`${c.xfile}::${c.xsymbol}`} className="chip xf" title={`${c.xsymbol} in ${c.xfile}`}
                  onClick={() => onOpenExternal?.(c.xfile!, c.xsymbol!)}>
                  {stem(c.xfile!)}.{c.xsymbol}
                </button>
              ))}
            </div>
          )}
          {libs.length > 0 && (
            <div><span className="rel-label">Uses</span>{libs.map((m) => <code key={m} className="chip ext">{m}</code>)}</div>
          )}
        </section>
      )}
      {!sym && repo && (repo.used_by.length > 0 || repo.uses.length > 0) && (
        <section className="relations">
          {repo.used_by.length > 0 && (
            <div><span className="rel-label">Used by</span>{repo.used_by.slice(0, 14).map(fileChip)}
              {repo.used_by.length > 14 && <span className="more">and {repo.used_by.length - 14} more</span>}</div>
          )}
          {repo.uses.length > 0 && (
            <div><span className="rel-label">Uses</span>{repo.uses.slice(0, 14).map(fileChip)}
              {repo.uses.length > 14 && <span className="more">and {repo.uses.length - 14} more</span>}</div>
          )}
        </section>
      )}

      <div className="code-head">
        <span>{from === 1 && to === analysis.file.lines ? "Whole file" : `Lines ${from}–${to}`}</span>
        {sym && sym.kind !== "module" && (
          <button className="link-btn" onClick={() => setWholeFile((w) => !w)}>{wholeFile ? "Show only this" : "Show whole file"}</button>
        )}
      </div>
      <Code analysis={analysis} from={from} to={to} highlight={lines} onFollow={onOpen}
        onFollowExternal={onOpenExternal as ((f: string, s: string) => void) | undefined} />
    </aside>
  );
});
