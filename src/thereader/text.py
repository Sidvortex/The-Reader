"""Turning docstrings into the short summaries the page shows."""

from __future__ import annotations

import re

_UNDERLINE = re.compile(r"\s*([=\-~^*#+`'\".:_])\1{2,}\s*")  # reStructuredText title lines: ====, ~~~~
_FIELD = re.compile(r":[A-Za-z][\w ]*:")  # :copyright: (c) 2017 ..., :param x: ...


def doc_summary(doc: str | None, limit: int | None = None) -> str | None:
    """The first real paragraph of a docstring, on one line. Skips titles with their underlines, fields such as
    :copyright:, and stops before code examples."""
    if not doc or not doc.strip():
        return None
    for para in re.split(r"\n\s*\n", doc.strip()):
        raw = para.splitlines()
        lines: list[str] = []
        for i, line in enumerate(raw):
            s = line.strip()
            if not s or _UNDERLINE.fullmatch(line):
                continue
            if i + 1 < len(raw) and _UNDERLINE.fullmatch(raw[i + 1]):  # a title sitting on its underline
                continue
            if s.startswith(">>>") or s.endswith("::"):  # an example follows: the summary ends here
                break
            if _FIELD.match(s):
                continue
            lines.append(s)
        while lines and lines[-1].endswith(":"):  # "Basic usage:" with its example cut off
            lines.pop()
        if lines:
            text = " ".join(" ".join(lines).split())
            if limit and len(text) > limit:
                text = text[: limit - 1].rstrip() + "…"
            return text
    return None
