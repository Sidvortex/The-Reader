"""Which file a JavaScript import means, what a name refers to across files, and what a TypeScript type is.

Covers relative paths with or without extensions (and `.js` written for a `.ts` file), `index` files, folders,
tsconfig/jsconfig `paths` and `baseUrl`, the common `@/` alias for `src/`, packages inside the repository
(workspaces), re-exports (`export * from`, `export { x } from`), CommonJS `module.exports` and `require`,
and the scripts and stylesheets an HTML page loads.
"""

from __future__ import annotations

import json
import posixpath
import re

JS_LANGS = {"javascript", "typescript", "vue", "svelte", "html"}
SCRIPT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
OTHER_EXTS = (".css", ".json", ".html")
SWAP = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


def loads_jsonc(raw: str) -> dict:
    """JSON with comments and trailing commas, as tsconfig.json allows."""
    out, i, n, in_str = [], 0, len(raw), False
    while i < n:
        c = raw[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(raw[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif raw.startswith("//", i):
            j = raw.find("\n", i)
            i = n if j == -1 else j
            continue
        elif raw.startswith("/*", i):
            j = raw.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    text = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _split_ts(t: str, sep: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in t:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


class JsResolver:
    _SEQ = {"Array", "ReadonlyArray", "Set", "ReadonlySet", "Iterable", "IterableIterator", "AsyncIterable",
            "AsyncIterableIterator", "Generator", "AsyncGenerator", "Iterator", "AsyncIterator", "NodeList",
            "HTMLCollection", "ArrayLike"}
    _MAP = {"Map", "ReadonlyMap", "Record", "WeakMap"}
    _SAME = {"Promise", "PromiseLike", "Awaited", "Partial", "Readonly", "Required", "NonNullable", "Ref",
             "ShallowRef", "ComputedRef", "MaybeRef", "Signal", "Writable", "Readable", "Observable"}
    _NOTHING = {"null", "undefined", "void", "never", "any", "unknown"}

    def __init__(self, mm, project_files: dict[str, bytes] | None, known: set[str] | None):
        self.mm = mm
        self.known = set(known or ()) | set(mm.symbols)
        self.cache: dict[tuple[str, str], str | None] = {}
        self.configs: dict[str, dict] = {}  # folder -> {"base": folder, "paths": {...}}
        self.packages: dict[str, tuple[str, dict]] = {}  # package name -> (folder, package.json)
        self.package_dirs: list[str] = []
        self.tables: dict[str, dict[str, tuple[str, str]]] = {}
        for path, raw in (project_files or {}).items():
            name = path.rsplit("/", 1)[-1]
            folder = posixpath.dirname(path)
            if name in ("tsconfig.json", "jsconfig.json") or re.fullmatch(r"tsconfig\.[\w.-]+\.json", name):
                data = loads_jsonc(raw.decode("utf-8", "replace"))
                self._add_config(folder, path, data, project_files)
            elif name == "package.json":
                data = loads_jsonc(raw.decode("utf-8", "replace"))
                self.package_dirs.append(folder)
                if isinstance(data.get("name"), str):
                    self.packages[data["name"]] = (folder, data)
        self.package_dirs.sort(key=len, reverse=True)

    def _add_config(self, folder: str, path: str, data: dict, files: dict, depth: int = 0) -> None:
        opts = data.get("compilerOptions") or {}
        base_url = opts.get("baseUrl")
        paths = opts.get("paths") or {}
        ext = data.get("extends")
        if isinstance(ext, str) and ext.startswith(".") and depth < 3 and (not paths or base_url is None):
            parent_path = posixpath.normpath(posixpath.join(folder, ext))
            if not parent_path.endswith(".json"):
                parent_path += ".json"
            raw = files.get(parent_path)
            if raw is not None:
                parent = loads_jsonc(raw.decode("utf-8", "replace"))
                popts = parent.get("compilerOptions") or {}
                pfolder = posixpath.dirname(parent_path)
                if not paths and popts.get("paths"):
                    paths = popts["paths"]
                    base_url = base_url if base_url is not None else posixpath.relpath(
                        posixpath.join(pfolder, popts.get("baseUrl", ".")), folder)
        if not paths and base_url is None and folder in self.configs:
            return
        base = posixpath.normpath(posixpath.join(folder, base_url or "."))
        self.configs[folder] = {"base": "" if base == "." else base, "paths": paths if isinstance(paths, dict) else {}}

    # ---------------------------------------------------------------- files
    def _exists(self, p: str) -> bool:
        return p in self.known

    def _try(self, base: str) -> str | None:
        base = posixpath.normpath(base)
        if base.startswith("../") or base == "..":
            return None
        base = "" if base == "." else base
        root, ext = posixpath.splitext(base)
        if ext and self._exists(base):
            return base
        for alt in SWAP.get(ext, ()):  # TypeScript projects write `./x.js` for x.ts
            if self._exists(root + alt):
                return root + alt
        for e in SCRIPT_EXTS:
            if self._exists(base + e):
                return base + e
        for e in SCRIPT_EXTS:
            p = posixpath.join(base, "index" + e) if base else "index" + e
            if self._exists(p):
                return p
        for e in OTHER_EXTS:
            if self._exists(base + e):
                return base + e
        return None

    def _config_for(self, importer: str) -> dict | None:
        d = posixpath.dirname(importer)
        while True:
            if d in self.configs:
                return self.configs[d]
            if not d:
                return None
            d = posixpath.dirname(d)

    def _package_dir(self, importer: str) -> str:
        for d in self.package_dirs:
            if not d or importer.startswith(d + "/"):
                return d
        return ""

    def _package_entry(self, folder: str, data: dict, sub: str) -> str | None:
        if sub:
            return self._try(posixpath.join(folder, sub)) or self._try(posixpath.join(folder, "src", sub))
        cands = []
        for key in ("source", "types", "typings", "module", "main", "browser"):
            v = data.get(key)
            if isinstance(v, str) and not v.endswith(".d.ts"):
                cands.append(v)
        exp = data.get("exports")
        if isinstance(exp, dict):
            exp = exp.get(".", exp)
        if isinstance(exp, str):
            cands.append(exp)
        elif isinstance(exp, dict):
            for k in ("source", "import", "default", "require", "node"):
                v = exp.get(k)
                if isinstance(v, dict):
                    v = v.get("default") or v.get("import")
                if isinstance(v, str):
                    cands.append(v)
        for c in cands:
            hit = self._try(posixpath.join(folder, c))
            if hit:
                return hit
        return self._try(posixpath.join(folder, "src")) or self._try(folder)

    def resolve_spec(self, importer: str, spec: str) -> str | None:
        """The project file an import specifier means, or None for a library or something outside the project."""
        key = (posixpath.dirname(importer), spec)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None  # also guards against loops
        hit = self._resolve(importer, spec.split("?")[0].split("#")[0])
        self.cache[key] = hit
        return hit

    def _resolve(self, importer: str, spec: str) -> str | None:
        here = posixpath.dirname(importer)
        if not spec or spec.startswith(("http:", "https:", "data:", "//")):
            return None
        if spec.startswith("."):
            return self._try(posixpath.join(here, spec))
        if spec.startswith("/"):  # an HTML page or Vite: from the site's root, which may be public/ or static/
            pkg = self._package_dir(importer)
            return self._try(posixpath.join(here, spec[1:])) or self._try(posixpath.join(pkg, spec[1:])) or \
                self._try(posixpath.join(pkg, "public", spec[1:])) or self._try(
                    posixpath.join(pkg, "static", spec[1:])) or self._try(spec[1:])
        if importer.endswith((".html", ".htm", ".css")) and not spec.startswith("@"):
            hit = self._try(posixpath.join(here, spec))  # <script src="app.js">, @import "theme.css"
            if hit:
                return hit
        cfg = self._config_for(importer)
        if cfg is not None:
            for pattern, targets in cfg["paths"].items():
                if not isinstance(targets, list):
                    continue
                prefix, star, suffix = pattern.partition("*")
                if (star and spec.startswith(prefix) and spec.endswith(suffix)) or (not star and spec == pattern):
                    middle = spec[len(prefix): len(spec) - len(suffix)] if star else ""
                    for t in targets:
                        hit = self._try(posixpath.join(cfg["base"], t.replace("*", middle)))
                        if hit:
                            return hit
        if spec.startswith(("@/", "~/", "#/")):  # the usual alias for the app's src/ folder
            pkg = self._package_dir(importer)
            hit = self._try(posixpath.join(pkg, "src", spec[2:])) or self._try(posixpath.join(pkg, spec[2:]))
            if hit:
                return hit
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        if name in self.packages:  # a package of this repository (a workspace)
            folder, data = self.packages[name]
            return self._package_entry(folder, data, spec[len(name) + 1:])
        if cfg is not None and cfg["base"] is not None:  # baseUrl: non-relative imports from there
            return self._try(posixpath.join(cfg["base"], spec))
        return None

    # ---------------------------------------------------------------- names
    def table(self, f: str) -> dict[str, tuple[str, str]]:
        t = self.tables.get(f)
        if t is None:
            t = {}
            for imp in self.mm.analyses.get(f, {}).get("imports", []):
                spec = imp["module"]
                if imp.get("alias"):
                    t.setdefault(imp["alias"], (spec, "*"))
                for n in imp["names"]:
                    if n["name"] != "*":
                        t.setdefault(n.get("alias") or n["name"], (spec, n["name"]))
            self.tables[f] = t
        return t

    def _path(self, hit: tuple[str, str | None] | None, rest: list[str], depth: int):
        if hit is None or not rest:
            return hit
        f, q = hit
        if q is None:  # a whole module: its export
            nxt = self.export(f, rest[0], depth + 1)
            return self._path(nxt, rest[1:], depth + 1)
        cand = f"{q}.{'.'.join(rest)}"
        if cand in self.mm.symbols.get(f, {}):
            return (f, cand)
        return None

    def name_in(self, f: str, dotted: str, depth: int = 0):
        if depth > 12:
            return None
        syms = self.mm.symbols.get(f, {})
        if dotted in syms:
            return (f, dotted)
        parts = dotted.split(".")
        root = parts[0]
        if root in syms:
            return None
        imp = self.table(f).get(root)
        if imp is not None:
            spec, name = imp
            target = self.resolve_spec(f, spec)
            if target is None:
                return None
            hit = (target, None) if name == "*" else self.export(target, name, depth + 1)
            return self._path(hit, parts[1:], depth)
        if self.mm.langs.get(f) == "html":  # functions from the plain scripts this page loads
            for imp in self.mm.analyses.get(f, {}).get("imports", []):
                if imp.get("html") == "script":
                    target = self.resolve_spec(f, imp["module"])
                    if target and dotted in self.mm.symbols.get(target, {}):
                        return (target, dotted)
        return None

    def export(self, f: str, name: str, depth: int = 0):
        """(file, qualname) for what file f exports as `name`; (file, None) for a whole module."""
        if depth > 12:
            return None
        a = self.mm.analyses.get(f)
        if a is None:
            return None
        ex = a.get("exports") or {}
        syms = self.mm.symbols.get(f, {})
        if name in ex:
            v = ex[name]
            if v is None:
                return None
            if isinstance(v, list):  # ["from", spec, name]
                target = self.resolve_spec(f, v[1])
                if target is None:
                    return None
                return (target, None) if v[2] == "*" else self.export(target, v[2], depth + 1)
            if v in syms:
                return (f, v)
            return self.name_in(f, v, depth + 1)  # re-exported import
        for spec in a.get("export_stars") or []:
            target = self.resolve_spec(f, spec)
            if target:
                hit = self.export(target, name, depth + 1)
                if hit:
                    return hit
        if name == "default" and self.mm.langs.get(f) in ("vue", "svelte"):
            return (f, "<module>")  # the component itself
        if not ex and name in syms:  # no exports written down (CommonJS objects, plain scripts)
            return (f, name)
        if name == "default" and not ex:
            return (f, None)
        return None

    # ---------------------------------------------------------------- types
    def parse_type(self, f: str, text: str, depth: int = 0):
        if depth > 12 or not text:
            return None
        t = text.strip()
        if t.startswith("readonly "):
            t = t[9:].strip()
        while t.startswith("(") and t.endswith(")"):
            t = t[1:-1].strip()
        parts = [p for p in _split_ts(t, "|") if p not in self._NOTHING]
        if len(parts) != 1:
            return self.parse_type(f, parts[0], depth + 1) if parts else None
        t = parts[0]
        both = _split_ts(t, "&")
        if len(both) > 1:
            return self.parse_type(f, both[0], depth + 1)
        if t.startswith("{") and t.endswith("}"):  # { store: Store; onSave(): void }
            members = {}
            for part in re.split(r"[;,\n]", t[1:-1]):
                m = re.match(r"\s*(?:readonly\s+)?([\w$]+)\??\s*:\s*(.+)", part)
                if m:
                    members[m.group(1)] = m.group(2).strip()
            return ("obj", f, members) if members else None
        if t.endswith("[]"):
            inner = self.parse_type(f, t[:-2], depth + 1)
            return ("seq", inner) if inner else None
        if t.startswith("[") and t.endswith("]"):
            return ("tup", [self.parse_type(f, x, depth + 1) for x in _split_ts(t[1:-1], ",")])
        m = re.fullmatch(r"([\w$.]+)\s*<(.*)>", t, re.S)
        if m:
            head, args = m.group(1).rsplit(".", 1)[-1], _split_ts(m.group(2), ",")
            if head in self._SEQ and args:
                inner = self.parse_type(f, args[0], depth + 1)
                return ("seq", inner) if inner else None
            if head in self._MAP and len(args) > 1:
                return ("map", self.parse_type(f, args[0], depth + 1), self.parse_type(f, args[1], depth + 1))
            if head in self._SAME and args:
                return self.parse_type(f, args[0], depth + 1)
            t = m.group(1)
        if not re.fullmatch(r"[\w$.]+", t):
            return None
        hit = self.name_in(f, t, depth + 1)
        if hit and hit[1]:
            sym = self.mm.symbols.get(hit[0], {}).get(hit[1])
            if sym and sym["kind"] == "class":
                return ("cls", hit[0], hit[1])
        return None
