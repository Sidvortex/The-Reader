"""Explanations for the upper layers of repo mode: the project overview and each folder.

Every layer has a structural version built from the index (instant, works offline) and an AI version
that gets the same facts plus outlines, and is cached like the function-level explanations.
"""

from __future__ import annotations

from collections import Counter

from ..explainer import ai_markdown
from .index import RepoIndex

TREE_LINES = 170
FOLDER_OUTLINE_CHARS = 7000


def _under(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(folder + "/")


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _first_line(doc: str | None) -> str | None:
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else None


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n:,} {word}{'es' if word.endswith('s') else 's'}"


def _other_langs(idx: RepoIndex, files: list[str]) -> str:
    langs = Counter(idx.by_path[f].language or "other" for f in files)
    return ", ".join(name for name, _ in langs.most_common(3))


def _folder_blurb(idx: RepoIndex, path: str) -> str:
    node = idx.dirs[path]
    st = node["stats"]
    if node["doc"]:
        return node["doc"]
    if st["py_files"]:
        return f"{_plural(st['py_files'], 'Python file')}, {_plural(st['functions'], 'function')}"
    files = [f for f in idx.by_path if _under(f, path)]
    return f"{_plural(st['files'], 'file')} ({_other_langs(idx, files)}), not analyzed yet"


def _file_blurb(idx: RepoIndex, f: str) -> str:
    a = idx.py[f]
    doc = _first_line(a["file"].get("docstring"))
    if doc:
        return doc
    kinds = Counter(s["kind"] for s in a["symbols"])
    funcs = kinds["function"] + kinds["method"]
    if kinds["class"] or funcs:
        bits = [_plural(kinds["class"], "class")] if kinds["class"] else []
        return ", ".join(bits + ([_plural(funcs, "function")] if funcs else []))
    names = sum(len(i["names"]) for i in a["imports"])
    if f.endswith("__init__.py") and names:
        return f"package entry; brings in {_plural(names, 'name')} from other modules"
    if a["file"].get("main_guard"):
        return "runs as a script"
    return f"{_plural(idx.by_path[f].lines, 'line')} of module-level code"


def folder_links(idx: RepoIndex, folder: str) -> tuple[Counter, Counter]:
    """Files outside `folder` that it uses, and files outside it that use it (with link counts)."""
    uses, used_by = Counter(), Counter()
    for (a, b), (imports, calls) in idx.edges.items():
        ia, ib = _under(a, folder), _under(b, folder)
        if ia and not ib:
            uses[b] += imports + calls
        elif ib and not ia:
            used_by[a] += imports + calls
    return uses, used_by


def _most_used(idx: RepoIndex, n: int = 5) -> list[tuple[str, int]]:
    ranked = sorted(((len(v), k) for k, v in idx.used_by.items() if not k.endswith("__init__.py")), reverse=True)
    return [(k, c) for c, k in ranked[:n] if c > 1]


# ---------------------------------------------------------------- structural
def structural_overview(idx: RepoIndex) -> str:
    parts: list[str] = []
    r = idx.readme
    if r and r.get("excerpt"):
        parts.append(r["excerpt"])
    langs = idx.languages()[:4]
    if langs:
        parts.append("**Written in:** " + ", ".join(f"{l['name']} ({l['lines']:,} lines)" for l in langs) + ".")
    st = idx.dirs[""]["stats"]
    parts.append(f"**Size:** {_plural(st['files'], 'file')}, including {_plural(st['py_files'], 'Python file')} "
                 f"with {_plural(st['functions'], 'function')} and {_plural(st['classes'], 'class')}.")
    eps = [e for e in idx.entry_points if e["file"]][:5]
    if eps:
        lines = []
        for e in eps:
            target = f"`{e['file']}`" + (f", function `{e['symbol']}`" if e["symbol"] and e["symbol"] != "<module>" else "")
            lines.append(f"- `{e['label']}` runs {target}")
        parts.append("**Start here:**\n" + "\n".join(lines))
    root = idx.dirs[""]
    if root["dirs"]:
        parts.append("**Main parts:**\n" + "\n".join(
            f"- `{idx.dirs[d]['path']}/`: {_folder_blurb(idx, d)}" for d in root["dirs"]))
    used = _most_used(idx)
    if used:
        parts.append("**Most used files:** " + ", ".join(f"`{f}` (used by {c})" for f, c in used) + ".")
    for dep in idx.dependencies[:2]:
        names = dep["names"]
        more = f" and {len(names) - 10} more" if len(names) > 10 else ""
        parts.append(f"**Depends on** ({dep['source']}): " + ", ".join(names[:10]) + more + ".")
    for note in idx.notes:
        parts.append(f"_{note}_")
    return "\n\n".join(parts)


def structural_folder(idx: RepoIndex, folder: str) -> str:
    node = idx.dirs[folder]
    st = node["stats"]
    parts = [f"**`{node['path']}/`**: {_plural(st['files'], 'file')}, {_plural(st['py_files'], 'Python file')}, "
             f"{st['lines']:,} lines of code."]
    if node["doc"]:
        parts.append(node["doc"])
    if node["dirs"]:
        rows = [f"- `{idx.dirs[d]['path']}/`: {_folder_blurb(idx, d)}" for d in node["dirs"][:30]]
        if len(node["dirs"]) > 30:
            rows.append(f"- and {len(node['dirs']) - 30} more")
        parts.append("**Folders:**\n" + "\n".join(rows))
    py_files = [f for f in node["files"] if f in idx.py]
    if py_files:
        rows = [f"- `{f}`: {_file_blurb(idx, f)}" for f in py_files[:25]]
        if len(py_files) > 25:
            rows.append(f"- and {len(py_files) - 25} more")
        parts.append("**Files:**\n" + "\n".join(rows))
    others = [f for f in node["files"] if f not in idx.py]
    if others:
        parts.append(f"**Other files here:** {len(others)} ({_other_langs(idx, others)}).")
    uses, used_by = folder_links(idx, folder)
    if used_by:
        parts.append("**Used by:** " + ", ".join(f"`{f}`" for f, _ in used_by.most_common(6))
                     + (f" and {len(used_by) - 6} more" if len(used_by) > 6 else "") + ".")
    if uses:
        parts.append("**Uses:** " + ", ".join(f"`{f}`" for f, _ in uses.most_common(6))
                     + (f" and {len(uses) - 6} more" if len(uses) > 6 else "") + ".")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- AI
def _tree_text(idx: RepoIndex) -> str:
    lines: list[str] = []

    def walk(path: str, depth: int) -> None:
        node = idx.dirs[path]
        pad = "  " * depth
        for d in node["dirs"]:
            sub = idx.dirs[d]
            st = sub["stats"]
            desc = f" — {sub['doc']}" if sub["doc"] else ""
            lines.append(f"{pad}{sub['name']}/  ({st['py_files']} Python files, {st['files']} files){desc}")
            if depth < 3 and len(lines) < TREE_LINES:
                walk(d, depth + 1)
        others = 0
        for f in node["files"]:
            if f in idx.py:
                doc = _first_line(idx.py[f]["file"].get("docstring"))
                lines.append(f"{pad}{_base(f)}" + (f" — {doc}" if doc else ""))
            else:
                others += 1
        if others:
            lines.append(f"{pad}(+{others} other files)")

    walk("", 0)
    if len(lines) > TREE_LINES:
        lines = lines[:TREE_LINES] + ["…"]
    return "\n".join(lines)


def _file_outline(idx: RepoIndex, f: str) -> str:
    a = idx.py[f]
    out = []
    doc = _first_line(a["file"].get("docstring"))
    out.append(f"{f}" + (f" — {doc}" if doc else ""))
    for s in a["symbols"]:
        if s["kind"] == "class":
            methods = [x["name"] for x in a["symbols"] if x["parent"] == s["id"]]
            out.append(f"  class {s['name']}" + (f"({', '.join(s['bases'])})" if s["bases"] else "")
                       + (f": {', '.join(methods[:12])}" if methods else ""))
        elif s["kind"] == "function" and s["parent"] is None:
            d = _first_line(s.get("docstring"))
            out.append(f"  def {s['name']}()" + (f" — {d}" if d else ""))
    return "\n".join(out)


def ai_overview(idx: RepoIndex, api_key: str | None = None) -> dict:
    r = idx.readme
    eps = "\n".join(f"- {e['label']} -> {e['file']}" + (f" ({e['symbol']})" if e["symbol"] else "")
                    for e in idx.entry_points if e["file"]) or "none found"
    deps = "\n".join(f"- {d['source']}: {', '.join(d['names'][:20])}" for d in idx.dependencies) or "none listed"
    langs = ", ".join(f"{l['name']} {l['lines']:,} lines" for l in idx.languages()[:6])
    used = "\n".join(f"- {f} (used by {c} files)" for f, c in _most_used(idx, 8)) or "none"
    prompt = (
        f"Project folder: {idx.name}\n\n"
        f"README (excerpt):\n{(r or {}).get('text') or '(no README)'}\n\n"
        f"Languages: {langs}\n\nEntry points:\n{eps}\n\nDependencies:\n{deps}\n\n"
        f"Folder structure (Python files with their docstrings):\n{_tree_text(idx)}\n\n"
        f"Most used files:\n{used}\n\n"
        "Write an overview for a developer who is new to this codebase, in under 300 words:\n"
        "1. One or two sentences: what the project does and who it is for.\n"
        "2. **Main parts**: 3-7 bullets, each `folder/` or `file.py` path: its role.\n"
        "3. **How it fits together**: 3-5 sentences on the main flow, from entry point to result.\n"
        "4. **Start reading here**: an ordered list of 3-6 files or functions, each with a few words on why.\n"
        "Write paths in backticks exactly as they appear in the structure above, relative to the project folder."
    )
    return ai_markdown(prompt, ["overview", prompt], max_tokens=1100, api_key=api_key)


def ai_folder(idx: RepoIndex, folder: str, api_key: str | None = None) -> dict:
    node = idx.dirs[folder]
    outline, used_chars = [], 0
    for f in node["files"]:
        if f in idx.py:
            block = _file_outline(idx, f)
            if used_chars + len(block) > FOLDER_OUTLINE_CHARS:
                outline.append(f"{f} (outline omitted for length)")
                continue
            outline.append(block)
            used_chars += len(block)
    subs = "\n".join(f"- {idx.dirs[d]['path']}/: {_folder_blurb(idx, d)}" for d in node["dirs"]) or "none"
    uses, used_by = folder_links(idx, folder)
    prompt = (
        f"Project: {idx.name}\nFolder: {node['path']}/\n"
        + (f"Package docstring: {node['doc']}\n" if node["doc"] else "")
        + "\nPython files in this folder, with outlines:\n" + ("\n".join(outline) or "none")
        + f"\n\nSubfolders:\n{subs}\n\n"
        f"Used by (outside this folder): {', '.join(f for f, _ in used_by.most_common(8)) or 'nothing'}\n"
        f"Uses (outside this folder): {', '.join(f for f, _ in uses.most_common(8)) or 'nothing'}\n\n"
        "Explain this folder in under 220 words:\n"
        "1. One sentence: what this folder is responsible for.\n"
        "2. **Files**: one bullet per file, most important first (at most 10), `path`: its role.\n"
        "3. **How they connect**: 2-3 sentences, including how the rest of the project uses this folder.\n"
        "Write paths in backticks exactly as given."
    )
    return ai_markdown(prompt, ["folder", prompt], max_tokens=900, api_key=api_key)


# ---------------------------------------------------------------- dispatch
def explain_overview(idx: RepoIndex, use_ai: bool, api_key: str | None = None) -> dict:
    if use_ai:
        return ai_overview(idx, api_key)
    return {"markdown": structural_overview(idx), "source": "structural", "cached": False}


def explain_folder(idx: RepoIndex, folder: str, use_ai: bool, api_key: str | None = None) -> dict:
    if folder not in idx.dirs:
        raise KeyError(folder)
    if not folder:
        return explain_overview(idx, use_ai, api_key)
    if use_ai:
        return ai_folder(idx, folder, api_key)
    return {"markdown": structural_folder(idx, folder), "source": "structural", "cached": False}


def file_context(idx: RepoIndex, a: dict, sid: str) -> str:
    """Facts from the rest of the repo that help explain one file or function."""
    rel = a["file"]["path"]
    lines = [f"This file is part of the project `{idx.name}` at {rel}."]
    repo = a.get("repo", {})
    if repo.get("used_by"):
        lines.append("Files that use it: " + ", ".join(repo["used_by"][:10]))
    if repo.get("uses"):
        lines.append("Project files it uses: " + ", ".join(repo["uses"][:10]))
    if sid != "<file>":
        s = next((x for x in a["symbols"] if x["id"] == sid), None)
        if s:
            ext_in = [f"{c['file']}: {c['symbol']}" for c in s.get("called_by_ext", [])[:10]]
            ext_out = sorted({f"{c['xfile']}: {c['xsymbol']}" for c in s["calls"] if c.get("xfile")})[:10]
            if ext_in:
                lines.append("Called from other files: " + "; ".join(ext_in))
            if ext_out:
                lines.append("Calls into other files: " + "; ".join(ext_out))
    return "\n".join(lines)
