"""JavaScript and TypeScript analyzer: outline, imports and exports, calls (including JSX components and `new`),
and flowcharts, in the same shape as the Python analyzer so everything after it works the same way.

Vue and Svelte components and HTML pages reuse it: their <script> parts are analyzed in place (see markup.py).
"""

from __future__ import annotations

import hashlib
import re

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from .flowchart import build_js_flowchart
from .python_analyzer import Symbol, _Descs, _link_self, text

_LANGS = {
    "javascript": Language(tree_sitter_javascript.language()),  # JSX included
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
_PARSERS: dict[str, Parser] = {}


def _parser(grammar: str) -> Parser:
    if grammar not in _PARSERS:
        _PARSERS[grammar] = Parser(_LANGS[grammar])
    return _PARSERS[grammar]


# file extension -> (language name, grammar, highlighter name)
EXTENSIONS = {
    ".js": ("javascript", "javascript", "jsx"), ".mjs": ("javascript", "javascript", "javascript"),
    ".cjs": ("javascript", "javascript", "javascript"), ".jsx": ("javascript", "javascript", "jsx"),
    ".ts": ("typescript", "typescript", "typescript"), ".mts": ("typescript", "typescript", "typescript"),
    ".cts": ("typescript", "typescript", "typescript"), ".tsx": ("typescript", "tsx", "tsx"),
}

FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
FUNC_DECLS = {"function_declaration", "generator_function_declaration"}
CLASS_DECLS = {"class_declaration", "abstract_class_declaration"}
DECISIONS = {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "switch_case",
             "catch_clause", "ternary_expression"}
JSX_TAGS = {"jsx_opening_element", "jsx_self_closing_element"}
# wrappers around a function that keep it the same function: React.memo(fn), forwardRef(fn), useCallback(fn)
WRAPPERS = {"memo", "forwardRef", "React.memo", "React.forwardRef", "useCallback", "React.useCallback",
            "observer", "defineComponent", "computed", "markRaw"}
GLOBALS = {
    "console", "Math", "JSON", "Object", "Array", "Promise", "String", "Number", "Boolean", "Date", "RegExp", "Error",
    "TypeError", "RangeError", "Map", "Set", "WeakMap", "WeakSet", "Symbol", "parseInt", "parseFloat",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval", "fetch", "document", "window", "globalThis",
    "process", "Buffer", "structuredClone", "queueMicrotask", "requestAnimationFrame", "cancelAnimationFrame",
    "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI", "alert", "confirm", "prompt",
    "localStorage", "sessionStorage", "navigator", "location", "history", "URL", "URLSearchParams", "TextEncoder",
    "TextDecoder", "AbortController", "Intl", "Reflect", "Proxy", "BigInt", "isNaN", "isFinite", "crypto",
    "performance", "atob", "btoa", "Blob", "File", "FileReader", "FormData", "Headers", "WebSocket", "Worker",
    "Event", "CustomEvent", "MutationObserver", "ResizeObserver", "IntersectionObserver", "Image", "Audio",
    "require", "module", "exports", "__dirname", "__filename", "setImmediate", "eval", "XMLHttpRequest",
}


def _is_cap(name: str) -> bool:
    return name[:1].isupper()


def _jsdoc(node: Node) -> str | None:
    """The /** ... */ comment just above a declaration, as plain text without its @tags."""
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment" or node.start_point[0] - prev.end_point[0] > 1:
        return None
    raw = text(prev)
    if not raw.startswith("/**"):
        return None
    lines = []
    for line in raw[3:-2].splitlines():
        line = line.strip()
        line = line[1:].strip() if line.startswith("*") else line
        if line.startswith("@"):
            break
        lines.append(line)
    out = "\n".join(lines).strip()
    return out or None


def _unwrap(node: Node | None) -> Node | None:
    """Look through parentheses, `as Foo`, `x!` and wrappers like memo(fn) to the value itself."""
    while node is not None:
        if node.type in ("parenthesized_expression", "non_null_expression", "satisfies_expression") \
                and node.named_child_count:
            node = node.named_children[0]
        elif node.type == "as_expression" and node.named_child_count:
            node = node.named_children[0]
        elif node.type == "call_expression" and text(node.child_by_field_name("function")) in WRAPPERS:
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_child_count else None
            if first is None or first.type not in FUNC_VALUES | {"object", "call_expression"}:
                return node
            node = first
        else:
            return node
    return None


def _string(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        frag = next((c for c in node.named_children if c.type == "string_fragment"), None)
        return text(frag) if frag is not None else ""
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.named_children):
        return text(node)[1:-1]
    return None


def _type_text(ann: Node | None) -> str | None:
    """`: Foo` -> `Foo`."""
    if ann is None:
        return None
    t = text(ann).strip()
    return t[1:].strip() if t.startswith(":") else t


def _complexity(node: Node, symbol_nodes: set[int]) -> int:
    count, stack, first = 1, [node], True
    while stack:
        n = stack.pop()
        if not first and n.id in symbol_nodes:
            continue
        first = False
        if n.type in DECISIONS:
            count += 1
        elif n.type == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and text(op) in ("&&", "||", "??"):
                count += 1
        stack.extend(n.named_children)
    return count


class JsAnalyzer:
    def __init__(self, filename: str, source: str, language: str, grammar: str, highlight: str):
        self.filename, self.source = filename, source
        self.language, self.highlight = language, highlight
        self.src = source.encode("utf-8")
        self.tree = _parser(grammar).parse(self.src)
        self._line_starts = [0]
        i = self.src.find(b"\n")
        while i != -1:
            self._line_starts.append(i + 1)
            i = self.src.find(b"\n", i + 1)
        self.symbols: dict[str, Symbol] = {}
        self.sym_nodes: dict[int, str] = {}  # node id of a function/class body owner -> symbol id
        self.bodies: dict[str, Node] = {}  # symbol id -> the node whose code belongs to it
        self.decl_nodes: dict[str, Node] = {}
        self.classes: dict[str, Symbol] = {}
        self.imports: list[dict] = []
        self.import_names: dict[str, tuple[str, str]] = {}  # local name -> (module, imported name or "*")
        self.exports: dict[str, object] = {}
        self.export_stars: list[str] = []
        self.globals: list[dict] = []
        self.main_guard_line: int | None = None
        self._declared: dict[str, set[str]] = {}
        self._descs_cache: dict[str, _Descs] = {}
        self._own_cache: dict[str, tuple] = {}
        self.component_name = None  # set by markup.py for .vue/.svelte files
        self._objects: dict[str, Symbol] = {}

    # ------------------------------------------------------------------ outline
    def run(self, flowcharts: bool = True) -> dict:
        root = self.tree.root_node
        module = Symbol(id="<module>", kind="module", name="<module>", qualname="<module>", parent=None, node=root,
                        start_line=1, end_line=root.end_point[0] + 1, docstring=self._file_doc(root))
        self.symbols["<module>"] = module
        self.bodies["<module>"] = root
        self._collect(root, None)
        self._imports(root)
        for sym in list(self.symbols.values()):
            if sym.kind == "class":
                self._class_attrs(sym)
        for sym in list(self.symbols.values()):
            if sym.kind == "method":
                cls = self.symbols.get(sym.parent)
                for name, alts in self._this_assignments(sym).items():
                    if cls is not None and cls.kind == "class" and name not in self._declared.get(cls.id, ()):
                        have = cls.attrs.setdefault(name, [])
                        for d in alts:
                            if d not in have and len(have) < 4:
                                have.append(d)
        for sym in self.symbols.values():
            if sym.kind in ("function", "method") and not sym.returns:
                sym.rdesc = self._return_desc(sym)
            if sym.kind != "module":
                sym.complexity = _complexity(self.bodies.get(sym.id, sym.node), set(self.sym_nodes) - {
                    self.bodies.get(sym.id, sym.node).id})
        for sym in self.symbols.values():
            self._collect_calls(sym)
        out = self._export(flowcharts)
        _link_self(out, self.filename)
        return out

    def _file_doc(self, root: Node) -> str | None:
        first = next((c for c in root.named_children), None)
        if first is not None and first.type == "comment" and text(first).startswith("/**"):
            nxt = first.next_named_sibling
            # a comment right above the first declaration documents that declaration, not the file
            if nxt is None or nxt.start_point[0] - first.end_point[0] > 1 or nxt.type in (
                    "import_statement", "expression_statement"):
                lines = []
                for line in text(first)[3:-2].splitlines():
                    line = line.strip()
                    line = line[1:].strip() if line.startswith("*") else line
                    if line.startswith("@"):
                        if line.startswith(("@file", "@fileoverview", "@module")):
                            line = line.split(None, 1)[1] if " " in line else ""
                        else:
                            break
                    lines.append(line)
                return "\n".join(lines).strip() or None
        return None

    def _qual(self, parent: str | None, name: str) -> str:
        return f"{self.symbols[parent].qualname}.{name}" if parent and parent != "<module>" else name

    def _add(self, kind: str, name: str, parent: str | None, node: Node, decl: Node, body: Node | None,
             params: Node | None = None, returns: Node | None = None, decorators: list[str] | None = None,
             bases: list[str] | None = None) -> Symbol:
        qual = self._qual(parent, name)
        if qual in self.symbols:  # overloads, getter + setter: keep the first, it's what's read
            return self.symbols[qual]
        sym = Symbol(
            id=qual, kind=kind, name=name, qualname=qual, parent=parent, node=node,
            start_line=decl.start_point[0] + 1, end_line=node.end_point[0] + 1,
            is_async=any(c.type == "async" for c in node.children),
            params=[text(p) for p in params.named_children if p.type != "comment"] if params is not None else [],
            decorators=decorators or [], bases=bases or [], returns=_type_text(returns),
            docstring=_jsdoc(decl),
        )
        self.symbols[qual] = sym
        self.sym_nodes[node.id] = qual
        self.bodies[qual] = body if body is not None else node
        self.decl_nodes[qual] = decl
        if kind == "class":
            self.classes[name] = sym
        return sym

    def _func(self, fn: Node, name: str, parent: str | None, decl: Node, kind: str = "function",
              decorators: list[str] | None = None) -> Symbol:
        params = fn.child_by_field_name("parameters")
        if params is None and fn.type == "arrow_function":
            p = fn.child_by_field_name("parameter")  # x => ...
            params = None
            sym = self._add(kind, name, parent, fn, decl, fn.child_by_field_name("body"), None,
                            fn.child_by_field_name("return_type"), decorators)
            if p is not None:
                sym.params = [text(p)]
        else:
            sym = self._add(kind, name, parent, fn, decl, fn.child_by_field_name("body"), params,
                            fn.child_by_field_name("return_type"), decorators)
        body = fn.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            self._collect(body, sym.id)
        return sym

    def _class(self, node: Node, name: str, parent: str | None, decl: Node, decorators: list[str]) -> Symbol:
        bases = []
        for h in node.named_children:
            if h.type == "class_heritage":
                for clause in h.named_children:
                    if clause.type == "extends_clause":
                        v = clause.child_by_field_name("value")
                        if v is not None:
                            bases.append(text(v))
                    elif clause.type == "implements_clause":
                        bases += [text(t) for t in clause.named_children]
                    elif clause.type not in ("comment",):  # plain JavaScript: class_heritage holds the value
                        bases.append(text(clause))
        sym = self._add("class", name, parent, node, decl, node.child_by_field_name("body"), decorators=decorators,
                        bases=bases)
        body = node.child_by_field_name("body")
        self._members(body, sym)
        return sym

    def _decorators(self, node: Node) -> list[str]:
        return [text(d).lstrip("@").strip() for d in node.named_children if d.type == "decorator"]

    def _members(self, body: Node | None, cls: Symbol) -> None:
        pending: list[str] = []
        for m in (body.named_children if body is not None else []):
            if m.type == "decorator":
                pending.append(text(m).lstrip("@").strip())
                continue
            decs = pending + self._decorators(m)
            pending = []
            if m.type in ("method_definition", "method_signature", "abstract_method_signature"):
                name = text(m.child_by_field_name("name"))
                words = {text(c) for c in m.children if not c.is_named}
                if "get" in words:
                    decs.append("get")
                elif "set" in words:
                    decs.append("set")
                if "static" in words or any(c.type == "static" for c in m.children):
                    decs.append("static")
                if m.type == "method_definition":
                    self._func(m, name, cls.id, m, "method", decs)
                else:
                    self._add("method", name, cls.id, m, m, None, m.child_by_field_name("parameters"),
                              m.child_by_field_name("return_type"), decs)
            elif m.type in ("public_field_definition", "field_definition", "property_signature"):
                name_node = m.child_by_field_name("name") or m.child_by_field_name("property")
                value = _unwrap(m.child_by_field_name("value"))
                if name_node is None:
                    continue
                name = text(name_node)
                if value is not None and value.type in FUNC_VALUES:
                    self._func(value, name, cls.id, m, "method", decs)
                    if m.type != "property_signature":
                        self.sym_nodes.setdefault(m.id, self._qual(cls.id, name))
            elif m.type == "pair" and cls.decorators[:1] == ["object"]:
                self._object_member(m, cls)
            elif m.type == "shorthand_property_identifier":
                pass

    # an object literal with functions in it reads like a class: `const api = { get() {}, post: () => {} }`,
    # Vue's `export default { methods: { save() {} } }`
    def _object(self, node: Node, name: str, parent: str | None, decl: Node) -> Symbol | None:
        if not any(self._is_member_fn(c) for c in node.named_children) and not any(
                c.type == "pair" and _unwrap(c.child_by_field_name("value")) is not None
                and _unwrap(c.child_by_field_name("value")).type == "object" for c in node.named_children):
            return None
        sym = self._add("class", name, parent, node, decl, node, decorators=["object"])
        self._object_members(node, sym)
        return sym

    @staticmethod
    def _is_member_fn(c: Node) -> bool:
        if c.type == "method_definition":
            return True
        if c.type == "pair":
            v = _unwrap(c.child_by_field_name("value"))
            return v is not None and v.type in FUNC_VALUES
        return False

    def _object_members(self, node: Node, cls: Symbol) -> None:
        for c in node.named_children:
            self._object_member(c, cls)

    def _object_member(self, c: Node, cls: Symbol) -> None:
        if c.type == "method_definition":
            name = text(c.child_by_field_name("name"))
            self._func(c, name, cls.id, c, "method", ["get"] if any(text(x) == "get" for x in c.children
                                                                   if not x.is_named) else [])
        elif c.type == "pair":
            key = c.child_by_field_name("key")
            v = _unwrap(c.child_by_field_name("value"))
            if key is None or v is None:
                return
            name = _string(key) if key.type == "string" else text(key)
            if v.type in FUNC_VALUES:
                self._func(v, name, cls.id, c, "method")
            elif v.type == "object" and name in ("methods", "computed", "watch", "actions", "getters", "mutations"):
                self._object_members(v, cls)  # Vue / Vuex / Pinia: these become the component's own methods

    def _collect(self, node: Node, parent: str | None) -> None:
        pending_decorators: list[str] = []
        for child in node.named_children:
            if child.type == "decorator":
                pending_decorators.append(text(child).lstrip("@").strip())
                continue
            self._collect_one(child, parent, child, pending_decorators)
            pending_decorators = []

    def _collect_one(self, n: Node, parent: str | None, decl: Node, decorators: list[str]) -> None:
        t = n.type
        if t == "export_statement":
            decs = decorators + self._decorators(n)
            d = n.child_by_field_name("declaration")
            is_default = any(c.type == "default" or text(c) == "default" for c in n.children if not c.is_named)
            if d is not None:
                before = set(self.symbols)
                self._collect_one(d, parent, n, decs)
                if parent is None:
                    for q in [q for q in self.symbols if q not in before and "." not in q]:
                        self.exports["default" if is_default else q] = q
                    if d.type in ("lexical_declaration", "variable_declaration"):
                        for v in d.named_children:
                            nm = v.child_by_field_name("name")
                            if v.type == "variable_declarator" and nm is not None and nm.type == "identifier":
                                self.exports.setdefault(text(nm), text(nm))
                    elif d.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                        nm = d.child_by_field_name("name")
                        if nm is not None:
                            self.exports.setdefault(text(nm), text(nm))
                return
            value = n.child_by_field_name("value")
            if is_default and value is not None and parent is None:
                v = _unwrap(value)
                if v is not None and v.type in FUNC_VALUES | {"class"} | FUNC_DECLS | CLASS_DECLS:
                    nm = v.child_by_field_name("name")
                    name = text(nm) if nm is not None else (self.component_name or "default")
                    sym = self._class(v, name, None, n, decs) if "class" in v.type else self._func(v, name, None, n)
                    self.exports["default"] = sym.id
                elif v is not None and v.type == "object":
                    sym = self._object(v, self.component_name or "default", None, n)
                    self.exports["default"] = sym.id if sym else None
                elif v is not None and v.type == "identifier":
                    self.exports["default"] = text(v)
                return
            if parent is None:
                for clause in n.named_children:
                    if clause.type == "export_clause" and n.child_by_field_name("source") is None:
                        for spec in clause.named_children:
                            name = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias")
                            if name is not None:
                                self.exports[text(alias or name)] = text(name)
            return
        if t in FUNC_DECLS:
            nm = n.child_by_field_name("name")
            if nm is not None:
                self._func(n, text(nm), parent, decl, decorators=decorators)
        elif t in CLASS_DECLS:
            nm = n.child_by_field_name("name")
            if nm is not None:
                self._class(n, text(nm), parent, decl, decorators + self._decorators(n))
        elif t == "interface_declaration" and parent is None:
            nm = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            if nm is not None:
                bases = []
                for c in n.named_children:
                    if c.type == "extends_type_clause":
                        bases += [text(x) for x in c.named_children]
                sym = self._add("class", text(nm), None, n, decl, body, decorators=["interface"], bases=bases)
                self._members(body, sym)
        elif t in ("lexical_declaration", "variable_declaration"):
            for v in n.named_children:
                if v.type != "variable_declarator":
                    continue
                nm, value = v.child_by_field_name("name"), _unwrap(v.child_by_field_name("value"))
                if value is not None and value.type == "assignment_expression" and parent is None:
                    lefts, value = self._chain(value)
                    if nm is not None and any(x in ("module.exports", "exports") for x in lefts):
                        self.exports["default"] = text(nm)
                if nm is None or nm.type != "identifier" or value is None:
                    if parent is None and nm is not None:
                        self._global(nm, n)
                    continue
                name = text(nm)
                if value.type in FUNC_VALUES:
                    self._func(value, name, parent, decl)
                elif value.type == "class":
                    self._class(value, name, parent, decl, decorators)
                elif value.type == "object" and parent is None:
                    if self._object(value, name, parent, decl) is None:
                        self._global(nm, n)
                elif parent is None:
                    self._global(nm, n)
        elif t == "expression_statement" and parent is None:
            self._module_assignment(n)
        elif t in ("if_statement", "try_statement", "statement_block") and parent is None:
            if t == "if_statement" and self.main_guard_line is None and re.fullmatch(
                    r"\(?\s*require\.main\s*===?\s*module\s*\)?", text(n.child_by_field_name("condition"))):
                self.main_guard_line = n.start_point[0] + 1
            for c in n.named_children:
                if c.type in ("statement_block", "else_clause", "catch_clause", "finally_clause"):
                    self._collect(c, None) if c.type == "statement_block" else self._collect(
                        c.child_by_field_name("body") or c, None)

    def _global(self, name: Node, stmt: Node) -> None:
        self.globals.append({"name": text(name), "line": stmt.start_point[0] + 1})

    @staticmethod
    def _chain(e: Node) -> tuple[list[str], Node | None]:
        """`a = b = c = value`: the names on the left, and the value."""
        lefts = []
        while e is not None and e.type == "assignment_expression":
            lefts.append(text(e.child_by_field_name("left")))
            e = e.child_by_field_name("right")
        return lefts, _unwrap(e)

    def _module_assignment(self, stmt: Node) -> None:
        """CommonJS: module.exports = ..., exports.name = ...; Foo.prototype.bar = function () {};
        and app.listen = function () {} on an object the file builds up."""
        e = stmt.named_children[0] if stmt.named_child_count else None
        if e is None or e.type != "assignment_expression":
            return
        lefts, right = self._chain(e)
        if right is None:
            return
        for lt in lefts[1:]:  # exports = module.exports = createApplication
            if lt in ("module.exports", "exports") and right.type == "identifier":
                self.exports["default"] = text(right)
        lt = lefts[0]
        m = re.fullmatch(r"(?:module\.)?exports\.(\w+)|module\.exports", lt)
        if m:
            name = m.group(1)
            if right.type in FUNC_VALUES:
                nm = right.child_by_field_name("name")
                sym = self._func(right, name or (text(nm) if nm is not None else "exports"), None, stmt)
                self.exports[name or "default"] = sym.id
            elif right.type == "class":
                nm = right.child_by_field_name("name")
                sym = self._class(right, name or (text(nm) if nm is not None else "exports"), None, stmt, [])
                self.exports[name or "default"] = sym.id
            elif right.type == "identifier":
                self.exports[name or "default"] = text(right)
            elif right.type == "object" and not name:
                for c in right.named_children:
                    if c.type == "shorthand_property_identifier":
                        self.exports[text(c)] = text(c)
                    elif c.type == "pair":
                        k, v = c.child_by_field_name("key"), _unwrap(c.child_by_field_name("value"))
                        if k is None or v is None:
                            continue
                        if v.type == "identifier":
                            self.exports[text(k)] = text(v)
                        elif v.type in FUNC_VALUES:
                            self.exports[text(k)] = self._func(v, text(k), None, c).id
                    elif c.type == "method_definition":
                        nm = text(c.child_by_field_name("name"))
                        self.exports[nm] = self._func(c, nm, None, c).id
            return
        m = re.fullmatch(r"(\w+)\.prototype\.(\w+)", lt) or re.fullmatch(r"(\w+)\.(\w+)", lt)
        if m and right.type in FUNC_VALUES:
            owner = self.symbols.get(m.group(1))
            if owner is None and m.group(1) in {g["name"] for g in self.globals} | set(self._objects):
                owner = self._objects.get(m.group(1)) or self._object_holder(m.group(1), stmt)
            if owner is not None and owner.kind in ("function", "class"):
                self._func(right, m.group(2), owner.id, stmt, "method")
                owner.end_line = max(owner.end_line, stmt.end_point[0] + 1)

    def _object_holder(self, name: str, stmt: Node) -> Symbol:
        """`var app = {}` followed by `app.listen = function () {}`: app reads like an object of functions."""
        decl = next((g for g in self.globals if g["name"] == name), None)
        sym = self._add("class", name, None, stmt, stmt, None, decorators=["object"])
        if decl is not None:
            sym.start_line = decl["line"]
        self.bodies[sym.id] = stmt
        self._objects[name] = sym
        return sym

    # ------------------------------------------------------------------ imports
    def _imports(self, root: Node) -> None:
        stack = [root]
        while stack:
            n = stack.pop()
            t = n.type
            if t == "import_statement":
                self._esm_import(n)
                continue
            if t == "export_statement" and n.child_by_field_name("source") is not None:
                self._reexport(n)
                continue
            if t == "call_expression":
                fn = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                first = args.named_children[0] if args is not None and args.named_child_count else None
                spec = _string(first)
                if spec is not None and fn is not None and (text(fn) == "require" or fn.type == "import"):
                    self._require(n, spec, dynamic=fn.type == "import")
            stack.extend(reversed(n.named_children))

    def _record(self, spec: str, names: list[dict], alias: str | None, node: Node, **extra) -> None:
        self.imports.append({"module": spec, "names": names, "alias": alias, "line": node.start_point[0] + 1,
                             **extra})

    def _esm_import(self, n: Node) -> None:
        spec = _string(n.child_by_field_name("source"))
        if spec is None:
            return
        type_only = any(text(c) == "type" for c in n.children if not c.is_named)
        names, alias = [], None
        clause = next((c for c in n.named_children if c.type == "import_clause"), None)
        for c in (clause.named_children if clause is not None else []):
            if c.type == "identifier":  # import React from "react"
                names.append({"name": "default", "alias": text(c)})
                self.import_names[text(c)] = (spec, "default")
            elif c.type == "namespace_import":  # import * as api from "./api"
                ident = next((x for x in c.named_children if x.type == "identifier"), None)
                if ident is not None:
                    alias = text(ident)
                    self.import_names[alias] = (spec, "*")
            elif c.type == "named_imports":
                for s in c.named_children:
                    if s.type != "import_specifier":
                        continue
                    nm, al = s.child_by_field_name("name"), s.child_by_field_name("alias")
                    nm_text = _string(nm) if nm is not None and nm.type == "string" else text(nm)
                    names.append({"name": nm_text, "alias": text(al) if al is not None else None})
                    self.import_names[text(al) if al is not None else nm_text] = (spec, nm_text)
        self._record(spec, names, alias, n, **({"types_only": True} if type_only else {}))

    def _reexport(self, n: Node) -> None:
        spec = _string(n.child_by_field_name("source"))
        if spec is None:
            return
        clause = next((c for c in n.named_children if c.type == "export_clause"), None)
        ns = next((c for c in n.named_children if c.type == "namespace_export"), None)
        names = []
        if clause is not None:
            for s in clause.named_children:
                nm, al = s.child_by_field_name("name"), s.child_by_field_name("alias")
                if nm is not None:
                    names.append({"name": text(nm), "alias": text(al) if al is not None else None})
                    self.exports[text(al) if al is not None else text(nm)] = ["from", spec, text(nm)]
        elif ns is not None:  # export * as utils from "./utils"
            ident = next((x for x in ns.named_children if x.type == "identifier"), None)
            if ident is not None:
                self.exports[text(ident)] = ["from", spec, "*"]
        else:
            self.export_stars.append(spec)
            names.append({"name": "*", "alias": None})
        self._record(spec, names, None, n, reexport=True)

    def _require(self, call: Node, spec: str, dynamic: bool) -> None:
        holder = call.parent
        while holder is not None and holder.type in ("await_expression", "parenthesized_expression"):
            holder = holder.parent
        local = holder is not None and self._inside_function(call)
        if holder is not None and holder.type == "variable_declarator":
            target = holder.child_by_field_name("name")
            if target is not None and target.type == "identifier":  # const x = require("x")
                self.import_names.setdefault(text(target), (spec, "*" if not dynamic else "*"))
                self._record(spec, [], text(target), call, local=local, **({"dynamic": True} if dynamic else {}))
                return
            if target is not None and target.type == "object_pattern":  # const { a, b: c } = require("x")
                names = []
                for p in target.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        names.append({"name": text(p), "alias": None})
                        self.import_names.setdefault(text(p), (spec, text(p)))
                    elif p.type == "pair_pattern":
                        k, v = p.child_by_field_name("key"), p.child_by_field_name("value")
                        if k is not None and v is not None and v.type == "identifier":
                            names.append({"name": text(k), "alias": text(v)})
                            self.import_names.setdefault(text(v), (spec, text(k)))
                self._record(spec, names, None, call, local=local)
                return
        self._record(spec, [], None, call, local=local, **({"dynamic": True} if dynamic else {}))

    def _inside_function(self, node: Node) -> bool:
        p = node.parent
        while p is not None:
            if p.type in FUNC_VALUES | FUNC_DECLS | {"method_definition"}:
                return True
            p = p.parent
        return False

    # ------------------------------------------------------------------ where values come from
    # Same "desc" format as the Python analyzer: ["T:<type>", steps] or ["^<name>", steps].
    def _own_nodes(self, sym: Symbol):
        """Nodes whose code belongs to sym: into callbacks and inline functions, not into named ones."""
        start = self.bodies.get(sym.id)
        if start is None:
            return
        if sym.kind == "class" and sym.decorators[:1] != ["object"]:
            # a class's own code: field initializers that aren't methods
            for m in start.named_children:
                if m.type in ("public_field_definition", "field_definition") and m.id not in self.sym_nodes:
                    v = m.child_by_field_name("value")
                    if v is not None and v.id not in self.sym_nodes:
                        yield from self._walk(v)
            return
        if sym.kind == "class":  # an object literal: the values that aren't its functions
            for m in start.named_children:
                if m.type == "pair" and m.id not in self.sym_nodes:
                    v = m.child_by_field_name("value")
                    if v is not None and v.id not in self.sym_nodes:
                        yield from self._walk(v, top=False) if v.type != "identifier" else iter([v])
                elif m.type == "shorthand_property_identifier":
                    yield m
            return
        params = self.symbols[sym.id].node.child_by_field_name("parameters") if sym.kind != "module" else None
        if params is not None:
            yield from self._walk(params)  # default values
        yield from self._walk(start, top=True)

    def _walk(self, node: Node, top: bool = False):
        stack = [node] if not top else list(reversed(node.named_children)) if node.type in (
            "statement_block", "program", "class_body", "object") else [node]
        while stack:
            n = stack.pop()
            if n.id in self.sym_nodes and n is not node:
                continue
            if n.type in ("import_statement",):
                continue
            yield n
            stack.extend(reversed(n.named_children))

    def _own_index(self, sym: Symbol):
        hit = self._own_cache.get(sym.id)
        if hit is None:
            calls, binds, members, jsx, idents = [], [], [], [], []
            for n in self._own_nodes(sym):
                t = n.type
                if t in ("call_expression", "new_expression"):
                    calls.append(n)
                elif t in ("variable_declarator", "assignment_expression", "for_in_statement"):
                    binds.append(n)
                elif t == "member_expression":
                    members.append(n)
                elif t in JSX_TAGS:
                    jsx.append(n)
                elif t in ("identifier", "shorthand_property_identifier"):
                    idents.append(n)
            hit = self._own_cache[sym.id] = (calls, binds, members, jsx, idents)
        return hit

    def _self_class(self, sym: Symbol) -> Symbol | None:
        p: str | None = sym.id
        while p:
            s = self.symbols[p]
            if s.kind == "class":
                return s
            # an arrow function keeps `this` from where it's written; a plain function gets its own
            p = s.parent
        return None

    def _expr_desc(self, sym: Symbol, node: Node | None, descs, depth: int = 0) -> list | None:
        if node is None or depth > 8:
            return None
        t = node.type
        if t == "identifier":
            name = text(node)
            return descs[name] if name in descs else None
        if t == "this":
            cls = self._self_class(sym)
            return ["T:" + cls.qualname, []] if cls else None
        if t == "member_expression":
            full = text(node)
            if full in descs:
                return descs[full]
            base = self._expr_desc(sym, node.child_by_field_name("object"), descs, depth + 1)
            prop = node.child_by_field_name("property")
            return [base[0], base[1] + [text(prop)]] if base and prop is not None else None
        if t in ("call_expression", "new_expression"):
            fn = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            if fn is None:
                return None
            if t == "call_expression" and text(fn) in WRAPPERS:
                return None
            if self._is_name_path(fn, descs):
                return ["^" + self._name_ref(sym, text(fn)), []]
            if fn.type == "member_expression":
                base = self._expr_desc(sym, fn.child_by_field_name("object"), descs, depth + 1)
                prop = fn.child_by_field_name("property")
                return [base[0], base[1] + [text(prop) + "()"]] if base and prop is not None else None
            return None
        if t == "subscript_expression":
            base = self._expr_desc(sym, node.child_by_field_name("object"), descs, depth + 1)
            return [base[0], base[1] + ["[]"]] if base else None
        if t == "as_expression":
            types = [c for c in node.named_children[1:]]
            return ["T:" + text(types[0]), []] if types else None
        if t in ("await_expression", "parenthesized_expression", "non_null_expression", "satisfies_expression"):
            return self._expr_desc(sym, node.named_children[0] if node.named_child_count else None, descs, depth + 1)
        if t == "ternary_expression":
            for f in ("consequence", "alternative"):
                d = self._expr_desc(sym, node.child_by_field_name(f), descs, depth + 1)
                if d:
                    return d
        if t == "binary_expression":
            for f in ("left", "right"):
                d = self._expr_desc(sym, node.child_by_field_name(f), descs, depth + 1)
                if d:
                    return d
        return None

    def _is_name_path(self, node: Node, descs) -> bool:
        while node.type == "member_expression":
            node = node.child_by_field_name("object")
            if node is None:
                return False
        if node.type != "identifier":
            return False
        root = text(node)
        return root not in descs and (root in self.import_names or root in self.symbols or root in self.classes)

    def _name_ref(self, sym: Symbol, name: str) -> str:
        """A name as the file knows it: a function nested where it's used comes before the file's own."""
        root, _, tail = name.partition(".")
        p = sym.id if sym.kind != "module" else None
        while p:
            cand = f"{self.symbols[p].qualname}.{root}"
            if cand in self.symbols and self.symbols[cand].kind in ("function", "class"):
                return cand + (f".{tail}" if tail else "")
            p = self.symbols[p].parent
        return name

    def _bind_pattern(self, pat: Node, desc: list | None, descs: _Descs, pos: int) -> None:
        if pat.type in ("identifier", "member_expression"):
            descs.bind(text(pat), pos, desc)
        elif pat.type == "object_pattern":
            for p in pat.named_children:
                if p.type == "shorthand_property_identifier_pattern":
                    descs.bind(text(p), pos, [desc[0], desc[1] + [text(p)]] if desc else None)
                elif p.type == "pair_pattern":
                    k, v = p.child_by_field_name("key"), p.child_by_field_name("value")
                    if k is not None and v is not None:
                        self._bind_pattern(v, [desc[0], desc[1] + [text(k)]] if desc else None, descs, pos)
        elif pat.type == "array_pattern":
            for k, p in enumerate(pat.named_children):
                self._bind_pattern(p, [desc[0], desc[1] + [f"#{k}"]] if desc else None, descs, pos)

    def _infer_var_types(self, sym: Symbol) -> _Descs:
        cached = self._descs_cache.get(sym.id)
        if cached is not None:
            return cached
        descs = _Descs()
        parent = self.symbols.get(sym.parent) if sym.parent else None
        if parent is not None and parent.kind in ("function", "method"):
            for k, d in self._infer_var_types(parent).latest().items():
                descs.bind(k, -1, d)
        params = sym.node.child_by_field_name("parameters") if sym.kind in ("function", "method") else None
        for p in (params.named_children if params is not None else []):
            pat = p.child_by_field_name("pattern")
            ann = _type_text(p.child_by_field_name("type"))
            if pat is not None and ann:
                if pat.type == "identifier":
                    descs.bind(text(pat), -1, ["T:" + ann, []])
                else:
                    self._bind_pattern(pat, ["T:" + ann, []], descs, -1)
            if any(c.type == "accessibility_modifier" for c in p.children) and pat is not None and ann:
                descs.bind("this." + text(pat), -1, ["T:" + ann, []])  # constructor(private api: Api)
        for n in self._own_index(sym)[1]:
            if n.type == "variable_declarator":
                name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
                ann = _type_text(n.child_by_field_name("type"))
                if name is None:
                    continue
                here, pos = descs.at(n.start_byte), n.end_byte
                d = ["T:" + ann, []] if ann else self._expr_desc(sym, value, here) if value is not None else None
                self._bind_pattern(name, d, descs, pos)
            elif n.type == "assignment_expression":
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                if left is not None and right is not None:
                    self._bind_pattern(left, self._expr_desc(sym, right, descs.at(n.start_byte)), descs, n.end_byte)
            elif n.type == "for_in_statement":
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                of = any(text(c) == "of" for c in n.children if not c.is_named)
                if left is not None and right is not None and of:
                    d = self._expr_desc(sym, right, descs.at(n.start_byte))
                    self._bind_pattern(left, [d[0], d[1] + ["in"]] if d else None, descs, right.end_byte)
        self._descs_cache[sym.id] = descs
        return descs

    def _this_assignments(self, sym: Symbol) -> dict[str, list]:
        out: dict[str, list] = {}
        for k, entries in self._infer_var_types(sym).hist.items():
            if k.startswith("this.") and k.count(".") == 1:
                for _, d in entries:
                    if d is not None and d not in out.setdefault(k[5:], []):
                        out[k[5:]].append(d)
        return out

    def _class_attrs(self, cls: Symbol) -> None:
        body = self.bodies.get(cls.id)
        for m in (body.named_children if body is not None else []):
            if m.type not in ("public_field_definition", "field_definition", "property_signature"):
                continue
            name_node = m.child_by_field_name("name") or m.child_by_field_name("property")
            if name_node is None or m.id in self.sym_nodes:
                continue
            ann = _type_text(m.child_by_field_name("type"))
            if ann:
                cls.attrs[text(name_node)] = [["T:" + ann, []]]
                self._declared.setdefault(cls.id, set()).add(text(name_node))
            else:
                d = self._expr_desc(cls, m.child_by_field_name("value"), {})
                if d:
                    cls.attrs[text(name_node)] = [d]
        ctor = self.symbols.get(f"{cls.qualname}.constructor")
        if ctor is not None:
            params = ctor.node.child_by_field_name("parameters")
            for p in (params.named_children if params is not None else []):
                if any(c.type == "accessibility_modifier" for c in p.children):
                    pat, ann = p.child_by_field_name("pattern"), _type_text(p.child_by_field_name("type"))
                    if pat is not None and ann:
                        cls.attrs[text(pat)] = [["T:" + ann, []]]
                        self._declared.setdefault(cls.id, set()).add(text(pat))

    def _return_desc(self, sym: Symbol) -> list | None:
        body = sym.node.child_by_field_name("body") if sym.node is not None else None
        if body is None:
            return None
        if body.type != "statement_block":  # x => expression
            return self._expr_desc(sym, body, self._infer_var_types(sym))
        descs = None
        for n in self._own_nodes(sym):
            if n.type == "return_statement" and n.named_child_count:
                if descs is None:
                    descs = self._infer_var_types(sym)
                d = self._expr_desc(sym, n.named_children[0], descs.at(n.start_byte))
                if d:
                    return d
        return None

    # ------------------------------------------------------------------ calls
    def _place(self, node: Node) -> dict:
        row = node.start_point[0]
        col = len(self.src[self._line_starts[row]:node.start_byte].decode("utf-8", "replace"))
        return {"line": row + 1, "col": col, "len": len(text(node))}

    def _local(self, sym: Symbol, name: str) -> str | None:
        """The symbol a plain name means inside sym: nested functions first, then outward, then the file's."""
        p = sym.id if sym.kind != "module" else None
        while p:
            cand = f"{self.symbols[p].qualname}.{name}"
            if cand in self.symbols and self.symbols[cand].kind in ("function", "class"):
                return cand
            p = self.symbols[p].parent
        if name in self.symbols and self.symbols[name].kind in ("function", "class"):
            return name
        return None

    def _target(self, q: str, label: str) -> dict:
        s = self.symbols[q]
        if s.kind == "class":
            ctor = f"{q}.constructor"
            return {"name": label, "kind": "internal", "target": ctor if ctor in self.symbols else q}
        return {"name": label, "kind": "internal", "target": q}

    def _method_in(self, cls: Symbol, name: str, depth: int = 0) -> str | None:
        cand = f"{cls.qualname}.{name}"
        if cand in self.symbols:
            return cand
        if depth < 5:
            for b in cls.bases:
                base = self.classes.get(b.split("<")[0].strip())
                if base is not None:
                    r = self._method_in(base, name, depth + 1)
                    if r:
                        return r
        return None

    def _resolve(self, sym: Symbol, fn: Node, descs) -> dict:
        label = text(fn)[:80]
        if fn.type == "identifier":
            name = text(fn)
            local = self._local(sym, name)
            if local:
                return self._target(local, label)
            if name in self.import_names:
                return {"name": label, "kind": "external", "module": self.import_names[name][0], "ref": name}
            if name in GLOBALS:
                return {"name": label, "kind": "builtin"}
            return {"name": label, "kind": "unknown", "ref": name}  # a global from another script, perhaps
        if fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            prop = fn.child_by_field_name("property")
            if obj is None or prop is None:
                return {"name": label, "kind": "unknown"}
            attr = text(prop)
            if obj.type == "this":
                cls = self._self_class(sym)
                if cls is not None:
                    m = self._method_in(cls, attr)
                    if m:
                        return {**self._target(m, label), "virtual": cls.qualname}
            if obj.type == "super":
                cls = self._self_class(sym)
                if cls is not None:
                    for b in cls.bases:
                        base = self.classes.get(b.split("<")[0].strip())
                        if base is not None:
                            m = self._method_in(base, attr)
                            if m:
                                return self._target(m, label)
                    if cls.bases:
                        return {"name": label, "kind": "unknown", "recv": ["T:" + cls.bases[0], []], "attr": attr,
                                "super": True}
                return {"name": label, "kind": "unknown"}
            if obj.type == "identifier":
                o = text(obj)
                local = self._local(sym, o)
                if local and f"{local}.{attr}" in self.symbols and o not in descs:
                    return self._target(f"{local}.{attr}", label)  # Klass.create(), api.get()
                if o in self.import_names and o not in descs:
                    return {"name": label, "kind": "external", "module": self.import_names[o][0],
                            "ref": f"{o}.{attr}"}
                if o in GLOBALS and o not in descs and not local:
                    return {"name": label, "kind": "builtin"}
            desc = self._expr_desc(sym, obj, descs)
            if desc is not None:
                return {"name": label, "kind": "unknown", "recv": desc, "attr": attr}
            return {"name": label, "kind": "unknown"}
        return {"name": label, "kind": "unknown"}

    def _collect_calls(self, sym: Symbol) -> None:
        descs = self._infer_var_types(sym)
        calls, _, members, jsx, idents = self._own_index(sym)
        callee_ids: set[int] = set()
        for n in calls:
            fn = n.child_by_field_name("function") or n.child_by_field_name("constructor")
            if fn is None or fn.type == "import" or text(fn) == "require":
                continue
            callee_ids.add(fn.id)
            info = self._resolve(sym, fn, descs.at(n.start_byte))
            name_node = fn.child_by_field_name("property") if fn.type == "member_expression" else fn
            sym.calls.append({**info, **self._place(name_node if name_node is not None else fn)})
        for n in jsx:  # <Header /> and <ui.Button> render another component
            name = n.child_by_field_name("name")
            if name is None or (name.type == "identifier" and not _is_cap(text(name))):
                continue
            if name.type == "member_expression":
                info = self._resolve(sym, name, descs.at(n.start_byte))
            elif name.type == "identifier":
                info = self._resolve(sym, name, descs.at(n.start_byte))
            else:
                continue
            info["name"] = f"<{text(name)}>"
            info["jsx"] = True
            callee_ids.add(name.id)
            sym.calls.append({**info, **self._place(name)})
        for n in members:  # reading obj.prop: a getter runs, or a method is handed on to be called later
            if n.id in callee_ids or self._is_target(n):
                continue
            parent = n.parent
            if parent is not None and parent.type == "member_expression" and parent.child_by_field_name(
                    "object") is not None and parent.child_by_field_name("object").id == n.id \
                    and parent.id in callee_ids:
                pass
            obj, prop = n.child_by_field_name("object"), n.child_by_field_name("property")
            if obj is None or prop is None:
                continue
            here = descs.at(n.start_byte)
            if obj.type == "this":
                cls = self._self_class(sym)
                m = self._method_in(cls, text(prop)) if cls is not None else None
                if m:
                    sym.calls.append({"name": text(n)[:80], "kind": "internal", "target": m, "access": True,
                                      **self._place(prop)})
                    continue
            if obj.type == "identifier" and text(obj) in self.import_names and text(obj) not in here:
                sym.uses.append({"name": text(n)[:80], "ref": f"{text(obj)}.{text(prop)}", **self._place(prop)})
                continue
            desc = self._expr_desc(sym, obj, here)
            if desc is not None:
                sym.uses.append({"name": text(n)[:80], "recv": desc, "attr": text(prop), **self._place(prop)})
        for n in idents:  # a function handed on by name: onClick={save}, useEffect(load), .then(done)
            if n.id in callee_ids or not (n.type == "shorthand_property_identifier" or self._is_value_use(n)):
                continue
            name = text(n)
            local = self._local(sym, name)
            if local and self.symbols[local].kind == "function" and local != sym.id:
                sym.calls.append({**self._target(local, name), "access": True, **self._place(n)})
            elif name in self.import_names and name not in descs.at(n.start_byte):
                sym.uses.append({"name": name, "ref": name, **self._place(n)})

    @staticmethod
    def _is_target(n: Node) -> bool:
        p = n.parent
        return p is not None and p.type in ("assignment_expression", "augmented_assignment_expression") \
            and p.child_by_field_name("left") is not None and p.child_by_field_name("left").id == n.id

    @staticmethod
    def _is_value_use(n: Node) -> bool:
        p = n.parent
        if p is None:
            return False
        if p.type in ("arguments", "jsx_expression", "array", "return_statement", "spread_element"):
            return True
        if p.type == "pair" and p.child_by_field_name("value") is not None \
                and p.child_by_field_name("value").id == n.id:
            return True
        if p.type == "variable_declarator" and p.child_by_field_name("value") is not None \
                and p.child_by_field_name("value").id == n.id:
            return True
        return p.type == "shorthand_property_identifier"

    # ------------------------------------------------------------------ output
    def _attr_and_refs(self) -> tuple[set[str], dict[str, int]]:
        attrs: set[str] = set()
        refs: dict[str, int] = {}
        stack = [self.tree.root_node]
        while stack:
            n = stack.pop()
            if n.type == "member_expression":
                p = n.child_by_field_name("property")
                if p is not None:
                    attrs.add(text(p))
            elif n.type == "identifier" and self._is_value_use(n):
                refs[text(n)] = refs.get(text(n), 0) + 1
            elif n.type == "shorthand_property_identifier":
                refs[text(n)] = refs.get(text(n), 0) + 1
            stack.extend(n.named_children)
        return attrs, refs

    def _export(self, flowcharts_on: bool) -> dict:
        attr_names, refs = self._attr_and_refs()
        symbols_out, flowcharts = [], {}
        for sym in self.symbols.values():
            ext = sorted({c["module"] for c in sym.calls if c["kind"] == "external" and c.get("module")})
            extra: dict = {}
            if sym.attrs:
                extra["attrs"] = sym.attrs
            if sym.rdesc:
                extra["rdesc"] = sym.rdesc
            if sym.uses:
                extra["uses"] = sym.uses
            if sym.kind in ("function", "class") and refs.get(sym.name):
                extra["referenced"] = True
            if sym.kind == "function" and _is_cap(sym.name) and self._renders(sym):
                extra["component"] = True
            elif sym.kind == "function" and re.match(r"use[A-Z0-9]", sym.name):
                extra["hook"] = True
            symbols_out.append({**extra,
                "id": sym.id, "kind": sym.kind, "name": sym.name, "qualname": sym.qualname, "parent": sym.parent,
                "start_line": sym.start_line, "end_line": sym.end_line, "is_async": sym.is_async,
                "params": sym.params, "decorators": sym.decorators, "bases": sym.bases, "returns": sym.returns,
                "docstring": sym.docstring, "complexity": sym.complexity, "calls": sym.calls,
                "called_by": sym.called_by, "uses_modules": ext,
                "children": [s.id for s in self.symbols.values() if s.parent == sym.id],
            })
            if not flowcharts_on:
                continue
            if sym.kind in ("function", "method"):
                body = sym.node.child_by_field_name("body")
                if body is None:
                    continue
                if body.type == "statement_block":
                    flowcharts[sym.id] = build_js_flowchart(body.named_children, sym, self.src)
                else:
                    flowcharts[sym.id] = build_js_flowchart([], sym, self.src, expression=body)
            elif sym.kind == "module":
                top = [n for n in self.tree.root_node.named_children if self._is_module_code(n)]
                if top:
                    flowcharts[sym.id] = build_js_flowchart(top, sym, self.src)
        lines = self.source.count("\n") + (0 if self.source.endswith("\n") else 1)
        return {
            "file": {
                "name": self.filename, "language": self.language, "lang": self.highlight, "lines": lines,
                "hash": hashlib.sha256(self.src).hexdigest()[:16],
                "docstring": self.symbols["<module>"].docstring, "has_errors": self.tree.root_node.has_error,
                "main_guard": self.main_guard_line,
            },
            "source": self.source,
            "imports": self.imports,
            "exports": self.exports,
            "export_stars": self.export_stars,
            "globals": self.globals,
            "attr_names": sorted(attr_names),
            "symbols": symbols_out,
            "call_edges": [],
            "flowcharts": flowcharts,
        }

    def _renders(self, sym: Symbol) -> bool:
        return any(n.type in JSX_TAGS or n.type in ("jsx_element", "jsx_fragment") for n in self._own_nodes(sym))

    def _is_module_code(self, n: Node) -> bool:
        if n.type in ("comment", "import_statement", "empty_statement", "interface_declaration",
                      "type_alias_declaration", "ambient_declaration") or n.type in FUNC_DECLS | CLASS_DECLS:
            return False
        if n.type == "export_statement":
            d = n.child_by_field_name("declaration")
            if d is None:
                v = n.child_by_field_name("value")
                return v is not None and v.id not in self.sym_nodes and _unwrap(v) is not None \
                    and _unwrap(v).id not in self.sym_nodes
            return self._is_module_code(d)
        if n.type in ("lexical_declaration", "variable_declaration"):
            for v in n.named_children:
                val = _unwrap(v.child_by_field_name("value")) if v.type == "variable_declarator" else None
                if val is None or val.id not in self.sym_nodes:
                    return True
            return False
        if n.type == "expression_statement" and n.named_child_count:
            e = n.named_children[0]
            if e.type == "assignment_expression":
                r = _unwrap(e.child_by_field_name("right"))
                return r is None or r.id not in self.sym_nodes
        return True


def analyze_js(filename: str, source: str, flowcharts: bool = True, ext: str | None = None,
               component: str | None = None) -> dict:
    """Analyze a JavaScript or TypeScript file. `ext` picks the grammar when the file name doesn't."""
    suffix = (ext or ("." + filename.rsplit(".", 1)[-1] if "." in filename else ".js")).lower()
    language, grammar, highlight = EXTENSIONS.get(suffix, EXTENSIONS[".js"])
    a = JsAnalyzer(filename, source, language, grammar, highlight)
    a.component_name = component
    return a.run(flowcharts)
