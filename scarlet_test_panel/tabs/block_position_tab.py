"""Block Position Monitor operator page.

Mirrors the industrial 'Block Position Monitor' HMI:

  ┌────────────────────────────┬──────────────────────────────────┐
  │ CURRENT MODE OF OPERATION  │ CURRENT BLOCK POSITION           │
  │   [ RUN ]  [ CALIBRATE ]   │        31.58 ft                  │
  ├────────────────────────────┼───────────────┬──────────────────┤
  │ BLOCK POSITION   31.58 ft  │ ENCODER       │ VELOCITY         │
  │ CURRENT WRAP     1         │ COUNTER       │ 0.00 ft/min      │
  │ DIRECTION    ▼ ON BOTTOM   │ 4437          │                  │
  ├────────────────────────────┴───────────────┴──────────────────┤
  │ CALIBRATION TABLE   (Wrap | Initial Tape Reading | Counter | │
  │   Counts/ft)  4 operator rows, selectable                     │
  ├───────────────────────────────────────────────────────────────┤
  │  TREND  (BLOCK POSITION vs TIME)    [ 1 Minute ] [ 1 Hour ]    │
  ├───────────────────────────────────────────────────────────────┤
  │ RESET COUNTER │ LOAD SAVED CALIBRATION │ SAVE CALIBRATION │   │
  │ SET WRAP │ SET BLOCK HEIGHT │ SET WITS CORRECTION │           │
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
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer, QEvent
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontMetrics

from ..config import (
    ACCENT, BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_MID, TEXT_LITE,
    TEXT_ON_DARK,
    FW_MAX_CAL_POINTS,
    FT_TO_M,
    SENSOR_CONFIG,
)
from ..widgets.graph_plot import _IndustrialTrendGraph
from ..widgets.flow_layout import FlowLayout
from ..services.security import ROLE_SUPERVISOR, ROLE_OPERATOR
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

# Serial link to the Arduino intermittently drops / corrupts command frames
# (measured ~50% loss on the CH340 link). The calibration pipeline retries each
# command a couple of times before declaring failure so a single lost ack does
# not surface as "CALIBRATION NOT SAVED". Values are send-attempt counts and a
# pacing delay so retried commands do not collide with the device's report
# stream.
CAL_SEND_ATTEMPTS = 5
CAL_RETRY_DELAY_MS = 300

# Responsive value table, matching the Dashboard drilling monitor: the cells
# are laid out in as many equal columns as the visible width allows and each
# cell stretches to fill its column, so there is never a horizontal scrollbar
# and the last row always fills the full width.
READOUT_GAP = 6
READOUT_MIN_CELL_W = 165
READOUT_MAX_COLUMNS = 5
# Chrome around the value table: the page margins plus the section frame, so
# the computed cell width never overflows the visible viewport.
READOUT_PAGE_MARGIN = 32


class _SectionCells:
    """Collects the value cells of one readout section in build order.

    The builders add the cells exactly as before (``addWidget``) and the call
    is forwarded to the section's FlowLayout, so Qt owns every widget from the
    start. The order is remembered so ``_relayout_readout`` can give each cell
    the width of one responsive column.
    """

    def __init__(self, flow, cells):
        self._flow = flow
        self.cells = cells

    def addWidget(self, widget):
        self._flow.addWidget(widget)
        self.cells.append(widget)

    def __getattr__(self, name):
        return getattr(self._flow, name)


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

        # Display-only unit for the BLOCK POSITION readout. The internal
        # position is ALWAYS feet (block_position_ft); METERS is only a display
        # conversion (meters = feet x 0.3048). promt1.txt.
        self._position_unit = "FT"

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

        # promt2: per-layer CAPTURE + main CALIBRATE / SAVE ALL LAYERS flow.
        # _capture_btns[i] is the CAPTURE button for table row i (parallel to
        # _cal_rows); _capturing[i] guards re-entrancy while a tick request is
        # in flight. _save_all_active guards the all-layers save pipeline,
        # _cal_saved_all holds the multi-line "✓ CALIBRATION SAVED — ALL
        # LAYERS" confirmation, and _last_cal_saved_at is the persisted
        # timestamp shown in that confirmation.
        self._capture_btns = []
        self._capturing = []
        self._save_all_active = False
        self._cal_saved_all = None
        self._last_cal_saved_at = str(
            self.mw._settings.get("cal_last_saved", "") or "")
        # Until this monotonic deadline, a fresh "\u2713 TICK CAPTURED"
        # confirmation stays visible even though the captured tick makes the
        # table calibrate-dirty (the MODIFIED notice follows after).
        self._capture_msg_until = 0.0

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
        # Responsive: never scroll horizontally — the value tables re-flow into
        # fewer equal columns on the visible width and the calibration table and
        # heading wrap, so narrow screens never get a sideways scrollbar.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
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
        # The readout table is placed from the *visible* width of this viewport
        # (the same approach as the Dashboard drilling monitor) so the column
        # count is correct even when the window is resized while another tab
        # is on screen.
        self._readout_scroll = scroll
        self._readout_viewport = scroll.viewport()
        self._readout_viewport.installEventFilter(self)

        # ── Heading (responsive: title + subtitle stack and wrap) ----------
        head = QHBoxLayout()
        head.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("BLOCK POSITION MONITOR")
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Segoe UI'; font-size:20px; "
            "font-weight:bold; letter-spacing:2px; background:transparent;")
        subtitle = QLabel("live device-reported measurement  •  multi-point calibration")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px; "
            "background:transparent;")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        head.addLayout(title_col)
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
        _mode_hint = QLabel("(operation mode is dashboard state, not a firmware mode)")
        _mode_hint.setWordWrap(True)
        mv.addWidget(_mode_hint, 1)
        # CURRENT MODE OF OPERATION box removed from the dashboard. Keep the
        # widgets alive (hidden, out of layout) so the RUN/CALIBRATE mode logic
        # and role handling keep working; only the visible box is gone.
        mode_group.setParent(container)
        mode_group.hide()
        self._apply_mode_buttons()

        # ── Responsive value table (table_value.txt) ---------------------
        # All value boxes are organised into three labelled sections —
        # POSITION / HOOKLOAD / SLIP WINDOW INFORMATION. Each section holds a
        # responsive table of equal-sized cells (Label -> large value -> unit)
        # that reflows into fewer columns on narrow screens instead of
        # squeezing or scrolling, exactly like the Dashboard drilling monitor.
        # The app remains the single source of every value; this is purely a
        # presentation re-layout.
        readout_col = QVBoxLayout()
        readout_col.setSpacing(6)
        self._readout_flow = readout_col
        self._readout_cards = []
        self._readout_rows = []
        self._readout_columns = 0

        # Cell height only: the width follows the responsive column count so
        # the readout numbers, the unit/toggle line and the state captions all
        # have room. Long captions wrap instead of clipping.
        cell_h = 116

        def _section(title):
            sec = QGroupBox(title)
            sec.setStyleSheet(self._group_box_style(SECTION_TITLE))
            # The section keeps its FlowLayout: a plain QLayout holding these
            # cells inside the QGroupBox aborts Qt when the tab is first
            # shown. The responsive equal columns come from
            # _relayout_readout, which resizes the cells to the column width
            # the visible width allows and lets the flow wrap them.
            flow = FlowLayout(sec, 0, 6, sec)
            flow.setContentsMargins(8, 4, 8, 6)
            cells = []
            self._readout_rows.append([sec, flow, cells])
            return sec, _SectionCells(flow, cells)

        def _value_cell(title):
            cell = QGroupBox(title)
            cell.setStyleSheet(self._cell_box_style())
            cell.setFixedHeight(cell_h)
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(10, 6, 10, 6)
            cv.setSpacing(3)
            return cell, cv

        def _unit_lbl(text):
            u = QLabel(text)
            u.setAlignment(Qt.AlignCenter)
            # Wrap long captions ("SEC - LIVE COUNTUP / TIME") onto a second
            # line instead of clipping them inside the box.
            u.setWordWrap(True)
            # Captions keep only the height they need; the white readout
            # screen below/above absorbs the rest of the box height.
            u.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            u.setStyleSheet(
                f"color:{TEXT_MID.name()}; background:transparent; "
                "font-family:'Segoe UI'; font-size:10px; font-weight:600;")
            return u

        def _digits_lbl(min_h):
            lbl = QLabel("--")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumHeight(min_h)
            # The readout is the screen box: it takes the spare height of the
            # cell so a taller CELL_H visibly grows the white display.
            lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            return lbl

        # ── POSITION INFORMATION ---------------------------------------
        sec, flow = _section("POSITION INFORMATION")
        self._readout_cards.append(sec)

        cell, cv = _value_cell("BLOCK POSITION")
        self.unit_toggle_btn = QPushButton("M")
        self.unit_toggle_btn.setFixedSize(40, 18)
        self.unit_toggle_btn.setStyleSheet(self._unit_toggle_style(active=False))
        self.unit_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.unit_toggle_btn.setToolTip("Switch the Block Position display between FT and M")
        self.unit_toggle_btn.clicked.connect(self._on_unit_toggle)
        posrow = QHBoxLayout()
        posrow.setSpacing(8)
        self.pos_value_lbl = _digits_lbl(30)
        self.pos_value_lbl.setStyleSheet(self._block_figure_style())
        posrow.addWidget(self.pos_value_lbl, 1)
        posrow.addWidget(self.unit_toggle_btn, 0, Qt.AlignTop)
        cv.addLayout(posrow)
        self.cal_status_lbl = QLabel("● NO CALIBRATION")
        self.cal_status_lbl.setAlignment(Qt.AlignCenter)
        self.cal_status_lbl.setWordWrap(True)
        self.cal_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))
        cv.addWidget(self.cal_status_lbl)
        flow.addWidget(cell)

        cell, cv = _value_cell("ENCODER COUNTER")
        self.counter_lbl = _digits_lbl(32)
        self.counter_lbl.setStyleSheet(self._cell_figure_style())
        cv.addWidget(self.counter_lbl)
        cv.addWidget(_unit_lbl("TICKS"))
        flow.addWidget(cell)

        cell, cv = _value_cell("VELOCITY")
        self.velocity_lbl = _digits_lbl(32)
        self.velocity_lbl.setStyleSheet(self._cell_figure_style())
        cv.addWidget(self.velocity_lbl)
        cv.addWidget(_unit_lbl("FT/MIN"))
        flow.addWidget(cell)

        cell, cv = _value_cell("DIRECTION")
        self.dir_lbl = QLabel("▼ DOWN")
        self.dir_lbl.setAlignment(Qt.AlignCenter)
        self.dir_lbl.setMinimumHeight(34)
        self.dir_lbl.setStyleSheet(self._dir_style("stopped"))
        cv.addWidget(self.dir_lbl)
        flow.addWidget(cell)

        # BIT / HOLE DEPTH / BOTTOM STATE keep their dark LCD screens; the
        # builders install the layout and captions exactly as before.
        cell = QGroupBox("BIT POSITION")
        cell.setStyleSheet(self._cell_box_style())
        cell.setFixedHeight(cell_h)
        self._build_bit_screen(cell)
        flow.addWidget(cell)

        cell = QGroupBox("HOLE DEPTH")
        cell.setStyleSheet(self._cell_box_style())
        cell.setFixedHeight(cell_h)
        self._build_hole_depth_screen(cell)
        flow.addWidget(cell)

        cell = QGroupBox("BOTTOM STATE")
        cell.setStyleSheet(self._cell_box_style())
        cell.setFixedHeight(cell_h)
        self._build_bottom_screen(cell)
        flow.addWidget(cell)

        cell, cv = _value_cell("STRING LENGTH")
        self.string_length_lbl = _digits_lbl(30)
        self.string_length_lbl.setStyleSheet(self._string_figure_style())
        cv.addWidget(self.string_length_lbl)
        self.sl_status_lbl = QLabel("● PIPE IN HOLE NOT SET")
        self.sl_status_lbl.setAlignment(Qt.AlignCenter)
        self.sl_status_lbl.setWordWrap(True)
        self.sl_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))
        cv.addWidget(self.sl_status_lbl)
        flow.addWidget(cell)

        readout_col.addWidget(sec)

        # ── HOOKLOAD INFORMATION ----------------------------------------
        # Single source, four readouts: HOOKLOAD = the live channel-0 analog
        # value (mw.hookload_klb); MINIMUM VALUE / HIGH POINT = the ENGINEERING
        # VALUES of the Hookload two-point calibration (SENSOR_CONFIG[0]
        # cal_val_lo / cal_val_hi); VOLTAGE = the live channel-0 input voltage
        # (V). No separate hookload engine — and no Pipe Weight channel exists,
        # so that spec cell is intentionally not fabricated.
        sec, flow = _section("HOOKLOAD INFORMATION")
        self._readout_cards.append(sec)

        cell, cv = _value_cell("HOOKLOAD")
        self.hook_current_lbl = _digits_lbl(32)
        self.hook_current_lbl.setStyleSheet(self._hook_cell_style())
        cv.addWidget(self.hook_current_lbl)
        cv.addWidget(_unit_lbl("KLB · LIVE"))
        flow.addWidget(cell)

        cell, cv = _value_cell("MINIMUM VALUE")
        self.hook_low_lbl = _digits_lbl(32)
        self.hook_low_lbl.setStyleSheet(self._hook_cell_style())
        cv.addWidget(self.hook_low_lbl)
        cv.addWidget(_unit_lbl("KLB · CAL LOW POINT"))
        flow.addWidget(cell)

        cell, cv = _value_cell("HIGH POINT")
        self.hook_high_lbl = _digits_lbl(32)
        self.hook_high_lbl.setStyleSheet(self._hook_cell_style())
        cv.addWidget(self.hook_high_lbl)
        cv.addWidget(_unit_lbl("KLB · CAL HIGH POINT"))
        flow.addWidget(cell)

        cell, cv = _value_cell("VOLTAGE")
        self.hook_voltage_lbl = _digits_lbl(32)
        self.hook_voltage_lbl.setStyleSheet(self._hook_cell_style())
        cv.addWidget(self.hook_voltage_lbl)
        cv.addWidget(_unit_lbl("V · CHANNEL-0 INPUT"))
        flow.addWidget(cell)

        readout_col.addWidget(sec)

        # ── SLIP WINDOW INFORMATION ------------------------------------
        sec, flow = _section("SLIP WINDOW INFORMATION")
        self._readout_cards.append(sec)

        cell, cv = _value_cell("SLIP WINDOW LOAD")
        self.slip_load_disp_lbl = _digits_lbl(32)
        self.slip_load_disp_lbl.setStyleSheet(self._slip_cell_style())
        cv.addWidget(self.slip_load_disp_lbl)
        cv.addWidget(_unit_lbl("KLB · INPUT + MINIMUM"))
        flow.addWidget(cell)

        cell, cv = _value_cell("SLIP WINDOW TIME")
        self.slip_time_disp_lbl = _digits_lbl(32)
        self.slip_time_disp_lbl.setStyleSheet(self._slip_cell_style())
        cv.addWidget(self.slip_time_disp_lbl)
        cv.addWidget(_unit_lbl("SEC · CONFIGURED"))
        flow.addWidget(cell)

        cell, cv = _value_cell("SLIP WINDOW TIMER")
        self.slip_timer_lbl = _digits_lbl(32)
        self.slip_timer_lbl.setStyleSheet(self._slip_cell_style())
        cv.addWidget(self.slip_timer_lbl)
        cv.addWidget(_unit_lbl("SEC · LIVE COUNTUP / TIME"))
        flow.addWidget(cell)

        cell, cv = _value_cell("STATUS")
        self.slip_status_lbl = QLabel("NOT ACTIVE")
        self.slip_status_lbl.setAlignment(Qt.AlignCenter)
        self.slip_status_lbl.setMinimumHeight(26)
        self.slip_status_lbl.setStyleSheet(self._slip_status_style("NOT ACTIVE"))
        cv.addWidget(self.slip_status_lbl)
        cv.addWidget(_unit_lbl("CONFIRMING / CONFIRMED STATE"))
        flow.addWidget(cell)

        readout_col.addWidget(sec)

        root.addLayout(readout_col)

        # ── PIPE IN HOLE / SLIP WINDOW inputs (promt.txt) ──────────────────
        pih_group = QGroupBox("PIPE IN HOLE  ·  SLIP WINDOW SETTINGS")
        pih_group.setStyleSheet(self._group_box_style(SECTION_TITLE))
        pih_lay = QVBoxLayout(pih_group)
        pih_lay.setContentsMargins(10, 8, 10, 6)
        pih_lay.setSpacing(8)

        pih_inner = QWidget()
        pih_inner.setStyleSheet(
            "QWidget { background:#FFFFFF; border:1px solid #E4DCCE; "
            "border-radius:10px; }")
        pih_row = QHBoxLayout(pih_inner)
        pih_row.setContentsMargins(14, 10, 14, 10)
        pih_row.setSpacing(10)

        pih_hint = QLabel("Enter the Pipe in Hole depth in FEET:")
        pih_hint.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:12px;")
        pih_row.addWidget(pih_hint)

        self.pipe_in_hole_spin = QDoubleSpinBox()
        self.pipe_in_hole_spin.setRange(0.0, 999999.0)
        self.pipe_in_hole_spin.setDecimals(2)
        self.pipe_in_hole_spin.setSingleStep(10.0)
        self.pipe_in_hole_spin.setValue(0.0)
        self.pipe_in_hole_spin.setMinimumWidth(160)
        self.pipe_in_hole_spin.setAlignment(Qt.AlignCenter)
        self.pipe_in_hole_spin.setStyleSheet(
            f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:5px; "
            f"font-family:'Consolas'; font-size:15px; font-weight:600; padding:6px; }}")
        pih_row.addWidget(self.pipe_in_hole_spin)

        pih_unit = QLabel("FT")
        pih_unit.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:13px; font-weight:bold;")
        pih_row.addWidget(pih_unit)

        self.pih_save_btn = QPushButton("SAVE")
        self.pih_save_btn.setCursor(Qt.PointingHandCursor)
        self.pih_save_btn.setMinimumWidth(100)
        self.pih_save_btn.setStyleSheet(self._btn_style())
        self.pih_save_btn.setToolTip(
            "Save the Pipe in Hole value. String Length will be calculated as "
            "Pipe in Hole + Block Position. This value persists across sessions.")
        self.pih_save_btn.clicked.connect(self._on_save_pipe_in_hole)
        pih_row.addWidget(self.pih_save_btn)

        self.pih_msg_lbl = QLabel("")
        self.pih_msg_lbl.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        pih_row.addWidget(self.pih_msg_lbl, 1)

        pih_lay.addWidget(pih_inner, 1)

        # ── SLIP WINDOW load/time confirmation (slip_window.txt) ──────
        slip_inner = QWidget()
        slip_inner.setStyleSheet(
            "QWidget { background:#FFFFFF; border:1px solid #E4DCCE; "
            "border-radius:10px; }")
        slip_row = QHBoxLayout(slip_inner)
        slip_row.setContentsMargins(14, 10, 14, 10)
        slip_row.setSpacing(10)

        slip_hint = QLabel("Slip Window — Hookload must stay at or above the "
                           "Slip Window Load (input + Hookload MINIMUM VALUE) "
                           "for the whole Slip Window Time before the Bit "
                           "Position is allowed to move:")
        slip_hint.setWordWrap(True)
        slip_hint.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:12px;")
        slip_row.addWidget(slip_hint, 1)

        slip_unit = QLabel("k-lb")
        slip_unit.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:13px; font-weight:bold;")
        slip_row.addWidget(slip_unit)

        self.slip_window_load_spin = QDoubleSpinBox()
        self.slip_window_load_spin.setRange(0.0, 2000.0)
        self.slip_window_load_spin.setDecimals(2)
        self.slip_window_load_spin.setSingleStep(1.0)
        self.slip_window_load_spin.setValue(20.0)
        self.slip_window_load_spin.setMinimumWidth(120)
        self.slip_window_load_spin.setAlignment(Qt.AlignCenter)
        self.slip_window_load_spin.setStyleSheet(
            f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:5px; "
            f"font-family:'Consolas'; font-size:15px; font-weight:600; padding:6px; }}")
        slip_row.addWidget(self.slip_window_load_spin)

        slip_unit2 = QLabel("sec")
        slip_unit2.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:13px; font-weight:bold;")
        slip_row.addWidget(slip_unit2)

        self.slip_window_time_spin = QDoubleSpinBox()
        self.slip_window_time_spin.setRange(0.1, 3600.0)
        self.slip_window_time_spin.setDecimals(2)
        self.slip_window_time_spin.setSingleStep(0.5)
        self.slip_window_time_spin.setValue(5.0)
        self.slip_window_time_spin.setMinimumWidth(120)
        self.slip_window_time_spin.setAlignment(Qt.AlignCenter)
        self.slip_window_time_spin.setStyleSheet(
            f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:5px; "
            f"font-family:'Consolas'; font-size:15px; font-weight:600; padding:6px; }}")
        slip_row.addWidget(self.slip_window_time_spin)

        self.slip_save_btn = QPushButton("SAVE")
        self.slip_save_btn.setCursor(Qt.PointingHandCursor)
        self.slip_save_btn.setMinimumWidth(90)
        self.slip_save_btn.setStyleSheet(self._btn_style())
        self.slip_save_btn.setToolTip(
            "Save the Slip Window Load (k-lb) and Time (seconds). The Bit "
            "Position stays constant until Hookload holds at/above the load "
            "INPUT + the Hookload MINIMUM VALUE for the full Time. Persists "
            "across sessions.")
        self.slip_save_btn.clicked.connect(self._on_save_slip_window)
        slip_row.addWidget(self.slip_save_btn)

        self.slip_msg_lbl = QLabel("")
        self.slip_msg_lbl.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent; "
            "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        slip_row.addWidget(self.slip_msg_lbl, 1)

        pih_lay.addWidget(slip_inner, 1)

        # Load the saved Pipe in Hole value into the spin box on startup.
        saved_pih = self.mw.pipe_in_hole_ft
        if saved_pih is not None:
            self.pipe_in_hole_spin.setValue(float(saved_pih))
        # Load the saved Slip Window Load / Time into the spin boxes.
        saved_slip_load = self.mw.slip_window_load_klb
        if saved_slip_load is not None:
            self.slip_window_load_spin.setValue(float(saved_slip_load))
        saved_slip_time = self.mw.slip_window_time_sec
        if saved_slip_time is not None:
            self.slip_window_time_spin.setValue(float(saved_slip_time))

        root.addWidget(pih_group)

        # ── RESULT / reset banner ----------------------------------------
        self.banner_lbl = QLabel("")
        self.banner_lbl.setVisible(False)
        root.addWidget(self.banner_lbl)

        # ── Calibration table ----------------------------------------------
        cal_group = QGroupBox("CALIBRATION  (Wrap | Encoder Counts | Position (ft) | Counts/ft)")
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
        for col in ("", "WRAP", "ENCODER COUNTS", "POSITION (FT)",
                    "CAPTURE", "COUNTS/FT"):
            l = QLabel(col)
            l.setStyleSheet(
                f"color:{TEXT_ON_DARK.name()}; background:{BG_INNER.name()}; "
                "font-family:'Segoe UI'; font-size:9px; font-weight:bold; "
                "padding:3px 4px; border-radius:4px;")
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
            layer_lbl = QLabel(f"Wrap {i+1}")
            layer_lbl.setAlignment(Qt.AlignLeft)
            layer_lbl.setMinimumWidth(70)
            layer_lbl.setStyleSheet(f"background:transparent; color:{TEXT_DARK.name()}; "
                                    "font-family:'Segoe UI'; font-size:12px; font-weight:bold;")
            pulses_spin = QSpinBox()
            pulses_spin.setRange(-2000000, 2000000)
            pulses_spin.setValue(0)
            pulses_spin.setMinimumWidth(120)
            pulses_spin.setStyleSheet(
                f"QSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:5px; "
                f"font-family:'Consolas'; font-size:13px; font-weight:600; padding:3px; }}")
            feet_spin = QDoubleSpinBox()
            feet_spin.setRange(MIN_REF, MAX_REF)
            feet_spin.setDecimals(4)
            feet_spin.setValue(0.0)
            feet_spin.setMinimumWidth(120)
            feet_spin.setStyleSheet(
                f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:5px; "
                f"font-family:'Consolas'; font-size:13px; font-weight:600; padding:3px; }}")
            cpf_lbl = QLabel("—")
            cpf_lbl.setAlignment(Qt.AlignRight)
            cpf_lbl.setMinimumWidth(70)
            cpf_lbl.setStyleSheet(f"background:transparent; color:{TEXT_MID.name()}; "
                                  "font-family:'Consolas'; font-size:13px; font-weight:600;")
            # promt2: per-layer CAPTURE CURRENT TICK — requests the Arduino's
            # authoritative live tick and drops it into THIS row's ENCODER COUNTS
            # field only. Never saves calibration; visually distinct from the
            # main CALIBRATE/SAVE-ALL button.
            capture_btn = QPushButton("CAPTURE")
            capture_btn.setCursor(Qt.PointingHandCursor)
            capture_btn.setMinimumWidth(88)
            capture_btn.setStyleSheet(self._capture_btn_style())
            capture_btn.setToolTip(
                "Request the CURRENT encoder tick count from the Arduino and "
                "place it into this wrap's ENCODER COUNTS field. The value "
                "stays editable so you can fine-tune it. Does NOT save "
                "calibration.")
            capture_btn.clicked.connect(lambda _c, idx=i: self._on_capture_tick(idx))
            # promt3: editing a cell marks the calibration as MODIFIED / UNSAVED.
            pulses_spin.valueChanged.connect(lambda _v, idx=i: self._mark_modified(idx))
            feet_spin.valueChanged.connect(lambda _v, idx=i: self._mark_modified(idx))
            row.addWidget(select_btn, 0)
            row.addWidget(layer_lbl, 1)
            row.addWidget(pulses_spin, 1)
            row.addWidget(feet_spin, 1)
            row.addWidget(capture_btn, 0)
            row.addWidget(cpf_lbl, 1)
            table_lay.addLayout(row)
            self._cal_rows.append((select_btn, layer_lbl, pulses_spin, feet_spin, cpf_lbl, i))
            self._capture_btns.append(capture_btn)
            self._capturing.append(False)
        _expl = QLabel(
            "Enter the ENCODER COUNTS (encoder counter anchor) and POSITION (FT) "
            "(position anchor) directly into a wrap row, or press that row's "
            "CAPTURE to read the CURRENT live tick from the Arduino into "
            "ENCODER COUNTS (editable). Then press CALIBRATE / SAVE ALL WRAPS "
            "to push the complete configuration to the device. "
            "COUNTS/FT is computed.")
        _expl.setWordWrap(True)
        _expl.setStyleSheet("color:" + TEXT_MID.name() + "; background:transparent;")
        table_lay.addWidget(_expl)
        cv.addWidget(table_card)
        root.addWidget(cal_group)
        # Kept so the Digital Sensor tab can scroll this existing calibration
        # section into view instead of opening a second calibration editor.
        self._cal_group = cal_group

        # ── BLOCK POSITION vs TIME TREND (promt1.txt) --------------------
        graph_group = QGroupBox("BLOCK POSITION TREND  —  block position vs time")
        graph_group.setStyleSheet(self._group_box_style(SECTION_TITLE))
        gl = QVBoxLayout(graph_group)
        gl.setContentsMargins(8, 14, 8, 8)
        self.graph = _IndustrialTrendGraph(self._trend_samples, QColor(0x00, 0x00, 0x00),
                                           default_range_s=0)
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
        self.graph_cur_lbl = QLabel("-- FT")
        self.graph_cur_lbl.setStyleSheet(f"background:transparent; color:{TEXT_DARK.name()}; "
                                         "font-family:'Consolas'; font-size:12px; font-weight:bold;")
        range_row.addWidget(suffix)
        range_row.addWidget(self.graph_cur_lbl)
        gl.addLayout(range_row)

        # Trend status indicator + PAUSE / RESUME + CLEAR TREND (promt1.txt).
        # Pausing freezes ONLY the chart view; the sampler keeps recording.
        # CLEAR TREND drops only the chart history.
        self._trend_paused = False
        self.trend_status_lbl = QLabel("AWAITING DATA")
        self.trend_status_lbl.setStyleSheet(self._trend_status_style("standby"))
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(self.trend_status_lbl)
        ctrl_row.addStretch(1)
        self._pause_btn = QPushButton("PAUSE VIEW")
        self._pause_btn.setCheckable(True)
        self._pause_btn.setCursor(Qt.PointingHandCursor)
        self._pause_btn.setStyleSheet(self._btn_style())
        self._pause_btn.setToolTip(
            "PAUSE VIEW freezes only the chart display. The encoder, Arduino "
            "link and trend recording continue running in the background.")
        self._pause_btn.toggled.connect(self._on_trend_pause)
        ctrl_row.addWidget(self._pause_btn)
        self._clear_trend_btn = QPushButton("CLEAR TREND")
        self._clear_trend_btn.setCursor(Qt.PointingHandCursor)
        self._clear_trend_btn.setStyleSheet(self._btn_style())
        self._clear_trend_btn.setToolTip(
            "CLEAR TREND removes only the chart history. Encoder tick, block "
            "position, calibration and wraps keep working; the chart resumes "
            "recording new live data immediately.")
        self._clear_trend_btn.clicked.connect(self._on_clear_trend)
        ctrl_row.addWidget(self._clear_trend_btn)
        gl.addLayout(ctrl_row)

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
            "count, velocity, direction, calStatus and current wraps are computed by the firmware.")
        formula_lbl.setWordWrap(True)
        formula_lbl.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:10px;")
        sg.addWidget(formula_lbl)

        # ── Command status (PENDING / SAVED / FAILED) ---------------------
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(
            f"color:{TEXT_ON_DARK.name()}; background:{BG_INNER.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:6px; "
            "font-family:'Segoe UI'; font-size:11px; font-weight:bold; padding:8px;")
        self.status_lbl.setMinimumHeight(34)
        root.addWidget(self.status_lbl)

        # ── Primary action bar (responsive: wraps to multiple rows) -------
        # promt.txt: buttons must remain visible, sized consistently and never
        # pushed off-screen on smaller laptops. A FlowLayout lets the full action
        # bar wrap to additional rows when the row no longer fits, so it never
        # forces horizontal overflow.
        actions = FlowLayout(owner=container)
        actions.setSpacing(10)
        self._action_flow = actions
        # promt2: ONE primary CALIBRATE / SAVE ALL LAYERS button — reads EVERY
        # layer input field, validates the complete set, and sends it all to the
        # firmware as one configuration. Per-layer single writes remain on the
        # SET LAYER button.
        self.calibrate_btn = QPushButton("CALIBRATE / SAVE ALL WRAPS")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_style())
        self.calibrate_btn.setToolTip(
            "Validate ALL wrap ENCODER COUNTS + POSITION (FT) rows together and "
            "save the complete configuration to the firmware. Success is shown "
            "only after the device confirms the saved values.")
        self.reset_btn = QPushButton("RESET COUNTER")
        self.reset_feet_btn = QPushButton("RESET POSITION")
        self.load_btn = QPushButton("LOAD SAVED CALIBRATION")
        self.save_btn = QPushButton("SAVE CALIBRATION")
        self.set_layer_btn = QPushButton("SET WRAP")
        self.set_height_btn = QPushButton("SET BLOCK HEIGHT")
        self.delete_level_btn = QPushButton("DELETE WRAP")
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
            btn.setMinimumWidth(150 if btn is self.calibrate_btn else 120)
            actions.addWidget(btn)
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
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
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
        # promt.txt two-role RBAC: OPERATOR = view-only (monitoring data stays
        # live, but EVERY configuration / calibration control is disabled);
        # ENGINEER = full access (all calibration + system controls enabled).
        engineer = self.roles.is_engineer()
        for btn in (self.calibrate_btn, self.load_btn, self.save_btn,
                    self.set_layer_btn, self.set_height_btn):
            btn.setEnabled(engineer)
        for btn in self._capture_btns:
            btn.setEnabled(engineer)
        for btn in (self.reset_btn, self.reset_feet_btn, self.delete_level_btn,
                    self.set_polarity_btn, self.set_wits_btn,
                    self.run_btn, self.cal_btn):
            btn.setEnabled(engineer)
        # The calibration table's input cells are view-only for Operators.
        for _sbtn, _llbl, pulses_spin, feet_spin, _cpf, _i in self._cal_rows:
            pulses_spin.setEnabled(engineer)
            feet_spin.setEnabled(engineer)
        # Pipe in Hole (promt.txt): OPERATOR views the value; only ENGINEER
        # may modify/save it.
        if hasattr(self, "pipe_in_hole_spin"):
            self.pipe_in_hole_spin.setEnabled(engineer)
        if hasattr(self, "pih_save_btn"):
            self.pih_save_btn.setEnabled(engineer)
        # Slip Window (slip_window.txt): same view/engineer-only rule.
        if hasattr(self, "slip_window_load_spin"):
            self.slip_window_load_spin.setEnabled(engineer)
        if hasattr(self, "slip_window_time_spin"):
            self.slip_window_time_spin.setEnabled(engineer)
        if hasattr(self, "slip_save_btn"):
            self.slip_save_btn.setEnabled(engineer)

    def _auto_return_to_operator(self):
        """promt.txt: after any successful Engineer modification, immediately
        return the role to OPERATOR (view-only). Only does something when an
        ENGINEER session is active; the role change notifies all RoleManager
        subscribers (status-bar indicator, this tab's _apply_role, header).
        """
        try:
            if self.roles is not None and self.roles.is_engineer():
                actor = self.roles.role()
                self.roles.set_role(ROLE_OPERATOR)
                try:
                    self.audit.record(
                        "ROLE_CHANGE", "Engineer auto-return to OPERATOR after modification",
                        actor)
                except Exception:
                    pass
        except Exception:
            pass

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

    def _cell_box_style(self):
        # Compact card chrome for individual value-table cells: slimmer
        # title, minimal top margin and padding so the 96px-high cells keep
        # room for the readout itself (table_value.txt).
        t = self._theme()
        bg = t.color_name("BG_CARD")
        border = t.color_name("BORDER")
        return (f"QGroupBox {{ color:{t.color_name('ACCENT')}; font-family:'Segoe UI'; "
                f"font-size:10px; font-weight:bold; letter-spacing:0.5px; "
                f"border:1px solid {border}; border-radius:8px; margin-top:2px; "
                f"padding-top:2px; background:{bg}; }} "
                f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; top:0px; "
                f"padding:1px 6px; color:{t.color_name('ACCENT')}; background:{bg}; "
                f"border-radius:4px; }}")

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
        self.calibrate_btn.setText(f"SAVING\u2026 WRAP {int(layer_pk)}")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_saving_style())
        self._cal_save_phase = "saving"
        self._save_flash_timer.stop()

    def _flash_not_saved(self, layer_pk):
        """Transient failure state: the write or persistence verification did
        NOT complete, so the button shows a non-success indicator and reverts
        to CALIBRATE (the success indicator is never shown on failure)."""
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText(f"\u2715  NOT SAVED \u2014 WRAP {int(layer_pk)}")
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
        self.calibrate_btn.setText(f"\u2713  SAVED \u2014 WRAP {int(layer_pk)}")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_saved_style())
        self._cal_save_phase = "saved"
        self._save_flash_timer.stop()
        self._save_flash_timer.start(1600)

    def _restore_calibrate_btn(self):
        if getattr(self, "calibrate_btn", None) is None:
            return
        self.calibrate_btn.setText("CALIBRATE / SAVE ALL WRAPS")
        self.calibrate_btn.setStyleSheet(self._calibrate_btn_style())
        self._cal_save_phase = "idle"

    def _cal_msg_style(self, kind):
        bg = {"saved": NORMAL_BG, "modified": WARN_BG,
              "saving": WARN_BG, "not_saved": FAULT_BG,
              "error": FAULT_BG, "idle": BG_INNER}.get(kind, BG_INNER)
        if not isinstance(bg, str):
            bg = bg.name()
        fc = "#FFFFFF" if kind in ("saved", "modified", "saving",
                                   "not_saved", "error") else TEXT_ON_DARK.name()
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

    def _unit_toggle_style(self, active):
        # Compact FT/M display toggle consistent with the industrial theme:
        # neutral when inactive, slate-blue when active (shows the CURRENT unit).
        base, hover, press = "#4C6B8A", "#3F5C77", "#2F475E"
        if active:
            return (f"QPushButton {{ background:{base}; color:#FFFFFF; border:none; "
                    f"border-radius:4px; font-family:'Segoe UI'; font-size:11px; "
                    f"font-weight:bold; padding:2px 6px; }} "
                    f"QPushButton:hover {{ background:{hover}; }} "
                    f"QPushButton:pressed {{ background:{press}; }}")
        return (f"QPushButton {{ background:#E8E2D6; color:#6B5E4E; border:none; "
                f"border-radius:4px; font-family:'Segoe UI'; font-size:11px; "
                f"font-weight:bold; padding:2px 6px; }} "
                f"QPushButton:hover {{ background:#D8CFC0; }} "
                f"QPushButton:pressed {{ background:#C8BDAD; }}")

    def _row_select_style(self, checked=False):
        if checked:
            return ("QPushButton { background:#4C6B8A; border-radius:4px; }")
        return ("QPushButton { background:#D8CFC0; border-radius:4px; }"
                "QPushButton:hover { background:#B5A999; }")

    def _capture_btn_style(self):
        # promt2: CAPTURE CURRENT TICK — visually distinct from the green main
        # CALIBRATE / SAVE ALL LAYERS button (slate blue secondary action).
        base, hover, press = "#4C6B8A", "#3F5C77", "#2F475E"
        return (f"QPushButton {{ background:{base}; color:#FFFFFF; border:none; "
                f"border-radius:5px; font-family:'Segoe UI'; font-size:11px; font-weight:bold; "
                f"padding:5px 10px; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                f"QPushButton:pressed {{ background:{press}; }} "
                f"QPushButton:disabled {{ background:#A9A394; color:#F0EAE0; }}")

    def _figure_style(self, tripping=False):
        border = "4px solid #7A5C10" if tripping else "1px solid #E4DCCE"
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                f"font-size:56px; font-weight:bold; border:{border}; "
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

    def _hook_figure_style(self):
        # Smaller white screen used by the three HOOKLOAD readouts (Low Point /
        # Current / High Point) so the card mirrors the Encoder Counter / Velocity
        # box.
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:30px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:10px; padding:6px;")

    def _block_figure_style(self):
        # White screen used by the BLOCK POSITION cell (largest readout on the
        # position table).
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:26px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:8px; padding:3px;")

    def _string_figure_style(self, tripping=False):
        border = "4px solid #7A5C10" if tripping else "1px solid #E4DCCE"
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                f"font-size:26px; font-weight:bold; border:{border}; "
                "border-radius:8px; padding:3px;")

    def _cell_figure_style(self):
        # White screen used by the ENCODER COUNTER / VELOCITY cells.
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:24px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:8px; padding:3px;")

    def _hook_cell_style(self):
        # White screen used by the HOOKLOAD INFORMATION cells.
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:26px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:8px; padding:3px;")

    def _slip_cell_style(self):
        # White screen used by the SLIP WINDOW INFORMATION cells.
        return ("color:#111111; background:#FFFFFF; font-family:'Consolas'; "
                "font-size:24px; font-weight:bold; border:1px solid #E4DCCE; "
                "border-radius:8px; padding:3px;")

    def _slip_status_style(self, kind):
        bg = {"CONFIRMED": NORMAL_BG, "CONFIRMING": WARN_BG}.get(kind, NEUTRAL_BG)
        return (f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; "
                "font-size:12px; font-weight:bold; border-radius:6px; padding:4px;")

    def _dir_style(self, kind):
        bg = {"up": NORMAL_BG, "down": NEUTRAL_BG,
              "on_bottom": WARN_BG, "stopped": NEUTRAL_BG, "none": NEUTRAL_BG}.get(kind, NEUTRAL_BG)
        return f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; font-size:13px; font-weight:bold; border-radius:6px; padding:5px;"

    def _cal_status_style(self, kind):
        bg = {"valid": NORMAL_BG, "out_of_range": FAULT_BG,
              "tripping": WARN_BG,
              "no_calibration": NEUTRAL_BG}.get(kind, NEUTRAL_BG)
        return f"color:#FFFFFF; background:{bg}; font-family:'Segoe UI'; font-size:10px; font-weight:bold; border-radius:6px; padding:3px;"

    # ––––––––––––––––––– trend sample source (promt1.txt) –––––––─
    # The chart draws from the controlled rolling sampler (mw.trend_data,
    # one point per second via a QTimer), which records the EXACT live
    # position the dashboard shows. The chart is READ-ONLY: it never
    # computes, estimates or fabricates position values.
    def _trend_samples(self, seconds):
        try:
            data = self.mw.trend_data
            if seconds and seconds > 0:
                cutoff = datetime.datetime.now() - datetime.timedelta(
                    seconds=float(seconds))
                return [(ts, pos) for ts, pos in data if ts >= cutoff]
            return list(data)
        except Exception:
            return []

    def _set_trend_range(self, secs, btn):
        for b, _s in self._range_buttons:
            b.setChecked(b is btn)
        self.graph.set_range_seconds(secs)

    def _trend_status_style(self, kind):
        bg = {"live": "#1E7A3E", "recording": "#D47B0F",
              "standby": "#8C826F"}.get(kind, "#8C826F")
        return (f"color:#FFFFFF; background:{bg}; border-radius:10px; padding:4px 10px; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def _refresh_trend_status(self):
        """Trend status: ● LIVE when new data is arriving, TREND RECORDING
        while the view is paused (sampling continues), otherwise STANDBY."""
        if self._trend_paused:
            self.trend_status_lbl.setText("TREND RECORDING")
            self.trend_status_lbl.setStyleSheet(self._trend_status_style("recording"))
            return
        data = self.mw.trend_data
        live = bool(data) and (datetime.datetime.now() - data[-1][0]).total_seconds() < 2.0
        if live:
            self.trend_status_lbl.setText("● LIVE")
            self.trend_status_lbl.setStyleSheet(self._trend_status_style("live"))
        else:
            self.trend_status_lbl.setText("AWAITING DATA")
            self.trend_status_lbl.setStyleSheet(self._trend_status_style("standby"))

    def _on_trend_pause(self, checked):
        """PAUSE VIEW / RESUME — freezes ONLY the chart display. The encoder,
        Arduino link and background trend recording keep running (promt1.txt)."""
        self._trend_paused = bool(checked)
        self.graph.set_paused(self._trend_paused)
        self._pause_btn.setText("RESUME" if self._trend_paused else "PAUSE VIEW")
        self._refresh_trend_status()

    def _on_clear_trend(self):
        """CLEAR TREND — drops only the chart history. Encoder tick, block
        position, calibration and wraps are untouched; the chart immediately
        resumes collecting new live data."""
        self.mw.clear_trend()
        self.graph.update()
        self._refresh_trend_status()

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
                            ("reset_feet", "Reset position"),
                            ("load_cal", "Load saved calibration"),
                            ("save_cal", "Save calibration"),
                            ("save_all", "Calibrate all wraps"),
                            ("set_layer", "Set wrap"),
                            ("set_height", "Set block height"),
                            ("delete_level", "Delete wrap"),
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
            f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
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
            f"QSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
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

    def _prompt_starting_ft(self):
        """Ask the operator for the NEW starting block position (FT).

        A dark-themed numeric dialog with a decimal input box and
        CONFIRM / CANCEL buttons. Only non-negative numeric feet
        values are accepted (the travelling-block position model is positive);
        CANCEL (or ESC) returns None and changes nothing.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("RESET STARTING POSITION")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(
            "QDialog { background:#20242C; }"
            "QLabel { color:#FFFFFF; font-size:13px; }"
            "QDoubleSpinBox { background:#181C24; color:#FFFFFF; border:1px solid #3A4050;"
            " border-radius:5px; padding:6px; font-size:15px; font-weight:bold; }")

        form = QFormLayout()
        form.setContentsMargins(20, 20, 20, 14)
        form.setSpacing(12)
        form.addRow("Enter Starting Feet Position:", None)

        spin = QDoubleSpinBox(dlg)
        spin.setRange(0.0, 999999999.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.5)
        spin.setValue(max(0.0, self.mw.block_position_ft))
        spin.setAlignment(Qt.AlignCenter)
        form.addRow("Starting position", spin)
        dlg.setLayout(form)

        # Buttons: CONFIRM / CANCEL (dark theme, white text).
        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, dlg)
        btns.button(QDialogButtonBox.Ok).setText("CONFIRM")
        btns.button(QDialogButtonBox.Cancel).setText("CANCEL")
        for b in btns.buttons():
            b.setStyleSheet(
                "color:#FFFFFF; background:#2A2F3A; border:1px solid #3A4050;"
                "padding:6px 16px; border-radius:5px; font-size:13px; font-weight:bold;")
        btns.button(QDialogButtonBox.Ok).setStyleSheet(
            "color:#FFFFFF; background:#1E7A3E; border:1px solid #2A9650;"
            "padding:6px 16px; border-radius:5px; font-size:13px; font-weight:bold;")
        form.addRow(btns)

        accepted = btns.accepted.connect(dlg.accept)
        _ = btns.rejected.connect(dlg.reject)
        del accepted
        if dlg.exec_() == QDialog.Accepted:
            return spin.value()
        return None

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
        return (f"\u2713 CALIBRATION SAVED \u2014 WRAP {int(layer_pk)}: "
                f"{int(pulses):} PULSES / {float(feet):.3f} ft")

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
        self._cal_saved_all = None
        # Editing a row selects it: CALIBRATE / SET LAYER act on the row the
        # operator is typing in, not the previous selection (which could be a
        # different layer and silently save the wrong values).
        self._select_row(idx)
        if self._cal_msg_kind != "error":
            self._show_cal_msg(
                "modified",
                f"CALIBRATION MODIFIED — UNSAVED (Wrap {idx + 1} edited). "
                "Press CALIBRATE / SAVE ALL WRAPS to save the complete set.")

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

    def _saved_msg_text(self):
        """Text of the green SAVED banner: all-layers confirmation if the last
        confirmed save was the CALIBRATE / SAVE ALL flow, otherwise the
        single-layer PULSES / FEET confirmation."""
        if self._cal_saved_all:
            return self._cal_saved_all
        if self._last_saved is not None:
            return self._save_confirmation(*self._last_saved)
        return f"CALIBRATION SAVED — WRAP {self._selected_row}."

    def _sync_cal_msg(self):
        # Recompute the MODIFIED / SAVED indicator from live row values so the
        # message stays accurate after status refreshes / apply acks.
        if (self._cal_msg_kind in ("error", "saving", "not_saved")
                or self._cal_saving_active or self._save_all_active):
            return  # keep the in-progress / explicit result visible
        if (self._cal_msg_kind == "saved"
                and _time.monotonic() < self._capture_msg_until):
            return  # keep a fresh "✓ TICK CAPTURED" visible for its grace window
        if self._calibration_dirty():
            self._show_cal_msg(
                "modified",
                "CALIBRATION MODIFIED — UNSAVED. Press CALIBRATE / SAVE ALL "
                f"WRAPS (Wrap {self._selected_row} last edited).")
        elif self._cal_dirty:
            # A previous edit has now been applied to the device: report SAVED.
            self._cal_dirty = False
            self._cal_msg_kind = "saved"
            self._show_cal_msg("saved", self._saved_msg_text())
        elif self._cal_msg_kind in ("saved", "modified"):
            # Values now agree with the device and no pending edit — keep the
            # "SAVED" confirmation (with the saved PULSES / FEET) visible.
            self._cal_msg_kind = "saved"
            self._show_cal_msg("saved", self._saved_msg_text())
        # else: idle — leave whatever message (or none) as-is.

    # ––––––––––––––––––––– promt3: local calibration validation –––––––
    def _validate_layer_input(self, layer_pk, pulses, feet, all_layers=None):
        """Validate one layer's ENCODER COUNTS / POSITION (FT) before saving (promt3 §8).

        Returns (ok:bool, error:str). Rejects invalid/empty/NaN/Inf inputs, and
        validates the whole calibration table as one coherent, mathematically
        valid set — exactly mirroring the firmware's authoritative rules
        (encoder_validate_calibration_point in encoder.cpp): a 0-position row
        is "not configured" (skipped), a stored anchor must have non-zero
        position, no duplicate pulses, no duplicate positions, and counters and
        positions must sort in the same (monotonic) direction so the
        piecewise-linear interpolation is well defined and continuous.

        ``all_layers`` is an optional dict ``{layer_pk: (pulses, feet)}``
        providing the operator-entered values for ALL layers (read from the
        spinbox widgets).  When given, pairwise consistency is checked against
        the OTHER operator-entered rows rather than the (possibly stale) device-
        confirmed values, so a complete CALIBRATE / SAVE ALL LAYERS pass can
        validate the whole new table as one coherent set.
        """
        mw = self.mw
        try:
            pulses_int = int(float(pulses))
        except (TypeError, ValueError):
            return False, f"Wrap {layer_pk}: PULSES must be an integer."
        try:
            feet_f = float(feet)
        except (TypeError, ValueError):
            return False, f"Wrap {layer_pk}: FEET must be a number."
        pulses_f = float(pulses_int)
        if not (math.isfinite(pulses_f) and math.isfinite(feet_f)):
            return False, f"Wrap {layer_pk}: PULSES / FEET must be finite (no NaN/Inf)."
        # A 0-ft position is "not configured" — the firmware rejects it as an
        # anchor (CAL_VAL_ZERO_POSITION) and treats (counter 0, 0 ft) as unused.
        if feet_f == 0.0:
            return False, (f"Wrap {layer_pk}: FEET cannot be 0.0 — a stored "
                           "anchor needs a non-zero physical position.")
        # Build the full snapshot with this layer's edit applied. Active rows are
        # those with non-zero position (the firmware's configured-mask semantics).
        rows = []
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            if i == layer_pk:
                c, p = pulses_int, feet_f
            elif all_layers and i in all_layers:
                c, p = all_layers[i]
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
                return False, f"Wrap {i}: non-finite value present — rejected."
        # Pairwise duplicate + direction-consistency (firmware §promt2).
        for a_i in range(len(active)):
            ca, pa, ia = active[a_i]
            for b_i in range(a_i + 1, len(active)):
                cb, pb, ib = active[b_i]
                if ca == cb:
                    return False, (f"Wrap {ia} and Wrap {ib}: duplicate "
                                   f"PULSES {ca} — rejected.")
                if abs(float(pa) - float(pb)) < 1e-6:
                    return False, (f"Wrap {ia} and Wrap {ib}: duplicate "
                                   f"FEET {float(pa):.3f} — rejected.")
                cAbove = (ca > cb)
                pAbove = (float(pa) > float(pb))
                if cAbove != pAbove:
                    return False, (f"Wrap {ia} / Wrap {ib}: encoder counter and "
                                   "physical position disagree on direction — rejected.")
        # The edited layer must itself be a configured (active) row.
        if not any(i == layer_pk for _, _, i in active):
            return False, f"Wrap {layer_pk}: position is 0.0 (not configured) — rejected."
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
                f"SAVING CALIBRATION \u2014 WRAP {layer_pk}: awaiting device "
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
            f"SAVING CALIBRATION \u2014 WRAP {layer_pk}: {int(pulses):} PULSES / "
            f"{float(feet):.3f} ft \u2026")
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
            # A single-layer save supersedes any previous "ALL LAYERS" banner.
            self._cal_saved_all = None
            self._last_saved = (layer_pk, pulses, feet)
            self._op_ok(op_kind, f"wrap {layer_pk} calibration saved")
            self._show_cal_msg("saved", self._save_confirmation(layer_pk, pulses, feet))
            self._flash_saved(layer_pk)
            _audit(True, "write+persist verified")
            self._refresh_status_line()
            self.refresh()
            self._auto_return_to_operator()

        def _finish_failed(reason, revert=None):
            self._cal_saving_active = False
            self._op_fail(op_kind, reason)
            self._show_cal_msg(
                "not_saved",
                f"\u2715 CALIBRATION NOT SAVED \u2014 WRAP {layer_pk}: {reason}")
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

            def _send_load(on_fail):
                rid = self.mw._cmd.load_calibration(
                    on_done=_load_verify, on_fail=on_fail)
                if rid is not None:
                    self._ops["load_cal"]["reqs"].add(rid)
                return rid

            self._retry_cmd(
                _send_load,
                lambda reason: _finish_failed(
                    f"persistence re-load failed: {reason}"))

        def _stored(req, data):
            # Value WRITE confirmed & verified by CommandTracker echo. Persist.
            self._ops.setdefault("save_cal", {"reqs": set(), "ok": None, "detail": ""})

            def _send_save(on_fail):
                rid = self.mw._cmd.save_calibration(
                    on_done=_persisted, on_fail=on_fail)
                if rid is not None:
                    self._ops["save_cal"]["reqs"].add(rid)
                return rid

            self._retry_cmd(
                _send_save,
                lambda reason: _finish_failed(f"EEPROM write failed: {reason}"))

        def _send_point(on_fail):
            rid = self.mw._cmd.set_calibration_point(
                layer_pk, feet, counter=pulses, on_done=_stored,
                on_fail=on_fail)
            if rid is not None:
                self._ops[op_kind]["reqs"].add(rid)
            return rid

        self._retry_cmd(_send_point, lambda reason: _finish_failed(reason))

    def _on_capture_tick(self, idx):
        """promt2: CAPTURE CURRENT TICK for ONE layer row.

        Requests the Arduino's AUTHORITATIVE live tick count via the `status`
        command and writes it into THIS row's ENCODER COUNTS field only. The
        value stays editable (operator can fine-tune before saving). CAPTURE
        never saves calibration and never touches the live counter. Success:
        "\u2713 TICK CAPTURED \u2014 LAYER N: <tick> PULSES"; failure (no reply
        / timeout / link loss): "\u2715 CAPTURE FAILED" and the field is left
        unchanged.
        """
        if idx >= len(self._capture_btns):
            return
        if self._capturing[idx]:
            return  # re-entrancy guard: one in-flight request per row
        if not self._ensure_serial("CAPTURE"):
            return
        layer_pk = idx + 1
        self._capturing[idx] = True
        self._capture_btns[idx].setText("\u2026")
        self._capture_btns[idx].setEnabled(False)

        def _done(_r, data):
            try:
                tick = int(data.get("currentTicks"))
            except (TypeError, ValueError):
                self._capture_failed(idx, "device reply carried no tick count")
                return
            if idx < len(self._capturing):
                self._capturing[idx] = False
            self._capture_btns[idx].setText("CAPTURE")
            self._capture_btns[idx].setEnabled(True)
            _sbtn, _llbl, pulses_spin, _ft, _cpf, _i = self._cal_rows[idx]
            pulses_spin.blockSignals(True)
            pulses_spin.setValue(tick)
            pulses_spin.blockSignals(False)
            self._mark_modified(idx)
            try:
                self.audit.record(
                    "CAPTURE_TICK",
                    f"layer {layer_pk} tick = {tick} (device-confirmed status)",
                    self.roles.role(), uptime=self.mw.encoder_uptime_s)
            except Exception:
                pass
            self._show_cal_msg(
                "saved",
                f"\u2713 TICK CAPTURED \u2014 WRAP {layer_pk}: {tick:} PULSES")
            self._capture_msg_until = _time.monotonic() + 4.0
            self.refresh()

        def _fail(_r, reason):
            self._capture_failed(idx, reason)

        if self._local():
            # Demo / no-device: the local display's current tick is the best
            # authoritative source available (there is no Arduino to ask).
            self._done_local_capture(idx, self.mw.current_ticks)
            return

        req_id = self.mw._cmd.capture_tick(on_done=_done, on_fail=_fail)
        if req_id is None:
            self._capture_failed(idx, "could not send (not connected?)")

    def _done_local_capture(self, idx, tick):
        layer_pk = idx + 1
        if idx < len(self._capturing):
            self._capturing[idx] = False
        self._capture_btns[idx].setText("CAPTURE")
        self._capture_btns[idx].setEnabled(True)
        _sbtn, _llbl, pulses_spin, _ft, _cpf, _i = self._cal_rows[idx]
        pulses_spin.blockSignals(True)
        pulses_spin.setValue(int(tick))
        pulses_spin.blockSignals(False)
        self._mark_modified(idx)
        try:
            self.audit.record(
                "CAPTURE_TICK",
                f"layer {layer_pk} tick = {int(tick)} (demo)", self.roles.role())
        except Exception:
            pass
        self._show_cal_msg(
            "saved",
            f"\u2713 TICK CAPTURED \u2014 WRAP {layer_pk}: {int(tick):} PULSES")
        self._capture_msg_until = _time.monotonic() + 4.0
        self.refresh()

    def _capture_failed(self, idx, reason):
        if idx >= len(self._capture_btns):
            return
        if idx < len(self._capturing):
            self._capturing[idx] = False
        self._capture_btns[idx].setText("CAPTURE")
        self._capture_btns[idx].setEnabled(True)
        try:
            self.audit.record(
                "CAPTURE_TICK",
                f"layer {idx + 1} FAILED: {reason} (field untouched)",
                self.roles.role(), uptime=self.mw.encoder_uptime_s)
        except Exception:
            pass
        self._show_cal_msg(
            "not_saved",
            f"\u2715 CAPTURE FAILED \u2014 WRAP {idx + 1}: {reason}")

    def _on_calibrate(self):
        """promt2: CALIBRATE / SAVE ALL LAYERS — validate EVERY row together,
        then push the COMPLETE configuration to the firmware as one operation.

        Flow: validate all rows -> snapshot the previous confirmed table ->
        stage each layer (two-stage, auto-confirmed) -> save_calibration
        (EEPROM) -> load_calibration -> verify ALL rows read back ->
        "\u2713 CALIBRATION SAVED" + complete config + last-saved time. On ANY
        failure / rejection / timeout / link loss the success indicator is
        NEVER shown: "\u2715 CALIBRATION NOT SAVED" and the previous confirmed
         calibration is restored (rollback) so it stays active.
        """
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("CALIBRATE / SAVE ALL WRAPS"):
            return
        if self._save_all_active:
            # Re-entrancy guard — never fire a second all-wraps pipeline.
            self._show_cal_msg(
                "saving",
                "SAVING CALIBRATION \u2014 ALL WRAPS: awaiting device "
                "confirmation\u2026")
            return
        if self._cal_saving_active and self._op_phase("set_layer")[0] == "pending":
            self._show_cal_msg(
                "saving",
                "SAVING CALIBRATION \u2014 awaiting the previous single-wrap "
                "confirmation\u2026")
            return

        # Validate the complete set BEFORE anything is sent. Every row must be
        # a configured anchor (feet != 0) and the four anchors must form a
        # monotonic, duplicate-free set (mirrors the firmware's rules).
        # Read ALL spinbox values FIRST so pairwise checks use the operator's
        # complete new table — not stale device values — fixing the false
        # "disagree on direction" rejection when all layers are edited at once.
        all_spin = {}     # layer_pk -> (pulses, feet) from the spinbox widgets
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            _sbtn, _llbl, p_spin, f_spin, _cpf, _i = self._cal_rows[i - 1]
            try:
                p_val = int(p_spin.value())
            except (TypeError, ValueError):
                p_val = 0
            try:
                f_val = float(f_spin.value())
            except (TypeError, ValueError):
                f_val = 0.0
            all_spin[i] = (p_val, f_val)
        rows = []          # (layer_pk, pulses, feet) — the complete config
        errors = []
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            pulses, feet = all_spin[i]
            ok, err = self._validate_layer_input(i, pulses, feet,
                                                  all_layers=all_spin)
            if not ok:
                errors.append(f"  Wrap {i}: {err}")
            else:
                rows.append((i, int(pulses), float(feet)))
        if errors:
            text = ("\u2715 CALIBRATION NOT SAVED \u2014 INVALID INPUT:\n"
                    + "\n".join(errors))
            self._show_cal_msg("error", text)
            QMessageBox.warning(self.window(), "Invalid Calibration", text)
            return

        self._save_all_active = True
        self._cal_editing = False
        self._ops["save_all"] = {"reqs": set(), "ok": None, "detail": ""}

        # Snapshot the PREVIOUS confirmed table (pre-op) for audit + rollback.
        snapshot = []
        for i in range(1, FW_MAX_CAL_POINTS + 1):
            try:
                cnt = int(self.mw.device.get(f"calCounter{i}", 0) or 0)
            except (TypeError, ValueError):
                cnt = 0
            try:
                ref = float(self.mw.device.get(f"calPosition{i}", 0.0) or 0.0)
            except (TypeError, ValueError):
                ref = 0.0
            snapshot.append((i, cnt, ref))

        self._show_cal_msg(
            "saving",
            f"SAVING CALIBRATION \u2014 ALL WRAPS: {len(rows)} wrap(s) to "
            f"write, persist and verify\u2026")
        self._flash_saving(rows[0][0])

        try:
            self.audit.record(
                "CALIBRATE_ALL",
                "requested config: " + self._config_str(rows),
                self.roles.role(), uptime=self.mw.encoder_uptime_s)
        except Exception:
            pass

        # ── LOCAL mode (demo / no device): store is settings.json. ──
        if self._local():
            for (layer, pulses, feet) in rows:
                self.mw.device[f"calPosition{layer}"] = feet
                self.mw.device[f"calCounter{layer}"] = pulses
            self.mw.device["confirmed"] = True
            self._recompute_demo_counts_per_foot()
            self.mw._persist_calibration()

            # Verify persistence by reading the store back from disk (all rows).
            from scarlet_test_panel.services import settings as settings_service
            persisted = settings_service.load().get("calibration") or {}
            for (layer, pulses, feet) in rows:
                if (int(persisted.get(f"calCounter{layer}", 0) or 0) != int(pulses)
                        or abs(float(persisted.get(f"calPosition{layer}", 0.0) or 0.0)
                               - float(feet)) > 1e-6):
                    self._finish_all_failed(
                        rows, snapshot,
                        f"persistence verification failed (wrap {layer} "
                        f"read back from store)")
                    return
            self._finish_all_saved(rows)
            return

        # ── REAL mode: firmware is authoritative. Chain stage -> stage -> ──
        # ── save_calibration (EEPROM) -> load_calibration -> verify.       ──
        n = len(rows)

        def _stage(idx):
            if idx >= n:
                _persist_all(rows)
                return
            layer, pulses, feet = rows[idx]

            def _staged(_r, _d):
                self._show_cal_msg(
                    "saving",
                    f"SAVING CALIBRATION \u2014 ALL WRAPS: staged wrap "
                    f"{idx + 1}/{n} ({int(pulses):} PULSES / "
                    f"{float(feet):.3f} ft)\u2026")
                # Pace the next layer so its set_calibration_point has clear air
                # from the previous ack/report. The serial link drops commands
                # sent back-to-back (~50% measured) but is ~100% reliable once
                # ~300ms separates them (firmware_build/pacing_sweep.py).
                QTimer.singleShot(
                    CAL_RETRY_DELAY_MS, lambda idx=idx: _stage(idx + 1))

            # Retry same layer on loss/timeout/rejection, then give up.
            self._stage_retry_send(idx, rows, snapshot, {"used": 0}, _staged)

        def _persist_all(ref_rows):
            def _persist_done(_r, _d):
                # EEPROM write acked (ec==0). Re-load and verify ALL rows.
                self._ops.setdefault("load_cal", {"reqs": set(), "ok": None,
                                                  "detail": ""})

                def _send_load(on_fail):
                    rid = self.mw._cmd.load_calibration(
                        on_done=lambda _r2, data: self._load_all_verify(
                            ref_rows, snapshot, data),
                        on_fail=on_fail)
                    if rid is not None:
                        self._ops["load_cal"]["reqs"].add(rid)
                    return rid

                def _load_fail(reason):
                    self._finish_all_failed(
                        ref_rows, snapshot,
                        f"persistence re-load failed: {reason}")

                self._retry_cmd(_send_load, _load_fail)

            def _persist_fail(reason):
                self._finish_all_failed(
                    ref_rows, snapshot, f"EEPROM write failed: {reason}")

            self._ops.setdefault("save_cal", {"reqs": set(), "ok": None,
                                              "detail": ""})

            def _send_save(on_fail):
                rid = self.mw._cmd.save_calibration(
                    on_done=_persist_done, on_fail=on_fail)
                if rid is not None:
                    self._ops["save_cal"]["reqs"].add(rid)
                return rid

            self._retry_cmd(_send_save, _persist_fail)

        _stage(0)

    def _stage_retry_send(self, idx, rows, snapshot, attempts, on_staged):
        """Send one calibration-point stage; retry on loss/timeout/rejection.

        `attempts` is a mutable {"used": int} counter shared across retries so a
        single genuinely-failing command is retried CAL_SEND_ATTEMPTS times
        before the pipeline gives up. Used because the serial link intermittently
        drops command frames (~50% measured), which otherwise surfaces as a
        spurious "no acknowledge device within 3sec".
        """
        layer, pulses, feet = rows[idx]
        attempts["used"] += 1

        def _retry_or_fail(_r, reason):
            if attempts["used"] < CAL_SEND_ATTEMPTS:
                QTimer.singleShot(
                    CAL_RETRY_DELAY_MS,
                    lambda: self._stage_retry_send(idx, rows, snapshot,
                                                   attempts, on_staged))
            else:
                self._finish_all_failed(
                    rows, snapshot, f"wrap {layer} rejected: {reason}")

        rid = self.mw._cmd.set_calibration_point(
            layer, feet, counter=pulses, on_done=on_staged,
            on_fail=_retry_or_fail)
        if rid is not None:
            self._ops["save_all"]["reqs"].add(rid)
        else:
            _retry_or_fail(None, "could not send (not connected?)")
        self.refresh()

    def _retry_cmd(self, send, on_final_fail,
                   delay=CAL_RETRY_DELAY_MS, attempts=None):
        """Run a command send with retry.

        `send(on_fail) -> rid` — a closure that calls a CommandTracker entry
        point passing our retry callback as on_fail, and returns the req id.
        Retries up to CAL_SEND_ATTEMPTS on timeout/rejection/link loss before
        invoking `on_final_fail(reason)`.
        """
        attempts = attempts if attempts is not None else {"used": 0}
        attempts["used"] += 1

        def _retry_or_fail(_r, reason):
            if attempts["used"] < CAL_SEND_ATTEMPTS:
                QTimer.singleShot(
                    delay, lambda: self._retry_cmd(send, on_final_fail,
                                                   delay, attempts))
            else:
                on_final_fail(reason)

        rid = send(_retry_or_fail)
        if rid is None:
            _retry_or_fail(None, "could not send (not connected?)")

    def _load_all_verify(self, rows, snapshot, data):
        # load_calibration echoes the reloaded ACTIVE config; verify EVERY row
        # reads back exactly what was saved before any success is shown.
        for (layer, pulses, feet) in rows:
            got_p = int(data.get(f"calCounter{layer}") or 0)
            got_f = float(data.get(f"calPosition{layer}") or 0.0)
            if got_p != int(pulses) or abs(got_f - float(feet)) > 1e-3:
                self._finish_all_failed(
                    rows, snapshot,
                    f"wrap {layer} read back {got_p}/{got_f:.4f} ft instead "
                    f"of {int(pulses)}/{float(feet):.4f} ft")
                return
        self._finish_all_saved(rows)

    def _finish_all_saved(self, rows):
        # The success indicator is shown ONLY after the firmware confirmed the
        # write AND verified persistence of the complete configuration.
        self._save_all_active = False
        self._cal_dirty = False
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_cal_saved_at = now
        try:
            from scarlet_test_panel.services import settings as settings_service
            settings_service.save({"cal_last_saved": now})
        except Exception:
            pass
        self._cal_saved_all = self._save_all_confirmation(rows)
        self._op_ok("save_all", f"{len(rows)} wrap(s) written + verified")
        self._show_cal_msg("saved", self._cal_saved_all)
        if getattr(self, "calibrate_btn", None) is not None:
            self.calibrate_btn.setText("\u2713  SAVED \u2014 ALL WRAPS")
            self.calibrate_btn.setStyleSheet(self._calibrate_btn_saved_style())
            self._cal_save_phase = "saved"
            self._save_flash_timer.stop()
            self._save_flash_timer.start(1600)
        try:
            self.audit.record(
                "CALIBRATE_ALL",
                "SAVED config: " + self._config_str(rows)
                + " (write + persist + load verified)",
                self.roles.role(), uptime=self.mw.encoder_uptime_s)
        except Exception:
            pass
        self._refresh_status_line()
        self.refresh()
        self._auto_return_to_operator()

    def _finish_all_failed(self, rows, snapshot, reason):
        # Never show success on a failed all-layers save; keep the previous
        # confirmed calibration active by rolling the device back to snapshot.
        self._save_all_active = False
        self._op_fail("save_all", reason)
        self._show_cal_msg(
            "not_saved",
            f"\u2715 CALIBRATION NOT SAVED \u2014 ALL WRAPS: {reason}")
        if getattr(self, "calibrate_btn", None) is not None:
            self.calibrate_btn.setText("\u2715  NOT SAVED")
            self.calibrate_btn.setStyleSheet(self._calibrate_btn_fail_style())
            self._cal_save_phase = "failed"
            self._save_flash_timer.stop()
            self._save_flash_timer.start(2000)
        self._rollback_layers(snapshot)
        try:
            self.audit.record(
                "CALIBRATE_ALL",
                f"FAILED: {reason} — previous config restored",
                self.roles.role(), uptime=self.mw.encoder_uptime_s)
        except Exception:
            pass
        self._refresh_status_line()
        self.refresh()

    def _rollback_layers(self, snapshot):
        """Roll the DEVICE back to the pre-op confirmed table after a failed
        all-layers save. Runs each snapshot row sequentially (single staged-op
        slot contract: a stage must be confirmed before the next is staged),
        then persists. Rows that were previously unconfigured are cleared
        rather than set to a 0-position anchor (which the firmware rejects)."""
        if self._local():
            for (i, c, f) in snapshot:
                self.mw.device[f"calCounter{i}"] = c
                self.mw.device[f"calPosition{i}"] = f
            self._recompute_demo_counts_per_foot()
            self.mw._persist_calibration()
            return

        steps = []
        for (layer, cnt, feet) in snapshot:
            if int(cnt) == 0 and abs(float(feet)) < 1e-9:
                steps.append(("clear", layer))
            else:
                steps.append(("set", layer, cnt, feet))
        steps.append(("save",))

        def _next(i):
            if i >= len(steps):
                return
            step = steps[i]
            if step[0] == "clear":
                rid = self.mw._cmd.clear_calibration_point(
                    step[1], on_done=lambda _r, _d: _next(i + 1),
                    on_fail=lambda _r, _z: _next(i + 1))
            elif step[0] == "set":
                rid = self.mw._cmd.set_calibration_point(
                    step[1], float(step[3]), counter=int(step[2]),
                    on_done=lambda _r, _d: _next(i + 1),
                    on_fail=lambda _r, _z: _next(i + 1))
            else:
                rid = self.mw._cmd.save_calibration(
                    on_done=lambda _r, _d: _next(i + 1),
                    on_fail=lambda _r, _z: _next(i + 1))
            if rid is None:
                _next(i + 1)  # write failed — move on; nothing more we can do

        _next(0)

    def _config_str(self, rows):
        return "; ".join(
            f"Wrap {l}={c} cnt/{f:.4f} ft" for (l, c, f) in rows)

    def _save_all_confirmation(self, rows):
        # The prominent green banner showing the COMPLETE saved configuration
        # plus the last save time — the operator must see every wrap's
        # PULSES / FEET, not a generic "saved" note.
        lines = ["\u2713 CALIBRATION SAVED \u2014 ALL WRAPS:"]
        for (layer, pulses, feet) in rows:
            lines.append(f"  Wrap {layer}: {int(pulses):} PULSES / "
                         f"{float(feet):.3f} ft")
        if self._last_cal_saved_at:
            lines.append(f"Last saved: {self._last_cal_saved_at}")
        return "\n".join(lines)

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
            f"Current: {pre_feet:.3f} ft  ({pre_ticks:} ticks)\n\n"
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
            self.mw._derive_direction(0)
            self.mw._set_reset_state(
                True, "RESET SUCCESSFUL — counter zeroed (demo)")
            self._op_ok("reset", "counter zeroed (demo); calibration unchanged")
            self.refresh()
            self._auto_return_to_operator()
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
        # position=0.00 ft, velocity=0. It NEVER touches the
        # calibration table (anchors, saved pulses/feet) or recalibrates.
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("RESET POSITION"):
            return
        phase, _d = self._op_phase("reset_feet")
        if phase == "pending":
            return  # re-entrancy guard (duplicate-reset prevention)

        pre_ticks = self.mw.current_ticks
        pre_feet = self.mw.block_position_ft
        start_ft = self._prompt_starting_ft()
        if start_ft is None:
            return  # CANCEL — change nothing

        self.audit.record("RESET_FEET",
                          f"reset feet requested (pre-reset ticks={pre_ticks}, "
                          f"position={pre_feet:.3f} ft -> new start={start_ft:.3f} ft) "
                          f"— calibration untouched",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self.mw.encoder_recovered = False
        self._reset_banner(False)

        if self._local():
            # Demo / no-device: apply the new starting position reference
            # directly. CRITICAL: do NOT re-base or persist calibration here —
            # RESET FEET leaves the calibration table completely unchanged.
            # Anchor the runtime reference to tick 0 (the reset datum) so the
            # simulated block reads `start_ft` AT tick 0 and then CONTINUES to
            # travel along the calibration — it must never sit frozen at the
            # datum after a reset.
            pts = self.mw.cal_points()
            base_abs = self.mw.calibrated_position(0)
            if base_abs is None:
                base_abs = pts[0][1] if pts else 0.0
            self.mw.set_runtime_reference(base_abs, start_ft)
            self.mw.current_ticks = 0
            self.mw.device["currentTicks"] = 0
            # Rewind the demo cycle to its boundary so the next demo tick
            # starts from tick 0 == start_ft and the block keeps moving.
            self.mw._demo_counter = 59
            self.mw._demo_prev_pos = start_ft
            self.mw._derive_direction(0)
            self.mw._set_block_position(start_ft)
            self.mw.device["blockPositionFt"] = start_ft
            self.mw.velocity_ft_min = 0.0
            self.mw.device["velocityFtMin"] = 0.0
            self.mw.current_layer = layers_service.derive_layer(
                self.mw.current_ticks, [p[0] for p in self.mw.cal_points()])
            self.mw._set_reset_state(
                True, f"STARTING POSITION RESET SUCCESSFULLY — "
                      f"New Starting Position: {start_ft:.2f} FT")
            self._ops["reset_feet"] = {"reqs": set(), "ok": None, "detail": ""}
            self._op_ok("reset_feet",
                        f"new start {start_ft:.3f} ft; calibration unchanged")
            self.refresh()
            self._auto_return_to_operator()
            return

        self._ops["reset_feet"] = {"reqs": set(), "ok": None, "detail": ""}

        def on_reset_feet_done(_r, _d):
            self._op_ok("reset_feet",
                        f"start {start_ft:.3f} ft confirmed; calibration unchanged")
            self._auto_return_to_operator()
            # Success banner driven via consume_reset_state on reset_feet_ack.

        def on_reset_feet_fail(_r, reason):
            self._op_fail("reset_feet", reason)
            self.mw._set_reset_state(False, f"RESET POSITION FAILED — {reason}")

        req_id = self.mw._cmd.reset_feet(
            start_ft=start_ft,
            on_done=on_reset_feet_done,
            on_fail=on_reset_feet_fail)
        if req_id is None:
            self._op_fail("reset_feet", "could not send (not connected?)")
            self.mw._set_reset_state(
                False, "RESET POSITION FAILED — could not send (not connected?)")
            return
        self._ops["reset_feet"]["reqs"].add(req_id)
        if hasattr(self, "reset_feet_btn"):
            self.reset_feet_btn.setEnabled(False)
            self.reset_feet_btn.setText("RESET POSITION PENDING…")
        self.refresh()

    # ––––––––––––––––––––– SET LAYER –––––––––––––––––──────────────
    def _on_set_layer(self):
        # The calibration table IS the input: the selected row's ENCODER COUNTS
        # (encoder counter anchor) and POSITION (FT) (position anchor) are read
        # straight from the editable cells and pushed to the firmware.
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        if not self._ensure_serial("SET WRAP"):
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
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
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
        dlg.setWindowTitle(f"Set Block Height — Wrap {layer_pk}")
        form = QFormLayout(dlg)
        hint = QLabel(
            f"Tell the system: “the block is currently at this known physical "
            f"position, on wrap {layer_pk}.” Either capture the live counter "
            f"as the anchor for that wrap, or enter the counter manually.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px;")
        form.addRow(hint)
        spin = self._spins(value=cur)
        form.addRow(f"Wrap {layer_pk} position (ft):", spin)
        cnt_widget, get_counter = self._counter_row(cur_cnt)
        form.addRow(f"Encoder counter (live now: {self.mw.current_ticks:}):", cnt_widget)
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
            self._auto_return_to_operator()
            return

        req_id = self.mw._cmd.set_wits_correction(
            value,
            on_done=lambda _r, _d: (self._op_ok(
                "set_wits", f"WITS correction = {value:.4f} ft"),
                self._auto_return_to_operator()),
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
        if not self._ensure_serial("DELETE WRAP"):
            return
        phase, _d = self._op_phase("delete_level")
        if phase == "pending":
            return

        layer_pk = self._selected_row
        cur = float(self.mw.device.get(f"calPosition{layer_pk}", 0.0) or 0.0)
        if cur == 0.0:
            QMessageBox.information(
                self.window(), "Delete Wrap",
                f"Wrap {layer_pk} has no calibration anchor to delete.")
            return
        ret = QMessageBox.question(
            self.window(), "Delete Wrap",
            f"Delete the calibration anchor for Wrap {layer_pk}?\n"
            f"(counter {int(self.mw.device.get(f'calCounter{layer_pk}', 0) or 0):} "
            f"→ {cur:.3f} ft).\n\nThe live encoder counter is NOT affected.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        self.audit.record("DELETE_CALIBRATION_POINT",
                          f"delete layer {layer_pk} anchor "
                          f"(counter {int(self.mw.device.get(f'calCounter{layer_pk}', 0) or 0)}, "
                          f"{cur:.3f} ft)",
                          self.roles.role(), uptime=self.mw.encoder_uptime_s)
        self._ops["delete_level"] = {"reqs": set(), "ok": None, "detail": ""}

        if self._local():
            self.mw.device[f"calPosition{layer_pk}"] = 0.0
            self.mw.device[f"calCounter{layer_pk}"] = 0
            self.mw.device["confirmed"] = True
            self._recompute_demo_counts_per_foot()
            self.mw._persist_calibration()
            self._op_ok("delete_level", f"wrap {layer_pk} anchor deleted (demo)")
            self.refresh()
            self._auto_return_to_operator()
            return

        req_id = self.mw._cmd.clear_calibration_point(
            layer_pk,
            on_done=lambda _r, _d: (self._op_ok(
                "delete_level", f"wrap {layer_pk} anchor deleted"),
                self._auto_return_to_operator()),
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
            f"QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
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
            self._auto_return_to_operator()
            return

        req_id = self.mw._cmd.set_encoder_polarity(
            polarity,
            on_done=lambda _r, _d: (self._op_ok(
                "set_polarity", f"polarity = {polarity:+d}"),
                self._auto_return_to_operator()),
            on_fail=lambda _r, reason: self._op_fail("set_polarity", reason))
        if req_id is None:
            self._op_fail("set_polarity", "could not send (not connected?)")
            return
        self._ops["set_polarity"]["reqs"].add(req_id)
        self.refresh()

    # ––––––––––––––––––––– SAVE / LOAD CALIBRATION –––––––––––––––––─
    def _on_save_calibration(self):
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
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
            self._auto_return_to_operator()
            return

        def _on_saved(_req, _data):
            # Firmware confirmed the save: mirror the active table to disk so
            # it survives restart/reload and stays the single source of truth.
            self.mw._persist_calibration()
            self._op_ok("save_cal", "calibration saved")
            self._auto_return_to_operator()

        req_id = self.mw._cmd.save_calibration(
            on_done=_on_saved,
            on_fail=lambda _r, reason: self._op_fail("save_cal", reason))
        if req_id is None:
            self._op_fail("save_cal", "could not send (not connected?)")
            return
        self._ops["save_cal"]["reqs"].add(req_id)
        self.refresh()

    def _on_load_calibration(self):
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
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
                self._cal_saved_all = None
                self._op_ok("load_cal", "saved calibration loaded (demo)")
                self._show_cal_msg(
                    "saved", "SAVED CALIBRATION LOADED — now active.")
                self._auto_return_to_operator()
            else:
                self._op_fail("load_cal", "no saved calibration found (demo)")
                self._show_cal_msg("error",
                                   "No saved calibration found to load.")
            self.refresh()
            return

        req_id = self.mw._cmd.load_calibration(
            on_done=lambda _r, _d: (
                setattr(self, "_cal_editing", False),
                setattr(self, "_cal_dirty", False),
                setattr(self, "_last_saved", None),
                setattr(self, "_cal_saved_all", None),
                self._op_ok("load_cal", "calibration loaded"),
                self._auto_return_to_operator(),
                self.refresh()),
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

    # ––––––––––––––––––––– FT/M display toggle (promt1.txt) –––––──
    # The internal block position is ALWAYS in FEET (block_position_ft).
    # METERS is DISPLAY ONLY: meters = feet x 0.3048. Switching units never
    # converts back, rescales, resets or touches encoder/calibration/layer.
    def _on_unit_toggle(self):
        """Toggle the Block Position display unit between FT and M."""
        self._position_unit = "FT" if self._position_unit == "M" else "M"
        self.update_block_position_display()
        # Bit machine first (see refresh): String Length must read the
        # committed bit depth of this update.
        self.update_bit_screen_display()
        self.update_string_length_display()

    def _on_save_pipe_in_hole(self):
        """Save the Pipe in Hole value (promt.txt). Engineer-only.
        Validates input, persists to settings, refreshes String Length."""
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        value = self.pipe_in_hole_spin.value()
        if value < 0.0:
            self.pih_msg_lbl.setText("Value must not be negative.")
            self.pih_msg_lbl.setStyleSheet(
                f"color:{FAULT_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            return
        ok = self.mw.set_pipe_in_hole(value)
        if ok:
            self.pih_msg_lbl.setText(
                f"✓ PIPE IN HOLE SAVED — {value:.2f} FT")
            self.pih_msg_lbl.setStyleSheet(
                f"color:{NORMAL_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            try:
                self.audit.record(
                    "PIPE_IN_HOLE",
                    f"pipe in hole set to {value:.2f} ft",
                    self.roles.role(), uptime=self.mw.encoder_uptime_s)
            except Exception:
                pass
            self._auto_return_to_operator()
            self.refresh()
        else:
            self.pih_msg_lbl.setText("Invalid input — enter a numeric value.")
            self.pih_msg_lbl.setStyleSheet(
                f"color:{FAULT_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def _on_save_slip_window(self):
        """Save the Slip Window Load (k-lb) + Time (seconds) (slip_window.txt).
        Engineer-only. Validates input, persists to settings, refreshes the
        readouts and advances the running confirmation state."""
        if not self.roles.at_least(ROLE_SUPERVISOR):
            return
        load_val = self.slip_window_load_spin.value()
        time_val = self.slip_window_time_spin.value()
        if load_val < 0.0:
            self.slip_msg_lbl.setText("Load must not be negative.")
            self.slip_msg_lbl.setStyleSheet(
                f"color:{FAULT_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            return
        if time_val <= 0.0:
            self.slip_msg_lbl.setText("Time must be greater than 0.")
            self.slip_msg_lbl.setStyleSheet(
                f"color:{FAULT_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            return
        ok_load = self.mw.set_slip_window_load(load_val)
        ok_time = self.mw.set_slip_window_time(time_val)
        if ok_load and ok_time:
            self.slip_msg_lbl.setText(
                f"✓ SLIP WINDOW SAVED — {load_val:.2f} k-lb / {time_val:.2f} sec")
            self.slip_msg_lbl.setStyleSheet(
                f"color:{NORMAL_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            try:
                self.audit.record(
                    "SLIP_WINDOW",
                    f"slip window load {load_val:.2f} k-lb / time {time_val:.2f} sec",
                    self.roles.role(), uptime=self.mw.encoder_uptime_s)
            except Exception:
                pass
            self._auto_return_to_operator()
            self.refresh()
        else:
            self.slip_msg_lbl.setText("Invalid input — enter numeric values.")
            self.slip_msg_lbl.setStyleSheet(
                f"color:{FAULT_BG}; background:transparent; "
                "font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def _readout_available_width(self):
        """Usable width of one readout section, in pixels."""
        vp = self._readout_viewport
        if vp is None:
            return 0
        # The section is a QGroupBox inside the scrollable page: the usable
        # width is the visible viewport minus the page margins and the section
        # frame.
        page = getattr(self, "_readout_scroll", None)
        page = page.widget() if page is not None else None
        chrome = READOUT_PAGE_MARGIN
        if page is not None and page.layout() is not None:
            m = page.layout().contentsMargins()
            chrome += m.left() + m.right()
        return max(0, vp.width() - chrome)

    def _readout_column_count(self, available):
        """How many equal columns fit, like the Dashboard value table."""
        if available <= 0:
            return 1
        for cols in range(READOUT_MAX_COLUMNS, 0, -1):
            need = cols * READOUT_MIN_CELL_W + (cols - 1) * READOUT_GAP
            if available >= need:
                return cols
        return 1

    def _relayout_readout(self, force=False):
        """Give every value cell the width of one responsive column.

        The column count follows the visible width (5 -> 4 -> 3 -> 2 -> 1, like
        the Dashboard drilling monitor) and every cell gets that width, so the
        FlowLayout of a section wraps them into full equal rows. The cells of a
        short last row are widened so the table always reaches the right edge
        and no horizontal scrollbar is needed. The widths are only touched
        when the column count changes, so it is safe to call on every resize.
        """
        rows = getattr(self, "_readout_rows", None)
        if not rows:
            return
        available = self._readout_available_width()
        if available <= 0:
            return
        widest = max(len(cells) for _sec, _flow, cells in rows)
        if not widest:
            return
        cols = min(self._readout_column_count(available), widest)
        if not force and cols == self._readout_columns:
            return
        self._readout_columns = cols
        cell_w = (available - (cols - 1) * READOUT_GAP) // cols
        for _sec, _flow, cells in rows:
            self._place_readout_row(cells, cols, cell_w)

    @staticmethod
    def _place_readout_row(cells, cols, cell_w):
        """Size the cells of one section: equal columns, filled last row."""
        n = len(cells)
        for start in range(0, n, cols):
            chunk = cells[start:start + cols]
            m = len(chunk)
            # A short last row spreads over the remaining width so the table
            # never leaves a hole on the right.
            width = cell_w if m == cols else (
                cell_w * cols - (cols - m) * READOUT_GAP) // m
            for cell in chunk:
                if cell.width() != width:
                    cell.setFixedWidth(width)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_readout_viewport", None) and event.type() == QEvent.Resize:
            self._relayout_readout()
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        # The tab is usually built while hidden, so the first real layout
        # happens here; place the table once the viewport has its real width.
        QTimer.singleShot(0, lambda: self._relayout_readout(force=True))

    def _build_bit_screen(self, card):
        """Populate a small always-visible "screen" card that shows the LIVE
        BIT POSITION (the bit's depth) in the same dark LCD style."""
        bv = QVBoxLayout(card)
        bv.setContentsMargins(8, 4, 8, 4)
        bv.setSpacing(4)
        screen = QFrame()
        screen.setFrameShape(QFrame.StyledPanel)
        screen.setStyleSheet(
            "QFrame { background:#141B24; border:2px solid #3A4B5C; "
            "border-radius:10px; }")
        sbox = QVBoxLayout()
        sbox.setSpacing(2)
        sbox.setContentsMargins(6, 3, 6, 4)
        self.bit_screen_cap = QLabel("BIT POSITION (FT)")
        self.bit_screen_cap.setAlignment(Qt.AlignCenter)
        self.bit_screen_cap.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:9px; "
            "letter-spacing:2px; background:transparent;")
        sbox.addWidget(self.bit_screen_cap)
        self.bit_screen_lbl = QLabel("--")
        self.bit_screen_lbl.setAlignment(Qt.AlignCenter)
        self.bit_screen_lbl.setStyleSheet(
            "color:#47E08A; font-family:'Consolas'; font-size:22px; "
            "font-weight:bold; background:transparent;")
        sbox.addWidget(self.bit_screen_lbl)
        self.bit_screen_sub_lbl = QLabel("")
        self.bit_screen_sub_lbl.setAlignment(Qt.AlignCenter)
        self.bit_screen_sub_lbl.setStyleSheet(
            "color:#9FB6C9; font-family:'Consolas'; font-size:9px; "
            "background:transparent;")
        sbox.addWidget(self.bit_screen_sub_lbl)
        screen.setLayout(sbox)
        bv.addWidget(screen)

    def update_bit_screen_display(self):
        """Live BIT POSITION screen (promt.txt, hookload-conditioned). The bit
        only moves while pipe load is proven (the live channel-0 Hookload, or
        the Slip Window confirmation when enabled): it is driven by the ACTUAL
        block delta (New Bit = Previous Bit - Block Delta, re-anchored on every
        pipe-load engagement), moving OPPOSITE to the block while String Length
        = Block + Bit stays constant. Without pipe load the depth HOLDS at the
        last valid value, so small block movement / sensor noise never creeps
        it. Feet internally; shown in the selected FT / M display unit."""
        unit = self._position_unit if self._position_unit in ("FT", "M") else "FT"
        self.bit_screen_cap.setText(f"BIT POSITION ({unit})")
        if self.mw.pipe_in_hole_ft is None:
            self.bit_screen_lbl.setText("--")
            self.bit_screen_sub_lbl.setText("set pipe in hole")
            return
        bit_ft = self.mw.bit_position_ft
        state = self.mw.bit_position_state
        if bit_ft is None:
            self.bit_screen_lbl.setText("--")
            self.bit_screen_lbl.setStyleSheet(
                "color:#9FB6C9; font-family:'Consolas'; font-size:22px; "
                "font-weight:bold; background:transparent;")
            self.bit_screen_sub_lbl.setText("● HOLD — no pipe load yet")
            return
        value = float(bit_ft) * FT_TO_M if unit == "M" else float(bit_ft)
        self.bit_screen_lbl.setText(f"{value:.2f}")
        self.bit_screen_lbl.setStyleSheet(
            ("color:#47E08A;" if state == "TRACK" else "color:#E8C15A;")
            + " font-family:'Consolas'; font-size:22px; "
            + "font-weight:bold; background:transparent;")
        bp = float(self.mw.block_position_ft or 0.0)
        pih = float(self.mw.pipe_in_hole_ft or 0.0)
        tag = "TRACKING" if state == "TRACK" else "HOLD"
        self.bit_screen_sub_lbl.setText(
            f"Block {bp:.1f} ft  ·  Pipe in Hole {pih:.1f} ft  ·  {tag}")

    def _build_hole_depth_screen(self, card):
        """Populate a small always-visible "screen" card that shows the HOLE
        DEPTH (the deepest Bit Position reached — accumulated, non-decreasing)
        in the same dark LCD style as the BIT POSITION screen."""
        hdv = QVBoxLayout(card)
        hdv.setContentsMargins(8, 4, 8, 4)
        hdv.setSpacing(4)
        screen = QFrame()
        screen.setFrameShape(QFrame.StyledPanel)
        screen.setStyleSheet(
            "QFrame { background:#141B24; border:2px solid #3A4B5C; "
            "border-radius:10px; }")
        sbox = QVBoxLayout()
        sbox.setSpacing(2)
        sbox.setContentsMargins(6, 3, 6, 4)
        self.hole_depth_screen_cap = QLabel("HOLE DEPTH (FT)")
        self.hole_depth_screen_cap.setAlignment(Qt.AlignCenter)
        self.hole_depth_screen_cap.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:9px; "
            "letter-spacing:2px; background:transparent;")
        sbox.addWidget(self.hole_depth_screen_cap)
        self.hole_depth_screen_lbl = QLabel("--")
        self.hole_depth_screen_lbl.setAlignment(Qt.AlignCenter)
        self.hole_depth_screen_lbl.setStyleSheet(
            "color:#47E08A; font-family:'Consolas'; font-size:22px; "
            "font-weight:bold; background:transparent;")
        sbox.addWidget(self.hole_depth_screen_lbl)
        self.hole_depth_screen_sub_lbl = QLabel("")
        self.hole_depth_screen_sub_lbl.setAlignment(Qt.AlignCenter)
        self.hole_depth_screen_sub_lbl.setStyleSheet(
            "color:#9FB6C9; font-family:'Consolas'; font-size:9px; "
            "background:transparent;")
        sbox.addWidget(self.hole_depth_screen_sub_lbl)
        screen.setLayout(sbox)
        hdv.addWidget(screen)

    def update_hole_depth_display(self):
        """Live HOLE DEPTH screen (promt.txt): the deepest Bit Position the
        string has reached — an accumulated running maximum that never
        decreases. It reads the live bit_position_ft every tick (so the bit
        state machine advances in step), then keeps the highest value: the
        depth HOLDS while tripping / coming out of the hole and only advances
        again when the bit runs deeper. Feet internally; shown in the
        selected FT / M display unit."""
        unit = self._position_unit if self._position_unit in ("FT", "M") else "FT"
        self.hole_depth_screen_cap.setText(f"HOLE DEPTH ({unit})")
        hd_ft = self.mw.hole_depth_ft
        if hd_ft is None:
            self.hole_depth_screen_lbl.setText("--")
            self.hole_depth_screen_sub_lbl.setText("set pipe in hole")
            return
        value = float(hd_ft) * FT_TO_M if unit == "M" else float(hd_ft)
        self.hole_depth_screen_lbl.setText(f"{value:.2f}")
        # Status line: whether the current bit still matches the deepest depth
        # (at bottom) or is shallower (tripping / about to go back in). Uses
        # the bit recorded by bit_position_ft this tick — never re-reads the
        # property (which would re-advance the bit state machine).
        current_bit = self.mw.hole_last_bit_ft
        if current_bit is None or float(current_bit) >= float(hd_ft) - 1e-9:
            self.hole_depth_screen_sub_lbl.setText("● AT MAX DEPTH")
        else:
            self.hole_depth_screen_sub_lbl.setText("● HOLDS - TRIPPING")

    def _build_bottom_screen(self, card):
        """Populate an always-visible "screen" card that shows whether the
        string is ON BOTTOM (the live bit position equals the deepest depth
        reached) or OFF BOTTOM (the bit is shallower — tripping / about to go
        back in), in the same dark LCD style as the BIT POSITION screen."""
        btv = QVBoxLayout(card)
        btv.setContentsMargins(8, 4, 8, 4)
        btv.setSpacing(4)
        screen = QFrame()
        screen.setFrameShape(QFrame.StyledPanel)
        screen.setStyleSheet(
            "QFrame { background:#141B24; border:2px solid #3A4B5C; "
            "border-radius:10px; }")
        sbox = QVBoxLayout()
        sbox.setSpacing(2)
        sbox.setContentsMargins(6, 3, 6, 4)
        self.bottom_screen_cap = QLabel("STRING STATE")
        self.bottom_screen_cap.setAlignment(Qt.AlignCenter)
        self.bottom_screen_cap.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:9px; "
            "letter-spacing:2px; background:transparent;")
        sbox.addWidget(self.bottom_screen_cap)
        self.bottom_screen_lbl = QLabel("--")
        self.bottom_screen_lbl.setAlignment(Qt.AlignCenter)
        self.bottom_screen_lbl.setMinimumHeight(26)
        self.bottom_screen_lbl.setStyleSheet(self._bottom_style("none"))
        sbox.addWidget(self.bottom_screen_lbl)
        self.bottom_screen_sub_lbl = QLabel("")
        self.bottom_screen_sub_lbl.setAlignment(Qt.AlignCenter)
        self.bottom_screen_sub_lbl.setStyleSheet(
            "color:#9FB6C9; font-family:'Consolas'; font-size:9px; "
            "background:transparent;")
        sbox.addWidget(self.bottom_screen_sub_lbl)
        screen.setLayout(sbox)
        btv.addWidget(screen)

    def _bottom_style(self, kind):
        bg = {"on": NORMAL_BG, "off": WARN_BG, "none": NEUTRAL_BG}
        return (f"color:#FFFFFF; background:{bg.get(kind, NEUTRAL_BG)}; "
                "font-family:'Segoe UI'; font-size:12px; font-weight:bold; "
                "border-radius:6px; padding:4px;")

    def update_bottom_screen_display(self):
        """Live ON BOTTOM / OFF BOTTOM screen: derived from the single bit
        depth. ON BOTTOM when the live bit position equals the deepest depth
        reached (hole depth); OFF BOTTOM when the bit is shallower (tripping,
        string above bottom). Uses the values recorded by bit_position_ft this
        tick — never re-reads the property (which would re-advance the bit
        state machine)."""
        mw = self.mw
        hd_ft = mw.hole_depth_ft
        if hd_ft is None:
            self.bottom_screen_lbl.setText("--")
            self.bottom_screen_lbl.setStyleSheet(self._bottom_style("none"))
            self.bottom_screen_sub_lbl.setText("set pipe in hole")
            return
        current_bit = mw.hole_last_bit_ft
        if current_bit is None:
            self.bottom_screen_lbl.setText("--")
            self.bottom_screen_lbl.setStyleSheet(self._bottom_style("none"))
            self.bottom_screen_sub_lbl.setText("no bit depth yet")
            return
        cb = float(current_bit)
        hd = float(hd_ft)
        if cb >= hd - 1e-9:
            self.bottom_screen_lbl.setText("ON BOTTOM")
            self.bottom_screen_lbl.setStyleSheet(self._bottom_style("on"))
            self.bottom_screen_sub_lbl.setText(
                f"Bit {cb:.1f} ft = Hole Depth {hd:.1f} ft")
        else:
            self.bottom_screen_lbl.setText("OFF BOTTOM")
            self.bottom_screen_lbl.setStyleSheet(self._bottom_style("off"))
            self.bottom_screen_sub_lbl.setText(
                f"Bit {cb:.1f} ft < Hole Depth {hd:.1f} ft")

    def update_block_position_display(self):
        """Single source of truth for the Block Position readout. Reads the
        internal feet value, converts to meters ONLY for display when the
        selected unit is M, and updates the label + toggle button + trend."""
        unit = self._position_unit if self._position_unit in ("FT", "M") else "FT"
        feet = float(self.mw.block_position_ft or 0.0)
        if unit == "M":
            value = feet * 0.3048
            label = f"{value:.2f} M"
        else:
            label = f"{feet:.2f} FT"
        self.pos_value_lbl.setText(label)
        # The button shows the unit you switch TO (promt1.txt: button = target).
        self.unit_toggle_btn.setText("M" if unit == "FT" else "FT")
        self.unit_toggle_btn.setStyleSheet(
            self._unit_toggle_style(active=(unit == "FT")))
        # The trend chart respects the same display unit (feet stay internal;
        # the chart converts only its drawn values/labels).
        self.graph.set_unit(unit)

    def update_hookload_display(self):
        """Update the three HOOKLOAD readouts (Low Point / Current / High
        Point). Single source: the Analog Monitor's live channel-0 value
        (mw.hookload_klb). Low / High Point are the ENGINEERING VALUES of the
        Hookload two-point calibration (SENSOR_CONFIG[0] cal_val_lo /
        cal_val_hi — the "Engineering Value (klb)" fields entered on the
        calibrate tab for the LOW / HIGH CALIBRATION POINT)."""
        cfg = SENSOR_CONFIG[0]
        lo_val = cfg.get("cal_val_lo")
        hi_val = cfg.get("cal_val_hi")
        if lo_val is not None:
            self.hook_low_lbl.setText(f"{float(lo_val):.2f}")
        else:
            self.hook_low_lbl.setText("--")
        if hi_val is not None:
            self.hook_high_lbl.setText(f"{float(hi_val):.2f}")
        else:
            self.hook_high_lbl.setText("--")
        hk = self.mw.hookload_klb
        if hk is None:
            self.hook_current_lbl.setText("--")
        else:
            self.hook_current_lbl.setText(f"{float(hk):.2f}")
        v = self.mw.analog_voltages[0]
        if v is None:
            self.hook_voltage_lbl.setText("--")
        else:
            self.hook_voltage_lbl.setText(f"{float(v):.3f}")

    def update_string_length_display(self):
        """Update the STRING LENGTH readout. Reads the authoritative tracked
        string_length_ft (promt2.txt): String Length = Block Position + Bit
        Position, constant during a pipe trip, following the block when the bit
        holds without pipe load. Converts to METERS only for display."""
        unit = self._position_unit if self._position_unit in ("FT", "M") else "FT"
        sl_ft = self.mw.string_length_ft
        if sl_ft is None:
            self.string_length_lbl.setText("--")
            self.string_length_lbl.setStyleSheet(self._string_figure_style())
            self.sl_status_lbl.setText("● PIPE IN HOLE NOT SET")
            self.sl_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))
            return
        if unit == "M":
            value = float(sl_ft) * FT_TO_M
            label = f"{value:.2f} M"
        else:
            label = f"{sl_ft:.2f} FT"
        tripping = self.mw.bit_position_state == "TRACK"
        self.string_length_lbl.setText(label)
        self.string_length_lbl.setStyleSheet(self._string_figure_style(tripping=tripping))
        pipe_in_hole = float(self.mw.pipe_in_hole_ft or 0.0)
        if tripping:
            # During pipe load the bit compensates block movement exactly, so
            # String Length is legitimately flat; make that read as INTENTIONAL
            # instead of a frozen/broken display (promt.txt §5/§6/§16).
            self.sl_status_lbl.setText(
                "■ STRING LENGTH CONSTANT - TRIPPING (PIPE LOAD)")
            self.sl_status_lbl.setStyleSheet(self._cal_status_style("tripping"))
        else:
            self.sl_status_lbl.setText(
                f"● PIPE IN HOLE {pipe_in_hole:.2f} FT · BIT FROZEN - SL FOLLOWS BLOCK")
            self.sl_status_lbl.setStyleSheet(self._cal_status_style("valid"))
        spin = self.pipe_in_hole_spin
        if not spin.hasFocus() and abs(spin.value() - pipe_in_hole) > 0.000001:
            spin.blockSignals(True)
            spin.setValue(pipe_in_hole)
            spin.blockSignals(False)

    def update_slip_window_display(self):
        """Update the Slip Window status + timer readouts (slip_window.txt).
        Advances the confirmation machine off the live analog channel-0
        hookload and shows the running confirmation TIME while CONFIRMING.
        The LOAD cell shows the EFFECTIVE Slip Window Load = the configured
        (input) load + the Hookload MINIMUM VALUE (channel-0 cal low point);
        the TIME cell mirrors the configured span (single source:
        mw.slip_window_effective_load_klb / mw.slip_window_time_sec)."""
        mw = self.mw
        mw._update_slip_window()
        status = mw.slip_window_status
        timer = mw.slip_window_timer_sec
        self.slip_status_lbl.setText(status if status else "NOT ACTIVE")
        self.slip_status_lbl.setStyleSheet(self._slip_status_style(status))
        if mw.slip_window_enabled and mw.slip_window_time_sec is not None:
            self.slip_timer_lbl.setText(f"{timer:.2f} / "
                                        f"{float(mw.slip_window_time_sec):.2f} sec")
        else:
            self.slip_timer_lbl.setText("-- / -- sec")
        load = mw.slip_window_effective_load_klb
        if load is not None:
            self.slip_load_disp_lbl.setText(f"{float(load):.2f}")
        else:
            self.slip_load_disp_lbl.setText("--")
        tm = mw.slip_window_time_sec
        if tm is not None:
            self.slip_time_disp_lbl.setText(f"{float(tm):.2f}")
        else:
            self.slip_time_disp_lbl.setText("--")

    # ––––––––––––––––––––– per-tick refresh –––––––––––––––––────────
    def reveal_calibration(self):
        """Scroll the EXISTING calibration section into view. Used by the
        Digital Sensor tab so its BLOCK POSITION option opens the calibration
        dashboard itself rather than duplicating any part of it."""
        page = getattr(self, "_readout_scroll", None)
        group = getattr(self, "_cal_group", None)
        if page is None or group is None:
            return False
        page.ensureWidgetVisible(group, 0, 12)
        return True

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
        self.update_block_position_display()
        # Advance the bit position machine BEFORE the string length readout:
        # String Length = Block + Bit must reflect the committed bit depth of
        # this tick (promt.txt §1/§16), so the bit screen updates first.
        self.update_bit_screen_display()
        self.update_hole_depth_display()
        self.update_bottom_screen_display()
        self.update_hookload_display()
        self.update_string_length_display()
        self.update_slip_window_display()
        self.velocity_lbl.setText(f"{mw.velocity_ft_min:.2f}" if mw.cal_status else "--")
        self.counter_lbl.setText(f"{mw.current_ticks:}")

        # Calibration status -------------------------------------------------
        cal_status = cal_status_raw
        if cal_status == "OUT_OF_RANGE":
            self.cal_status_lbl.setText("OUT OF RANGE")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("out_of_range"))
        elif cal_status == "VALID":
            self.cal_status_lbl.setText("● VALID")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("valid"))
        else:
            self.cal_status_lbl.setText("● NO CALIBRATION")
            self.cal_status_lbl.setStyleSheet(self._cal_status_style("no_calibration"))

        # Direction (2-state: UP / DOWN ONLY). promt.txt requires the dashboard
        # to show only UP or DOWN and NEVER NONE/IDLE/STOPPED. `mw.direction` is
        # derived from the live tick delta (kept at the last valid direction
        # when the encoder stops); ON BOTTOM is tracked separately below.
        direction = (mw.direction or "DOWN").upper()
        if direction == "UP":
            self.dir_lbl.setText("▲ UP")
            self.dir_lbl.setStyleSheet(self._dir_style("up"))
        else:
            self.dir_lbl.setText("▼ DOWN")
            self.dir_lbl.setStyleSheet(self._dir_style("down"))

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
            cpf_lbl.setText(f"{_cpf:.2f}" if _cpf > 0.0 else "—")
            layer_lbl.setText(f"Wrap {layer_pk}")
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
            f"WITS: {mw.device.get('witsCorrectionFt', 0.0):.2f} ft  "
            f"Config: {conf_txt}")

        # Graph + current point ------------------------------------------------
        ft = float(mw.block_position_ft or 0.0)
        cunit = self._position_unit if self._position_unit in ("FT", "M") else "FT"
        cdisp = ft * FT_TO_M if cunit == "M" else ft
        self.graph_cur_lbl.setText(f"{cdisp:.2f} {cunit}")
        self.graph.update()
        self._refresh_trend_status()

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

        self.reset_feet_btn.setText("RESET POSITION")
        phase, _ = self._op_phase("reset_feet")
        if phase == "pending":
            self.reset_feet_btn.setText("RESET POSITION PENDING…")
            self.reset_feet_btn.setEnabled(False)
        else:
            # Re-enable after success / failure / timeout (duplicate prevention
            # only while the request is in-flight).
            self.reset_feet_btn.setEnabled(True)