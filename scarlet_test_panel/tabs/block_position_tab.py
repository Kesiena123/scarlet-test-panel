"""Block Position Monitor operator page (promt1.txt).

Mirrors the industrial 'Block Position Monitor' HMI:

  ┌────────────────────────────┬──────────────────────────────────┐
  │ CURRENT MODE OF OPERATION  │ CURRENT BLOCK POSITION           │
  │   [ RUN ]  [ CALIBRATE ]   │        31.58 ft                  │
  ├────────────────────────────┼───────────────┬──────────────────┤
  │ BLOCK POSITION   31.58 ft  │ ENCODER       │ VELOCITY         │
  │ CURRENT LAYER    1         │ COUNTER       │ 0.00 ft/min      │
  │ DIRECTION    ▼ ON BOTTOM   │ 4437          │                  │
  ├────────────────────────────┴───────────────┴──────────────────┤
  │ CALIBRATION TABLE   (Layer | Initial Tape Reading | Counter | │
  │   Counts/ft)  4 operator rows, selectable                     │
  ├───────────────────────────────────────────────────────────────┤
  │  TREND  (BLOCK POSITION vs TIME)    [ 1 Minute ] [ 1 Hour ]    │
  ├───────────────────────────────────────────────────────────────┤
  │ RESET COUNTER │ LOAD SAVED CALIBRATION │ SAVE CALIBRATION │   │
  │ SET LAYER │ SET BLOCK HEIGHT │ SET WITS CORRECTION │           │
  └───────────────────────────────────────────────────────────────┘

Every telemetry value shown here is what the firmware REPORTED (the firmware
is the single authoritative measurement engine — the dashboard never computes
a competing position). Calibration is a piecewise-linear table of up to 4
anchors captured in CALIBRATE mode. Every setting change goes through the
two-stage REQUEST -> CONFIRM -> ACK(echo) -> VERIFY protocol and shows PENDING
until the device confirms, then SAVED, or FAILED on timeout. RESET COUNTER
zeroes the counter and preserves the physical position via a runtime reference
without re-basing or saving the calibration (calibration input never changes).
RESET SUCCESSFUL is shown only after the firmware's `reset_ack` reports
currentTicks == 0.

There are NO encoder-hardware concepts on this page (no PPR, no decoding mode,
no gear ratio, no wheel diameter — no countsPerFoot) — the operator works with
physical values and calibration anchors only.
"""

import datetime
import math
import time as _time

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QGroupBox,
    QPushButton, QDoubleSpinBox, QSpinBox, QRadioButton, QComboBox, QLineEdit,
    QFrame, QScrollArea, QMessageBox, QDialog, QFormLayout, QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontMetrics

from ..config import (
    ACCENT, BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_MID, TEXT_LITE,
    FW_MAX_CAL_POINTS,
)
from ..widgets.graph_plot import _IndustrialTrendGraph
from ..services.security import ROLE_SUPERVISOR
from ..services import layers as layers_service

# ── Industrial status colours (always paired with text, never color-only) ─
NORMAL_BG = "#1E7A3E"    # live / applied / saved / UP
WARN_BG = "#D47B0F"      # warning / recovered / pending / ON BOTTOM
FAULT_BG = "#C0392B"     # fault / failed
STALE_BG = "#8A5A00"     # stale data
NEUTRAL_BG = "#8C826F"   # waiting / stopped / DOWN
RUN_BG = "#1E7A3E"
CAL_BG = "#D47B0F"

CARD_BORDER = "#DCD3C4"
SECTION_TITLE = "#4C6B8A"

MIN_REF = -100000.0
MAX_REF = 1000000.0


class BlockPositionTab(QWidget):
    def __init__(self, main_window, roles, audit, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.roles = roles
        self.audit = audit
        self._layout_layers = layers_service.load_layers()

        # Dashboard-side mode of operation (RUN / CALIBRATE). This is NOT a
        # firmware mode — it is persisted dashboard state (settings ["op_mode"]).
        self.op_mode = str(self.mw._settings.get("op_mode", "RUN"))
        if self.op_mode not in ("RUN", "CALIBRATE"):
            self.op_mode = "RUN"

        # Selected calibration row (1-based layer) for the table editing.
        self._selected_row = 1

        # promt3: calibration save-status feedback state.
        #   _cal_msg_kind: "idle" | "modified" | "saved" | "error"
        self._cal_msg_kind = "idle"
        self._cal_msg_text = ""
        # Track a user edit since the last calibrate/save/load/status refresh so
        # the operator sees "CALIBRATION MODIFIED UNSAVED" until it is applied.
        self._cal_dirty = False
        # Last successfully-saved calibration (layer_pk, pulses, feet) — the
        # green SAVED banner shows THESE exact values until the next edit.
        self._last_saved = None
        # Confirm-on-the-CALIBRATE-button flash: the button itself briefly
        # becomes a green "SAVED ✓" state after each save, then reverts.
        self._save_flash_timer = QTimer(self)
        self._save_flash_timer.setSingleShot(True)
        self._save_flash_timer.timeout.connect(self._restore_calibrate_btn)
        # Current CALIBRATE-button flash phase: "saving" | "saved" | "failed" |
        # "idle". Drives progress/done/fail states so no premature "saved" shows.
        self._cal_save_phase = "idle"
        # In-progress verified-save bookkeeping (guards re-entrancy + stale msgs).
        self._cal_saving_active = False
        # promt3: once the operator edits any calibration cell, stop letting the
        # per-tick refresh overwrite their typed values. Cleared when the
        # calibration is explicitly saved / loaded / reset (or matches device).
        self._cal_editing = False

        # Per-operation command bookkeeping (PENDING / SAVED / FAILED).
        #  op kind -> { "reqs": set[int], "ok": None|bool, "detail": str }
        self._ops = {}

        # ── Scroll shell -------------------------------------------------
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        container = QWidget()
        container.setStyleSheet("background: transparent;")

        root = QVBoxLayout(container)
        root.setContentsMargins(2, 6, 14, 14)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; } "
            "QScrollBar:vertical { background: transparent; width: 12px; margin: 0; } "
            "QScrollBar::handle:vertical { background:#C8BDAD; border-radius:6px; min-height:30px; } "
            "QScrollBar::handle:vertical:hover { background:#B5A999; } "
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; } "
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }")
        scroll.setWidget(container)
        page_layout.addWidget(scroll)

        # ── Heading ------------------------------------------------------
        head = QHBoxLayout()
        title = QLabel("BLOCK POSITION MONITOR")
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Segoe UI'; font-size:20px; "
            "font-weight:bold; letter-spacing:2px; background:transparent;")
        subtitle = QLabel("live device-reported measurement  •  multi-point calibration")
        subtitle.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px; "
            "background:transparent;")
        head.addWidget(title)
        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        head.addLayout(right_col)
        head.addStretch()
        root.addLayout(head)

        # ── Mode of operation (RUN / CALIBRATE) card --------------------
        mode_group = QGroupBox("CURRENT MODE OF OPERATION")
        mode_group.setStyleSheet(self._group_box_style(SECTION_TITLE))
        mv = QHBoxLayout(mode_group)
        mv.setContentsMargins(14, 16, 14, 14)
        mv.setSpacing(12)
        self.run_btn = QPushButton("RUN")
        self.run_btn.setCheckable(True)
        self.cal_btn = QPushButton("CALIBRATE")
        self.cal_btn.setCheckable(True)
        for btn, handler in ((self.run_btn, lambda: self._set_mode("RUN")),
                             (self.cal_btn, lambda: self._set_mode("CALIBRATE"))):
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(handler)
            mv.addWidget(btn, 1)
        mv.addWidget(QLabel("(operation mode is dashboard state, not a firmware mode)"))
        root.addWidget(mode_group)
        self._apply_mode_buttons()

        # ── Primary readout grid -----------------------------------------
        grid = QGridLayout()
        grid.setSpacing(12)

        # BLOCK POSITION card
        pos_card = QGroupBox("BLOCK POSITION")
        pos_card.setStyleSheet(self._group_box_style())
        pv = QVBoxLayout(pos_card)
        pv.setContentsMargins(14, 16, 14, 12)
        pv.setSpacing(6)
        self.pos_value_lbl = QLabel("--")
        self.pos_value_lbl.setAlignment(Qt.AlignCenter)
        self.pos_value_lbl.setMinimumHeight(96)
        self.pos_value_lbl.setStyleSheet(self._figure_style())
        pv.addWidget(self.pos_value_lbl)
        self.cal_status_lbl = QLabel("● NO CALIBRATION")
        self.cal_status_lbl.setAlignment(Qt.AlignCenter)
        self.cal_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))
        pv.addWidget(self.cal_status_lbl)
        grid.addWidget(pos_card, 0, 0)

        # ENCODER COUNTER card
        enc_card = QGroupBox("ENCODER COUNTER")
        enc_card.setStyleSheet(self._group_box_style())
        ev = QVBoxLayout(enc_card)
        ev.setContentsMargins(14, 16, 14, 12)
        ev.setSpacing(6)
        self.counter_lbl = QLabel("--")
        self.counter_lbl.setAlignment(Qt.AlignCenter)
        self.counter_lbl.setMinimumHeight(72)
        self.counter_lbl.setStyleSheet(self._white_figure_style())
        ev.addWidget(self.counter_lbl)
        grid.addWidget(enc_card, 0, 1)

        # VELOCITY card
        vel_card = QGroupBox("VELOCITY")
        vel_card.setStyleSheet(self._group_box_style())
        vv = QVBoxLayout(vel_card)
        vv.setContentsMargins(14, 16, 14, 12)
        vv.setSpacing(6)
        self.velocity_lbl = QLabel("--")
        self.velocity_lbl.setAlignment(Qt.AlignCenter)
        self.velocity_lbl.setMinimumHeight(72)
        self.velocity_lbl.setStyleSheet(self._white_figure_style())
        vv.addWidget(self.velocity_lbl)
        grid.addWidget(vel_card, 0, 2)

        # DIRECTION card (3-state lamp: DOWN / ON BOTTOM / UP)
        dir_card = QGroupBox("DIRECTION")
        dir_card.setStyleSheet(self._group_box_style())
        dv = QVBoxLayout(dir_card)
        dv.setContentsMargins(14, 16, 14, 12)
        dv.setSpacing(6)
        self.dir_lbl = QLabel("● STOPPED")
        self.dir_lbl.setAlignment(Qt.AlignCenter)
        self.dir_lbl.setMinimumHeight(54)
        self.dir_lbl.setStyleSheet(self._dir_style("stopped"))
        dv.addWidget(self.dir_lbl)
        grid.addWidget(dir_card, 0, 3)

        # CURRENT LAYER card
        layer_card = QGroupBox("CURRENT LAYER")
        layer_card.setStyleSheet(self._group_box_style())
        lcv = QVBoxLayout(layer_card)
        lcv.setContentsMargins(14, 16, 14, 12)
        lcv.setSpacing(6)
        self.layer_lbl = QLabel("1")
        self.layer_lbl.setAlignment(Qt.AlignCenter)
        self.layer_lbl.setMinimumHeight(72)
        self.layer_lbl.setStyleSheet(self._white_figure_style())
        lcv.addWidget(self.layer_lbl)
        grid.addWidget(layer_card, 0, 4)

        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(4, 2)
        root.addLayout(grid)

        # ── RESULT / reset banner ----------------------------------------
        self.banner_lbl = QLabel("")
        self.banner_lbl.setVisible(False)
        root.addWidget(self.banner_lbl)

        # ── Calibration table ----------------------------------------------
        cal_group = QGroupBox("CALIBRATION  (Level | Encoder Counts | Position (ft) | Counts/ft)")
        cal_group.setStyleSheet(self._group_box_style(SECTION_TITLE))
        cv = QVBoxLayout(cal_group)
        cv.setContentsMargins(12, 14, 12, 10)
        cv.setSpacing(6)

        # White display card that surrounds the Level 1–4 table (matches the
        # white-screen look of the Block Position box).
        table_card = QWidget()
        table_card.setStyleSheet(
            "QWidget { background:#FFFFFF; border:1px solid #E4DCCE; "
            "border-radius:10px; }")
        table_lay = QVBoxLayout(table_card)
        table_lay.setContentsMargins(10, 10, 10, 10)
        table_lay.setSpacing(6)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(6)
        for col in ("", "LEVEL", "ENCODER COUNTS", "POSITION (FT)", "COUNTS/FT"):
            l = QLabel(col)
            l.setStyleSheet(
                f"color:{TEXT_MID.name()}; background:{BG_INNER.name()}; "
                "font-family:'Segoe UI'; font-size:10px; font-weight:bold; "
                "padding:4px 8px; border-radius:4px;")
            header.addWidget(l, 1)
        table_lay.addLayout(header)

        # Each table row carries editable, user-defined input cells:
        #   [select]  LEVEL  |  ENCODER COUNTS (editable)  |  POSITION (FT) (editable)  |  COUNTS/FT (auto)
        # The operator types the physical counts (encoder counter anchor) and the
        # physical feet (position anchor) directly into the row, then SET LAYER /
        # SET BLOCK HEIGHT / SAVE push them to the firmware. COUNTS/FT is computed.
        self._cal_rows = []   # [(row_btn, layer_lbl, pulses_spin, feet_spin, cpf_lbl, i)]
        for i in range(FW_MAX_CAL_POINTS):
            row = QHBoxLayout()
            row.setSpacing(6)
            select_btn = QPushButton("  ")
            select_btn.setCheckable(True)
            select_btn.setFixedWidth(26)
            select_btn.setCursor(Qt.PointingHandCursor)
            select_btn.setStyleSheet(self._row_select_style())
            select_btn.clicked.connect(lambda _c, idx=i: self._select_row(idx))
            layer_lbl = QLabel(f"Level {i+1}")
            layer_lbl.setAlignment(Qt.AlignLeft)
            layer_lbl.setStyleSheet(f"background:transparent; color:{TEXT_DARK.name()}; "
                                    "font-family:'Segoe UI'; font-size:12px; font-weight:bold;")
            pulses_spin = QSpinBox()
            pulses_spin.setRange(-2000000, 2000000)
            pulses_spin.setValue(0)
            pulses_spin.setStyleSheet(
                f"QSpinBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:5px; "
                "font-family:'Consolas'; font-size:13px; font-weight:600; padding:3px; }}")
            feet_spin = QDoubleSpinBox()
            feet_spin.setRange(MIN_REF, MAX_REF)
            feet_spin.setDecimals(4)
            feet_spin.setValue(0.0)
            feet_spin.setStyleSheet(
                f"QDoubleSpinBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:5px; "
                "font-family:'Consolas'; font-size:13px; font-weight:600; padding:3px; }}")
            cpf_lbl = QLabel("—")
            cpf_lbl.setAlignment(Qt.AlignRight)
            cpf_lbl.setStyleSheet(f"background:transparent; color:{TEXT_MID.name()}; "
                                  "font-family:'Consolas'; font-size:13px; font-weight:600;")
            # promt3: editing a cell marks the calibration as MODIFIED / UNSAVED.
            pulses_spin.valueChanged.connect(lambda _v, idx=i: self._mark_modified(idx))
            feet_spin.valueChanged.connect(lambda _v, idx=i: self._mark_modified(idx))
            row.addWidget(select_btn, 0)
            row.addWidget(layer_lbl, 1)
            row.addWidget(pulses_spin, 1)
            row.addWidget(feet_spin, 1)
            row.addWidget(cpf_lbl, 1)
            table_lay.addLayout(row)
            self._cal_rows.append((select_btn, layer_lbl, pulses_spin, feet_spin, cpf_lbl, i))
        table_lay.addWidget(QLabel(
            "Enter the ENCODER COUNTS (encoder counter anchor) and POSITION (FT) "
            "(position anchor) directly into a level row, then press CALIBRATE / "
            "SET LAYER / SAVE CALIBRATION to push them to the device. "
            "COUNTS/FT is computed."))
        cv.addWidget(table_card)
        root.addWidget(cal_group)

        # ── TREND (1 Minute / 1 Hour) --------------------------------------
        graph_group = QGroupBox("TREND  —  block position vs time")
        graph_group.setStyleSheet(self._group_box_style(SECTION_TITLE))
        gl = QVBoxLayout(graph_group)
        gl.setContentsMargins(8, 14, 8, 8)
        self.graph = _IndustrialTrendGraph(self._trend_samples, QColor(0x00, 0x00, 0x00),
                                           default_range_s=60)
        gl.addWidget(self.graph)

        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        range_row.addWidget(QLabel("RANGE:"))
        self._range_buttons = []
        for label, secs in _IndustrialTrendGraph.RANGES:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._btn_style())
            btn.clicked.connect(
                lambda _checked, s=secs, b=btn: self._set_trend_range(s, b))
            self._range_buttons.append((btn, secs))
            range_row.addWidget(btn)
        self._range_buttons[0][0].setChecked(True)
        range_row.addStretch(1)
        suffix = QLabel("current: ")
        suffix.setStyleSheet(f"background:transparent; color:{TEXT_MID.name()}; "
                             "font-family:'Segoe UI'; font-size:11px;")
        self.graph_cur_lbl = QLabel("-- ft")
        self.graph_cur_lbl.setStyleSheet(f"background:transparent; color:{TEXT_DARK.name()}; "
                                         "font-family:'Consolas'; font-size:12px; font-weight:bold;")
        range_row.addWidget(suffix)
        range_row.addWidget(self.graph_cur_lbl)
        gl.addLayout(range_row)

        # ── System / device-confirmed information --------------------------
        self._gb_sys = QGroupBox("SYSTEM  (device-confirmed)")
        self._gb_sys.setStyleSheet(self._group_box_style(SECTION_TITLE))
        sg = QVBoxLayout(self._gb_sys)
        sg.setContentsMargins(12, 14, 12, 10)
        self.fw_lbl = QLabel("")
        self.fw_lbl.setWordWrap(True)
        self.fw_lbl.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Consolas'; font-size:11px;")
        sg.addWidget(self.fw_lbl)
        formula_lbl = QLabel(
            "measuring model: position = P1 + (count − C1)/(C2 − C1) × (P2 − P1)  "
            "over the calibration anchors.\n"
            "count, velocity, direction, calStatus and current layer are computed by the firmware.")
        formula_lbl.setWordWrap(True)
        formula_lbl.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:10px;")
        sg.addWidget(formula_lbl)

        # ── Command status (PENDING / SAVED / FAILED) ---------------------
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:{BG_INNER.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:6px; "
            "font-family:'Segoe UI'; font-size:11px; font-weight:bold; padding:8px;")
        self.status_lbl.setMinimumHeight(34)
        root.addWidget(self.status_lbl)

        # ── Primary action bar --------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(10)
        # promt3: ONE dedicated CALIBRATE button — saves the currently selected
        # layer's ENCODER COUNTS + POSITION (FT) to THAT layer only and persists it.
        self.calibrate_btn = QPushButton("CALIBRATE")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_style())
        self.calibrate_btn.setToolTip(
            "Save the selected layer's ENCODER COUNTS + POSITION (FT) to that layer "
            "only, then persist the calibration.")
        self.reset_btn = QPushButton("RESET COUNTER")
        self.reset_feet_btn = QPushButton("RESET FEET")
        self.load_btn = QPushButton("LOAD SAVED CALIBRATION")
        self.save_btn = QPushButton("SAVE CALIBRATION")
        self.set_layer_btn = QPushButton("SET LAYER")
        self.set_height_btn = QPushButton("SET BLOCK HEIGHT")
        self.delete_level_btn = QPushButton("DELETE LEVEL")
        self.set_polarity_btn = QPushButton("SET POLARITY")
        self.set_wits_btn = QPushButton("SET WITS CORRECTION")
        for btn, handler in ((self.calibrate_btn, self._on_calibrate),
                             (self.reset_btn, self._on_reset),
                             (self.reset_feet_btn, self._on_reset_feet),
                             (self.load_btn, self._on_load_calibration),
                             (self.save_btn, self._on_save_calibration),
                             (self.set_layer_btn, self._on_set_layer),
                             (self.set_height_btn, self._on_set_block_height),
                             (self.delete_level_btn, self._on_delete_level),
                             (self.set_polarity_btn, self._on_set_polarity),
                             (self.set_wits_btn, self._on_set_wits)):
            btn.setCursor(Qt.PointingHandCursor)
            if btn is not self.calibrate_btn:
                btn.setStyleSheet(self._btn_style())
            btn.clicked.connect(handler)
            actions.addWidget(btn, stretch=1)
        root.addLayout(actions)

        # ── promt3: calibration save-status feedback ---------------------
        self.cal_msg_lbl = QLabel("")
        self.cal_msg_lbl.setWordWrap(True)
        self.cal_msg_lbl.setStyleSheet(self._cal_msg_style("idle"))
        self.cal_msg_lbl.setVisible(False)
        root.addWidget(self.cal_msg_lbl)

        # ── Trend + System at the bottom of the dashboard -----------------
        root.addWidget(graph_group, stretch=1)
        root.addWidget(self._gb_sys, stretch=0)

        # ── Role gate ----------------------------------------------------
        self._apply_role(self.roles.role())
        self.roles.subscribe(self._apply_role)

    # ------------------------------------------------------------------ mode
    def _set_mode(self, mode):
        if mode == self.op_mode:
            return
        self.op_mode = mode
        self._apply_mode_buttons()
        from ..services import settings as settings_service
        settings_service.save({"op_mode": mode})
        self.audit.record("OP_MODE",
                          f"operation mode set to {mode}", self.roles.role())
        self.refresh()

    def _apply_mode_buttons(self):
        run_active = (self.op_mode == "RUN")
        self.run_btn.setChecked(run_active)
        self.cal_btn.setChecked(not run_active)
        base = "#C0392B"
        self.run_btn.setStyleSheet(self._toggle_style(run_active))
        self.cal_btn.setStyleSheet(self._toggle_style(not run_active))

    def _select_row(self, idx):
        self._selected_row = idx + 1  # 1-based layer
        for sbtn, _l, _r, _c, _cpf, i in self._cal_rows:
            checked = (i == idx)
            sbtn.setChecked(checked)
            sbtn.setStyleSheet(self._row_select_style(checked))

    # ------------------------------------------------------------------ role
    def _apply_role(self, role):
        # Calibration entry + save (pulse/feet inputs, CALIBRATE, SAVE, LOAD,
        # SET LAYER, SET BLOCK HEIGHT) is available to EVERY role — it is the
        # core operator calibration function (promt3: default input editable +
        # saveable). Destructive/system actions (RESET, DELETE, SET POLARITY,
        # SET WITS, operation-mode switch) stay gated to Supervisor+.
        calibration_editable = True                     # all roles
        supervisor = self.roles.at_least(ROLE_SUPERVISOR)
        for btn in (self.calibrate_btn, self.load_btn, self.save_btn,
                    self.set_layer_btn, self.set_height_btn):
            btn.setEnabled(calibration_editable)
        for btn in (self.reset_btn, self.reset_feet_btn, self.delete_level_btn,
                    self.set_polarity_btn, self.set_wits_btn,
                    self.run_btn, self.cal_btn):
            btn.setEnabled(supervisor)
        # The calibration table's editable input cells are always editable.
        for _sbtn, _llbl, pulses_spin, feet_spin, _cpf, _i in self._cal_rows:
            pulses_spin.setEnabled(calibration_editable)
            feet_spin.setEnabled(calibration_editable)

    # ––––––––––––––––––––– styling helpers –––––––––––––––––────────────
    def _theme(self):
        from .. import theme
        return theme.current()

    def _group_box_style(self, title_color=None):
        t = self._theme()
        bg = t.color_name("BG_CARD")
        border = t.color_name("BORDER")
        color = title_color or t.color_name("ACCENT")
        return (f"QGroupBox {{ color:{color}; font-family:'Segoe UI'; font-size:12px; "
                f"font-weight:bold; letter-spacing:0.5px; border:1px solid {border}; "
                f"border-radius:10px; margin-top:10px; padding-top:4px; "
                f"background:{bg}; }} "
                f"QGroupBox::title {{ subcontrol-origin:margin; left:12px; top:0px; "
                f"padding:2px 8px; color:{color}; background:{bg}; "
                f"border-radius:5px; }}")

    def _btn_style(self):
        base, hover, press = "#C0392B", "#A93026", "#8E241C"
        return (f"QPushButton {{ background:{base}; color:white; border:none; "
                f"border-radius:6px; font-family:'Segoe UI'; font-size:12px; font-weight:bold; "
                f"padding:9px 14px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:focus {{ border:2px solid #FFFFFF; }} "
                f"QPushButton:disabled {{ background:#c9bfb3; color:#f2ede6; }}")

    def _calibrate_btn_style(self):
        # Prominent, primary-action CALIBRATE button (promt3).
        base, hover, press = "#1E7A3E", "#166A33", "#104F26"
        return (f"QPushButton {{ background:{base}; color:white; border:none; "
                f"border-radius:6px; font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                f"padding:10px 18px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:focus {{ border:2px solid #FFFFFF; }} "
                f"QPushButton:disabled {{ background:#c9bfb3; color:#f2ede6; }}")

    def _calibrate_btn_saved_style(self):
        # Bright green "SAVED" state shown temporarily on the CALIBRATE button.
        base, hover, press = "#1E8E4E", "#177B43", "#0F5A32"
        return (f"QPushButton {{ background:{base}; color:#FFFFFF; "
                f"border:2px solid #BFFFC9; border-radius:6px; "
                f"font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                f"padding:10px 18px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:disabled {{ background:#c9bfb3; color:#f2ede6; }}")

    def _calibrate_btn_saving_style(self):
        # Amber "SAVING…" state shown from button press until verification.
        base, hover, press = "#D47B0F", "#B96A0C", "#8F5208"
        return (f"QPushButton {{ background:{base}; color:#FFFFFF; "
                f"border:2px solid #FFE2AB; border-radius:6px; "
                f"font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                f"padding:10px 18px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:disabled {{ background:#c9bfb3; color:#f2ede6; }}")

    def _calibrate_btn_fail_style(self):
        # Red "✕ NOT SAVED" state shown when verification fails / times out.
        base, hover, press = "#C0392B", "#A83222", "#7B241B"
        return (f"QPushButton {{ background:{base}; color:#FFFFFF; "
                f"border:2px solid #FFC2B8; border-radius:6px; "
                f"font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                f"padding:10px 18px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:disabled {{ background:#c9bfb3; color:#f2ede6; }}")

    def _flash_saving(self, layer_pk):
        """Transient in-progress state on the CALIBRATE button shown the moment
        the operator presses CALIBRATE — it stays until the write + persistence
        have been verified (no immediate "saved" claim)."""
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText(f"SAVING\u2026 LAYER {int(layer_pk)}")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_saving_style())
        self._cal_save_phase = "saving"
        self._save_flash_timer.stop()

    def _flash_not_saved(self, layer_pk):
        """Transient failure state: the write or persistence verification did
        NOT complete, so the button shows a non-success indicator and reverts
        to CALIBRATE (the success indicator is never shown on failure)."""
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText(f"\u2715  NOT SAVED \u2014 LAYER {int(layer_pk)}")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_fail_style())
        self._cal_save_phase = "failed"
        self._save_flash_timer.stop()
        self._save_flash_timer.start(2000)

    def _flash_saved(self, layer_pk):
        """Unmistakable confirm-on-the-button: the CALIBRATE button itself
        becomes a green "\u2713  SAVED — LAYER N" state for ~1.6s each time a
        layer is saved (verified), then reverts to CALIBRATE."""
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText(f"\u2713  SAVED \u2014 LAYER {int(layer_pk)}")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_saved_style())
        self._cal_save_phase = "saved"
        self._save_flash_timer.stop()
        self._save_flash_timer.start(1600)

    def _restore_calibrate_btn(self):
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText("CALIBRATE")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_style())
        self._cal_save_phase = "idle"

    def _cal_msg_style(self, kind):
        bg = {"saved": NORMAL_BG, "modified": WARN_BG,
              "saving": WARN_BG, "not_saved": FAULT_BG,
              "error": FAULT_BG, "idle": BG_INNER}.get(kind, BG_INNER)
        fc = "#FFFFFF" if kind in ("saved", "modified", "saving",
                                   "not_saved", "error") else TEXT_DARK.name()
        return (f"color:{fc}; background:{bg}; border-radius:7px; "
                f"font-family:'Segoe UI'; font-size:12px; font-weight:bold; padding:8px;")

    def _toggle_style(self, active):
        if active:
            return ("QPushButton { background:#1E7A3E; color:white; border:2px solid #14542A; "
                    "border-radius:6px; font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                    "padding:8px 14px; }")
        return ("QPushButton { background:#E8E2D6; color:#6B5E4E; border:1px solid #C8BDAD; "
                "border-radius:6px; font-family:'Segoe UI'; font-size:13px; font-weight:bold; "
                "padding:8px 14px; }")

    def _row_select_style(self, checked=False):
        if checked:
            return ("QPushButton { background:#4C6B8A; border-radius:4px; }")
        return ("QPushButton { background:#D8CFC0; border-radius:4px; }"
                "QPushButton:hover { background:#B5A999; }")

    def _figure_style(self):
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:56px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:12px; padding:12px;")

    def _big_value_style(self):
        return ("color:#1A140A; background:transparent; font-family:'Consolas'; "
                "font-size:34px; font-weight:bold;")

    def _white_figure_style(self):
        # White digital readout screen used by the numeric boxes (Encoder
        # Counter, Velocity, Current Layer) so they match the Block Position
        # box's white display.
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:40px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:12px; padding:8px;")

    def _dir_style(self, kind):
        bg = {"up": NORMAL_BG, "down": NEUTRAL_BG,
              "on_bottom": WARN_BG, "stopped": NEUTRAL_BG}.get(kind, NEUTRAL_BG)
        return f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; font-size:15px; font-weight:bold; border-radius:8px; padding:8px;"

    def _cal_status_style(self, kind):
        bg = {"valid": NORMAL_BG, "out_of_range": FAULT_BG,
              "no_calibration": NEUTRAL_BG}.get(kind, NEUTRAL_BG)
        return f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; font-size:12px; font-weight:bold; border-radius:7px; padding:6px;"

    # ––––––––––––––––––––– sample source for the graph –––––––––––––─────
    def _trend_samples(self, seconds):
        try:
            cutoff = datetime.datetime.now() - datetime.timedelta(seconds=seconds)
            return [(ts, pos) for pos, ts in self.mw.position_history if ts >= cutoff]
        except Exception:
            return []

    def _set_trend_range(self, secs, btn):
        for b, _s in self._range_buttons:
            b.setChecked(b is btn)
        self.graph.set_range_seconds(secs)

    # ––––––––––––––––––––– op bookkeeping –––––––––––––––––───────────────
    def _op_phase(self, kind):
        """Return ('pending'|'saved'|'failed'|'idle', detail) for an op."""
        op = self._ops.get(kind)
        if op is None:
            return "idle", ""
        for req_id in list(op["reqs"]):
            st = self.mw._cmd.status(req_id)
            if st is not None:
                return "pending", st.get("detail", "")
        if op["ok"] is True:
            return "saved", op["detail"]
        if op["ok"] is False:
            return "failed", op["detail"]
        return "idle", op["detail"]

    def _refresh_status_line(self):
        lines = []
        for kind, label in (("reset", "Reset counter"),
                            ("reset_feet", "Reset feet"),
                            ("load_cal", "Load saved calibration"),
                            ("save_cal", "Save calibration"),
                            ("set_layer", "Set layer"),
                            ("set_height", "Set block height"),
                            ("delete_level", "Delete level"),
                            ("set_polarity", "Set polarity"),
                            ("set_wits", "Set WITS correction"),
                            ("restore", "Restore defaults")):
            phase, detail = self._op_phase(kind)
            if phase == "pending":
                lines.append(f"{label}: PENDING — awaiting device confirmation")
            elif phase == "saved":
                lines.append(f"{label}: SAVED — {detail}")
            elif phase == "failed":
                lines.append(f"{label}: FAILED — {detail}")
        if not lines:
            lines = ["No pending changes. Settings are shown SAVED only after the "
                     "device confirms (validated / applied / stored / verified)."]
        self.status_lbl.setText("\n".join(lines))

    # ––––––––––––––––––––– helpers –––––––––––––––––––––───────────────
    def _spins(self, lo=MIN_REF, hi=MAX_REF, value=0.0, decimals=4):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setValue(value)
        s.setStyleSheet(
            f"QDoubleSpinBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:5px; "
            "font-family:'Segoe UI'; font-size:12px; padding:4px; }}")
        return s

    def _counter_row(self, default=0):
        """Build a live-capture vs manual encoder-counter input row.

        Returns (widget, get_counter) where get_counter() returns the int
        counter to send, or None to use the firmware's LIVE counter.
        """
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        live_radio = QRadioButton("Use live counter")
        manual_radio = QRadioButton("Enter counter manually")
        live_radio.setChecked(True)
        spin = QSpinBox()
        spin.setRange(-2000000, 2000000)
        spin.setValue(int(default))
        spin.setEnabled(False)
        spin.setStyleSheet(
            f"QSpinBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:5px; "
            "font-family:'Segoe UI'; font-size:12px; padding:4px; }}")
        manual_radio.toggled.connect(spin.setEnabled)
        lay.addWidget(live_radio)
        lay.addWidget(manual_radio)
        lay.addWidget(spin)
        lay.addStretch(1)

        def get_counter():
            return spin.value() if manual_radio.isChecked() else None

        return wrap, get_counter

    def _ok_cancel_dialog(self, dlg):
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        return btns

    def _local(self):
        """True when this calibration edit applies to the ACTIVE LOCAL table:
        demo sim is on, OR no serial device is connected. With no device the
        operator's saved values must take effect immediately (persist + drive
        the position calc); the firmware path is only used while connected."""
        return self.mw._demo_mode or not (self.mw.serial and self.mw.serial.is_open)

    def _ensure_serial(self, action):
        if self._local() or (self.mw.serial and self.mw.serial.is_open):
            return True
        QMessageBox.warning(self.window(), "Not connected",
                            f"Connect to the firmware before using {action}.")
        return False

    def _op_ok(self, kind, detail):
        op = self._ops.get(kind)
        if op is not None:
            op["ok"] = True
            op["detail"] = detail

    def _op_fail(self, kind, reason):
        op = self._ops.get(kind)
        if op is not None:
            op["ok"] = False
            op["detail"] = reason
        self._refresh_status_line()
        self.refresh()

    def _reset_banner(self, show):
        self.banner_lbl.setVisible(show)

    def _confirm_reset_dialog(self, title, text):
        """A confirm dialog whose message text is forced WHITE.

        The dashboard uses a dark theme; the stock QMessageBox renders its label
        with the OS palette (often dark-on-light). Setting the dialog's
        stylesheet so its label is white keeps the reset confirmation readable.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        yes = box.addButton("Yes", QMessageBox.YesRole)
        box.addButton("No", QMessageBox.NoRole)
        box.setDefaultButton(yes)
        box.setStyleSheet(
            "QMessageBox QLabel { color:#FFFFFF; }"
            "QMessageBox { background:#20242C; }")
        # Force every message-box button (Yes/No) to render WHITE text. The
        # app runs a dark Fusion palette; applying the style directly to each
        # button object is more reliable than a type-wide stylesheet rule,
        # which platform message-box styling can override.
        for b in box.buttons():
            b.setStyleSheet(
                "color:#FFFFFF; background:#2A2F3A; border:1px solid #3A4050;"
                "padding:5px 14px; border-radius:5px;")
        box.exec_()
        return box.clickedButton() is yes

    # ––––––––––––––––––––– promt3: calibration save-status feedback –––––
    def _show_cal_msg(self, kind, text):
        self._cal_msg_kind = kind
        self._cal_msg_text = text
        self.cal_msg_lbl.setText(text)
        self.cal_msg_lbl.setStyleSheet(self._cal_msg_style(kind))
        self.cal_msg_lbl.setVisible(bool(text))

    def _save_confirmation(self, layer_pk, pulses, feet):
        """The prominent green SAVED banner showing the ACTUAL values that were
        just saved — the operator must see the new PULSES / FEET, not a generic
        'saved' note."""
        return (f"\u2713 CALIBRATION SAVED \u2014 LAYER {int(layer_pk)}: "
                f"{int(pulses):,} PULSES / {float(feet):,.3f} ft")

    def _mark_modified(self, idx):
        # A user edit marks calibration as MODIFIED / UNSAVED until applied,
        # and enters an "editing session" so the per-tick refresh does not
        # overwrite the typed values (promt3).
        try:
            if self.cal_msg_lbl is None:
                return
        except AttributeError:
            return
        self._cal_dirty = True
        self._cal_editing = True
        # Editing a row selects it: CALIBRATE / SET LAYER act on the row the
        # operator is typing in, not the previous selection (which could be a
        # different layer and silently save the wrong values).
        self._select_row(idx)
        if self._cal_msg_kind != "error":
            self._show_cal_msg(
                "modified",
                f"CALIBRATION MODIFIED — UNSAVED (Layer {idx + 1} edited). "
                "Press CALIBRATE to save the selected layer.")

    def _calibration_dirty(self):
        """True when any row's typed pulses/feet differ from the device-confirmed
        values (i.e. an operator edit is pending save)."""
        mw = self.mw
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            dev_cnt = int(mw.device.get(f"calCounter{i}", 0) or 0)
            dev_ref = float(mw.device.get(f"calPosition{i}", 0.0) or 0.0)
            _sbtn, _llbl, pulses_spin, feet_spin, _cpf, _idx = self._cal_rows[i - 1]
            if pulses_spin.value() != dev_cnt or abs(feet_spin.value() - dev_ref) > 1e-9:
                return True
        return False

    def _sync_cal_msg(self):
        # Recompute the MODIFIED / SAVED indicator from live row values so the
        # message stays accurate after status refreshes / apply acks.
        if (self._cal_msg_kind in ("error", "saving", "not_saved")
                or self._cal_saving_active):
            return  # keep the in-progress / explicit result visible
        if self._calibration_dirty():
            self._show_cal_msg(
                "modified",
                "CALIBRATION MODIFIED — UNSAVED. Press CALIBRATE to save the "
                f"selected layer (Layer {self._selected_row}).")
        elif self._cal_dirty:
            # A previous edit has now been applied to the device: report SAVED.
            self._cal_dirty = False
            self._cal_msg_kind = "saved"
            if self._last_saved is not None:
                self._show_cal_msg("saved",
                                   self._save_confirmation(*self._last_saved))
            else:
                self._show_cal_msg(
                    "saved",
                    f"CALIBRATION SAVED — LAYER {self._selected_row}.")
        elif self._cal_msg_kind in ("saved", "modified"):
            # Values now agree with the device and no pending edit — keep the
            # "SAVED" confirmation (with the saved PULSES / FEET) visible.
            self._cal_msg_kind = "saved"
            if self._last_saved is not None:
                self._show_cal_msg("saved",
                                   self._save_confirmation(*self._last_saved))
            else:
                self._show_cal_msg(
                    "saved",
                    f"CALIBRATION SAVED — LAYER {self._selected_row}.")
        # else: idle — leave whatever message (or none) as-is.

    # ––––––––––––––––––––– promt3: local calibration validation –––––––
    def _validate_layer_input(self, layer_pk, pulses, feet):
        """Validate one layer's ENCODER COUNTS / POSITION (FT) before saving (promt3 §8).

        Returns (ok:bool, error:str). Rejects invalid/empty/NaN/Inf inputs, and
        validates the whole calibration table as one coherent, mathematically
        valid set — exactly mirroring the firmware's authoritative rules
        (encoder_validate_calibration_point in encoder.cpp): a 0-position row
        is "not configured" (skipped), a stored anchor must have non-zero
        position, no duplicate pulses, no duplicate positions, and counters and
        positions must sort in the same (monotonic) direction so the
        piecewise-linear interpolation is well defined and continuous.
        """
        mw = self.mw
        try:
            pulses_int = int(float(pulses))
        except (TypeError, ValueError):
            return False, f"Layer {layer_pk}: PULSES must be an integer."
        try:
            feet_f = float(feet)
        except (TypeError, ValueError):
            return False, f"Layer {layer_pk}: FEET must be a number."
        pulses_f = float(pulses_int)
        if not (math.isfinite(pulses_f) and math.isfinite(feet_f)):
            return False, f"Layer {layer_pk}: PULSES / FEET must be finite (no NaN/Inf)."
        # A 0-ft position is "not configured" — the firmware rejects it as an
        # anchor (CAL_VAL_ZERO_POSITION) and treats (counter 0, 0 ft) as unused.
        if feet_f == 0.0:
            return False, (f"Layer {layer_pk}: FEET cannot be 0.0 — a stored "
                           "anchor needs a non-zero physical position.")
        # Build the full snapshot with this layer's edit applied. Active rows are
        # those with non-zero position (the firmware's configured-mask semantics).
        rows = []
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            if i == layer_pk:
                c, p = pulses_int, feet_f
            else:
                try:
                    c = int(mw.device.get(f"calCounter{i}", 0) or 0)
                except (TypeError, ValueError):
                    c = 0
                try:
                    p = float(mw.device.get(f"calPosition{i}", 0.0) or 0.0)
                except (TypeError, ValueError):
                    p = 0.0
            rows.append((c, p, i))
        active = [(c, p, i) for (c, p, i) in rows if p != 0.0]
        if not active:
            return True, ""
        for c, p, i in active:
            if not (math.isfinite(float(c)) and math.isfinite(float(p))):
                return False, f"Layer {i}: non-finite value present — rejected."
        # Pairwise duplicate + direction-consistency (firmware §promt2).
        for a_i in range(len(active)):
            ca, pa, ia = active[a_i]
            for b_i in range(a_i + 1, len(active)):
                cb, pb, ib = active[b_i]
                if ca == cb:
                    return False, (f"Layer {ia} and Layer {ib}: duplicate "
                                   f"PULSES {ca} — rejected.")
                if abs(float(pa) - float(pb)) < 1e-6:
                    return False, (f"Layer {ia} and Layer {ib}: duplicate "
                                   f"FEET {float(pa):.3f} — rejected.")
                cAbove = (ca > cb)
                pAbove = (float(pa) > float(pb))
                if cAbove != pAbove:
                    return False, (f"Layer {ia} / Layer {ib}: encoder counter and "
                                   "physical position disagree on direction — rejected.")
        # The edited layer must itself be a configured (active) row.
        if not any(i == layer_pk for _, _, i in active):
            return False, f"Layer {layer_pk}: position is 0.0 (not configured) — rejected."
        return True, ""

    # ––––––––––––––––––––– promt3: dedicated CALIBRATE button –––––––––
    def _run_calibration_save(self, layer_pk, pulses, feet, op_kind):
        """Shared, fully-verified CALIBRATE/SAVE pipeline (promt4).

        Flow: CALIBRATE -> "SAVING CALIBRATION..." -> WRITE NEW VALUE ->
        VERIFY PERSISTENCE -> CONFIRMED -> "\u2713 CALIBRATION SAVED" + value.
        On any failure / rejection / timeout / link loss the success indicator
        is NEVER shown; instead "\u2715 CALIBRATION NOT SAVED" and the previous
        confirmed value is left unchanged.

        `pulses` is the encoder counter anchor and `feet` the position anchor
        for `layer_pk` (1-based). `op_kind` is the _ops bucket
        ("set_layer"|"set_height") used for the status line.
        """
        if not self._ensure_serial("CALIBRATE"):
            return
        if self._cal_saving_active and self._op_phase(op_kind)[0] == "pending":
            # Re-entrancy guard — never fire duplicate requests, but never be
            # silent either (a repeated CALIBRATE must always show feedback).
            self._show_cal_msg(
                "saving",
                f"SAVING CALIBRATION \u2014 LAYER {layer_pk}: awaiting device "
                "confirmation for the current request\u2026")
            return

        # Snapshot the PREVIOUS confirmed values (old value for audit, and what
        # to restore on a failed persistence so nothing changes).
        old_cnt = int(self.mw.device.get(f"calCounter{layer_pk}", 0) or 0)
        old_ft = float(self.mw.device.get(f"calPosition{layer_pk}", 0.0) or 0.0)
        self._cal_saving_active = True
        self._cal_editing = False

        # Stage 0 — show the transient in-progress state (NOT "saved").
        self._show_cal_msg(
            "saving",
            f"SAVING CALIBRATION \u2014 LAYER {layer_pk}: {int(pulses):,} PULSES / "
            f"{float(feet):,.3f} ft \u2026")
        self._flash_saving(layer_pk)
        self._ops[op_kind] = {"reqs": set(), "ok": None, "detail": ""}

        def _audit(result, detail):
            try:
                self.audit.record(
                    "CALIBRATE_" + ("SAVED" if result else "FAILED"),
                    f"layer {layer_pk} old={old_cnt}/{old_ft:.4f}ft "
                    f"new={pulses}/{feet:.4f}ft result={detail}",
                    self.roles.role(), uptime=self.mw.encoder_uptime_s)
            except Exception:
                pass

        def _finish_saved():
            self._cal_saving_active = False
            self._cal_dirty = False
            self._last_saved = (layer_pk, pulses, feet)
            self._op_ok(op_kind, f"layer {layer_pk} calibration saved")
            self._show_cal_msg("saved", self._save_confirmation(layer_pk, pulses, feet))
            self._flash_saved(layer_pk)
            _audit(True, "write+persist verified")
            self._refresh_status_line()
            self.refresh()

        def _finish_failed(reason, revert=None):
            self._cal_saving_active = False
            self._op_fail(op_kind, reason)
            self._show_cal_msg(
                "not_saved",
                f"\u2715 CALIBRATION NOT SAVED \u2014 LAYER {layer_pk}: {reason}")
            self._flash_not_saved(layer_pk)
            if revert is not None:
                # Return the active table to the previously confirmed value so
                # the "previous value remains unchanged" guarantee holds.
                revert()
            _audit(False, reason)
            self._refresh_status_line()
            self.refresh()

        # ── LOCAL mode (demo / no device): the store is settings.json. ──
        if self._local():
            old_cnt_local = int(self.mw.device.get(f"calCounter{layer_pk}", 0) or 0)
            old_ft_local = float(self.mw.device.get(f"calPosition{layer_pk}", 0.0) or 0.0)

            def _revert_local():
                self.mw.device[f"calCounter{layer_pk}"] = old_cnt_local
                self.mw.device[f"calPosition{layer_pk}"] = old_ft_local
                self._recompute_demo_counts_per_foot()
                self.mw._persist_calibration()
                self.refresh()

            self.mw.device[f"calPosition{layer_pk}"] = feet
            self.mw.device[f"calCounter{layer_pk}"] = pulses
            self.mw.device["confirmed"] = True
            self._recompute_demo_counts_per_foot()
            self.mw._persist_calibration()

            # Stage 6 — VERIFY persistence by reading the store back from disk.
            from scarlet_test_panel.services import settings as settings_service
            persisted = settings_service.load().get("calibration") or {}
            p_cnt = int(persisted.get(f"calCounter{layer_pk}", 0) or 0)
            p_ft = float(persisted.get(f"calPosition{layer_pk}", 0.0) or 0.0)
            if p_cnt == int(pulses) and abs(p_ft - float(feet)) < 1e-6:
                _finish_saved()
            else:
                _finish_failed(
                    f"persistence verification failed (read back {p_cnt}/{p_ft:.4f} ft)",
                    revert=_revert_local)
            return

        # ── REAL mode: firmware is authoritative. Chain WRITE -> PERSIST -> VERIFY.
        def _load_verify(req, data):
            # load_calibration echoes the reloaded active config; confirm the
            # persisted value read back matches what was saved.
            got = float(data.get(f"calPosition{layer_pk}") or 0.0)
            if abs(got - float(feet)) < 1e-3 and int(
                    data.get(f"calCounter{layer_pk}") or 0) == int(pulses):
                _finish_saved()
            else:
                _finish_failed(
                    f"persisted value mismatch (loaded {data.get(f'calCounter{layer_pk}')}/"
                    f"{got:.4f} ft)")

        def _persisted(req, data):
            # EEPROM write acked (ec==0). Now re-load from EEPROM and verify.
            self._ops.setdefault("load_cal", {"reqs": set(), "ok": None, "detail": ""})
            rid = self.mw._cmd.load_calibration(on_done=_load_verify, on_fail=_load_fail)
            if rid is not None:
                self._ops["load_cal"]["reqs"].add(rid)
            else:
                _finish_failed("load-for-verify could not be sent (link down?)")

        def _load_fail(req, reason):
            _finish_failed(f"persistence verify failed: {reason}")

        def _stored(req, data):
            # Value WRITE confirmed & verified by CommandTracker echo. Persist.
            self._ops.setdefault("save_cal", {"reqs": set(), "ok": None, "detail": ""})
            rid = self.mw._cmd.save_calibration(on_done=_persisted, on_fail=_persist_fail)
            if rid is not None:
                self._ops["save_cal"]["reqs"].add(rid)
            else:
                _finish_failed("persist command could not be sent (link down?)")

        def _persist_fail(req, reason):
            _finish_failed(f"EEPROM write failed: {reason}")

        def _fail(req, reason):
            _finish_failed(reason)

        req_id = self.mw._cmd.set_calibration_point(
            layer_pk, feet, counter=pulses, on_done=_stored, on_fail=_fail)
        if req_id is None:
            _finish_failed("could not send (not connected?)")
            return
        self._ops[op_kind]["reqs"].add(req_id)
        self.refresh()

    def _on_calibrate(self):
        # ONE CALIBRATE button (promt3): save the CURRENTLY SELECTED layer's
        # ENCODER COUNTS + POSITION (FT) to that layer only, then persist. Never
        # touches the other three layers and never touches the live counter.
        if not self._ensure_serial("CALIBRATE"):
            return
        phase, _d = self._op_phase("set_layer")
        if phase == "pending":
            # A previous write is still awaiting device confirmation — never
            # fire a duplicate request. Give the operator visible feedback
            # instead of silently doing nothing (promt4).
            self._show_cal_msg(
                "saving",
                f"SAVING CALIBRATION \u2014 LAYER {self._selected_row}: awaiting "
                "device confirmation for a previous request\u2026")
            return

        layer_pk = self._selected_row
        _sbtn, _llbl, pulses_spin, feet_spin, _cpf, _i = self._cal_rows[layer_pk - 1]
        pulses = int(pulses_spin.value())
        feet = float(feet_spin.value())

        # Local validation with a clear error message before anything is sent.
        ok, err = self._validate_layer_input(layer_pk, pulses, feet)
        if not ok:
            QMessageBox.warning(self.window(), "Invalid Calibration", err)
            self._show_cal_msg("error", err)
            return

        self.audit.record("CALIBRATE",
                          f"layer {layer_pk} pulses = {pulses} feet = {feet:.4f} ft "
                          f"(CALIBRATE button)",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._run_calibration_save(layer_pk, pulses, feet, "set_layer")

    def _persist_after_calibrate(self):
        # After a successful CALIBRATE, persist the table to non-volatile
        # storage so values survive restart/power cycle (promt3). In real mode
        # the firmware auto-saves on set_calibration_point; this sends the
        # explicit save_calibration too so the operator has a guaranteed persist.
        if self._local():
            return
        self._ops.setdefault("save_cal", {"reqs": set(), "ok": None, "detail": ""})
        req_id = self.mw._cmd.save_calibration(
            on_done=lambda _r, _d: self._op_ok("save_cal", "calibration saved"),
            on_fail=lambda _r, reason: self._op_fail("save_cal", reason))
        if req_id is not None:
            self._ops["save_cal"]["reqs"].add(req_id)

    # ––––––––––––––––––––– RESET COUNTER –––––––––––––––––──────────────
    def _on_reset(self):
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("RESET COUNTER"):
            return
        phase, _d = self._op_phase("reset")
        if phase == "pending":
            return  # re-entrancy guard

        pre_ticks = self.mw.current_ticks
        pre_feet = self.mw.block_position_ft
        reply = self._confirm_reset_dialog(
            "Confirm Reset",
            f"Reset the counter to 0?\n\n"
            f"Current: {pre_feet:,.3f} ft  ({pre_ticks:,} ticks)\n\n"
            "The counter reads 0 while the block stays at the same physical "
            "position (a runtime reference preserves the reading).\n"
            "The calibration table is NOT modified or re-saved.\n"
            "This cannot be undone.")
        if not reply:
            return

        self.audit.record("RESET",
                          f"reset requested (pre-reset ticks={pre_ticks}, "
                          f"position={pre_feet:.3f} ft) — calibration untouched",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self.mw.encoder_recovered = False
        self._reset_banner(False)

        if self._local():
            # Demo / no-device: zero the counter display WITHOUT re-basing or
            # persisting any calibration value. RESET COUNTER never changes the
            # calibration input (the demo sweep recomputes position on the next
            # sample, so no calibration table is shifted here either).
            self.mw.current_ticks = 0
            self.mw.device["currentTicks"] = 0
            self.mw._set_reset_state(
                True, "RESET SUCCESSFUL — counter zeroed (demo)")
            self._op_ok("reset", "counter zeroed (demo); calibration unchanged")
            self.refresh()
            return

        self._ops["reset"] = {"reqs": set(), "ok": None, "detail": ""}

        def on_reset_done(_r, _d):
            self._op_ok("reset", "counter zeroed")
            # Success banner is separately driven by consume_reset_state when the
            # reset_ack (currentTicks==0) is confirmed in _handle_reset_ack.

        def on_reset_fail(_r, reason):
            self._op_fail("reset", reason)
            # Show the "RESET FAILED" banner on the failure path (the success
            # banner is gated on the device-confirmed reset_ack).
            self.mw._set_reset_state(False, f"RESET FAILED — {reason}")

        req_id = self.mw._cmd.reset_counter(
            on_done=on_reset_done,
            on_fail=on_reset_fail)
        if req_id is None:
            self._op_fail("reset", "could not send (not connected?)")
            self.mw._set_reset_state(False, "RESET FAILED — could not send (not connected?)")
            return
        self._ops["reset"]["reqs"].add(req_id)
        if hasattr(self, "reset_btn"):
            self.reset_btn.setEnabled(False)
            self.reset_btn.setText("RESET PENDING…")
        self.refresh()

    # ––––––––––––––––––––– RESET FEET –––––––––––––––––──────────────
    def _on_reset_feet(self):
        # RESET FEET resets ONLY the CURRENT RUNTIME position: tick=0,
        # position=0.00 ft, velocity=0, direction=STOPPED. It NEVER touches the
        # calibration table (anchors, saved pulses/feet) or recalibrates.
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("RESET FEET"):
            return
        phase, _d = self._op_phase("reset_feet")
        if phase == "pending":
            return  # re-entrancy guard (duplicate-reset prevention)

        pre_ticks = self.mw.current_ticks
        pre_feet = self.mw.block_position_ft
        reply = self._confirm_reset_dialog(
            "Confirm Reset Feet",
            f"Reset the runtime position reference?\n\n"
            f"Current: {pre_feet:,.3f} ft  ({pre_ticks:,} ticks)\n\n"
            "Sets Encoder Tick = 0, Block Position = 0.00 FT, "
            "Velocity = 0.00 FT/MIN, Direction = STOPPED.\n"
            "The saved calibration table is NOT modified.")
        if not reply:
            return

        self.audit.record("RESET_FEET",
                          f"reset feet requested (pre-reset ticks={pre_ticks}, "
                          f"position={pre_feet:.3f} ft) — calibration untouched",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self.mw.encoder_recovered = False
        self._reset_banner(False)

        if self._local():
            # Demo / no-device: apply the runtime zero reference directly.
            # CRITICAL: do NOT re-base or persist calibration here — RESET FEET
            # leaves the calibration table completely unchanged.
            self.mw.current_ticks = 0
            self.mw.device["currentTicks"] = 0
            self.mw._set_block_position(0.0)
            self.mw.device["blockPositionFt"] = 0.0
            self.mw.velocity_ft_min = 0.0
            self.mw.device["velocityFtMin"] = 0.0
            self.mw.direction = "STOPPED"
            self.mw.device["direction"] = "STOPPED"
            self.mw._set_reset_state(
                True, "RESET FEET SUCCESSFUL — Encoder Tick: 0   "
                      "Block Position: 0.00 FT")
            self._op_ok("reset_feet", "runtime zeroed; calibration unchanged")
            self.refresh()
            return

        self._ops["reset_feet"] = {"reqs": set(), "ok": None, "detail": ""}

        def on_reset_feet_done(_r, _d):
            self._op_ok("reset_feet", "runtime zeroed; calibration unchanged")
            # Success banner driven via consume_reset_state on reset_feet_ack.

        def on_reset_feet_fail(_r, reason):
            self._op_fail("reset_feet", reason)
            self.mw._set_reset_state(False, f"RESET FEET FAILED — {reason}")

        req_id = self.mw._cmd.reset_feet(
            on_done=on_reset_feet_done,
            on_fail=on_reset_feet_fail)
        if req_id is None:
            self._op_fail("reset_feet", "could not send (not connected?)")
            self.mw._set_reset_state(
                False, "RESET FEET FAILED — could not send (not connected?)")
            return
        self._ops["reset_feet"]["reqs"].add(req_id)
        if hasattr(self, "reset_feet_btn"):
            self.reset_feet_btn.setEnabled(False)
            self.reset_feet_btn.setText("RESET FEET PENDING…")
        self.refresh()

    # ––––––––––––––––––––– SET LAYER –––––––––––––––––──────────────
    def _on_set_layer(self):
        # The calibration table IS the input: the selected row's ENCODER COUNTS
        # (encoder counter anchor) and POSITION (FT) (position anchor) are read
        # straight from the editable cells and pushed to the firmware.
        if not self._ensure_serial("SET LAYER"):
            return
        phase, _d = self._op_phase("set_layer")
        if phase == "pending":
            return

        layer_pk = self._selected_row
        _sbtn, _llbl, pulses_spin, feet_spin, _cpf, _i = self._cal_rows[layer_pk - 1]
        value = feet_spin.value()
        counter = int(pulses_spin.value())

        self.audit.record("SET_CALIBRATION_POINT",
                          f"layer {layer_pk} position = {value:.4f} ft "
                          f"counter (encoder counts) = {counter}",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._run_calibration_save(layer_pk, counter, value, "set_layer")

    # ––––––––––––––––––––– SET BLOCK HEIGHT –––––––––––––––––─────────
    def _on_set_block_height(self):
        if not self._ensure_serial("SET BLOCK HEIGHT"):
            return
        phase, _d = self._op_phase("set_height")
        if phase == "pending":
            return

        # "Set Block Height" = capture the currently selected layer's anchor
        # at the CURRENT physical position (live counter by default, or enter
        # the counter manually).
        layer_pk = self._selected_row
        cur = self.mw.block_position_ft
        cur_cnt = int(self.mw.device.get(f"calCounter{layer_pk}", 0) or 0)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Set Block Height — Layer {layer_pk}")
        form = QFormLayout(dlg)
        hint = QLabel(
            f"Tell the system: “the block is currently at this known physical "
            f"position, on layer {layer_pk}.” Either capture the live counter "
            f"as the anchor for that layer, or enter the counter manually.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px;")
        form.addRow(hint)
        spin = self._spins(value=cur)
        form.addRow(f"Layer {layer_pk} position (ft):", spin)
        cnt_widget, get_counter = self._counter_row(cur_cnt)
        form.addRow(f"Encoder counter (live now: {self.mw.current_ticks:,}):", cnt_widget)
        form.addRow(self._ok_cancel_dialog(dlg))
        if dlg.exec_() != QDialog.Accepted:
            return
        value = spin.value()
        counter = get_counter()

        self.audit.record("SET_BLOCK_HEIGHT",
                          f"known physical position = {value:.4f} ft on layer {layer_pk} "
                          f"counter = {counter if counter is not None else 'LIVE(' + str(self.mw.current_ticks) + ')'}",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        saved_cnt = self.mw.current_ticks if counter is None else counter
        self._run_calibration_save(layer_pk, int(saved_cnt), value, "set_height")

    # ––––––––––––––––––––– SET WITS CORRECTION –––––––––––––––––─────
    def _on_set_wits(self):
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("SET WITS CORRECTION"):
            return
        phase, _d = self._op_phase("set_wits")
        if phase == "pending":
            return

        cur = float(self.mw.device.get("witsCorrectionFt", 0.0) or 0.0)
        dlg = QDialog(self)
        dlg.setWindowTitle("Set WITS Correction")
        form = QFormLayout(dlg)
        hint = QLabel(
            "Operator offset (ft) added to the calibrated position for the "
            "reported block position. Leave 0.0 for no correction. Velocity "
            "is unaffected by this offset.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px;")
        form.addRow(hint)
        spin = self._spins(value=cur)
        form.addRow("WITS correction (ft):", spin)
        form.addRow(self._ok_cancel_dialog(dlg))
        if dlg.exec_() != QDialog.Accepted:
            return
        value = spin.value()

        self.audit.record("SET_WITS",
                          f"witsCorrectionFt = {value:.4f} ft requested",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._ops["set_wits"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            self.mw.device["witsCorrectionFt"] = value
            self.mw.device["confirmed"] = True
            self.mw._persist_calibration()
            self._op_ok("set_wits", f"WITS correction = {value:.4f} ft (demo)")
            self.refresh()
            return

        req_id = self.mw._cmd.set_wits_correction(
            value,
            on_done=lambda _r, _d: self._op_ok(
                "set_wits", f"WITS correction = {value:.4f} ft"),
            on_fail=lambda _r, reason: self._op_fail("set_wits", reason))
        if req_id is None:
            self._op_fail("set_wits", "could not send (not connected?)")
            return
        self._ops["set_wits"]["reqs"].add(req_id)
        self.refresh()

    # ––––––––––––––––––––– DELETE LEVEL –––––––––––––––––─────────
    def _on_delete_level(self):
        # promt2.txt: DELETE one calibration level — kept fully separate from
        # RESET COUNTER. Deleting a level never touches the live encoder counter.
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("DELETE LEVEL"):
            return
        phase, _d = self._op_phase("delete_level")
        if phase == "pending":
            return

        layer_pk = self._selected_row
        cur = float(self.mw.device.get(f"calPosition{layer_pk}", 0.0) or 0.0)
        if cur == 0.0:
            QMessageBox.information(
                self.window(), "Delete Level",
                f"Layer {layer_pk} has no calibration anchor to delete.")
            return
        ret = QMessageBox.question(
            self.window(), "Delete Level",
            f"Delete the calibration anchor for Layer {layer_pk}?\n"
            f"(counter {int(self.mw.device.get(f'calCounter{layer_pk}', 0) or 0):,} "
            f"→ {cur:,.3f} ft).\n\nThe live encoder counter is NOT affected.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        self.audit.record("DELETE_CALIBRATION_POINT",
                          f"delete layer {layer_pk} anchor "
                          f"(counter {int(self.mw.device.get(f'calCounter{layer_pk}', 0) or 0)}, "
                          f"{cur:,.3f} ft)",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._ops["delete_level"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            self.mw.device[f"calPosition{layer_pk}"] = 0.0
            self.mw.device[f"calCounter{layer_pk}"] = 0
            self.mw.device["confirmed"] = True
            self._recompute_demo_counts_per_foot()
            self.mw._persist_calibration()
            self._op_ok("delete_level", f"layer {layer_pk} anchor deleted (demo)")
            self.refresh()
            return

        req_id = self.mw._cmd.clear_calibration_point(
            layer_pk,
            on_done=lambda _r, _d: self._op_ok(
                "delete_level", f"layer {layer_pk} anchor deleted"),
            on_fail=lambda _r, reason: self._op_fail("delete_level", reason))
        if req_id is None:
            self._op_fail("delete_level", "could not send (not connected?)")
            return
        self._ops["delete_level"]["reqs"].add(req_id)
        self.refresh()

    # ––––––––––––––––––––– SET POLARITY –––––––––––––––––─────────
    def _on_set_polarity(self):
        # promt2.txt: configurable encoder direction polarity (+1 / -1) for a
        # reversed motor/drawworks installation.
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("SET POLARITY"):
            return
        phase, _d = self._op_phase("set_polarity")
        if phase == "pending":
            return

        cur = int(self.mw.device.get("encoderPolarity", 1) or 1)
        if cur not in (1, -1):
            cur = 1

        dlg = QDialog(self)
        dlg.setWindowTitle("Set Encoder Direction Polarity")
        form = QFormLayout(dlg)
        hint = QLabel(
            "Choose how the encoder counter relates to physical travel. "
            "+1: counter increasing while the block moves UP (default). "
            "-1: counter decreasing while UP (reversed motor/drawworks "
            "installation). Calibration is stored with the matching sign.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px;")
        form.addRow(hint)
        pol = QComboBox()
        pol.addItem("+1  (counter up = block UP)", 1)
        pol.addItem("-1  (counter up = block DOWN)", -1)
        pol.setCurrentIndex(0 if cur == 1 else 1)
        pol.setStyleSheet(
            f"QComboBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:6px; padding:6px; }}")
        form.addRow("Polarity:", pol)
        form.addRow(self._ok_cancel_dialog(dlg))
        if dlg.exec_() != QDialog.Accepted:
            return
        polarity = int(pol.currentData())

        self.audit.record("SET_ENCODER_POLARITY",
                          f"encoder direction polarity = {polarity:+d}",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._ops["set_polarity"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            self.mw.device["encoderPolarity"] = polarity
            self.mw.device["confirmed"] = True
            self.mw._persist_calibration()
            self._op_ok("set_polarity", f"polarity = {polarity:+d} (demo)")
            self.refresh()
            return

        req_id = self.mw._cmd.set_encoder_polarity(
            polarity,
            on_done=lambda _r, _d: self._op_ok(
                "set_polarity", f"polarity = {polarity:+d}"),
            on_fail=lambda _r, reason: self._op_fail("set_polarity", reason))
        if req_id is None:
            self._op_fail("set_polarity", "could not send (not connected?)")
            return
        self._ops["set_polarity"]["reqs"].add(req_id)
        self.refresh()

    # ––––––––––––––––––––– SAVE / LOAD CALIBRATION –––––––––––––––––─
    def _on_save_calibration(self):
        if not self._ensure_serial("SAVE CALIBRATION"):
            return
        phase, _d = self._op_phase("save_cal")
        if phase == "pending":
            return
        self._ops["save_cal"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            self.mw._persist_calibration()
            self._op_ok("save_cal", "calibration saved (demo)")
            self.refresh()
            return

        def _on_saved(_req, _data):
            # Firmware confirmed the save: mirror the active table to disk so
            # it survives restart/reload and stays the single source of truth.
            self.mw._persist_calibration()
            self._op_ok("save_cal", "calibration saved")

        req_id = self.mw._cmd.save_calibration(
            on_done=_on_saved,
            on_fail=lambda _r, reason: self._op_fail("save_cal", reason))
        if req_id is None:
            self._op_fail("save_cal", "could not send (not connected?)")
            return
        self._ops["save_cal"]["reqs"].add(req_id)
        self.refresh()

    def _on_load_calibration(self):
        if not self._ensure_serial("LOAD SAVED CALIBRATION"):
            return
        phase, _d = self._op_phase("load_cal")
        if phase == "pending":
            return
        self._ops["load_cal"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            # Reload the LAST SAVED calibration from disk into the ACTIVE
            # device table (the source of truth for the calculation).
            from ..services import settings as settings_service
            if self.mw._apply_saved_calibration(
                    settings=settings_service.load()):
                self.mw.device["confirmed"] = True
                self._recompute_demo_counts_per_foot()
                self._cal_editing = False
                self._cal_dirty = False
                self._last_saved = None
                self._op_ok("load_cal", "saved calibration loaded (demo)")
                self._show_cal_msg(
                    "saved", "SAVED CALIBRATION LOADED — now active.")
            else:
                self._op_fail("load_cal", "no saved calibration found (demo)")
                self._show_cal_msg("error",
                                   "No saved calibration found to load.")
            self.refresh()
            return

        req_id = self.mw._cmd.load_calibration(
            on_done=lambda _r, _d: self._op_ok("load_cal", "calibration loaded"),
            on_fail=lambda _r, reason: self._op_fail("load_cal", reason))
        if req_id is None:
            self._op_fail("load_cal", "could not send (not connected?)")
            return
        self._ops["load_cal"]["reqs"].add(req_id)
        self.refresh()

    # ––––––––––––––––––––– demo helper –––––––––––––––––──────────────
    def _recompute_demo_counts_per_foot(self):
        # Per-interval counts/ft (countsPerFoot1..3) for the interval whose
        # LOWER anchor is each layer. The TOP layer (4) has no interval and so
        # reports 0.0 — matching the firmware / CALIBRATION_DEFAULTS.md.
        pts = self.mw.cal_points()
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            cpf = 0.0
            for (cA, pA), (cB, pB) in zip(pts, pts[1:]):
                # Find the interval whose lower anchor is layer i (by position,
                # since cal_points sorts by counter).
                lower_layer = self._layer_index_for_position(pA)
                if lower_layer == i and abs(pB - pA) > 1e-9:
                    cpf = (cB - cA) / (pB - pA)
                    break
            self.mw.device[f"countsPerFoot{i}"] = round(cpf, 2)

    def _layer_index_for_position(self, pos):
        for idx in range(1, FW_MAX_CAL_POINTS + 1):
            if (float(self.mw.device.get(f"calPosition{idx}", 0.0) or 0.0)
                    == pos):
                return idx
        return 0

    # ––––––––––––––––––––– per-tick refresh –––––––––––––––––────────
    def apply_theme(self, name):
        self.refresh()

    def refresh(self):
        mw = self.mw

        # Mode indication stays in sync with persisted state.
        self._apply_mode_buttons()

        # Primary figure -----------------------------------------------------
        # Always show the computed block position (ft). No "OUT OF RANGE" text:
        # the reported number is displayed regardless of calibration status.
        cal_status_raw = (mw.cal_status or "NO_CALIBRATION").upper()
        pos = mw.block_position_ft
        self.pos_value_lbl.setText(f"{pos:,.2f} ft")
        self.velocity_lbl.setText(f"{mw.velocity_ft_min:,.2f}" if mw.cal_status else "--")
        self.counter_lbl.setText(f"{mw.current_ticks:,}")
        self.layer_lbl.setText(str(mw.current_layer or 1))

        # Calibration status -------------------------------------------------
        cal_status = cal_status_raw
        if cal_status == "OUT_OF_RANGE":
            self.cal_status_lbl.setText(f"{pos:,.2f} ft")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("valid"))
        elif cal_status == "VALID":
            self.cal_status_lbl.setText("● VALID")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("valid"))
        else:
            self.cal_status_lbl.setText("● NO CALIBRATION")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))

        # Direction (3-state: DOWN / ON BOTTOM / UP, plus STOPPED) -----------
        direction = (mw.direction or "STOPPED").upper()
        if mw.on_bottom and direction == "STOPPED":
            self.dir_lbl.setText("▼ ON BOTTOM")
            self.dir_lbl.setStyleSheet(self._dir_style("on_bottom"))
        elif direction == "UP":
            self.dir_lbl.setText("▲ UP")
            self.dir_lbl.setStyleSheet(self._dir_style("up"))
        elif direction == "DOWN":
            self.dir_lbl.setText("▼ DOWN")
            self.dir_lbl.setStyleSheet(self._dir_style("down"))
        else:
            self.dir_lbl.setText("● STOPPED")
            self.dir_lbl.setStyleSheet(self._dir_style("stopped"))

        # Calibration table rows ---------------------------------------------
        # The table IS the calibration input: ENCODER COUNTS / POSITION (FT) are
        # editable cells. Once the operator starts editing (`_cal_editing`),
        # the per-tick refresh stops overwriting their typed values (promt3:
        # type pulses/feet, then press CALIBRATE). PULSES/FT is computed from
        # the row's own typed pulses/feet.
        cur_layer = mw.current_layer or 1
        editing = self._cal_editing
        # Live (possibly-typed) pulses/feet per layer, read straight from the
        # editable cells so PULSES/FT always reflects what the operator is
        # entering — not stale device countsPerFoot from a prior CALIBRATE.
        _typed = {}
        for _s, _l, _p, _f, _c, _ii in self._cal_rows:
            try:
                _typed[_ii + 1] = (int(_p.value()), float(_f.value()))
            except (TypeError, ValueError):
                _typed[_ii + 1] = (0, 0.0)
        _top_layer = max(
            (k for k, (c, f) in _typed.items() if c != 0 or abs(f) > 1e-9),
            default=1)
        for sbtn, layer_lbl, pulses_spin, feet_spin, cpf_lbl, i in self._cal_rows:
            layer_pk = i + 1
            dev_ref = float(mw.device.get(f"calPosition{layer_pk}", 0.0) or 0.0)
            dev_cnt = int(mw.device.get(f"calCounter{layer_pk}", 0) or 0)
            if not editing:
                # Not editing: keep the table in sync with device-confirmed values.
                if pulses_spin.value() != dev_cnt:
                    pulses_spin.blockSignals(True)
                    pulses_spin.setValue(dev_cnt)
                    pulses_spin.blockSignals(False)
                if abs(feet_spin.value() - dev_ref) > 1e-9:
                    feet_spin.blockSignals(True)
                    feet_spin.setValue(dev_ref)
                    feet_spin.blockSignals(False)
            # PULSES/FT column — computed live from the row's current (typed)
            # pulses/feet so newly-entered values are used immediately. For a
            # non-top layer N it is the interval N->N+1: (C[N+1]-C[N])/(F[N+1]-F[N]).
            # The TOP layer has no upward interval, so it shows the LAST interval
            # (T-1 -> T) — the segment that governs entering that layer's region.
            _c_lo, _f_lo = _typed.get(layer_pk, (0, 0.0))
            if layer_pk < _top_layer:
                _c_hi, _f_hi = _typed.get(layer_pk + 1, (0, 0.0))
            else:
                _c_lo, _f_lo = _typed.get(_top_layer - 1, (_c_lo, _f_lo))
                _c_hi, _f_hi = _typed.get(_top_layer, (0, 0.0))
            if abs(_f_hi - _f_lo) > 1e-9:
                _cpf = (_c_hi - _c_lo) / (_f_hi - _f_lo)
            else:
                _cpf = 0.0
            cpf_lbl.setText(f"{_cpf:,.2f}" if _cpf > 0.0 else "—")
            layer_lbl.setText(f"Level {layer_pk}")
            if (i + 1) == cur_layer:
                layer_lbl.setStyleSheet("background:transparent; color:#1E7A3E; "
                                        "font-family:'Segoe UI'; font-size:12px; font-weight:bold;")
            else:
                layer_lbl.setStyleSheet(f"background:transparent; color:{TEXT_DARK.name()}; "
                                        "font-family:'Segoe UI'; font-size:12px; font-weight:bold;")
            # Ensure the selected-row button is correct after a rebuild.
            self._select_row(self._selected_row - 1)

        # Promote to device-sync when the current table now matches the device
        # (e.g. after a successful CALIBRATE / SAVE / LOAD), so the table
        # resumes tracking device-confirmed values on later ticks.
        if editing and not self._calibration_dirty():
            self._cal_editing = False

        # RESET banner --------------------------------------------------------
        reset = mw.consume_reset_state()
        if reset is not None:
            _kind, ok, detail, _ts = reset
            if ok:
                self.banner_lbl.setText(detail)
                self.banner_lbl.setStyleSheet(
                    "color:#FFFFFF; background:#1E7A3E; border-radius:8px; padding:11px; "
                    "font-family:'Segoe UI'; font-size:15px; font-weight:bold;")
                self.banner_lbl.setToolTip("Device confirmed currentTicks = 0")
            else:
                self.banner_lbl.setText(detail)
                self.banner_lbl.setStyleSheet(
                    "color:#FFFFFF; background:#C0392B; border-radius:8px; padding:11px; "
                    "font-family:'Segoe UI'; font-size:15px; font-weight:bold;")
            self.banner_lbl.setVisible(True)
            self._banner_hide_at = _time.monotonic() + 5.0
        elif getattr(self, "_banner_hide_at", 0.0) and _time.monotonic() > self._banner_hide_at:
            self.banner_lbl.setVisible(False)
            self._banner_hide_at = 0.0

        # System / device-confirmed info --------------------------------------
        confirmed = bool(mw.device.get("confirmed"))
        conf_txt = "VERIFIED (device-confirmed)" if confirmed else "awaiting device status"
        self.fw_lbl.setText(
            f"FW: {mw.encoder_fw_version or '--'}  Build: {mw.encoder_build or '--'}  "
            f"Protocol: {mw.encoder_protocol_version or '--'}  EEPROM: {mw.encoder_eeprom_status or '--'}  "
            f"SEQ: {mw.encoder_sequence}\n"
            f"Uptime: {mw.encoder_uptime_s}s  Boot: {mw.encoder_boot_reason or '--'}  "
            f"WITS: {mw.device.get('witsCorrectionFt', 0.0):,.2f} ft  "
            f"Config: {conf_txt}")

        # Graph + current point ------------------------------------------------
        self.graph_cur_lbl.setText(f"{pos:,.2f} ft")
        self.graph.update()

        # Command phase line -----------------------------------------------------
        self._refresh_status_line()

        # promt3: calibration save-status feedback kept in sync with the rows.
        self._sync_cal_msg()

        # Role gating re-applied (in case a request finished mid-flight).
        self.reset_btn.setText("RESET COUNTER")
        phase, _ = self._op_phase("reset")
        if phase == "pending":
            self.reset_btn.setText("RESET PENDING…")
            self.reset_btn.setEnabled(False)
        else:
            # Re-enable after success / failure / timeout so a fresh reset can
            # be issued again (duplicate-reset prevention only while in-flight).
            self.reset_btn.setEnabled(True)

        self.reset_feet_btn.setText("RESET FEET")
        phase, _ = self._op_phase("reset_feet")
        if phase == "pending":
            self.reset_feet_btn.setText("RESET FEET PENDING…")
            self.reset_feet_btn.setEnabled(False)
        else:
            # Re-enable after success / failure / timeout (duplicate prevention
            # only while the request is in-flight).
            self.reset_feet_btn.setEnabled(True)