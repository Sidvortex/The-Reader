"""Open repos in the background so the page can show progress while a big project is read."""

from __future__ import annotations

import os
import threading
import traceback
from pathlib import Path

from . import github
from .cache import AnalysisCache
from .index import Progress, RepoIndex, repo_id

MAX_OPEN = int(os.environ.get("THE_READER_MAX_OPEN", "4"))  # projects kept in memory
# projects read at the same time; more wait their turn (reading is heavy on memory and CPU)
_slots = threading.BoundedSemaphore(int(os.environ.get("THE_READER_MAX_BUSY", "2")))

_lock = threading.Lock()
_jobs: dict[str, "RepoJob"] = {}
_cache: AnalysisCache | None = None


def _shared_cache() -> AnalysisCache:
    global _cache
    if _cache is None:
        _cache = AnalysisCache()
    return _cache


class RepoJob:
    def __init__(self, rid: str, name: str, root: Path | None = None, target: github.Target | None = None):
        self.id = rid
        self.name = name
        self.root = root  # for a GitHub link, known once the download is done
        self.target = target
        self.source = None if target is None else {
            "kind": "github", "owner": target.link.owner, "repo": target.link.repo, "ref": target.ref,
            "commit": target.commit, "url": target.link.url,
        }
        self.state = "scanning"  # scanning | ready | error
        self.error: str | None = None
        self.progress = Progress()
        self.index: RepoIndex | None = None
        self._thread = threading.Thread(target=self._run, name=f"index-{rid[:40]}", daemon=True)

    def _run(self) -> None:
        self.progress.set("Waiting for another project to finish")
        try:
            with _slots:
                if self.target is not None:
                    self.progress.set("Downloading from GitHub")
                    self.root = github.fetch(self.target)
                    with _lock:
                        in_use = {j.root for j in _jobs.values() if j.root is not None}
                    github.prune(in_use | {self.root})
                self.index = RepoIndex(self.root, self.progress, _shared_cache(), rid=self.id, name=self.name,
                                       source=self.source)
            self.state = "ready"
        except github.LinkError as e:
            self.error = str(e)
            self.state = "error"
        except Exception as e:
            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
            self.state = "error"

    def status(self) -> dict:
        shown = self.source["url"] if self.source else str(self.root)
        return {"id": self.id, "state": self.state, "error": self.error, "name": self.name, "root": shown,
                "source": self.source, "phase": self.progress.phase, "done": self.progress.done,
                "total": self.progress.total}


def _register(job: RepoJob) -> None:
    """Add a job (with _lock held), forgetting the least recently used finished ones beyond MAX_OPEN."""
    _jobs[job.id] = job
    while len(_jobs) > MAX_OPEN:
        old = next((k for k, j in _jobs.items() if j.state != "scanning" and k != job.id), None)
        if old is None:
            break
        _jobs.pop(old)


def open_repo(path: str | Path) -> RepoJob:
    """Read (or read again) a folder on this computer."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")
    rid = repo_id(root)
    with _lock:
        job = _jobs.get(rid)
        if job and job.state == "scanning":
            return job
        job = RepoJob(rid, root.name or str(root), root=root)
        _register(job)
    job._thread.start()
    return job


def _github_job(target: github.Target) -> RepoJob:
    rid = f"gh~{target.link.owner}~{target.link.repo}~{target.commit}"
    with _lock:
        job = _jobs.get(rid)
        if job and job.state != "error":
            _jobs[rid] = _jobs.pop(rid)  # most recently used
            if target.ref and job.source and not job.source["ref"]:  # reopened from a shared link: now we know the branch
                job.source["ref"] = target.ref
                if job.index and job.index.git:
                    job.index.git["branch"] = target.ref
            return job
        job = RepoJob(rid, target.link.repo, target=target)
        _register(job)
    job._thread.start()
    return job


def open_github(text: str, allow_short: bool = False) -> tuple[RepoJob, str]:
    """Read a public GitHub repository from a link. Also returns the folder or file the link pointed at."""
    target = github.resolve(github.parse(text, allow_short))
    return _github_job(target), target.path


def get_job(rid: str) -> RepoJob | None:
    """The job for an id. A GitHub project that isn't open any more (say the server restarted) is read again,
    since its id says exactly which commit it was."""
    with _lock:
        job = _jobs.get(rid)
        if job is not None:
            _jobs[rid] = _jobs.pop(rid)
            return job
    m = github.ID.fullmatch(rid)
    if m is None:
        return None
    owner, repo, commit = m.groups()
    return _github_job(github.Target(github.Link(owner, repo), commit, None, ""))


def wait(job: RepoJob, timeout: float | None = None) -> RepoJob:
    job._thread.join(timeout)
    return job
