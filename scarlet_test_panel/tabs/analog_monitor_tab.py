"""Analog Monitor tab (analog_monitor.txt).

A single-screen, non-scrolling industrial monitoring table for all configured
analog sensors.  Column order is fixed: Device | Channel | Sensor Name | Value
| Unit | Volt | Enable | Calibration.

Everything is read FRESH from the existing services on refresh() — nothing is
recalculated with a second engine, nothing writes to the firmware, and no new
serial/sensor loop is created (the main dashboard tick drives this tab via
mw._tick, exactly like the other tabs).  The linear voltage->value mapping is
the SAME single formula used everywhere else (SENSOR_CONFIG cal_min/cal_max),
and the Calibration button opens the reusable SensorCalibrationDialog (per
analog_monitor.txt §685-1373), which commits through the SAME existing
AnalogTab._on_calibrated callback — so there is no duplicated calibration
logic and per-channel calibration persists in settings.json.
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QCheckBox,
    QPushButton, QFrame, QSizePolicy,
)
from PyQt5.QtCore import Qt

from ..config import (
    NUM_ANALOG, SENSOR_CONFIG, BG_CARD, BORDER, ACCENT,
    TEXT_DARK, TEXT_MID, TEXT_LITE, CAL_MAX_CLR, CAL_BAND_CLR,
)
from ..services.settings import load as load_settings
from ..services.settings import save as save_settings
from ..dialogs.sensor_calibration_dialog import (
    SensorCalibrationDialog, project_two_point,
)

_OK = CAL_MAX_CLR          # green  — normal / simulation
_WARN = CAL_BAND_CLR       # amber  — stale / disabled-dim
_BAD = ACCENT              # red    — comm error / no data / disabled
_SOFT = TEXT_LITE          # faint  — idle

_HEADERS = ["Device", "Channel", "Sensor Name", "Value", "Unit",
            "Volt", "Enable", "Calibration"]
# Responsive column weights: all 8 columns share the available width in
# proportion to these base weights (so wide windows balance the table instead
# of leaving one huge stretch column), each with a minimum so the table stays
# usable on narrow windows. Long names elide instead of breaking layout.
_BASE_W = {0: 56, 1: 66, 2: 210, 3: 110, 4: 70, 5: 92, 6: 62, 7: 108}
_MIN_W  = {0: 48, 1: 56, 2: 130, 3: 86, 4: 54, 5: 72, 6: 54, 7: 94}
_DEVICE_ID = "1"


class AnalogMonitorTab(QWidget):
    def __init__(self, main_window, roles, audit, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.roles = roles
        self.audit = audit
        self._last_row_h = 0

        # Persisted per-channel ENABLE state (the Analog Signals tab has no
        # enable mechanism, so this tab adds one that survives restarts).
        self._load_enables()
        # Re-apply per-channel two-point calibration saved in earlier sessions
        # (analog_monitor.txt §685-1373: "previously saved calibration values
        # must be loaded and applied to the appropriate sensors").
        self._load_calibrations()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(6)

        # -- compact page header (no dashboard cards) -------------------
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("ANALOG MONITOR")
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Georgia'; font-size:15px; "
            "font-weight:900; letter-spacing:2px; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        self.status_chip = QLabel("")
        self.status_chip.setAlignment(Qt.AlignCenter)
        self.status_chip.setStyleSheet(self._chip_style(_SOFT, "NORMAL"))
        head.addWidget(self.status_chip)
        root.addLayout(head)

        # -- the single full-view table --------------------------------
        self.table = QTableWidget(NUM_ANALOG, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        # NON-scrolling by design: the page fits the window.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideRight)
        # sizePolicy allows the page layout to expand/fill without forcing the
        # window scroll area to grow (keeps the whole page scroll-free).
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setMinimumSize(0, 0)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        hh.setHighlightSections(False)
        hh.setSectionsMovable(False)
        hh.setSectionsClickable(False)
        for col in range(len(_HEADERS)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
        self.table.setColumnHidden(len(_HEADERS) - 1, False)

        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(30)

        self._build_rows()
        self._apply_table_style()
        self._reflow(self.table.width())   # immediate layout, not just on resize
        root.addWidget(self.table, stretch=1)

        self.refresh()

    # ------------------------------------------------------------------
    # Responsive layout: no scrollbars — columns and rows reflow to fill
    # the page on every resize (analog_monitor.txt §15/§16).
    # ------------------------------------------------------------------
    def _reflow(self, total_w):
        """Distribute `total_w` among the 8 columns proportionally to their
        base weights (never below each column's minimum)."""
        if not total_w or total_w < 10:
            return
        scale = float(total_w) / float(sum(_BASE_W.values()))
        for col in range(len(_HEADERS)):
            width = max(_MIN_W[col], int(round(_BASE_W[col] * scale)))
            self.table.setColumnWidth(col, width)

    def _apply_row_heights(self, avail=None):
        """Size the rows to fill the available vertical space (24..48 px),
        so the table always covers the page and never needs a scrollbar."""
        if avail is None:
            avail = max(0, self.table.height()
                        - self.table.horizontalHeader().height() - 4)
        per = max(24, min(48, avail // NUM_ANALOG))
        if per != self._last_row_h:
            self._last_row_h = per
            self.table.verticalHeader().setDefaultSectionSize(per)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load_enables(self):
        try:
            saved = load_settings().get("analog_enabled")
        except Exception:
            saved = None
        if isinstance(saved, list):
            for i, cfg in enumerate(SENSOR_CONFIG):
                if i < len(saved):
                    cfg["enabled"] = bool(saved[i])

    def _persist_enables(self):
        save_settings({
            "analog_enabled": [bool(c.get("enabled", True)) for c in SENSOR_CONFIG]})

    # -- per-channel two-point calibration persistence (analog_monitor.txt
    #    §685-1373 §15/§20: independent per channel, survives restarts) -------
    def _load_calibrations(self):
        try:
            saved = load_settings().get("analog_calibration")
        except Exception:
            saved = None
        if not isinstance(saved, list) or len(saved) != NUM_ANALOG:
            return
        for i, entry in enumerate(saved):
            if not isinstance(entry, dict):
                continue
            try:
                lo_in = float(entry["lo_in"])
                lo_val = float(entry["lo_val"])
                hi_in = float(entry["hi_in"])
                hi_val = float(entry["hi_val"])
            except (KeyError, TypeError, ValueError):
                continue
            c0, c5 = project_two_point(lo_in, lo_val, hi_in, hi_val)
            if c0 is None:
                continue
            cfg = SENSOR_CONFIG[i]
            cfg["cal_min"] = c0
            cfg["cal_max"] = c5
            cfg["cal_in_lo"] = lo_in
            cfg["cal_val_lo"] = lo_val
            cfg["cal_in_hi"] = hi_in
            cfg["cal_val_hi"] = hi_val

    def _persist_calibration(self):
        cal = []
        for c in SENSOR_CONFIG:
            lo_in = c.get("cal_in_lo")
            hi_in = c.get("cal_in_hi")
            if lo_in is None or hi_in is None:
                cal.append(None)
            else:
                cal.append({
                    "lo_in": float(lo_in), "lo_val": float(c["cal_val_lo"]),
                    "hi_in": float(hi_in), "hi_val": float(c["cal_val_hi"]),
                })
        save_settings({"analog_calibration": cal})

    # ------------------------------------------------------------------
    # Table construction (built once; refresh() only rewrites cells)
    # ------------------------------------------------------------------
    def _build_rows(self):
        self._check_widgets = [None] * NUM_ANALOG
        self._cal_btns = [None] * NUM_ANALOG
        for i in range(NUM_ANALOG):
            def _mk_item(text, align, color=None, bold=False):
                it = QTableWidgetItem(str(text))
                it.setTextAlignment(align)
                if color is not None:
                    it.setForeground(color)
                if bold:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                return it

            self.table.setItem(i, 0, _mk_item(_DEVICE_ID, Qt.AlignCenter, TEXT_MID))
            self.table.setItem(i, 1, _mk_item(i, Qt.AlignCenter, TEXT_MID))

            name = _mk_item("", Qt.AlignLeft | Qt.AlignVCenter, TEXT_DARK)
            self.table.setItem(i, 2, name)

            self.table.setItem(i, 3, _mk_item("--", Qt.AlignRight | Qt.AlignVCenter,
                                              TEXT_DARK, bold=True))
            self.table.setItem(i, 4, _mk_item("", Qt.AlignCenter, TEXT_MID))
            self.table.setItem(i, 5, _mk_item("--", Qt.AlignRight | Qt.AlignVCenter,
                                              TEXT_MID))

            # Enable checkbox (operator view-only; engineer may toggle).
            box = QCheckBox()
            box.setChecked(bool(SENSOR_CONFIG[i].get("enabled", True)))
            box.setCursor(Qt.PointingHandCursor)
            box.stateChanged.connect(lambda _s, idx=i, cb=box: self._on_enable(idx, cb))
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignCenter)
            lay.addWidget(box)
            self.table.setCellWidget(i, 6, wrap)
            self._check_widgets[i] = box

            # Calibration button -> existing calibration interface.
            btn = QPushButton("Calibrate")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._btn_style())
            btn.clicked.connect(lambda _c, idx=i: self._open_calibration(idx))
            wrap2 = QWidget()
            lay2 = QHBoxLayout(wrap2)
            lay2.setContentsMargins(4, 0, 4, 0)
            lay2.setAlignment(Qt.AlignCenter)
            lay2.addWidget(btn)
            self.table.setCellWidget(i, 7, wrap2)
            self._cal_btns[i] = btn

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _apply_table_style(self):
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {BG_CARD.name()};
                border: 1px solid {BORDER.name()};
                border-radius: 8px;
                gridline-color: {BORDER.name()};
                font-family: 'Segoe UI'; font-size: 12px;
            }}
            QTableWidget::item {{ padding: 2px 6px; }}
            QTableWidget::item:alternate {{
                background: #F7F0E8;
            }}
            QHeaderView::section {{
                background: {ACCENT.name()};
                color: #FFFFFF;
                border: none;
                border-right: 1px solid #A03020;
                padding: 7px 6px;
                font-family: 'Segoe UI'; font-size: 11px;
                font-weight: bold; letter-spacing: 0.6px;
            }}
        """)

    def _btn_style(self):
        return (f"QPushButton {{ background:{CAL_MAX_CLR.name()}; color:white; "
                "border:none; border-radius:5px; font-family:'Segoe UI'; "
                "font-size:11px; font-weight:bold; padding:5px 12px; }} "
                "QPushButton:hover { background:#155e30; } "
                f"QPushButton:disabled {{ background:#D9CFC3; color:{TEXT_LITE.name()}; }}")

    def _chip_style(self, color, text):
        return (f"color:#FFFFFF; background:{color.name()}; font-family:'Segoe UI'; "
                "font-size:10px; font-weight:bold; border-radius:8px; "
                "padding:4px 14px;")

    def apply_theme(self, name):
        # The table uses the fixed industrial palette (like the analog gauges);
        # re-apply so a Light/Dark toggle cannot leave stale styles.
        self._apply_table_style()

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------
    def _can_edit(self):
        return self.roles.is_engineer()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _link_text(self):
        """Overall communication/system indicator for the header line."""
        mw = self.mw
        if mw._demo_mode:
            return "SIMULATION", _OK
        ser_ok = bool(mw.serial and mw.serial.is_open)
        if not ser_ok:
            return "DISCONNECTED", _BAD
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", _BAD
        if getattr(mw, "_data_stale", False):
            return "STALE DATA", _WARN
        return "NORMAL", _OK

    def _channel_state(self, i):
        """(label, color, show_numbers) for channel i — never a misleading 0."""
        cfg = SENSOR_CONFIG[i]
        if not cfg.get("enabled", True):
            return "DISABLED", _SOFT, False
        mw = self.mw
        if mw._demo_mode:
            return "NORMAL", _OK, True
        ser_ok = bool(mw.serial and mw.serial.is_open)
        if not ser_ok:
            return "NO DATA", _BAD, False
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", _BAD, False
        if getattr(mw, "_data_stale", False):
            return "STALE DATA", _WARN, False
        # Per-sensor fault/diagnostic status reported by the firmware in the
        # compact "sensorStatus" field (protocol 4.2). Only overrides the
        # link-level NORMAL verdict; live numbers stay visible under a fault.
        per = getattr(mw, "analog_statuses", None)
        if per and i < len(per):
            st = str(per[i]).upper()
            if st == "UNUSED":
                # Firmware does not sample a disabled channel (reports a fixed
                # 0.000 V); show the row like a disabled one, never a live 0 V.
                return st, _SOFT, False
            if st and st.lower() != "normal":
                color = _WARN if st in ("LOW", "CALIBRATION",
                                        "STALE DATA", "NO DATA") else _BAD
                return st, color, True
        return "NORMAL", _OK, True

    # ------------------------------------------------------------------
    # Per-tick refresh (only rewrites live cells, never rebuilds the table)
    # ------------------------------------------------------------------
    def refresh(self):
        mw = self.mw
        editable = self._can_edit()

        label, color = self._link_text()
        self.status_chip.setText("● " + label)
        self.status_chip.setStyleSheet(self._chip_style(color, label))

        for i in range(NUM_ANALOG):
            cfg = SENSOR_CONFIG[i]
            state, scolor, show = self._channel_state(i)

            # Sensor Name (may change through calibration).
            name_item = self.table.item(i, 2)
            name_item.setText(cfg["name"])
            name_item.setToolTip(cfg["name"])

            # Unit — never the voltage unit.
            unit_item = self.table.item(i, 4)
            unit_item.setText(cfg["unit"])

            # Value + Volt.
            val_item = self.table.item(i, 3)
            volt_item = self.table.item(i, 5)
            if show:
                v = float(mw.analog_voltages[i] or 0.0)
                cal = float(cfg["cal_min"]) + (v / 5.0) * (
                    float(cfg["cal_max"]) - float(cfg["cal_min"]))
                # A per-sensor diagnostic (protocol 4.2) is shown as a tag
                # in front of the engineering value so a fault can never hide
                # behind otherwise-normal numbers (no status column exists).
                if state != "NORMAL":
                    val_item.setText(f"{state} {cal:.2f}")
                    val_item.setForeground(scolor)
                    volt_item.setText(f"{v:.3f} V")
                    volt_item.setForeground(scolor)
                else:
                    val_item.setText(f"{cal:.2f}")
                    volt_item.setText(f"{v:.3f} V")
                    val_item.setForeground(TEXT_DARK)
                    volt_item.setForeground(TEXT_MID)
            else:
                if state in ("DISABLED", "UNUSED"):
                    # The channel was switched off: show the state, never a 0
                    # (voltage_read.txt: a disabled channel is not sampled).
                    val_item.setText("DISABLED")
                    volt_item.setText("DISABLED")
                else:
                    val_item.setText("--")
                    volt_item.setText("--")
                val_item.setForeground(scolor)
                volt_item.setForeground(_SOFT)

            # Enable control (engineer can toggle, operator view-only).
            self._check_widgets[i].setEnabled(editable)
            # Calibration: every row keeps a working button. Operator opens a
            # READ-ONLY view of the sensor's calibration (spec §685-1373 §16);
            # Engineer gets the full capture/edit/save workflow.
            self._cal_btns[i].setEnabled(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the page scroll-free: reflow columns to the available width and
        # grow/shrink rows to fill the height instead of overflowing.
        try:
            self._reflow(self.table.width())
            self._apply_row_heights()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Engineer actions (enable + calibration)
    # ------------------------------------------------------------------
    def _on_enable(self, i, box):
        if not self._can_edit():
            box.blockSignals(True)
            box.setChecked(bool(SENSOR_CONFIG[i].get("enabled", True)))
            box.blockSignals(False)
            return
        enabled = box.isChecked()
        SENSOR_CONFIG[i]["enabled"] = enabled
        self._persist_enables()
        try:
            self.audit.record(
                "ANALOG_ENABLE",
                f"Channel {i} ({SENSOR_CONFIG[i]['name']}) "
                f"{'enabled' if enabled else 'disabled'}",
                self.roles.role())
        except Exception:
            pass
        if i > 0 and not self.mw._demo_mode:
            # Mirror the local enable state into the firmware so a disabled
            # channel is truly UNUSED (never sampled), matching the local
            # display rule. Channel 0 (hookload) stays fixed in the firmware.
            cmd = getattr(self.mw, "_cmd", None)
            ser = getattr(self.mw, "serial", None)
            if cmd is not None and ser and ser.is_open:
                cmd.sensor_set_param(
                    i, "enabled", 1.0 if enabled else 0.0,
                    on_done=lambda _req, _data=None: None,
                    on_fail=lambda _req, reason=None, n=i:
                        self._enable_cmd_failed(n, reason))
        self.mw._return_role_to_operator()
        self.refresh()

    def _enable_cmd_failed(self, i, reason):
        try:
            self.audit.record(
                "ANALOG_ENABLE",
                f"Channel {i} enable not applied on device: {reason}",
                self.roles.role())
        except Exception:
            pass

    def _open_calibration(self, i):
        # One reusable calibration window for EVERY channel; it reads its live
        # signal from the existing stream and writes back through the SINGLE
        # existing calibration callback (gauges/graph/history stay in sync).
        # Operator mode opens it read-only (view status/info only).
        dlg = SensorCalibrationDialog(
            i, _DEVICE_ID,
            lambda: self.mw.analog_voltages[i],
            lambda: self._dialog_status(i),
            lambda idx, cmin, cmax: self._apply_sensor_calibration(idx, cmin, cmax),
            self.roles, self.audit, self)
        if self.window():
            pw = self.window().frameGeometry()
            dlg.move(pw.center() - dlg.rect().center())
        dlg.exec_()

    def _dialog_status(self, i):
        state, scolor, _show = self._channel_state(i)
        return state, scolor

    def _apply_sensor_calibration(self, idx, cal_min, cal_max):
        cfg = SENSOR_CONFIG[idx]
        lo_val = cfg.get("cal_val_lo", cal_min)
        hi_val = cfg.get("cal_val_hi", cal_max)
        band_lo = band_hi = None
        span = float(cal_max) - float(cal_min)
        if abs(span) > 1e-9:
            band_lo = max(0.0, min(1.0, (float(lo_val) - float(cal_min)) / span))
            band_hi = max(0.0, min(1.0, (float(hi_val) - float(cal_min)) / span))
        self._on_calibrated(idx, cfg["name"], cal_min, cal_max, band_lo, band_hi)

    def _on_calibrated(self, idx, name, cal_min, cal_max, band_lo, band_hi):
        # SINGLE existing calibration path (also used by the Analog Signals tab
        # via VoltageCalibrationDialog): persists, syncs gauges/graph labels,
        # audits and returns the system to Operator mode.
        self._persist_calibration()
        call = getattr(self.mw.analog_tab, "_on_calibrated", None)
        if call is not None:
            call(idx, name, cal_min, cal_max, band_lo, band_hi)
        try:
            self.audit.record(
                "ANALOG_CALIBRATE",
                f"Channel {idx} ({name}) recalibrated",
                self.roles.role())
        except Exception:
            pass
        self.mw._return_role_to_operator()
        self.refresh()