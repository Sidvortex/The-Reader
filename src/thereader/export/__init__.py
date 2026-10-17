"""Export a project's layered explanation as a PDF or Word file. Exports run in the background with progress,
because a big project takes a while; the finished file waits in a temporary folder until it's downloaded."""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import tempfile
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path

from ..repo.index import RepoIndex
from .build import DEPTHS, build

FORMATS = {"pdf": ("application/pdf", "PDF"),
           "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Word file")}
KEEP = 4  # finished files kept for download
MAX_AGE = 30 * 60  # seconds a finished file is kept
MAX_SECTIONS: int | None = None  # None: build.MAX_SECTIONS; public mode sets a lower limit

_jobs: OrderedDict[str, "ExportJob"] = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(int(os.environ.get("THE_READER_EXPORTS_AT_ONCE", "2")))
_dir: Path | None = None


def _folder() -> Path:
    global _dir
    if _dir is None or not _dir.exists():
        _dir = Path(tempfile.mkdtemp(prefix="the-reader-exports-"))
    return _dir


class ExportJob:
    def __init__(self, idx: RepoIndex, scope: str, depth: str, fmt: str, skip_tests: bool):
        self.id = secrets.token_urlsafe(12)
        self.idx, self.scope, self.depth, self.fmt, self.skip_tests = idx, scope, depth, fmt, skip_tests
        self.state = "waiting"  # waiting | working | ready | error
        self.phase = "Waiting for another export to finish"
        self.done = self.total = 0
        self.error: str | None = None
        self.path: Path | None = None
        self.filename = "explanation." + fmt
        self.finished = 0.0

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "phase": self.phase, "done": self.done, "total": self.total,
                "error": self.error, "filename": self.filename}

    def _progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def run(self) -> None:
        with _slots:
            try:
                self.state = "working"
                self.phase = "Collecting the explanations"
                doc = build(self.idx, self.scope, self.depth, self.skip_tests, self._progress, MAX_SECTIONS)
                self.phase = f"Writing the {FORMATS[self.fmt][1]}"
                self.filename = f"{doc.filename}.{self.fmt}"
                path = _folder() / f"{self.id}.{self.fmt}"
                if self.fmt == "pdf":
                    from .pdf import write_pdf
                    write_pdf(doc, path)
                else:
                    from .word import write_docx
                    write_docx(doc, path)
                self.path = path
                self.state, self.phase = "ready", "Ready"
            except ImportError:
                traceback.print_exc()
                self.state = "error"
                self.error = ("Saving documents needs two libraries that aren't installed yet. Run The Reader's "
                              "install command again to add them.")
            except Exception:
                traceback.print_exc()
                self.state = "error"
                self.error = "The document couldn't be made. Try again, or export a smaller part."
            finally:
                self.idx = None  # don't keep the project in memory because of this job
                self.finished = time.time()
                _tidy()


def _tidy() -> None:
    """Forget old exports and delete their files."""
    with _lock:
        done = [j for j in _jobs.values() if j.state in ("ready", "error")]
        now = time.time()
        old = [j for j in done if now - j.finished > MAX_AGE] + done[:-KEEP]
        for j in {id(j): j for j in old}.values():
            _jobs.pop(j.id, None)
            if j.path:
                j.path.unlink(missing_ok=True)


def start(idx: RepoIndex, scope: str, depth: str, fmt: str, skip_tests: bool) -> ExportJob:
    if fmt not in FORMATS:
        raise ValueError("Pick PDF or Word.")
    if depth not in DEPTHS:
        raise ValueError("Pick how much to include.")
    if scope not in idx.dirs:
        raise KeyError(scope)
    _tidy()
    job = ExportJob(idx, scope, depth, fmt, skip_tests)
    with _lock:
        _jobs[job.id] = job
    threading.Thread(target=job.run, daemon=True, name=f"export-{job.id}").start()
    return job


def get(job_id: str) -> ExportJob | None:
    with _lock:
        return _jobs.get(job_id)


def cleanup() -> None:
    if _dir is not None:
        shutil.rmtree(_dir, ignore_errors=True)


atexit.register(cleanup)
