"""On-disk cache of per-file analyses, keyed by file content, so reopening a repo is fast."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib
from pathlib import Path

MAX_ENTRIES = 60_000


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "the-reader"
    base.mkdir(parents=True, exist_ok=True)
    return base


class AnalysisCache:
    def __init__(self, path: Path | None = None):
        self.lock = threading.Lock()
        try:
            self.db = sqlite3.connect(path or cache_dir() / "index.sqlite", check_same_thread=False)
            self.db.execute("CREATE TABLE IF NOT EXISTS idx (key TEXT PRIMARY KEY, data BLOB, used REAL)")
        except sqlite3.Error:
            self.db = None  # caching is an optimisation; work without it

    def get_many(self, keys: list[str]) -> dict[str, dict]:
        if not self.db or not keys:
            return {}
        out: dict[str, dict] = {}
        with self.lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i: i + 500]
                rows = self.db.execute(
                    f"SELECT key, data FROM idx WHERE key IN ({','.join('?' * len(chunk))})", chunk).fetchall()
                for k, data in rows:
                    try:
                        out[k] = json.loads(zlib.decompress(data))
                    except (zlib.error, ValueError):
                        pass
            now = time.time()
            self.db.executemany("UPDATE idx SET used=? WHERE key=?", [(now, k) for k in out])
            self.db.commit()
        return out

    def put_many(self, items: dict[str, dict]) -> None:
        if not self.db or not items:
            return
        now = time.time()
        rows = [(k, zlib.compress(json.dumps(v, separators=(",", ":")).encode(), 6), now) for k, v in items.items()]
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO idx VALUES (?, ?, ?)", rows)
            (count,) = self.db.execute("SELECT COUNT(*) FROM idx").fetchone()
            if count > MAX_ENTRIES:  # forget what hasn't been used for longest
                self.db.execute("DELETE FROM idx WHERE key IN (SELECT key FROM idx ORDER BY used LIMIT ?)",
                                (count - MAX_ENTRIES,))
            self.db.commit()
