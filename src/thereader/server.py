"""FastAPI server: file and repo analysis, explanations, and the built web UI."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .analyzer import UnsupportedLanguage, analyze
from .explainer import ai_available, explain, model_name
from .repo import jobs, summaries

UI_DIR = Path(__file__).parent / "frontend_build"
MAX_BYTES = 2_000_000

app = FastAPI(title="The Reader", version=__version__)
_analyses: OrderedDict[str, dict] = OrderedDict()
_repo_files: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
_initial: dict | None = None

# Only answer requests addressed to this machine by name. This blocks DNS-rebinding tricks where a web
# page points its own domain at 127.0.0.1 to read your files through this server.
_allowed_hosts: set[str] | None = {"127.0.0.1", "localhost", "::1", "testserver"}


def allow_any_host() -> None:
    """Used when the user deliberately serves on a network address (--host 0.0.0.0)."""
    global _allowed_hosts
    _allowed_hosts = None


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


def _ai_errors(fn):
    try:
        return fn()
    except (KeyError, HTTPException):
        raise
    except Exception as e:  # surface API errors to the page instead of a bare 500
        status = getattr(e, "status_code", None)
        if status == 401:
            msg = "AI explanation failed: the API key was rejected. Check ANTHROPIC_API_KEY and restart the-reader."
        elif status == 429:
            msg = "AI explanation failed: rate limited. Wait a moment and select it again."
        else:
            msg = f"AI explanation failed: {e}"
        raise HTTPException(502, msg)


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
    return {"version": __version__, "ai": ai_available(), "model": model_name() if ai_available() else None}


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
def explain_ep(body: ExplainIn):
    a = _analyses.get(body.hash)
    if a is None:
        raise HTTPException(404, "Analysis expired, re-open the file")
    if body.symbol != "<file>" and not any(s["id"] == body.symbol for s in a["symbols"]):
        raise HTTPException(404, "Unknown symbol")
    return _ai_errors(lambda: explain(a, body.symbol, body.ai))


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
    try:
        job = jobs.open_repo(body.path.strip())
    except (NotADirectoryError, FileNotFoundError, OSError) as e:
        raise HTTPException(400, str(e))
    return job.status()


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
def repo_explain(body: RepoExplainIn):
    idx = _ready_index(body.id)
    if body.level == "repo":
        return _ai_errors(lambda: summaries.explain_overview(idx, body.ai))
    if body.level == "dir":
        if body.path not in idx.dirs:
            raise HTTPException(404, "Unknown folder")
        return _ai_errors(lambda: summaries.explain_folder(idx, body.path, body.ai))
    a = _repo_file(body.id, body.path)
    sid = body.symbol or "<file>"
    if sid != "<file>" and not any(s["id"] == sid for s in a["symbols"]):
        raise HTTPException(404, "Unknown symbol")
    return _ai_errors(lambda: explain(a, sid, body.ai, summaries.file_context(idx, a, sid)))


# ------------------------------------------------------------------ the page
if (UI_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")

    _ui_root = UI_DIR.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        f = (UI_DIR / path).resolve()
        # only serve files that really live inside the built UI folder
        if path and f.is_file() and f.is_relative_to(_ui_root):
            return FileResponse(f)
        return FileResponse(UI_DIR / "index.html")
