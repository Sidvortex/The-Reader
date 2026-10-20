"""FastAPI server: file and repo analysis, explanations, and the built web UI."""

from __future__ import annotations

import os
import re
import threading
import traceback
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, explainer, export, llm
from .analyzer import UnsupportedLanguage, analyze
from .explainer import NoAIKey, ai_available, explain
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
    index.MAX_PY_FILES = int(os.environ.get("THE_READER_MAX_FILES") or os.environ.get("THE_READER_MAX_PY_FILES")
                             or "2500")
    index.WORKERS = int(os.environ.get("THE_READER_WORKERS", "1"))
    jobs.MAX_OPEN = int(os.environ.get("THE_READER_MAX_OPEN", "2"))
    jobs._slots = threading.BoundedSemaphore(int(os.environ.get("THE_READER_MAX_BUSY", "1")))
    github.KEEP = int(os.environ.get("THE_READER_KEEP_REPOS", "6"))
    # documents: one made at a time, and shorter, so a small server keeps up
    export._slots = threading.BoundedSemaphore(int(os.environ.get("THE_READER_EXPORTS_AT_ONCE", "1")))
    export.MAX_SECTIONS = int(os.environ.get("THE_READER_EXPORT_MAX_SECTIONS", "1500"))


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


def _ai_errors(fn):
    """Run an explanation, turning failures into a message the page can show."""
    try:
        return fn()
    except (KeyError, HTTPException):
        raise
    except NoAIKey as e:
        raise HTTPException(403, str(e))
    except llm.AIError as e:
        raise HTTPException(e.status, str(e))
    except Exception:  # the details go to the terminal, not to the page
        traceback.print_exc()
        raise HTTPException(502, "The AI explanation couldn't be written. Try again in a moment.")


_TOKEN = re.compile(r"[\x21-\x7e]{1,400}")  # printable ASCII, no spaces


def _own_ai(request: Request) -> llm.AIConfig | None:
    """The person's own AI settings, sent with each explanation request from the key button on the page.
    They're used for that request only and never stored here."""
    h = request.headers
    key = (h.get("x-ai-key") or "").strip()
    provider = (h.get("x-ai-provider") or "").strip().lower()
    model = (h.get("x-ai-model") or "").strip() or None
    base = (h.get("x-ai-base-url") or "").strip() or None
    if model and not _TOKEN.fullmatch(model):
        raise HTTPException(400, "That model name doesn't look right. Check it in the key settings.")
    if provider == llm.CUSTOM:
        # your own AI server works on your own computer only: a public server must not fetch addresses for visitors
        if PUBLIC:
            raise HTTPException(400, "This site can't use your own AI server. Pick one of the listed providers.")
        if not base or not re.fullmatch(r"https?://[^\s]{1,300}", base):
            raise HTTPException(400, "Type your AI server's address in the key settings, like http://localhost:11434/v1")
        if key and not _TOKEN.fullmatch(key):
            raise HTTPException(400, "That API key doesn't look right. Paste it again in the key settings.")
        return llm.AIConfig(llm.CUSTOM, key, model, base)
    if not key:
        return None
    if not _TOKEN.fullmatch(key):
        raise HTTPException(400, "That API key doesn't look right. Paste it again in the key settings.")
    provider = provider if provider in llm.PROVIDERS else llm.detect(key)
    if provider is None:
        raise HTTPException(400, "Pick which provider your API key is from in the key settings.")
    return llm.AIConfig(provider, key, model)


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
    # "ai": the server pays for AI explanations itself; "providers": what the key settings offer
    own = llm.server_config()
    return {"version": __version__, "public": PUBLIC, "ai": ai_available(),
            "ai_name": own.name if own and ai_available() else None,
            "providers": llm.describe(), "custom_server": not PUBLIC}


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
        raise HTTPException(413, "This file is too large to open here (the limit is 2 MB).")
    try:
        return run_analysis(body.filename, body.source)
    except UnsupportedLanguage as e:
        raise HTTPException(415, str(e))


@app.post("/api/explain")
def explain_ep(body: ExplainIn, request: Request):
    a = _analyses.get(body.hash)
    if a is None:
        raise HTTPException(404, "This file isn't open here any more. Open it again.")
    if body.symbol != "<file>" and not any(s["id"] == body.symbol for s in a["symbols"]):
        raise HTTPException(404, "That function isn't in this file.")
    own = _own_ai(request) if body.ai else None
    return _ai_errors(lambda: explain(a, body.symbol, body.ai, own=own))


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
        raise HTTPException(404, "This project isn't open any more. Open it again.")
    if job.state == "error":
        raise HTTPException(409, job.error or "This project couldn't be read.")
    if job.state != "ready" or job.index is None:
        raise HTTPException(409, "This project is still being read.")
    return job.index


def _repo_file(rid: str, path: str) -> dict:
    idx = _ready_index(rid)
    if path not in idx.code:
        raise HTTPException(404, f"{path} isn't a file The Reader can read in this project.")
    try:
        mtime = (idx.root / path).stat().st_mtime
    except OSError:
        raise HTTPException(404, f"{path} can't be opened any more.")
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
    except (NotADirectoryError, FileNotFoundError, OSError):
        # "owner/repo" that isn't a folder here is probably a GitHub shorthand
        if re.fullmatch(r"[\w.-]+/[\w.-]+", text) and not Path(text).expanduser().exists():
            try:
                job, path = jobs.open_github(text, allow_short=True)
                return {**job.status(), "path": path}
            except github.LinkError:
                pass
        raise HTTPException(400, "That folder can't be opened. Check the path and try again.")
    return {**job.status(), "path": ""}


@app.get("/api/repo/status")
def repo_status(id: str):
    job = jobs.get_job(id)
    if job is None:
        raise HTTPException(404, "This project isn't open any more. Open it again.")
    return job.status()


@app.get("/api/repo/index")
def repo_index(id: str):
    return _ready_index(id).to_json()


@app.get("/api/repo/file")
def repo_file(id: str, path: str):
    return _repo_file(id, path)


@app.post("/api/repo/explain")
def repo_explain(body: RepoExplainIn, request: Request):
    idx = _ready_index(body.id)
    own = _own_ai(request) if body.ai else None
    if body.level == "repo":
        return _ai_errors(lambda: summaries.explain_overview(idx, body.ai, own))
    if body.level == "dir":
        if body.path not in idx.dirs:
            raise HTTPException(404, "That folder isn't in this project.")
        return _ai_errors(lambda: summaries.explain_folder(idx, body.path, body.ai, own))
    a = _repo_file(body.id, body.path)
    sid = body.symbol or "<file>"
    if sid != "<file>" and not any(s["id"] == sid for s in a["symbols"]):
        raise HTTPException(404, "That function isn't in this file.")
    return _ai_errors(lambda: explain(a, sid, body.ai, summaries.file_context(idx, a, sid), own=own))


class ExportIn(BaseModel):
    id: str
    scope: str = ""  # a folder, or "" for the whole project
    depth: str = "functions"  # folders | files | functions
    format: str = "pdf"  # pdf | docx
    skip_tests: bool = True


@app.post("/api/repo/export")
def repo_export(body: ExportIn):
    """Start making a PDF or Word file of the project's explanation. Poll its status, then download it."""
    idx = _ready_index(body.id)
    try:
        job = export.start(idx, body.scope, body.depth, body.format, body.skip_tests)
    except KeyError:
        raise HTTPException(404, "That folder isn't in this project.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return job.status()


def _export_job(id: str):
    job = export.get(id)
    if job is None:
        raise HTTPException(404, "This document isn't available any more. Make it again.")
    return job


@app.get("/api/repo/export/status")
def repo_export_status(id: str):
    return _export_job(id).status()


@app.get("/api/repo/export/file")
def repo_export_file(id: str):
    job = _export_job(id)
    if job.state != "ready" or not job.path or not job.path.exists():
        raise HTTPException(409, "This document isn't ready yet.")
    return FileResponse(job.path, media_type=export.FORMATS[job.fmt][0], filename=job.filename)


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
