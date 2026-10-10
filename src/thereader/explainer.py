"""Explanations: an instant structural one that needs nothing, and an optional AI one (cached)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

PROMPT_VERSION = "1"
DEFAULT_MODEL = "claude-sonnet-5-5"


def model_name() -> str:
    return os.environ.get("THE_READER_MODEL", DEFAULT_MODEL)


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- cache
class _Cache:
    def __init__(self):
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "the-reader"
        base.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(base / "explanations.sqlite", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS ex (key TEXT PRIMARY KEY, md TEXT)")
        self.lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT md FROM ex WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, md: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO ex VALUES (?, ?)", (key, md))
            self.db.commit()


_cache: _Cache | None = None


def cache() -> _Cache:
    global _cache
    if _cache is None:
        _cache = _Cache()
    return _cache


# ---------------------------------------------------------------- helpers
def _code(a: dict, s: dict, numbered: bool = True) -> str:
    lines = a["source"].split("\n")[s["start_line"] - 1: s["end_line"]]
    if not numbered:
        return "\n".join(lines)
    return "\n".join(f"{s['start_line'] + i:>4} | {ln}" for i, ln in enumerate(lines))


def _outline(a: dict) -> str:
    out = []
    for s in a["symbols"]:
        if s["kind"] == "module":
            continue
        depth = 0
        p = s["parent"]
        while p:
            depth += 1
            p = next(x for x in a["symbols"] if x["id"] == p)["parent"]
        sig = f"({', '.join(s['params'])})" if s["kind"] != "class" else ""
        doc = f"  # {s['docstring'].splitlines()[0]}" if s.get("docstring") else ""
        out.append(f"{'  ' * depth}{s['kind']} {s['name']}{sig}  [L{s['start_line']}-{s['end_line']}]{doc}")
    return "\n".join(out)


def _sym(a: dict, sid: str) -> dict:
    return next(s for s in a["symbols"] if s["id"] == sid)


def _label(a: dict, sid: str) -> str:
    return "module code" if sid == "<module>" else f"`{_sym(a, sid)['qualname']}`"


def _names(a: dict, ids) -> str:
    return ", ".join(_label(a, i) for i in dict.fromkeys(ids))


# ---------------------------------------------------------------- structural (no AI)
def structural(a: dict, sid: str) -> str:
    if sid == "<file>":
        return _structural_file(a)
    s = _sym(a, sid)
    parts: list[str] = []
    if s["kind"] == "module":
        parts.append("Code that runs at import time, outside any function.")
    elif s["kind"] == "class":
        bases = f" extending {', '.join(f'`{b}`' for b in s['bases'])}" if s["bases"] else ""
        parts.append(f"Class `{s['name']}`{bases}.")
        if s.get("docstring"):
            parts.append(s["docstring"].strip().split("\n\n")[0])
        methods = [c for c in s["children"] if _sym(a, c)["kind"] == "method"]
        if methods:
            parts.append(f"**Methods:** {_names(a, methods)}")
        return "\n\n".join(parts)
    else:
        if s.get("docstring"):
            parts.append(s["docstring"].strip().split("\n\n")[0])
        sig_bits = []
        params = [p for p in s["params"] if p.split(":")[0].split("=")[0].strip() not in ("self", "cls")]
        sig_bits.append(f"Takes {', '.join(f'`{p}`' for p in params)}." if params else "Takes no arguments.")
        if s["returns"]:
            sig_bits.append(f"Declared to return `{s['returns']}`.")
        parts.append(" ".join(sig_bits))

    fc = a["flowcharts"].get(sid)
    if fc:
        kinds = [n["kind"] for n in fc["nodes"]]

        def n(word, k):
            c = kinds.count(k)
            return f"{c} {word}{'s' if c > 1 else ''}" if c else None

        bits = [x for x in (n("decision", "decision"), n("loop", "loop"),
                            "error handling" if "try" in kinds else None,
                            n("return point", "return"), n("raise point", "raise")) if x]
        if bits:
            parts.append("**Shape:** " + ", ".join(bits) + f". Complexity {s['complexity']}.")
        else:
            parts.append("**Shape:** straight-line code with no branches.")
    if s["kind"] in ("function", "method") and not s["called_by"] and not s["name"].startswith("__"):
        if s.get("called_by_ext"):
            parts.append(f"_Only called from other files ({len(s['called_by_ext'])} place{'s' if len(s['called_by_ext']) != 1 else ''})._")
        elif a.get("repo"):
            parts.append("_Nothing in this project calls it directly, so it's likely an entry point, a callback or public API._")
        else:
            parts.append("_Nothing in this file calls it, so it's likely an entry point or public API._")
    return "\n\n".join(parts)


def _count(n: int, word: str) -> str:
    if not n:
        return ""
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}{'es' if word.endswith('s') else 's'}"


def _structural_file(a: dict) -> str:
    f = a["file"]
    syms = [s for s in a["symbols"] if s["kind"] != "module"]
    classes = [s for s in syms if s["kind"] == "class"]
    funcs = [s for s in syms if s["kind"] == "function"]
    methods = [s for s in syms if s["kind"] == "method"]
    counts = [_count(len(classes), "class"), _count(len(funcs), "function"), _count(len(methods), "method")]
    parts = [f"`{f['name']}`: " + ", ".join([_count(f["lines"], "line")] + [c for c in counts if c]) + "."]
    if f.get("docstring"):
        parts.append(f["docstring"].strip().split("\n\n")[0])
    stdlib = getattr(sys, "stdlib_module_names", set())
    tops = {i["module"].split(".")[0] for i in a["imports"] if not i["module"].startswith(".")}
    tops.discard("__future__")
    tops -= set((a.get("repo") or {}).get("own_packages", []))  # the project's own packages aren't libraries
    libs = sorted(t for t in tops if t not in stdlib)
    std = sorted(t for t in tops if t in stdlib)
    local = sorted({i["module"] for i in a["imports"] if i["module"].startswith(".")})
    if libs:
        parts.append("**Libraries:** " + ", ".join(f"`{t}`" for t in libs))
    if std:
        parts.append("**Standard library:** " + ", ".join(std))
    if local and not a.get("repo"):  # in repo mode the linked files are listed beside this
        parts.append("**Own package:** " + ", ".join(f"`{m}`" for m in local))
    hot = sorted([s for s in syms if s["kind"] != "class"], key=lambda s: -s["complexity"])[:3]
    hot = [s for s in hot if s["complexity"] >= 5]
    if hot:
        parts.append("**Most intricate:** " + ", ".join(f"`{s['qualname']}` ({s['complexity']})" for s in hot))
    if a["reading_order"]:
        parts.append("**Suggested reading order:** " + " → ".join(
            _label(a, i) for i in a["reading_order"][:8]) + (" → …" if len(a["reading_order"]) > 8 else ""))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- AI
SYSTEM = (
    "You explain code to a developer who is reading it for the first time. Be concrete and brief. "
    "Refer to lines as L12 or L12-L18. Use markdown. No preamble, no closing remarks, no restating the code."
)


def _prompt(a: dict, sid: str, context: str = "") -> str:
    name = a["file"].get("path") or a["file"]["name"]
    ctx = f"File: {name}\n\nOutline:\n{_outline(a)}\n\nImports:\n" + "\n".join(
        f"- {i['module']}" + (f": {', '.join(n['name'] for n in i['names'])}" if i["names"] else "") for i in a["imports"])
    if context:
        ctx += f"\n\n{context}"
    if sid == "<file>":
        return (
            f"{ctx}\n\nFull source:\n```python\n{_code(a, {'start_line': 1, 'end_line': a['file']['lines']})}\n```\n\n"
            "Write an overview of this file in under 220 words:\n"
            "1. One sentence: what this file is for.\n"
            "2. **Main pieces**: 3-6 bullets, each `name`: its role.\n"
            "3. **How it fits together**: 2-4 sentences on the main flow (what calls what, where data goes).\n"
            "4. **Read in this order**: a short ordered list of names with a few words on why."
        )
    s = _sym(a, sid)
    callers = _names(a, s["called_by"]) or "nothing in this file"
    return (
        f"{ctx}\n\nExplain `{s['qualname']}` ({s['kind']}), called by {callers}:\n"
        f"```python\n{_code(a, s)}\n```\n\n"
        "Respond in under 180 words:\n"
        "- First line: one plain sentence saying what it does and why it exists (no heading).\n"
        "- **Step by step**: a numbered list of the logic, each step citing lines.\n"
        "- **In / out**: what it takes, returns, mutates or raises (one or two lines).\n"
        "- **Watch out**: only if there is a real gotcha, edge case or bug; otherwise omit this section."
    )


def ai_markdown(prompt: str, key_parts: list, max_tokens: int = 900) -> dict:
    """Ask Claude, caching the answer under everything that shaped the prompt."""
    key = hashlib.sha256(json.dumps([PROMPT_VERSION, model_name(), *key_parts]).encode()).hexdigest()
    hit = cache().get(key)
    if hit:
        return {"markdown": hit, "source": "ai", "cached": True}
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model_name(), max_tokens=max_tokens, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    md = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    cache().put(key, md)
    return {"markdown": md, "source": "ai", "cached": False}


def explain(a: dict, sid: str, use_ai: bool, context: str = "") -> dict:
    """Explain a function, class or (sid="<file>") the whole file. `context` adds repo-level facts."""
    if not use_ai or not ai_available():
        return {"markdown": structural(a, sid), "source": "structural", "cached": False}
    code = a["source"] if sid == "<file>" else _code(a, _sym(a, sid), numbered=False)
    return ai_markdown(_prompt(a, sid, context), [sid, code, _outline(a), context])
