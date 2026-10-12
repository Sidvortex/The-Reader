import { memo, useEffect, useRef, useState } from "react";
import {
  Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import type { Analysis, Call, FlowNodeKind, Sym } from "./types";
import { chipLabel, layoutFlow, layoutMap, maybeEntry, symSub, type Ghost } from "./layout";
import { boundsOf, edgeTypes, flowSettings } from "./graphParts";

type Nav = {
  onOpen: (id: string) => void; // select a symbol (map) or follow a call (flow)
  onLines: (range: [number, number] | null) => void;
  /** repo mode: jump to a function in another file */
  onOpenExternal?: (file: string, symbol: string) => void;
};

/* ---------- nodes ---------- */
const KIND_GLYPH: Partial<Record<FlowNodeKind, string>> = {
  loop: "↻", return: "↩", raise: "⚠", try: "⛉", except: "✕", finally: "◇", context: "⧉",
};

type FlowData = { kind: FlowNodeKind; label: string; start_line: number; end_line: number; calls?: Call[]; nav: Nav; active: boolean };

const FlowNodeView = memo(function FlowNodeView({ data }: NodeProps<Node<FlowData>>) {
  const { kind, label, calls, nav, active } = data;
  const lines = kind === "start" || kind === "end" || kind === "raise_end" ? null : `L${data.start_line}${data.end_line !== data.start_line ? `–${data.end_line}` : ""}`;
  return (
    <div
      className={`fnode k-${kind}${active ? " active" : ""}`}
      title={lines ? `Lines ${lines.slice(1)}. Click to highlight in code.` : undefined}
      onClick={() => lines && nav.onLines(active ? null : [data.start_line, data.end_line])}
    >
      <Handle type="target" position={Position.Top} className="h" />
      {kind === "decision" && (
        <svg className="hex" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
          <polygon points="6,1 94,1 99.5,50 94,99 6,99 0.5,50" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      <div className="fnode-inner">
        <div className="fnode-label">
          {KIND_GLYPH[kind] && <span className="glyph" aria-hidden>{KIND_GLYPH[kind]}</span>}
          <span>{label}</span>
        </div>
        {calls && calls.length > 0 && (
          <div className="fnode-calls">
            {calls.map((c) => c.xfile ? (
              <button key={chipLabel(c)} className="call-chip ext nodrag" title={`Go to ${c.xsymbol} in ${c.xfile}`}
                onClick={(e) => { e.stopPropagation(); nav.onOpenExternal?.(c.xfile!, c.xsymbol!); }}>
                ↳ {chipLabel(c)}
              </button>
            ) : (
              <button key={c.target} className="call-chip nodrag" onClick={(e) => { e.stopPropagation(); nav.onOpen(c.target!); }}
                title={`Follow the call into ${c.target}`}>
                ↳ {c.target}
              </button>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="h" />
    </div>
  );
});

type SymData = { sym: Sym; label: string; onTrail: boolean; selected: boolean; nav: Nav; dead: boolean; extIn?: number };

const SymNodeView = memo(function SymNodeView({ data }: NodeProps<Node<SymData>>) {
  const { sym, label, onTrail, selected, nav, dead, extIn } = data;
  const heat = Math.min(1, (sym.complexity - 1) / 12);
  const { text: sub, pill } = symSub(sym); // the layout sized the box for exactly this line
  return (
    <div className={`snode k-${sym.kind}${onTrail ? " trail" : ""}${selected ? " selected" : ""}`} onClick={() => nav.onOpen(sym.id)}
      title={`${sym.qualname}, complexity ${sym.complexity}${dead ? ". Nothing in this file calls it" : ""}${selected && sym.kind !== "class" ? ". Click again for its flowchart" : ""}`}>
      <Handle type="target" position={Position.Left} className="h" />
      <div className="snode-name">{label}</div>
      <div className="snode-sub">
        <span>{sub}</span>
        {pill && (extIn
          ? <span className="pill ext" title="Called from other files; they're listed beside the code">{pill}</span>
          : <span className="pill">{pill}</span>)}
        <span className="heat" style={{ ["--heat" as any]: heat }} aria-label={`complexity ${sym.complexity}`}><i /></span>
      </div>
      <Handle type="source" position={Position.Right} className="h" />
    </div>
  );
});

type GhostData = { ghost: Ghost; nav: Nav };

/** A function in another file: faded, and clicking it opens that file. */
const GhostNodeView = memo(function GhostNodeView({ data }: NodeProps<Node<GhostData>>) {
  const { ghost, nav } = data;
  return (
    <div className={`snode ghost ${ghost.dir}`} onClick={() => nav.onOpenExternal?.(ghost.file, ghost.symbol)}
      title={`${ghost.dir === "in" ? "Calls into this file from" : "Called from this file:"} ${ghost.symbol} in ${ghost.file}. Click to open it.`}>
      <Handle type="target" position={Position.Left} className="h" />
      <div className="snode-name">{ghost.label}</div>
      <div className="snode-sub"><span>{ghost.sub}</span></div>
      <Handle type="source" position={Position.Right} className="h" />
    </div>
  );
});

const nodeTypes = { flow: FlowNodeView, sym: SymNodeView, ghost: GhostNodeView };

/* ---------- canvas ---------- */
type Props = {
  analysis: Analysis;
  view: "map" | "flow";
  selected: string | null;
  trail: string[];
  lines: [number, number] | null;
} & Nav;

function Inner({ analysis, view, selected, trail, lines, onOpen, onLines, onOpenExternal }: Props) {
  const [graph, setGraph] = useState<{ nodes: Node[]; edges: Edge[]; key: string } | null>(null);
  const { fitView, setViewport } = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  const flowSym = view === "flow" ? selected : null;
  const fc = flowSym ? analysis.flowcharts[flowSym] : undefined;

  useEffect(() => {
    let live = true;
    const key = `${analysis.file.hash}:${view}:${view === "flow" ? flowSym : trail.join(",")}`;
    const job = view === "flow" ? (fc ? layoutFlow(fc, analysis, flowSym!) : Promise.resolve(null)) : layoutMap(analysis, trail);
    job.then((g) => live && setGraph(g ? { ...g, key } : null));
    return () => { live = false; };
  }, [analysis, view, flowSym, fc, trail]);

  useEffect(() => {
    if (!graph || !wrap.current) return;
    const t = setTimeout(() => {
      const W = wrap.current!.clientWidth, H = wrap.current!.clientHeight;
      const b = boundsOf(graph.nodes);
      const fit = Math.min(W / (b.width + 80), H / (b.height + 80));
      const sel = view === "map" && selected ? graph.nodes.find((n) => n.id === selected) : undefined;
      if (sel) {
        const z = Math.max(0.8, Math.min(1, fit));
        const cx = sel.position.x + ((sel.style?.width as number) ?? 200) / 2;
        const cy = sel.position.y + ((sel.style?.height as number) ?? 62) / 2;
        setViewport({ x: W / 2 - cx * z, y: H / 2 - cy * z, zoom: z }, { duration: 350 });
        return;
      }
      if (fit >= 0.85) {
        fitView({ duration: 300, maxZoom: 1.05, padding: 0.1 });
        return;
      }
      // Too big to read when fitted: keep text legible and start at the entry point.
      const z = Math.min(1, Math.max(0.85, (W - 80) / b.width));
      const anchor = graph.nodes.find((n) => (n.data as any).kind === "start") ?? graph.nodes[0];
      const cx = b.width * z <= W - 40 ? b.x + b.width / 2 : anchor.position.x + ((anchor.style?.width as number) ?? 100) / 2;
      if (view === "flow") setViewport({ x: W / 2 - cx * z, y: 28 - b.y * z, zoom: z }, { duration: 300 });
      else {
        // big file: start where reading starts
        const callers = new Set(analysis.call_edges.map((e) => e.from));
        const first = analysis.reading_order.filter((id) => callers.has(id)).map((id) => graph.nodes.find((n) => n.id === id)).find(Boolean) ?? graph.nodes[0];
        const fy = first.position.y + ((first.style?.height as number) ?? 62) / 2;
        setViewport({ x: 40 - first.position.x * z, y: H / 2 - fy * z, zoom: z }, { duration: 300 });
      }
    }, 30);
    return () => clearTimeout(t);
    // refit only when the graph itself or the selection changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph?.key, view === "map" ? selected : null]);

  if (view === "flow" && !fc) {
    const sym = analysis.symbols.find((s) => s.id === selected);
    return (
      <div className="canvas-empty">
        {sym?.kind === "class"
          ? <p>Classes don't have a flow of their own. Pick one of its methods in the outline.</p>
          : <p>Pick a function in the outline or the map to see its flowchart.</p>}
      </div>
    );
  }
  if (!graph) return <div className="canvas-empty"><p>Laying out…</p></div>;

  const nav: Nav = { onOpen, onLines, onOpenExternal };
  const dead = new Set(analysis.symbols.filter(maybeEntry).map((s) => s.id));
  const nodes = graph.nodes.map((n) =>
    n.type === "flow"
      ? { ...n, data: { ...n.data, nav, active: !!lines && (n.data as any).start_line === lines[0] && (n.data as any).end_line === lines[1] } }
      : n.type === "ghost"
        ? { ...n, data: { ...n.data, nav } }
        : { ...n, data: { ...n.data, nav, selected: n.id === selected, dead: dead.has(n.id) } });

  return (
    <div ref={wrap} style={{ width: "100%", height: "100%" }}>
    <ReactFlow nodes={nodes} edges={graph.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} {...flowSettings}>
      <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} className="bg-dots" />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
    </div>
  );
}

export default function Canvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
