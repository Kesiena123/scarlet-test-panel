"""Persistent dashboard settings (last-used serial port/baud, role, geometry).

Stored as a small JSON file alongside the SQLite database in PANEL_DATA_DIR.
Designed to be cheap and crash-safe (atomic write), with defaults when the
file is missing or corrupt.
"""

import os
import json
import tempfile

from scarlet_test_panel.config import PANEL_DATA_DIR
from scarlet_test_panel.services.security import ROLE_OPERATOR, ROLE_SUPERVISOR

SETTINGS_PATH = os.path.join(PANEL_DATA_DIR, "settings.json")

DEFAULTS = {
    "port": "",
    "baud": "9600",
    "role": ROLE_OPERATOR,
    "theme": "light",            # "light" | "dark"
    "geometry": None,            # [x, y, w, h]
    "maximized": False,
    # Operator-defined calibration model (Block Position Monitor, promt1.txt).
    # These are dashboard fallbacks; the authoritative values always come from
    # the firmware (STATUS / done-ack echo). Kept in sync with config.py and
    # encoder/encoder/config.h DEFAULT_*.
    "layer_positions": [0.0, 0.0, 0.0, 0.0],   # 4 calibration positions (ft) fallback
    "layer_names": ["", "", "", ""],           # optional operator labels per layer
    "layer_refs": [],                          # legacy key kept so old files normalize
    "op_mode": "RUN",                          # dashboard-side RUN / CALIBRATE state
    # Persisted ACTIVE calibration table (single source of truth for the
    # position calculation across restart/reload). Keys mirror the device
    # confirmation keys: calCounter1..4 / calPosition1..4, per-interval
    # countsPerFoot1..4, witsCorrectionFt, encoderPolarity, confirmed.
    "calibration": None,
}


def _read_raw():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load():
    """Load settings merged over defaults. Never raises; returns a dict."""
    raw = _read_raw()
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in raw.items() if k in DEFAULTS})
    if merged["role"] not in (ROLE_OPERATOR, ROLE_SUPERVISOR):
        merged["role"] = ROLE_OPERATOR
    if str(merged["baud"]) not in ("9600", "19200", "38400", "57600", "115200"):
        merged["baud"] = "9600"
    if merged["theme"] not in ("light", "dark"):
        merged["theme"] = "light"
    if not isinstance(merged.get("geometry"), list) or len(merged["geometry"]) != 4:
        merged["geometry"] = None
    return merged


def save(overrides):
    """Merge runtime dict over current file state and write atomically."""
    data = _read_raw()
    data.update({k: v for k, v in overrides.items() if v is not None})
    try:
        os.makedirs(PANEL_DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=PANEL_DATA_DIR, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        pass
