import datetime
from PyQt5.QtGui import QColor

NUM_ANALOG = 16

# The sixteen configured analog sensors (drilling rig instrument list, one per
# ADC channel 0..15). These names/units ARE the existing sensor configuration —
# every surface (Analog Signals gauges, calibration dialog, Graph, Analog
# Monitor table) reads them from here, so nothing is duplicated or hard-coded
# per tab. `key` is the firmware telemetry field (sensor1..sensor16).
SENSOR_CONFIG = [
    {"name": "Hookload",              "key": "sensor1",  "unit": "klb",
     "cal_min": 0.0, "cal_max": 1000.0, "enabled": True},
    {"name": "Pump Pressure",         "key": "sensor2",  "unit": "psi",
     "cal_min": 0.0, "cal_max": 1000.0, "enabled": True},
    {"name": "Return Flow Sensor 1",  "key": "sensor3",  "unit": "%",
     "cal_min": 0.0, "cal_max": 100.0,  "enabled": True},
    {"name": "RPM Top Drive",         "key": "sensor4",  "unit": "rpm",
     "cal_min": 0.0, "cal_max": 200.0,  "enabled": True},
    {"name": "Torque Top Drive",      "key": "sensor5",  "unit": "amp",
     "cal_min": 0.0, "cal_max": 1000.0, "enabled": True},
    {"name": "Pit 1 Volume",          "key": "sensor6",  "unit": "bbls",
     "cal_min": 0.0, "cal_max": 2000.0, "enabled": True},
    {"name": "Pit 2 Volume",          "key": "sensor7",  "unit": "bbls",
     "cal_min": 0.0, "cal_max": 2000.0, "enabled": True},
    {"name": "Total Gas Sensor 1",    "key": "sensor8",  "unit": "%",
     "cal_min": 0.0, "cal_max": 100.0,  "enabled": True},
    {"name": "Total Gas Sensor 2",    "key": "sensor9",  "unit": "%",
     "cal_min": 0.0, "cal_max": 100.0,  "enabled": True},
    {"name": "Pit 3 Volume",          "key": "sensor10", "unit": "bbls",
     "cal_min": 0.0, "cal_max": 2000.0, "enabled": True},
    {"name": "Pit 4 Volume",          "key": "sensor11", "unit": "bbls",
     "cal_min": 0.0, "cal_max": 2000.0, "enabled": True},
    {"name": "Trip Tank 1 Volume",    "key": "sensor12", "unit": "bbls",
     "cal_min": 0.0, "cal_max": 500.0,  "enabled": True},
    {"name": "Mud Temp In",           "key": "sensor13", "unit": "°F",
     "cal_min": 0.0, "cal_max": 250.0,  "enabled": True},
    {"name": "Mud Temp Out Sensor 1", "key": "sensor14", "unit": "°F",
     "cal_min": 0.0, "cal_max": 250.0,  "enabled": True},
    {"name": "Mud Density In",        "key": "sensor15", "unit": "ppg",
     "cal_min": 0.0, "cal_max": 25.0,   "enabled": True},
    {"name": "Mud Density Out",       "key": "sensor16", "unit": "ppg",
     "cal_min": 0.0, "cal_max": 25.0,   "enabled": True},
]

TWO_POINT_CAL = [
    {"v_min": None, "val_min": None, "v_max": None, "val_max": None}
    for _ in range(NUM_ANALOG)
]

# Factory (uncalibrated) engineering range per analog channel, captured once at
# import BEFORE any per-channel calibration is re-applied from settings. Used by
# the Analog Monitor's Sensor Calibration window to restore a single channel to
# its built-in defaults ("Reset Calibration", analog_monitor.txt §685-1373 §19).
_ANALOG_FACTORY = [(float(c["cal_min"]), float(c["cal_max"]))
                   for c in SENSOR_CONFIG]

SPM_CONFIG = [
    {"name": f"SPM {i+1}", "counter_key": f"counter{i+1}", "spm_key": f"spm{i+1}"}
    for i in range(4)
]

RPM_CONFIG = [
    {"name": "RPM 1", "counter_key": "counter5", "rpm_key": "rpm1"},
    {"name": "RPM 2", "counter_key": "counter6", "rpm_key": "rpm2"},
]

MS_PER_SAMPLE = 50
SAMPLES_PER_SEC = 1000 // MS_PER_SAMPLE
WINDOW_MINUTES = 60
WINDOW_SECS = WINDOW_MINUTES * 60
RETAIN_MINUTES = 10
RETAIN_SECS = RETAIN_MINUTES * 60
RETAIN_SAMPLES = RETAIN_SECS * SAMPLES_PER_SEC
HISTORY_LEN = WINDOW_SECS * SAMPLES_PER_SEC + 500

# ── Real-time trend (promt1.txt) ──────────────────────────────────
# Display-only conversion: internal trend data stays in FEET, METERS is
# computed only at chart render time (feet * FT_TO_M) so switching units
# never accumulates conversion errors.
FT_TO_M = 0.3048
# Controlled sampling: one trend point per second from a QTimer. The UI
# redraws at MS_PER_SAMPLE (20 Hz) but the trend buffer is NOT appended on
# every UI refresh — that is what keeps roling history both smooth and
# memory-bounded (TREND_MAX_POINTS ~ one hour at 1 Hz plus headroom).
TREND_SAMPLE_MS = 1000
TREND_MAX_POINTS = 2 * 3600
# LIVE toggle = the fast-following window (last N seconds) that always
# keeps the newest data visible.
TREND_LIVE_WINDOW_S = 30

# Colors — base LIGHT palette (the original scheme). Derived / custom-painted
# widgets (gauge, graph, cards, calibration dialog, tab bar, analog) read these
# fixed names directly and remain on the light-tuned scheme regardless of
# theme. The core operator surfaces (block tab, status bar, main chrome,
# settings/about) read design tokens from theme.current() instead and re-skin
# live when the user toggles Light/Dark.
try:
    from PyQt5.QtGui import QColor
except ImportError:  # pragma: no cover - only when Qt is unavailable at import
    class QColor:
        def __init__(self, *a):
            self._a = a
        def name(self):
            from PyQt5.QtGui import QColor as _R
            return _R(*self._a).name()

# ── Industrial red palette (professional Oil & Gas control HMI) ──────
# Colour hierarchy:
#   BG_MAIN   dark industrial red  — window/structure, major areas
#   BG_INNER  deep industrial red  — panels, section bars, status/button bg
#   ACCENT    industrial red       — primary action buttons & emphasis
#   BORDER    light red accent     — card outlines, separators, highlights
#   BG_CARD   white / light neutral— content, inputs, tables, data entry
BG_MAIN   = QColor(0x7B, 0x1A, 0x1A)   # dark industrial red
BG_CARD   = QColor(0xFF, 0xFD, 0xF7)   # white content / inputs / tables
BG_INNER  = QColor(0x8E, 0x23, 0x23)   # deep industrial red
ACCENT    = QColor(0xC0, 0x39, 0x2B)   # industrial red
TEXT_DARK = QColor(0x1A, 0x14, 0x0A)   # on-white text
TEXT_MID  = QColor(0x5A, 0x48, 0x3C)   # muted on-white text
TEXT_LITE = QColor(0xA0, 0x96, 0x8C)   # faint on-white text
TEXT_ON_DARK = QColor(0xFF, 0xF5, 0xF0)  # light text on dark red
BORDER    = QColor(0xD9, 0x53, 0x4F)   # light red accent
GRID_CLR  = QColor(0xC8, 0xB0, 0xAC)   # soft red-tinted grid
CAL_MIN_CLR  = QColor(0x1A, 0x5C, 0x94)
CAL_MAX_CLR  = QColor(0x1E, 0x7A, 0x3E)
CAL_BAND_CLR = QColor(0xD4, 0x7B, 0x0F)

SENSOR_COLORS = [
    QColor(0xC0, 0x39, 0x2B),
    QColor(0x1A, 0x5C, 0x94),
    QColor(0x1E, 0x7A, 0x3E),
    QColor(0xD4, 0x7B, 0x0F),
    QColor(0x6B, 0x2D, 0x8B),
    QColor(0x0F, 0x7A, 0x8A),
]

def sensor_color(idx):
    return SENSOR_COLORS[idx % len(SENSOR_COLORS)]

SESSION_START = datetime.datetime.now()

# ── Production / industrial configuration ─────────────────────
import os as _os

# Where persistent data (SQLite, audit, historian) is stored. An environment
# override (SCARLET_PANEL_DATA_DIR) lets test runs point at a throwaway dir so
# they never touch the operator's real data; the default is unchanged.
PANEL_DATA_DIR = _os.environ.get(
    "SCARLET_PANEL_DATA_DIR",
    _os.path.join(_os.path.expanduser("~"), "scarlet_test_panel_data"))
AUDIT_DB_PATH = _os.path.join(PANEL_DATA_DIR, "audit.db")
HISTORIAN_DB_PATH = _os.path.join(PANEL_DATA_DIR, "historian.db")
HISTORIAN_RETENTION_DAYS = 30

# Stale-data / no-data detection (seconds). If no valid, CRC-verified
# message arrives within this window, the displayed value is flagged STALE.
STALE_TIMEOUT_S = 3.0
# If no data at all for this long, treat as disconnected / lost link.
LINK_TIMEOUT_S = 8.0
# Serial reconnection backoff (seconds) between reconnect attempts.
RECONNECT_BACKOFF_S = 2.0

# ── Measurement model (Block Position Monitor, promt1.txt) ─────────
# The encoder is an *internal* position-counting sensor. There are NO encoder
# hardware parameters (PPR / decoding mode / gear ratio / wheel diameter /
# countsPerFoot) anywhere in the dashboard. The operator-defined model is a
# piecewise-LINEAR calibration table of up to MAX_CAL_POINTS anchor points:
#     position = P1 + (count - C1)/(C2 - C1) * (P2 - P1)
# These dashboard-side defaults mirror encoder/encoder/config.h DEFAULT_* and
# are used only as a fallback until the firmware CONFIRMS its active model via
# a STATUS / done-ack echo.
FW_MAX_CAL_POINTS = 4
FW_WITS_CORRECTION_DEFAULT = 0.0
ON_BOTTOM_EPS_FT = 0.75  # matches firmware: on-bottom when displayed pos <= this
# Demo calibration seeds (promt1.txt says the reference-image values are
# illustrative only — never real project values). The dashboard seeds these
# ONLY in DEMO mode so the monitor has a visible multi-point curve to render.
DEMO_CAL_POINTS = [
    {"counter": 4437,  "position": 31.58},
    {"counter": 7988,  "position": 63.21},
    {"counter": 11228, "position": 94.78},
    {"counter": 13199, "position": 111.00},
]
DEMO_WITS_CORRECTION = 0.0

# Demo encoder-count span: the demo calibration is rebased/scaled so the
# bottom anchor reads 0 and the top anchor reads DEMO_MAX_COUNT (100000),
# matching a higher-count encoder reference.
DEMO_MAX_COUNT = 100000

# Demo String Length (PIPE IN HOLE, ft). Measured Depth / TVD / Bit Depth /
# Sample Lag Depth are only defined once the string length is known, so Demo
# Mode seeds one (runtime only - it is never written to settings, so it can
# never overwrite the operator's real value).
DEMO_PIPE_IN_HOLE_FT = 6000.0

DEVICE_DEFAULTS = {
    # Calibration table (device-confirmed names): 4 anchor (counter, position)
    # rows, one per layer. Set only from firmware STATUS / done-ack.
    "calCounter1": 0,           "calPosition1": 0.0,
    "calCounter2": 0,           "calPosition2": 0.0,
    "calCounter3": 0,           "calPosition3": 0.0,
    "calCounter4": 0,           "calPosition4": 0.0,
    "countsPerFoot1": 0.0,      # per-interval counts/ft (device-confirmed)
    "countsPerFoot2": 0.0,
    "countsPerFoot3": 0.0,
    "countsPerFoot4": 0.0,
    "witsCorrectionFt": FW_WITS_CORRECTION_DEFAULT,
    # Live telemetry:
    "currentTicks": 0,
    "blockPositionFt": 0.0,
    "velocityFtMin": 0.0,
    "direction": "DOWN",     # "UP" / "DOWN" only (never NONE)
    "onBottom": False,          # stopped near the lowest calibrated anchor
    "calStatus": "NO_CALIBRATION",  # "NO_CALIBRATION" / "VALID" / "OUT_OF_RANGE"
    "calInRange": 0,            # 1 when calStatus == VALID
    "currentLayer": 1,
    "uptime_s": 0,
    "fw_version": "",
    "build": "",
    "boot_reason": "",
    "protocol_version": "",
    "eeprom_status": "--",
    "confirmed": False,   # True only after a STATUS/ack from the firmware
}
# How long the dashboard waits for a firmware acknowledgment before declaring
# a setting change "failed / not applied" (never silent success).
CMD_ACK_TIMEOUT_S = 3.0

# ── BIT POSITION state hysteresis ──────────────────────────────────────
# A state transition (HOLD <-> TRACK) must persist this many consecutive
# refreshes before it is applied, so tiny sensor noise around the hookload
# / Slip Window threshold cannot flip the bit position state back and forth.
# ~150 ms at the 20 Hz refresh rate.
BIT_POS_STATE_STABLE_TICKS = 3
