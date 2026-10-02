"""Independent multi-point calibration for the digital pulse sensors.

Implements the calibration model required by ``digitalsensor1.txt`` for SPM 1-4
(Strokes Per Minute) and RPM (Revolutions Per Minute):

* **Per-channel independence** (§5/§6/§12). Each channel owns one record;
  saving SPM 1 rewrites ONLY index 0 of ``spm_calibration``. SPM and RPM keep
  separate records, separate units and separate code paths - they are never
  merged into one calibration system.
* **Multi-point interpolation** (§8/§9). The Engineer enters any number of
  ``(reference, actual)`` pairs. Values between two points are linearly
  interpolated (never snapped to the nearest point) at full float precision;
  rounding happens only at display time. Outside the calibrated span the
  nearest segment is extrapolated, because a rate below the first reference is
  still a real reading - the result is floored at 0.0 since a rate cannot be
  negative.
* **Validation** (§13). Missing points, non-numeric input, negatives, duplicate
  references, impossible configuration values and bad pulses-per-revolution are
  all rejected with a message that names what must be corrected.
* **Raw / calculated / calibrated separation** (§7/§11).

This module is the SINGLE owner of the calibration data and the single
calculation path: ``main.py`` applies it once, in ``_apply_legacy`` (the one
place both the live stream and Demo Mode pass through), so the Dashboard, the
Digital Sensor tab and Total SPM all read the same calibrated numbers and no
second calculation ever exists.
"""
import datetime

from scarlet_test_panel.config import SPM_CONFIG, RPM_CONFIG
from scarlet_test_panel.services.settings import load as load_settings
from scarlet_test_panel.services.settings import save as save_settings

# A calibration needs a span, not a single point. The UI shows four rows (the
# layout in §3/§6/§20) and can grow to MAX_POINTS; §8's six-point example fits.
MIN_POINTS = 2
MAX_POINTS = 12
DEFAULT_POINTS = 4

# Calibration status vocabulary (§14) — two states only.
CAL_VALID = "VALID"
CAL_REQUIRED = "REQUIRED"

# storage keys — one independent list per measurement kind
KEYS = {"SPM": "spm_calibration", "RPM": "rpm_calibration"}


def count(kind):
    return len(SPM_CONFIG) if kind == "SPM" else len(RPM_CONFIG)


def unit(kind):
    """SPM = strokes/min, RPM = revolutions/min (§Definitions)."""
    return kind


def measurement(kind):
    return ("Strokes Per Minute" if kind == "SPM"
            else "Revolutions Per Minute")


def config_for(kind, idx):
    """The EXISTING firmware channel descriptor (single source of truth)."""
    src = SPM_CONFIG if kind == "SPM" else RPM_CONFIG
    return src[idx] if 0 <= idx < len(src) else {}


def name_for(kind, idx):
    if kind == "RPM":
        # ONE RPM calibration page (§6) that covers BOTH existing RPM firmware
        # channels, with the active one selected by the "Input Channel" field.
        # The channels stay independent on purpose (§5/§6): each has its own
        # record and its own Pulses / Revolution value.
        return "RPM"
    return config_for(kind, idx).get("name", f"SPM {idx + 1}")


def channel_label(kind, idx):
    """The per-channel label, e.g. 'SPM 1' or 'RPM 2'.

    `name_for` deliberately returns the generic page name 'RPM' (§6: ONE RPM
    calibration page), so the UI must never use it to tell the two RPM channels
    apart - this function is the unambiguous one."""
    cfg = config_for(kind, idx)
    return cfg.get("name", f"{kind} {idx + 1}")


def channel_number(kind, idx):
    """Firmware counter channel this record belongs to (counter1..counter6)."""
    cfg = config_for(kind, idx)
    key = cfg.get("counter_key", "")
    digits = "".join(ch for ch in key if ch.isdigit())
    return int(digits) if digits else idx + 1


def counter_key(kind, idx):
    return config_for(kind, idx).get("counter_key", "")


def value_key(kind, idx):
    cfg = config_for(kind, idx)
    return cfg.get("spm_key") if kind == "SPM" else cfg.get("rpm_key")


def blank_record(kind, idx):
    """An uncalibrated factory record. pulses_per_rev defaults to 1.00 and is
    NOT assumed from hardware: the firmware configuration does not carry one
    (§7), so 1.00 keeps the existing device calculation untouched until an
    Engineer sets a real value."""
    return {
        "channel": channel_number(kind, idx),
        "pulses_per_rev": 1.0,
        "points": [],
        "saved_at": "",
    }


# ----------------------------------------------------------------------
# normalisation
# ----------------------------------------------------------------------
def _clean_points(raw):
    """Coerce stored points into [(ref, actual), ...] and sort by reference.
    Malformed rows are dropped rather than crashing a load."""
    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for row in raw:
        try:
            ref, act = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        out.append((ref, act))
    out.sort(key=lambda p: p[0])
    return out


def normalize(record):
    """Return a well-formed copy of a record (never raises)."""
    base = blank_record("SPM", 0)
    rec = dict(base)
    if isinstance(record, dict):
        try:
            rec["channel"] = int(record.get("channel", rec["channel"]))
        except (TypeError, ValueError):
            pass
        try:
            rec["pulses_per_rev"] = float(record.get("pulses_per_rev", 1.0))
        except (TypeError, ValueError):
            rec["pulses_per_rev"] = 1.0
        rec["points"] = _clean_points(record.get("points"))
        rec["saved_at"] = str(record.get("saved_at", "") or "")
    return rec


# ----------------------------------------------------------------------
# persistence — per channel, never whole-list clobbering
# ----------------------------------------------------------------------
def load_all():
    """{kind: [record|None, ...]} for every digital channel."""
    settings = load_settings()
    out = {}
    for kind, key in KEYS.items():
        stored = settings.get(key)
        records = []
        for idx in range(count(kind)):
            entry = stored[idx] if isinstance(stored, list) and idx < len(stored) else None
            if isinstance(entry, dict) and entry.get("points"):
                records.append(normalize(entry))
            else:
                records.append(None)
        out[kind] = records
    return out


def record_for(kind, idx, all_records=None):
    """The active record for one channel, or None when not calibrated."""
    records = all_records if all_records is not None else load_all()
    rec = records.get(kind) if isinstance(records, dict) else None
    if not rec or not (0 <= idx < len(rec)):
        return None
    return rec[idx]


def save_channel(kind, idx, record):
    """Persist ONE channel, leaving every other channel untouched (§5).

    Returns the record actually stored (normalised) or None when the channel
    was cleared (record None)."""
    if kind not in KEYS or not (0 <= idx < count(kind)):
        return None
    rec = normalize(record)
    rec["channel"] = channel_number(kind, idx)
    current = load_settings().get(KEYS[kind])
    if not isinstance(current, list) or len(current) != count(kind):
        current = [None] * count(kind)
    else:
        current = [normalize(e) if isinstance(e, dict) and e.get("points")
                   else None for e in current]
    current[idx] = rec if rec["points"] else None
    save_settings({KEYS[kind]: current})
    return current[idx]


# ----------------------------------------------------------------------
# validation (§13)
# ----------------------------------------------------------------------
def validate(kind, record):
    """Return (ok, reason). Names the exact field to correct."""
    if record is None:
        return False, ("No calibration saved for this sensor. Enter at least "
                       f"{MIN_POINTS} reference points.")
    rec = record if isinstance(record, dict) else {}
    points = _clean_points(rec.get("points"))
    if not points:
        return False, "Calibration points are missing."
    if len(points) < MIN_POINTS:
        return False, (f"At least {MIN_POINTS} calibration points are required "
                       f"({len(points)} entered).")
    if len(points) > MAX_POINTS:
        return False, f"At most {MAX_POINTS} calibration points are supported."

    for ref, act in points:
        if ref < 0.0:
            return False, ("Reference Pulse Rate cannot be negative "
                           f"(found {ref:g}).")
        if act < 0.0:
            return False, (f"Reference Value cannot be negative for "
                           f"{unit(kind)} (found {act:g}).")
    for i in range(1, len(points)):
        if abs(points[i][0] - points[i - 1][0]) < 1e-9:
            return False, (f"Duplicate Reference Pulse Rate {points[i][0]:g} — "
                           "every point must use a different reference.")
    if abs(points[-1][0] - points[0][0]) < 1e-9:
        return False, "All reference values are identical — the curve has no span."

    if kind == "RPM":
        try:
            ppr = float(rec.get("pulses_per_rev", 1.0))
        except (TypeError, ValueError):
            return False, "Pulses / Revolution must be a number."
        if not (ppr > 0.0):
            return False, ("Pulses / Revolution must be greater than zero "
                           "(it cannot be 0 or negative).")
        if ppr > 10000.0:
            return False, "Pulses / Revolution is impossibly large."
    return True, "Valid"


# ----------------------------------------------------------------------
# the single calculation path (§7/§9/§11)
# ----------------------------------------------------------------------
def interpolate(points, reference):
    """Linear interpolation through the calibration curve (§9).

    Full float precision is preserved internally — no rounding, and never a
    snap to the nearest calibration point."""
    pts = _clean_points(points)
    if not pts:
        return None
    if len(pts) == 1:
        return float(pts[0][1])
    x = float(reference)
    if x <= pts[0][0]:
        lo, hi = pts[0], pts[1]
    elif x >= pts[-1][0]:
        lo, hi = pts[-2], pts[-1]
    else:
        lo = pts[0]
        hi = pts[1]
        for i in range(1, len(pts)):
            if pts[i][0] >= x:
                lo, hi = pts[i - 1], pts[i]
                break
    x0, y0 = lo
    x1, y1 = hi
    if abs(x1 - x0) < 1e-12:
        return float(y0)
    val = y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return max(0.0, float(val))


def pulse_rate_hz(kind, record, measured):
    """RAW / MEASURED signal in pulses per second (§11).

    The device reports the per-minute rate it derived from its own pulse
    counting, so the raw pulse train is recovered as
    ``pulses_per_minute / 60 x pulses_per_revolution``. Returned as None when
    there is no honest measurement to show."""
    try:
        value = float(measured)
    except (TypeError, ValueError):
        return None
    if value < 0.0:
        return None
    ppr = 1.0
    if isinstance(record, dict):
        try:
            ppr = float(record.get("pulses_per_rev", 1.0))
        except (TypeError, ValueError):
            ppr = 1.0
    if not (ppr > 0.0):
        ppr = 1.0
    return value / 60.0 * ppr


def calculated_value(kind, record, measured):
    """CALCULATED value in the channel's own unit (§7).

    For RPM the configured Pulses / Revolution is applied exactly as the spec
    requires: pulses -> revolutions is a division by the configured pulses per
    revolution, so a device that assumed a different pulse count is corrected
    here. With the factory 1.00 this is the identity and the existing device
    value passes through unchanged. SPM is already strokes per minute, so its
    calculation path carries no pulse divider at all (SPM is never treated as
    RPM)."""
    try:
        value = float(measured)
    except (TypeError, ValueError):
        return None
    if value < 0.0:
        return None
    if kind != "RPM":
        return value
    ppr = 1.0
    if isinstance(record, dict):
        try:
            ppr = float(record.get("pulses_per_rev", 1.0))
        except (TypeError, ValueError):
            ppr = 1.0
    if not (ppr > 0.0):
        ppr = 1.0
    return value / ppr


def calibrated_value(kind, record, measured):
    """CALIBRATED value — the number the Dashboard and Digital Sensor tab show.

    An uncalibrated channel returns the calculated value untouched, so adding
    the calibration system can never change existing readings until an Engineer
    actually saves points for that channel."""
    calc = calculated_value(kind, record, measured)
    if calc is None:
        return None
    if not isinstance(record, dict):
        return calc
    points = _clean_points(record.get("points"))
    if len(points) < MIN_POINTS:
        return calc
    out = interpolate(points, calc)
    return calc if out is None else out


def cal_status(kind, idx, record=None):
    """Per-sensor calibration status for §14 (VALID / REQUIRED).

    A channel with no saved curve is REQUIRED — that is exactly the §14 example
    ("SPM 2 → Calibration: REQUIRED")."""
    rec = record if record is not None else record_for(kind, idx)
    if rec is None:
        return CAL_REQUIRED
    ok, _reason = validate(kind, rec)
    return CAL_VALID if ok else CAL_REQUIRED


def is_calibrated(kind, idx, record=None):
    return cal_status(kind, idx, record) == CAL_VALID


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def summary(kind, idx, record=None):
    """One-line human summary used by the Digital Sensor tab and the dialog."""
    rec = record if record is not None else record_for(kind, idx)
    name = name_for(kind, idx)
    if rec is None:
        return f"{name} — Calibration: {CAL_REQUIRED} (no points saved)"
    ok, reason = validate(kind, rec)
    n = len(_clean_points(rec.get("points")))
    if not ok:
        return f"{name} — Calibration: {CAL_REQUIRED} ({reason})"
    extra = ""
    if kind == "RPM":
        extra = f", {float(rec.get('pulses_per_rev', 1.0)):g} pulses/rev"
    saved = rec.get("saved_at") or "—"
    return (f"{name} — Calibration: {CAL_VALID} ({n} points{extra}), "
            f"saved {saved}")