"""Runs in helper processes when a repo has many files that haven't been analyzed before."""

from __future__ import annotations


def analyze_for_index(job: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    path, source = job
    try:
        from ..analyzer import analyze_quick

        return path, analyze_quick(path, source), None
    except Exception as e:  # one bad file shouldn't stop the whole repo
        return path, None, f"{type(e).__name__}: {e}"
