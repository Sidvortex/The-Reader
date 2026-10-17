"""Language analyzers. Each returns the same JSON shape so the UI stays language-agnostic."""

from pathlib import PurePath

from .python_analyzer import analyze_python

# Bump when the analyzer's output changes, so cached repo indexes are rebuilt.
ANALYZER_VERSION = "4"

ANALYZERS = {".py": analyze_python, ".pyw": analyze_python, ".pyi": analyze_python}


class UnsupportedLanguage(ValueError):
    pass


def analyze(filename: str, source: str) -> dict:
    ext = PurePath(filename).suffix.lower()
    fn = ANALYZERS.get(ext)
    if fn is None:
        # default to python for pasted snippets without a name
        if not ext:
            fn = analyze_python
        else:
            raise UnsupportedLanguage(f"{ext} files can't be read yet. The Reader reads Python files (.py).")
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
