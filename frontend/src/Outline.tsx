import { memo, useMemo, useState } from "react";
import type { Analysis, Sym } from "./types";

type Props = {
  analysis: Analysis;
  selected: string | null;
  visited: Set<string>;
  onSelect: (id: string) => void;
};

const MARK: Record<Sym["kind"], string> = { module: "▸", class: "C", function: "ƒ", method: "m" };

function Row({ s, depth, selected, visited, onSelect, step }: {
  s: Sym; depth: number; selected: boolean; visited: boolean; onSelect: (id: string) => void; step?: number;
}) {
  const label = s.kind === "module" ? "module code" : s.name;
  return (
    <button
      className={`orow k-${s.kind}${selected ? " sel" : ""}${visited ? " seen" : ""}`}
      style={{ paddingLeft: 12 + depth * 16 }}
      onClick={() => onSelect(s.id)}
      title={`${s.qualname} · lines ${s.start_line}–${s.end_line}`}
    >
      {step !== undefined ? <span className="step">{step}</span> : <span className="mark" aria-hidden>{MARK[s.kind]}</span>}
      <span className="oname">{label}</span>
      {step !== undefined && s.parent && s.qualname !== s.name && (
        <span className="ocls">{s.qualname.slice(0, -s.name.length - 1)}</span>
      )}
      {s.kind !== "class" && s.complexity >= 6 && <span className="cx" title={`complexity ${s.complexity}`}>{s.complexity}</span>}
    </button>
  );
}

export default memo(function Outline({ analysis, selected, visited, onSelect }: Props) {
  const [mode, setMode] = useState<"file" | "read">("read");
  const byId = useMemo(() => new Map(analysis.symbols.map((s) => [s.id, s])), [analysis]);
  const tree = useMemo(() => {
    const rows: { s: Sym; depth: number }[] = [];
    const walk = (s: Sym, depth: number) => {
      rows.push({ s, depth });
      s.children.map((c) => byId.get(c)!).sort((a, b) => a.start_line - b.start_line).forEach((c) => walk(c, depth + 1));
    };
    const mod = byId.get("<module>")!;
    if (analysis.flowcharts["<module>"]) rows.push({ s: mod, depth: 0 });
    mod.children.map((c) => byId.get(c)!).sort((a, b) => a.start_line - b.start_line).forEach((c) => walk(c, 0));
    return rows;
  }, [analysis, byId]);

  return (
    <nav className="outline" aria-label="File outline">
      <div className="seg" role="tablist">
        <button role="tab" aria-selected={mode === "read"} onClick={() => setMode("read")}>Reading order</button>
        <button role="tab" aria-selected={mode === "file"} onClick={() => setMode("file")}>File order</button>
      </div>
      <div className="orows">
        {mode === "file"
          ? tree.map(({ s, depth }) => (
              <Row key={s.id} s={s} depth={depth} selected={s.id === selected} visited={visited.has(s.id)} onSelect={onSelect} />
            ))
          : analysis.reading_order.map((id, i) => {
              const s = byId.get(id)!;
              return <Row key={id} s={s} depth={0} step={i + 1} selected={id === selected} visited={visited.has(id)} onSelect={onSelect} />;
            })}
      </div>
      {analysis.imports.length > 0 && (
        <details className="imports">
          <summary>Imports ({analysis.imports.length})</summary>
          <ul>
            {analysis.imports.map((im, i) => (
              <li key={i}>
                <code>{im.module}</code>
                {im.names.length > 0 && <span> {im.names.map((n) => n.alias ? `${n.name} as ${n.alias}` : n.name).join(", ")}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </nav>
  );
});
