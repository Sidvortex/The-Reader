"""FastAPI server: file and repo analysis, explanations, and the built web UI."""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, explainer
from .analyzer import UnsupportedLanguage, analyze
from .explainer import NoAIKey, ai_available, explain, model_name
from .repo import github, index, jobs, summaries

UI_DIR = Path(__file__).parent / "frontend_build"
MAX_BYTES = 2_000_000

app = FastAPI(title="The Reader", version=__version__)
_analyses: OrderedDict[str, dict] = OrderedDict()
_repo_files: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
_initial: dict | None = None

# Only answer requests addressed to this machine by name. This blocks DNS-rebinding tricks where a web
# page points its own domain at 127.0.0.1 to read your files through this server.
_allowed_hosts: set[str] | None = {"127.0.0.1", "localhost", "::1", "testserver"}

# Public mode is for running The Reader as a website. Visitors can read GitHub links and single files, but never
# folders on the server, and AI explanations use their own API key (see explainer.py).
PUBLIC = False


def allow_any_host() -> None:
    """Used when the user deliberately serves on a network address (--host 0.0.0.0)."""
    global _allowed_hosts
    _allowed_hosts = None


def enable_public() -> None:
    global PUBLIC
    PUBLIC = True
    explainer.PUBLIC = True
    allow_any_host()
    # Sized for a small server (512 MB): one project read at a time, no helper processes, two projects in memory.
    # A project the size of Django (about 2,900 Python files) needs about 250 MB while it's read.
    index.MAX_PY_FILES = int(os.environ.get("THE_READER_MAX_PY_FILES", "2500"))
    index.WORKERS = int(os.environ.get("THE_READER_WORKERS", "1"))
    jobs.MAX_OPEN = int(os.environ.get("THE_READER_MAX_OPEN", "2"))
    jobs._slots = threading.BoundedSemaphore(int(os.environ.get("THE_READER_MAX_BUSY", "1")))
    github.KEEP = int(os.environ.get("THE_READER_KEEP_REPOS", "6"))


if os.environ.get("THE_READER_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on"):
    enable_public()


@app.middleware("http")
async def _host_guard(request: Request, call_next):
    if _allowed_hosts is not None:
        host = request.headers.get("host", "")
        host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
        if host not in _allowed_hosts:
            return PlainTextResponse("This server only answers on localhost.", status_code=400)
    return await call_next(request)


def _remember(a: dict) -> None:
    _analyses[a["file"]["hash"]] = a
    _analyses.move_to_end(a["file"]["hash"])
    while len(_analyses) > 48:
        _analyses.popitem(last=False)


def run_analysis(filename: str, source: str) -> dict:
    a = analyze(filename, source)
    _remember(a)
    return a


def open_initial(path: Path) -> None:
    """Open a file or folder passed on the command line so the page starts there."""
    global _initial
    if path.is_dir():
        _initial = {"kind": "repo", "id": jobs.open_repo(path).id}
    else:
        _initial = {"kind": "file", "analysis": run_analysis(path.name, path.read_text(encoding="utf-8", errors="replace"))}


def open_initial_link(link: str) -> None:
    """Open a GitHub link passed on the command line. Raises github.LinkError with a readable message."""
    global _initial
    job, path = jobs.open_github(link)
    _initial = {"kind": "repo", "id": job.id, "path": path}


def _ai_errors(fn, own_key: bool = False):
    try:
        return fn()
    except (KeyError, HTTPException):
        raise
    except NoAIKey as e:
        raise HTTPException(403, str(e))
    except Exception as e:  # surface API errors to the page instead of a bare 500
        status = getattr(e, "status_code", None)
        if status == 401 and own_key:
            msg = "AI explanation failed: Anthropic rejected your API key. Check it with the key button at the top."
        elif status == 401:
            msg = "AI explanation failed: the API key was rejected. Check ANTHROPIC_API_KEY and restart the-reader."
        elif status == 429:
            msg = "AI explanation failed: rate limited. Wait a moment and select it again."
        else:
            msg = f"AI explanation failed: {type(e).__name__}"
            if not PUBLIC:
                msg += f": {e}"
        raise HTTPException(502, msg)


def _visitor_key(value: str | None) -> str | None:
    """A visitor's own Anthropic key from the X-Anthropic-Key header, used for this request only."""
    value = (value or "").strip()
    return value if 20 <= len(value) <= 300 and value.isascii() and " " not in value else None


# ------------------------------------------------------------------ single files
class AnalyzeIn(BaseModel):
    filename: str = "snippet.py"
    source: str


class ExplainIn(BaseModel):
    hash: str
    symbol: str
    ai: bool = True


@app.get("/api/health")
def health():
    # "ai": the server can pay for AI explanations without a visitor's own key
    return {"version": __version__, "ai": ai_available(), "model": model_name(), "public": PUBLIC}


@app.get("/api/initial")
def initial():
    return _initial


@app.get("/api/example")
def example():
    path = Path(__file__).parent / "examples" / "inventory.py"
    return run_analysis(path.name, path.read_text(encoding="utf-8"))


@app.post("/api/analyze")
def analyze_ep(body: AnalyzeIn):
    if len(body.source.encode()) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 2 MB")
    try:
        return run_analysis(body.filename, body.source)
    except UnsupportedLanguage as e:
        raise HTTPException(415, str(e))


@app.post("/api/explain")
def explain_ep(body: ExplainIn, x_anthropic_key: str | None = Header(default=None)):
    a = _analyses.get(body.hash)
    if a is None:
        raise HTTPException(404, "Analysis expired, re-open the file")
    if body.symbol != "<file>" and not any(s["id"] == body.symbol for s in a["symbols"]):
        raise HTTPException(404, "Unknown symbol")
    key = _visitor_key(x_anthropic_key)
    return _ai_errors(lambda: explain(a, body.symbol, body.ai, api_key=key), own_key=bool(key))


# ------------------------------------------------------------------ repos
class RepoOpenIn(BaseModel):
    path: str


class RepoExplainIn(BaseModel):
    id: str
    level: str  # repo | dir | file | symbol
    path: str = ""
    symbol: str | None = None
    ai: bool = True


def _ready_index(rid: str):
    job = jobs.get_job(rid)
    if job is None:
        raise HTTPException(404, "This project isn't open any more. Open the folder again.")
    if job.state == "error":
        raise HTTPException(409, job.error or "This project couldn't be read.")
    if job.state != "ready" or job.index is None:
        raise HTTPException(409, "This project is still being read.")
    return job.index


def _repo_file(rid: str, path: str) -> dict:
    idx = _ready_index(rid)
    if path not in idx.py:
        raise HTTPException(404, f"{path} isn't a Python file in this project")
    try:
        mtime = (idx.root / path).stat().st_mtime
    except OSError:
        raise HTTPException(404, f"{path} can't be read any more")
    key = (rid, path)
    hit = _repo_files.get(key)
    if hit and hit[0] == mtime:
        _repo_files.move_to_end(key)
        return hit[1]
    a = idx.file_view(path)
    _remember(a)
    _repo_files[key] = (mtime, a)
    while len(_repo_files) > 64:
        _repo_files.popitem(last=False)
    return a


@app.post("/api/repo/open")
def repo_open(body: RepoOpenIn):
    """Open a folder on this computer, or a GitHub link. A public server only takes links."""
    text = body.path.strip()
    if github.looks_like_github(text) or PUBLIC:
        try:
            job, path = jobs.open_github(text, allow_short=PUBLIC)
        except github.LinkError as e:
            raise HTTPException(400, str(e))
        return {**job.status(), "path": path}
    try:
        job = jobs.open_repo(text)
    except (NotADirectoryError, FileNotFoundError, OSError) as e:
        # "owner/repo" that isn't a folder here is probably a GitHub shorthand
        if re.fullmatch(r"[\w.-]+/[\w.-]+", text) and not Path(text).expanduser().exists():
            try:
                job, path = jobs.open_github(text, allow_short=True)
                return {**job.status(), "path": path}
            except github.LinkError:
                pass
        raise HTTPException(400, str(e))
    return {**job.status(), "path": ""}


@app.get("/api/repo/status")
def repo_status(id: str):
    job = jobs.get_job(id)
    if job is None:
        raise HTTPException(404, "Unknown project")
    return job.status()


@app.get("/api/repo/index")
def repo_index(id: str):
    return _ready_index(id).to_json()


@app.get("/api/repo/file")
def repo_file(id: str, path: str):
    return _repo_file(id, path)


@app.post("/api/repo/explain")
def repo_explain(body: RepoExplainIn, x_anthropic_key: str | None = Header(default=None)):
    idx = _ready_index(body.id)
    key = _visitor_key(x_anthropic_key)
    if body.level == "repo":
        return _ai_errors(lambda: summaries.explain_overview(idx, body.ai, key), own_key=bool(key))
    if body.level == "dir":
        if body.path not in idx.dirs:
            raise HTTPException(404, "Unknown folder")
        return _ai_errors(lambda: summaries.explain_folder(idx, body.path, body.ai, key), own_key=bool(key))
    a = _repo_file(body.id, body.path)
    sid = body.symbol or "<file>"
    if sid != "<file>" and not any(s["id"] == sid for s in a["symbols"]):
        raise HTTPException(404, "Unknown symbol")
    return _ai_errors(lambda: explain(a, sid, body.ai, summaries.file_context(idx, a, sid), api_key=key),
                      own_key=bool(key))


# ------------------------------------------------------------------ the page
if (UI_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")

    _ui_root = UI_DIR.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        f = (UI_DIR / path).resolve()
        # only serve files that really live inside the built UI folder
        if path and f.is_file() and f.is_relative_to(_ui_root):
            if f.suffix == ".md":  # the license and notices: show them as text instead of downloading
                return FileResponse(f, media_type="text/plain; charset=utf-8")
            return FileResponse(f)
        return FileResponse(UI_DIR / "index.html")
