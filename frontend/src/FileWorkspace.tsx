import { useCallback, useEffect, useMemo, useState } from "react";
import Canvas from "./Canvas";
import DetailPanel from "./DetailPanel";
import Outline from "./Outline";
import type { Analysis } from "./types";

/** Single-file mode: outline, call map or flowchart, and the explanation beside the code. */
export default function FileWorkspace({ analysis, ai }: { analysis: Analysis; ai: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [trail, setTrail] = useState<string[]>([]);
  const [visited, setVisited] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"map" | "flow">("map");
  const [lines, setLines] = useState<[number, number] | null>(null);

  useEffect(() => {
    setSelected(null); setTrail([]); setVisited(new Set()); setLines(null); setView("map");
  }, [analysis]);

  /** Navigate to a symbol. Following a call keeps you in the same view and extends the trail. */
  const open = useCallback((id: string) => {
    if (!analysis.symbols.some((s) => s.id === id)) return;
    setSelected(id);
    setLines(null);
    setVisited((v) => new Set(v).add(id));
    setTrail((t) => {
      const at = t.indexOf(id);
      return at >= 0 ? t.slice(0, at + 1) : [...t, id];
    });
  }, [analysis]);

  const back = useCallback(() => {
    setTrail((t) => {
      if (t.length <= 1) { setSelected(null); return []; }
      const nt = t.slice(0, -1);
      setSelected(nt[nt.length - 1]);
      setLines(null);
      return nt;
    });
  }, []);

  const step = useCallback((dir: 1 | -1) => {
    const order = analysis.reading_order;
    const i = selected ? order.indexOf(selected) : -1;
    const next = order[Math.min(order.length - 1, Math.max(0, i + dir))];
    if (next) open(next);
  }, [analysis, selected, open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "m") setView("map");
      else if (e.key === "f") setView("flow");
      else if (e.key === "j") step(1);
      else if (e.key === "k") step(-1);
      else if (e.key === "Backspace" || e.key === "[") back();
      else if (e.key === "Escape") setLines(null);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, back]);

  const symName = useMemo(() => (id: string) => (id === "<module>" ? "module code" : id), []);

  return (
    <main className="workspace">
      <Outline analysis={analysis} selected={selected} visited={visited} onSelect={open} />
      <section className="stage">
        <div className="stage-bar">
          <div className="seg" role="tablist" aria-label="View">
            <button role="tab" aria-selected={view === "map"} onClick={() => setView("map")}>Call map</button>
            <button role="tab" aria-selected={view === "flow"} onClick={() => setView("flow")}>Flowchart</button>
          </div>
          <ol className="trail" aria-label="Your reading trail">
            {trail.length === 0 && <li className="hint">Your path through the code shows up here</li>}
            {trail.map((id, i) => (
              <li key={id}>
                <button className={id === selected ? "here" : ""} onClick={() => open(id)}>{symName(id)}</button>
                {i < trail.length - 1 && <span className="sep" aria-hidden>›</span>}
              </li>
            ))}
          </ol>
          {trail.length > 0 && <button className="ghost small" onClick={back} title="Back ([)">Back</button>}
        </div>
        <div className="canvas">
          <Canvas analysis={analysis} view={view} selected={selected} trail={trail} lines={lines}
            onOpen={(id) => { if (view === "map" && id === selected) setView("flow"); else open(id); }} onLines={setLines} />
        </div>
      </section>
      <DetailPanel analysis={analysis} selected={selected} lines={lines} ai={ai} onOpen={open} />
    </main>
  );
}
