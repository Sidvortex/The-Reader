import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import { curveBetween, layoutFolder, type FolderGraph, type FolderItem } from "./layout";
import { boundsOf, edgeTypes, flowSettings } from "./graphParts";
import type { RepoIndex } from "./types";

type BoxData = { item: FolderItem; onGo: (path: string) => void; faded?: boolean; lit?: boolean };

const BoxNode = memo(function BoxNode({ data }: NodeProps<Node<BoxData>>) {
  const { item, onGo, faded, lit } = data;
  const tip = item.kind === "ghost"
    ? `${item.path} is outside this folder. Click to go there.`
    : `${item.path}${item.usedBy ? `, used by ${item.usedBy} file${item.usedBy === 1 ? "" : "s"}` : ""}. Click to open.`;
  return (
    <div className={`fbox k-${item.kind}${item.dim ? " dim" : ""}${faded ? " faded" : ""}${lit ? " lit" : ""}`}
      onClick={() => onGo(item.path)} title={tip}>
      <Handle type="target" position={Position.Left} className="h" />
      <div className="fbox-name">
        <span className="fbox-label">{item.label}</span>
        {item.entry && <span className="pill start">start here</span>}
        {!!item.usedBy && item.kind === "file" && <span className="fbox-used" aria-label={`used by ${item.usedBy}`}>{item.usedBy}</span>}
      </div>
      <div className="fbox-sub">{item.sub}</div>
      <Handle type="source" position={Position.Right} className="h" />
    </div>
  );
});

const nodeTypes = { box: BoxNode };

type Props = { index: RepoIndex; dir: string; onGo: (path: string) => void };

function Inner({ index, dir, onGo }: Props) {
  const [graph, setGraph] = useState<(FolderGraph & { key: string }) | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const { fitView, setViewport } = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setHover(null);
    const width = wrap.current?.clientWidth ?? 900;
    layoutFolder(index, dir, width).then((g) => live && setGraph({ ...g, key: `${index.id}:${dir}` }));
    return () => { live = false; };
  }, [index, dir]);

  useEffect(() => {
    if (!graph || !graph.nodes.length || !wrap.current) return;
    const t = setTimeout(() => {
      const W = wrap.current!.clientWidth, H = wrap.current!.clientHeight;
      const b = boundsOf(graph.nodes);
      const fit = Math.min(W / (b.width + 80), H / (b.height + 100));
      if (fit >= 0.95) fitView({ duration: 300, maxZoom: 1, padding: 0.12 });
      else {
        // taller than the screen: read it like a page, from the top
        const z = Math.min(1, Math.max(0.85, (W - 60) / (b.width + 60)));
        setViewport({ x: (W - b.width * z) / 2 - b.x * z, y: 56 - b.y * z, zoom: z }, { duration: 300 });
      }
    }, 30);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph?.key]);

  // which links to draw: everything in a sparse folder; in a busy one, the strongest link per box,
  // and every link of the box under the pointer
  const { edges, neighbours } = useMemo(() => {
    if (!graph) return { edges: [] as Edge[], neighbours: null as Set<string> | null };
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    const maxW = Math.max(1, ...graph.pairs.map((p) => p.w));
    const curve = (p: FolderGraph["pairs"][number], hot: boolean): Edge | null => {
      const a = byId.get(p.from), b = byId.get(p.to);
      if (!a || !b) return null;
      const { points, orient } = curveBetween(a, b);
      return { id: `c:${p.from}>${p.to}`, source: p.from, target: p.to, type: "curve",
               data: { points, orient, hot, tone: p.ghost ? "ghost" : undefined,
                       weight: 1 + Math.round((p.w / maxW) * 3) } } as Edge;
    };
    if (hover) {
      const mine = graph.pairs.filter((p) => p.from === hover || p.to === hover);
      const n = new Set<string>([hover, ...mine.flatMap((p) => [p.from, p.to])]);
      return { edges: mine.map((p) => curve(p, true)).filter(Boolean) as Edge[], neighbours: n };
    }
    if (!graph.dense) {
      return { edges: graph.pairs.map((p) => curve(p, false)).filter(Boolean) as Edge[], neighbours: null };
    }
    const strongest = new Map<string, FolderGraph["pairs"][number]>();
    for (const p of graph.pairs) {
      if (p.ghost) continue;
      const cur = strongest.get(p.from);
      if (!cur || p.w > cur.w) strongest.set(p.from, p);
    }
    return {
      edges: [...strongest.values()].map((p) => ({ ...curve(p, false)!, className: "faint" })),
      neighbours: null,
    };
  }, [graph, hover]);

  if (!graph || graph.key !== `${index.id}:${dir}`) return <div ref={wrap} className="canvas-empty"><p>Laying out…</p></div>;
  if (!graph.items.length) {
    const st = index.dirs[dir].stats;
    return (
      <div className="canvas-empty">
        <p>Nothing here The Reader can read. This folder has {st.files.toLocaleString()} file{st.files === 1 ? "" : "s"} in other
          languages or formats.</p>
      </div>
    );
  }
  const nodes = graph.nodes.map((n) => ({
    ...n,
    data: { ...n.data, onGo, faded: !!neighbours && !neighbours.has(n.id), lit: hover === n.id },
  }));
  return (
    <div ref={wrap} className={`folder-map${graph.dense ? " dense" : ""}`} style={{ width: "100%", height: "100%" }}>
      {graph.dense && !hover && <p className="map-hint">Showing each box's strongest link. Point at a box to see all of its connections.</p>}
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} {...flowSettings}
        onNodeMouseEnter={(_, n) => setHover(n.id)} onNodeMouseLeave={() => setHover(null)}>
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} className="bg-dots" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
    </div>
  );
}

/** A folder's subfolders and files of code as boxes; arrows point from a file to what it uses. */
export default function FolderMap(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
