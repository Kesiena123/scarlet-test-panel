# =============================================================
#  Industrial Rig Block Position Monitor Dashboard
#
#  DEVICE<->DASHBOARD SETTINGS PROTOCOL  (keep in sync with
#  encoder/encoder/encoder.ino, serial_protocol.cpp and
#  services/commands.py + PROTOCOL.md)
#
#  Core principle: the dashboard NEVER shows a setting as applied based on
#  what the user typed. Every setting change is REQUEST -> CONFIRM ->
#  ACK(echo) -> VERIFY:
#    {"cmd":"set_calibration_point","layer":1,"value":31.58,"counter":4437,"req_id":1}
#    {"cmd":"confirm","req_id":1}
#  The firmware answers with an ack echoing its ACTIVE config; the dashboard
#  VERIFIES the echoed value == requested value and only then shows success.
#  save_calibration / load_calibration / reset_counter are IMMEDIATE: success
#  is shown only after the firmware's corresponding done/reset_ack.
#
#  Measurement model (promt1.txt): the encoder is an internal position counter
#  and the operator defines a piecewise-LINEAR calibration table of up to 4
#  anchor points (encoderCount, positionFt):
#      position = P1 + (count - C1)/(C2 - C1) * (P2 - P1)
#  The firmware computes blockPositionFt (authoritative), velocityFtMin,
#  calStatus and currentLayer. The
#  dashboard DISPLAYS those verbatim — it never maintains a competing
#  measurement calculation (it only mirrors the interpolation for precondition
#  checks / demo).
#
#  COMMANDS (dashboard -> firmware):
#     {"cmd":"status"}
#     {"cmd":"set_calibration_point","layer":..,"value":..[, "counter":..]}
#     {"cmd":"set_wits_correction","value":..}
#     {"cmd":"restore_defaults"} / {"cmd":"confirm"}
#     {"cmd":"save_calibration"} / {"cmd":"load_calibration"}
#     {"cmd":"reset_counter"}
#
#  RESPONSES (firmware -> dashboard), each ends ",\"crc\":N}":
#     {"type":"status",...,"calStatus":..,"calInRange":..,"currentTicks":..,
#      "blockPositionFt":..,"calCounter1":..,"calPosition1":..,..}
#     {"type":"report","currentTicks":..,"blockPositionFt":..,"velocityFtMin":..,
#      "direction":..,"calStatus":..,"currentLayer":..,..}
#     {"type":"ack","state":"awaiting_confirm"|"done"|"rejected"|"error",...}
#     {"type":"reset_ack","currentTicks":0,...}
#     {"type":"log","message":...}
#
#  TIMESTAMPS: the Arduino has NO RTC. Device-reported time is always
#  `uptime_s` = seconds since boot (clearly labelled in the UI). Reboots are
#  detected when the uptime field resets and are audited.
# =============================================================

import sys
import os
import time
import json
import math
import random
import serial
import datetime
from collections import deque

# --- Fix: allow running directly from inside the package folder ---
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# --- Absolute imports (package name required) ---
from scarlet_test_panel.config import (
    BG_MAIN, BG_CARD, BG_INNER, TEXT_DARK, TEXT_MID, TEXT_ON_DARK, BORDER,
    NUM_ANALOG, SENSOR_CONFIG, SPM_CONFIG, RPM_CONFIG,
    MS_PER_SAMPLE, SAMPLES_PER_SEC, WINDOW_SECS, RETAIN_SAMPLES, HISTORY_LEN,
    SESSION_START, AUDIT_DB_PATH, HISTORIAN_DB_PATH, HISTORIAN_RETENTION_DAYS,
    STALE_TIMEOUT_S, LINK_TIMEOUT_S, RECONNECT_BACKOFF_S,
    DEVICE_DEFAULTS, CMD_ACK_TIMEOUT_S,
    FW_MAX_CAL_POINTS, FW_WITS_CORRECTION_DEFAULT, ON_BOTTOM_EPS_FT,
    DEMO_CAL_POINTS, DEMO_WITS_CORRECTION, DEMO_MAX_COUNT,
    DEMO_PIPE_IN_HOLE_FT,
    TREND_SAMPLE_MS, TREND_MAX_POINTS,
    BIT_POS_STATE_STABLE_TICKS,
)
from scarlet_test_panel.utils import resource_path
from scarlet_test_panel.widgets.tab_bar import TabBar
from scarlet_test_panel.widgets.status_bar import StatusBar
from scarlet_test_panel.tabs.analog_tab import AnalogTab
from scarlet_test_panel.tabs.sensors_tab import SensorsTab
from scarlet_test_panel.tabs.graph_tab import GraphTab
from scarlet_test_panel.tabs.spm_rpm_tab import StrokeRpmTab
from scarlet_test_panel.tabs.analog_monitor_tab import AnalogMonitorTab
from scarlet_test_panel.tabs.block_position_tab import BlockPositionTab

# --- Design tokens (light/dark theme) ---
import scarlet_test_panel.theme as theme
from scarlet_test_panel.theme import current as theme_current

# --- Production services ---
from scarlet_test_panel.services.protocol import (
    decode_message, BadChecksum, MessageError, SENSOR_STATUS_CHARS)
from scarlet_test_panel.services.security import RoleManager, ROLE_OPERATOR, ROLE_ENGINEER
from scarlet_test_panel.services.audit import AuditLog
from scarlet_test_panel.services.historian import Historian
from scarlet_test_panel.services.commands import CommandTracker
from scarlet_test_panel.services.settings import load as load_settings, save as save_settings
from scarlet_test_panel.services import layers as layers_service
from scarlet_test_panel.services import digital_calibration as digcal

# --- Qt imports ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QFrame, QMessageBox, QFileDialog, QScrollArea,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap, QPalette

# Commands the generic gateway will accept (match firmware verbs).
ALLOWED_CMDS = {"status", "set_calibration_point", "set_wits_correction",
                "confirm", "save_calibration", "load_calibration",
                "reset_counter", "restore_defaults"}

# The ACTIVE calibration table persisted with the device-confirmed key names.
# `calibrated_position` / `cal_points` / `derive_layer` read exactly these
# keys from `self.device`, so saved values are the single source of truth.
CAL_PERSIST_KEYS = tuple(
    [f"calCounter{i}" for i in range(1, FW_MAX_CAL_POINTS + 1)]
    + [f"calPosition{i}" for i in range(1, FW_MAX_CAL_POINTS + 1)]
    + [f"countsPerFoot{i}" for i in range(1, FW_MAX_CAL_POINTS + 1)]
    + ["witsCorrectionFt", "encoderPolarity", "confirmed"]
)


class PumpDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("")
        self.setStyleSheet(f"background-color: {theme_current().color_name('BG_MAIN')};")
        self.serial = None
        self._demo_mode = False
        self._demo_counter = 0
        self._demo_analog_targets = [random.uniform(0.5, 4.5) for _ in range(NUM_ANALOG)]
        self._demo_spm_counter = [0] * 4
        self._demo_rpm_counter = [0] * 2
        self._demo_init = False

        # ── Production services ──────────────────────────────
        self._settings = load_settings()
        theme.set_theme(self._settings.get("theme", "red"))
        theme.refresh_shortcuts()
        self.roles = RoleManager(self._settings.get("role", ROLE_OPERATOR))
        self.audit = AuditLog(AUDIT_DB_PATH, default_actor="system")
        self.historian = Historian(HISTORIAN_DB_PATH, HISTORIAN_RETENTION_DAYS)

        # ── Link / data-freshness state (network resilience) ──
        self._last_valid_time = time.monotonic()          # last CRC-valid message
        self._data_stale = False
        self._link_down = False
        self._connect_backoff_until = 0.0
        self._crc_failures = 0
        self._reboot_count = 0
        self._recovered_count = 0
        self._legacy_throttle_ts = 0.0
        self._legacy_mode = False              # True while a no-CRC source is feeding
        self._serial_rx_buffer = bytearray()

        # Analog (16-channel) state
        self.analog_voltages = [0.0] * NUM_ANALOG
        self.analog_statuses = [""] * NUM_ANALOG
        self.histories = [deque(maxlen=HISTORY_LEN) for _ in range(NUM_ANALOG)]
        for h in self.histories:
            h.append((0.0, 0.0, datetime.datetime.now()))
        self.epoch_samples = [0] * NUM_ANALOG

        # SPM / RPM state
        self.spm_raw_counter = [0.0] * 4
        self.spm_offset = [0.0] * 4
        # spm_measured / rpm_measured are the RAW device reports; spm_values /
        # rpm_values are the CALIBRATED numbers every consumer reads
        # (digitalsensor1.txt §11/§17). The distinction exists so the Engineer
        # can always see the underlying signal, never to create a second
        # calculation path.
        self.spm_measured = [0.0] * 4
        self.spm_values = [0.0] * 4
        self.rpm_raw_counter = [0.0] * 2
        self.rpm_offset = [0.0] * 2
        self.rpm_measured = [0.0] * 2
        self.rpm_values = [0.0] * 2
        # Independent per-channel digital calibration (digitalsensor1.txt). Loaded
        # ONCE here and refreshed only when a save happens, so the 20 Hz tick
        # never touches the settings file.
        self._dig_cal = {"SPM": [None] * 4, "RPM": [None] * 2}
        self.reload_digital_calibration()

        # ── Block Position Monitor live telemetry ─────────────
        # Values here DISPLAY what the firmware reported (report/status).
        # The firmware is the authoritative measurement engine.
        self.current_ticks = 0
        self.block_position_ft = 0.0
        self.velocity_ft_min = 0.0
        self.direction = "DOWN"     # UP/DOWN only; derived from the live tick delta
        self._direction_prev_tick = None
        # RESET FEET runtime reference (local/demo mode). Used to re-reference
        # the position so it reads the operator's chosen starting feet at the
        # reset instant and tracks movement relative to it. Calibration is
        # NEVER modified. On the live (firmware) path the device itself holds
        # the equivalent reference, so these fields only affect the demo sweep.
        self._runtime_reference_active = False
        self._runtime_reference_offset = 0.0
        self.on_bottom = False          # stopped near the lowest calibrated anchor
        # String Length feature (promt.txt): user-defined Pipe in Hole (ft).
        # None = not configured; float >= 0 = saved value in feet.
        self.pipe_in_hole_ft = None
        self._bit_hold_ft = None  # seeded from the saved Pipe in Hole below
        self._load_pipe_in_hole()
        # SLIP WINDOW (promt.txt §18): seconds the state must hold before the
        # TRACK/HOLD transition applies. None = built-in default.
        self.slip_window_sec = None
        self._load_slip_window()
        # SLIP WINDOW load/time confirmation (slip_window.txt): Hookload (k-lb)
        # threshold + confirmation span (seconds). None = feature off, existing
        # TRACK/HOLD gating unchanged.
        self.slip_window_load_klb = None
        self.slip_window_time_sec = None
        self._load_slip_window_config()
        # SAMPLE LAG DEPTH derived display (promt3 §15): operator lag offset.
        self.sample_lag_offset_ft = 0.0
        self._load_sample_lag_offset()
        # Slip-window confirmation state machine.
        self._slip_state = "NOT ACTIVE"   # NOT ACTIVE | CONFIRMING | CONFIRMED
        self._slip_since = None           # _slip_clock() when CONFIRMING began
        self._slip_clock = time.monotonic  # injectable for deterministic tests
        # Analog-hookload-conditioned BIT POSITION / STRING LENGTH state
        # machine (promt.txt). Pipe-load is proven by the Analog Monitor's
        # live channel-0 Hookload (single hookload source):
        #   * NONE  - pipe in hole not yet configured (nothing to show).
        #   * HOLD  - no pipe load proven (hookload <= 0, or the Slip Window
        #             confirmation is not yet CONFIRMED): the bit position is
        #             frozen at the last valid depth, so small block movement /
        #             sensor noise never creeps it.
        #   * TRACK - pipe load proven: bit depth = String Length - Block
        #             Position, i.e. Bit moves opposite to the block while
        #             String Length = Block + Bit stays constant for the trip.
        # `_bit_hold_ft` keeps the last valid depth through HOLD windows and
        # starts at the entered Pipe in Hole (the flat string), so the bit is
        # initialised from a valid value before the first trip. After every
        # advance `pipe_in_hole_ft` is kept in lock-step with the live bit
        # (Pipe in Hole == current Bit Position, never a stale number).
        # `_bit_track_block_ft` is the previous Block Position while tracking:
        # the bit is driven by the ACTUAL block delta (promt.txt §4/§13/§16),
        # never by continuously re-deriving it from a String Length reference.
        # It is re-anchored to the live block every time the state re-enters
        # TRACK, so re-engagement never jumps (promt.txt §12).
        # State transitions are debounced for BIT_POS_STATE_STABLE_TICKS.
        self._bit_state = "NONE"
        self._bit_want = None
        self._bit_confirm_ct = 0
        self._bit_track_block_ft = None
        # HOLE DEPTH (promt.txt): the deepest Bit Position the string has
        # reached (accumulated running maximum). Non-decreasing: it HOLDS while
        # tripping / coming out of the hole and only advances again when the
        # bit runs deeper. None = never configured.
        self._hole_depth_max_ft = None
        self._hole_last_bit_ft = None
        self.cal_status = "NO_CALIBRATION"  # VALID / OUT_OF_RANGE / NO_CALIBRATION
        self.cal_in_range = 0
        self.current_layer = 1
        self.encoder_heartbeat = False
        self.encoder_uptime_s = 0
        self.encoder_boot_reason = ""
        self.encoder_boot_error = ""
        self.encoder_fw_version = ""
        self.encoder_build = ""
        self.encoder_protocol_version = ""
        self.encoder_eeprom_status = "--"
        self.encoder_sequence = 0
        self.encoder_recovered = False
        self.position_history = deque(maxlen=HISTORY_LEN)
        self.position_history.append((0.0, datetime.datetime.now()))

        # ── Real-time trend (promt1.txt) ──────────────────────────────
        # READ-ONLY trend history. Each entry is (timestamp, position_feet)
        # sampled at a controlled interval (TREND_SAMPLE_MS) by a QTimer, so
        # the chart never grows a point on every UI refresh. The buffer is a
        # bounded deque -> rolling history with flat memory. Feet always stays
        # the internal unit; meters is only a chart-display conversion.
        self.trend_data = deque(maxlen=TREND_MAX_POINTS)

        self._msg_count = 0
        self._comm_errors = 0

        # ── Device-confirmed settings model ──────────────────────────
        # Values here reflect what the FIRMWARE CONFIRMED it is actually
        # using (from STATUS / done-ack echo). Until the first STATUS arrives
        # after a (re)connect, `confirmed` is False and the UI treats
        # everything as unverified.
        self.device = dict(DEVICE_DEFAULTS)

        # Restart/reload: restore a previously-saved calibration table as the
        # ACTIVE calibration (single source of truth for the position calc).
        # When none exists, the demo/default seed may run as before.
        self._apply_saved_calibration()

        # RESET banner state: None | ("in_flight", ts) | ("done", ok, detail, ts)
        self._reset_state = None

        # ── Settings command tracker (request->confirm->verify) ───────
        self._cmd = CommandTracker(
            write=self._write_command_line, timeout=CMD_ACK_TIMEOUT_S)
        self._last_status_request = None

        self._build_ui()

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(MS_PER_SAMPLE)

        # Trend sampler: a separate QTimer so sampling never blocks the UI and
        # keeps running while the chart view is paused. (promt1.txt)
        self._trend_timer = QTimer()
        self._trend_timer.timeout.connect(self._record_trend_point)
        self._trend_timer.start(TREND_SAMPLE_MS)
        self._restore_geometry()

    # ------------------------------------------------------------------
    # Screen / geometry
    # ------------------------------------------------------------------
    def _screen_available(self):
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                return screen.availableGeometry()
        except Exception:
            pass
        return None

    def _restore_geometry(self):
        avail = self._screen_available()
        min_w, min_h = 1024, 680
        if avail is not None:
            min_w = min(min_w, avail.width())
            min_h = min(min_h, avail.height())
        self.setMinimumSize(min_w, min_h)
        geo = self._settings.get("geometry")
        if isinstance(geo, list) and len(geo) == 4:
            try:
                x, y, w, h = int(geo[0]), int(geo[1]), int(geo[2]), int(geo[3])
                if avail is not None:
                    w = min(w, avail.width())
                    h = min(h, avail.height())
                    max_x = avail.x() + avail.width() - w
                    max_y = avail.y() + avail.height() - h
                    x = max(avail.x(), min(x, max_x))
                    y = max(avail.y(), min(y, max_y))
                self.setGeometry(x, y, w, h)
            except Exception:
                self.resize(min_w, min_h)
            if self._settings.get("maximized"):
                self.showMaximized()
            return
        self.resize(min_w, min_h)
        if avail is not None and (avail.width() < 1100 or avail.height() < 700):
            self.showMaximized()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(14, 0, 14, 10)

        # Top chrome: the logo row is collapsed (0px) and grouped with the tab
        # bar in a 1px-gap sub-layout, so the only space above the menu buttons
        # is 1px. Every other gap in `root` keeps its original 8px.
        # The date/time clock lives in the fixed bottom bar (added to the
        # StatusBar further down), not up here.
        top_area = QVBoxLayout()
        top_area.setContentsMargins(0, 0, 0, 0)
        top_area.setSpacing(1)
        top_bar_host = QWidget()
        top_bar_host.setFixedHeight(0)
        top_bar = QHBoxLayout(top_bar_host)
        top_bar.setContentsMargins(1, 1, 1, 1)
        top_bar.setSpacing(10)
        logo_label = QLabel()
        logo_pixmap = QPixmap(resource_path("scarletIcon.jpg"))
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaledToHeight(4, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pixmap)
        else:
            lp2 = QPixmap(resource_path("scarletIcon.ico"))
            if not lp2.isNull():
                lp2 = lp2.scaledToHeight(4, Qt.SmoothTransformation)
                logo_label.setPixmap(lp2)
        logo_label.setStyleSheet("background: transparent;")
        logo_label.setFixedWidth(0 if logo_pixmap.isNull() else logo_pixmap.width())
        logo_label.setFixedHeight(4)
        top_bar.addWidget(logo_label)
        top_bar.addStretch()
        top_area.addWidget(top_bar_host)

        # Session date/time clock — re-homed to the fixed bottom bar below.
        self.hdr_dt_lbl = QLabel("")
        self.hdr_dt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.hdr_dt_lbl.setStyleSheet(
            f"color:{TEXT_ON_DARK.name()}; font-family:'Segoe UI'; font-size:11px; "
            "background:transparent;")
        self.hdr_dt_lbl.setFixedHeight(15)

        self.tab_bar = TabBar(["  Analog Signals  ", "  Dashboard  ",
                               "  Block Position  ", "  Analog Monitor  ",
                               "  Graph  ", "  Digital Sensor  ", "  Settings  "])
        top_area.addWidget(self.tab_bar)
        root.addLayout(top_area)
        self._root_sep = QFrame()
        self._root_sep.setFrameShape(QFrame.HLine)
        self._root_sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        root.addWidget(self._root_sep)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content_scroll.setWidget(self.stack)
        root.addWidget(content_scroll, stretch=1)

        self.analog_tab = AnalogTab(self)
        self.stack.addWidget(self.analog_tab)

        # Tab order: the live monitoring pages sit directly after the Dashboard
        # (Dashboard -> Block Position -> Analog Monitor), then the remaining
        # engineering pages. Every index below is named so no call site has to
        # remember a magic number.
        self.sensors_tab = SensorsTab(self)
        self.stack.addWidget(self.sensors_tab)

        self.block_tab = BlockPositionTab(self, self.roles, self.audit)
        self.stack.addWidget(self.block_tab)

        self.analog_monitor_tab = AnalogMonitorTab(self, self.roles, self.audit)
        self.stack.addWidget(self.analog_monitor_tab)

        self.graph_tab = GraphTab(self.histories, self.epoch_samples)
        self.stack.addWidget(self.graph_tab)
        self.graph_card = self.graph_tab.graph_card

        from scarlet_test_panel.tabs.digital_sensor_tab import DigitalSensorTab
        self.digital_sensor_tab = DigitalSensorTab(self, self.roles)
        self.stack.addWidget(self.digital_sensor_tab)

        # Must match the addWidget order above.
        self.IDX_ANALOG_SIGNALS = 0
        self.IDX_DASHBOARD = 1
        self.IDX_BLOCK_POSITION = 2
        self.IDX_ANALOG_MONITOR = 3
        self.IDX_GRAPH = 4
        self.IDX_DIGITAL_SENSORS = 5
        self.IDX_SETTINGS = 6

        self.tab_bar.on_change(self.stack.setCurrentIndex)

        # "Analog Signals" is no longer shown in the menu — only its BUTTON is
        # hidden. The page stays in the stack on purpose: live telemetry still
        # feeds its gauges (see _on_telemetry) and the Analog Monitor
        # calibration dialogs call into it, so removing the widget would break
        # those. The app now opens on "Dashboard".
        self.tab_bar.set_tab_visible(self.IDX_ANALOG_SIGNALS, False)
        self.tab_bar.set_current(self.IDX_DASHBOARD)

        # Diagnostics/engineering page is Supervisor/Engineer-only.
        def _apply_diag_role(role):
            try:
                if not hasattr(self, "IDX_DIAGNOSTICS"):
                    return
                visible = getattr(self.roles, "can_command", lambda: False)() or role in ("Supervisor", "Engineer", "admin")
                self.tab_bar.set_tab_visible(self.IDX_DIAGNOSTICS, visible)
                if not visible and self.stack.currentIndex() == self.IDX_DIAGNOSTICS:
                    self.stack.setCurrentIndex(getattr(self, "IDX_BLOCK_POSITION", 0))
            except Exception:
                pass
        try:
            self.roles.subscribe(_apply_diag_role)
            _apply_diag_role(self.roles.role())
        except Exception:
            pass

        self.status = StatusBar(self.roles, self.audit, self)
        # Date/time clock now rides the fixed bottom bar (last cell, right edge).
        self.status.layout().addWidget(self.hdr_dt_lbl)
        root.addWidget(self.status)
        self.status.connect_btn.clicked.connect(self._toggle_serial)
        self.status.demo_btn.clicked.connect(self._toggle_demo)

    def apply_theme(self, name):
        theme.set_theme(name)
        theme.refresh_shortcuts()
        t = theme_current()
        self.setStyleSheet(f"background-color: {t.color_name('BG_MAIN')};")
        sep_line = getattr(self, "_root_sep", None)
        if sep_line is not None:
            sep_line.setStyleSheet(
                f"background:{t.color_name('BORDER')}; max-height:1px; border:none;")
        if hasattr(self, "block_tab"):
            self.block_tab.apply_theme(name)
        if hasattr(self, "analog_tab"):
            self.analog_tab.apply_theme(name)
        if hasattr(self, "status"):
            self.status.apply_theme(name)
        if hasattr(self, "about_tab"):
            self.about_tab.theme_toggle.blockSignals(True)
            self.about_tab.theme_toggle.setCurrentText("Dark" if name == "dark" else "Light")
            self.about_tab.theme_toggle.blockSignals(False)
            self.about_tab.apply_theme(name)
        if hasattr(self, "diagnostics_tab"):
            self.diagnostics_tab.apply_theme(name)
        if hasattr(self, "analog_monitor_tab"):
            self.analog_monitor_tab.apply_theme(name)
        if hasattr(self, "sensors_tab"):
            self.sensors_tab.apply_theme(name)
        if hasattr(self, "digital_sensor_tab"):
            self.digital_sensor_tab.apply_theme(name)
        if hasattr(self, "stroke_tab"):
            self.stroke_tab.apply_theme(name)
        save_settings({"theme": name})

    # ------------------------------------------------------------------
    # Connection / demo
    # ------------------------------------------------------------------
    def _toggle_serial(self):
        if self.serial and self.serial.is_open:
            self._disconnect("dashboard")
            return
        port_label = self.status.port_cb.currentText()
        if "Refresh" in port_label:
            self.status.refresh_ports()
            return
        port = port_label.split("  [", 1)[0]
        baud = int(self.status.baud_cb.currentText()) if self.status.baud_cb.currentText().isdigit() else 115200
        try:
            self.serial = serial.Serial(port=port, baudrate=baud, timeout=0)
            self._serial_rx_buffer.clear()
            self.status.connect_btn.setText("Disconnect")
            self._demo_mode = False
            self.status.set_demo(False)
            self.status.set_connected(True, port)
            self.graph_card.unfreeze_elapsed()
            self._last_valid_time = time.monotonic()
            self._data_stale = False
            self._link_down = False
            self.audit.record("CONNECTION", f"connected to {port} @ {baud}", self.roles.role())
            self.status.set_link_quality(0.0)
            save_settings({"port": port, "baud": str(baud)})
            self.device["confirmed"] = False
            self._cmd.reset()
            self.request_status(reason="connect")
        except Exception as e:
            self.status.lbl.setText(f"ERROR: {e}")
            self.status.set_error()
            self.audit.record("CONNECTION_ERROR", str(e), self.roles.role())

    def _disconnect(self, actor="dashboard"):
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.serial = None
        self._serial_rx_buffer.clear()
        self._cmd.fail_all("disconnected before confirmation")
        self.status.set_connected(False)
        self.status.connect_btn.setText("Connect")
        self.graph_card.freeze_elapsed()
        self.audit.record("DISCONNECT", "link closed", actor)

    def _toggle_demo(self):
        self._demo_mode = not self._demo_mode
        if self._demo_mode:
            if self.serial and self.serial.is_open:
                self._disconnect("demo_toggle")
            # Re-seed the demo table with defaults ONLY when the device has no
            # calibration yet. If the operator has already calibrated / loaded
            # values, keep them across demo off/on toggles — do NOT reset the
            # seed guard (previously every demo re-enable re-seeded the defaults
            # over the operator's saved inputs).
            if not self.cal_points():
                self._demo_init = False
            self._demo_counter = 0
            self.status.set_demo(True)
            self.graph_card.unfreeze_elapsed()
            self.audit.record("DEMO", "demo mode enabled", self.roles.role())
        else:
            self.status.set_demo(False)
            self.graph_card.freeze_elapsed()
            self.audit.record("DEMO", "demo mode disabled", self.roles.role())

    # ------------------------------------------------------------------
    # Command gateway / raw write
    # ------------------------------------------------------------------
    def send_firmware_command(self, raw_json, actor=None, log=True):
        """Generic guarded gateway (used for custom/troubleshooting commands).

        Accepts only well-formed JSON carrying one of the allowed firmware
        verbs. Role-gated: Supervisor+ may issue configuration commands.
        """
        actor = actor or self.roles.role()
        try:
            msg = json.loads(raw_json)
        except (ValueError, TypeError):
            self.audit.record("COMMAND_REJECTED", f"not JSON: {raw_json!r}", actor)
            return False
        if not isinstance(msg, dict) or not str(msg.get("cmd", "")).strip():
            self.audit.record("COMMAND_REJECTED", "missing cmd in JSON", actor)
            return False
        cmd = str(msg["cmd"])
        if cmd not in ALLOWED_CMDS:
            self.audit.record("COMMAND_REJECTED", f"unknown verb {cmd}", actor)
            return False
        if not (self.serial and self.serial.is_open):
            QMessageBox.warning(self.window(), "Not connected",
                                "Connect to the firmware before sending commands.")
            return False
        try:
            self.serial.write((json.dumps(msg) + "\n").encode('ascii'))
            self.audit.record("COMMAND", f"sent {raw_json}", actor)
            return True
        except Exception as e:
            self.audit.record("COMMAND_ERROR", f"{cmd}: {e}", actor)
            return False

    def _write_command_line(self, line):
        """Raw serializer for the CommandTracker (role-gating happens in the
        Supervisor-gated UI handlers)."""
        if not (self.serial and self.serial.is_open):
            return False
        try:
            self.serial.write((line + "\n").encode('ascii'))
            return True
        except Exception:
            return False

    def request_status(self, reason="startup"):
        if self.serial and self.serial.is_open:
            self._write_command_line(json.dumps({"cmd": "status"}))
            self._last_status_request = reason

    # ------------------------------------------------------------------
    # Data pipeline
    # ------------------------------------------------------------------
    def _tick(self):
        # The sim advances ONLY while demo mode is ON; toggling demo OFF must
        # stop the simulation (real firmware handles it when a device is
        # connected; with no device the position simply freezes at the last
        # calculated value).
        if self._demo_mode:
            self._demo_tick()
        else:
            self._serial_tick()
        self._check_freshness()

        self._cmd.tick()
        self._update_header()

        idx = self.stack.currentIndex()
        if idx == self.IDX_DASHBOARD:
            self.sensors_tab.refresh()
        elif idx == self.IDX_GRAPH:
            self.graph_card.refresh()
        elif idx == self.IDX_DIGITAL_SENSORS:
            self.digital_sensor_tab.refresh()
        elif idx == self.IDX_BLOCK_POSITION:
            self.block_tab.refresh()
        elif idx == self.IDX_SETTINGS:
            self.about_tab.refresh()
        elif idx == self.IDX_ANALOG_MONITOR:
            self.analog_monitor_tab.refresh()
        self.status.refresh_state(self._data_stale, self._link_down)

    def _update_header(self):
        self.hdr_dt_lbl.setText(datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    # -- freshness / stale & link detection -----------------------------
    def _return_role_to_operator(self):
        """promt.txt: after a successful Engineer modification, immediately
        return the role to OPERATOR. Only acts when an ENGINEER session is
        active; real-time monitoring data is unaffected by the role change."""
        try:
            if self.roles.is_engineer():
                actor = self.roles.role()
                self.roles.set_role(ROLE_OPERATOR)
                try:
                    self.audit.record(
                        "ROLE_CHANGE",
                        "Engineer auto-return to OPERATOR after modification",
                        actor)
                except Exception:
                    pass
        except Exception:
            pass

    def _check_freshness(self):
        now = time.monotonic()
        if self._demo_mode:
            self._data_stale = False
            self._link_down = False
            return
        age = now - self._last_valid_time
        was_stale = self._data_stale
        was_link = self._link_down

        if self.serial and self.serial.is_open:
            fresh = age <= STALE_TIMEOUT_S
            linked = age <= LINK_TIMEOUT_S
            self._data_stale = not fresh
            self._link_down = not linked
        else:
            self._data_stale = False
            self._link_down = True

        if self._data_stale and not was_stale:
            self.audit.record("STALE_DATA",
                              f"no valid data for {age:.1f}s (freshness threshold {STALE_TIMEOUT_S}s)",
                              "system")
        if self._link_down and not was_link and self.serial and self.serial.is_open:
            self.audit.record("LINK_LOST", f"no link for {age:.1f}s; reconnecting", "system")
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self.serial is None or not self.serial.is_open:
            return
        now = time.monotonic()
        if now < self._connect_backoff_until:
            return
        self._connect_backoff_until = now + RECONNECT_BACKOFF_S
        port = self.status.port_cb.currentText().split("  [", 1)[0]
        baud = int(self.status.baud_cb.currentText()) if self.status.baud_cb.currentText().isdigit() else 115200
        try:
            self.serial.close()
        except Exception:
            pass
        try:
            self.serial = serial.Serial(port=port, baudrate=baud, timeout=0)
            self._serial_rx_buffer.clear()
            self._last_valid_time = time.monotonic()
            self._data_stale = False
            self._link_down = False
            self.audit.record("RECONNECT", f"reconnected to {port}", "system")
            self.device["confirmed"] = False
            self._cmd.fail_all("link lost before confirmation")
            self._cmd.reset()
            self.request_status(reason="reconnect")
        except Exception as e:
            try:
                if self.serial is not None:
                    self.serial.close()
            except Exception:
                pass
            self.serial = None
            self._cmd.fail_all("reconnect failed; link is down")
            self.status.set_connected(False)
            self.status.connect_btn.setText("Connect")
            self.audit.record("RECONNECT_FAILED", str(e), "system")

    # -- serial receive ---------------------------------------------------
    def _serial_tick(self):
        if self.serial is None or not self.serial.is_open:
            self._check_freshness()
            return
        try:
            waiting = self.serial.in_waiting
            if waiting <= 0:
                return
            self._serial_rx_buffer.extend(self.serial.read(waiting))
            if len(self._serial_rx_buffer) > 4096:
                del self._serial_rx_buffer[:-4096]
                self.audit.record("BAD_MESSAGE", "serial receive buffer overflow; discarded old bytes", "system")

            while True:
                nl = self._serial_rx_buffer.find(b"\n")
                cr = self._serial_rx_buffer.find(b"\r")
                ends = [p for p in (nl, cr) if p >= 0]
                if not ends:
                    break
                end = min(ends)
                raw = bytes(self._serial_rx_buffer[:end])
                del self._serial_rx_buffer[:end + 1]
                line = raw.decode('utf-8', errors='ignore').strip()
                if line:
                    self._handle_serial_line(line)
        except Exception as e:
            self.audit.record("SERIAL_ERROR", str(e), "system")

    def _handle_serial_line(self, line):
        if '"crc"' in line:
            try:
                data = decode_message(line)
                self._apply_protocol(data)
            except BadChecksum as e:
                self._crc_failures += 1
                self._comm_errors += 1
                self.audit.record("CHECKSUM_FAILURE",
                                  f"rejected message: {e} (total={self._crc_failures})",
                                  "system")
                self.status.set_link_quality(-1.0)
            except MessageError as e:
                self._comm_errors += 1
                self.audit.record("BAD_MESSAGE", str(e), "system")
        else:
            self._note_legacy()
            try:
                data = json.loads(line)
                self._apply_legacy(data)
            except (json.JSONDecodeError, ValueError, KeyError):
                self._crc_failures += 1
                self._comm_errors += 1
                self.audit.record("BAD_MESSAGE", "unparseable legacy line", "system")

    def _note_legacy(self):
        self._legacy_mode = True
        now = time.monotonic()
        if now - self._legacy_throttle_ts > 30:
            self._legacy_throttle_ts = now
            self.audit.record("LEGACY_DATA",
                              "received data WITHOUT checksum (legacy source)",
                              "system")

    # -- protocol (CRC-validated) apply -----------------------------------
    def _apply_protocol(self, data):
        if not isinstance(data, dict):
            raise MessageError("protocol message is not an object")
        self._legacy_mode = False
        mtype = data.get("type", "report")
        self._last_valid_time = time.monotonic()
        self._data_stale = False
        self._crc_failures = 0
        self.status.set_link_quality(1.0)
        self._apply_analog_telemetry(data)

        if mtype == "ack":
            self._handle_ack(data)
            return
        if mtype == "status":
            self._handle_status(data)
            # A status reply completes an outstanding capture_tick() request
            # (promt2: CAPTURE reads the Arduino's authoritative currentTicks).
            self._cmd.on_status(data)
            return
        if mtype == "reset_ack":
            self._handle_reset_ack(data)
            return
        if mtype == "reset_feet_ack":
            self._handle_reset_feet_ack(data)
            return
        if mtype == "log":
            self.audit.record("FW_LOG",
                              data.get("message") or data.get("detail") or "",
                              "system", uptime=self._uptime_from(data))
            return
        if mtype in ("data", "report"):
            self._handle_report(data)
            self._write_historian()
            return
        # Unknown type: count as a comm oddity but do not drop silently.
        self._comm_errors += 1
        self.audit.record("BAD_MESSAGE", f"unknown firmware type {mtype!r}", "system")

    def _uptime_from(self, data):
        try:
            return int(data.get("uptime_s", self.encoder_uptime_s) or self.encoder_uptime_s)
        except (TypeError, ValueError):
            return self.encoder_uptime_s

    def _track_uptime(self, data):
        """Update uptime and detect firmware reboots (uptime decreasing)."""
        try:
            new_up = int(data.get("uptime_s") or 0)
        except (TypeError, ValueError):
            return
        if new_up < self.encoder_uptime_s:
            self._reboot_count += 1
            self.encoder_recovered = True
            self._recovered_count += 1
            self.audit.record("FW_REBOOT",
                              f"firmware uptime reset from {self.encoder_uptime_s}s to {new_up}s",
                              "system", uptime=new_up)
        self.encoder_uptime_s = new_up

    # -- STATUS snapshot --------------------------------------------------
    def _derive_direction(self, new_ticks):
        """UP/DOWN-only direction derived from the live encoder tick delta.

        Rule (promt2): if currentTick > previousTick -> UP; if currentTick <
        previousTick -> DOWN; if they are EQUAL keep the last valid direction.
        Direction is NEVER reported as NONE/IDLE/STOPPED. On the very first
        sample the initial default (DOWN) is preserved until real movement is
        observed."""
        try:
            new_ticks = int(new_ticks)
        except (TypeError, ValueError):
            return
        prev = self._direction_prev_tick
        if prev is None:
            self._direction_prev_tick = new_ticks
            return
        self._direction_prev_tick = new_ticks
        if new_ticks > prev:
            self.direction = "UP"
        elif new_ticks < prev:
            self.direction = "DOWN"

    def _handle_status(self, data):
        self.encoder_fw_version = data.get("fw_version", "") or ""
        self.encoder_build = data.get("build", "") or ""
        self.encoder_boot_reason = data.get("boot_reason", "") or ""
        self.encoder_boot_error = data.get("boot_error", "") or ""
        self.encoder_protocol_version = data.get("protocol_version", "") or ""
        es = data.get("eeprom_status")
        if es:
            self.encoder_eeprom_status = str(es)
        seq = data.get("sequence")
        if seq is not None:
            self.encoder_sequence = int(seq)
        self._track_uptime(data)
        if "currentTicks" in data:
            self.current_ticks = int(float(data["currentTicks"]))
        if "blockPositionFt" in data:
            self._set_block_position(float(data["blockPositionFt"]))
        if "velocityFtMin" in data:
            self.velocity_ft_min = float(data["velocityFtMin"])
        self._derive_direction(self.current_ticks)
        if "onBottom" in data:
            self.on_bottom = str(data["onBottom"]).lower() in ("1", "true", "yes")
        if "calStatus" in data:
            self.cal_status = str(data["calStatus"])
        if "calInRange" in data:
            try:
                self.cal_in_range = int(float(data["calInRange"]))
            except (TypeError, ValueError):
                self.cal_in_range = 0
        if "currentLayer" in data:
            self.current_layer = int(data["currentLayer"])
        self._apply_device_config(data, source="status")

    def _handle_report(self, data):
        self._msg_count += 1
        seq = data.get("sequence")
        if seq is not None:
            self.encoder_sequence = int(seq)
        es = data.get("eeprom_status")
        if es:
            self.encoder_eeprom_status = str(es)
        if "currentTicks" in data:
            self.current_ticks = int(float(data["currentTicks"]))
        if "blockPositionFt" in data:
            self._set_block_position(float(data["blockPositionFt"]))
        if "velocityFtMin" in data:
            self.velocity_ft_min = float(data["velocityFtMin"])
        self._derive_direction(self.current_ticks)
        if "onBottom" in data:
            self.on_bottom = str(data["onBottom"]).lower() in ("1", "true", "yes")
        if "calStatus" in data:
            self.cal_status = str(data["calStatus"])
        if "calInRange" in data:
            try:
                self.cal_in_range = int(float(data["calInRange"]))
            except (TypeError, ValueError):
                self.cal_in_range = 0
        if "currentLayer" in data:
            self.current_layer = int(data["currentLayer"])
        if "heartbeat" in data:
            self.encoder_heartbeat = bool(data["heartbeat"])
        self._track_uptime(data)
        # Reports carry the operator-defined parameters too; store them (the
        # firmware is authoritative) but do NOT flip `confirmed` mid-stream.
        self._apply_device_config(data, source="report")

    def _handle_ack(self, data):
        state = str(data.get("state", "")).lower()
        if state == "done":
            if "currentTicks" in data:
                self.current_ticks = int(float(data["currentTicks"]))
            if "blockPositionFt" in data:
                self._set_block_position(float(data["blockPositionFt"]))
            if "velocityFtMin" in data:
                self.velocity_ft_min = float(data["velocityFtMin"])
            self._derive_direction(self.current_ticks)
            if "onBottom" in data:
                self.on_bottom = str(data["onBottom"]).lower() in ("1", "true", "yes")
            if "calStatus" in data:
                self.cal_status = str(data["calStatus"])
            if "calInRange" in data:
                try:
                    self.cal_in_range = int(float(data["calInRange"]))
                except (TypeError, ValueError):
                    self.cal_in_range = 0
            if "currentLayer" in data:
                self.current_layer = int(data["currentLayer"])
        result = self._cmd.on_ack(data)
        if state == "done" and result == "verified":
            self._apply_device_config(data, source="ack")
        self.audit.record("FIRMWARE_ACK",
                          f"action={data.get('action')} state={state} "
                          f"detail={data.get('detail','')} req_id={data.get('req_id','')}",
                          "system", uptime=self.encoder_uptime_s)

    def _handle_reset_ack(self, data):
        """RESET SUCCESSFUL only after the device confirms currentTicks == 0."""
        self._track_uptime(data)
        raw = data.get("currentTicks")
        ok = raw is not None and int(float(raw)) == 0
        detail = data.get("detail") or ""
        source = data.get("source") or "device"
        verified = self._cmd.on_reset_ack(data)

        if verified == "verified":
            self._set_reset_state(True, f"RESET SUCCESSFUL — counter zeroed ({source})")
            self.audit.record("RESET",
                              f"device confirmed counter reset (currentTicks=0) "
                              f"source={source} detail={detail}",
                              "system", uptime=self.encoder_uptime_s)
            self._return_role_to_operator()
        elif ok and verified is False:
            # No in-flight request matched (physical button press). Still a
            # legitimate, device-confirmed reset.
            self._set_reset_state(True, "RESET SUCCESSFUL — counter zeroed (button)")
            self.audit.record("RESET",
                              "device confirmed counter reset via physical button "
                              "(currentTicks=0)",
                              "system", uptime=self.encoder_uptime_s)
            self._return_role_to_operator()
        else:
            self._set_reset_state(False, "reset_ack reported a NON-ZERO counter")
            self.audit.record("RESET_FAILED",
                              f"reset_ack with counter={data.get('currentTicks')}",
                              "system", uptime=self.encoder_uptime_s)
        self.encoder_recovered = False
        if ok:
            self.current_ticks = 0
        # Update the live display immediately on the reset confirmation so the
        # dashboard never shows stale velocity/direction/position after a reset
        # (the next periodic report may be up to DATA_PERIOD_MS away). The
        # reset_ack carries the device-confirmed values; when a field is absent
        # (older firmware) fall back to the reset invariants: velocity 0;
        # direction is re-derived from the tick delta (UP/DOWN only).
        if "blockPositionFt" in data:
            self._set_block_position(float(data["blockPositionFt"]))
        self.velocity_ft_min = float(data.get("velocityFtMin") or 0.0)
        self._derive_direction(self.current_ticks)
        if "onBottom" in data:
            self.on_bottom = str(data["onBottom"]).lower() in ("1", "true", "yes")
        if "calStatus" in data:
            self.cal_status = str(data["calStatus"])
        if "calInRange" in data:
            try:
                self.cal_in_range = int(float(data["calInRange"]))
            except (TypeError, ValueError):
                self.cal_in_range = 0

    def _handle_reset_feet_ack(self, data):
        """RESET FEET — runtime position only. Confirmation is shown ONLY after
        the device confirms currentTicks == 0 AND blockPositionFt equals the
        requested NEW starting position (default 0.00 ft, or any user-supplied
        starting feet). The saved calibration anchors are never touched; this
        only re-establishes the temporary runtime reference (tick=0 ->
        user starting feet)."""
        self._track_uptime(data)
        raw_ticks = data.get("currentTicks")
        raw_pos = data.get("blockPositionFt")
        ticks_ok = (raw_ticks is not None and int(float(raw_ticks)) == 0)
        try:
            new_pos = float(raw_pos) if raw_pos is not None else 0.0
        except (TypeError, ValueError):
            new_pos = 0.0
        source = data.get("source") or "device"
        # Capture the requested reference BEFORE on_reset_feet_ack() resolves
        # (and pops) the pending request, so a failed verify can still report
        # the operator-entered starting feet in the banner.
        req_expected = None
        try:
            rid = int(data.get("req_id") or 0)
            pending = self._cmd._pending
            req_obj = pending.get(rid)
            if req_obj is None:
                # Older firmware acks omit req_id — capture the single pending
                # reset_feet request's requested reference for the diagnostic.
                for r in pending.values():
                    if r.cmd == "reset_feet":
                        req_obj = r
                        break
            if req_obj is not None:
                req_expected = dict(req_obj.expected)
        except Exception:
            req_expected = None
        verified = self._cmd.on_reset_feet_ack(data)

        if verified == "verified":
            self._set_reset_state(
                True, f"STARTING POSITION RESET SUCCESSFULLY — "
                      f"New Starting Position: {new_pos:.2f} FT")
            self.audit.record("RESET_FEET",
                              f"device confirmed new runtime reference "
                              f"(tick=0, start={new_pos:.2f} ft) "
                              f"source={source} — calibration untouched",
                              "system", uptime=self.encoder_uptime_s)
        else:
            # Device confirmed the tick but echoed a position that does NOT
            # match the operator-entered starting feet. The classic 0.00
            # response means the flashed firmware predates the `value` field
            # (starting-feet support, FW 4.1+) and must be reflashed.
            detail = "RESET POSITION FAILED — device did not confirm the new reference"
            if ticks_ok and raw_pos is not None:
                want = float(req_expected.get("blockPositionFt", 0.0)) \
                    if req_expected else 0.0
                detail += (f" (device returned {new_pos:.2f} ft, "
                           f"requested {want:.2f} ft)")
            self._set_reset_state(False, detail)
            self.audit.record("RESET_FEET_FAILED",
                              f"reset_feet_ack tick={raw_ticks} pos={raw_pos}",
                              "system", uptime=self.encoder_uptime_s)
        self.encoder_recovered = False
        if ticks_ok:
            self.current_ticks = 0
        # Immediately reflect the new runtime reference. NOTE: the block
        # position is set directly to the device-confirmed starting feet — it
        # does NOT recompute from calibration (the calibration table is kept
        # intentionally unchanged).
        self._set_block_position(new_pos)
        self.velocity_ft_min = float(data.get("velocityFtMin") or 0.0)
        self._derive_direction(self.current_ticks)
        if "onBottom" in data:
            self.on_bottom = str(data["onBottom"]).lower() in ("1", "true", "yes")
        if "calStatus" in data:
            self.cal_status = str(data["calStatus"])
        if "calInRange" in data:
            try:
                self.cal_in_range = int(float(data["calInRange"]))
            except (TypeError, ValueError):
                self.cal_in_range = 0

    def _set_reset_state(self, ok, detail):
        self._reset_state = ("done", bool(ok), detail, time.monotonic())
    def consume_reset_state(self):
        """Return and clear the pending RESET banner (block tab reads it)."""
        if self._reset_state is None:
            return None
        state = self._reset_state
        self._reset_state = None
        return state

    # -- device-confirmed config -------------------------------------------
    # KEYS accepted from the firmware (all the operator-defined calibration model).
    DEV_INT_KEYS = ("currentTicks", "calCounter1", "calCounter2", "calCounter3",
                    "calCounter4", "calInRange", "currentLayer", "uptime_s",
                    "encoderPolarity")
    DEV_FLOAT_KEYS = ("calPosition1", "calPosition2", "calPosition3", "calPosition4",
                      "countsPerFoot1", "countsPerFoot2", "countsPerFoot3", "countsPerFoot4",
                      "witsCorrectionFt", "blockPositionFt", "velocityFtMin")

    def _apply_device_config(self, data, source="status"):
        """Record which values the device CONFIRMED it is actively using.

        Only STATUS snapshots and `done` acks may raise `confirmed`; report
        messages update the live measurement fields but never raise it.
        """
        changed = []
        for k in self.DEV_FLOAT_KEYS + self.DEV_INT_KEYS:
            if k not in data:
                continue
            v = data[k]
            try:
                if k in self.DEV_INT_KEYS:
                    v = int(float(v))
                else:
                    v = float(v)
            except (TypeError, ValueError):
                continue
            old = self.device.get(k)
            if old is not None and abs(float(old) - v) > 1e-9:
                changed.append(f"{k}={old}->{v}")
            self.device[k] = v
        for k in ("fw_version", "build", "boot_reason", "protocol_version", "eeprom_status"):
            if k in data and data[k] is not None:
                if str(self.device.get(k, "")) != str(data[k]):
                    changed.append(f"{k}={self.device.get(k)}->{data[k]}")
                self.device[k] = str(data[k])
        if source in ("status", "ack"):
            self.device["confirmed"] = True
        if changed:
            self.audit.record("DEVICE_CONFIG",
                              f"{source}: " + ", ".join(changed),
                              "system", uptime=self.encoder_uptime_s)

    # -- telemetry setter ----------------------------------------------------
    def _set_block_position(self, ft):
        self.block_position_ft = ft
        self.position_history.append((ft, datetime.datetime.now()))

    # -- real-time trend (promt1.txt) ---------------------------------------
    # The trend is READ-ONLY. These methods only *record* the existing
    # authoritative live position (block_position_ft) into a bounded rolling
    # buffer. They never touch encoder ticks, calibration, wraps, velocity or
    # the Arduino link.
    def _record_trend_point(self):
        """QTimer slot: controlled 1 Hz trend sample.

        Appends (timestamp, feet) for the EXACT live position currently shown
        on the dashboard. Runs even while the chart view is PAUSED — pausing
        only freezes the display, recording continues.
        """
        self.trend_data.append(
            (datetime.datetime.now(), float(self.block_position_ft or 0.0)))

    def clear_trend(self):
        """CLEAR TREND — drop ONLY the trend history.

        Encoder tick, block position, calibration, wraps and the Arduino link
        all keep running; the chart simply restarts collecting live data.
        """
        self.trend_data.clear()
        self.audit.record("TREND", "CLEAR TREND - chart history cleared",
                          "operator")

    def set_runtime_reference(self, base_abs_ft, start_ft):
        """Re-reference the position (RESET FEET, local/demo mode). Records the
        absolute calibrated position `base_abs_ft` at the reset instant so the
        position reads `start_ft` right now and tracks movement relative to it.
        Calibration is NEVER modified. (Live firmware path has its own reference
        on the device and does not use this.)
        """
        self._runtime_reference_active = True
        self._runtime_reference_offset = float(base_abs_ft) - float(start_ft)

    def display_position_from_calibration(self, abs_ft):
        """Apply the active RESET FEET reference to an absolute calibrated
        position. Used by the demo sweep so it continues tracking from the
        operator's chosen starting feet."""
        if self._runtime_reference_active:
            return float(abs_ft) - self._runtime_reference_offset
        return float(abs_ft)

    def _write_historian(self):
        self.historian.record(self.block_position_ft, int(self.current_ticks),
                              source="firmware", is_recovered=self.encoder_recovered)

    # -- analog --------------------------------------------------------------
    def _apply_legacy(self, data):
        """Legacy no-CRC sources (usually analog/SPM/RPM signals)."""
        self._last_valid_time = time.monotonic()
        self._data_stale = False
        self.status.set_link_quality(0.5)

        self._apply_analog_telemetry(data)

        for i, cfg in enumerate(SPM_CONFIG):
            if cfg["counter_key"] in data:
                try:
                    self.spm_raw_counter[i] = float(data[cfg["counter_key"]])
                except (TypeError, ValueError):
                    pass
            if cfg["spm_key"] in data:
                try:
                    raw = float(data[cfg["spm_key"]])
                except (TypeError, ValueError):
                    raw = None
                if raw is not None:
                    # RAW / MEASURED stays visible for the calibration windows;
                    # spm_values is the single CALIBRATED source every
                    # consumer (Dashboard, Digital Sensor, Total SPM) reads.
                    self.spm_measured[i] = raw
                    calibrated = digcal.calibrated_value(
                        "SPM", self._dig_cal["SPM"][i], raw)
                    if calibrated is not None:
                        self.spm_values[i] = calibrated

        for i, cfg in enumerate(RPM_CONFIG):
            if cfg["counter_key"] in data:
                try:
                    self.rpm_raw_counter[i] = float(data[cfg["counter_key"]])
                except (TypeError, ValueError):
                    pass
            if cfg["rpm_key"] in data:
                try:
                    raw = float(data[cfg["rpm_key"]])
                except (TypeError, ValueError):
                    raw = None
                if raw is not None:
                    self.rpm_measured[i] = raw
                    calibrated = digcal.calibrated_value(
                        "RPM", self._dig_cal["RPM"][i], raw)
                    if calibrated is not None:
                        self.rpm_values[i] = calibrated

    def _apply_analog_telemetry(self, data):
        """Ingest the 16-channel analog bank from any message that carries it.

        protocol 4.2: the periodic report appends ``sensor1..sensor16``
        (corrected input volts, 3 d.p.) plus ``sensorStatus`` (16-char compact
        status string, char[i] = Sensor i).  Legacy no-CRC sources may send
        only the sensor keys.  All parsing is additive and never raises.
        A channel whose status is ``UNUSED`` was disabled by the dashboard and
        is NOT sampled by the firmware (it reports a fixed 0.000 V); its
        reported value is deliberately NOT ingested and its live channel is
        zeroed so a floating unused input can never linger as a real reading.
        """
        if not isinstance(data, dict):
            return
        raw = data.get("sensorStatus")
        statuses = [None] * NUM_ANALOG
        if isinstance(raw, str):
            for i in range(min(len(raw), NUM_ANALOG)):
                status = SENSOR_STATUS_CHARS.get(raw[i].upper(), "INVALID")
                if status != "NO DATA":
                    statuses[i] = status
                    self.analog_statuses[i] = status
        for i in range(NUM_ANALOG):
            if statuses[i] == "UNUSED":
                # voltage_read.txt §4/§12/§18-T4: an unused channel must read
                # 0.000 V, never a stale or phantom live value; its gauge shows
                # the DISABLED state instead of a number.
                self.analog_voltages[i] = 0.0
                try:
                    self.analog_tab.setDisabled(i, True)
                except Exception:
                    pass
                continue
            v = data.get(f"sensor{i + 1}")
            if v is not None:
                try:
                    self._push_analog(i, max(0.0, min(5.0, float(v))))
                except (TypeError, ValueError):
                    pass

    def _push_analog(self, i, v):
        cfg = SENSOR_CONFIG[i]
        cal = cfg["cal_min"] + (v / 5.0) * (cfg["cal_max"] - cfg["cal_min"])
        ts = datetime.datetime.now()
        self.analog_voltages[i] = v
        try:
            self.analog_tab.setDisabled(i, False)
        except Exception:
            pass
        self.analog_tab.setVoltage(i, v)
        self.histories[i].append((v, cal, ts))
        self.epoch_samples[i] += 1
        if self.epoch_samples[i] >= WINDOW_SECS * SAMPLES_PER_SEC:
            hist = self.histories[i]
            retained = list(hist)[-RETAIN_SAMPLES:]
            hist.clear()
            hist.extend(retained)
            self.epoch_samples[i] = RETAIN_SAMPLES

    def reset_spm(self, i):
        self.spm_offset[i] = self.spm_raw_counter[i]

    def reset_rpm(self, i):
        self.rpm_offset[i] = self.rpm_raw_counter[i]

    # -- DIGITAL SENSOR CALIBRATION (digitalsensor1.txt) -----------------
    def reload_digital_calibration(self):
        """Re-read the per-channel SPM/RPM calibrations from the existing
        settings store and re-apply them to the values already on screen, so a
        saved curve takes effect immediately (the UI can never show the new
        calibration while the engine keeps using the old one)."""
        try:
            self._dig_cal = digcal.load_all()
        except Exception:
            self._dig_cal = {"SPM": [None] * len(SPM_CONFIG),
                             "RPM": [None] * len(RPM_CONFIG)}
        for i in range(len(self.spm_values)):
            calibrated = digcal.calibrated_value(
                "SPM", self._dig_cal["SPM"][i], self.spm_measured[i])
            if calibrated is not None:
                self.spm_values[i] = calibrated
        for i in range(len(self.rpm_values)):
            calibrated = digcal.calibrated_value(
                "RPM", self._dig_cal["RPM"][i], self.rpm_measured[i])
            if calibrated is not None:
                self.rpm_values[i] = calibrated

    def digital_measured(self, kind, idx):
        """RAW / MEASURED device report for one channel (never calibrated).

        A channel that has never reported stays None - the page shows '--',
        never a misleading 0."""
        src = self.spm_measured if kind == "SPM" else self.rpm_measured
        if not (0 <= idx < len(src)) or src[idx] is None:
            return None
        return float(src[idx])

    def digital_value(self, kind, idx):
        """CALIBRATED value the Dashboard already shows for one channel."""
        src = self.spm_values if kind == "SPM" else self.rpm_values
        if not (0 <= idx < len(src)) or src[idx] is None:
            return None
        return float(src[idx])

    @property
    def total_spm(self):
        """TOTAL SPM — the sum of the four existing SPM values (single source;
        no separate pump calculation is added). A channel that never reported
        contributes nothing instead of breaking the sum."""
        return float(sum(v for v in self.spm_values if v is not None))

    # -- SAMPLE LAG DEPTH derived display (promt3 §15) --------------------
    def _load_sample_lag_offset(self):
        """Load the saved SAMPLE LAG OFFSET (ft) from settings. None/absent =
        a 0-foot offset: Sample Lag Depth follows Measured Depth exactly."""
        val = self._settings.get("sample_lag_offset_ft")
        try:
            self.sample_lag_offset_ft = float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            self.sample_lag_offset_ft = 0.0
        if self.sample_lag_offset_ft < 0.0:
            self.sample_lag_offset_ft = 0.0

    def set_sample_lag_offset(self, value):
        """Save a new SAMPLE LAG OFFSET (ft). Non-negative number; returns
        True on success (the Dashboard only reads this value)."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v < 0.0:
            return False
        self.sample_lag_offset_ft = v
        save_settings({"sample_lag_offset_ft": v})
        return True

    # -- helpers for the block tab -------------------------------------------

    def _persist_calibration(self):
        """Write the ACTIVE calibration table to settings.json so the
        operator's saved values become the single source of truth and survive
        restart/reload (they are reloaded at startup and used by the position
        calculation — never the old defaults)."""
        from scarlet_test_panel.services import settings as settings_service
        snapshot = {}
        for k in CAL_PERSIST_KEYS:
            if k in self.device:
                snapshot[k] = self.device[k]
        settings_service.save({"calibration": snapshot})
        if snapshot:
            self.audit.record(
                "CAL_PERSIST",
                "calibration table persisted: "
                + ", ".join(f"{k}={snapshot[k]}" for k in snapshot),
                self.roles.role())

    def _apply_saved_calibration(self, settings=None):
        """Load a previously-persisted calibration into the ACTIVE device table.
        Call with `settings` = a freshly-read settings dict to reload from disk
        (LOAD button); the startup call uses the already-loaded settings.
        Returns True when a persisted table was applied."""
        if settings is None:
            settings = self._settings
        cal = settings.get("calibration")
        if not isinstance(cal, dict) or not cal:
            return False
        applied = False
        for k in CAL_PERSIST_KEYS:
            if k not in cal:
                continue
            v = cal[k]
            if k == "confirmed":
                self.device[k] = bool(v)
            elif k.startswith("calCounter") or k == "encoderPolarity":
                try:
                    self.device[k] = int(float(v))
                except (TypeError, ValueError):
                    continue
            elif k in ("calPosition1", "calPosition2", "calPosition3",
                       "calPosition4", "countsPerFoot1", "countsPerFoot2",
                       "countsPerFoot3", "countsPerFoot4", "witsCorrectionFt"):
                try:
                    self.device[k] = float(v)
                except (TypeError, ValueError):
                    continue
            else:
                continue
            applied = True
        return applied

    def layer_refs(self):
        """Return the device-confirmed calibration positions as [ref1..ref4]."""
        return [float(self.device.get(f"calPosition{i}", 0.0)) for i in range(1, FW_MAX_CAL_POINTS + 1)]

    def cal_points(self):
        """Return the device-confirmed calibration anchors as sorted
        (counter, positionFt) pairs (only configured rows, counter>0 or pos!=0)."""
        pts = []
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            counter = int(self.device.get(f"calCounter{i}", 0) or 0)
            pos = float(self.device.get(f"calPosition{i}", 0.0) or 0.0)
            if counter != 0 or pos != 0.0:
                pts.append((counter, pos))
        pts.sort(key=lambda p: p[0])
        return pts

    def calibrated_position(self, ticks=None):
        """Dashboard mirror of the firmware multi-point interpolation
        (display/precondition only; the firmware remains authoritative).
        Returns reportedPositionFt, or None when not enough anchors or
        out-of-range (never silently extrapolated)."""
        pts = self.cal_points()
        if ticks is None:
            ticks = self.current_ticks
        ticks = float(ticks)
        if len(pts) == 0:
            return None
        if len(pts) == 1:
            c0, p0 = pts[0]
            if abs(ticks - c0) < 1e-9:
                pos = p0
            else:
                return None
        else:
            c0, p0 = pts[0]
            cn, pn = pts[-1]
            if ticks < c0 or ticks > cn:
                return None                     # out of range — not trusted
            pos = p0
            for (c1, p1), (c2, p2) in zip(pts, pts[1:]):
                if c1 <= ticks <= c2:
                    if c2 == c1:
                        pos = p1
                    else:
                        pos = p1 + (ticks - c1) / (c2 - c1) * (p2 - p1)
                    break
        return pos + float(self.device.get("witsCorrectionFt", 0.0) or 0.0)

    # -- String Length feature (promt.txt) ------------------------------------
    def _load_pipe_in_hole(self):
        """Load saved Pipe in Hole (ft) from settings. None = not configured."""
        val = self._settings.get("pipe_in_hole")
        if val is None:
            self.pipe_in_hole_ft = None
            self._bit_hold_ft = None
            return
        try:
            v = float(val)
            self.pipe_in_hole_ft = v if v >= 0.0 else None
        except (TypeError, ValueError):
            self.pipe_in_hole_ft = None
        # The entered Pipe in Hole is the current bit depth of the flat string:
        # initialise the held depth from it (promt.txt §3 idle bit = pipe in hole).
        self._bit_hold_ft = self.pipe_in_hole_ft
        self._bit_track_block_ft = None
        self._bit_state = "NONE"
        self._bit_want = None
        self._bit_confirm_ct = 0

    def set_pipe_in_hole(self, value):
        """Save a new Pipe in Hole value (ft) to settings and runtime state.
        Must be a non-negative number. Returns True on success."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v < 0.0:
            return False
        self.pipe_in_hole_ft = v
        save_settings({"pipe_in_hole": v})
        # Reset the bit track to the new flat-string depth.
        self._bit_hold_ft = v
        self._bit_track_block_ft = None
        self._bit_state = "NONE"
        self._bit_want = None
        self._bit_confirm_ct = 0
        return True

    # -- SLIP WINDOW (promt.txt §18 hysteresis) ----------------------------
    def _load_slip_window(self):
        """Load the saved SLIP WINDOW (seconds) from settings. None = the
        built-in state-stability debounce (BIT_POS_STATE_STABLE_TICKS, so
        existing behaviour is unchanged until the operator sets a value)."""
        val = self._settings.get("slip_window_sec")
        if val is None:
            self.slip_window_sec = None
            return
        try:
            v = float(val)
            self.slip_window_sec = v if v > 0.0 else None
        except (TypeError, ValueError):
            self.slip_window_sec = None

    def _slip_stable_ticks(self):
        """Effective state-stability debounce in refresh ticks (50 ms each) for
        the bit TRACK/HOLD transition. Never relaxes below the built-in
        BIT_POS_STATE_STABLE_TICKS baseline; the operator slip window only ever
        extends it."""
        builtin = max(1, BIT_POS_STATE_STABLE_TICKS)
        if self.slip_window_sec is None or self.slip_window_sec <= 0.0:
            return builtin
        return max(builtin,
                   int(round(float(self.slip_window_sec) * 1000.0 / MS_PER_SAMPLE)))

    def set_slip_window(self, value):
        """Save a new SLIP WINDOW (seconds) to settings and runtime state.
        Must be a positive number. Returns True on success."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v <= 0.0:
            return False
        self.slip_window_sec = v
        save_settings({"slip_window_sec": v})
        return True

    # -- SLIP WINDOW LOAD / TIME confirmation (slip_window.txt) -------------
    @property
    def slip_window_enabled(self):
        """True when BOTH Slip Window Load (k-lb) and Slip Window Time
        (seconds) are configured (load >= 0, time > 0). While enabled the
        confirmation machine gates Bit Position movement."""
        lk = self.slip_window_load_klb
        ts = self.slip_window_time_sec
        return (lk is not None and ts is not None
                and float(lk) >= 0.0 and float(ts) > 0.0)

    @property
    def slip_window_effective_load_klb(self):
        """Effective Slip Window Load (klb): the configured (input) Slip
        Window Load PLUS the Hookload MINIMUM VALUE — the channel-0 two-point
        calibration low point (SENSOR_CONFIG[0] cal_val_lo). The confirmation
        gate compares the live hookload against this combined threshold, and
        the SLIP WINDOW LOAD readout shows it (slip window load = input value
        + minimum value). None when the feature is unconfigured; the minimum
        defaults to 0 when no hookload calibration is present."""
        raw = self.slip_window_load_klb
        if raw is None:
            return None
        lo = SENSOR_CONFIG[0].get("cal_val_lo")
        try:
            minimum = float(lo) if lo is not None else 0.0
        except (TypeError, ValueError):
            minimum = 0.0
        return float(raw) + minimum

    def _load_slip_window_config(self):
        """Restore Slip Window Load (k-lb) and Time (seconds) from settings."""
        load_klb = self._settings.get("slip_window_load_klb")
        try:
            self.slip_window_load_klb = (float(load_klb) if load_klb is not None
                                         else None)
        except (TypeError, ValueError):
            self.slip_window_load_klb = None
        time_sec = self._settings.get("slip_window_time_sec")
        try:
            self.slip_window_time_sec = (float(time_sec) if time_sec is not None
                                         else None)
        except (TypeError, ValueError):
            self.slip_window_time_sec = None
        self._reset_slip_window()

    def set_slip_window_load(self, value):
        """Save a new Slip Window Load (k-lb). Non-negative number. Returns
        True on success; the confirmation state is re-armed."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v < 0.0:
            return False
        self.slip_window_load_klb = v
        save_settings({"slip_window_load_klb": v})
        self._reset_slip_window()
        return True

    def set_slip_window_time(self, value):
        """Save a new Slip Window Time (seconds). Positive number. Returns
        True on success; the confirmation state is re-armed."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v <= 0.0:
            return False
        self.slip_window_time_sec = v
        save_settings({"slip_window_time_sec": v})
        self._reset_slip_window()
        return True

    def _reset_slip_window(self):
        """Forget any in-progress/confirmed slip state (settings changed)."""
        self._slip_state = "NOT ACTIVE"
        self._slip_since = None

    @property
    def hookload_klb(self):
        """Live Hookload (klb) from the Analog Monitor's channel-0 linear
        conversion (SENSOR_CONFIG[0], 0-5 V -> cal_min..cal_max). This is the
        single source of hookload for the Slip Window confirmation machine and
        the bit-position TRACK/HOLD gate (no separate hookload engine)."""
        cfg = SENSOR_CONFIG[0]
        v = float(self.analog_voltages[0] or 0.0)
        return float(cfg["cal_min"]) + (v / 5.0) * (
            float(cfg["cal_max"]) - float(cfg["cal_min"]))

    def _update_slip_window(self):
        """Advance the Slip Window confirmation machine OFF the live analog
        hookload (slip_window.txt §16). No second hookload calculation:
        compares the channel-0 klb value directly with the Slip Window Load
        threshold.

          NOT ACTIVE:  hookload < load          -> timer 0, bit constant
          CONFIRMING:  hookload >= load + timer running; on elapsed span ->
                       CONFIRMED (bit movement enabled immediately)
          CONFIRMED:   hookload >= load -> stays confirmed; a drop below the
                       threshold first leaves the confirmed state but PRESERVES
                       the accumulated bit position (re-confirmation is then
                       required for movement to resume — slip_window.txt §15).
        """
        if not self.slip_window_enabled:
            self._slip_state = "NOT ACTIVE"
            self._slip_since = None
            return
        hk = self.hookload_klb
        load_klb = float(self.slip_window_effective_load_klb)
        if hk < load_klb:
            if self._slip_state == "CONFIRMED":
                # A confirmed cycle later loses load: leave the confirmed state
                # (bit position is preserved by the caller's HOLD path).
                self._slip_state = "NOT ACTIVE"
            else:
                self._slip_state = "NOT ACTIVE"
            self._slip_since = None
            return
        now = self._slip_clock()
        if self._slip_state == "CONFIRMING":
            if self._slip_since is not None and \
                    (now - self._slip_since) >= float(self.slip_window_time_sec):
                self._slip_state = "CONFIRMED"
            return
        if self._slip_state == "CONFIRMED":
            return
        # First time at/above threshold in this cycle: start from zero.
        self._slip_state = "CONFIRMING"
        self._slip_since = now

    @property
    def slip_window_status(self):
        """Slip Window confirmation status: "NOT ACTIVE" | "CONFIRMING" |
        "CONFIRMED". Reports NOT ACTIVE when the feature is unconfigured."""
        return self._slip_state if self.slip_window_enabled else "NOT ACTIVE"

    @property
    def slip_window_timer_sec(self):
        """Elapsed Slip Window confirmation time (seconds), capped at the
        configured span. 0 outside CONFIRMING/CONFIRMED."""
        if not self.slip_window_enabled:
            return 0.0
        if self._slip_state == "CONFIRMING":
            if self._slip_since is None:
                return 0.0
            return min(float(self.slip_window_time_sec),
                       max(0.0, self._slip_clock() - self._slip_since))
        if self._slip_state == "CONFIRMED":
            return float(self.slip_window_time_sec)
        return 0.0

    @property
    def slip_window_active(self):
        """True only in the CONFIRMED state (bit movement enabled)."""
        return self.slip_window_enabled and self._slip_state == "CONFIRMED"

    @property
    def string_length_ft(self):
        """String Length (ft), in feet, tracked with pipe movement (promt.txt):
        String Length = Bit Position + Block Position (promt.txt §1/§16). The
        bit depth is the authoritative, state-driven value from bit_position_ft
        (frozen during HOLD, accumulated from the actual block delta during
        TRACK), so this readout is always exactly Block + Bit, with no separate
        or conflicting calculation. Before the first pipe trip the entered Pipe
        in Hole is the bit depth, giving the historical Pipe in Hole + Block
        Position readout. Returns None when Pipe in Hole is not configured."""
        if self.pipe_in_hole_ft is None:
            return None
        bp = float(self.block_position_ft or 0.0)
        base = self._bit_hold_ft if self._bit_hold_ft is not None else float(self.pipe_in_hole_ft)
        return bp + float(base)

    @property
    def bit_position_state(self):
        """Current BIT POSITION state (promt1.txt): "NONE" | "HOLD" | "TRACK"."""
        return self._bit_state

    @property
    def bit_position_ft(self):
        """Hookload-conditioned BIT POSITION (promt.txt), in feet. Pipe load is
        proven by the Analog Monitor's live channel-0 Hookload (single hookload
        source; no separate hookload engine). Gate:
          * Slip Window ENABLED — the Slip Window confirmation machine governs:
            TRACK only once the hookload has held >= Slip Window Load for the
            whole Slip Window Time (CONFIRMED); otherwise HOLD.
          * Slip Window DISABLED — TRACK whenever the live hookload is above 0
            (pipe load proven), HOLD otherwise.

          * HOLD (hookload fails to prove pipe load): returns the frozen
            last-valid depth so the bit never moves with small block changes
            (promt.txt §3/§14).
          * TRACK (pipe load proven): the bit is driven by the ACTUAL Block
            Position delta each refresh (promt.txt §4/§13/§15/§16): Block Delta
            = Current Block - Previous Block, then New Bit = Previous Bit -
            Block Delta. The previous block is re-anchored every time the state
            re-enters TRACK from the live position, so the transition starts
            from the current frozen depth with no stale-hold jump, and unloaded
            block movement is never added to the bit (promt.txt §12). Never
            fixed increments; full float precision; bit never negative.

        Before the first trip the held depth is the entered Pipe in Hole (the
        flat string), so the bit starts from a valid value. Returns None only
        when Pipe in Hole is not configured (state NONE).
        """
        if self.pipe_in_hole_ft is None:
            self._bit_state = "NONE"
            self._bit_want = "NONE"
            return None
        hk = self.hookload_klb
        if self.slip_window_enabled:
            # Slip Window confirmation (slip_window.txt): Bit Position stays
            # constant until Hookload has held >= Slip Window Load for the
            # whole Slip Window Time (CONFIRMED). Comparing the analog
            # channel-0 klb value directly with the Slip Window Load threshold
            # — no second hookload calculation system.
            self._update_slip_window()
            want = "TRACK" if self._slip_state == "CONFIRMED" else "HOLD"
        elif hk <= 0.0:
            want = "HOLD"
        else:
            want = "TRACK"
        # Debounce: the requested state must persist N consecutive refreshes
        # before it is applied (promt.txt §18 stability / hysteresis). With the
        # Slip Window confirmation active, its span already provides that
        # stability, so the TRACK/HOLD flip mirrors the confirmation state
        # exactly (no extra tick count on top of the confirmation time).
        prev_state = self._bit_state
        if self.slip_window_enabled:
            self._bit_state = want
        elif want == self._bit_want:
            self._bit_confirm_ct += 1
        else:
            self._bit_want = want
            self._bit_confirm_ct = 1
        if (not self.slip_window_enabled
                and self._bit_confirm_ct >= self._slip_stable_ticks()):
            self._bit_state = want
        if self._bit_state == "TRACK":
            # The bit is driven ONLY by the actual Block Position delta
            # (promt.txt §4/§13/§15/§16) — never by fixed increments and never
            # by continuously recalculating it from a String Length reference:
            #   Block Delta  = Current Block - Previous Block
            #   Bit Delta    = -Block Delta
            #   New Bit      = Previous Bit - Block Delta
            bp = float(self.block_position_ft or 0.0)
            if prev_state != "TRACK" or self._bit_track_block_ft is None:
                # (Re-)enter TRACK: anchor the previous block at the live
                # position so the bit continues from the current frozen depth
                # with no jump on re-engagement (promt.txt §12). The unloaded
                # block movement is NOT added to the bit (promt.txt §3/§14).
                self._bit_track_block_ft = bp
            else:
                block_delta = bp - self._bit_track_block_ft
                if block_delta != 0.0:
                    self._bit_track_block_ft = bp
                    bit = self._bit_hold_ft
                    if bit is None:
                        bit = float(self.pipe_in_hole_ft or 0.0)
                    bit = bit - block_delta
                    self._bit_hold_ft = bit if bit >= 0.0 else 0.0
            hold = self._bit_hold_ft
            if hold is None:
                hold = float(self.pipe_in_hole_ft or 0.0)
                self._bit_hold_ft = hold
            self.pipe_in_hole_ft = hold
            self._record_hole_depth(hold)
            return hold
        hold = self._bit_hold_ft
        if hold is None:
            hold = float(self.pipe_in_hole_ft or 0.0)
            self._bit_hold_ft = hold
        self.pipe_in_hole_ft = hold
        self._record_hole_depth(hold)
        return hold

    def _record_hole_depth(self, hold):
        """Record the latest committed bit depth for the HOLE DEPTH box. Called
        ONLY from bit_position_ft (the single per-tick source), so reading the
        bit advances the state machine and the running max exactly once per
        refresh."""
        self._hole_last_bit_ft = hold
        if (self._hole_depth_max_ft is None
                or float(hold) > self._hole_depth_max_ft):
            self._hole_depth_max_ft = float(hold)

    @property
    def hole_depth_ft(self):
        """HOLE DEPTH (ft): the deepest Bit Position the string has reached —
        an accumulated running maximum that is NEVER decreased. The max is
        recorded inside bit_position_ft (the single per-tick read), so this is
        a pure getter with no side effects: the depth HOLDS while tripping /
        coming out of the hole and only advances again when the bit runs
        deeper. None before any bit depth has been recorded."""
        return self._hole_depth_max_ft

    @property
    def hole_last_bit_ft(self):
        """The most recent committed bit depth (ft), recorded by
        bit_position_ft for the HOLE DEPTH status line. None before any depth."""
        return self._hole_last_bit_ft

    # -- MEASURED DEPTH / TVD / SAMPLE LAG DEPTH (derived display values) --
    # promt3 §15: this rig has no standalone measured-depth, directional-survey
    # or lag engine, so the Dashboard readouts below are single-source DISPLAY
    # derivations that reuse the existing engines ONLY — never a second
    # calculation, and never a hard-coded value:
    #   * Measured Depth     -> the existing Bit Position/Bit Depth engine
    #     (bit_position_ft): in this vertical-string block-position system the
    #     well measured depth at the drill string equals the bit depth.
    #   * TVD                -> Measured Depth (no directional survey exists, so
    #     the well is treated as vertical; flagged in the Dashboard UI).
    #   * Sample Lag Depth   -> Measured Depth - operator lag offset (feet),
    #     i.e. the depth at which the sample now returning at surface was cut.
    # IMPORTANT: measured_depth_ft reads bit_position_ft, the side-effectful
    # per-tick engine read. Callers must read it ONCE per refresh cycle and
    # reuse the returned value for the related boxes.
    @property
    def measured_depth_ft(self):
        return self.bit_position_ft

    @property
    def tvd_ft(self):
        return self.measured_depth_ft

    @property
    def sample_lag_depth_ft(self):
        md = self.measured_depth_ft
        if md is None:
            return None
        off = float(getattr(self, "sample_lag_offset_ft", 0.0) or 0.0)
        return max(0.0, md - off)

    # -- audit export ----------------------------------------------------------
    def export_audit(self, fmt):
        dlg = QFileDialog()
        default = os.path.join(os.path.expanduser("~"),
                               f"audit_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.{fmt}")
        path, _ = dlg.getSaveFileName(self, f"Export audit log as {fmt}", default,
                                      f"{fmt.upper()} files (*.{fmt})")
        if not path:
            return
        try:
            n = self.audit.export_csv(path) if fmt == "csv" else self.audit.export_pdf(path)
            QMessageBox.information(self.window(), "Export",
                                    f"Exported {n} records to\n{path}")
        except Exception as e:
            QMessageBox.critical(self.window(), "Export failed", str(e))

    # -- demo ------------------------------------------------------------------
    def _demo_tick(self):
        self._demo_counter += 1
        if self._demo_counter >= 60:
            self._demo_counter = 0
            self._demo_analog_targets = [random.uniform(0.2, 4.8) for _ in range(NUM_ANALOG)]

        if not self._demo_init:
            # Seed the DEMO table only ONCE — and only when the device does not
            # already hold a calibration. If the operator has calibrated / loaded
            # values (or the seed already ran this session), never clobber them
            # with the hardcoded defaults (previously a demo off/on toggle
            # re-seeded the defaults over the operator's saved inputs).
            self._demo_init = True
            if not self.cal_points():
                # Demo calibration: rebase so the encoder count starts from 0
                # and spans up to DEMO_MAX_COUNT — subtract the lowest anchor's
                # counter (so the block bottoms out at tick 0), then scale so
                # the highest anchor stops at DEMO_MAX_COUNT. Physical positions
                # are unchanged; pulses/foot are recomputed from the scaled
                # anchors.
                demo_pts = sorted(DEMO_CAL_POINTS, key=lambda p: p["counter"])
                base = int(demo_pts[0]["counter"])
                scaled = []
                for pt in demo_pts:
                    v = int(pt["counter"]) - base
                    scaled.append(max(v, 0))
                top = max(scaled) or 1
                scale = DEMO_MAX_COUNT / float(top)
                mapped = [int(round(v * scale)) for v in scaled]
                seed = {}
                for i, pt in enumerate(demo_pts, start=1):
                    seed[f"calCounter{i}"] = mapped[i - 1]
                    seed[f"calPosition{i}"] = float(pt["position"])
                # Per-interval counts/ft from the scaled demo anchors (display
                # only). countsPerFoot1..3; the top layer (4) has no interval.
                for i in range(1, FW_MAX_CAL_POINTS + 1):
                    if i <= len(demo_pts) - 1:
                        c1, p1 = mapped[i - 1], demo_pts[i - 1]["position"]
                        c2, p2 = mapped[i], demo_pts[i]["position"]
                        cpf = (c2 - c1) / (p2 - p1) if abs(p2 - p1) > 1e-9 else 0.0
                        seed[f"countsPerFoot{i}"] = round(cpf, 2)
                    else:
                        seed[f"countsPerFoot{i}"] = 0.0
                seed["witsCorrectionFt"] = DEMO_WITS_CORRECTION
                seed["encoderPolarity"] = 1
                seed["confirmed"] = True
                self.device.update(seed)

                # Demo String Length: Measured Depth / TVD / Bit Depth / Sample
                # Lag Depth are undefined until the pipe length is known, so a
                # demo session needs one or those boxes read '--'. Set the
                # RUNTIME fields only (exactly like _load_pipe_in_hole), never
                # save_settings(): a demo value must not overwrite the
                # operator's real Pipe in Hole.
                if self.pipe_in_hole_ft is None:
                    self.pipe_in_hole_ft = float(DEMO_PIPE_IN_HOLE_FT)
                    self._bit_hold_ft = float(DEMO_PIPE_IN_HOLE_FT)
                    self._bit_track_block_ft = None
                    self._bit_state = "NONE"
                    self._bit_want = None
                    self._bit_confirm_ct = 0
        self.device["uptime_s"] = self._demo_counter + 100

        # Analog + SPM/RPM simulated signals (mirrors the reported dashboard).
        legacy = {}
        for i in range(NUM_ANALOG):
            noisy = self._demo_analog_targets[i] + random.uniform(-0.03, 0.03)
            legacy[f"sensor{i+1}"] = round(max(0.0, min(5.0, noisy)), 3)
        for i, cfg in enumerate(SPM_CONFIG):
            self._demo_spm_counter[i] += random.choice([0, 0, 0, 1])
            legacy[cfg["counter_key"]] = self._demo_spm_counter[i]
            legacy[cfg["spm_key"]] = round(random.uniform(20, 90), 1)
        for i, cfg in enumerate(RPM_CONFIG):
            self._demo_rpm_counter[i] += random.choice([0, 0, 0, 1])
            legacy[cfg["counter_key"]] = self._demo_rpm_counter[i]
            legacy[cfg["rpm_key"]] = round(random.uniform(40, 160), 1)
        self._apply_legacy(legacy)

        # Block position: drive a simulated encoder COUNTER across the full
        # calibrated stroke and compute the reported block position FROM the
        # ACTIVE calibration (current tick -> calculated position). This
        # emulates the real flow end-to-end, so every saved pulse/feet anchor
        # immediately and visibly changes the calculated position — old saved
        # values are never reused (the report reads `cal_points()` live).
        pts = self.cal_points()
        if len(pts) >= 2:
            # Demo counter ALWAYS sweeps the full range up to DEMO_MAX_COUNT
            # (100000) regardless of where the top calibration anchor sits, so
            # the demo encoder count visibly counts 0 -> 100000 -> 0.
            frac = 0.5 - 0.5 * math.cos(self._demo_counter / 7.0)
            frac = max(0.0, min(1.0, frac))
            ticks = int(round(frac * DEMO_MAX_COUNT))
            pos = self.calibrated_position(ticks)
            if pos is None:
                # Beyond the calibrated span: keep the true demed counter value
                # (do NOT collapse it to 0) and extrapolate along the first/last
                # calibrated interval so the demo count reads to 100000 while the
                # position stays a sensible number, not OUT_OF_RANGE.
                if ticks < pts[0][0]:
                    c0, p0 = pts[0]
                    c1, p1 = pts[1]
                else:
                    c0, p0 = pts[-2]
                    c1, p1 = pts[-1]
                if c1 == c0:
                    pos = p1
                else:
                    pos = p0 + (ticks - c0) / (c1 - c0) * (p1 - p0)
                cal_status = "VALID"
                cal_in_range = 1
            else:
                cal_status = "VALID"
                cal_in_range = 1
        else:
            ticks, pos = 0, 0.0
            cal_status = "NO_CALIBRATION"
            cal_in_range = 0

        # Apply any active RESET FEET reference so the demo sweep tracks from
        # the operator's chosen starting feet (position continuity).
        pos = self.display_position_from_calibration(pos)

        prev = getattr(self, "_demo_prev_pos", pos)
        dt_sec = MS_PER_SAMPLE / 1000.0
        vel = (pos - prev) / dt_sec * 60.0 if dt_sec > 0 else 0.0
        self._demo_prev_pos = pos
        # Direction is derived from the LIVE tick delta (UP/DOWN only, never
        # NONE). The velocity value above is still used for the display and for
        # the separate ON BOTTOM lamp, not for direction.
        self._derive_direction(ticks)
        direction = self.direction
        on_bottom = (abs(vel) <= 0.05 and ticks <= 1)
        current_layer = layers_service.derive_layer(
            ticks,
            [p[0] for p in self.cal_points()])

        report = {
            "type": "report",
            "currentTicks": ticks,
            "blockPositionFt": round(pos, 3),
            "velocityFtMin": round(vel, 1),
            "direction": direction,
            "onBottom": "true" if on_bottom else "false",
            "calStatus": cal_status,
            "calInRange": cal_in_range,
            "currentLayer": current_layer,
            "calCounter1": self.device.get("calCounter1", 0),
            "calCounter2": self.device.get("calCounter2", 0),
            "calCounter3": self.device.get("calCounter3", 0),
            "calCounter4": self.device.get("calCounter4", 0),
            "calPosition1": self.device.get("calPosition1", 0.0),
            "calPosition2": self.device.get("calPosition2", 0.0),
            "calPosition3": self.device.get("calPosition3", 0.0),
            "calPosition4": self.device.get("calPosition4", 0.0),
            "countsPerFoot1": self.device.get("countsPerFoot1", 0.0),
            "countsPerFoot2": self.device.get("countsPerFoot2", 0.0),
            "countsPerFoot3": self.device.get("countsPerFoot3", 0.0),
            "countsPerFoot4": self.device.get("countsPerFoot4", 0.0),
            "witsCorrectionFt": self.device.get("witsCorrectionFt", FW_WITS_CORRECTION_DEFAULT),
            "encoderPolarity": self.device.get("encoderPolarity", 1),
            "uptime_s": self._demo_counter + 100,
            "sequence": self._demo_counter,
        }
        if self._demo_counter == 59 and not getattr(self, "_demo_rec_done", False):
            self.encoder_recovered = True
            self._demo_rec_done = True
        self._apply_protocol(report)

    # -- close -------------------------------------------------------------------
    def closeEvent(self, event):
        if self.serial and self.serial.is_open:
            self.serial.close()
        try:
            geo = self.geometry()
            maximized = self.isMaximized()
            save_settings({
                "geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
                "maximized": maximized,
                "role": ROLE_OPERATOR,   # role never persists as Engineer (promt.txt)
            })
        except Exception:
            pass
        self.audit.record("SESSION", "dashboard closed", self.roles.role())
        try:
            self.historian.purge()
        except Exception:
            pass
        event.accept()


def _serial_ports_present():
    """True when at least one serial port exists, i.e. a device may be attached.

    Used at startup only, to decide whether simulated values are appropriate.
    Never raises: if the port list cannot be read we assume hardware MIGHT be
    present and leave the Dashboard honest ('--') rather than showing
    simulated numbers next to a real device.
    """
    try:
        from serial.tools import list_ports
        return any(True for _ in list_ports.comports())
    except Exception:
        return True


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, BG_MAIN)
    pal.setColor(QPalette.WindowText, TEXT_DARK)
    pal.setColor(QPalette.Base, BG_CARD)
    pal.setColor(QPalette.AlternateBase, BG_INNER)
    pal.setColor(QPalette.Text, TEXT_DARK)
    pal.setColor(QPalette.Button, BG_INNER)
    pal.setColor(QPalette.ButtonText, TEXT_DARK)
    app.setPalette(pal)
    win = PumpDashboard()
    win.show()
    # No device attached: every Dashboard box would read '--'. Start the app's
    # EXISTING Demo Mode so the operator sees live simulated values instead (no
    # separate simulation is introduced). Demo is clearly flagged: the status
    # bar reads "DEMO MODE" and the boxes read "SIMULATED". The operator stops
    # it with the Demo Mode button, and connecting to a real device switches it
    # off automatically (_toggle_serial).
    if not _serial_ports_present():
        win._toggle_demo()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()