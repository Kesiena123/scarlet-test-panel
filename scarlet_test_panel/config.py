import datetime
from PyQt5.QtGui import QColor

NUM_ANALOG = 16

SENSOR_CONFIG = [
    {"name": f"Sensor {i+1}", "key": f"sensor{i+1}", "unit": "value",
     "cal_min": 0.0, "cal_max": 1000.0}
    for i in range(NUM_ANALOG)
]

TWO_POINT_CAL = [
    {"v_min": None, "val_min": None, "v_max": None, "val_max": None}
    for _ in range(NUM_ANALOG)
]

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

BG_MAIN   = QColor(0xF5, 0xF0, 0xE8)
BG_CARD   = QColor(0xFF, 0xFD, 0xF7)
BG_INNER  = QColor(0xEC, 0xE5, 0xD8)
ACCENT    = QColor(0xC0, 0x39, 0x2B)
TEXT_DARK = QColor(0x1A, 0x14, 0x0A)
TEXT_MID  = QColor(0x6B, 0x5E, 0x4E)
TEXT_LITE = QColor(0xA0, 0x92, 0x80)
BORDER    = QColor(0xC8, 0xBD, 0xAD)
GRID_CLR  = QColor(0xD8, 0xCF, 0xC0)
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

# Where persistent data (SQLite, audit, historian) is stored.
PANEL_DATA_DIR = _os.path.join(
    _os.path.expanduser("~"), "scarlet_test_panel_data")
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
    "direction": "STOPPED",     # "UP" / "ON BOTTOM" / "DOWN" / "STOPPED"
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
# a settings change "failed / not applied" (never silent success).
CMD_ACK_TIMEOUT_S = 3.0
