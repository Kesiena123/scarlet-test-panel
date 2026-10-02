"""Digital pulse sensor calibration window for SPM 1-4 and RPM (digitalsensor1.txt).

The SPM and RPM pages deliberately share ONE implementation: they differ only in
their measurement terminology (Strokes Per Minute vs Revolutions Per Minute),
exactly as the spec requires. Every visual element is reused from the existing
Analog Sensor Calibration page — the flat header, the card styling, the input
fields, the validation/status line, the Save/Cancel controls and the Engineer
permission model are imported from ``sensor_calibration_dialog`` so the two
calibration systems cannot drift apart visually (§2).

What this window deliberately does NOT do:
  * it does not open a serial connection or a sensor loop — it reads the live
    signal from the EXISTING dashboard stream (mw.digital_measured), the same
    stream the Dashboard itself shows (§16, §21);
  * it does not keep its own calibration copy — the saved curve is written
    through ``digital_calibration`` and the main window RELOADS and RE-APPLIES
    it, so the UI can never show a curve the engine is not using (§12);
  * it does not touch any other channel. Saving SPM 2 rewrites index 1 only
    (§5/§6).
"""
from PyQt5.QtWidgets import (
    QDialog, QApplication, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QWidget,
    QPushButton, QDoubleSpinBox, QGroupBox, QFrame, QComboBox, QMessageBox,
    QHeaderView, QAbstractItemView, QTableWidget, QTableWidgetItem, QCheckBox,
)
from PyQt5.QtCore import Qt, QTimer

from ..config import (
    BG_CARD, BORDER, ACCENT, TEXT_DARK, TEXT_MID, TEXT_LITE, TEXT_ON_DARK,
    CAL_MAX_CLR, MS_PER_SAMPLE,
)
from ..services import digital_calibration as digcal

# Reuse the Analog Sensor Calibration page's exact styling (§2).
from .sensor_calibration_dialog import _S

_MIN_W = 0.0
_MAX_W = 1_000_000.0


class DigitalSensorCalibrationDialog(QDialog):
    """Modal calibration page for ONE digital channel.

    kind: "SPM" | "RPM"     idx: channel index inside that measurement
    """

    def __init__(self, kind, idx, main_window, roles, audit, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.idx = idx
        self.mw = main_window
        self.roles = roles
        self.audit = audit
        # §19: Engineer edits/tests/saves; Operator gets a read-only view.
        self._editable = bool(roles and roles.is_engineer())
        self._dirty = False
        self._saved = False
        self.ask_fn = None                    # test hook for confirmation boxes
        self._lock_inputs = not self._editable

        self._record = digcal.record_for(kind, idx) or digcal.blank_record(kind, idx)

        title = ("RPM CALIBRATION" if kind == "RPM"
                 else f"{digcal.name_for(kind, idx).upper()} CALIBRATION")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(1060, 760)
        self.setStyleSheet(f"background:{BG_CARD.name()};")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._drag_off = None
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                self.resize(min(g.width() - 120, 1180), min(g.height() - 120, 860))
                self.move(g.left() + max((g.width() - self.width()) // 2, 40),
                          g.top() + max((g.height() - self.height()) // 2, 40))
            else:
                self.resize(1140, 840)
        except Exception:
            self.resize(1140, 840)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(20, 16, 20, 14)

        # ---- header (same shape as the Analog page) ------------------------
        head = QHBoxLayout()
        head_lbl = QLabel(title)
        head_lbl.setStyleSheet(_S["section_title"] + "font-size:15px;")
        head.addWidget(head_lbl)
        head.addStretch()
        if not self._editable:
            view = QLabel("VIEW ONLY")
            view.setStyleSheet(
                f"color:{TEXT_ON_DARK.name()}; background:{ACCENT.name()}; "
                "border-radius:8px; font-family:'Segoe UI'; font-size:10px; "
                "font-weight:bold; padding:4px 12px;")
            head.addWidget(view)
        self.btn_close = QPushButton("\u2715")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setToolTip("Close")
        self.btn_close.setStyleSheet(
            f"QPushButton {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:4px; "
            "font-family:'Segoe UI'; font-size:13px; font-weight:bold; } "
            f"QPushButton:hover {{ background:{ACCENT.name()}; color:white; border:none; }}")
        self.btn_close.clicked.connect(self.close)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        self.lbl_ident = QLabel("")
        self.lbl_ident.setTextFormat(Qt.RichText)
        self.lbl_ident.setStyleSheet(_S["key"])
        root.addWidget(self.lbl_ident)
        root.addWidget(self._rule())

        # ---- SENSOR INFORMATION + CONFIGURATION (§20) ---------------------
        info = QGroupBox("SENSOR INFORMATION")
        info.setStyleSheet(_S["card"])
        il = QGridLayout(info)
        il.setHorizontalSpacing(8)
        il.setVerticalSpacing(4)
        il.addWidget(self._k("Sensor:"), 0, 0)
        il.addWidget(self._v(digcal.name_for(kind, idx)), 0, 1)
        il.addWidget(self._k("Sensor Type:"), 0, 2)
        il.addWidget(self._v("Digital Pulse"), 0, 3)
        il.addWidget(self._k("Measurement:"), 1, 0)
        il.addWidget(self._v(digcal.measurement(kind)), 1, 1)
        il.addWidget(self._k("Unit:"), 1, 2)
        il.addWidget(self._v(digcal.unit(kind)), 1, 3)
        root.addWidget(info)

        cfg_card = QGroupBox("SENSOR CONFIGURATION")
        cfg_card.setStyleSheet(_S["card"])
        cl = QGridLayout(cfg_card)
        cl.setHorizontalSpacing(8)
        cl.setVerticalSpacing(4)
        cl.addWidget(self._k("Input Channel:"), 0, 0)
        # The firmware counter this channel reports on. For RPM both existing
        # RPM firmware channels live on ONE calibration page (§6), so the
        # active channel is chosen here; SPM always opens on its own channel.
        self.combo_input = None
        if kind == "RPM":
            self.combo_input = QComboBox()
            self.combo_input.setStyleSheet(f"""
                QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()};
                    border:1px solid {ACCENT.name()}; border-radius:4px;
                    font-family:'Segoe UI'; font-size:12px; padding:3px;
                    min-height:26px; }}
                QComboBox QAbstractItemView {{ background:{BG_CARD.name()};
                    color:{TEXT_DARK.name()}; }}
                QComboBox:disabled {{ border:1px solid {BORDER.name()};
                    color:{TEXT_LITE.name()}; }}
            """)
            for i in range(digcal.count("RPM")):
                # Label each firmware channel explicitly (RPM 1 / RPM 2) so the
                # two independent calibrations are never confused.
                self.combo_input.addItem(
                    f"{digcal.channel_label('RPM', i)}  "
                    f"({digcal.counter_key('RPM', i)})", i)
            self.combo_input.setCurrentIndex(min(self.idx, self.combo_input.count() - 1))
            if not self._editable:
                self.combo_input.setEnabled(False)
            self.combo_input.currentIndexChanged.connect(self._on_channel_changed)
            cl.addWidget(self.combo_input, 0, 1)
        else:
            self.lbl_input_ch = self._v(str(digcal.counter_key(kind, idx)))
            self.lbl_input_ch.setToolTip(
                "Firmware counter reported by the device for this sensor. "
                "It comes from the existing hardware configuration and is not "
                "editable here.")
            cl.addWidget(self.lbl_input_ch, 0, 1)
        if kind == "RPM":
            cl.addWidget(self._k("Pulses / Revolution:"), 1, 0)
            self.spin_ppr = self._mk_spin(
                float(self._record.get("pulses_per_rev", 1.0) or 1.0),
                0.001, 10000.0, 3, 0.5)
            self.spin_ppr.setToolTip(
                "Pulses the sensor produces per revolution. Revolutions are "
                "derived by dividing the pulse count by this value. Leave at "
                "1.000 unless the sensor is known to emit multiple pulses per "
                "revolution.")
            cl.addWidget(self.spin_ppr, 1, 1)
            self.spin_ppr.valueChanged.connect(self._on_edited)
            cl.addWidget(self._k("pulses ÷ this = revolutions"),
                         1, 2, 1, 2)
        else:
            cl.addWidget(self._k("Pulse Configuration:"), 1, 0)
            cl.addWidget(self._v("1 pulse = 1 stroke"), 1, 1)
            cl.addWidget(self._k("SPM is strokes, never revolutions"),
                         1, 2, 1, 2)
            self.spin_ppr = None
        root.addWidget(cfg_card)

        # ---- CALIBRATION POINTS table (§8/§20) ----------------------------
        pts_card = QGroupBox("CALIBRATION POINTS")
        pts_card.setStyleSheet(_S["card"])
        pv = QVBoxLayout(pts_card)
        pv.setSpacing(4)
        headers = ["Use", "Point", "Pulse Rate", "Reference Value", "Unit"]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background:{BG_CARD.name()};
                border:1px solid {BORDER.name()}; border-radius:4px;
                gridline-color:{BORDER.name()};
                font-family:'Segoe UI'; font-size:12px; }}
            QTableWidget::item:alternate {{ background:#F7F0E8; }}
            QHeaderView::section {{ background:{ACCENT.name()}; color:#FFFFFF;
                border:none; border-right:1px solid #A03020; padding:5px 6px;
                font-family:'Segoe UI'; font-size:11px; font-weight:bold; }}
        """)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 44)
        self.table.setColumnWidth(1, 58)
        self.table.setColumnWidth(4, 74)
        pv.addWidget(self.table)

        pts_btn = QHBoxLayout()
        pts_btn.setSpacing(8)
        pts_btn.addWidget(self._k(
            "Enter the reference pulse rate the equipment is actually "
            "producing and the value a reference instrument reads."))
        pts_btn.addStretch(1)
        self.btn_add_point = QPushButton("Add Point")
        self.btn_add_point.setStyleSheet(_S["btn_flat"])
        self.btn_add_point.clicked.connect(self._add_point)
        pts_btn.addWidget(self.btn_add_point)
        self.btn_del_point = QPushButton("Remove Point")
        self.btn_del_point.setStyleSheet(_S["btn_flat"])
        self.btn_del_point.clicked.connect(self._del_point)
        pts_btn.addWidget(self.btn_del_point)
        pv.addLayout(pts_btn)
        root.addWidget(pts_card)

        self._load_record_points()

        # ---- LIVE CALIBRATION TEST (§10/§11) ------------------------------
        live_card = QGroupBox("LIVE CALIBRATION TEST")
        live_card.setStyleSheet(_S["card"])
        lv = QGridLayout(live_card)
        lv.setHorizontalSpacing(8)
        lv.setVerticalSpacing(4)
        self.lbl_raw_hz = self._v("\u2014")
        self.lbl_measured = self._v("\u2014")
        self.lbl_calculated = self._v("\u2014")
        self.lbl_calibrated = self._v("\u2014")
        self.lbl_signal = self._v("\u2014")
        lv.addWidget(self._k("Raw Pulse Rate:"), 0, 0)
        lv.addWidget(self.lbl_raw_hz, 0, 1)
        lv.addWidget(self._k(f"Measured ({digcal.unit(kind)}):"), 0, 2)
        lv.addWidget(self.lbl_measured, 0, 3)
        lv.addWidget(self._k("Calculated Value:"), 1, 0)
        lv.addWidget(self.lbl_calculated, 1, 1)
        lv.addWidget(self._k("Calibrated Value:"), 1, 2)
        lv.addWidget(self.lbl_calibrated, 1, 3)
        lv.addWidget(self._k("Signal Status:"), 2, 0)
        lv.addWidget(self.lbl_signal, 2, 1)
        root.addWidget(live_card)

        # ---- STATUS / VALIDATION line (§13) -------------------------------
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(_S["value"])
        root.addWidget(self.lbl_status)

        # ---- CONTROLS (§3/§6/§20) ----------------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_test = QPushButton("TEST")
        self.btn_test.setStyleSheet(_S["btn_pri"])
        self.btn_test.clicked.connect(self._test)
        self.btn_save = QPushButton("SAVE CALIBRATION")
        self.btn_save.setStyleSheet(_S["btn_sec"])
        self.btn_save.clicked.connect(self._save)
        self.btn_reset = QPushButton("RESET CALIBRATION")
        self.btn_reset.setStyleSheet(_S["btn_danger"])
        self.btn_reset.clicked.connect(self._reset)
        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setStyleSheet(_S["btn_flat"])
        self.btn_cancel.clicked.connect(self.reject)
        for b in (self.btn_test, self.btn_save, self.btn_reset, self.btn_cancel):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # ---- RBAC: Operator is read-only; TEST/CANCEL stay usable ----------
        if not self._editable:
            if self.spin_ppr is not None:
                self.spin_ppr.setReadOnly(True)
                self.spin_ppr.setEnabled(False)
            self.btn_add_point.setEnabled(False)
            self.btn_del_point.setEnabled(False)
            for b in (self.btn_save, self.btn_reset):
                b.setEnabled(False)
        root.addWidget(self._k(
            "Reads the existing dashboard stream — no new serial connection, "
            "no second calculation. TEST previews the curve; SAVE CALIBRATION "
            "applies it to the live calculation."))

        self._update_validation()
        self._live_update()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._live_update)
        self._timer.start(MS_PER_SAMPLE)

    # ------------------------------------------------------------------ util
    def _rule(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        return sep

    def _k(self, text, color=None):
        lab = QLabel(text)
        lab.setStyleSheet(_S["key"] if color is None else
                          f"color:{color.name()}; font-family:'Segoe UI'; font-size:11px;")
        lab.setWordWrap(True)
        return lab

    def _v(self, text):
        lab = QLabel(str(text))
        lab.setStyleSheet(_S["value"])
        return lab

    def _mk_spin(self, value, lo, hi, dec, step):
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(dec)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        spin.setStyleSheet(_S["spin"])
        return spin

    # ------------------------------------------------------------------ points
    def _on_channel_changed(self, *_):
        """RPM holds both firmware channels on one page: load the newly selected
        channel's OWN record (points + pulses/revolution). Unsaved edits to the
        previous channel are never written to the new one - the row state is
        DISCARDED, not carried over."""
        if self.combo_input is None:
            return
        new_idx = self.combo_input.currentData()
        if new_idx is None or new_idx == self.idx:
            return
        self.idx = int(new_idx)
        self._record = (digcal.record_for(self.kind, self.idx)
                        or digcal.blank_record(self.kind, self.idx))
        self._dirty = False
        self._load_record_points()
        self._update_validation()
        self._live_update()

    def _initial_rows(self):
        pts = self._record.get("points") or []
        return max(digcal.DEFAULT_POINTS, len(pts))

    def _load_record_points(self):
        """Replace the table with the SAVED record of the current channel."""
        self._row_widgets = None
        self._row_used = []
        self._row_values = []
        pts = [(float(p[0]), float(p[1]))
               for p in (self._record.get("points") or [])]
        self._saved_points = pts
        self._rebuild_points(max(digcal.DEFAULT_POINTS, len(pts)))

    def _rebuild_points(self, rows, reset=False):
        """Rebuild the point table, keeping whatever the Engineer already typed.

        A row is a calibration point only when its USE box is ticked, so the empty
        placeholders shown by default are never mistaken for calibration points
        and a legitimate (0, 0) anchor can be entered simply by ticking USE."""
        if not getattr(self, "_row_widgets", None):
            saved = getattr(self, "_saved_points", None)
            if saved is None:
                saved = [(float(p[0]), float(p[1]))
                         for p in (self._record.get("points") or [])]
            self._row_used = [True] * len(saved)
            self._row_values = list(saved)
        if reset:
            self._row_used = []
            self._row_values = []
        used = list(getattr(self, "_row_used", []))
        values = list(getattr(self, "_row_values", []))
        rows = max(digcal.MIN_POINTS, min(int(rows), digcal.MAX_POINTS))
        self.table.setRowCount(rows)
        self._row_widgets = []
        self._row_used = []
        self._row_values = []
        for r in range(rows):
            pair = values[r] if r < len(values) else None
            is_used = bool(used[r]) if r < len(used) else False
            self._row_used.append(is_used)
            self._row_values.append(pair)
            # column 0: USE
            check = QCheckBox()
            check.setChecked(is_used)
            check.setEnabled(self._editable)
            check.setToolTip(
                "Tick to use this row as a calibration point. Unticked rows "
                "are ignored.")
            check.toggled.connect(lambda _c, r=r: self._on_row_used(r))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignCenter)
            hl.addWidget(check)
            self.table.setCellWidget(r, 0, holder)
            # column 1: point number
            lab = QTableWidgetItem(str(r + 1))
            lab.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, lab)
            # columns 2/3: pulse rate + reference value
            ref, act = pair if pair is not None else (0.0, 0.0)
            spin_ref = self._mk_spin(ref, _MIN_W, _MAX_W, 3, 1.0)
            spin_act = self._mk_spin(act, _MIN_W, _MAX_W, 3, 1.0)
            for col, spin in ((2, spin_ref), (3, spin_act)):
                if not self._editable:
                    spin.setReadOnly(True)
                    spin.setEnabled(False)
                spin.valueChanged.connect(
                    lambda _v, r=r: self._on_row_edited(r))
                self.table.setCellWidget(r, col, spin)
            unit_lab = QTableWidgetItem(digcal.unit(self.kind))
            unit_lab.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 4, unit_lab)
            self._row_widgets.append((spin_ref, spin_act))
        if self.kind == "RPM" and self.spin_ppr is not None:
            self.spin_ppr.blockSignals(True)
            self.spin_ppr.setValue(float(self._record.get("pulses_per_rev", 1.0) or 1.0))
            self.spin_ppr.blockSignals(False)

    def _typed_points(self):
        """Only the rows the Engineer ticked USE for, in table order."""
        out = []
        for r in range(len(self._row_widgets)):
            if r >= len(self._row_used) or not self._row_used[r]:
                continue
            spin_ref, spin_act = self._row_widgets[r]
            out.append((spin_ref.value(), spin_act.value()))
        return out

    def _on_row_used(self, r):
        """USE ticked/unticked. Ticking a row takes whatever is on screen, so a
        (0, 0) anchor is a valid first point."""
        if 0 <= r < len(self._row_used):
            self._row_used[r] = self.table.cellWidget(r, 0).findChild(
                QCheckBox).isChecked()
            if self._row_used[r] and 0 <= r < len(self._row_widgets):
                spin_ref, spin_act = self._row_widgets[r]
                self._row_values[r] = (spin_ref.value(), spin_act.value())
        self._on_edited()

    def _on_row_edited(self, r):
        if 0 <= r < len(self._row_widgets) and 0 <= r < len(self._row_values):
            spin_ref, spin_act = self._row_widgets[r]
            self._row_values[r] = (spin_ref.value(), spin_act.value())
        self._on_edited()

    def _add_point(self):
        if self.table.rowCount() >= digcal.MAX_POINTS:
            self._note(f"At most {digcal.MAX_POINTS} calibration points are "
                       "supported.", ACCENT)
            return
        self._rebuild_points(self.table.rowCount() + 1)

    def _del_point(self):
        if self.table.rowCount() <= digcal.MIN_POINTS:
            self._note(f"At least {digcal.MIN_POINTS} calibration points are "
                       "required.", ACCENT)
            return
        self._rebuild_points(self.table.rowCount() - 1)

    def _on_edited(self, *_):
        self._dirty = True
        self._update_validation()

    def _snapshot(self):
        """The record described by what is currently on screen."""
        ppr = 1.0
        if self.kind == "RPM" and self.spin_ppr is not None:
            ppr = float(self.spin_ppr.value())
        elif self.kind == "RPM":
            ppr = float(self._record.get("pulses_per_rev", 1.0) or 1.0)
        return {
            "channel": digcal.channel_number(self.kind, self.idx),
            "pulses_per_rev": ppr,
            "points": [list(p) for p in self._typed_points()],
            "saved_at": self._record.get("saved_at", ""),
        }

    # ------------------------------------------------------------------ live
    def _measured_safe(self):
        try:
            return self.mw.digital_measured(self.kind, self.idx)
        except Exception:
            return None

    def _live_update(self):
        measured = self._measured_safe()
        staged = self._snapshot()
        hz = digcal.pulse_rate_hz(self.kind, staged, measured)
        calc = digcal.calculated_value(self.kind, staged, measured)
        active = digcal.calibrated_value(self.kind, staged, measured)

        if measured is None:
            self.lbl_raw_hz.setText("\u2014")
            self.lbl_measured.setText("\u2014")
            self.lbl_calculated.setText("\u2014")
            self.lbl_calibrated.setText("\u2014")
        else:
            self.lbl_raw_hz.setText(f"{hz:.1f} Hz")
            self.lbl_measured.setText(f"{measured:.1f}")
            self.lbl_calculated.setText(f"{calc:.1f}")
            self.lbl_calibrated.setText(
                f"{active:.1f} {digcal.unit(self.kind)}"
                if active is not None else "\u2014")

        state, color = self._signal_status(measured)
        self.lbl_signal.setText(state)
        self.lbl_signal.setStyleSheet(
            f"color:{color.name()}; font-family:'Segoe UI'; font-size:12px; "
            "font-weight:bold; background:transparent;")

        status = digcal.cal_status(self.kind, self.idx, staged)
        saved_flag = "UNSAVED CHANGES" if self._dirty else (
            "SAVED" if digcal.is_calibrated(self.kind, self.idx) else "NOT SAVED")
        self.lbl_ident.setText(
            f"Device: <b>1</b>    Channel: <b>{digcal.channel_number(self.kind, self.idx)}</b>"
            f"    Sensor: <b>{digcal.name_for(self.kind, self.idx)}</b>"
            f"    Unit: <b>{digcal.unit(self.kind)}</b>"
            f"    Calibration: <b>{status}</b>    ({saved_flag})")

    def _signal_status(self, measured):
        """§15 status vocabulary, taken from the EXISTING link/fault layer."""
        mw = self.mw
        if mw._demo_mode:
            return ("ACTIVE", CAL_MAX_CLR) if measured is not None else (
                "NO SIGNAL", ACCENT)
        if not (mw.serial and mw.serial.is_open):
            return "NO SIGNAL", ACCENT
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", ACCENT
        if getattr(mw, "_data_stale", False):
            return "NO SIGNAL", ACCENT
        if measured is None:
            return "NO SIGNAL", ACCENT
        if digcal.cal_status(self.kind, self.idx) != digcal.CAL_VALID:
            return "CALIBRATION REQUIRED", ACCENT
        return "ACTIVE", CAL_MAX_CLR

    # ------------------------------------------------------------------ validation
    def _update_validation(self):
        staged = self._snapshot()
        ok, reason = digcal.validate(self.kind, staged)
        if ok:
            n = len([p for p in staged["points"]])
            txt = (f"✓ {digcal.name_for(self.kind, self.idx)} calibration ready — "
                   f"{n} point(s) entered.")
            self.lbl_status.setText(txt)
            self.lbl_status.setStyleSheet(_S["value"] + f"color:{CAL_MAX_CLR.name()};")
        else:
            self._note(reason, ACCENT)

    def _note(self, text, color):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            f"color:{color.name()}; font-family:'Segoe UI'; font-size:12px; "
            "font-weight:bold; background:transparent;")

    def _test(self):
        """Preview the staged curve against the live signal without saving."""
        staged = self._snapshot()
        ok, reason = digcal.validate(self.kind, staged)
        if not ok:
            self._note("TEST not run: " + reason, ACCENT)
            return False
        measured = self._measured_safe()
        if measured is None:
            self._note("TEST not run: no digital signal is being received "
                       "from this channel.", ACCENT)
            return False
        calc = digcal.calculated_value(self.kind, staged, measured)
        active = digcal.calibrated_value(self.kind, staged, measured)
        self._note(
            f"TEST OK — raw {digcal.pulse_rate_hz(self.kind, staged, measured):.1f} Hz, "
            f"calculated {calc:.1f}, calibrated {active:.1f} "
            f"{digcal.unit(self.kind)} (preview, not saved).", CAL_MAX_CLR)
        return True

    # ------------------------------------------------------------------ save
    def _save(self):
        """§12: validate → save → reload → apply → verify → success message."""
        if not self._editable:
            return False
        staged = self._snapshot()
        ok, reason = digcal.validate(self.kind, staged)
        if not ok:
            self._note(reason, ACCENT)
            return False
        staged["saved_at"] = digcal.stamp()
        stored = digcal.save_channel(self.kind, self.idx, staged)
        if stored is None:
            self._note("Save failed — the calibration could not be stored.",
                       ACCENT)
            return False
        # RELOAD + APPLY: the main window re-reads the stored curve and applies
        # it to the live calculation, so the engine can never keep using the old
        # calibration while the UI shows the new one.
        try:
            self.mw.reload_digital_calibration()
        except Exception:
            pass
        self._record = stored
        self._dirty = False
        # VERIFY: the live calibrated value must come from the stored curve.
        measured = self._measured_safe()
        applied = None
        try:
            applied = self.mw.digital_value(self.kind, self.idx)
        except Exception:
            applied = None
        if measured is not None and applied is not None:
            expect = digcal.calibrated_value(self.kind, stored, measured)
            if expect is None or abs(applied - expect) > 1e-6:
                self._note("Save verification failed — the live value does not "
                           "match the saved calibration.", ACCENT)
                return False
        if self.audit is not None:
            try:
                self.audit.record(
                    "DIGITAL_CALIBRATION",
                    f"{digcal.name_for(self.kind, self.idx)} calibration saved "
                    f"({len(stored['points'])} points)",
                    self.roles.role() if self.roles else "-")
            except Exception:
                pass
        self._note(f"✓ {digcal.name_for(self.kind, self.idx)} calibration "
                   "saved successfully.", CAL_MAX_CLR)
        self._saved = True
        try:
            self.mw._return_role_to_operator()
        except Exception:
            pass
        self.stop_live()
        self.accept()
        return True

    def _reset(self):
        """Clear only THIS channel's calibration, after confirmation."""
        if not self._editable:
            return
        if not self._ask(
                "Reset Calibration",
                f"Remove the saved calibration for "
                f"{digcal.name_for(self.kind, self.idx)}?\n"
                "Only this sensor is affected — every other SPM / RPM "
                "channel keeps its own calibration."):
            return
        digcal.save_channel(self.kind, self.idx, None)
        try:
            self.mw.reload_digital_calibration()
        except Exception:
            pass
        self._record = digcal.blank_record(self.kind, self.idx)
        self._dirty = False
        if self.audit is not None:
            try:
                self.audit.record(
                    "DIGITAL_CALIBRATION",
                    f"{digcal.name_for(self.kind, self.idx)} calibration reset",
                    self.roles.role() if self.roles else "-")
            except Exception:
                pass
        self._note(f"{digcal.name_for(self.kind, self.idx)} calibration cleared "
                   "— values return to the device measurement.", CAL_MAX_CLR)
        self._rebuild_points(digcal.DEFAULT_POINTS, reset=True)
        self._live_update()

    # ------------------------------------------------------------------ window
    def _ask(self, title, text):
        if callable(self.ask_fn):
            return bool(self.ask_fn(title, text))
        return QMessageBox.question(
            self, title, text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def stop_live(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 64:
            self._drag_off = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        self._drag_off = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_off)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_off = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        # Closing with unsaved edits asks first; a committed calibration is
        # never silently discarded.
        if self._dirty and not self._saved:
            if not self._ask("Unsaved Calibration",
                             "This calibration has unsaved changes.\n"
                             "Discard them and close?"):
                event.ignore()
                return
        self.stop_live()
        super().closeEvent(event)