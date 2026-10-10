import { memo } from "react";
import type { Edge, EdgeProps, Node } from "@xyflow/react";
import type { ElkEdgeData, Pt } from "./layout";

/** Orthogonal path with softened corners. */
export function roundedPath(pts: Pt[], r = 8) {
  if (pts.length < 2) return "";
  let d = `M${pts[0].x},${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i - 1], c = pts[i], n = pts[i + 1];
    const d1 = Math.hypot(c.x - p.x, c.y - p.y), d2 = Math.hypot(n.x - c.x, n.y - c.y);
    const rr = Math.min(r, d1 / 2, d2 / 2);
    const a = { x: c.x - ((c.x - p.x) / d1) * rr, y: c.y - ((c.y - p.y) / d1) * rr };
    const b = { x: c.x + ((n.x - c.x) / d2) * rr, y: c.y + ((n.y - c.y) / d2) * rr };
    d += ` L${a.x},${a.y} Q${c.x},${c.y} ${b.x},${b.y}`;
  }
  const last = pts[pts.length - 1];
  return d + ` L${last.x},${last.y}`;
}

export const ElkEdge = memo(function ElkEdge({ data, markerEnd }: EdgeProps<Edge<ElkEdgeData>>) {
  if (!data?.points?.length) return null;
  const cls = ["elk-edge", data.back && "back", data.hot && "hot", data.tone].filter(Boolean).join(" ");
  const style = data.weight ? ({ ["--w" as any]: data.weight } as React.CSSProperties) : undefined;
  return (
    <g className={cls} style={style}>
      {data.hot && <path d={roundedPath(data.points)} className="hot-under" />}
      <path d={roundedPath(data.points)} markerEnd={markerEnd} className="line" />
      {data.label && data.labelPos && (
        <foreignObject x={data.labelPos.x - 60} y={data.labelPos.y - 10} width={120} height={20} className="edge-label-fo">
          <div className="edge-label"><span>{data.label}</span></div>
        </foreignObject>
      )}
    </g>
  );
});

/** Two points joined by an S-curve; used for links that appear on hover. */
export const CurveEdge = memo(function CurveEdge({ data, markerEnd }: EdgeProps<Edge<ElkEdgeData>>) {
  if (!data?.points || data.points.length < 2) return null;
  const [s, t] = data.points;
  let d: string;
  if (data.orient === "v") {
    const dy = Math.max(30, Math.abs(t.y - s.y) / 2) * (t.y >= s.y ? 1 : -1);
    d = `M${s.x},${s.y} C${s.x},${s.y + dy} ${t.x},${t.y - dy} ${t.x},${t.y}`;
  } else {
    const dx = Math.max(40, Math.abs(t.x - s.x) / 2) * (t.x >= s.x ? 1 : -1);
    d = `M${s.x},${s.y} C${s.x + dx},${s.y} ${t.x - dx},${t.y} ${t.x},${t.y}`;
  }
  const cls = ["elk-edge", "curve", data.hot && "hot", data.tone].filter(Boolean).join(" ");
  const style = data.weight ? ({ ["--w" as any]: data.weight } as React.CSSProperties) : undefined;
  return (
    <g className={cls} style={style}>
      <path d={d} markerEnd={markerEnd} className="line" />
    </g>
  );
});

export const edgeTypes = { elk: ElkEdge, curve: CurveEdge };

/** Settings shared by every diagram: read-only, pannable, zoomable. */
export const flowSettings = {
  defaultEdgeOptions: { markerEnd: { type: "arrowclosed" as any, width: 14, height: 14, color: "var(--edge)" } },
  nodesConnectable: false,
  nodesDraggable: false,
  elementsSelectable: false,
  minZoom: 0.15,
  maxZoom: 2,
  proOptions: { hideAttribution: true },
  // reading tool: the wheel scrolls like a page; pinch or Ctrl/Cmd + wheel zooms
  panOnScroll: true,
  zoomOnScroll: false,
  zoomActivationKeyCode: ["Meta", "Control"] as string[],
} as const;

export const stem = (path: string) => path.split("/").pop()!.replace(/\.pyi?$/, "");
export const base = (path: string) => path.split("/").pop()!;

/** Bounding box of laid-out nodes, using the sizes the layout gave them. */
export function boundsOf(nodes: Node[]) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    const w = (n.style?.width as number) ?? 0, h = (n.style?.height as number) ?? 0;
    x0 = Math.min(x0, n.position.x); y0 = Math.min(y0, n.position.y);
    x1 = Math.max(x1, n.position.x + w); y1 = Math.max(y1, n.position.y + h);
  }
  if (!nodes.length) return { x: 0, y: 0, width: 0, height: 0 };
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}
