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
#  direction (UP/ON BOTTOM/DOWN/STOPPED), calStatus and currentLayer. The
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
    BG_MAIN, BG_CARD, BG_INNER, TEXT_DARK, TEXT_MID, BORDER,
    NUM_ANALOG, SENSOR_CONFIG, SPM_CONFIG, RPM_CONFIG,
    MS_PER_SAMPLE, SAMPLES_PER_SEC, WINDOW_SECS, RETAIN_SAMPLES, HISTORY_LEN,
    SESSION_START, AUDIT_DB_PATH, HISTORIAN_DB_PATH, HISTORIAN_RETENTION_DAYS,
    STALE_TIMEOUT_S, LINK_TIMEOUT_S, RECONNECT_BACKOFF_S,
    DEVICE_DEFAULTS, CMD_ACK_TIMEOUT_S,
    FW_MAX_CAL_POINTS, FW_WITS_CORRECTION_DEFAULT,
    DEMO_CAL_POINTS, DEMO_WITS_CORRECTION, DEMO_MAX_COUNT,
)
from scarlet_test_panel.utils import resource_path
from scarlet_test_panel.widgets.tab_bar import TabBar
from scarlet_test_panel.widgets.status_bar import StatusBar
from scarlet_test_panel.tabs.analog_tab import AnalogTab
from scarlet_test_panel.tabs.graph_tab import GraphTab
from scarlet_test_panel.tabs.spm_rpm_tab import StrokeRpmTab
from scarlet_test_panel.tabs.block_position_tab import BlockPositionTab

# --- Design tokens (light/dark theme) ---
import scarlet_test_panel.theme as theme
from scarlet_test_panel.theme import current as theme_current

# --- Production services ---
from scarlet_test_panel.services.protocol import decode_message, BadChecksum, MessageError
from scarlet_test_panel.services.security import RoleManager, ROLE_OPERATOR
from scarlet_test_panel.services.audit import AuditLog
from scarlet_test_panel.services.historian import Historian
from scarlet_test_panel.services.commands import CommandTracker
from scarlet_test_panel.services.settings import load as load_settings, save as save_settings
from scarlet_test_panel.services import layers as layers_service

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
        self.setWindowTitle("Industrial Rig Block Position Monitor")
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
        theme.set_theme(self._settings.get("theme", "light"))
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
        self.histories = [deque(maxlen=HISTORY_LEN) for _ in range(NUM_ANALOG)]
        for h in self.histories:
            h.append((0.0, 0.0, datetime.datetime.now()))
        self.epoch_samples = [0] * NUM_ANALOG

        # SPM / RPM state
        self.spm_raw_counter = [0.0] * 4
        self.spm_offset = [0.0] * 4
        self.spm_values = [0.0] * 4
        self.rpm_raw_counter = [0.0] * 2
        self.rpm_offset = [0.0] * 2
        self.rpm_values = [0.0] * 2

        # ── Block Position Monitor live telemetry ─────────────
        # Values here DISPLAY what the firmware reported (report/status).
        # The firmware is the authoritative measurement engine.
        self.current_ticks = 0
        self.block_position_ft = 0.0
        self.velocity_ft_min = 0.0
        self.direction = "STOPPED"
        self.on_bottom = False          # stopped near the lowest calibrated anchor
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
        min_w, min_h = 1100, 700
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
        root.setContentsMargins(14, 14, 14, 10)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 4)
        top_bar.setSpacing(10)
        logo_label = QLabel()
        logo_pixmap = QPixmap(resource_path("scarletIcon.jpg"))
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaledToHeight(52, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pixmap)
        else:
            lp2 = QPixmap(resource_path("scarletIcon.ico"))
            if not lp2.isNull():
                lp2 = lp2.scaledToHeight(52, Qt.SmoothTransformation)
                logo_label.setPixmap(lp2)
        logo_label.setStyleSheet("background: transparent;")
        logo_label.setFixedWidth(0 if logo_pixmap.isNull() else logo_pixmap.width())
        title = QLabel("INDUSTRIAL RIG\nBLOCK POSITION MONITOR")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Segoe UI'; font-size:16px; "
            f"font-weight:bold; letter-spacing:2px; padding:0; background:transparent;")
        top_bar.addWidget(logo_label)
        top_bar.addWidget(title)
        top_bar.addStretch()

        # Header status cluster: connection + data state + clock + user.
        self.hdr_dt_lbl = QLabel("")
        self.hdr_dt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.hdr_dt_lbl.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px; "
            "background:transparent;")
        self.hdr_user_lbl = QLabel("User: --")
        self.hdr_user_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.hdr_user_lbl.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px; "
            "background:transparent;")
        self.hdr_status_lbl = QLabel("NORMAL")
        self.hdr_status_lbl.setAlignment(Qt.AlignCenter)
        self.hdr_status_lbl.setStyleSheet(
            "color:#FFFFFF; background:#1E7A3E; font-family:'Segoe UI'; "
            "font-size:11px; font-weight:bold; border-radius:9px; padding:5px 12px;")
        self.hdr_conn_lbl = QLabel("DISCONNECTED")
        self.hdr_conn_lbl.setAlignment(Qt.AlignCenter)
        self.hdr_conn_lbl.setStyleSheet(
            "color:#FFFFFF; background:#C0392B; font-family:'Segoe UI'; "
            "font-size:11px; font-weight:bold; border-radius:9px; padding:5px 12px;")
        top_bar.addWidget(self.hdr_dt_lbl)
        top_bar.addWidget(self.hdr_user_lbl)
        top_bar.addWidget(self.hdr_status_lbl)
        top_bar.addWidget(self.hdr_conn_lbl)
        root.addLayout(top_bar)

        self.tab_bar = TabBar(["  Analog Signals  ", "  Graph  ", "  SPM / RPM  ",
                               "  Block Position  ", "  Settings  ", "  Diagnostics  "])
        root.addWidget(self.tab_bar)
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

        self.graph_tab = GraphTab(self.histories, self.epoch_samples)
        self.stack.addWidget(self.graph_tab)
        self.graph_card = self.graph_tab.graph_card

        self.stroke_tab = StrokeRpmTab(self)
        self.stack.addWidget(self.stroke_tab)

        self.block_tab = BlockPositionTab(self, self.roles, self.audit)
        self.stack.addWidget(self.block_tab)

        from scarlet_test_panel.tabs.about_tab import AboutTab
        self.about_tab = AboutTab(self, self.audit)
        self.stack.addWidget(self.about_tab)

        from scarlet_test_panel.tabs.diagnostics_tab import DiagnosticsTab
        self.diagnostics_tab = DiagnosticsTab(self, self.roles)
        self.stack.addWidget(self.diagnostics_tab)

        self.tab_bar.on_change(self.stack.setCurrentIndex)

        # Diagnostics/engineering page is Supervisor/Engineer-only.
        def _apply_diag_role(role):
            visible = self.roles.can_command()
            self.tab_bar.set_tab_visible(5, visible)
            if not visible and self.stack.currentIndex() == 5:
                self.stack.setCurrentIndex(3)
        self.roles.subscribe(_apply_diag_role)
        _apply_diag_role(self.roles.role())

        self.status = StatusBar(self.roles, self.audit, self)
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
        baud = int(self.status.baud_cb.currentText()) if self.status.baud_cb.currentText().isdigit() else 9600
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
        if idx == 1:
            self.graph_card.refresh()
        elif idx == 2:
            self.stroke_tab.refresh()
        elif idx == 3:
            self.block_tab.refresh()
        elif idx == 4:
            self.about_tab.refresh()
        elif idx == 5:
            self.diagnostics_tab.refresh()
        self.status.refresh_state(self._data_stale, self._link_down)

    def _update_header(self):
        self.hdr_dt_lbl.setText(datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.hdr_user_lbl.setText(f"User: {self.roles.role().upper()}")

        if self._link_down:
            text, bg = "DISCONNECTED", "#C0392B"
        elif self._data_stale:
            text, bg = "STALE DATA", "#8A5A00"
        else:
            text, bg = "NORMAL", "#1E7A3E"
        self.hdr_status_lbl.setText(text)
        self.hdr_status_lbl.setStyleSheet(
            f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; "
            "font-size:11px; font-weight:bold; border-radius:9px; padding:5px 12px;")

        conn = bool(self.serial and self.serial.is_open and not self._link_down)
        self.hdr_conn_lbl.setText("CONNECTED" if conn else "DISCONNECTED")
        self.hdr_conn_lbl.setStyleSheet(
            f"color:#FFFFFF; background:{'#1E7A3E' if conn else '#C0392B'}; "
            "font-family:'Segoe UI'; font-size:11px; font-weight:bold; "
            "border-radius:9px; padding:5px 12px;")

    # -- freshness / stale & link detection -----------------------------
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
        baud = int(self.status.baud_cb.currentText()) if self.status.baud_cb.currentText().isdigit() else 9600
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

        if mtype == "ack":
            self._handle_ack(data)
            return
        if mtype == "status":
            self._handle_status(data)
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
        if "direction" in data:
            self.direction = str(data["direction"])
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
        if "direction" in data:
            self.direction = str(data["direction"])
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
            if "direction" in data:
                self.direction = str(data["direction"])
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
        elif ok and verified is False:
            # No in-flight request matched (physical button press). Still a
            # legitimate, device-confirmed reset.
            self._set_reset_state(True, "RESET SUCCESSFUL — counter zeroed (button)")
            self.audit.record("RESET",
                              "device confirmed counter reset via physical button "
                              "(currentTicks=0)",
                              "system", uptime=self.encoder_uptime_s)
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
        # (older firmware) fall back to the reset invariants: velocity 0,
        # direction STOPPED.
        if "blockPositionFt" in data:
            self._set_block_position(float(data["blockPositionFt"]))
        self.velocity_ft_min = float(data.get("velocityFtMin") or 0.0)
        self.direction = str(data.get("direction") or "STOPPED")
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
        the device confirms currentTicks == 0 and blockPositionFt == 0.00 ft.
        The saved calibration anchors are never touched; this only re-establishes
        the temporary runtime zero reference (tick=0 -> 0.00 ft)."""
        self._track_uptime(data)
        raw_ticks = data.get("currentTicks")
        raw_pos = data.get("blockPositionFt")
        ok = (raw_ticks is not None and int(float(raw_ticks)) == 0
              and raw_pos is not None and abs(float(raw_pos)) < 1e-9)
        source = data.get("source") or "device"
        verified = self._cmd.on_reset_feet_ack(data)

        if verified == "verified" or (ok and verified is False):
            self._set_reset_state(
                True, "RESET FEET SUCCESSFUL — Encoder Tick: 0   "
                      "Block Position: 0.00 FT")
            self.audit.record("RESET_FEET",
                              f"device confirmed runtime reset (tick=0, position=0.00 ft) "
                              f"source={source} — calibration untouched",
                              "system", uptime=self.encoder_uptime_s)
        else:
            self._set_reset_state(
                False, "RESET FEET FAILED — device reported non-zero tick/position")
            self.audit.record("RESET_FEET_FAILED",
                              f"reset_feet_ack tick={raw_ticks} pos={raw_pos}",
                              "system", uptime=self.encoder_uptime_s)
        self.encoder_recovered = False
        if ok:
            self.current_ticks = 0
        # Immediately reflect the new runtime zero reference. NOTE: this sets
        # the runtime block position to 0.00 ft directly — it does NOT recompute
        # from calibration (the calibration table is intentionally left intact).
        self._set_block_position(0.0)
        self.velocity_ft_min = float(data.get("velocityFtMin") or 0.0)
        self.direction = str(data.get("direction") or "STOPPED")
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

    def _write_historian(self):
        self.historian.record(self.block_position_ft, int(self.current_ticks),
                              source="firmware", is_recovered=self.encoder_recovered)

    # -- analog --------------------------------------------------------------
    def _apply_legacy(self, data):
        """Legacy no-CRC sources (usually analog/SPM/RPM signals)."""
        self._last_valid_time = time.monotonic()
        self._data_stale = False
        self.status.set_link_quality(0.5)

        for i in range(NUM_ANALOG):
            key = f"sensor{i+1}"
            if key in data:
                try:
                    v = max(0.0, min(5.0, float(data[key])))
                    self._push_analog(i, v)
                except (TypeError, ValueError):
                    continue

        for i, cfg in enumerate(SPM_CONFIG):
            if cfg["counter_key"] in data:
                try:
                    self.spm_raw_counter[i] = float(data[cfg["counter_key"]])
                except (TypeError, ValueError):
                    pass
            if cfg["spm_key"] in data:
                try:
                    self.spm_values[i] = float(data[cfg["spm_key"]])
                except (TypeError, ValueError):
                    pass

        for i, cfg in enumerate(RPM_CONFIG):
            if cfg["counter_key"] in data:
                try:
                    self.rpm_raw_counter[i] = float(data[cfg["counter_key"]])
                except (TypeError, ValueError):
                    pass
            if cfg["rpm_key"] in data:
                try:
                    self.rpm_values[i] = float(data[cfg["rpm_key"]])
                except (TypeError, ValueError):
                    pass

    def _push_analog(self, i, v):
        cfg = SENSOR_CONFIG[i]
        cal = cfg["cal_min"] + (v / 5.0) * (cfg["cal_max"] - cfg["cal_min"])
        ts = datetime.datetime.now()
        self.analog_voltages[i] = v
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

        prev = getattr(self, "_demo_prev_pos", pos)
        dt_sec = MS_PER_SAMPLE / 1000.0
        vel = (pos - prev) / dt_sec * 60.0 if dt_sec > 0 else 0.0
        self._demo_prev_pos = pos
        if vel > 0.05:
            direction = "UP"
        elif vel < -0.05:
            direction = "DOWN"
        else:
            direction = "STOPPED"
        on_bottom = (direction == "STOPPED" and ticks <= 1)
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
                "role": self.roles.role(),
            })
        except Exception:
            pass
        self.audit.record("SESSION", "dashboard closed", self.roles.role())
        try:
            self.historian.purge()
        except Exception:
            pass
        event.accept()


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
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()