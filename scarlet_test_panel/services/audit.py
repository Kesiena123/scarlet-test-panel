"""Audit log for compliance and incident reporting.

Records every fault, checksum failure, disconnect/reconnect, tare command,
and calibration change. Every row carries:
  * a host wall-clock timestamp (ts), so logs are human-readable, and
  * the DEVICE uptime (seconds since boot) at that moment, so a device
    reboot (which resets uptime) remains detectable in the log even though
    the Arduino has no real-time clock.

Storage: SQLite (robust, queryable). Export: CSV or PDF.
"""
import os
import csv
import datetime
import sqlite3
import threading

__all__ = ["AuditLog"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT,
    detail TEXT,
    uptime_s INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


def _migrate(conn):
    """Add the uptime_s column to pre-existing databases (no-op if present)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(audit)").fetchall()}
    if "uptime_s" not in cols:
        conn.execute("ALTER TABLE audit ADD COLUMN uptime_s INTEGER")


class AuditLog:
    def __init__(self, path, default_actor="system"):
        self._lock = threading.Lock()
        self._path = path
        self._default_actor = default_actor
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    # -- insertion -------------------------------------------------------
    def record(self, event, detail="", actor=None, uptime=None):
        actor = actor or self._default_actor
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO audit(ts,event,actor,detail,uptime_s) VALUES(?,?,?,?,?)",
                    (datetime.datetime.now().isoformat(), event, actor,
                     detail, uptime))
                self._conn.commit()
        except sqlite3.Error:
            # Never let audit failures crash the dashboard; log to console.
            print(f"[audit] failed to write {event}")

    def log(self, event, detail="", actor=None, uptime=None):
        self.record(event, detail, actor, uptime)

    # -- querying --------------------------------------------------------
    def recent(self, limit=200):
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts,event,actor,detail,uptime_s FROM audit ORDER BY id DESC LIMIT ?",
                (limit,))
            return cur.fetchall()

    # -- export ----------------------------------------------------------
    def export_csv(self, path):
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,event,actor,detail,uptime_s FROM audit ORDER BY id").fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "event", "actor", "detail", "device_uptime_s"])
            w.writerows(rows)
        return len(rows)

    def export_pdf(self, path):
        """Export the full audit log to a simple text-based PDF."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            raise RuntimeError(
                "reportlab is required for PDF export. Install with: pip install reportlab")

        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,event,actor,detail,uptime_s FROM audit ORDER BY id").fetchall()

        doc = SimpleDocTemplate(path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = [Paragraph("Audit Log", styles['Title'])]
        story.append(Paragraph(
            f"Generated {datetime.datetime.now().isoformat()} — {len(rows)} records",
            styles['Normal']))
        data = [["Timestamp", "Event", "Actor", "Detail", "Dev Uptime (s)"]]
        for r in rows:
            data.append([r[0], r[1], r[2] or "", r[3] or "", r[4] if r[4] is not None else "-"])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), "#dddddd"),
            ("GRID", (0, 0), (-1, -1), 0.5, "#888888"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
        doc.build(story)
        return len(rows)
