"""Find the files in a project folder: what to read, what to skip, and what language each one is."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Folders that hold tools, caches, installs or build output rather than the project's own code.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "site-packages",
    ".eggs", "htmlcov", ".next", ".nuxt", ".cache", ".gradle",
}
# Skipped only when walking without git (with git, a tracked build/ folder is real source).
SKIP_DIRS_NO_GIT = {"dist", "build", "target", "out"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock",
             "Pipfile.lock", "composer.lock", "Gemfile.lock", "bun.lockb"}

MAX_LIST = 40_000          # files listed at most
MAX_TEXT_BYTES = 1_500_000  # bigger files are listed but not read

LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".pyw": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS", ".less": "Less",
    ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".json": "JSON", ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML", ".ini": "INI", ".cfg": "INI",
    ".xml": "XML", ".csv": "CSV",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C", ".dart": "Dart",
    ".lua": "Lua", ".r": "R", ".jl": "Julia", ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell", ".ipynb": "Jupyter", ".proto": "Protobuf", ".graphql": "GraphQL",
}
NAMED = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "CMakeLists.txt": "CMake", "Justfile": "Just"}
# Not counted when describing what a project is written in.
NOT_CODE = {"Markdown", "reStructuredText", "Text", "JSON", "YAML", "TOML", "INI", "XML", "CSV"}


@dataclass
class FileEntry:
    path: str        # posix path relative to the project root
    language: str | None
    size: int
    lines: int = 0
    is_lock: bool = False
    binary: bool = False


def language_of(name: str) -> str | None:
    if name in NAMED:
        return NAMED[name]
    return LANGUAGES.get(PurePosixPath(name).suffix.lower())


def _git_files(root: Path) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    files = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    return files or None


def _walk_files(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in dirnames:
            if d.startswith(".") or d in SKIP_DIRS or d in SKIP_DIRS_NO_GIT or d.endswith(".egg-info"):
                continue
            if (here / d / "pyvenv.cfg").exists():  # a virtualenv with an unusual name
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for f in sorted(filenames):
            out.append((here / f).relative_to(root).as_posix())
            if len(out) >= MAX_LIST:
                return out
    return out


def _skipped(path: str) -> bool:
    parts = path.split("/")
    for d in parts[:-1]:
        if d.startswith(".") or d in SKIP_DIRS or d.endswith(".egg-info"):
            return True
    return parts[-1].startswith(".") and parts[-1] not in (".gitignore",)


def list_files(root: Path) -> tuple[list[FileEntry], bool]:
    """All project files (relative posix paths), plus whether git decided what counts as the project."""
    names = _git_files(root)
    used_git = names is not None
    if names is None:
        names = _walk_files(root)
    entries: list[FileEntry] = []
    for rel in sorted(set(names)):
        if _skipped(rel):
            continue
        full = root / rel
        try:
            if full.is_symlink() or not full.is_file():
                continue
            size = full.stat().st_size
        except OSError:
            continue
        name = rel.rsplit("/", 1)[-1]
        entries.append(FileEntry(rel, language_of(name), size, is_lock=name in LOCKFILES))
        if len(entries) >= MAX_LIST:
            break
    return entries, used_git


def read_text(root: Path, entry: FileEntry) -> bytes | None:
    """File bytes if it's a reasonably sized text file; also fills in its line count."""
    if entry.size > MAX_TEXT_BYTES:
        return None
    try:
        data = (root / entry.path).read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        entry.binary = True
        return None
    entry.lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return data
