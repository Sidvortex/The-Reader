"""Runs in helper processes when a repo has many uncached Python files."""

from __future__ import annotations


def analyze_for_index(job: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    path, source = job
    try:
        from ..analyzer.python_analyzer import analyze_python

        a = analyze_python(path.rsplit("/", 1)[-1], source, flowcharts=False)
        a.pop("source", None)
        a.pop("flowcharts", None)
        return path, a, None
    except Exception as e:  # one bad file shouldn't stop the whole repo
        return path, None, f"{type(e).__name__}: {e}"
