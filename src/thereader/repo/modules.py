"""Work out each Python file's module name, then resolve imports and calls from one file into another."""

from __future__ import annotations

import re

PY_SUFFIXES = (".py", ".pyi", ".pyw")


def _parent(path: str) -> str:
    """Parent folder of a posix path; "." for things at the project root."""
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    for suf in PY_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _join(module: str, name: str) -> str:
    return f"{module}{name}" if module.endswith(".") else f"{module}.{name}"


def import_table(analysis: dict) -> tuple[dict[str, str], list[str]]:
    """Local name -> dotted target for one file's imports, plus modules it star-imports from."""
    names: dict[str, str] = {}
    stars: list[str] = []
    for imp in analysis.get("imports", []):
        mod = imp["module"]
        if not imp["names"]:  # import a.b.c  /  import a.b as x
            if imp.get("alias"):
                names[imp["alias"]] = mod
            else:
                names[mod.split(".")[0]] = mod.split(".")[0]
            continue
        for n in imp["names"]:
            if n["name"] == "*":
                stars.append(mod)
            else:
                names[n["alias"] or n["name"]] = _join(mod, n["name"])
    return names, stars


class ModuleMap:
    def __init__(self, py_files: list[str], root_name: str, analyses: dict[str, dict]):
        self.files = set(py_files)
        self.root_name = root_name
        self.pkg_dirs = {_parent(p) for p in py_files if _stem(p) == "__init__"}
        self.canonical: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        self.by_suffix: dict[str, list[str]] = {}
        for p in py_files:
            name = self._canonical(p)
            self.canonical[p] = name
            if name:
                self.by_name.setdefault(name, []).append(p)
            parts = self._path_parts(p)
            for i in range(len(parts) - 1):  # path-based names with 2+ parts, for folders without __init__.py
                key = ".".join(parts[i:])
                if key != name:
                    self.by_suffix.setdefault(key, []).append(p)
        self.symbols: dict[str, dict[str, dict]] = {
            p: {s["qualname"]: s for s in a["symbols"] if s["kind"] != "module"} for p, a in analyses.items()
        }
        self.imports: dict[str, tuple[dict[str, str], list[str]]] = {p: import_table(a) for p, a in analyses.items()}
        self._subclasses: dict[tuple[str, str], list[tuple[str, str]]] | None = None

    # ---------- names ----------
    def _canonical(self, path: str) -> str:
        chain: list[str] = []
        d = _parent(path)
        while d in self.pkg_dirs:
            chain.insert(0, self.root_name if d == "." else d.rsplit("/", 1)[-1])
            if d == ".":
                break
            d = _parent(d)
        stem = _stem(path)
        return ".".join(chain if stem == "__init__" else chain + [stem])

    def _path_parts(self, path: str) -> list[str]:
        parts = path.split("/")
        parts[-1] = _stem(parts[-1])
        if parts[-1] == "__init__":
            parts = parts[:-1] or [self.root_name]
        return parts

    def is_package(self, path: str) -> bool:
        return _stem(path) == "__init__"

    @staticmethod
    def _closest(files: list[str], importer: str) -> str:
        def score(f: str):
            common = 0
            for a, b in zip(f.split("/"), importer.split("/")):
                if a != b:
                    break
                common += 1
            return (-common, f.endswith(".pyi"), f.count("/"), f)
        return min(files, key=score)

    def lookup(self, key: str, importer: str) -> str | None:
        """The file for a dotted module name, as seen from `importer`."""
        if key in self.by_name:
            return self._closest(self.by_name[key], importer)
        if "." not in key:
            # plain scripts importing a neighbour: `import utils` next to utils.py
            d = _parent(importer)
            prefix = "" if d == "." else d + "/"
            for cand in (f"{prefix}{key}.py", f"{prefix}{key}/__init__.py"):
                if cand in self.files:
                    return cand
            return None
        cands = self.by_suffix.get(key)
        return self._closest(cands, importer) if cands else None

    def absolutize(self, dotted: str, importer: str) -> str | None:
        """Turn `.storage.save` into `pkg.storage.save` based on where the importer lives."""
        if not dotted.startswith("."):
            return dotted
        level = len(dotted) - len(dotted.lstrip("."))
        rest = dotted[level:]
        base = self.canonical.get(importer, "")
        parts = base.split(".") if base else []
        if not self.is_package(importer):
            parts = parts[:-1]
        drop = level - 1
        if drop > len(parts):
            return None
        parts = parts[: len(parts) - drop]
        return ".".join(parts + ([rest] if rest else [])) or None

    # ---------- resolution ----------
    def resolve(self, dotted: str, importer: str, depth: int = 0) -> tuple[str, str | None] | None:
        """(file, qualname) for a dotted path, or (file, None) when it names a whole module."""
        if depth > 8:
            return None
        full = self.absolutize(dotted, importer)
        if not full:
            return None
        parts = full.split(".")
        for i in range(len(parts), 0, -1):
            f = self.lookup(".".join(parts[:i]), importer)
            if f is None:
                continue
            rest = parts[i:]
            if not rest:
                return (f, None)
            return self._find_in_file(f, rest, depth + 1)
        return None

    def _find_in_file(self, f: str, rest: list[str], depth: int) -> tuple[str, str | None] | None:
        if depth > 8:
            return None
        syms = self.symbols.get(f, {})
        q = ".".join(rest)
        if q in syms:
            return (f, q)
        names, stars = self.imports.get(f, ({}, []))
        # a method inherited from a base class, possibly defined in another file
        if len(rest) >= 2:
            cls, meth = ".".join(rest[:-1]), rest[-1]
            if cls in syms and syms[cls]["kind"] == "class":
                for base in syms[cls]["bases"]:
                    base = base.split("[")[0].strip()
                    if not base or "=" in base or base == cls:
                        continue
                    if base in syms and syms[base]["kind"] == "class":
                        hit = self._find_in_file(f, base.split(".") + [meth], depth + 1)
                    else:
                        root, _, tail = base.partition(".")
                        hit = None
                        if root in names:
                            target = names[root] + (f".{tail}" if tail else "")
                            hit = self.resolve(f"{target}.{meth}", f, depth + 1)
                    if hit and hit[1]:
                        return hit
        # re-exported: pkg/__init__.py does `from .core import thing`
        if rest[0] in names:
            tail = ".".join(rest[1:])
            return self.resolve(names[rest[0]] + (f".{tail}" if tail else ""), f, depth + 1)
        for src in stars:
            hit = self.resolve(_join(src, q), f, depth + 1)
            if hit and hit[1]:
                return hit
        return None

    def resolve_call(self, call: dict, importer: str) -> tuple[str, str] | None:
        """Cross-file target of a call the single-file analyzer couldn't settle on its own."""
        if "recv" in call and call.get("kind") != "internal":
            hits = self.recv_targets(call, importer)
            return hits[0] if hits else None
        if call.get("kind") != "external" or not call.get("module"):
            return None
        mod, label = call["module"], call["name"]
        if call.get("via_type"):
            dotted = mod
        else:
            if any(ch in label for ch in "()[]{} "):
                return None
            _, dot, rest = label.partition(".")
            dotted = mod + (f".{rest}" if dot else "")
        hit = self.resolve(dotted, importer)
        if not hit or not hit[1] or hit[0] == importer:
            return None
        f, q = hit
        sym = self.symbols.get(f, {}).get(q)
        if sym and sym["kind"] == "class" and f"{q}.__init__" in self.symbols[f]:
            q = f"{q}.__init__"  # calling a class runs its __init__
        return f, q

    def imported_names(self, analysis: dict, importer: str) -> list[tuple[str, tuple[str, str]]]:
        """(name, (file, qualname)) for each function or class this file imports from another file."""
        out = []
        for imp in analysis.get("imports", []):
            for n in imp["names"]:
                if n["name"] == "*":
                    continue
                hit = self.resolve(_join(imp["module"], n["name"]), importer)
                if hit and hit[1] and hit[0] != importer:
                    out.append((n["name"], hit))
        return out

    def recv_targets(self, call: dict, importer: str) -> list[tuple[str, str]]:
        """Every function in another file a call on a typed value may reach (one per class the value can be)."""
        state, hits, _ = self.resolve_member(call["recv"], call["attr"], importer)
        return [h for h in hits if h[0] != importer] if state == "hit" else []

    def import_targets(self, analysis: dict, importer: str) -> list[str]:
        """Files this file imports (each listed once per import statement name)."""
        out: list[str] = []
        for imp in analysis.get("imports", []):
            mod = imp["module"]
            names = [n["name"] for n in imp["names"]] or [None]
            for n in names:
                hit = None
                if n and n != "*":
                    hit = self.resolve(_join(mod, n), importer)
                if hit is None:
                    hit = self.resolve(mod, importer)
                if hit and hit[0] != importer:
                    out.append(hit[0])
        return out

    # ---------- types: which class a value belongs to ----------
    # Types are ("cls", file, qualname), ("seq", item type) for lists and the like, or ("map", key, value).
    # They come from "descs" the analyzer writes (see python_analyzer._expr_desc).
    _SEQ = {"list", "List", "Sequence", "MutableSequence", "Iterable", "Iterator", "Generator", "AsyncIterator",
            "AsyncIterable", "AsyncGenerator", "Collection", "set", "Set", "frozenset", "FrozenSet", "MutableSet",
            "tuple", "Tuple", "deque", "Deque", "Reversible", "Awaitable", "Coroutine"}
    _MAP = {"dict", "Dict", "Mapping", "MutableMapping", "DefaultDict", "defaultdict", "OrderedDict", "Counter"}
    _WRAP = {"Optional", "Union", "Annotated", "Final", "ClassVar", "Type", "type", "ReadOnly", "Required",
             "NotRequired"}

    @staticmethod
    def _split_top(text: str, sep: str) -> list[str]:
        out, depth, cur = [], 0, []
        for ch in text:
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            if ch == sep and depth == 0:
                out.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        out.append("".join(cur))
        return [p.strip() for p in out]

    def name_in(self, f: str, dotted: str, depth: int = 0) -> tuple[str, str | None] | None:
        """What a (dotted) name means inside file f: its own definition, or what it imports."""
        syms = self.symbols.get(f, {})
        if dotted in syms:
            return (f, dotted)
        root, _, tail = dotted.partition(".")
        names, stars = self.imports.get(f, ({}, []))
        if root in names:
            return self.resolve(names[root] + (f".{tail}" if tail else ""), f, depth + 1)
        for src in stars:
            hit = self.resolve(_join(src, dotted), f, depth + 1)
            if hit and hit[1]:
                return hit
        return None

    def parse_type(self, f: str, text: str, depth: int = 0):
        if depth > 12 or not text:
            return None
        t = text.strip().strip("'\"").strip()
        parts = self._split_top(t, "|")
        if len(parts) > 1:
            parts = [p for p in parts if p not in ("None", "")]
            return self.parse_type(f, parts[0], depth + 1) if parts else None
        m = re.fullmatch(r"([\w.]+)\[(.*)\]", t, re.S)
        if m:
            head, args = m.group(1).rsplit(".", 1)[-1], self._split_top(m.group(2), ",")
            if head in self._WRAP:
                args = [a for a in args if a not in ("None", "")]
                return self.parse_type(f, args[0], depth + 1) if args else None
            if head in ("tuple", "Tuple") and len(args) > 1 and args[-1] != "...":
                return ("tup", [self.parse_type(f, a, depth + 1) for a in args])
            if head in self._SEQ:
                inner = self.parse_type(f, args[0], depth + 1)
                return ("seq", inner) if inner else None
            if head in self._MAP:
                return ("map", self.parse_type(f, args[0], depth + 1),
                        self.parse_type(f, args[-1], depth + 1) if len(args) > 1 else None)
            t = m.group(1)  # a generic class of our own: Box[int] is a Box
        if t in ("Self", "typing.Self", "typing_extensions.Self"):
            return None
        if not re.fullmatch(r"[\w.]+", t):
            return None
        hit = self.name_in(f, t, depth + 1)
        if hit and hit[1]:
            sym = self.symbols.get(hit[0], {}).get(hit[1])
            if sym and sym["kind"] == "class":
                return ("cls", hit[0], hit[1])
        return None

    MAX_TYPES = 4  # a value can be one of a few classes (an attribute set in different places); more is noise

    def _merge(self, vs) -> list:
        out = []
        for v in vs:
            if v is not None and v not in out:
                out.append(v)
        return out[: self.MAX_TYPES]

    def _returns(self, f: str, sym: dict, depth: int) -> list:
        ret = (sym.get("returns") or "").strip()
        if ret in ("Self", "typing.Self", "typing_extensions.Self") and "." in sym["qualname"]:
            return [("cls", f, sym["qualname"].rsplit(".", 1)[0])]
        if ret:
            return self._merge([self.parse_type(f, ret, depth + 1)])
        if sym.get("rdesc"):
            return self.eval_desc(f, sym["rdesc"], depth + 1)
        return []

    def eval_desc(self, f: str, desc: list, depth: int = 0) -> list:
        """The classes a value can be, given where it came from."""
        if depth > 12 or not desc:
            return []
        base, steps = desc[0], desc[1]
        if base.startswith("T:"):
            vs = self._merge([self.parse_type(f, base[2:], depth + 1)])
        elif base.startswith("^"):
            hit = self.name_in(f, base[1:], depth + 1)
            sym = self.symbols.get(hit[0], {}).get(hit[1]) if hit and hit[1] else None
            if sym is None:
                return []
            # calling a class builds exactly that class; anything else may be a subclass of what it says
            vs = [("cls", hit[0], hit[1], True)] if sym["kind"] == "class" else self._returns(hit[0], sym, depth)
        else:
            return []
        for st in steps:
            if not vs:
                return []
            vs = self._merge(x for v in vs for x in self._step(v, st, depth + 1))
        return vs

    def _step(self, v, st: str, depth: int) -> list:
        if st.startswith("#"):  # one position of a tuple
            k = int(st[1:])
            if v[0] == "tup":
                return [v[1][k]] if k < len(v[1]) else []
            return [v[1]] if v[0] == "seq" else []
        if st in ("[]", "in"):
            if v[0] == "seq":
                return [v[1]]
            if v[0] == "map":
                return [v[2] if st == "[]" else v[1]]
            return []
        if v[0] != "cls":
            return []
        call = st.endswith("()")
        return self._member_type(v[1], v[2], st[:-2] if call else st, call, depth)

    def _mro(self, f: str, cls: str, depth: int = 0, seen: set | None = None):
        """The class, then its base classes (wherever they're defined)."""
        seen = seen if seen is not None else set()
        if (f, cls) in seen or depth > 10:
            return
        seen.add((f, cls))
        yield f, cls
        sym = self.symbols.get(f, {}).get(cls)
        for base in (sym or {}).get("bases", []):
            b = self.parse_type(f, base.split("(")[0], depth + 1) if "=" not in base else None
            if b and b[0] == "cls":
                yield from self._mro(b[1], b[2], depth + 1, seen)

    _TRIVIAL_BASES = {"object", "Protocol", "typing.Protocol", "ABC", "abc.ABC", "Generic", "typing.Generic"}

    def outside_bases(self, f: str, cls: str) -> list[str]:
        """Base classes (of the class or its bases) that come from outside the project, such as a library's."""
        out = []
        for cf, cq in self._mro(f, cls):
            for base in self.symbols.get(cf, {}).get(cq, {}).get("bases", []):
                name = base.split("[")[0].split("(")[0].strip()
                if "=" in base or name in self._TRIVIAL_BASES:
                    continue
                if not self.parse_type(cf, name) and name not in out:
                    out.append(name)
        return out

    @staticmethod
    def is_property(sym: dict) -> bool:
        return any(d.split("(")[0].rsplit(".", 1)[-1] in ("property", "cached_property") for d in sym.get("decorators", []))

    def _member(self, f: str, cls: str, name: str):
        """("method", file, qualname) or ("attr", file, desc) for a member of a class, looking through its bases;
        "missing" when every class involved is known and none has it; None when a base class can't be seen."""
        complete = True
        for cf, cq in self._mro(f, cls):
            syms = self.symbols.get(cf, {})
            q = f"{cq}.{name}"
            if q in syms and syms[q]["kind"] in ("function", "method", "class"):
                return ("method", cf, q)
            alts = syms.get(cq, {}).get("attrs", {}).get(name)
            if alts:
                return ("attr", cf, alts)
            for base in syms.get(cq, {}).get("bases", []):
                b = self.parse_type(cf, base.split("(")[0]) if "=" not in base else None
                if not b and base.split("[")[0].strip() not in ("object", "Protocol", "typing.Protocol", "ABC",
                                                                  "abc.ABC", "Generic", "typing.Generic", "Enum",
                                                                  "enum.Enum", "NamedTuple", "typing.NamedTuple"):
                    complete = False
        return "missing" if complete else None

    def _member_type(self, f: str, cls: str, name: str, call: bool, depth: int) -> list:
        m = self._member(f, cls, name)
        if not isinstance(m, tuple):
            return []
        kind, mf, x = m
        if kind == "method":
            sym = self.symbols[mf][x]
            if sym["kind"] == "class":
                return [("cls", mf, x)]
            if call or self.is_property(sym):
                return self._returns(mf, sym, depth)
            return []
        if call:
            return []
        return self._merge(t for d in x for t in self.eval_desc(mf, d, depth + 1))

    def resolve_member(self, desc: list, attr: str, importer: str):
        """What `<value>.attr` refers to, where desc says where the value came from. Returns (state, hits, more):
        "hit" with the methods, properties or nested classes it names (one per class the value can be), plus
        `more`: versions of that method in subclasses, which run when the value is a subclass instance;
        "miss" when the value's classes are known and none has such a method (a plain attribute);
        "unknown" when it can't be told."""
        try:
            vs = [v for v in self.eval_desc(importer, desc) if v[0] == "cls"]
        except RecursionError:
            return "unknown", [], []
        if not vs:
            return "unknown", [], []
        hits, more, unknown = [], [], False
        for v in vs:
            m = self._member(v[1], v[2], attr)
            if isinstance(m, tuple):
                if m[0] == "method" and (m[1], m[2]) not in hits:
                    hits.append((m[1], m[2]))
                    if not (len(v) > 3 and v[3]):
                        more += [o for o in self.overrides(v[1], v[2], attr) if o not in more]
            elif m is None:
                unknown = True
        if hits:
            return "hit", hits, [o for o in more if o not in hits]
        return ("unknown", [], []) if unknown else ("miss", [], [])

    def overrides(self, f: str, cls: str, meth: str) -> list[tuple[str, str]]:
        """Versions of method `meth` defined in subclasses (in any file) of class `cls` in file f."""
        if meth == "__init__" or self.symbols.get(f, {}).get(cls, {}).get("kind") != "class":
            return []
        if self._subclasses is None:
            self._subclasses = {}
            for cf, syms in self.symbols.items():
                for cq, sym in syms.items():
                    if sym["kind"] != "class":
                        continue
                    for base in sym.get("bases", []):
                        b = self.parse_type(cf, base.split("(")[0]) if "=" not in base else None
                        if b and b[0] == "cls" and (b[1], b[2]) != (cf, cq):
                            self._subclasses.setdefault((b[1], b[2]), []).append((cf, cq))
        out, todo, seen = [], [(f, cls)], set()
        while todo:
            key = todo.pop()
            for sub in self._subclasses.get(key, []):
                if sub in seen:
                    continue
                seen.add(sub)
                todo.append(sub)
                m = f"{sub[1]}.{meth}"
                if m in self.symbols.get(sub[0], {}):
                    out.append((sub[0], m))
        return out
