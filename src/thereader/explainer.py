"""Explanations: an instant one built from the code that needs nothing, and an optional AI one (cached)."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

from .llm import AIConfig, complete, server_config
from .text import doc_summary

PROMPT_VERSION = "2"


# ---------------------------------------------------------------- who pays for an AI explanation
# A person can add an API key from any provider on the page; it comes with each request, is used for that request
# and isn't stored on the server. The server can also have its own key (see llm.server_config). On your own
# computer that key has no limit; on a public server (PUBLIC) it pays for at most THE_READER_AI_DAILY_LIMIT new
# explanations a day across all visitors (0 unless set). Explanations already in the cache are free for everyone.
PUBLIC = False
NO_KEY = "Add an API key with the Add key button at the top to get AI explanations."


class NoAIKey(Exception):
    """No key can pay for a new AI explanation; the message says what to do."""


_spent = {"day": "", "count": 0}
_spent_lock = threading.Lock()


def _shared_limit() -> int | None:
    """New explanations a day the server's key pays for. None means no limit."""
    if server_config() is None:
        return 0
    raw = os.environ.get("THE_READER_AI_DAILY_LIMIT", "").strip()
    if not raw:
        return 0 if PUBLIC else None
    try:
        return max(0, int(raw))
    except ValueError:
        return 0 if PUBLIC else None


def _today() -> str:
    return datetime.date.today().isoformat()


def ai_available(own: AIConfig | None = None) -> bool:
    """Whether a new AI explanation can be paid for, by the person's own key or the server's."""
    if own is not None:
        return True
    limit = _shared_limit()
    if limit is None:
        return True
    with _spent_lock:
        used = _spent["count"] if _spent["day"] == _today() else 0
    return used < limit


def _payer(own: AIConfig | None) -> AIConfig:
    if own is not None:
        return own
    limit = _shared_limit()
    if limit is not None:
        with _spent_lock:
            if _spent["day"] != _today():
                _spent.update(day=_today(), count=0)
            if _spent["count"] >= limit:
                if limit == 0:
                    raise NoAIKey(NO_KEY)
                raise NoAIKey("Today's free AI explanations have run out. Add your own API key with the Add key "
                              "button at the top to keep going.")
            _spent["count"] += 1
    cfg = server_config()
    if cfg is None:
        raise NoAIKey(NO_KEY)
    return cfg


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
    if sid == "<module>":
        return "component code" if a["file"].get("component") else "page scripts" if _lang(a) == "html" \
            else "module code"
    return f"`{_sym(a, sid)['qualname']}`"


def _names(a: dict, ids) -> str:
    return ", ".join(_label(a, i) for i in dict.fromkeys(ids))


# ---------------------------------------------------------------- structural (no AI)
_MODULE_CODE = {
    "python": "Code that runs at import time, outside any function.",
    "javascript": "Code that runs when the file is loaded, outside any function.",
    "typescript": "Code that runs when the file is loaded, outside any function.",
    "vue": "The component's own code: its <script> part, plus what its template shows and calls.",
    "svelte": "The component's own code: its <script> part, plus what its markup shows and calls.",
    "html": "The page's inline scripts, plus the functions its markup calls (onclick and the like).",
    "css": "The stylesheet as a whole.",
}


def _lang(a: dict) -> str:
    return a["file"].get("language", "python")


def structural(a: dict, sid: str) -> str:
    if sid == "<file>":
        return _structural_file(a)
    s = _sym(a, sid)
    parts: list[str] = []
    if s["kind"] == "rule":
        n = s["end_line"] - s["start_line"] + 1
        what = f"`{s['name']}`" if not s["name"].startswith("@") else f"the `{s['name'].split()[0]}` block `{s['name']}`"
        parts.append(f"Styles for {what}, {n} line{'s' if n != 1 else ''}.")
        if s.get("docstring"):
            parts.append(s["docstring"])
        if s["children"]:
            parts.append(f"**Rules inside:** {len(s['children'])}.")
        return "\n\n".join(parts)
    if s["kind"] == "element":
        return f"`{s['name']}` on line {s['start_line']} of the page."
    if s["kind"] == "module":
        parts.append(_MODULE_CODE.get(_lang(a), _MODULE_CODE["python"]))
    elif s["kind"] == "class":
        if "interface" in s["decorators"]:
            bases = f" extending {', '.join(f'`{b}`' for b in s['bases'])}" if s["bases"] else ""
            parts.append(f"Interface `{s['name']}`{bases}: a type that describes what an object has.")
        elif "object" in s["decorators"]:
            parts.append(f"`{s['name']}` is an object that holds functions, used like a class with one instance.")
        else:
            bases = f" extending {', '.join(f'`{b}`' for b in s['bases'])}" if s["bases"] else ""
            parts.append(f"Class `{s['name']}`{bases}.")
        if doc := doc_summary(s.get("docstring")):
            parts.append(doc)
        methods = [c for c in s["children"] if _sym(a, c)["kind"] == "method"]
        if methods:
            parts.append(f"**Methods:** {_names(a, methods)}")
        return "\n\n".join(parts)
    else:
        if s.get("component"):
            parts.append("A component: it returns the markup to show, and other components use it as a tag.")
        elif s.get("hook"):
            parts.append("A hook: a function components call to share stateful logic (its name starts with `use`).")
        if doc := doc_summary(s.get("docstring")):
            parts.append(doc)
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
                            n("return point", "return"),
                            n("raise point" if _lang(a) == "python" else "throw point", "raise")) if x]
        if bits:
            parts.append("**Shape:** " + ", ".join(bits) + f". {complexity_words(s['complexity'])}")
        else:
            parts.append("**Shape:** straight-line code with no branches.")
    if s["kind"] in ("function", "method") and not s["called_by"] and not s["name"].startswith("__"):
        parts.append(_uncalled_note(a, s))
    return "\n\n".join(parts)


# decorators that wrap or describe a function rather than hand it to a framework
_WRAPPERS = {"property", "cached_property", "staticmethod", "classmethod", "abstractmethod", "wraps", "overload",
             "override", "setter", "getter", "deleter", "lru_cache", "cache", "final", "contextmanager",
             "asynccontextmanager", "total_ordering", "deprecated", "dataclass", "get", "set", "static", "interface",
             "object"}


def _files(paths: list[str], limit: int = 3) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def _is_test(a: dict, s: dict) -> bool:
    path = a["file"].get("path") or a["file"]["name"]
    name = path.rsplit("/", 1)[-1]
    in_tests = name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}"
    cls = s["qualname"].rsplit(".", 1)[0] if s["kind"] == "method" else ""
    return in_tests and s["name"].startswith("test") and (not cls or cls.startswith("Test") or "Test" in cls)


def _uncalled_note(a: dict, s: dict) -> str:
    """Why nothing in this file calls a function, saying only what's known: a confident "nothing calls it"
    only when no call, import, registration or use by name could be found."""
    ext = s.get("called_by_ext") or []
    if ext:
        n = len(ext)
        return f"_Only called from other files ({n} place{'s' if n != 1 else ''})._"
    registered = [d for d in s.get("decorators", []) if d.split("(")[0].rsplit(".", 1)[-1] not in _WRAPPERS]
    if registered:
        return (f"_Nothing calls it directly: `@{registered[0]}` registers it, and the framework calls it when it's "
                "needed._")
    if _is_test(a, s):
        return "_A test: the test runner (pytest or unittest) finds it by its name and runs it._"
    if s.get("imported_by"):
        users = s["imported_by"]
        return f"_Not called in this file; {_files(users)} import{'s' if len(users) == 1 else ''} it._"
    if s.get("referenced"):
        return "_Not called directly; its name is passed on as a value (for example as a callback), so it runs later._"
    if s.get("name_used_in"):
        return (f"_No call to it could be traced for certain, but `.{s['name']}` appears in {_files(s['name_used_in'])}, "
                "on objects whose class isn't written down there, so it's probably called from there._")
    if s.get("outside_bases"):
        base = s["outside_bases"][0]
        return (f"_Nothing in this project calls it, but its class builds on `{base}` from outside the project, "
                "whose own code may call it._")
    if s.get("component") and not a.get("repo"):
        return "_Nothing in this file shows it as a tag, so it's likely the root component or used by other files._"
    if s.get("component"):
        return ("_No file in this project shows it as a tag. It may be loaded by a router or framework by its file "
                "name, kept for other projects to use, or simply unused._")
    is_property = any(d.split("(")[0].rsplit(".", 1)[-1] in ("property", "cached_property", "get")
                      for d in s.get("decorators", []))
    if is_property:
        return "_A property: it runs when the attribute is read, and no place that reads it could be found._"
    if a.get("repo"):
        return "_Nothing in this project calls it, imports it or uses its name, so it's likely an entry point or unused._"
    return "_Nothing in this file calls it, so it's likely an entry point or used from elsewhere._"


def complexity_words(n: int) -> str:
    """Cyclomatic complexity in words: roughly how many separate paths run through the code."""
    feel = "easy to follow" if n <= 4 else "takes some following" if n <= 9 else "intricate"
    return f"Complexity {n}: {feel}."


def _count(n: int, word: str) -> str:
    if not n:
        return ""
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}{'es' if word.endswith('s') else 's'}"


_NODE = {"assert", "async_hooks", "buffer", "child_process", "cluster", "console", "crypto", "dgram", "dns",
         "events", "fs", "http", "http2", "https", "net", "os", "path", "perf_hooks", "process", "querystring",
         "readline", "stream", "string_decoder", "timers", "tls", "tty", "url", "util", "v8", "vm", "worker_threads",
         "zlib", "module", "test"}


def _package(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _structural_web(a: dict) -> str:
    """The file summary for JavaScript, TypeScript, Vue, Svelte, HTML and CSS."""
    f, lang = a["file"], _lang(a)
    syms = [s for s in a["symbols"] if s["kind"] != "module"]
    head = f"`{f['name']}`: {_count(f['lines'], 'line')}"
    if lang == "css":
        rules = [s for s in syms if s["kind"] == "rule"]
        parts = [head + f", {_count(len(rules), 'rule')}."]
        if f.get("docstring"):
            parts.append(f["docstring"])
        if a["imports"]:
            parts.append("**Imports:** " + ", ".join(f"`{i['module']}`" for i in a["imports"]))
        if a.get("globals"):
            parts.append("**Custom properties:** " + ", ".join(f"`{g['name']}`" for g in a["globals"][:12])
                         + (" …" if len(a["globals"]) > 12 else ""))
        return "\n\n".join(parts)
    comps = [s for s in syms if s.get("component")]
    classes = [s for s in syms if s["kind"] == "class" and "object" not in s["decorators"]]
    funcs = [s for s in syms if s["kind"] in ("function", "method") and not s.get("component")]
    counts = [_count(len(comps), "component"), _count(len(classes), "class"), _count(len(funcs), "function")]
    parts = [head + "".join(f", {c}" for c in counts if c) + "."]
    if f.get("component"):
        parts.append(f"A {lang.capitalize()} component: markup, the code behind it and usually its styles in one file.")
    if doc := doc_summary(f.get("docstring")):
        parts.append(doc)
    loads = [i for i in a["imports"] if i.get("html")]
    if loads:
        parts.append("**Loads:** " + ", ".join(f"`{i['module']}`" for i in loads))
    specs = [i["module"] for i in a["imports"] if not i.get("html")]
    libs = sorted({_package(m) for m in specs if not m.startswith((".", "/", "@/", "~/", "#")) and not m.startswith(
        "node:") and _package(m) not in _NODE})
    node = sorted({m.removeprefix("node:").split("/")[0] for m in specs if m.startswith("node:") or _package(m) in _NODE})
    if libs:
        parts.append("**Libraries:** " + ", ".join(f"`{t}`" for t in libs))
    if node:
        parts.append("**Node built-ins:** " + ", ".join(node))
    if not a.get("repo"):
        local = sorted({m for m in specs if m.startswith((".", "/", "@/", "~/"))})
        if local:
            parts.append("**Own files:** " + ", ".join(f"`{m}`" for m in local[:10]))
    exported = [k for k in (a.get("exports") or {}) if k != "default"]
    if exported:
        parts.append("**Exports:** " + ", ".join(f"`{k}`" for k in exported[:12]) + (" …" if len(exported) > 12 else "")
                     + (", plus a default export" if "default" in (a.get("exports") or {}) else ""))
    hot = sorted([s for s in syms if s["kind"] in ("function", "method")], key=lambda s: -s["complexity"])[:3]
    hot = [s for s in hot if s["complexity"] >= 5]
    if hot:
        parts.append("**Most intricate:** " + ", ".join(f"`{s['qualname']}` ({s['complexity']})" for s in hot))
    if a["reading_order"]:
        parts.append("**Suggested reading order:** " + " → ".join(
            _label(a, i) for i in a["reading_order"][:8]) + (" → …" if len(a["reading_order"]) > 8 else ""))
    return "\n\n".join(parts)


def _structural_file(a: dict) -> str:
    if _lang(a) != "python":
        return _structural_web(a)
    f = a["file"]
    syms = [s for s in a["symbols"] if s["kind"] != "module"]
    classes = [s for s in syms if s["kind"] == "class"]
    funcs = [s for s in syms if s["kind"] == "function"]
    methods = [s for s in syms if s["kind"] == "method"]
    counts = [_count(len(classes), "class"), _count(len(funcs), "function"), _count(len(methods), "method")]
    parts = [f"`{f['name']}`: " + ", ".join([_count(f["lines"], "line")] + [c for c in counts if c]) + "."]
    if doc := doc_summary(f.get("docstring")):
        parts.append(doc)
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


_FENCE = {"python": "python", "javascript": "javascript", "typescript": "typescript", "vue": "vue",
          "svelte": "svelte", "html": "html", "css": "css"}


def _prompt(a: dict, sid: str, context: str = "") -> str:
    fence = _FENCE.get(_lang(a), "")
    name = a["file"].get("path") or a["file"]["name"]
    ctx = f"File: {name}\n\nOutline:\n{_outline(a)}\n\nImports:\n" + "\n".join(
        f"- {i['module']}" + (f": {', '.join(n['name'] for n in i['names'])}" if i["names"] else "") for i in a["imports"])
    if context:
        ctx += f"\n\n{context}"
    if sid == "<file>":
        return (
            f"{ctx}\n\nFull source:\n```{fence}\n{_code(a, {'start_line': 1, 'end_line': a['file']['lines']})}\n```\n\n"
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
        f"```{fence}\n{_code(a, s)}\n```\n\n"
        "Respond in under 180 words:\n"
        "- First line: one plain sentence saying what it does and why it exists (no heading).\n"
        "- **Step by step**: a numbered list of the logic, each step citing lines.\n"
        "- **In / out**: what it takes, returns, mutates or raises (one or two lines).\n"
        "- **Watch out**: only if there is a real gotcha, edge case or bug; otherwise omit this section."
    )


def _cache_key(key_parts: list) -> str:
    return hashlib.sha256(json.dumps([PROMPT_VERSION, *key_parts]).encode()).hexdigest()


def cached_markdown(key_parts: list) -> str | None:
    """An AI explanation that was already written, or None. Never asks the AI."""
    return cache().get(_cache_key(key_parts))


def ai_markdown(prompt: str, key_parts: list, max_tokens: int = 900, own: AIConfig | None = None) -> dict:
    """Ask an AI model, caching the answer under everything that shaped the prompt. `own` is the person's own key.
    The cache is shared by every model: once anyone has an explanation of some code, it's free for everyone."""
    key = _cache_key(key_parts)
    hit = cache().get(key)
    if hit:
        return {"markdown": hit, "source": "ai", "cached": True}
    md = complete(_payer(own), SYSTEM, prompt, max_tokens)
    cache().put(key, md)
    return {"markdown": md, "source": "ai", "cached": False}


def _key_parts(a: dict, sid: str, context: str) -> list:
    code = a["source"] if sid == "<file>" else _code(a, _sym(a, sid), numbered=False)
    return [sid, code, _outline(a), context]


def explain(a: dict, sid: str, use_ai: bool, context: str = "", own: AIConfig | None = None) -> dict:
    """Explain a function, class or (sid="<file>") the whole file. `context` adds repo-level facts."""
    if not use_ai:
        return {"markdown": structural(a, sid), "source": "structural", "cached": False}
    return ai_markdown(_prompt(a, sid, context), _key_parts(a, sid, context), own=own)


def explain_made(a: dict, sid: str, context: str = "") -> dict:
    """The AI explanation if one was already written, otherwise the one worked out from the code. Free."""
    md = cached_markdown(_key_parts(a, sid, context))
    if md:
        return {"markdown": md, "source": "ai", "cached": True}
    return {"markdown": structural(a, sid), "source": "structural", "cached": False}
