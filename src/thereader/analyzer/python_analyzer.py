"""Python analyzer: outline, call graph and per-function flowcharts from a tree-sitter AST."""

from __future__ import annotations

import builtins
import hashlib
from dataclasses import dataclass, field

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from .flowchart import build_flowchart

_PARSER = Parser(Language(tree_sitter_python.language()))
_BUILTINS = set(dir(builtins))

DECISION_TYPES = {
    "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
    "except_group_clause", "conditional_expression", "case_clause", "if_clause",
}


def text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


@dataclass
class Symbol:
    id: str
    kind: str  # function | method | class | module
    name: str
    qualname: str
    parent: str | None
    node: Node | None
    start_line: int
    end_line: int
    is_async: bool = False
    params: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    returns: str | None = None
    docstring: str | None = None
    calls: list[dict] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    complexity: int = 1


def _docstring(body: Node | None) -> str | None:
    if body is None:
        return None
    for child in body.named_children:
        if child.type == "comment":
            continue
        if child.type == "expression_statement" and child.named_child_count == 1:
            s = child.named_children[0]
            if s.type == "string":
                raw = text(s)
                for q in ('"""', "'''", '"', "'"):
                    i = raw.find(q)
                    if i != -1 and raw.endswith(q) and len(raw) >= i + 2 * len(q):
                        import inspect
                        return inspect.cleandoc(raw[i + len(q): -len(q)])
        return None
    return None


def _without_docstring(stmts: list[Node]) -> list[Node]:
    """A docstring is documentation, not a step, so it stays out of the flowchart."""
    for i, n in enumerate(stmts):
        if n.type == "comment":
            continue
        if n.type == "expression_statement" and n.named_child_count == 1 and n.named_children[0].type == "string":
            return stmts[:i] + stmts[i + 1:]
        break
    return stmts


def _is_main_guard(node: Node) -> bool:
    cond = "".join(text(node.child_by_field_name("condition")).split()).replace("'", '"')
    return cond in ('__name__=="__main__"', '"__main__"==__name__')


def _params(params: Node | None) -> list[str]:
    if params is None:
        return []
    return [text(p) for p in params.named_children if p.type != "comment"]


def _complexity(node: Node) -> int:
    count = 1
    stack = [node]
    first = True
    while stack:
        n = stack.pop()
        if not first and n.type in ("function_definition", "class_definition", "lambda"):
            continue
        first = False
        if n.type in DECISION_TYPES:
            count += 1
        elif n.type == "boolean_operator":
            count += 1
        stack.extend(n.named_children)
    return count


class PythonAnalyzer:
    def __init__(self, filename: str, source: str):
        self.filename = filename
        self.source = source
        self.src = source.encode("utf-8")
        self.tree = _PARSER.parse(self.src)
        self._line_starts = [0]
        i = self.src.find(b"\n")
        while i != -1:
            self._line_starts.append(i + 1)
            i = self.src.find(b"\n", i + 1)
        self._own_cache: dict[str, tuple[list[Node], list[Node]]] = {}
        self.symbols: dict[str, Symbol] = {}
        self.imports: list[dict] = []
        self.import_names: dict[str, str] = {}  # local name -> module path
        self.globals: list[dict] = []
        self.classes: dict[str, Symbol] = {}
        self.main_guard_line: int | None = None

    # ---------- collection ----------
    def run(self, flowcharts: bool = True) -> dict:
        root = self.tree.root_node
        module = Symbol(
            id="<module>", kind="module", name="<module>", qualname="<module>", parent=None,
            node=root, start_line=1, end_line=root.end_point[0] + 1,
            docstring=_docstring(root),
        )
        self.symbols[module.id] = module
        self._collect(root, parent=None, class_name=None)
        self._attr_types: dict[str, dict[str, str]] = {}
        self._var_types: dict[str, str] = {}
        for sym in self.symbols.values():
            if sym.kind == "method":
                cls = self._enclosing_class(sym)
                for k, v in self._infer_var_types(sym).items():
                    if k.startswith("self."):
                        self._attr_types.setdefault(cls.qualname, {})[k] = v
        for sym in self.symbols.values():
            self._collect_calls(sym)
        for sym in self.symbols.values():
            for c in sym.calls:
                tgt = c.get("target")
                if tgt and tgt in self.symbols and sym.id not in self.symbols[tgt].called_by:
                    self.symbols[tgt].called_by.append(sym.id)
        return self._export(flowcharts)

    def _collect(self, node: Node, parent: str | None, class_name: str | None) -> None:
        for child in node.named_children:
            decorators: list[str] = []
            definition = child
            if child.type == "decorated_definition":
                decorators = [text(d).lstrip("@").strip() for d in child.named_children if d.type == "decorator"]
                definition = child.child_by_field_name("definition")
                if definition is None:
                    continue
            if definition.type == "function_definition":
                name = text(definition.child_by_field_name("name"))
                is_method = class_name is not None and parent is not None and self.symbols[parent].kind == "class"
                qual = f"{self.symbols[parent].qualname}.{name}" if parent else name
                body = definition.child_by_field_name("body")
                sym = Symbol(
                    id=qual, kind="method" if is_method else "function", name=name, qualname=qual,
                    parent=parent, node=definition, start_line=child.start_point[0] + 1,
                    end_line=definition.end_point[0] + 1,
                    is_async=any(c.type == "async" for c in definition.children),
                    params=_params(definition.child_by_field_name("parameters")),
                    decorators=decorators,
                    returns=text(definition.child_by_field_name("return_type")) or None,
                    docstring=_docstring(body), complexity=_complexity(definition),
                )
                self.symbols[sym.id] = sym
                if body is not None:
                    self._collect(body, parent=sym.id, class_name=None)
            elif definition.type == "class_definition":
                name = text(definition.child_by_field_name("name"))
                qual = f"{self.symbols[parent].qualname}.{name}" if parent else name
                body = definition.child_by_field_name("body")
                supers = definition.child_by_field_name("superclasses")
                sym = Symbol(
                    id=qual, kind="class", name=name, qualname=qual, parent=parent, node=definition,
                    start_line=child.start_point[0] + 1, end_line=definition.end_point[0] + 1,
                    decorators=decorators,
                    bases=[text(b) for b in supers.named_children] if supers else [],
                    docstring=_docstring(body),
                )
                self.symbols[sym.id] = sym
                self.classes[name] = sym
                if body is not None:
                    self._collect(body, parent=sym.id, class_name=name)
            elif parent is None and child.type in ("import_statement", "import_from_statement"):
                self._import(child)
            elif parent is None and child.type == "expression_statement":
                inner = child.named_children[0] if child.named_child_count else None
                if inner is not None and inner.type == "assignment":
                    left = inner.child_by_field_name("left")
                    if left is not None and left.type in ("identifier", "pattern_list"):
                        for ident in ([left] if left.type == "identifier" else left.named_children):
                            if ident.type == "identifier":
                                self.globals.append({"name": text(ident), "line": child.start_point[0] + 1})
            elif child.type in ("if_statement", "try_statement", "with_statement", "block",
                                "else_clause", "elif_clause", "except_clause", "finally_clause"):
                # imports / defs guarded by if/try at module level
                if parent is None:
                    if child.type == "if_statement" and self.main_guard_line is None and _is_main_guard(child):
                        self.main_guard_line = child.start_point[0] + 1
                    self._collect(child, parent=None, class_name=None)

    def _import(self, node: Node) -> None:
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            for n in node.children_by_field_name("name"):
                if n.type == "aliased_import":
                    mod = text(n.child_by_field_name("name"))
                    alias = text(n.child_by_field_name("alias"))
                    self.import_names[alias] = mod
                    self.imports.append({"module": mod, "names": [], "alias": alias, "line": line})
                else:
                    mod = text(n)
                    self.import_names[mod.split(".")[0]] = mod.split(".")[0]
                    self.imports.append({"module": mod, "names": [], "alias": None, "line": line})
        else:
            mod = text(node.child_by_field_name("module_name"))
            names = []
            for n in node.children_by_field_name("name"):
                if n.type == "aliased_import":
                    nm, alias = text(n.child_by_field_name("name")), text(n.child_by_field_name("alias"))
                else:
                    nm, alias = text(n), None
                names.append({"name": nm, "alias": alias})
                # `from . import x` -> ".x", `from .pkg import x` -> ".pkg.x"
                self.import_names[alias or nm] = f"{mod}{nm}" if mod.endswith(".") else f"{mod}.{nm}"
            if any(c.type == "wildcard_import" for c in node.named_children):
                names.append({"name": "*", "alias": None})
            self.imports.append({"module": mod, "names": names, "alias": None, "line": line})

    # ---------- calls ----------
    def _own_nodes(self, sym: Symbol):
        """Yield nodes belonging to sym, not descending into nested defs/classes."""
        if sym.node is None:
            return
        stack = list(reversed(sym.node.named_children))
        while stack:
            n = stack.pop()
            if n.type in ("function_definition", "class_definition"):
                continue
            if n.type == "decorated_definition":
                continue
            yield n
            stack.extend(reversed(n.named_children))

    def _own_index(self, sym: Symbol) -> tuple[list[Node], list[Node]]:
        hit = self._own_cache.get(sym.id)
        if hit is None:
            calls, assigns = [], []
            for n in self._own_nodes(sym):
                if n.type == "call":
                    calls.append(n)
                elif n.type == "assignment":
                    assigns.append(n)
            hit = self._own_cache[sym.id] = (calls, assigns)
        return hit

    def _enclosing_class(self, sym: Symbol) -> Symbol | None:
        p = sym.parent
        while p:
            s = self.symbols[p]
            if s.kind == "class":
                return s
            p = s.parent
        return None

    def _method_in_class(self, cls: Symbol, name: str, depth: int = 0) -> str | None:
        cand = f"{cls.qualname}.{name}"
        if cand in self.symbols:
            return cand
        if depth < 5:
            for b in cls.bases:
                base = self.classes.get(b.split("(")[0].strip())
                if base is not None:
                    r = self._method_in_class(base, name, depth + 1)
                    if r:
                        return r
        return None

    def _resolve(self, sym: Symbol, fn: Node) -> dict:
        label = text(fn)
        if fn.type == "identifier":
            name = label
            # nested function defined inside this function
            nested = f"{sym.qualname}.{name}" if sym.kind != "module" else name
            if nested in self.symbols and self.symbols[nested].kind in ("function", "method", "class"):
                return self._target(nested, label)
            # walk outward through parents
            p = sym.parent
            while p:
                cand = f"{self.symbols[p].qualname}.{name}"
                if cand in self.symbols and self.symbols[cand].kind != "method":
                    return self._target(cand, label)
                p = self.symbols[p].parent
            if name in self.symbols and self.symbols[name].kind in ("function", "class"):
                return self._target(name, label)
            if name in self.import_names:
                return {"name": label, "kind": "external", "module": self.import_names[name]}
            if name in _BUILTINS:
                return {"name": label, "kind": "builtin"}
            return {"name": label, "kind": "unknown"}
        if fn.type == "attribute":
            obj = fn.child_by_field_name("object")
            attr = text(fn.child_by_field_name("attribute"))
            obj_text = text(obj)
            if obj_text in ("self", "cls"):
                cls = self._enclosing_class(sym)
                if cls:
                    m = self._method_in_class(cls, attr)
                    if m:
                        return self._target(m, label)
            if obj is not None and obj.type == "call" and text(obj.child_by_field_name("function")) == "super":
                cls = self._enclosing_class(sym)
                if cls:
                    for b in cls.bases:
                        base = self.classes.get(b.strip())
                        if base:
                            m = self._method_in_class(base, attr)
                            if m:
                                return self._target(m, label)
            cls_name = self.classes.get(obj_text) and obj_text or self._var_types.get(obj_text)
            if cls_name and cls_name.startswith("@"):
                # method on an instance of a class imported from another module
                return {"name": label, "kind": "external", "module": f"{cls_name[1:]}.{attr}", "via_type": True}
            if cls_name in self.classes:
                m = self._method_in_class(self.classes[cls_name], attr)
                if m:
                    return self._target(m, label)
            root = obj_text.split(".")[0].split("(")[0]
            if root in self.import_names:
                return {"name": label, "kind": "external", "module": self.import_names[root]}
            return {"name": label, "kind": "unknown"}
        return {"name": label[:60], "kind": "unknown"}

    def _target(self, target: str, label: str) -> dict:
        s = self.symbols[target]
        if s.kind == "class":
            init = f"{s.qualname}.__init__"
            return {"name": label, "kind": "internal", "target": init if init in self.symbols else target}
        return {"name": label, "kind": "internal", "target": target}

    def _type_ref(self, t: str) -> str | None:
        """Map a type or constructor name to a class in this file, or "@dotted.path" for an imported class."""
        t = t.strip().strip("'\"")
        if t.startswith("Optional[") and t.endswith("]"):
            t = t[9:-1]
        if "|" in t:
            t = next((p.strip() for p in t.split("|") if p.strip() not in ("None", "")), "")
        if not t or "[" in t or "(" in t:
            return None
        if t in self.classes:
            return t
        root, _, rest = t.partition(".")
        last = t.rsplit(".", 1)[-1]
        if root in self.import_names and last[:1].isupper():
            return "@" + self.import_names[root] + ("." + rest if rest else "")
        return None

    def _infer_var_types(self, sym: Symbol) -> dict[str, str]:
        """Cheap local type inference: `x = Klass(...)`, `self.x = Klass(...)` and `x: Klass` params."""
        types: dict[str, str] = {}
        cls = self._enclosing_class(sym)
        if cls is not None:
            types.update(self._attr_types.get(cls.qualname, {}))
        if sym.node is not None and sym.kind != "module":
            params = sym.node.child_by_field_name("parameters")
            for p in (params.named_children if params else []):
                if p.type in ("typed_parameter", "typed_default_parameter"):
                    t = self._type_ref(text(p.child_by_field_name("type")))
                    name_node = p.child_by_field_name("name") or next((c for c in p.named_children if c.type == "identifier"), None)
                    if t and name_node is not None:
                        types[text(name_node)] = t
        for n in self._own_index(sym)[1]:
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if right is None or left is None:
                continue
            if right.type == "call":
                t = self._type_ref(text(right.child_by_field_name("function")))
            elif right.type == "identifier":
                t = types.get(text(right))  # `self.repo = repo` where `repo: Repo`
            else:
                t = None
            if t:
                types[text(left)] = t
        return types

    def _collect_calls(self, sym: Symbol) -> None:
        if sym.kind == "class":
            return
        self._var_types = self._infer_var_types(sym)
        for n in self._own_index(sym)[0]:
            fn = n.child_by_field_name("function")
            if fn is None:
                continue
            info = self._resolve(sym, fn)
            # column of the callable name (last identifier) for click-to-jump in code view
            name_node = fn.child_by_field_name("attribute") if fn.type == "attribute" else fn
            info["line"] = name_node.start_point[0] + 1
            row = name_node.start_point[0]
            info["col"] = len(self.src[self._line_starts[row]:name_node.start_byte].decode("utf-8", "replace"))
            info["len"] = len(text(name_node))
            sym.calls.append(info)

    # ---------- export ----------
    def _export(self, flowcharts_on: bool = True) -> dict:
        symbols_out = []
        flowcharts = {}
        edges: dict[tuple[str, str], int] = {}
        for sym in self.symbols.values():
            ext = sorted({c["module"] for c in sym.calls if c["kind"] == "external"})
            symbols_out.append({
                "id": sym.id, "kind": sym.kind, "name": sym.name, "qualname": sym.qualname,
                "parent": sym.parent, "start_line": sym.start_line, "end_line": sym.end_line,
                "is_async": sym.is_async, "params": sym.params, "decorators": sym.decorators,
                "bases": sym.bases, "returns": sym.returns, "docstring": sym.docstring,
                "complexity": sym.complexity, "calls": sym.calls, "called_by": sym.called_by,
                "uses_modules": ext,
                "children": [s.id for s in self.symbols.values() if s.parent == sym.id],
            })
            for c in sym.calls:
                if c.get("target"):
                    edges[(sym.id, c["target"])] = edges.get((sym.id, c["target"]), 0) + 1
            if not flowcharts_on:
                continue
            if sym.kind in ("function", "method") and sym.node is not None:
                body = sym.node.child_by_field_name("body")
                flowcharts[sym.id] = build_flowchart(_without_docstring(body.named_children if body else []), sym, self.src)
            elif sym.kind == "module":
                top = [n for n in _without_docstring(self.tree.root_node.named_children)
                       if n.type not in ("function_definition", "class_definition", "decorated_definition",
                                         "import_statement", "import_from_statement", "comment",
                                         "future_import_statement")]
                if top:
                    flowcharts[sym.id] = build_flowchart(top, sym, self.src)
        lines = self.source.count("\n") + (0 if self.source.endswith("\n") else 1)
        return {
            "file": {
                "name": self.filename, "language": "python", "lines": lines,
                "hash": hashlib.sha256(self.src).hexdigest()[:16],
                "docstring": self.symbols["<module>"].docstring,
                "has_errors": self.tree.root_node.has_error,
                "main_guard": self.main_guard_line,
            },
            "source": self.source,
            "imports": self.imports,
            "globals": self.globals,
            "symbols": symbols_out,
            "call_edges": [{"from": a, "to": b, "count": n} for (a, b), n in edges.items()],
            "flowcharts": flowcharts,
        }


def analyze_python(filename: str, source: str, flowcharts: bool = True) -> dict:
    """Full analysis of one Python file. flowcharts=False is the faster form used to index a repo."""
    return PythonAnalyzer(filename, source).run(flowcharts)
