"""Read public GitHub repositories: understand a pasted link, fetch a shallow copy, keep a few on disk."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from .cache import cache_dir

HOST = "https://github.com"
MAX_DOWNLOAD = int(os.environ.get("THE_READER_MAX_DOWNLOAD_MB", "250")) * 1_000_000  # compressed, as fetched
MAX_CHECKOUT = int(os.environ.get("THE_READER_MAX_CHECKOUT_MB", "500")) * 1_000_000  # files written out
MAX_FILES = 80_000
KEEP = int(os.environ.get("THE_READER_KEEP_REPOS", "12"))  # copies kept on disk, most recently used first
TIMEOUT = 240  # seconds for one download

OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
NAME = r"[A-Za-z0-9._-]{1,100}"
SHA = re.compile(r"[0-9a-f]{40}")
# Repo ids for GitHub copies carry everything needed to fetch them again: gh~owner~repo~commit
ID = re.compile(rf"gh~({OWNER})~({NAME})~([0-9a-f]{{40}})")

# Never ask for a password: a private or missing repo should fail at once, not wait for an answer.
_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GCM_INTERACTIVE": "never",
        "GIT_LFS_SKIP_SMUDGE": "1", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
_NO_HELPERS = ["-c", "credential.helper=", "-c", "core.askPass="]


class LinkError(ValueError):
    """A link that can't be opened, with a message meant for the page."""


@dataclass
class Link:
    owner: str
    repo: str
    kind: str | None = None  # "tree" or "blob" when the link points inside the repo
    rest: str = ""  # what follows /tree/ or /blob/: "<branch or tag or commit>/<path>"

    @property
    def url(self) -> str:
        return f"{HOST}/{self.owner}/{self.repo}"


@dataclass
class Target:
    link: Link
    commit: str
    ref: str | None  # branch or tag name; None when the link named a commit
    path: str  # the file or folder the link pointed at, "" for the whole repo


def looks_like_github(text: str) -> bool:
    t = text.strip()
    return bool(re.match(r"(?:https?://)?(?:www\.)?github\.com/", t, re.I)) or t.startswith("git@github.com:")


def parse(text: str, allow_short: bool = False) -> Link:
    """github.com/owner/repo in any of its usual forms, optionally pointing at a branch, folder or file.
    With `allow_short`, a bare owner/repo works too."""
    t = re.sub(r"[?#].*$", "", text.strip())
    m = re.match(r"(?:git@github\.com:|(?:https?://)?(?:www\.)?github\.com/)(.*)$", t, re.I)
    if m:
        t = m.group(1)
    elif not (allow_short and re.fullmatch(rf"{OWNER}/{NAME}/?", t)):
        raise LinkError("That isn't a GitHub link. Paste one like https://github.com/owner/repo")
    parts = [unquote(p) for p in t.split("/") if p]
    if len(parts) < 2:
        raise LinkError("That link doesn't name a repository. It should look like https://github.com/owner/repo")
    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not re.fullmatch(OWNER, owner) or not re.fullmatch(NAME, repo) or repo in (".", ".."):
        raise LinkError("That doesn't look like a GitHub repository link.")
    if len(parts) > 3 and parts[2] in ("tree", "blob"):
        return Link(owner, repo, parts[2], "/".join(parts[3:]))
    return Link(owner, repo)


def _git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *_NO_HELPERS, *args], capture_output=True, text=True, timeout=timeout, env=_ENV)
    except FileNotFoundError:
        raise LinkError("Reading GitHub links needs git, and git isn't installed here.") from None
    except subprocess.TimeoutExpired:
        raise LinkError("GitHub took too long to answer. Try again in a moment.") from None


def _git_error(stderr: str, link: Link) -> str:
    s = stderr.lower()
    if any(w in s for w in ("not found", "could not read username", "terminal prompts disabled",
                            "authentication failed", "access denied", "403")):
        return (f"Couldn't find github.com/{link.owner}/{link.repo}. Check the link; "
                "private repositories can't be read from a link.")
    if any(w in s for w in ("could not resolve host", "unable to access", "connection", "timed out")):
        return "Couldn't reach GitHub. Check the internet connection and try again."
    last = stderr.strip().splitlines()[-1] if stderr.strip() else "no details"
    return f"GitHub didn't answer as expected ({last[:160]})."


def _refs(link: Link, everything: bool) -> tuple[str | None, dict[str, str]]:
    """The default branch, and {ref: commit} for HEAD only, or for every branch and tag."""
    url = link.url + ".git"
    r = _git("ls-remote", "--symref", url, "HEAD", timeout=40) if not everything else \
        _git("ls-remote", "--heads", "--tags", url, timeout=60)
    if r.returncode != 0:
        raise LinkError(_git_error(r.stderr, link))
    head, refs = None, {}
    for line in r.stdout.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            head = line[5:].split("\t")[0].removeprefix("refs/heads/")
            continue
        sha, _, name = line.partition("\t")
        if name.endswith("^{}"):
            refs[name[:-3]] = sha  # an annotated tag: use the commit it points at
        else:
            refs.setdefault(name, sha)
    return head, refs


def resolve(link: Link) -> Target:
    """Which commit to read, and where inside it the link pointed."""
    if not link.kind:
        head, refs = _refs(link, everything=False)
        if "HEAD" not in refs:
            raise LinkError(f"github.com/{link.owner}/{link.repo} has no commits yet.")
        return Target(link, refs["HEAD"], head, "")
    parts = [p for p in link.rest.split("/") if p]
    if parts and SHA.fullmatch(parts[0].lower()):
        return Target(link, parts[0].lower(), None, "/".join(parts[1:]))
    _, refs = _refs(link, everything=True)
    names: dict[str, str] = {}
    for full, sha in refs.items():  # branches come before tags, so a branch wins a name clash
        for prefix in ("refs/heads/", "refs/tags/"):
            if full.startswith(prefix):
                names.setdefault(full[len(prefix):], sha)
    for i in range(len(parts), 0, -1):  # branch names can contain "/", so try the longest first
        ref = "/".join(parts[:i])
        if ref in names:
            return Target(link, names[ref], ref, "/".join(parts[i:]))
    raise LinkError(f"Couldn't find that branch or tag in github.com/{link.owner}/{link.repo}.")


# ---------------------------------------------------------------- copies on disk
def _root() -> Path:
    return cache_dir() / "github"


def _size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _download(target: Target, dest: Path) -> None:
    link = target.link
    if _git("init", "-q", str(dest)).returncode != 0:
        raise LinkError("Couldn't prepare a folder for the download.")
    # links stay plain text files, so nothing in a repo can point outside its own folder
    _git("-C", str(dest), "config", "core.symlinks", "false")
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            ["git", *_NO_HELPERS, "-C", str(dest), "fetch", "--quiet", "--depth", "1", "--no-tags",
             link.url + ".git", target.commit],
            stdout=subprocess.DEVNULL, stderr=err, env=_ENV)
        started, objects = time.monotonic(), dest / ".git" / "objects"
        while proc.poll() is None:
            if _size(objects) > MAX_DOWNLOAD:
                proc.kill()
                proc.wait()
                raise LinkError(f"github.com/{link.owner}/{link.repo} is too big to read here (over "
                                f"{MAX_DOWNLOAD // 1_000_000} MB to download). Run The Reader on your own computer for it.")
            if time.monotonic() - started > TIMEOUT:
                proc.kill()
                proc.wait()
                raise LinkError("Downloading from GitHub took too long. Try again later.")
            time.sleep(0.25)
        if proc.returncode != 0:
            err.seek(0)
            raise LinkError(_git_error(err.read().decode("utf-8", "replace"), link))
    listing = _git("-C", str(dest), "ls-tree", "-r", "-l", "--full-tree", "FETCH_HEAD", timeout=120)
    if listing.returncode != 0:
        raise LinkError("Couldn't list the files of that commit.")
    count, total = 0, 0
    for line in listing.stdout.splitlines():
        size = line.split("\t", 1)[0].split()[-1]
        count += 1
        total += int(size) if size.isdigit() else 0
    if count > MAX_FILES or total > MAX_CHECKOUT:
        raise LinkError(f"github.com/{link.owner}/{link.repo} is too big to read here ({count:,} files, "
                        f"{total / 1e6:,.0f} MB). Run The Reader on your own computer for it.")
    co = _git("-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", timeout=TIMEOUT)
    if co.returncode != 0:
        raise LinkError("Couldn't unpack the downloaded files.")


_locks: dict[Path, threading.Lock] = {}
_locks_lock = threading.Lock()


def fetch(target: Target) -> Path:
    """A folder holding the files of target.commit, downloaded once and reused after that."""
    link = target.link
    dest = _root() / link.owner.lower() / link.repo.lower() / target.commit
    with _locks_lock:
        lock = _locks.setdefault(dest, threading.Lock())
    with lock:
        if dest.is_dir():
            os.utime(dest)  # most recently used
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.parent / f".{target.commit}.part-{os.getpid()}-{threading.get_ident()}"
        shutil.rmtree(part, ignore_errors=True)
        try:
            _download(target, part)
            os.replace(part, dest)
        finally:
            shutil.rmtree(part, ignore_errors=True)
        return dest


def prune(protect: set[Path]) -> None:
    """Keep the KEEP most recently used copies (and any in use), and clear out failed downloads."""
    root = _root()
    if not root.is_dir():
        return
    copies, now = [], time.time()
    for p in root.glob("*/*/*"):
        try:
            if p.name.startswith("."):
                if now - p.stat().st_mtime > 3600:
                    shutil.rmtree(p, ignore_errors=True)
                continue
            if p.is_dir():
                copies.append((p.stat().st_mtime, p))
        except OSError:
            continue
    copies.sort(reverse=True)
    keep = {p.resolve() for p in protect}
    for _, p in copies[KEEP:]:
        if p.resolve() not in keep:
            shutil.rmtree(p, ignore_errors=True)
