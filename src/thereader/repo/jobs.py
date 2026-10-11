"""Open repos in the background so the page can show progress while a big project is indexed."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from .cache import AnalysisCache
from .index import Progress, RepoIndex, repo_id

MAX_OPEN = 4

_lock = threading.Lock()
_jobs: dict[str, "RepoJob"] = {}
_cache: AnalysisCache | None = None


def _shared_cache() -> AnalysisCache:
    global _cache
    if _cache is None:
        _cache = AnalysisCache()
    return _cache


class RepoJob:
    def __init__(self, root: Path):
        self.root = root
        self.id = repo_id(root)
        self.state = "scanning"  # scanning | ready | error
        self.error: str | None = None
        self.progress = Progress()
        self.index: RepoIndex | None = None
        self._thread = threading.Thread(target=self._run, name=f"index-{self.id}", daemon=True)

    def _run(self) -> None:
        try:
            self.index = RepoIndex(self.root, self.progress, _shared_cache())
            self.state = "ready"
        except Exception as e:
            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
            self.state = "error"

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "name": self.root.name, "root": str(self.root),
                "phase": self.progress.phase, "done": self.progress.done, "total": self.progress.total}


def open_repo(path: str | Path) -> RepoJob:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")
    rid = repo_id(root)
    with _lock:
        job = _jobs.get(rid)
        if job and job.state == "scanning":
            return job
        job = RepoJob(root)
        _jobs[rid] = job
        while len(_jobs) > MAX_OPEN:
            _jobs.pop(next(iter(_jobs)))
    job._thread.start()
    return job


def get_job(rid: str) -> RepoJob | None:
    return _jobs.get(rid)


def wait(job: RepoJob, timeout: float | None = None) -> RepoJob:
    job._thread.join(timeout)
    return job
