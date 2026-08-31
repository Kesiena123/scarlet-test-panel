"""Continuous local data historian (depth/time) persisted to SQLite.

Survives dashboard restarts and can be reviewed later. Retention: rows older
than a configurable number of days are purged (default 30).
"""
import os
import datetime
import sqlite3
import threading

__all__ = ["Historian"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS depth_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ft REAL NOT NULL,
    raw_count INTEGER NOT NULL,
    source TEXT,
    is_recovered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_depth_ts ON depth_samples(ts);
"""


class Historian:
    def __init__(self, path, retention_days=30):
        self._lock = threading.Lock()
        self._path = path
        self._retention_days = retention_days
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record(self, ft, raw_count, source="", is_recovered=False):
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO depth_samples(ts,ft,raw_count,source,is_recovered) "
                    "VALUES(?,?,?,?,?)",
                    (datetime.datetime.now().isoformat(), float(ft),
                     int(raw_count), source, 1 if is_recovered else 0))
                self._conn.commit()
        except sqlite3.Error:
            print("[historian] write failed")

    def write(self, ft, raw_count, source="", is_recovered=False):
        self.record(ft, raw_count, source, is_recovered)

    def recent(self, limit=2000):
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts,ft,raw_count,source,is_recovered FROM depth_samples "
                "ORDER BY id DESC LIMIT ?", (limit,))
            return list(reversed(cur.fetchall()))

    def since(self, since_iso):
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts,ft,raw_count,source,is_recovered FROM depth_samples "
                "WHERE ts >= ? ORDER BY id", (since_iso,))
            return cur.fetchall()

    def purge(self, retention_days=None):
        """Delete rows older than the retention window. Returns rows removed."""
        days = retention_days if retention_days is not None else self._retention_days
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM depth_samples WHERE ts < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def rowcount(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM depth_samples").fetchone()[0]
