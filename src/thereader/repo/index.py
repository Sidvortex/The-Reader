"""Index a project folder: its files, its Python modules, how they connect, and where to start reading."""

from __future__ import annotations

import configparser
import copy
import hashlib
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..analyzer import ANALYZER_VERSION, analyze
from ..text import doc_summary
from .cache import AnalysisCache
from .files import NOT_CODE, FileEntry, list_files, read_text
from .modules import ModuleMap
from .worker import analyze_for_index

MAX_PY_FILES = int(os.environ.get("THE_READER_MAX_PY_FILES", "8000"))
WORKERS = int(os.environ.get("THE_READER_WORKERS", "0"))  # helper processes; 0 picks from the CPU count
PARALLEL_FROM = 150  # uncached files before helper processes are worth starting
SPECIAL_FILES = {"pyproject.toml", "setup.cfg", "setup.py", "package.json"}


class Progress:
    def __init__(self):
        self.phase = "Starting"
        self.done = 0
        self.total = 0

    def set(self, phase: str, total: int = 0) -> None:
        self.phase, self.done, self.total = phase, 0, total


def _can_spawn() -> bool:
    """Helper processes restart from the main script; only possible when there is one on disk."""
    main = sys.modules.get("__main__")
    if getattr(main, "__spec__", None) is not None:
        return True
    f = getattr(main, "__file__", None)
    return bool(f) and os.path.isfile(f)


TESTY = re.compile(r"(^|/)(tests?|testing|examples?|docs?|benchmarks?)(/|$)|(^|/)test_")


def repo_id(root: Path) -> str:
    return hashlib.sha1(str(root).encode()).hexdigest()[:12]


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _first_para(doc: str | None, limit: int = 220) -> str | None:
    return doc_summary(doc, limit)


class RepoIndex:
    def __init__(self, root: Path, progress: Progress | None = None, cache: AnalysisCache | None = None,
                 rid: str | None = None, name: str | None = None, source: dict | None = None):
        self.root = root.resolve()
        self.name = name or self.root.name or str(self.root)
        self.id = rid or repo_id(self.root)
        self.source = source  # where the files came from, for a GitHub link
        self.progress = progress or Progress()
        self.cache = cache
        self.notes: list[str] = []
        self.build()

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        self.progress.set("Listing files")
        self.entries, self.used_git = list_files(self.root)
        self.by_path: dict[str, FileEntry] = {e.path: e for e in self.entries}

        self.progress.set("Reading files", len(self.entries))
        texts: dict[str, bytes] = {}
        for e in self.entries:
            name = e.path.rsplit("/", 1)[-1]
            if e.language or name in SPECIAL_FILES or name.lower().startswith("readme"):
                data = read_text(self.root, e)
                if data is not None and (e.language == "Python" or name in SPECIAL_FILES
                                         or name.lower().startswith("readme")):
                    texts[e.path] = data
            self.progress.done += 1

        py_paths = [e.path for e in self.entries if e.language == "Python" and e.path in texts]
        if len(py_paths) > MAX_PY_FILES:
            self.notes.append(f"This project has {len(py_paths):,} Python files; the first {MAX_PY_FILES:,} were analyzed.")
            py_paths = py_paths[:MAX_PY_FILES]
        self.py: dict[str, dict] = self._analyze_all(py_paths, texts)
        self.failed = [p for p in py_paths if p not in self.py]

        self.progress.set("Linking files")
        self.modmap = ModuleMap(list(self.py), self.name, self.py)
        self.own_packages = sorted({n.split(".")[0] for n in self.modmap.canonical.values() if n})
        self._link()
        self._build_tree()
        self.readme = self._readme(texts)
        self.entry_points = self._entry_points(texts)
        self.dependencies = self._dependencies(texts)
        self.git = self._git_info() if self.used_git else None
        self.progress.set("Ready")

    def _analyze_all(self, paths: list[str], texts: dict[str, bytes]) -> dict[str, dict]:
        keys = {p: hashlib.sha256(texts[p]).hexdigest() + ":" + ANALYZER_VERSION for p in paths}
        cached = self.cache.get_many(list(set(keys.values()))) if self.cache else {}
        out: dict[str, dict] = {}
        todo: list[tuple[str, str]] = []
        used: set[str] = set()
        for p in paths:
            hit = cached.get(keys[p])
            if hit is not None:
                # identical files share one cached object; links are written into it later, so copy
                out[p] = copy.deepcopy(hit) if keys[p] in used else hit
                used.add(keys[p])
            else:
                todo.append((p, texts[p].decode("utf-8", "replace")))
        self.progress.set("Analyzing Python files", len(paths))
        self.progress.done = len(out)
        fresh: dict[str, dict] = {}

        def take(result):
            path, analysis, err = result
            if analysis is not None:
                out[path] = analysis
                fresh[keys[path]] = analysis
            self.progress.done += 1

        workers = WORKERS or min(8, os.cpu_count() or 1)
        if len(todo) >= PARALLEL_FROM and workers > 1 and _can_spawn():
            try:
                ctx = multiprocessing.get_context("spawn")
                with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
                    for result in pool.map(analyze_for_index, todo, chunksize=24):
                        take(result)
                todo = []
            except Exception:  # helper processes unavailable: fall back to doing it here
                todo = [t for t in todo if t[0] not in out]
        for job in todo:
            take(analyze_for_index(job))
        if self.cache and fresh:
            self.cache.put_many(fresh)
        return out

    def _link_calls(self, f: str, a: dict) -> list[tuple[dict, tuple[str, str], bool]]:
        """Point a file's calls at the functions they reach in other files (writing xfile/xsymbol into them),
        including subclass versions of a method and properties that are read.
        Returns (symbol, target, through a base class) for each link."""
        out: list[tuple[dict, tuple[str, str], bool]] = []
        mm = self.modmap
        for s in a["symbols"]:
            for c in s["calls"]:
                hits, more = [], []
                if "recv" in c:  # a call on a value whose class had to be worked out
                    state, found, subs = mm.resolve_member(c["recv"], c["attr"], f)
                    if state == "hit":
                        hits = [h for h in found if h[0] != f]
                        more = [] if c.get("super") else [o for o in subs if o[0] != f]
                else:
                    hit = mm.resolve_call(c, f)
                    hits = [hit] if hit else []
                if hits and c.get("kind") != "internal":
                    c["xfile"], c["xsymbol"] = hits[0]
                    out.append((s, hits[0], False))
                    more = hits[1:] + more  # the value may be one of several classes
                elif hits:
                    more = hits + more
                if isinstance(c.get("virtual"), str):  # self.method(): subclasses elsewhere may have their own
                    more += [o for o in mm.overrides(f, c["virtual"], c["target"].rsplit(".", 1)[-1]) if o[0] != f]
                more = [o for o in dict.fromkeys(tuple(x) for x in more) if o != (c.get("xfile"), c.get("xsymbol"))]
                if more:
                    c["xalso"] = [list(o) for o in more]
                    out += [(s, o, True) for o in more]
            for u in s.pop("uses", []):  # reading a property defined in another file
                state, hits, _ = mm.resolve_member(u["recv"], u["attr"], f)
                for hit in hits:
                    sym = mm.symbols.get(hit[0], {}).get(hit[1])
                    if hit[0] != f and sym and sym["kind"] in ("function", "method"):
                        s["calls"].append({"name": u["name"], "kind": "external", "access": True, "xfile": hit[0],
                                           "xsymbol": hit[1], "line": u["line"], "col": u["col"], "len": u["len"]})
                        out.append((s, hit, False))
        return out

    def _link(self) -> None:
        """Resolve imports and calls between files."""
        self.edges: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])  # [imports, calls]
        self.called_by_ext: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        self.imported_by: dict[tuple[str, str], set[str]] = defaultdict(set)  # (file, name) -> files importing it
        self.attr_users: dict[str, set[str]] = defaultdict(set)  # `.name` -> files where it appears
        for f, a in self.py.items():
            for target in self.modmap.import_targets(a, f):
                self.edges[(f, target)][0] += 1
            for name, hit in self.modmap.imported_names(a, f):
                self.imported_by[hit].add(f)
            for n in a.get("attr_names", ()):
                self.attr_users[n].add(f)
            for s, hit, via_base in self._link_calls(f, a):
                if not via_base:  # the folder map shows direct use only
                    self.edges[(f, hit[0])][1] += 1
                callers = self.called_by_ext[hit]
                if (f, s["id"]) not in callers:
                    callers.append((f, s["id"]))
        self.used_by: dict[str, set[str]] = defaultdict(set)
        self.uses: dict[str, set[str]] = defaultdict(set)
        for (a, b) in self.edges:
            self.uses[a].add(b)
            self.used_by[b].add(a)

    # ------------------------------------------------------------------ tree
    def _file_stats(self, path: str) -> dict[str, int]:
        e = self.by_path[path]
        code = bool(e.language) and e.language not in NOT_CODE and not e.is_lock
        a = self.py.get(path)
        kinds = [s["kind"] for s in a["symbols"]] if a else []
        return {
            "files": 1, "code_files": int(code), "py_files": int(e.language == "Python"),
            "lines": e.lines if code else 0,
            "functions": kinds.count("function") + kinds.count("method"), "classes": kinds.count("class"),
        }

    def _build_tree(self) -> None:
        raw: dict[str, dict] = defaultdict(lambda: {"dirs": set(), "files": []})
        raw[""]  # the root always exists
        for e in self.entries:
            d = _dir_of(e.path)
            raw[d]["files"].append(e.path)
            while d:
                parent = _dir_of(d)
                raw[parent]["dirs"].add(d)
                d = parent

        def compact(d: str) -> str:  # a/b/c shown as one folder when a and b hold nothing else
            while not raw[d]["files"] and len(raw[d]["dirs"]) == 1:
                d = next(iter(raw[d]["dirs"]))
            return d

        self.dirs: dict[str, dict] = {}

        def build(path: str, parent: str | None, name: str) -> dict:
            node = {"path": path, "name": name, "parent": parent, "dirs": [], "files": sorted(raw[path]["files"]),
                    "doc": None}
            self.dirs[path] = node
            totals = {"files": 0, "code_files": 0, "py_files": 0, "lines": 0, "functions": 0, "classes": 0}
            for f in node["files"]:
                for k, v in self._file_stats(f).items():
                    totals[k] += v
            for child in sorted(raw[path]["dirs"]):
                c = compact(child)
                node["dirs"].append(c)
                sub = build(c, path, c[len(path) + 1:] if path else c)
                for k in totals:
                    totals[k] += sub["stats"][k]
            init = f"{path}/__init__.py" if path else "__init__.py"
            if init in self.py:
                node["doc"] = _first_para(self.py[init]["file"].get("docstring"))
            node["stats"] = totals
            return node

        build("", None, self.name)

    # ------------------------------------------------------------------ project facts
    def _readme(self, texts: dict[str, bytes]) -> dict | None:
        cands = [p for p in texts if "/" not in p and p.lower().startswith("readme")]
        if not cands:
            return None
        path = sorted(cands, key=lambda p: (not p.lower().endswith(".md"), len(p)))[0]
        text = texts[path].decode("utf-8", "replace")
        title, paras, cur, in_code = None, [], [], False

        def flush():
            if cur:
                paras.append(" ".join(cur))
                cur.clear()

        for line in text.splitlines():
            s = line.strip()
            if s.startswith("```") or s.startswith("~~~"):
                in_code = not in_code
                flush()
                continue
            if in_code:
                continue
            if s.startswith("#"):
                if title is None and not paras and not cur:  # a heading after text is a section, not the title
                    title = s.lstrip("#").strip() or None
                flush()
                continue
            if re.fullmatch(r"[=\-~^*#]{3,}", s):  # reStructuredText underline
                if len(cur) == 1 and title is None and not paras:
                    title = cur.pop()
                flush()
                continue
            if not s or s.startswith(("![", "[![", "<", "|", "..", ">", "---")):
                flush()
                continue
            cur.append(s)
        flush()

        def clean(p: str) -> str:
            p = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", p)
            p = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", p)
            p = re.sub(r"\[([^\]]+)\](\[[^\]]*\])?", r"\1", p)  # reference-style links
            p = re.sub(r"`{1,2}([^`]+)`{1,2}", r"\1", p)
            return re.sub(r"\*\*?([^*]+)\*\*?", r"\1", p).strip()

        prose = [clean(p) for p in paras if not re.match(r"^[-*+]\s|^\d+\.\s", p)]
        excerpt = next((p for p in prose if len(p) >= 40), prose[0] if prose else None)
        if excerpt and len(excerpt) > 600:
            excerpt = excerpt[:599].rstrip() + "…"
        return {"path": path, "title": title, "excerpt": excerpt, "text": "\n\n".join(clean(p) for p in paras)[:3000]}

    def _module_file(self, module: str) -> str | None:
        return self.modmap.lookup(module.strip(), "")

    def _entry_points(self, texts: dict[str, bytes]) -> list[dict]:
        eps: list[dict] = []
        scripts: list[tuple[str, str]] = []
        if "pyproject.toml" in texts:
            try:
                data = tomllib.loads(texts["pyproject.toml"].decode("utf-8", "replace"))
            except tomllib.TOMLDecodeError:
                data = {}
            for table in (data.get("project", {}).get("scripts", {}), data.get("project", {}).get("gui-scripts", {}),
                          data.get("tool", {}).get("poetry", {}).get("scripts", {})):
                for name, target in (table or {}).items():
                    if isinstance(target, str):
                        scripts.append((name, target))
        if "setup.cfg" in texts:
            cfg = configparser.ConfigParser(interpolation=None)
            try:
                cfg.read_string(texts["setup.cfg"].decode("utf-8", "replace"))
                for line in cfg.get("options.entry_points", "console_scripts", fallback="").splitlines():
                    if "=" in line:
                        name, target = line.split("=", 1)
                        scripts.append((name.strip(), target.strip()))
            except configparser.Error:
                pass
        if "setup.py" in texts:
            for m in re.finditer(r"['\"]([\w.-]+)\s*=\s*([\w.]+:[\w.]+)['\"]", texts["setup.py"].decode("utf-8", "replace")):
                scripts.append((m.group(1), m.group(2)))
        seen = set()
        for name, target in scripts:
            if name in seen:
                continue
            seen.add(name)
            mod, _, func = target.partition(":")
            f = self._module_file(mod)
            func = func.strip().split(".")[0] if func else None
            eps.append({"kind": "command", "label": name, "detail": target.strip(), "file": f,
                        "symbol": func if f and func in self.modmap.symbols.get(f, {}) else None})
        for f in sorted(self.py, key=lambda f: (bool(TESTY.search(f)), f.count("/"), f)):
            if f.rsplit("/", 1)[-1] == "__main__.py" and (not TESTY.search(f) or len(eps) < 3):
                pkg = self.modmap.canonical.get(f, "").rsplit(".", 1)[0]
                eps.append({"kind": "module", "label": f"python -m {pkg}" if pkg else f"python {f}", "file": f,
                            "symbol": "<module>", "detail": None})
        guards = [f for f, a in self.py.items() if a["file"].get("main_guard") and not f.endswith("__main__.py")]
        guards.sort(key=lambda f: (bool(TESTY.search(f)), f.count("/"), f))
        for f in guards[:8]:
            mod = self.modmap.canonical.get(f, "")
            if "." in mod:  # part of a package: `python path/to/file.py` would break its imports
                eps.append({"kind": "module", "label": f"python -m {mod}", "file": f, "symbol": "<module>",
                            "detail": None})
            else:
                eps.append({"kind": "script", "label": f"python {f}", "file": f, "symbol": "<module>", "detail": None})
        return eps

    def _dependencies(self, texts: dict[str, bytes]) -> list[dict]:
        out: list[dict] = []
        name_re = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
        if "pyproject.toml" in texts:
            try:
                data = tomllib.loads(texts["pyproject.toml"].decode("utf-8", "replace"))
            except tomllib.TOMLDecodeError:
                data = {}
            names = [m.group(1) for d in data.get("project", {}).get("dependencies", []) or []
                     if isinstance(d, str) and (m := name_re.match(d))]
            names += [k for k in (data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}) if k != "python"]
            if names:
                out.append({"source": "pyproject.toml", "ecosystem": "Python", "names": sorted(set(names), key=str.lower)})
        for e in self.entries:
            if "/" in e.path or not re.fullmatch(r"requirements[\w.-]*\.txt", e.path):
                continue
            data = read_text(self.root, e)
            if data:
                names = [m.group(1) for line in data.decode("utf-8", "replace").splitlines()
                         if not line.strip().startswith(("#", "-")) and (m := name_re.match(line))]
                if names:
                    out.append({"source": e.path, "ecosystem": "Python", "names": sorted(set(names), key=str.lower)})
        for p, data in sorted(texts.items()):
            if p.rsplit("/", 1)[-1] != "package.json" or p.count("/") > 2:
                continue
            try:
                pkg = json.loads(data)
            except ValueError:
                continue
            names = sorted((pkg.get("dependencies") or {}).keys())
            if names:
                out.append({"source": p, "ecosystem": "JavaScript", "names": names})
        return out

    def _git_info(self) -> dict | None:
        if self.source:
            return {"branch": self.source["ref"], "commit": self.source["commit"][:7]}

        def git(*args):
            try:
                return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                                      timeout=5, check=True).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return None
        commit = git("rev-parse", "--short", "HEAD")
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "commit": commit} if commit else None

    # ------------------------------------------------------------------ output
    def languages(self) -> list[dict]:
        by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for e in self.entries:
            if e.language and e.language not in NOT_CODE and not e.is_lock:
                by[e.language][0] += 1
                by[e.language][1] += e.lines
        return [{"name": k, "files": v[0], "lines": v[1]} for k, v in sorted(by.items(), key=lambda kv: -kv[1][1])]

    def to_json(self) -> dict:
        files: dict[str, dict] = {}
        for e in self.entries:
            info: dict = {"lang": e.language, "lines": e.lines}
            a = self.py.get(e.path)
            if a is not None:
                info["doc"] = _first_para(a["file"].get("docstring"), 160)
                info["module"] = self.modmap.canonical.get(e.path)
                info["errors"] = bool(a["file"].get("has_errors"))
                info["main"] = bool(a["file"].get("main_guard"))
                info["symbols"] = [[s["id"], s["kind"], s["name"], s["start_line"], s["parent"]]
                                   for s in a["symbols"] if s["kind"] != "module"]
            elif e.language == "Python":
                info["skipped"] = "too many files" if e.path not in self.failed else "could not be read"
            files[e.path] = info
        st = self.dirs[""]["stats"]
        return {
            "id": self.id, "root": self.source["url"] if self.source else str(self.root), "name": self.name,
            "source": self.source, "git": self.git,
            "stats": {**st, "py_analyzed": len(self.py)},
            "languages": self.languages(),
            "readme": {k: v for k, v in self.readme.items() if k != "text"} if self.readme else None,
            "entry_points": self.entry_points,
            "dependencies": self.dependencies,
            "dirs": self.dirs,
            "files": files,
            "edges": [[a, b, n[0], n[1]] for (a, b), n in self.edges.items()],
            "notes": self.notes,
        }

    def file_view(self, rel: str) -> dict:
        """Full single-file analysis (with flowcharts) plus its links to the rest of the repo."""
        if rel not in self.py:
            raise KeyError(rel)
        full = (self.root / rel).resolve()
        if not full.is_relative_to(self.root):
            raise KeyError(rel)
        a = analyze(rel.rsplit("/", 1)[-1], full.read_bytes().decode("utf-8", "replace"))
        a["file"]["path"] = rel
        self._link_calls(rel, a)
        for s in a["symbols"]:
            s["called_by_ext"] = [{"file": f, "symbol": sid}
                                  for f, sid in self.called_by_ext.get((rel, s["qualname"]), [])]
            if s["kind"] in ("function", "method", "class") and not s["called_by"] and not s["called_by_ext"]:
                # nothing calls it that could be confirmed; say what else is known
                users = sorted(self.imported_by.get((rel, s["qualname"]), ()))
                if users:
                    s["imported_by"] = users
                if s["kind"] == "method" and not s["name"].startswith("__"):
                    near = sorted(self.attr_users.get(s["name"], ()))
                    if near:
                        s["name_used_in"] = near
                if s["kind"] == "method":
                    outside = self.modmap.outside_bases(rel, s["qualname"].rsplit(".", 1)[0])
                    if outside:
                        s["outside_bases"] = outside
        a["repo"] = {"id": self.id, "path": rel, "own_packages": self.own_packages,
                     "uses": sorted(self.uses.get(rel, ())), "used_by": sorted(self.used_by.get(rel, ()))}
        return a
