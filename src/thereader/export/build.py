"""Collect the layered explanation of a project, in reading order: the overview, then each folder with its files,
then each file's classes and functions. AI write-ups are used where they were already made (free); everything
else uses the explanation worked out from the code."""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from ..explainer import explain_made
from ..repo import summaries
from ..repo.summaries import code_files
from ..repo.index import TESTY, RepoIndex
from .md import Block, parse

DEPTHS = ("folders", "files", "functions")
MAX_SECTIONS = int(os.environ.get("THE_READER_EXPORT_MAX_SECTIONS", "5000"))


@dataclass
class Section:
    level: int  # 1: overview or folder, 2: file, 3: class or function
    title: str
    meta: str  # the small line under the title
    blocks: list[Block]
    ai: bool
    anchor: str
    mono: bool = True  # titles that are paths or names


@dataclass
class Document:
    title: str
    facts: list[tuple[str, str]]
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    filename: str = "explanation"


def _plural(n: int, word: str) -> str:
    return f"1 {word}" if n == 1 else f"{n:,} {word}{'es' if word.endswith(('s', 'sh', 'ch')) else 's'}"


def _anchor(n: int) -> str:
    return f"s{n}"


def _is_test(path: str) -> bool:
    return bool(TESTY.search(path))


def plan(idx: RepoIndex, scope: str, skip_tests: bool) -> tuple[list[str], dict[str, list[str]]]:
    """The folders to include, in reading order, and the files of code directly in each."""
    folders: list[str] = []
    files: dict[str, list[str]] = {}

    def walk(path: str) -> None:
        node = idx.dirs[path]
        if path != scope and (not node["stats"]["read_files"] or (skip_tests and _is_test(path + "/"))):
            return
        folders.append(path)
        files[path] = [f for f in node["files"] if f in idx.code and not (skip_tests and _is_test(f) and f != scope)]
        for d in node["dirs"]:
            walk(d)

    walk(scope)
    return folders, files


def build(idx: RepoIndex, scope: str = "", depth: str = "functions", skip_tests: bool = True,
          progress: Callable[[int, int], None] | None = None, max_sections: int | None = None) -> Document:
    if scope not in idx.dirs:
        raise KeyError(scope)
    depth = depth if depth in DEPTHS else "functions"
    limit = max_sections or MAX_SECTIONS
    folders, files = plan(idx, scope, skip_tests)
    total_files = sum(len(v) for v in files.values()) if depth != "folders" else 0
    report = progress or (lambda done, total: None)

    project = f"{idx.source['owner']}/{idx.source['repo']}" if idx.source else idx.name
    doc = Document(title=project, facts=[])
    doc.filename = re.sub(r"[^\w.-]+", "-", project if not scope else f"{project}-{scope}").strip("-") or "explanation"

    def add(level: int, title: str, meta: str, ex: dict, mono: bool = True) -> bool:
        if len(doc.sections) >= limit:
            return False
        doc.sections.append(Section(level, title, meta, parse(ex["markdown"]), ex["source"] == "ai",
                                    _anchor(len(doc.sections)), mono))
        return True

    # the top: the project overview, or the chosen folder
    if scope:
        st = idx.dirs[scope]["stats"]
        add(1, f"{scope}/", f"{code_files(idx, st['read_files'])}, {st['lines']:,} lines of code",
            summaries.made_folder(idx, scope))
    else:
        st = idx.dirs[""]["stats"]
        add(1, "Overview", f"{_plural(st['files'], 'file')}, {code_files(idx, st['read_files'])}",
            summaries.made_overview(idx), mono=False)

    done = 0
    stopped = False
    for folder in folders:
        if stopped:
            break
        if folder != scope:
            st = idx.dirs[folder]["stats"]
            if not add(1, f"{folder}/", f"{code_files(idx, st['read_files'])}, {st['lines']:,} lines of code",
                       summaries.made_folder(idx, folder)):
                stopped = True
                break
        elif not scope and files[folder] and depth != "folders":
            doc.sections.append(Section(1, "Files at the top level", "Files of code directly in the project folder",
                                        [], False, _anchor(len(doc.sections)), mono=False))
        if depth == "folders":
            continue
        for f in files[folder]:
            try:
                a = idx.file_view(f)
            except (KeyError, OSError, UnicodeError):
                done += 1
                continue
            context = summaries.file_context(idx, a, "<file>")
            meta = f"{_plural(a['file']['lines'], 'line')}"
            used_by = len(a["repo"]["used_by"])
            if used_by:
                meta += f", used by {_plural(used_by, 'file')}"
            if not add(2, f, meta, explain_made(a, "<file>", context)):
                stopped = True
                break
            if depth == "functions":
                for s in a["symbols"]:
                    if s["kind"] not in ("function", "method", "class"):  # CSS rules and page elements stay in
                        continue                                          # the file's summary
                    kind = {"method": "method", "class": "class"}.get(s["kind"], "function")
                    if s.get("component"):
                        kind = "component"
                    elif "interface" in s["decorators"]:
                        kind = "interface"
                    lines = f"line {s['start_line']}" if s["start_line"] == s["end_line"] \
                        else f"lines {s['start_line']}–{s['end_line']}"
                    title = s["qualname"] + ("" if s["kind"] == "class" else "()")
                    ctx = summaries.file_context(idx, a, s["id"])
                    if not add(3, title, f"{kind} in {f.rsplit('/', 1)[-1]}, {lines}", explain_made(a, s["id"], ctx)):
                        stopped = True
                        break
            done += 1
            report(done, total_files)
            if stopped:
                break

    if stopped:
        doc.notes.append(f"This document stops after {limit:,} sections so it stays a readable size. "
                         "Export a single folder to get the rest of it.")

    # what the document covers, for the title page
    if idx.source:
        doc.facts.append(("Source", idx.source["url"]))
        doc.facts.append(("Version", (idx.source.get("ref") + ", " if idx.source.get("ref") else "")
                          + f"commit {idx.source['commit'][:7]}"))
    else:
        doc.facts.append(("Folder", str(idx.root)))
        if idx.git and idx.git.get("commit"):
            doc.facts.append(("Version", (f"{idx.git['branch']}, " if idx.git.get("branch") else "")
                              + f"commit {idx.git['commit']}"))
    reach = {"folders": "folders", "files": "folders and files", "functions": "folders, files, classes and functions"}
    doc.facts.append(("Covers", ("the whole project" if not scope else f"the folder {scope}/") + f": {reach[depth]}"
                      + (", without tests and examples" if skip_tests else "")))
    n_ai = sum(1 for s in doc.sections if s.ai)
    n_all = sum(1 for s in doc.sections if s.blocks)
    if n_ai:
        doc.facts.append(("Written by", f"AI for {n_ai:,} of {n_all:,} sections; the rest are worked out from the code"))
    else:
        doc.facts.append(("Written by", "worked out from the code"))
    doc.facts.append(("Made", datetime.date.today().strftime("%d %B %Y").lstrip("0")))
    doc.notes += idx.notes
    return doc
