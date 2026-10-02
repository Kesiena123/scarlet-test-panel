from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDoubleSpinBox, QGroupBox, QLineEdit, QMessageBox, QFrame
from PyQt5.QtCore import Qt, QTimer
from ..config import BG_MAIN, BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_LITE, TEXT_MID, ACCENT, CAL_MIN_CLR, CAL_MAX_CLR, SENSOR_CONFIG, TWO_POINT_CAL, sensor_color, MS_PER_SAMPLE
from ..widgets.gauge import GaugeWidget

class VoltageCalibrationDialog(QDialog):
    _IDLE = 0
    _GOT_MIN = 1

    def __init__(self, sensor_idx, get_voltage_fn, on_submit, parent=None):
        super().__init__(parent)
        self._idx = sensor_idx
        self._get_voltage = get_voltage_fn
        self._submit_callback = on_submit
        self._state = self._IDLE
        self._v_min = None
        self._v_max = None

        tp = TWO_POINT_CAL[sensor_idx]
        if tp["v_min"] is not None:
            self._v_min = tp["v_min"]
            self._state = self._GOT_MIN
        if tp["v_max"] is not None:
            self._v_max = tp["v_max"]

        cfg = SENSOR_CONFIG[sensor_idx]
        self.setWindowTitle(f"Calibrate — {cfg['name']}")
        self.setModal(True)
        self.setMinimumSize(850, 750)
        self.setStyleSheet(f"background:{BG_CARD.name()};")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(24, 20, 24, 16)

        # Sensor name
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        name_tag = QLabel("Sensor Name:")
        name_tag.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:11px; background:transparent;")
        self.name_edit = QLineEdit(cfg["name"])
        self.name_edit.setFixedWidth(180)
        self.name_edit.setStyleSheet(f"""
            QLineEdit {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()};
                border:1px solid {ACCENT.name()}; border-radius:4px;
                font-family:'Georgia'; font-size:14px; font-weight:bold; padding:3px 8px; }}
            QLineEdit:focus {{ border:1px solid {ACCENT.name()}; }}
        """)
        hdr_row.addStretch()
        hdr_row.addWidget(name_tag)
        hdr_row.addWidget(self.name_edit)
        hdr_row.addStretch()
        root.addLayout(hdr_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        root.addWidget(sep)

        # Gauges
        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(20)
        accent = sensor_color(sensor_idx)

        volt_wrap = QVBoxLayout()
        self.volt_gauge = GaugeWidget("V", 0.0, 5.0, 6, accent)
        self.volt_gauge.setMinimumSize(280, 280)
        volt_lbl = QLabel("VOLTAGE")
        volt_lbl.setAlignment(Qt.AlignCenter)
        volt_lbl.setStyleSheet(f"color:{accent.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; background:transparent;")
        volt_wrap.addWidget(self.volt_gauge)
        volt_wrap.addWidget(volt_lbl)

        val_wrap = QVBoxLayout()
        self.val_gauge = GaugeWidget(cfg["unit"], cfg["cal_min"], cfg["cal_max"], 6, accent)
        self.val_gauge.setMinimumSize(280, 280)
        val_lbl = QLabel(cfg["name"].upper())
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(f"color:{accent.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; background:transparent;")
        val_wrap.addWidget(self.val_gauge)
        val_wrap.addWidget(val_lbl)

        gauge_row.addStretch()
        gauge_row.addLayout(volt_wrap)
        gauge_row.addLayout(val_wrap)
        gauge_row.addStretch()
        root.addLayout(gauge_row)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        root.addWidget(sep2)

        S_SPIN = f"""
            QDoubleSpinBox {{
                background:{BG_CARD.name()}; color:{TEXT_DARK.name()};
                border:1px solid {ACCENT.name()}; border-radius:4px;
                font-family:'Georgia'; font-size:12px; padding:4px;
                min-height: 30px;
            }}
        """

        # Min and Max cards
        points_row = QHBoxLayout()
        points_row.setSpacing(20)
        points_row.addStretch()

        min_card = QGroupBox()
        min_card.setStyleSheet(f"QGroupBox {{ background:{BG_CARD.name()}; border:2px solid {CAL_MIN_CLR.name()}; border-radius:8px; padding:10px; }}")
        min_lay = QVBoxLayout(min_card)
        min_lay.setSpacing(6)

        min_title = QLabel("MIN POINT")
        min_title.setAlignment(Qt.AlignCenter)
        min_title.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; letter-spacing:2px; border:none;")

        self.lbl_v_min = QLabel("—")
        self.lbl_v_min.setAlignment(Qt.AlignCenter)
        self.lbl_v_min.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:32px; font-weight:900; border:none;")
        self.lbl_v_min_sub = QLabel("not captured")
        self.lbl_v_min_sub.setAlignment(Qt.AlignCenter)
        self.lbl_v_min_sub.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; font-style:italic; border:none;")

        min_volt_row = QHBoxLayout()
        min_volt_row.setSpacing(4)
        min_volt_lbl = QLabel("or type V:")
        min_volt_lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:10px; border:none;")
        self.spin_min_volt = QDoubleSpinBox()
        self.spin_min_volt.setRange(0.0, 5.0)
        self.spin_min_volt.setDecimals(3)
        self.spin_min_volt.setSingleStep(0.001)
        self.spin_min_volt.setFixedWidth(90)
        self.spin_min_volt.setStyleSheet(S_SPIN)
        self.spin_min_volt.setToolTip("Manually enter the voltage for the min point")
        min_set_btn = QPushButton("Set")
        min_set_btn.setFixedWidth(42)
        min_set_btn.setStyleSheet(f"QPushButton {{ background:{CAL_MIN_CLR.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:10px; font-weight:bold; padding:3px 6px; }} QPushButton:hover {{ background:#154a78; }}")
        min_set_btn.clicked.connect(self._set_min_manual)
        min_volt_row.addWidget(min_volt_lbl)
        min_volt_row.addWidget(self.spin_min_volt)
        min_volt_row.addWidget(min_set_btn)

        self.spin_min_val = QDoubleSpinBox()
        self.spin_min_val.setRange(-1e6, 1e6)
        self.spin_min_val.setDecimals(2)
        self.spin_min_val.setValue(0.0)
        self.spin_min_val.setFixedWidth(110)
        self.spin_min_val.setEnabled(False)
        self.spin_min_val.setStyleSheet(S_SPIN)
        spin_min_lbl = QLabel("Desired value:")
        spin_min_lbl.setAlignment(Qt.AlignCenter)
        spin_min_lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:10px; border:none;")

        min_lay.addWidget(min_title)
        min_lay.addWidget(self.lbl_v_min)
        min_lay.addWidget(self.lbl_v_min_sub)
        min_lay.addLayout(min_volt_row)
        min_lay.addWidget(spin_min_lbl)
        min_lay.addWidget(self.spin_min_val, alignment=Qt.AlignCenter)

        max_card = QGroupBox()
        max_card.setStyleSheet(f"QGroupBox {{ background:{BG_CARD.name()}; border:2px solid {CAL_MAX_CLR.name()}; border-radius:8px; padding:10px; }}")
        max_lay = QVBoxLayout(max_card)
        max_lay.setSpacing(6)

        max_title = QLabel("MAX POINT")
        max_title.setAlignment(Qt.AlignCenter)
        max_title.setStyleSheet(f"color:{CAL_MAX_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; letter-spacing:2px; border:none;")

        self.lbl_v_max = QLabel("—")
        self.lbl_v_max.setAlignment(Qt.AlignCenter)
        self.lbl_v_max.setStyleSheet(f"color:{CAL_MAX_CLR.name()}; font-family:'Georgia'; font-size:32px; font-weight:900; border:none;")
        self.lbl_v_max_sub = QLabel("not captured")
        self.lbl_v_max_sub.setAlignment(Qt.AlignCenter)
        self.lbl_v_max_sub.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; font-style:italic; border:none;")

        max_volt_row = QHBoxLayout()
        max_volt_row.setSpacing(4)
        max_volt_lbl = QLabel("or type V:")
        max_volt_lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:10px; border:none;")
        self.spin_max_volt = QDoubleSpinBox()
        self.spin_max_volt.setRange(0.0, 5.0)
        self.spin_max_volt.setDecimals(3)
        self.spin_max_volt.setSingleStep(0.001)
        self.spin_max_volt.setFixedWidth(90)
        self.spin_max_volt.setStyleSheet(S_SPIN)
        self.spin_max_volt.setToolTip("Manually enter the voltage for the max point")
        max_set_btn = QPushButton("Set")
        max_set_btn.setFixedWidth(42)
        max_set_btn.setStyleSheet(f"QPushButton {{ background:{CAL_MAX_CLR.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:10px; font-weight:bold; padding:3px 6px; }} QPushButton:hover {{ background:#155e30; }}")
        max_set_btn.clicked.connect(self._set_max_manual)
        max_volt_row.addWidget(max_volt_lbl)
        max_volt_row.addWidget(self.spin_max_volt)
        max_volt_row.addWidget(max_set_btn)

        self.spin_max_val = QDoubleSpinBox()
        self.spin_max_val.setRange(-1e6, 1e6)
        self.spin_max_val.setDecimals(2)
        self.spin_max_val.setValue(100.0)
        self.spin_max_val.setFixedWidth(110)
        self.spin_max_val.setEnabled(False)
        self.spin_max_val.setStyleSheet(S_SPIN)
        spin_max_lbl = QLabel("Desired value:")
        spin_max_lbl.setAlignment(Qt.AlignCenter)
        spin_max_lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:10px; border:none;")

        max_lay.addWidget(max_title)
        max_lay.addWidget(self.lbl_v_max)
        max_lay.addWidget(self.lbl_v_max_sub)
        max_lay.addLayout(max_volt_row)
        max_lay.addWidget(spin_max_lbl)
        max_lay.addWidget(self.spin_max_val, alignment=Qt.AlignCenter)

        points_row.addWidget(min_card)
        points_row.addWidget(max_card)
        points_row.addStretch()
        root.addLayout(points_row)

        # Status
        self.status_lbl = QLabel("Step 1 — Press 'Get Min Value' or type a voltage to set your minimum point.")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(f"color:#7A5C10; font-family:'Georgia'; font-size:10px; font-style:italic; background:transparent;")
        root.addWidget(self.status_lbl)

        # Buttons
        S_PRI = f"QPushButton {{ background:{ACCENT.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:12px; font-weight:bold; padding:7px 20px; }} QPushButton:hover {{ background:#a03020; }} QPushButton:disabled {{ background:{BORDER.name()}; color:{TEXT_LITE.name()}; }}"
        S_SEC = f"QPushButton {{ background:{CAL_MAX_CLR.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:12px; font-weight:bold; padding:7px 20px; }} QPushButton:hover {{ background:#155e30; }} QPushButton:disabled {{ background:{BORDER.name()}; color:{TEXT_LITE.name()}; }}"
        S_SUB = f"QPushButton {{ background:#2C6E49; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:12px; font-weight:bold; padding:7px 22px; }} QPushButton:hover {{ background:#1e5035; }} QPushButton:disabled {{ background:{BORDER.name()}; color:{TEXT_LITE.name()}; }}"
        S_CAN = f"QPushButton {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; border:1px solid {BORDER.name()}; border-radius:4px; font-family:'Georgia'; font-size:12px; padding:7px 18px; }} QPushButton:hover {{ background:{BORDER.name()}; }}"
        self._s_pri = S_PRI
        self._s_sec = S_SEC

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        self.action_btn = QPushButton("Get Min Value")
        self.action_btn.setStyleSheet(S_PRI)
        self.action_btn.clicked.connect(self._on_action)
        self.submit_btn = QPushButton("Submit")
        self.submit_btn.setStyleSheet(S_SUB)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_submit_clicked)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setStyleSheet(S_CAN)
        self.reset_btn.clicked.connect(self._reset)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(S_CAN)
        cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(self.action_btn)
        btn_row.addWidget(self.submit_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addSpacing(20)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._live_update)
        self._timer.start(MS_PER_SAMPLE)

        # Restore previous calibration
        if tp["v_min"] is not None:
            self.lbl_v_min.setText(f"{tp['v_min']:.3f} V")
            self.lbl_v_min_sub.setText("from last calibration")
            self.lbl_v_min_sub.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; border:none;")
            self.spin_min_val.setValue(tp["val_min"])
            self.spin_min_val.setEnabled(True)
            self.spin_min_volt.setValue(tp["v_min"])

        if tp["v_max"] is not None:
            self.lbl_v_max.setText(f"{tp['v_max']:.3f} V")
            self.lbl_v_max_sub.setText("from last calibration")
            self.lbl_v_max_sub.setStyleSheet(f"color:{CAL_MAX_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; border:none;")
            self.spin_max_val.setValue(tp["val_max"])
            self.spin_max_val.setEnabled(True)
            self.spin_max_volt.setValue(tp["v_max"])

        if tp["v_min"] is not None and tp["v_max"] is not None:
            lo = min(tp["v_min"], tp["v_max"]) / 5.0
            hi = max(tp["v_min"], tp["v_max"]) / 5.0
            self.volt_gauge.setCalBand(lo, hi)
            self.submit_btn.setEnabled(True)
            self.action_btn.setText("Get Max Value")
            self.action_btn.setStyleSheet(S_SEC)
            self.action_btn.setEnabled(False)
            self.status_lbl.setText("Previous calibration loaded — edit values or Reset to re-capture, then Submit.")
            self.status_lbl.setStyleSheet(f"color:#1E7A3E; font-family:'Georgia'; font-size:10px; font-style:italic;")
        elif tp["v_min"] is not None:
            self.volt_gauge.setCalBand(tp["v_min"] / 5.0, tp["v_min"] / 5.0)
            self.action_btn.setText("Get Max Value")
            self.action_btn.setStyleSheet(S_SEC)
            self.status_lbl.setText("Previous min loaded — capture or type max voltage.")
            self.status_lbl.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")

    # ---- helpers ----
    def _apply_min(self, v):
        self._v_min = v
        self.lbl_v_min.setText(f"{v:.3f} V")
        self.lbl_v_min_sub.setText("captured ✓")
        self.lbl_v_min_sub.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; border:none;")
        self.spin_min_val.setEnabled(True)
        if self._v_max is not None:
            self._state = self._GOT_MIN
            self.action_btn.setEnabled(False)
            self.submit_btn.setEnabled(True)
            lo = min(self._v_min, self._v_max) / 5.0
            hi = max(self._v_min, self._v_max) / 5.0
            self.volt_gauge.setCalBand(lo, hi)
            self.status_lbl.setText(f"Min set ({v:.3f} V) — enter its value and press Submit.")
            self.status_lbl.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
        else:
            self._state = self._GOT_MIN
            self.action_btn.setText("Get Max Value")
            self.action_btn.setStyleSheet(self._s_sec)
            self.volt_gauge.setCalBand(v / 5.0, v / 5.0)
            self.status_lbl.setText(f"Min set ({v:.3f} V) — enter its value, then capture max.")
            self.status_lbl.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
        self.spin_min_val.setFocus()

    def _apply_max(self, v):
        if self._v_min is None:
            self.status_lbl.setText("Set the minimum point before setting the maximum.")
            self.status_lbl.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
            return
        self._v_max = v
        self.lbl_v_max.setText(f"{v:.3f} V")
        self.lbl_v_max_sub.setText("captured ✓")
        self.lbl_v_max_sub.setStyleSheet(f"color:{CAL_MAX_CLR.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; border:none;")
        self.spin_max_val.setEnabled(True)
        self.submit_btn.setEnabled(True)
        self.action_btn.setEnabled(False)
        lo = min(self._v_min, self._v_max) / 5.0
        hi = max(self._v_min, self._v_max) / 5.0
        self.volt_gauge.setCalBand(lo, hi)
        self.status_lbl.setText(f"Max set ({v:.3f} V) — enter its value and press Submit.")
        self.status_lbl.setStyleSheet(f"color:{CAL_MAX_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
        self.spin_max_val.setFocus()

    def _set_min_manual(self):
        self._apply_min(round(self.spin_min_volt.value(), 3))

    def _set_max_manual(self):
        self._apply_max(round(self.spin_max_volt.value(), 3))

    def _live_update(self):
        v = self._get_voltage()
        cfg = SENSOR_CONFIG[self._idx]
        cal = cfg["cal_min"] + (v / 5.0) * (cfg["cal_max"] - cfg["cal_min"])
        self.volt_gauge.setValue(v)
        self.val_gauge.setValue(cal)
        self.val_gauge.min_val = float(cfg["cal_min"])
        self.val_gauge.max_val = float(cfg["cal_max"])

    def _on_action(self):
        v = self._get_voltage()
        if self._state == self._IDLE:
            self._apply_min(v)
        elif self._state == self._GOT_MIN:
            self._apply_max(v)

    def _reset(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Reset Calibration")
        msg.setText("Which calibration point do you want to reset?")
        msg.setIcon(QMessageBox.Question)
        msg.setWindowFlags(msg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msg.setStyleSheet(f"""
            QMessageBox {{ background:{BG_MAIN.name()}; font-family:'Georgia'; font-size:12px; }}
            QLabel {{ color:{TEXT_DARK.name()}; font-family:'Georgia'; font-size:12px; }}
            QPushButton {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()};
                border:1px solid {BORDER.name()}; border-radius:4px;
                font-family:'Georgia'; font-size:11px; padding:5px 14px; min-width:80px; }}
            QPushButton:hover {{ background:{BORDER.name()}; }}
        """)
        btn_low = msg.addButton("Low Point", QMessageBox.ActionRole)
        btn_high = msg.addButton("High Point", QMessageBox.ActionRole)
        btn_all = msg.addButton("Reset All", QMessageBox.DestructiveRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.RejectRole)
        btn_all.setStyleSheet(f"background:{ACCENT.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:11px; padding:5px 14px; font-weight:bold;")
        msg.exec_()
        clicked = msg.clickedButton()
        if clicked == btn_cancel:
            return

        if clicked in (btn_low, btn_all):
            self._v_min = None
            self.lbl_v_min.setText("—")
            self.lbl_v_min_sub.setText("not captured")
            self.lbl_v_min_sub.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; font-style:italic; border:none;")
            self.spin_min_val.setValue(0.0)
            self.spin_min_val.setEnabled(False)

        if clicked in (btn_high, btn_all):
            self._v_max = None
            self.lbl_v_max.setText("—")
            self.lbl_v_max_sub.setText("not captured")
            self.lbl_v_max_sub.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; font-style:italic; border:none;")
            self.spin_max_val.setValue(100.0)
            self.spin_max_val.setEnabled(False)

        if self._v_min is None and self._v_max is None:
            self._state = self._IDLE
            self.action_btn.setEnabled(True)
            self.action_btn.setText("Get Min Value")
            self.action_btn.setStyleSheet(self._s_pri)
            self.submit_btn.setEnabled(False)
            self.volt_gauge.clearCalBand()
            self.status_lbl.setText("Step 1 — Press 'Get Min Value' or type a voltage to set your minimum point.")
            self.status_lbl.setStyleSheet(f"color:#7A5C10; font-family:'Georgia'; font-size:10px; font-style:italic;")
        elif self._v_min is not None and self._v_max is None:
            self._state = self._GOT_MIN
            self.action_btn.setEnabled(True)
            self.action_btn.setText("Get Max Value")
            self.action_btn.setStyleSheet(self._s_sec)
            self.submit_btn.setEnabled(False)
            self.volt_gauge.setCalBand(self._v_min / 5.0, self._v_min / 5.0)
            self.status_lbl.setText("High point cleared — capture or type a new max voltage.")
            self.status_lbl.setStyleSheet(f"color:{CAL_MIN_CLR.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
        elif self._v_min is None and self._v_max is not None:
            self._state = self._IDLE
            self.action_btn.setEnabled(True)
            self.action_btn.setText("Get Min Value")
            self.action_btn.setStyleSheet(self._s_pri)
            self.submit_btn.setEnabled(False)
            self.volt_gauge.setCalBand(self._v_max / 5.0, self._v_max / 5.0)
            self.status_lbl.setText("Low point cleared — capture or type a new min voltage.")
            self.status_lbl.setStyleSheet(f"color:#7A5C10; font-family:'Georgia'; font-size:10px; font-style:italic;")

    def _on_submit_clicked(self):
        if self._v_min is None or self._v_max is None:
            return
        val_at_min = self.spin_min_val.value()
        val_at_max = self.spin_max_val.value()
        v_span = self._v_max - self._v_min
        if abs(v_span) < 1e-6:
            self.status_lbl.setText("Error: min and max voltages are identical — re-capture at different readings.")
            self.status_lbl.setStyleSheet(f"color:{ACCENT.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
            return
        if val_at_max <= val_at_min:
            self.status_lbl.setText("Error: max value must be greater than min value.")
            self.status_lbl.setStyleSheet(f"color:{ACCENT.name()}; font-family:'Georgia'; font-size:10px; font-style:italic;")
            return
        slope = (val_at_max - val_at_min) / v_span
        cal_at_0v = val_at_min + slope * (0.0 - self._v_min)
        cal_at_5v = val_at_min + slope * (5.0 - self._v_min)

        new_name = self.name_edit.text().strip() or SENSOR_CONFIG[self._idx]["name"]
        SENSOR_CONFIG[self._idx]["name"] = new_name
        SENSOR_CONFIG[self._idx]["cal_min"] = cal_at_0v
        SENSOR_CONFIG[self._idx]["cal_max"] = cal_at_5v
        TWO_POINT_CAL[self._idx].update({
            "v_min": self._v_min,
            "val_min": val_at_min,
            "v_max": self._v_max,
            "val_max": val_at_max
        })

        band_lo = band_hi = None
        cal_span = cal_at_5v - cal_at_0v
        if abs(cal_span) > 1e-9:
            band_lo = max(0.0, min(1.0, (val_at_min - cal_at_0v) / cal_span))
            band_hi = max(0.0, min(1.0, (val_at_max - cal_at_0v) / cal_span))

        self._timer.stop()
        self._submit_callback(self._idx, new_name, cal_at_0v, cal_at_5v, band_lo, band_hi)
        self.accept()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)