"""Language analyzers. Each returns the same JSON shape so the UI stays language-agnostic."""

import re
from pathlib import PurePath

from .js_analyzer import EXTENSIONS as JS_EXTENSIONS, analyze_js
from .markup import analyze_component, analyze_css, analyze_html
from .python_analyzer import analyze_python

# Bump when the analyzer's output changes, so cached repo indexes are rebuilt.
ANALYZER_VERSION = "5"

ANALYZERS = {".py": analyze_python, ".pyw": analyze_python, ".pyi": analyze_python,
             **{ext: analyze_js for ext in JS_EXTENSIONS},
             ".vue": analyze_component, ".svelte": analyze_component,
             ".html": analyze_html, ".htm": analyze_html, ".css": analyze_css}
READABLE = set(ANALYZERS)


def can_read(path: str) -> bool:
    """Whether The Reader analyzes this file (TypeScript declaration files and minified bundles excepted)."""
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith((".d.ts", ".d.mts", ".d.cts")) or ".min." in name or name.endswith((".bundle.js", ".map")):
        return False
    return PurePath(name).suffix in READABLE


def guess_language(source: str) -> str:
    """For pasted code without a file name: Python unless it clearly looks like something else."""
    head = source[:4000]
    if re.search(r"^\s*(<template|<script|<style)\b", head, re.M) and "<template" in head:
        return ".vue"
    if re.search(r"^\s*<(!doctype|html|head|body|div)\b", head, re.I | re.M):
        return ".html"
    js = len(re.findall(r"\b(const|let|var|function|=>|import .* from|export (default|const|function)|"
                        r"interface|console\.)\b|[;{}]\s*$", head, re.M))
    py = len(re.findall(r"^\s*(def |class \w+.*:|import \w+$|from \S+ import|elif |print\()|:\s*$", head, re.M))
    if js > py * 2 and js >= 3:
        return ".tsx" if re.search(r":\s*(string|number|boolean)\b|interface \w+", head) else ".jsx"
    if re.search(r"^[.#]?[\w-]+\s*\{[^}]*:[^}]*;", head, re.M) and py == 0 and "function" not in head:
        return ".css"
    return ".py"


class UnsupportedLanguage(ValueError):
    pass


def analyze(filename: str, source: str) -> dict:
    ext = PurePath(filename).suffix.lower()
    fn = ANALYZERS.get(ext)
    if fn is None:
        if not ext:  # pasted code without a name
            ext = guess_language(source)
            filename = f"{filename}{ext}"
            fn = ANALYZERS[ext]
        else:
            raise UnsupportedLanguage(f"{ext} files can't be read yet. The Reader reads Python, JavaScript, "
                                      "TypeScript, Vue, Svelte, HTML and CSS files.")
    result = fn(filename, source)
    result["reading_order"] = reading_order(result)
    return result


def reading_order(a: dict) -> list[str]:
    """Entry points first, then follow calls depth-first: the order a person would trace the file."""
    syms = {s["id"]: s for s in a["symbols"]}
    callables = [s for s in a["symbols"] if s["kind"] in ("function", "method", "module")]
    order: list[str] = []
    seen: set[str] = set()

    def visit(sid: str):
        if sid in seen or sid not in syms:
            return
        seen.add(sid)
        order.append(sid)
        for c in syms[sid]["calls"]:
            if c.get("target"):
                visit(c["target"])

    has_module_code = "<module>" in a["flowcharts"]
    if has_module_code:
        visit("<module>")

    def reach(sid: str) -> int:
        seen, stack = {sid}, [sid]
        while stack:
            for c in syms[stack.pop()]["calls"]:
                t = c.get("target")
                if t and t not in seen:
                    seen.add(t)
                    stack.append(t)
        return len(seen)

    # entry points: nothing in the file calls them. Public ones that pull in the most code come first.
    roots = [s for s in callables if s["kind"] != "module" and not s["called_by"]]
    roots.sort(key=lambda s: (s["name"].startswith("_") and not s["name"].startswith("__"), -reach(s["id"]), s["start_line"]))
    for s in roots:
        visit(s["id"])
    for s in sorted(callables, key=lambda s: s["start_line"]):
        if s["kind"] != "module":
            visit(s["id"])
    return order


def analyze_quick(path: str, source: str) -> dict:
    """The lighter analysis used to index a whole project: no flowcharts, no copy of the source."""
    fn = ANALYZERS[PurePath(path).suffix.lower()]
    a = fn(path.rsplit("/", 1)[-1], source, flowcharts=False)
    a.pop("source", None)
    a.pop("flowcharts", None)
    return a
