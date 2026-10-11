"""Work out each Python file's module name, then resolve imports and calls from one file into another."""

from __future__ import annotations

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
        """Cross-file target of a call the single-file analyzer marked as `external`."""
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
