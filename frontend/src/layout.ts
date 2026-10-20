import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";
import type { Analysis, Call, FlowChart, RepoIndex, Sym } from "./types";

/** What a file's top-level code is called: module code, component code, page scripts. */
export function moduleLabel(a: Analysis): string {
  if (a.file.component) return "component code";
  if (a.file.language === "html") return "page scripts";
  if (a.file.language === "css") return "whole stylesheet";
  return "module code";
}

const elk = new ELK();

export type Pt = { x: number; y: number };
export type ElkEdgeData = {
  points: Pt[]; label?: string | null; labelPos?: Pt; back?: boolean; hot?: boolean; tone?: string; weight?: number;
  /** curves only: vertical or horizontal S */
  orient?: "v" | "h";
};

// Fonts must match styles.css (.fnode, .call-chip, .edge-label, .snode-name, .snode-sub, .snode .pill).
export const FONTS = {
  flow: '13px "JetBrains Mono Variable"',
  chip: '500 12.5px "Lexend Variable"',
  edge: '12px "Lexend Variable"',
  mapName: '500 14.5px "Lexend Variable"',
  mapSub: '12.5px "Lexend Variable"',
  pill: '12px "Lexend Variable"',
};

/** Defined but never called from this file or (in repo mode) another one: maybe an entry point. */
export function maybeEntry(s: Sym): boolean {
  return s.kind !== "module" && s.called_by.length === 0 && !s.called_by_ext?.length && !s.name.startsWith("__")
    && !s.imported_by?.length && !s.name_used_in?.length && !s.referenced;
}

/** The second line of a call-map box, before its complexity bar. */
export function symSub(s: Sym): { text: string; pill: string | null } {
  const n = s.end_line - s.start_line + 1;
  const text = s.kind === "module" ? (s.role === "component" ? "template and script" : s.role === "page"
    ? "inline scripts" : "runs on import") : s.kind === "class" ? "class"
    : `${s.is_async ? "async " : ""}${n} line${n === 1 ? "" : "s"}`;
  const ext = s.called_by_ext?.length ?? 0;
  return { text, pill: ext ? `+${ext} outside` : maybeEntry(s) ? "start?" : null };
}

const fileStem = (path: string) => path.split("/").pop()!.replace(/\.(pyi?|[cm]?[jt]sx?|vue|svelte|html?|css)$/, "");

/** What a call chip in a flowchart says: the function in this file, or `module.function` in another file. */
export function chipLabel(c: Call): string {
  return c.xfile ? `${fileStem(c.xfile)}.${c.xsymbol}` : c.target!;
}
const LINE = 20;

const measureCtx = document.createElement("canvas").getContext("2d")!;
function textWidth(text: string, font: string) {
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}

let fontsLoaded: Promise<unknown> | null = null;
function fontsReady() {
  fontsLoaded ??= Promise.all(Object.values(FONTS).map((f) => document.fonts.load(f, "Ag_(){}")));
  return fontsLoaded;
}

function boxFor(label: string, kind: string) {
  const lines = label.split("\n");
  const longest = Math.max(...lines.map((l) => textWidth(l, FONTS.flow)));
  const pad = kind === "decision" ? 60 : 34;
  const glyph = ["loop", "return", "raise", "try", "except", "finally", "context"].includes(kind) ? 22 : 0;
  const width = Math.min(460, Math.max(kind === "start" || kind === "end" ? 90 : 140, Math.ceil(longest + pad + glyph)));
  const height = Math.max(42, lines.length * LINE + 22);
  return { width, height };
}

async function run(graph: any) {
  await fontsReady();
  return elk.layout(graph);
}

function edgesFromElk(laid: any, extra: (id: string) => Partial<ElkEdgeData> & { source: string; target: string }): Edge[] {
  return (laid.edges ?? []).map((e: any) => {
    const sec = e.sections?.[0];
    const points: Pt[] = sec ? [sec.startPoint, ...(sec.bendPoints ?? []), sec.endPoint] : [];
    const lab = e.labels?.[0];
    const meta = extra(e.id);
    return {
      id: e.id,
      source: meta.source,
      target: meta.target,
      type: "elk",
      data: {
        points,
        label: lab?.text ?? null,
        labelPos: lab ? { x: lab.x + lab.width / 2, y: lab.y + lab.height / 2 } : undefined,
        back: meta.back,
        hot: meta.hot,
        tone: meta.tone,
      } satisfies ElkEdgeData,
    } as Edge;
  });
}

/** Per-function flowchart, top to bottom. */
export async function layoutFlow(fc: FlowChart, a: Analysis, symId: string): Promise<{ nodes: Node[]; edges: Edge[] }> {
  await fontsReady();
  const sym = a.symbols.find((s) => s.id === symId)!;
  const calls = sym.calls.filter((c) => c.target || c.xfile);
  const children = fc.nodes.map((n) => {
    const nodeCalls = n.kind === "start" || n.kind === "end"
      ? []
      : [...new Map(calls.filter((c) => c.line >= n.start_line && c.line <= n.end_line).map((c) => [chipLabel(c), c])).values()];
    const size = boxFor(n.label, n.kind);
    if (nodeCalls.length) {
      size.height += 32;
      const chips = nodeCalls.reduce((w, c) => w + textWidth(`↳ ${chipLabel(c)}`, FONTS.chip) + 18 + 5, 0);
      size.width = Math.min(480, Math.max(size.width, Math.ceil(Math.min(chips, 440) + 30)));
    }
    const layoutOptions: Record<string, string> =
      n.kind === "start" ? { "elk.layered.layering.layerConstraint": "FIRST_SEPARATE" }
      : n.kind === "end" || n.kind === "raise_end" ? { "elk.layered.layering.layerConstraint": "LAST_SEPARATE" } : {};
    return { id: n.id, ...size, layoutOptions, _calls: nodeCalls };
  });
  // Loop-back edges stay out of the layout so the body lays out as a clean top-down tree;
  // they are drawn afterwards as a return lane on the right of the loop.
  const edges = fc.edges.flatMap((e, i) => e.back ? [] : [{
    id: `e${i}`,
    sources: [e.from],
    targets: [e.to],
    labels: e.label ? [{ text: e.label, width: Math.ceil(textWidth(e.label, FONTS.edge)) + 14, height: 20 }] : [],
  }]);
  const laid = await run({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "44",
      "elk.spacing.nodeNode": "36",
      "elk.spacing.edgeNode": "22",
      "elk.spacing.edgeEdge": "14",
      "elk.layered.spacing.edgeNodeBetweenLayers": "20",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.edgeLabels.inline": "true",
    },
    children: children.map(({ _calls, ...c }) => c),
    edges,
  });
  const byId = new Map(fc.nodes.map((n) => [n.id, n]));
  const callsById = new Map(children.map((c) => [c.id, c._calls]));
  const nodes: Node[] = laid.children!.map((c: any) => ({
    id: c.id,
    type: "flow",
    position: { x: c.x, y: c.y },
    data: { ...byId.get(c.id)!, calls: callsById.get(c.id), width: c.width, height: c.height },
    style: { width: c.width, height: c.height },
    draggable: false,
  }));
  const meta = new Map(fc.edges.map((e, i) => [`e${i}`, e]));
  const forward = edgesFromElk(laid, (id) => {
    const e = meta.get(id)!;
    const tone = e.label === "error" ? "danger" : e.label === "no" || e.label === "false" || e.label === "done" ? "muted" : undefined;
    return { source: e.from, target: e.to, back: false, tone };
  });
  return { nodes, edges: [...forward, ...loopBacks(fc, nodes)] };
}

function loopBacks(fc: FlowChart, nodes: Node[]): Edge[] {
  const box = new Map(nodes.map((n) => [n.id, { x: n.position.x, y: n.position.y, w: (n.data as any).width as number, h: (n.data as any).height as number }]));
  const lanesPerHead = new Map<string, number>();
  const fwd = new Map<string, string[]>(), rev = new Map<string, string[]>();
  for (const e of fc.edges) if (!e.back) {
    (fwd.get(e.from) ?? fwd.set(e.from, []).get(e.from)!).push(e.to);
    (rev.get(e.to) ?? rev.set(e.to, []).get(e.to)!).push(e.from);
  }
  const reach = (start: string, adj: Map<string, string[]>) => {
    const seen = new Set([start]); const q = [start];
    while (q.length) for (const n of adj.get(q.pop()!) ?? []) if (!seen.has(n)) { seen.add(n); q.push(n); }
    return seen;
  };
  const loopBody = (head: string, src: string) => {
    const down = reach(head, fwd), up = reach(src, rev);
    return [...down].filter((n) => up.has(n));
  };
  const out: Edge[] = [];
  fc.edges.forEach((e, i) => {
    if (!e.back) return;
    const s = box.get(e.from), t = box.get(e.to);
    if (!s || !t) return;
    const lane = lanesPerHead.get(e.to) ?? 0;
    lanesPerHead.set(e.to, lane + 1);
    // the lane runs just right of the loop's own body: nodes on a forward path head → … → source
    const body = loopBody(e.to, e.from);
    let right = Math.max(s.x + s.w, t.x + t.w);
    for (const id of body) { const b = box.get(id); if (b) right = Math.max(right, b.x + b.w); }
    const laneX = right + 18 + lane * 10;
    const sy = s.y + s.h / 2 + Math.min(6, s.h / 4);
    const ty = t.y + t.h / 2 - 4 + lane * 4;
    const points: Pt[] = [
      { x: s.x + s.w, y: sy }, { x: laneX, y: sy }, { x: laneX, y: ty }, { x: t.x + t.w, y: ty },
    ];
    out.push({
      id: `b${i}`, source: e.from, target: e.to, type: "elk",
      data: { points, back: true, label: e.label, labelPos: e.label ? { x: laneX, y: (sy + ty) / 2 } : undefined } satisfies ElkEdgeData,
    } as Edge);
  });
  return out;
}

export function mapSymbols(a: Analysis): Sym[] {
  const targeted = new Set(a.call_edges.map((e) => e.to));
  return a.symbols.filter((s) =>
    s.kind === "function" || s.kind === "method" ||
    (s.kind === "module" && (s.calls.some((c) => c.target) || a.flowcharts["<module>"])) ||
    (s.kind === "class" && targeted.has(s.id)));
}

export type Ghost = { id: string; file: string; symbol: string; label: string; sub: string; dir: "in" | "out"; count: number };

/** Functions in other files that this file calls, for repo mode. (Callers from elsewhere are a count on the box.) */
export function ghostsFor(a: Analysis, syms: Sym[]): { ghosts: Ghost[]; edges: { from: string; to: string }[] } {
  const out = new Map<string, Ghost>();
  const edges: { from: string; to: string }[] = [];
  const mk = (dir: "in" | "out", file: string, symbol: string): Ghost => ({
    id: `${dir}:${file}::${symbol}`, file, symbol, dir, count: 0,
    label: symbol === "<module>" ? (/\.(vue|svelte)$/.test(file) ? `<${fileStem(file)}>` : `${fileStem(file)} (module code)`) : symbol,
    sub: file.split("/").slice(-2).join("/"),
  });
  for (const s of syms) {
    for (const c of s.calls) {
      if (!c.xfile || !c.xsymbol) continue;
      const g = out.get(`${c.xfile}::${c.xsymbol}`) ?? mk("out", c.xfile, c.xsymbol);
      out.set(`${c.xfile}::${c.xsymbol}`, g);
      g.count++;
      edges.push({ from: s.id, to: g.id });
    }
  }
  const keepOut = [...out.values()].sort((x, y) => y.count - x.count).slice(0, 12);
  const kept = new Set(keepOut.map((g) => g.id));
  const seen = new Set<string>();
  return {
    ghosts: keepOut,
    edges: edges.filter((e) => (kept.has(e.from) || kept.has(e.to)) && !seen.has(`${e.from}>${e.to}`) && seen.add(`${e.from}>${e.to}`)),
  };
}

/** Whole-file call map, left to right: callers before callees. In repo mode, other files appear as ghosts. */
export async function layoutMap(a: Analysis, trail: string[]): Promise<{ nodes: Node[]; edges: Edge[] }> {
  await fontsReady();
  const syms = mapSymbols(a);
  const ids = new Set(syms.map((s) => s.id));
  const label = (s: Sym) => (s.kind === "module" ? moduleLabel(a) : s.qualname);
  const { ghosts, edges: ghostEdges } = a.repo ? ghostsFor(a, syms) : { ghosts: [], edges: [] };
  // wide enough for the name and for the line under it (size, pill, complexity bar), so neither wraps
  const subWidth = (s: Sym) => {
    const { text, pill } = symSub(s);
    return textWidth(text, FONTS.mapSub) + (pill ? 8 + textWidth(pill, FONTS.pill) : 0) + 8 + 34;
  };
  const children = [
    ...syms.map((s) => ({
      id: s.id,
      width: Math.min(360, Math.max(160, Math.ceil(Math.max(textWidth(label(s), FONTS.mapName), subWidth(s))) + 36)),
      height: 62,
    })),
    ...ghosts.map((g) => ({
      id: g.id,
      width: Math.min(300, Math.max(150, Math.ceil(Math.max(textWidth(g.label, FONTS.mapName), textWidth(g.sub, FONTS.mapSub))) + 36)),
      height: 62,
    })),
  ];
  const edgeList = [
    ...a.call_edges.filter((e) => ids.has(e.from) && ids.has(e.to)),
    ...ghostEdges.map((e) => ({ ...e, count: 1, ghost: true })),
  ] as { from: string; to: string; count: number; ghost?: boolean }[];
  const trailPairs = new Set<string>();
  for (let i = 1; i < trail.length; i++) trailPairs.add(`${trail[i - 1]}>${trail[i]}`);
  const laid = await run({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "70",
      "elk.spacing.nodeNode": "22",
      "elk.spacing.componentComponent": "40",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    },
    children,
    edges: edgeList.map((e, i) => ({ id: `c${i}`, sources: [e.from], targets: [e.to] })),
  });
  const byId = new Map(syms.map((s) => [s.id, s]));
  const ghostById = new Map(ghosts.map((g) => [g.id, g]));
  const onTrail = new Set(trail);
  const nodes: Node[] = laid.children!.map((c: any) => {
    const g = ghostById.get(c.id);
    return {
      id: c.id,
      type: g ? "ghost" : "sym",
      position: { x: c.x, y: c.y },
      data: g ? { ghost: g } : {
        sym: byId.get(c.id)!, label: label(byId.get(c.id)!), onTrail: onTrail.has(c.id),
        extIn: byId.get(c.id)!.called_by_ext?.length ?? 0,
      },
      style: { width: c.width, height: c.height },
      draggable: false,
    };
  });
  return {
    nodes,
    edges: edgesFromElk(laid, (id) => {
      const e = edgeList[Number(id.slice(1))];
      return { source: e.from, target: e.to, hot: trailPairs.has(`${e.from}>${e.to}`), back: e.from === e.to,
               tone: e.ghost ? "ghost" : undefined };
    }),
  };
}

/* ------------------------------------------------------------------ folder map (repo mode) */

export type FolderItem = {
  id: string; kind: "dir" | "file" | "ghost"; path: string; label: string; sub: string;
  dim?: boolean; entry?: boolean; usedBy?: number; outside?: boolean;
};

const owners = new WeakMap<RepoIndex, Map<string, string>>();

/** Which (compacted) folder each file sits in. */
function ownerOf(index: RepoIndex): Map<string, string> {
  let m = owners.get(index);
  if (!m) {
    m = new Map();
    for (const d of Object.values(index.dirs)) for (const f of d.files) m.set(f, d.path);
    owners.set(index, m);
  }
  return m;
}

const plural = (n: number, w: string) => `${n.toLocaleString()} ${w}${n === 1 ? "" : w.endsWith("s") ? "es" : "s"}`;

export function dirSummary(index: RepoIndex, path: string): string {
  const st = index.dirs[path].stats;
  if (st.read_files) return `${plural(st.read_files, "file")} of code, ${st.lines.toLocaleString()} lines`;
  return `${plural(st.files, "file")}`;
}

export type FolderPair = { from: string; to: string; w: number; ghost: boolean };
export type FolderGraph = { nodes: Node[]; items: FolderItem[]; pairs: FolderPair[]; dense: boolean };

const BOX_W = 232, BOX_H = 64, GAP_X = 18, GAP_Y = 16, BAND_GAP = 46;

/**
 * Layers for "uses" links: a box sits below everything that uses it, so reading top to bottom goes
 * from the high-level parts to the foundations. Cycles are broken where they're first met.
 */
function layers(ids: string[], pairs: FolderPair[]): { level: Map<string, number>; isolated: Set<string> } {
  const idset = new Set(ids);
  const out = new Map<string, Map<string, number>>(), inc = new Map<string, Map<string, number>>();
  const touched = new Set<string>();
  for (const p of pairs) {
    if (!idset.has(p.from) || !idset.has(p.to)) continue;
    (out.get(p.from) ?? out.set(p.from, new Map()).get(p.from)!).set(p.to, p.w);
    (inc.get(p.to) ?? inc.set(p.to, new Map()).get(p.to)!).set(p.from, p.w);
    touched.add(p.from); touched.add(p.to);
  }
  // Eades–Lin–Smyth ordering: peel off sinks and sources, otherwise take the box that uses the most
  // and is used the least. Links that point backwards in this order are the ones a cycle gives up.
  const alive = new Set(ids.filter((i) => touched.has(i)));
  const wsum = (m: Map<string, number> | undefined) => { let t = 0; if (m) for (const [k, w] of m) if (alive.has(k)) t += w; return t; };
  const head: string[] = [], tail: string[] = [];
  while (alive.size) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const n of [...alive]) {
        if (wsum(out.get(n)) === 0) { tail.unshift(n); alive.delete(n); changed = true; }
        else if (wsum(inc.get(n)) === 0) { head.push(n); alive.delete(n); changed = true; }
      }
    }
    if (!alive.size) break;
    let best = "", bestScore = -Infinity;
    for (const n of alive) {
      const sc = wsum(out.get(n)) - wsum(inc.get(n));
      if (sc > bestScore || (sc === bestScore && n < best)) { best = n; bestScore = sc; }
    }
    head.push(best);
    alive.delete(best);
  }
  const rank = new Map([...head, ...tail].map((n, i) => [n, i]));
  // longest path from the top over the links that point forwards
  const level = new Map<string, number>();
  for (const n of [...head, ...tail]) {
    let l = 0;
    for (const [m] of inc.get(n) ?? []) if (rank.get(m)! < rank.get(n)!) l = Math.max(l, (level.get(m) ?? 0) + 1);
    level.set(n, l);
  }
  return { level, isolated: new Set(ids.filter((i) => !touched.has(i))) };
}

/**
 * One folder's contents as boxes (its subfolders and files of code) in layers: what uses others sits
 * above what it uses. Places outside the folder that it talks to are faded boxes, users above and
 * things it relies on below. Busy folders show only the strongest link per box until you point at one.
 */
export async function layoutFolder(index: RepoIndex, dir: string, width = 900): Promise<FolderGraph> {
  await fontsReady();
  const node = index.dirs[dir];
  const owner = ownerOf(index);
  const ancestors = new Set<string>();
  for (let d: string | null = dir; d !== null; d = index.dirs[d]?.parent ?? null) ancestors.add(d);

  const entryFiles = new Set(index.entry_points.map((e) => e.file).filter(Boolean) as string[]);
  const usedBy = new Map<string, Set<string>>();
  for (const [a, b] of index.edges) (usedBy.get(b) ?? usedBy.set(b, new Set()).get(b)!).add(a);

  const items: FolderItem[] = [];
  for (const d of node.dirs) {
    const sub = index.dirs[d];
    items.push({ id: d, kind: "dir", path: d, label: `${sub.name}/`, sub: sub.doc ?? dirSummary(index, d),
                 dim: !sub.stats.read_files });
  }
  for (const f of node.files) {
    const info = index.files[f];
    if (!info.symbols) continue;
    const kinds = info.symbols.map((r) => r[1]);
    const funcs = kinds.filter((k) => k === "function" || k === "method").length;
    const classes = kinds.filter((k) => k === "class").length;
    const rules = kinds.filter((k) => k === "rule").length;
    const sub = info.doc ?? ([classes && plural(classes, "class"), funcs && plural(funcs, "function"),
                              rules && plural(rules, "rule")].filter(Boolean).join(", ") || `${plural(info.lines, "line")}`);
    items.push({ id: f, kind: "file", path: f, label: f.split("/").pop()!, sub, entry: entryFiles.has(f),
                 usedBy: usedBy.get(f)?.size ?? 0 });
  }
  const inside = new Set(items.map((i) => i.id));

  /** The box a file belongs to from this folder's point of view, and whether it's outside. */
  const place = (f: string): { id: string; outside: boolean } | null => {
    const o = owner.get(f);
    if (o === undefined) return null;
    if (o === dir) return inside.has(f) ? { id: f, outside: false } : null;
    let n: string | null = o;
    while (n !== null && index.dirs[n].parent !== dir && !ancestors.has(n)) n = index.dirs[n].parent;
    if (n !== null && index.dirs[n].parent === dir && !ancestors.has(n)) return { id: n, outside: false };
    if (ancestors.has(o)) return { id: f, outside: true };  // a file in a folder above this one
    let g = o;
    while (!ancestors.has(index.dirs[g].parent!)) g = index.dirs[g].parent!;
    return { id: g, outside: true };
  };

  const weights = new Map<string, number>();
  const ghostIn = new Map<string, number>(), ghostOut = new Map<string, number>();
  for (const [a, b, imports, calls] of index.edges) {
    const pa = place(a), pb = place(b);
    if (!pa || !pb || pa.id === pb.id || (pa.outside && pb.outside)) continue;
    const from = pa.outside ? `ghost:${pa.id}` : pa.id;
    const to = pb.outside ? `ghost:${pb.id}` : pb.id;
    const w = imports + calls;
    weights.set(`${from}\n${to}`, (weights.get(`${from}\n${to}`) ?? 0) + w);
    if (pa.outside) ghostIn.set(pa.id, (ghostIn.get(pa.id) ?? 0) + w);
    if (pb.outside) ghostOut.set(pb.id, (ghostOut.get(pb.id) ?? 0) + w);
  }
  const ghostTotal = new Map<string, number>();
  for (const [g, w] of [...ghostIn, ...ghostOut]) ghostTotal.set(g, (ghostTotal.get(g) ?? 0) + w);
  const ghosts: FolderItem[] = [...ghostTotal.entries()].sort((x, y) => y[1] - x[1]).slice(0, 8).map(([g]) => {
    const isDir = index.dirs[g] !== undefined;
    const users = (ghostIn.get(g) ?? 0) >= (ghostOut.get(g) ?? 0);
    return { id: `ghost:${g}`, kind: "ghost", path: g, outside: true, label: isDir ? `${g}/` : g.split("/").pop()!,
             sub: users ? "uses this folder" : "used by this folder" };
  });
  const allItems = [...items, ...ghosts];
  const shown = new Set(allItems.map((i) => i.id));
  const pairs: FolderPair[] = [...weights.entries()]
    .map(([k, w]) => { const [from, to] = k.split("\n"); return { from, to, w, ghost: from.startsWith("ghost:") || to.startsWith("ghost:") }; })
    .filter((e) => shown.has(e.from) && shown.has(e.to));
  const internal = pairs.filter((e) => !e.ghost);
  const dense = internal.length > Math.max(10, items.length * 1.3);

  // bands: outside users, then layers from high level to foundations, then unconnected, then outside dependencies
  const byId = new Map(allItems.map((it) => [it.id, it]));
  const importance = (id: string) => (byId.get(id)?.usedBy ?? 0);
  const { level, isolated } = layers(items.map((i) => i.id), internal);
  const bands: FolderItem[][] = [];
  const topGhosts = ghosts.filter((g) => g.sub === "uses this folder");
  const bottomGhosts = ghosts.filter((g) => g.sub !== "uses this folder");
  if (topGhosts.length) bands.push(topGhosts);
  const maxLevel = Math.max(-1, ...items.filter((i) => !isolated.has(i.id)).map((i) => level.get(i.id) ?? 0));
  for (let l = 0; l <= maxLevel; l++) {
    bands.push(items.filter((i) => !isolated.has(i.id) && (level.get(i.id) ?? 0) === l)
      .sort((a, b) => importance(b.id) - importance(a.id) || (a.kind === "dir" ? -1 : 1) - (b.kind === "dir" ? -1 : 1) || a.label.localeCompare(b.label)));
  }
  const loose = items.filter((i) => isolated.has(i.id)).sort((a, b) => (a.kind === "dir" ? -1 : 1) - (b.kind === "dir" ? -1 : 1) || a.label.localeCompare(b.label));
  if (loose.length) bands.push(loose);
  if (bottomGhosts.length) bands.push(bottomGhosts);

  const cols = Math.max(1, Math.floor((width - 60 + GAP_X) / (BOX_W + GAP_X)));
  const nodes: Node[] = [];
  let y = 0;
  bands.forEach((band, bi) => {
    band.forEach((it, i) => {
      const r = Math.floor(i / cols), c = i % cols;
      nodes.push({ id: it.id, type: "box", position: { x: c * (BOX_W + GAP_X), y: y + r * (BOX_H + GAP_Y) },
                   data: { item: it, band: bi }, style: { width: BOX_W, height: BOX_H }, draggable: false });
    });
    y += Math.ceil(band.length / cols) * (BOX_H + GAP_Y) - GAP_Y + BAND_GAP;
  });
  return { nodes, items: allItems, pairs, dense };
}

/** Where a link between two boxes starts and ends, and whether it runs up/down or sideways. */
export function curveBetween(a: Node, b: Node): { points: Pt[]; orient: "v" | "h" } {
  const aw = a.style!.width as number, ah = a.style!.height as number;
  const bw = b.style!.width as number, bh = b.style!.height as number;
  if (b.position.y >= a.position.y + ah) {
    return { points: [{ x: a.position.x + aw / 2, y: a.position.y + ah }, { x: b.position.x + bw / 2, y: b.position.y }], orient: "v" };
  }
  if (b.position.y + bh <= a.position.y) {
    return { points: [{ x: a.position.x + aw / 2, y: a.position.y }, { x: b.position.x + bw / 2, y: b.position.y + bh }], orient: "v" };
  }
  const right = b.position.x >= a.position.x;
  return {
    points: [{ x: a.position.x + (right ? aw : 0), y: a.position.y + ah / 2 }, { x: b.position.x + (right ? 0 : bw), y: b.position.y + bh / 2 }],
    orient: "h",
  };
}
