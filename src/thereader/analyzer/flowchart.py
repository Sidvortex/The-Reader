"""Control-flow flowchart builder for Python function bodies (tree-sitter nodes).

Produces a small graph meant for *reading*: consecutive simple statements are merged
into one block, branches become decisions, loops get a back edge, and every node keeps
its line range so the UI can highlight the matching code.
"""

from __future__ import annotations

from tree_sitter import Node

MAX_LINE = 56
MAX_BLOCK_LINES = 4

SIMPLE_SKIP = {"comment"}
TERMINATORS = {"return_statement", "raise_statement", "break_statement", "continue_statement"}


def _t(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _clip(s: str, n: int = MAX_LINE) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _header(node: Node) -> str:
    """Text of a compound statement up to its body colon."""
    body = node.child_by_field_name("body") or node.child_by_field_name("consequence")
    if body is None:
        for c in node.children:
            if c.type == "block":
                body = c
                break
    raw = _t(node)
    if body is not None:
        raw = raw[: body.start_byte - node.start_byte]
    raw = raw.rstrip()
    if raw.endswith(":"):
        raw = raw[:-1]
    return _clip(raw)


class _Builder:
    def __init__(self, sym):
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.loops: list[dict] = []  # stack of {"head": id, "breaks": []}
        self.start = self._node("start", self._start_label(sym), sym.start_line, sym.start_line)
        self.end = self._node("end", "end", sym.end_line, sym.end_line)
        self.raise_end = None

    @staticmethod
    def _start_label(sym) -> str:
        if sym.kind == "module":
            return "module runs"
        # parameter names only: annotations and defaults make the start box unreadably wide
        names = []
        for p in sym.params:
            star = p[: len(p) - len(p.lstrip("*"))]
            name = p.lstrip("*").split(":")[0].split("=")[0].strip()
            names.append(star + name if name else p.strip())
        return _clip(f"{'async ' if sym.is_async else ''}{sym.name}({', '.join(names)})", 56)

    def _node(self, kind: str, label: str, l1: int, l2: int) -> str:
        nid = f"n{len(self.nodes)}"
        self.nodes.append({"id": nid, "kind": kind, "label": label, "start_line": l1, "end_line": l2})
        return nid

    def _link(self, preds: list[tuple[str, str | None]], target: str, back: bool = False) -> None:
        for src, label in preds:
            self.edges.append({"from": src, "to": target, "label": label, "back": back})

    def _raise_end(self) -> str:
        if self.raise_end is None:
            self.raise_end = self._node("raise_end", "raises", 0, 0)
        return self.raise_end

    # ---- sequences ----
    def seq(self, stmts: list[Node], preds: list[tuple[str, str | None]]):
        buf: list[Node] = []

        def flush(p):
            if not buf:
                return p
            lines = []
            for s in buf:
                first = _t(s).split("\n", 1)[0]
                if s.type in ("function_definition", "class_definition", "decorated_definition"):
                    first = first.rstrip(":") if first.rstrip().endswith(":") else first
                lines.append(_clip(first))
            if len(lines) > MAX_BLOCK_LINES:
                lines = lines[: MAX_BLOCK_LINES - 1] + [f"… +{len(lines) - MAX_BLOCK_LINES + 1} more"]
            nid = self._node("block", "\n".join(lines), buf[0].start_point[0] + 1, buf[-1].end_point[0] + 1)
            self._link(p, nid)
            buf.clear()
            return [(nid, None)]

        for s in stmts:
            if s.type in SIMPLE_SKIP:
                continue
            if not preds and not buf:
                break  # unreachable code after return/break
            handler = getattr(self, f"_{s.type}", None)
            if handler is None:
                buf.append(s)
                continue
            preds = flush(preds)
            preds = handler(s, preds)
        return flush(preds)

    def _body(self, node: Node | None, preds):
        if node is None:
            return preds
        return self.seq(node.named_children, preds)

    # ---- terminators ----
    def _return_statement(self, s, preds):
        nid = self._node("return", _clip(_t(s)), s.start_point[0] + 1, s.end_point[0] + 1)
        self._link(preds, nid)
        self._link([(nid, None)], self.end)
        return []

    def _raise_statement(self, s, preds):
        nid = self._node("raise", _clip(_t(s)), s.start_point[0] + 1, s.end_point[0] + 1)
        self._link(preds, nid)
        self._link([(nid, None)], self._raise_end())
        return []

    def _break_statement(self, s, preds):
        if self.loops:
            self.loops[-1]["breaks"].extend(preds)
        return []

    def _continue_statement(self, s, preds):
        if self.loops:
            self._link(preds, self.loops[-1]["head"], back=True)
        return []

    # ---- branches ----
    def _if_statement(self, s, preds):
        cond = s.child_by_field_name("condition")
        d = self._node("decision", _clip(_t(cond)) + " ?", s.start_point[0] + 1, cond.end_point[0] + 1)
        self._link(preds, d)
        exits = self._body(s.child_by_field_name("consequence"), [(d, "yes")])
        pending = [(d, "no")]
        has_else = False
        for alt in s.children_by_field_name("alternative"):
            if alt.type == "elif_clause":
                c = alt.child_by_field_name("condition")
                d2 = self._node("decision", _clip(_t(c)) + " ?", alt.start_point[0] + 1, c.end_point[0] + 1)
                self._link(pending, d2)
                exits += self._body(alt.child_by_field_name("consequence"), [(d2, "yes")])
                pending = [(d2, "no")]
            elif alt.type == "else_clause":
                has_else = True
                exits += self._body(alt.child_by_field_name("body"), pending)
        if not has_else:
            exits += pending
        return exits

    def _match_statement(self, s, preds):
        subj = s.child_by_field_name("subject")
        d = self._node("decision", "match " + _clip(_t(subj)), s.start_point[0] + 1, s.start_point[0] + 1)
        self._link(preds, d)
        exits = []
        wildcard = False
        body = s.child_by_field_name("body")
        for case in (body.named_children if body else []):
            if case.type != "case_clause":
                continue
            pat = _header(case).removeprefix("case ").strip()
            if pat in ("_",) or pat.startswith("_ "):
                wildcard = True
            exits += self._body(case.child_by_field_name("consequence"), [(d, _clip(pat, 28))])
        if not wildcard:
            exits.append((d, "no match"))
        return exits

    # ---- loops ----
    def _loop(self, s, preds, label, yes, no):
        head = self._node("loop", label, s.start_point[0] + 1, s.start_point[0] + 1)
        self._link(preds, head)
        self.loops.append({"head": head, "breaks": []})
        body_exits = self._body(s.child_by_field_name("body"), [(head, yes)])
        self._link(body_exits, head, back=True)
        frame = self.loops.pop()
        exits = [(head, no)]
        alt = s.child_by_field_name("alternative")
        if alt is not None:
            exits = self._body(alt.child_by_field_name("body"), exits)
        return exits + frame["breaks"]

    def _for_statement(self, s, preds):
        return self._loop(s, preds, _header(s), "each", "done")

    def _while_statement(self, s, preds):
        return self._loop(s, preds, _header(s), "true", "false")

    # ---- context / exceptions ----
    def _with_statement(self, s, preds):
        nid = self._node("context", _header(s), s.start_point[0] + 1, s.start_point[0] + 1)
        self._link(preds, nid)
        return self._body(s.child_by_field_name("body"), [(nid, None)])

    def _try_statement(self, s, preds):
        t = self._node("try", "try", s.start_point[0] + 1, s.start_point[0] + 1)
        self._link(preds, t)
        body_exits = self._body(s.child_by_field_name("body"), [(t, None)])
        handler_exits = []
        finally_node = None
        for c in s.named_children:
            if c.type in ("except_clause", "except_group_clause"):
                block = next((b for b in c.named_children if b.type == "block"), None)
                h = self._node("except", _header(c), c.start_point[0] + 1, c.start_point[0] + 1)
                self._link([(t, "error")], h)
                handler_exits += self._body(block, [(h, None)])
            elif c.type == "else_clause":
                # else runs only when the try body finished without an exception
                body_exits = self._body(c.child_by_field_name("body"), body_exits)
            elif c.type == "finally_clause":
                finally_node = c
        exits = body_exits + handler_exits
        if finally_node is not None:
            block = next((b for b in finally_node.named_children if b.type == "block"), None)
            f = self._node("finally", "finally", finally_node.start_point[0] + 1, finally_node.start_point[0] + 1)
            self._link(exits, f)
            exits = self._body(block, [(f, None)])
        return exits


def build_flowchart(stmts: list[Node], sym, src: bytes) -> dict:
    b = _Builder(sym)
    exits = b.seq(stmts, [(b.start, None)])
    b._link(exits, b.end)
    # drop the end node if nothing reaches it (e.g. function always raises)
    reached = {e["to"] for e in b.edges}
    # terminal nodes go last so the layout's model order reads top to bottom
    nodes = [n for n in b.nodes if n["kind"] not in ("end", "raise_end")]
    nodes += [n for n in b.nodes if n["kind"] == "raise_end"]
    nodes += [n for n in b.nodes if n["kind"] == "end" and n["id"] in reached]
    return {"nodes": nodes, "edges": b.edges}
