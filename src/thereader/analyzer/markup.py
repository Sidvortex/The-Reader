"""Vue and Svelte components, HTML pages and CSS stylesheets.

Components and pages: their <script> parts go through the JavaScript analyzer in place (everything else is blanked
out, so line and column numbers stay exact), and the template adds what it uses: the components it shows and the
functions its event handlers and expressions call. HTML pages also list the scripts and stylesheets they load and an
outline of their headings and elements with ids. CSS gets an outline of its rules and what it imports.
"""

from __future__ import annotations

import hashlib
import re

import tree_sitter_css
from tree_sitter import Language, Node, Parser

from .js_analyzer import EXTENSIONS, JsAnalyzer
from .python_analyzer import Symbol, text

_CSS = Parser(Language(tree_sitter_css.language()))

SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
STYLE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.S | re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
# compiler macros and runes that look like calls but belong to the framework
FRAMEWORK = {"defineProps", "defineEmits", "defineExpose", "withDefaults", "defineModel", "defineSlots",
             "defineOptions", "$state", "$derived", "$effect", "$props", "$bindable", "$inspect", "$host"}


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"\b{name}\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>]+))", attrs, re.I)
    if not m:
        return None
    return next(g for g in m.groups()[1:] if g is not None)


def _mask(source: str, keep: list[tuple[int, int]]) -> str:
    """Blank out everything except the kept character ranges, leaving line breaks so positions don't move."""
    out = [c if c == "\n" else " " for c in source]
    for a, b in keep:
        out[a:b] = source[a:b]
    return "".join(out)


def _pascal(tag: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in tag.split("-")) if "-" in tag else tag


def _line_col(source: str, offset: int) -> tuple[int, int]:
    line = source.count("\n", 0, offset) + 1
    return line, offset - (source.rfind("\n", 0, offset) + 1)


class _ScriptHost(JsAnalyzer):
    """The JavaScript analyzer, plus what the markup around the scripts uses."""

    template_spans: list[tuple[int, int, str]] = []  # (start, end, kind): "tags" or "expr"
    original = ""

    def _collect_calls(self, sym: Symbol) -> None:
        super()._collect_calls(sym)
        if sym.kind == "module":
            self._template_calls(sym)

    def _known(self, name: str) -> str | None:
        local = self._local(self.symbols["<module>"], name)
        if local:
            return local
        for cls in self.classes.values():  # Vue options API: the template calls the component's methods
            if cls.decorators[:1] == ["object"] and f"{cls.qualname}.{name}" in self.symbols:
                return f"{cls.qualname}.{name}"
        return None

    def _template_calls(self, module: Symbol) -> None:
        src = self.original
        for start, end, kind in self.template_spans:
            chunk = src[start:end]
            if kind == "tags":  # <TodoItem>, <todo-item>, <ui.Button>
                for m in re.finditer(r"<([A-Z][\w.]*|[a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", chunk):
                    name = _pascal(m.group(1))
                    line, col = _line_col(src, start + m.start(1))
                    place = {"line": line, "col": col, "len": len(m.group(1))}
                    known = self._known(name)
                    if known:
                        module.calls.append({**self._target(known, f"<{name}>"), "jsx": True, **place})
                    elif name.split(".")[0] in self.import_names:
                        module.calls.append({"name": f"<{name}>", "kind": "external", "jsx": True, "ref": name,
                                             "module": self.import_names[name.split(".")[0]][0], **place})
                continue
            for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)(\s*\()?", chunk):  # names in expressions
                name, called = m.group(1), bool(m.group(2))
                if name in FRAMEWORK:
                    continue
                line, col = _line_col(src, start + m.start(1))
                place = {"line": line, "col": col, "len": len(name)}
                known = self._known(name)
                if known and self.symbols[known].kind in ("function", "method"):
                    module.calls.append({**self._target(known, name), **({} if called else {"access": True}),
                                         **place})
                elif name in self.import_names and called:
                    module.calls.append({"name": name, "kind": "external", "ref": name,
                                         "module": self.import_names[name][0], **place})


def _expression_spans(source: str, start: int, end: int, style: str) -> list[tuple[int, int, str]]:
    """Where a template has code in it: Vue's {{ }} and @event / :prop / v-* attributes, Svelte's { }."""
    chunk = source[start:end]
    spans = [(start, end, "tags")]
    if style == "vue":
        for m in re.finditer(r"\{\{(.*?)\}\}", chunk, re.S):
            spans.append((start + m.start(1), start + m.end(1), "expr"))
        for m in re.finditer(r"(?:\s)(?:@|:|v-[\w-]+|#)[\w.:\-\[\]]*\s*=\s*(\"[^\"]*\"|'[^']*')", chunk):
            spans.append((start + m.start(1) + 1, start + m.end(1) - 1, "expr"))
    else:  # svelte, and the event attributes of plain HTML
        if style == "svelte":
            for m in re.finditer(r"\{([^{}]*)\}", chunk):
                spans.append((start + m.start(1), start + m.end(1), "expr"))
        for m in re.finditer(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*')", chunk, re.I):
            spans.append((start + m.start(1) + 1, start + m.end(1) - 1, "expr"))
    return spans


def _host(filename: str, source: str, scripts: list[tuple[int, int, str]], spans, language: str, highlight: str,
          component: str | None, flowcharts: bool) -> dict:
    exts = {ext for _, _, ext in scripts}
    ext = ".tsx" if ".tsx" in exts else ".ts" if ".ts" in exts else ".jsx" if ".jsx" in exts else ".js"
    _, grammar, _ = EXTENSIONS[ext]
    masked = _mask(source, [(a, b) for a, b, _ in scripts])
    a = _ScriptHost(filename, masked, "typescript" if ext in (".ts", ".tsx") else "javascript", grammar, highlight)
    a.component_name = component
    a.template_spans = spans
    a.original = source
    out = a.run(flowcharts)
    out["source"] = source
    out["file"]["language"] = language
    out["file"]["lang"] = highlight
    out["file"]["script"] = "typescript" if ext in (".ts", ".tsx") else "javascript"
    out["file"]["hash"] = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    out["symbols"][0]["role"] = "page" if language == "html" else "component"  # what "<module>" stands for
    return out


def _script_ext(attrs: str) -> str:
    lang = (_attr(attrs, "lang") or "").lower()
    return {"ts": ".ts", "typescript": ".ts", "tsx": ".tsx", "jsx": ".jsx"}.get(lang, ".js")


def analyze_component(filename: str, source: str, flowcharts: bool = True) -> dict:
    """A .vue or .svelte single-file component."""
    style = "vue" if filename.lower().endswith(".vue") else "svelte"
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    scripts, taken = [], []
    for m in SCRIPT.finditer(source):
        scripts.append((m.start(2), m.end(2), _script_ext(m.group(1))))
        taken.append((m.start(), m.end()))
    taken += [(m.start(), m.end()) for m in STYLE.finditer(source)]
    if style == "vue":
        m = re.search(r"^<template\b[^>]*>", source, re.M)
        ends = [x.end() for x in re.finditer(r"^</template\s*>", source, re.M)]
        spans = _expression_spans(source, m.end(), ends[-1], "vue") if m and ends else []
    else:  # everything outside <script> and <style> is the template
        spans, pos = [], 0
        for a, b in sorted(taken):
            if a > pos:
                spans += _expression_spans(source, pos, a, "svelte")
            pos = max(pos, b)
        if pos < len(source):
            spans += _expression_spans(source, pos, len(source), "svelte")
    out = _host(filename, source, scripts, spans, "vue" if style == "vue" else "svelte", style, _pascal(stem),
                flowcharts)
    out["file"]["component"] = True
    return out


# ---------------------------------------------------------------- HTML
LANDMARKS = {"header", "nav", "main", "section", "article", "aside", "footer", "form", "dialog", "template"}


def analyze_html(filename: str, source: str, flowcharts: bool = True) -> dict:
    """An HTML page: inline scripts like a script file, the files it loads, and an outline of the page."""
    clean = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)
    scripts, loads = [], []
    for m in SCRIPT.finditer(clean):
        attrs = m.group(1)
        src = _attr(attrs, "src")
        kind = (_attr(attrs, "type") or "").lower()
        line = clean.count("\n", 0, m.start()) + 1
        if src:
            loads.append({"module": src, "names": [], "alias": None, "line": line, "html": "script",
                          **({"esm": True} if kind == "module" else {})})
        elif kind in ("", "module", "text/javascript", "application/javascript", "text/babel"):
            scripts.append((m.start(2), m.end(2), ".jsx" if kind == "text/babel" else ".js"))
    for m in re.finditer(r"<link\b([^>]*)>", clean, re.I):
        rel = (_attr(m.group(1), "rel") or "").lower()
        href = _attr(m.group(1), "href")
        if href and ("stylesheet" in rel or "modulepreload" in rel):
            loads.append({"module": href, "names": [], "alias": None, "line": clean.count("\n", 0, m.start()) + 1,
                          "html": "style" if "stylesheet" in rel else "script"})
    body = [(0, len(clean))]
    spans = []
    for a, b in body:
        spans += _expression_spans(clean, a, b, "html")[1:]  # onclick="..." only; tags are plain HTML here
    out = _host(filename, source, scripts, spans, "html", "html", None, flowcharts)
    out["imports"] = loads + out["imports"]
    # the outline: headings, landmarks and elements with an id
    for m in re.finditer(r"<(h[1-3]|[a-z][a-z0-9-]*)\b([^>]*)>", clean, re.I):
        tag, attrs = m.group(1).lower(), m.group(2)
        ident = _attr(attrs, "id")
        if not (tag in ("h1", "h2", "h3") or ident or tag in LANDMARKS):
            continue
        if tag in ("h1", "h2", "h3"):
            close = re.search(rf"</{tag}\s*>", clean[m.end():], re.I)
            inner = re.sub(r"<[^>]+>", "", clean[m.end(): m.end() + close.start()]) if close else ""
            name = f"{tag}: {' '.join(inner.split())[:60]}" if inner.strip() else tag
        else:
            name = f"<{tag}" + (f" id=\"{ident}\"" if ident else "") + ">"
        line = clean.count("\n", 0, m.start()) + 1
        sid = f"{name}@{line}"
        out["symbols"].append(_plain_symbol(sid, "element", name, line, line))
    return out


def _plain_symbol(sid: str, kind: str, name: str, start: int, end: int, parent: str | None = None,
                  doc: str | None = None) -> dict:
    return {"id": sid, "kind": kind, "name": name, "qualname": name, "parent": parent, "start_line": start,
            "end_line": end, "is_async": False, "params": [], "decorators": [], "bases": [], "returns": None,
            "docstring": doc, "complexity": 1, "calls": [], "called_by": [], "uses_modules": [], "children": []}


# ---------------------------------------------------------------- CSS
def analyze_css(filename: str, source: str, flowcharts: bool = True) -> dict:
    """A stylesheet: its rules (inside @media and the like too), what it imports, its custom properties."""
    src = source.encode("utf-8")
    tree = _CSS.parse(src)
    symbols = [_plain_symbol("<module>", "module", "<module>", 1, tree.root_node.end_point[0] + 1)]
    imports, props = [], []
    seen: dict[str, int] = {}

    def comment_before(n: Node) -> str | None:
        p = n.prev_named_sibling
        if p is not None and p.type == "comment" and n.start_point[0] - p.end_point[0] <= 1:
            return text(p)[2:-2].strip().strip("*").strip() or None
        return None

    def add(n: Node, name: str, parent: str | None) -> str:
        name = " ".join(name.split())
        name = name if len(name) <= 70 else name[:69] + "…"
        sid = name if name not in seen else f"{name}@{n.start_point[0] + 1}"
        seen[name] = 1
        symbols.append(_plain_symbol(sid, "rule", name, n.start_point[0] + 1, n.end_point[0] + 1, parent,
                                     comment_before(n)))
        if parent:
            next(s for s in symbols if s["id"] == parent)["children"].append(sid)
        return sid

    def walk(node: Node, parent: str | None) -> None:
        for n in node.named_children:
            t = n.type
            if t == "rule_set":
                sel = n.child_by_field_name("selectors") or next(
                    (c for c in n.named_children if c.type == "selectors"), None)
                add(n, text(sel) if sel is not None else text(n).split("{")[0], parent)
                block = next((c for c in n.named_children if c.type == "block"), None)
                for d in (block.named_children if block is not None else []):
                    if d.type == "declaration":
                        prop = next((c for c in d.named_children if c.type == "property_name"), None)
                        if prop is not None and text(prop).startswith("--"):
                            props.append({"name": text(prop), "line": d.start_point[0] + 1})
                    elif d.type == "rule_set":  # CSS nesting
                        walk(block, parent)
                        break
            elif t in ("media_statement", "supports_statement", "at_rule", "keyframes_statement",
                       "scope_statement", "layer_statement", "container_statement"):
                head = text(n).split("{")[0]
                sid = add(n, head, parent)
                block = next((c for c in n.named_children if c.type in ("block", "keyframe_block_list")), None)
                if block is not None and t != "keyframes_statement":
                    walk(block, sid)
            elif t == "import_statement":
                m = re.search(r"""(?:url\(\s*)?["']?([^"')\s;]+)""", text(n)[len("@import"):])
                if m:
                    imports.append({"module": m.group(1), "names": [], "alias": None,
                                    "line": n.start_point[0] + 1, "html": "style"})

    walk(tree.root_node, None)
    root_doc = None
    first = next(iter(tree.root_node.named_children), None)
    if first is not None and first.type == "comment":
        root_doc = text(first)[2:-2].strip().strip("*").strip() or None
    symbols[0]["docstring"] = root_doc
    symbols[0]["children"] = [s["id"] for s in symbols[1:] if s["parent"] is None]
    lines = source.count("\n") + (0 if source.endswith("\n") else 1)
    return {
        "file": {"name": filename, "language": "css", "lang": "css", "lines": lines,
                 "hash": hashlib.sha256(src).hexdigest()[:16], "docstring": root_doc,
                 "has_errors": tree.root_node.has_error, "main_guard": None},
        "source": source, "imports": imports, "exports": {}, "export_stars": [], "globals": props,
        "attr_names": [], "symbols": symbols, "call_edges": [], "flowcharts": {},
    }
