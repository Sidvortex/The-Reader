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
    attrs: dict = field(default_factory=dict)  # classes: attribute -> where its value comes from (a "desc")
    rdesc: list | None = None  # functions without a return annotation: where the returned value comes from
    uses: list[dict] = field(default_factory=list)  # attribute reads that may be properties in other files


def _literal_key(node: Node) -> str | None:
    """`name['key']` for a subscript like name["key"], so a dict literal's values can be looked up."""
    value, index = node.child_by_field_name("value"), node.child_by_field_name("subscript")
    if value is None or index is None or value.type != "identifier" or index.type != "string":
        return None
    raw = text(index)
    if raw[:1] not in "'\"" or raw[-1:] != raw[:1]:
        return None
    return f"{text(value)}[{raw[1:-1]!r}]"


class _Descs:
    """Where each name's value comes from, as of one point in a function: a name can be given a new value
    later, and a call only sees the value given before it."""

    def __init__(self, hist: dict | None = None, pos: int | None = None):
        self.hist: dict[str, list[tuple[int, list | None]]] = hist if hist is not None else {}
        self.pos = pos  # None: the latest value

    def bind(self, name: str, pos: int, desc: list | None) -> None:
        self.hist.setdefault(name, []).append((pos, desc))

    def at(self, pos: int) -> "_Descs":
        return _Descs(self.hist, pos)

    def get(self, name: str) -> list | None:
        best, best_pos = None, None
        for p, d in self.hist.get(name, ()):
            if (self.pos is None or p <= self.pos) and (best_pos is None or p >= best_pos):
                best, best_pos = d, p
        return best

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __getitem__(self, name: str) -> list:
        return self.get(name)

    def latest(self) -> dict[str, list]:
        return {k: d for k in self.hist if (d := _Descs(self.hist).get(k)) is not None}


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
        self._own_cache: dict[str, tuple[list[Node], list[Node], list[Node]]] = {}
        self._descs_cache: dict[str, dict[str, list]] = {}
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
        self._local_imports(root)
        self._var_descs: _Descs | dict = {}
        self._declared: dict[str, set[str]] = {}
        # what each class's attributes hold: annotations in the class body, then `self.x = ...` in its methods
        for sym in list(self.symbols.values()):
            if sym.kind == "class":
                self._class_attrs(sym)
        for sym in list(self.symbols.values()):
            if sym.kind == "method":
                cls = self._enclosing_class(sym)
                for name, alts in self._self_assignments(sym).items():
                    if cls is not None and name not in self._declared.get(cls.id, ()):
                        have = cls.attrs.setdefault(name, [])
                        for d in alts:
                            if d not in have and len(have) < 4:
                                have.append(d)
        for sym in self.symbols.values():
            if sym.kind in ("function", "method") and not sym.returns:
                sym.rdesc = self._return_desc(sym)
        for sym in self.symbols.values():
            self._collect_calls(sym)
        out = self._export(flowcharts)
        _link_self(out, self.filename)
        return out

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

    def _local_imports(self, root: Node) -> None:
        """Imports inside functions or blocks: they count as this file using that module too."""
        seen = {imp["line"] for imp in self.imports}
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type in ("import_statement", "import_from_statement"):
                if n.start_point[0] + 1 not in seen:
                    self._import(n, local=True)
                continue
            stack.extend(n.named_children)

    def _import(self, node: Node, local: bool = False) -> None:
        line = node.start_point[0] + 1
        if local:  # names imported inside a function don't override the file's own imports
            before, count = dict(self.import_names), len(self.imports)
            self._import(node)
            self.import_names.update(before)
            for imp in self.imports[count:]:
                imp["local"] = True
            return
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

    def _own_index(self, sym: Symbol) -> tuple[list[Node], list[Node], list[Node]]:
        """sym's own calls; the statements that give names values (assignments, for loops); attribute reads."""
        hit = self._own_cache.get(sym.id)
        if hit is None:
            calls, binds, attrs = [], [], []
            for n in self._own_nodes(sym):
                if n.type == "call":
                    calls.append(n)
                elif n.type in ("assignment", "for_statement", "for_in_clause"):
                    binds.append(n)
                elif n.type == "attribute":
                    attrs.append(n)
            hit = self._own_cache[sym.id] = (calls, binds, attrs)
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
                    if m:  # a subclass of this class may have its own version
                        return {**self._target(m, label), "virtual": cls.qualname}
            if obj is not None and obj.type == "call" and text(obj.child_by_field_name("function")) == "super":
                cls = self._enclosing_class(sym)
                if cls:
                    for b in cls.bases:
                        base = self.classes.get(b.strip())
                        if base:
                            m = self._method_in_class(base, attr)
                            if m:
                                return self._target(m, label)
                    if cls.bases and "=" not in cls.bases[0]:  # super() goes to the base class, never down
                        return {"name": label, "kind": "unknown", "recv": ["T:" + cls.bases[0], []], "attr": attr,
                                "super": True}
                    return {"name": label, "kind": "unknown"}
            if obj_text in self.classes:  # Klass.method()
                m = self._method_in_class(self.classes[obj_text], attr)
                if m:
                    return self._target(m, label)
            desc = self._expr_desc(sym, obj, self._var_descs)
            if desc is not None:
                out = {"name": label, "kind": "unknown", "recv": desc, "attr": attr}
                root = desc[0][2:] if desc[0].startswith("T:") else desc[0][1:]
                root = root.split(".")[0].split("[")[0].strip("'\" ")
                if root in self.import_names and not desc[1]:
                    out["kind"], out["module"] = "external", f"{self.import_names[root]}"
                return out
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

    # ---------- where values come from ----------
    # A "desc" says where a value comes from, so a call on it can be linked later, even into another file:
    #   ["T:Store", []]                   a value of the annotated type `Store`, as named in this file
    #   ["^open_store", []]               what calling `open_store` gives (for a class: an instance of it)
    #   [<base>, ["store", "search()", "in"]]  then its .store, what .search() returns, one item from iterating it
    # repo/modules.py turns these into classes once every file is known.
    def _self_class(self, sym: Symbol) -> Symbol | None:
        return sym if sym.kind == "class" else self._enclosing_class(sym)

    def _is_name_path(self, node: Node, descs: dict) -> bool:
        """`pkg.make` or `Klass.create`: a dotted name, not a value held in a variable."""
        while node.type == "attribute":
            node = node.child_by_field_name("object")
            if node is None:
                return False
        if node.type != "identifier":
            return False
        root = text(node)
        return root not in descs and root not in ("self", "cls") and (
            root in self.import_names or root in self.classes or root in self.symbols)

    def _expr_desc(self, sym: Symbol, node: Node | None, descs: dict, depth: int = 0) -> list | None:
        if node is None or depth > 8:
            return None
        t = node.type
        if t == "identifier":
            name = text(node)
            if name in descs:
                return descs[name]
            if name in ("self", "cls"):
                cls = self._self_class(sym)
                return ["T:" + cls.qualname, []] if cls else None
            return None
        if t == "attribute":
            full = text(node)
            if full in descs:
                return descs[full]
            base = self._expr_desc(sym, node.child_by_field_name("object"), descs, depth + 1)
            return [base[0], base[1] + [text(node.child_by_field_name("attribute"))]] if base else None
        if t == "call":
            fn = node.child_by_field_name("function")
            if fn is None:
                return None
            args = node.child_by_field_name("arguments")
            if text(fn) in ("copy.copy", "copy.deepcopy", "copy", "deepcopy") and args is not None \
                    and args.named_child_count == 1:  # a copy is the same kind of thing
                return self._expr_desc(sym, args.named_children[0], descs, depth + 1)
            if fn.type == "identifier":
                name = text(fn)
                if name in descs or name in _BUILTINS and name not in self.symbols and name not in self.import_names:
                    return None
                # a nested function or class first, then the file's own names and imports
                p = sym.id if sym.kind != "module" else None
                while p:
                    cand = f"{self.symbols[p].qualname}.{name}"
                    if cand in self.symbols and self.symbols[cand].kind in ("function", "class"):
                        return ["^" + cand, []]
                    p = self.symbols[p].parent
                return ["^" + name, []]
            if fn.type == "attribute":
                if self._is_name_path(fn, descs):
                    return ["^" + text(fn), []]
                base = self._expr_desc(sym, fn.child_by_field_name("object"), descs, depth + 1)
                return [base[0], base[1] + [text(fn.child_by_field_name("attribute")) + "()"]] if base else None
            return None
        if t == "subscript":
            key = _literal_key(node)
            if key and key in descs:  # state["engine"], where state = {"engine": ...}
                return descs[key]
            base = self._expr_desc(sym, node.child_by_field_name("value"), descs, depth + 1)
            return [base[0], base[1] + ["[]"]] if base else None
        if t in ("await", "parenthesized_expression"):
            return self._expr_desc(sym, node.named_children[0] if node.named_child_count else None, descs, depth + 1)
        if t in ("boolean_operator", "conditional_expression"):
            for c in node.named_children:
                d = self._expr_desc(sym, c, descs, depth + 1)
                if d:
                    return d
        return None

    def _bind(self, left: Node, desc: list | None, descs: _Descs, pos: int) -> None:
        if left.type in ("identifier", "attribute"):
            descs.bind(text(left), pos, desc)  # None too: a new value of unknown kind hides the old one

    def _infer_var_types(self, sym: Symbol) -> _Descs:
        """Where each local name (and self.x) gets its value: annotations, assignments, loops."""
        cached = self._descs_cache.get(sym.id)
        if cached is not None:
            return cached
        descs = _Descs()
        parent = self.symbols.get(sym.parent) if sym.parent else None
        if parent is not None and parent.kind in ("function", "method"):
            for k, d in self._infer_var_types(parent).latest().items():  # names of the enclosing function
                descs.bind(k, -1, d)
        if sym.node is not None and sym.kind in ("function", "method"):
            params = sym.node.child_by_field_name("parameters")
            for p in (params.named_children if params else []):
                if p.type in ("typed_parameter", "typed_default_parameter"):
                    ann = text(p.child_by_field_name("type"))
                    name_node = p.child_by_field_name("name") or next(
                        (c for c in p.named_children if c.type == "identifier"), None)
                    if ann and name_node is not None:
                        descs.bind(text(name_node), -1, ["T:" + ann, []])
        for n in self._own_index(sym)[1]:
            if n.type in ("for_statement", "for_in_clause"):
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                if left is not None and right is not None:
                    d = self._expr_desc(sym, right, descs.at(n.start_byte))
                    item = [d[0], d[1] + ["in"]] if d else None
                    # a comprehension's loop name is used before the `for` that gives it a value
                    pos = n.parent.start_byte if n.type == "for_in_clause" and n.parent is not None else right.end_byte
                    if left.type in ("pattern_list", "tuple_pattern"):  # for i, x in pairs
                        for k, part in enumerate(left.named_children):
                            self._bind(part, [item[0], item[1] + [f"#{k}"]] if item else None, descs, pos)
                    else:
                        self._bind(left, item, descs, pos)
                continue
            left, right, ann = n.child_by_field_name("left"), n.child_by_field_name("right"), n.child_by_field_name("type")
            if left is None:
                continue
            here, pos = descs.at(n.start_byte), n.end_byte  # the new value counts from the end of the statement
            if ann is not None:
                self._bind(left, ["T:" + text(ann), []], descs, pos)
            elif right is not None and left.type in ("pattern_list", "tuple_pattern") \
                    and right.type in ("expression_list", "tuple") \
                    and len(left.named_children) == len(right.named_children):
                for l, r in zip(left.named_children, right.named_children):  # a, b = x, y
                    self._bind(l, self._expr_desc(sym, r, here), descs, pos)
            elif right is not None and left.type in ("pattern_list", "tuple_pattern"):
                d = self._expr_desc(sym, right, here)  # a, b = pair()
                for k, part in enumerate(left.named_children):
                    self._bind(part, [d[0], d[1] + [f"#{k}"]] if d else None, descs, pos)
            elif right is not None:
                self._bind(left, self._expr_desc(sym, right, here), descs, pos)
                if right.type == "dictionary" and left.type == "identifier":  # x = {"key": value, ...}
                    for pair in right.named_children:
                        k, v = pair.child_by_field_name("key"), pair.child_by_field_name("value")
                        if pair.type == "pair" and k is not None and k.type == "string" and v is not None:
                            descs.bind(f'{text(left)}[{text(k)[1:-1]!r}]', pos, self._expr_desc(sym, v, here))
        self._descs_cache[sym.id] = descs
        return descs

    def _self_assignments(self, sym: Symbol) -> dict[str, list]:
        """Every value a method gives self.x: `self.x = Thing()`, `self.x = param` and so on."""
        out: dict[str, list] = {}
        for k, entries in self._infer_var_types(sym).hist.items():
            if k.startswith("self.") and k.count(".") == 1:
                for _, d in entries:
                    if d is not None and d not in out.setdefault(k[5:], []):
                        out[k[5:]].append(d)
        return out

    def _class_attrs(self, cls: Symbol) -> None:
        """Attributes declared in a class body: `store: Store`, `x = Thing()` (dataclass fields included)."""
        body = cls.node.child_by_field_name("body") if cls.node is not None else None
        for child in (body.named_children if body else []):
            if child.type != "expression_statement" or not child.named_child_count:
                continue
            n = child.named_children[0]
            if n.type != "assignment":
                continue
            left, right, ann = n.child_by_field_name("left"), n.child_by_field_name("right"), n.child_by_field_name("type")
            if left is None or left.type != "identifier":
                continue
            if ann is not None:
                cls.attrs[text(left)] = [["T:" + text(ann), []]]
                self._declared.setdefault(cls.id, set()).add(text(left))  # the annotation is the answer
            elif right is not None:
                d = self._expr_desc(cls, right, {})
                if d:
                    cls.attrs[text(left)] = [d]

    def _return_desc(self, sym: Symbol) -> list | None:
        """For a function without a return annotation: where its returned value comes from, if that's clear."""
        descs = None
        for n in self._own_nodes(sym):
            if n.type == "return_statement" and n.named_child_count:
                if descs is None:
                    descs = self._infer_var_types(sym)
                d = self._expr_desc(sym, n.named_children[0], descs.at(n.start_byte))
                if d:
                    return d
        return None

    def _collect_calls(self, sym: Symbol) -> None:
        descs = self._infer_var_types(sym)
        call_fns = set()
        for n in self._own_index(sym)[0]:
            fn = n.child_by_field_name("function")
            if fn is None:
                continue
            call_fns.add(fn.id)
            self._var_descs = descs.at(n.start_byte)
            info = self._resolve(sym, fn)
            # column of the callable name (last identifier) for click-to-jump in code view
            name_node = fn.child_by_field_name("attribute") if fn.type == "attribute" else fn
            sym.calls.append({**info, **self._place(name_node)})
        # reading an attribute can run code too (a property), or hand a method on to be called later
        for n in self._own_index(sym)[2]:
            if n.id in call_fns:
                continue
            parent = n.parent
            if parent is not None and parent.type in ("assignment", "augmented_assignment") \
                    and parent.child_by_field_name("left") is not None \
                    and parent.child_by_field_name("left").id == n.id:
                continue
            obj = n.child_by_field_name("object")
            desc = self._expr_desc(sym, obj, descs.at(n.start_byte))
            if desc is None:
                continue
            attr_node = n.child_by_field_name("attribute")
            sym.uses.append({"name": text(n)[:80], "recv": desc, "attr": text(attr_node), **self._place(attr_node)})

    def _place(self, node: Node) -> dict:
        row = node.start_point[0]
        col = len(self.src[self._line_starts[row]:node.start_byte].decode("utf-8", "replace"))
        return {"line": row + 1, "col": col, "len": len(text(node))}

    # ---------- export ----------
    _SKIP_NAME = {"function_definition", "class_definition", "parameters", "typed_parameter", "default_parameter",
                  "typed_default_parameter", "keyword_argument", "dotted_name", "aliased_import", "lambda_parameters",
                  "list_splat_pattern", "dictionary_splat_pattern"}

    def _name_refs(self) -> dict[str, int]:
        """How often each name is used as a value (not defined, not called), and every `.attribute` name."""
        counts: dict[str, int] = {}
        self._attr_names: set[str] = set()
        stack = [self.tree.root_node]
        while stack:
            n = stack.pop()
            if n.type == "attribute":
                a = n.child_by_field_name("attribute")
                if a is not None:
                    self._attr_names.add(text(a))
                obj = n.child_by_field_name("object")
                if obj is not None:
                    stack.append(obj)
                continue
            if n.type == "identifier":
                p = n.parent
                if p is None or p.type in self._SKIP_NAME:
                    continue
                if p.type == "call" and p.child_by_field_name("function") is not None \
                        and p.child_by_field_name("function").id == n.id:
                    continue
                if p.type in ("assignment", "augmented_assignment") and p.child_by_field_name("left") is not None \
                        and p.child_by_field_name("left").id == n.id:
                    continue
                counts[text(n)] = counts.get(text(n), 0) + 1
                continue
            stack.extend(n.named_children)
        return counts

    def _export(self, flowcharts_on: bool = True) -> dict:
        symbols_out = []
        flowcharts = {}
        edges: dict[tuple[str, str], int] = {}
        referenced = self._name_refs()
        for sym in self.symbols.values():
            ext = sorted({c["module"] for c in sym.calls if c["kind"] == "external" and c.get("module")})
            extra = {}
            if sym.attrs:
                extra["attrs"] = sym.attrs
            if sym.rdesc:
                extra["rdesc"] = sym.rdesc
            if sym.uses:
                extra["uses"] = sym.uses
            if sym.kind in ("function", "class") and referenced.get(sym.name):
                extra["referenced"] = True  # its name appears somewhere other than a call: passed as a value
            symbols_out.append({**extra,
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
            "attr_names": sorted(self._attr_names),
            "globals": self.globals,
            "symbols": symbols_out,
            "call_edges": [{"from": a, "to": b, "count": n} for (a, b), n in edges.items()],
            "flowcharts": flowcharts,
        }


def _link_self(out: dict, filename: str) -> None:
    """Resolve the calls whose receiver needed type information, as far as this one file allows, then fill in
    who calls whom. Calls that lead into other files keep their `recv` for repo/modules.py."""
    from ..repo.modules import ModuleMap

    mm = ModuleMap([filename], "file", {filename: out})
    by_id = {s["id"]: s for s in out["symbols"]}
    for s in out["symbols"]:
        for c in s["calls"]:
            if "recv" in c and c.get("kind") != "internal":
                state, hits, more = mm.resolve_member(c["recv"], c["attr"], filename)
                local = [q for f, q in hits if f == filename and q in by_id]
                if state == "hit" and local:
                    c["kind"], c["target"] = "internal", local[0]
                    subs = [] if c.get("super") else more
                    also = local[1:] + [q for f, q in subs if f == filename and q in by_id and q not in local]
                    if also:
                        c["also"] = also
                    c.pop("module", None)
        kept = []
        for u in s.pop("uses", []):
            if "recv" not in u:  # a name imported from another file: settled once every file is known
                kept.append(u)
                continue
            state, hits, _ = mm.resolve_member(u["recv"], u["attr"], filename)
            local = [q for f, q in hits if f == filename and by_id.get(q, {}).get("kind") in ("function", "method")]
            if state == "hit" and local:
                s["calls"].append({"name": u["name"], "kind": "internal", "target": local[0], "access": True,
                                   "line": u["line"], "col": u["col"], "len": u["len"]})
            if state == "unknown" or (state == "hit" and len(local) < len(hits)):
                kept.append(u)  # it may still lead into another file
        if kept:
            s["uses"] = kept
    # a call to a method may run a subclass's version of it
    for s in out["symbols"]:
        for c in s["calls"]:
            if c.get("target") and isinstance(c.get("virtual"), str):  # self.method(): subclasses' versions too
                meth = c["target"].rsplit(".", 1)[-1]
                also = [q for f, q in mm.overrides(filename, c["virtual"], meth) if f == filename and q != c["target"]]
                if also:
                    c["also"] = list(dict.fromkeys(c.get("also", []) + also))
    edges: dict[tuple[str, str], int] = {}
    for s in out["symbols"]:
        s["called_by"] = []
    for s in out["symbols"]:
        for c in s["calls"]:
            for tgt in ([c["target"]] if c.get("target") else []) + c.get("also", []):
                if tgt in by_id:
                    if s["id"] not in by_id[tgt]["called_by"]:
                        by_id[tgt]["called_by"].append(s["id"])
                    edges[(s["id"], tgt)] = edges.get((s["id"], tgt), 0) + 1
    out["call_edges"] = [{"from": a, "to": b, "count": n} for (a, b), n in edges.items()]


def analyze_python(filename: str, source: str, flowcharts: bool = True) -> dict:
    """Full analysis of one Python file. flowcharts=False is the faster form used to index a repo."""
    return PythonAnalyzer(filename, source).run(flowcharts)
