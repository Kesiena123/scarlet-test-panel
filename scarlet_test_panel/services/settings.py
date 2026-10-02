"""Persistent dashboard settings (last-used serial port/baud, role, geometry).

Stored as a small JSON file alongside the SQLite database in PANEL_DATA_DIR.
Designed to be cheap and crash-safe (atomic write), with defaults when the
file is missing or corrupt.
"""

import os
import json
import tempfile

from scarlet_test_panel.config import (PANEL_DATA_DIR, NUM_ANALOG, SPM_CONFIG,
                                       RPM_CONFIG)
from scarlet_test_panel.services.security import ROLE_OPERATOR, ROLE_SUPERVISOR

SETTINGS_PATH = os.path.join(PANEL_DATA_DIR, "settings.json")

DEFAULTS = {
    # Settings-file format version (migrations in load()/save()).
    "_version": 4,
    "port": "",
    "baud": "115200",
    "role": ROLE_OPERATOR,
    "theme": "red",              # "light" | "dark" | "red"
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
    # Human-readable timestamp of the last confirmed CALIBRATE / SAVE ALL
    # LAYERS (promt2); shown in the saved-confirmation banner.
    "cal_last_saved": "",
    # String Length feature (promt.txt): Pipe in Hole (ft), user-defined.
    # None = not configured; float = saved value in feet.
    "pipe_in_hole": None,
    # SLIP WINDOW (seconds): how long the bit-position state must stay stable
    # before the TRACK/HOLD transition is applied (promt.txt §18 hysteresis).
    # None = built-in default (BIT_POS_STATE_STABLE_TICKS); float = seconds.
    "slip_window_sec": None,
    # SLIP WINDOW load/time confirmation (slip_window.txt): the Hookload
    # threshold (k-lb) and confirmation span (seconds). Bit Position movement
    # is enabled only after the load holds at/above Slip Window Load for the
    # whole Slip Window Time. None = feature off (existing behaviour unchanged).
    "slip_window_load_klb": None,
    "slip_window_time_sec": None,
    # Dashboard SAMPLE LAG DEPTH (promt3 §15) derived offset (feet): the lag
    # distance subtracted from Measured Depth. None = 0 ft (lag follows MD).
    "sample_lag_offset_ft": None,
    # Analog Monitor tab per-channel enable state. None = all channels active
    # (legacy files / fresh installs behave exactly as before).
    "analog_enabled": None,
    # Analog Monitor per-channel two-point calibration
    # [{lo_in, lo_val, hi_in, hi_val} | None, ...] — input voltage -> engineering
    # value points, stored independently per device/channel so calibration never
    # leaks across rows and survives restarts. None = factory (0-5 V, defaults).
    "analog_calibration": None,
    # Dashboard SPM display slots (spmselection.txt): which of the four
    # EXISTING SPM channels each SPM value box shows, one entry per box
    # (0-based channel index). None = the default mapping (box 1 -> SPM 1,
    # box 2 -> SPM 2, box 3 -> SPM 3, box 4 -> SPM 4), so legacy files and
    # fresh installs show exactly what they always did.
    "dashboard_spm_slots": None,
    # Which RPM channel the Dashboard RPM box displays (0-based). None = the
    # default (RPM 1); it only changes what is displayed, never the acquisition.
    "dashboard_rpm_slot": None,
    # Digital pulse sensor calibration (digitalsensor1.txt). TWO independent
    # lists — SPM and RPM are different measurements and never share a
    # calibration system. One entry per channel; a None entry = that channel
    # has no saved calibration (its value passes through untouched), so legacy
    # files and fresh installs behave exactly as before.
    #   {"channel": int, "pulses_per_rev": float,
    #    "points": [[reference, actual], ...], "saved_at": str}
    "spm_calibration": None,
    "rpm_calibration": None,
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
    version = int(raw.get("_version", 1) or 1)
    merged["_version"] = max(version, DEFAULTS["_version"])
    if merged["role"] not in (ROLE_OPERATOR, ROLE_SUPERVISOR):
        merged["role"] = ROLE_OPERATOR
    if str(merged["baud"]) not in ("9600", "19200", "38400", "57600", "115200"):
        merged["baud"] = "115200"
    if merged["theme"] not in ("light", "dark", "red"):
        merged["theme"] = "red"
    if not isinstance(merged.get("geometry"), list) or len(merged["geometry"]) != 4:
        merged["geometry"] = None
    # Normalize the Analog Monitor per-channel enable list: any absent / wrong
    # length value enables every channel so legacy files behave like before.
    ae = merged.get("analog_enabled")
    if not isinstance(ae, list) or len(ae) != NUM_ANALOG:
        merged["analog_enabled"] = [True] * NUM_ANALOG
    else:
        merged["analog_enabled"] = [bool(x) for x in ae]
    # Normalize per-channel two-point calibration: any malformed / missing entry
    # stays None (factory), so corrupted files never crash the load.
    ac = merged.get("analog_calibration")
    if not isinstance(ac, list):
        merged["analog_calibration"] = None
    else:
        out = []
        for i in range(NUM_ANALOG):
            e = ac[i] if i < len(ac) else None
            if isinstance(e, dict):
                try:
                    out.append({k: float(e[k])
                                for k in ("lo_in", "lo_val", "hi_in", "hi_val")})
                except (KeyError, TypeError, ValueError):
                    out.append(None)
            else:
                out.append(None)
        merged["analog_calibration"] = out
    # Normalize the Dashboard SPM slot selection: anything absent, malformed or
    # out of range falls back to the default mapping (box i -> SPM i+1).
    slots = merged.get("dashboard_spm_slots")
    if not isinstance(slots, list):
        merged["dashboard_spm_slots"] = None
    else:
        out = []
        for i in range(len(SPM_CONFIG)):
            e = slots[i] if i < len(slots) else None
            if isinstance(e, bool) or not isinstance(e, int):
                e = i
            out.append(e if 0 <= e < len(SPM_CONFIG) else i)
        merged["dashboard_spm_slots"] = out
    # Normalize the Dashboard RPM slot the same way: absent, malformed or out of
    # range falls back to RPM 1.
    rpm_slot = merged.get("dashboard_rpm_slot")
    if isinstance(rpm_slot, bool) or not isinstance(rpm_slot, int) \
            or not 0 <= rpm_slot < len(RPM_CONFIG):
        merged["dashboard_rpm_slot"] = None
    # Normalize the digital pulse sensor calibrations (digitalsensor1.txt):
    # SPM and RPM are normalized SEPARATELY and independently. Any missing,
    # malformed, negative-pulses or empty entry becomes None = "not
    # calibrated", so a corrupted file can never crash a load or silently
    # apply a bogus curve.
    for kind, key, n in (("SPM", "spm_calibration", len(SPM_CONFIG)),
                         ("RPM", "rpm_calibration", len(RPM_CONFIG))):
        stored = merged.get(key)
        if not isinstance(stored, list):
            merged[key] = None
            continue
        out = []
        for i in range(n):
            entry = stored[i] if i < len(stored) else None
            if not isinstance(entry, dict):
                out.append(None)
                continue
            try:
                ppr = float(entry.get("pulses_per_rev", 1.0))
            except (TypeError, ValueError):
                ppr = 1.0
            if not (ppr > 0.0) or ppr > 10000.0:
                ppr = 1.0
            pts = []
            for row in (entry.get("points") or []):
                try:
                    ref, act = float(row[0]), float(row[1])
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                if ref < 0.0 or act < 0.0:
                    continue
                pts.append([ref, act])
            if len(pts) < 2:
                out.append(None)
                continue
            try:
                ch = int(entry.get("channel", i + 1))
            except (TypeError, ValueError):
                ch = i + 1
            out.append({
                "channel": ch,
                "pulses_per_rev": ppr,
                "points": pts,
                "saved_at": str(entry.get("saved_at", "") or ""),
            })
        merged[key] = out
    return merged


def save(overrides):
    """Merge runtime dict over current file state and write atomically."""
    data = _read_raw()
    data.update({k: v for k, v in overrides.items() if v is not None})
    data["_version"] = DEFAULTS["_version"]
    try:
        os.makedirs(PANEL_DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=PANEL_DATA_DIR, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        pass
