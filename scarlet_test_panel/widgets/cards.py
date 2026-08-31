import datetime
from PyQt5.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QCheckBox, QComboBox, QPushButton, QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt

from ..config import (
    ACCENT, BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_LITE, TEXT_MID,
    SENSOR_CONFIG, TWO_POINT_CAL, sensor_color, SESSION_START, RPM_CONFIG
)
from .. import theme
from .gauge import GaugeWidget
from .graph_plot import _GraphPlot


class GraphCard(QGroupBox):
    def __init__(self, histories, configs, epoch_samples, parent=None):
        super().__init__("Signal Graph  (1-hour window)", parent)
        self.histories = histories
        self.configs = configs
        self.epoch_samples = epoch_samples

        self.setStyleSheet(f"""
            QGroupBox {{ color:{ACCENT.name()}; font-family:'Georgia'; font-size:12px; font-weight:bold;
                border:1.5px solid {BORDER.name()}; border-radius:10px; margin-top:10px;
                padding-top:6px; background:{BG_CARD.name()}; }}
            QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; color:{ACCENT.name()}; }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 20, 10, 10)
        outer.setSpacing(8)

        cc = QWidget()
        cc.setStyleSheet("background: transparent;")
        cv = QVBoxLayout(cc)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        lbl_s = QLabel("SENSOR:")
        lbl_s.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:13px; font-weight:900; letter-spacing:2px; background:transparent;")
        self.sensor_cb = QComboBox()
        for cfg in configs:
            self.sensor_cb.addItem(cfg["name"])
        self.sensor_cb.setFixedWidth(150)
        self.sensor_cb.setFixedHeight(34)
        self.sensor_cb.setStyleSheet(f"""
            QComboBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; border:2px solid {BORDER.name()};
                border-radius:6px; font-family:'Georgia'; font-size:13px; font-weight:900; padding:4px 8px; }}
            QComboBox QAbstractItemView {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()};
                font-family:'Georgia'; font-size:13px; font-weight:bold; }}
        """)
        self.sensor_cb.currentIndexChanged.connect(self._on_sensor_changed)

        self.volt_toggle = QCheckBox("Show Voltage")
        self.volt_toggle.setChecked(True)
        self.volt_toggle.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:11px; background:transparent;")
        self.volt_toggle.toggled.connect(self._on_volt_toggle)

        top_row.addWidget(lbl_s)
        top_row.addWidget(self.sensor_cb)
        top_row.addSpacing(16)
        top_row.addWidget(self.volt_toggle)
        top_row.addStretch()

        self.time_label = QLabel("--:-- --")
        self.time_label.setStyleSheet(f"color:#1A5C94; font-family:'Georgia'; font-size:13px; font-weight:bold; background:transparent;")
        self.epoch_label = QLabel("0:00")
        self.epoch_label.setStyleSheet(f"color:#7A5C10; font-family:'Georgia'; font-size:13px; font-weight:bold; background:transparent;")
        epoch_lbl_tag = QLabel("elapsed:")
        epoch_lbl_tag.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; background:transparent;")
        time_lbl_tag = QLabel("time:")
        time_lbl_tag.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:10px; background:transparent;")

        top_row.addWidget(time_lbl_tag)
        top_row.addWidget(self.time_label)
        top_row.addSpacing(12)
        top_row.addWidget(epoch_lbl_tag)
        top_row.addWidget(self.epoch_label)

        cv.addLayout(top_row)

        rr = QHBoxLayout()
        rr.setSpacing(20)
        rr.addStretch()

        def pill(tag, attr, color_str=None):
            p = QWidget()
            p.setStyleSheet(f"background:{BG_INNER.name()}; border:2px solid {BORDER.name()}; border-radius:10px;")
            l = QVBoxLayout(p)
            l.setContentsMargins(18, 8, 18, 8)
            l.setSpacing(0)
            t = QLabel(tag)
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:9px; font-weight:bold; letter-spacing:2px; border:none; background:transparent;")
            v = QLabel("--")
            v.setAlignment(Qt.AlignCenter)
            clr = color_str or TEXT_DARK.name()
            v.setStyleSheet(f"color:{clr}; font-family:'Georgia'; font-size:28px; font-weight:900; border:none; background:transparent;")
            l.addWidget(t)
            l.addWidget(v)
            setattr(self, attr, v)
            return p

        self.volt_pill = pill("VOLTAGE", "voltage_label")
        val_pill = pill("CURRENT VALUE", "value_label", ACCENT.name())
        rr.addWidget(self.volt_pill)
        rr.addWidget(val_pill)
        rr.addStretch()
        cv.addLayout(rr)

        outer.addWidget(cc)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        outer.addWidget(sep)
        self.plot = _GraphPlot(histories, configs, epoch_samples)
        outer.addWidget(self.plot, stretch=1)
        self.update_display()

    def _on_sensor_changed(self, idx):
        self.plot.set_sensor(idx)
        self.update_display()

    def _on_volt_toggle(self, checked):
        self.volt_pill.setVisible(checked)
        self.plot._show_voltage = checked
        self.plot.update()

    def update_display(self):
        idx = self.sensor_cb.currentIndex()
        hist = self.histories[idx]
        last_volt = hist[-1][0] if hist else 0.0
        cfg = self.configs[idx]
        cal = cfg["cal_min"] + (last_volt / 5.0) * (cfg["cal_max"] - cfg["cal_min"])
        self.voltage_label.setText(f"{last_volt:.3f} V")
        span = cfg["cal_max"] - cfg["cal_min"]
        self.value_label.setText(f"{int(round(cal))}" if span >= 1000 else f"{cal:.1f}" if span >= 10 else f"{cal:.2f}")
        if getattr(self, '_elapsed_frozen', None) is None:
            e = (datetime.datetime.now() - SESSION_START).total_seconds()
            self.epoch_label.setText(f"{int(e)//60}:{int(e)%60:02d}")
        self.time_label.setText(datetime.datetime.now().strftime("%I:%M:%S %p").lstrip("0"))

    def freeze_elapsed(self):
        e = (datetime.datetime.now() - SESSION_START).total_seconds()
        self._elapsed_frozen = e
        self.epoch_label.setText(f"{int(e)//60}:{int(e)%60:02d}")

    def unfreeze_elapsed(self):
        self._elapsed_frozen = None

    def refresh_sensor_names(self):
        current = self.sensor_cb.currentIndex()
        self.sensor_cb.blockSignals(True)
        for i, cfg in enumerate(SENSOR_CONFIG):
            self.sensor_cb.setItemText(i, cfg["name"])
        self.sensor_cb.blockSignals(False)
        self.sensor_cb.setCurrentIndex(current)

    def refresh(self):
        self.plot.update()
        self.update_display()


class SensorPanel(QGroupBox):
    """Analog sensor card rendered in the Block Position design language.

    Matches the Block Position card style: a Segoe UI bold ACCENT-coloured
    title on a theme-aware rounded card (1px border, radius 10), large centered
    Consolas value figures, and a colour-coded status. The gauge dials and the
    add/recalibrate/toggle controls are preserved unchanged.
    """

    def __init__(self, cfg, accent_color, calibrate_callback=None, parent=None):
        super().__init__(cfg["name"], parent)
        self.cfg = cfg
        self.accent = accent_color
        self._last_volts = 0.0
        self._active = True

        self._t = theme.current()
        self._build(cfg, calibrate_callback)
        self._apply_card_style()

    # -- shared helpers (Block Position style) -----------------------------
    def _card_style(self):
        t = self._t
        bg = t.color_name("BG_CARD")
        border = t.color_name("BORDER")
        color = self.accent.name()
        return (f"QGroupBox {{ color:{color}; font-family:'Segoe UI'; "
                f"font-size:12px; font-weight:bold; letter-spacing:0.5px; "
                f"border:1px solid {border}; border-radius:10px; margin-top:10px; "
                f"padding-top:4px; background:{bg}; }} "
                f"QGroupBox::title {{ subcontrol-origin:margin; left:12px; top:0px; "
                f"padding:2px 8px; color:{color}; background:{bg}; border-radius:5px; }}")

    def _field_style(self):
        t = self._t
        return (f"color:{t.color_name('TEXT_DARK')}; background:{t.color_name('BG_INNER')}; "
                f"font-family:'Consolas'; font-size:34px; font-weight:bold; "
                f"border:1px solid {t.color_name('BORDER')}; border-radius:8px; "
                f"padding:6px;")

    def _label_style(self):
        t = self._t
        return (f"color:{t.color_name('TEXT_MID')}; font-family:'Segoe UI'; "
                f"font-size:10px; font-weight:bold; letter-spacing:1px; "
                f"border:none; background:transparent;")

    def _apply_card_style(self):
        self.setStyleSheet(self._card_style())
        for lbl in getattr(self, "_label_widgets", []):
            lbl.setStyleSheet(self._label_style())
        for fig in getattr(self, "_figure_widgets", []):
            fig.setStyleSheet(self._field_style())

    def apply_theme(self, name):
        self._t = theme.current()
        self._apply_card_style()
        self._style_toggle(self._active)

    def _build(self, cfg, calibrate_callback):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 14, 8, 8)
        outer.setSpacing(4)
        self.setMinimumSize(340, 400)   # increased to fit bigger gauges

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.toggle_btn = QPushButton("● ACTIVE")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setFixedHeight(24)
        self._style_toggle(True)
        self.toggle_btn.clicked.connect(self._on_toggle)

        self.recal_btn = QPushButton("⚙ Recalibrate")
        self.recal_btn.setFixedHeight(24)
        self.recal_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INNER.name()}; color:{TEXT_MID.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:4px; font-family:'Segoe UI'; "
            f"font-size:10px; font-weight:bold; padding:2px 10px; }} "
            f"QPushButton:hover {{ background:{BORDER.name()}; }}")
        if calibrate_callback is not None:
            self.recal_btn.clicked.connect(calibrate_callback)

        top_row.addWidget(self.recal_btn)
        top_row.addStretch()
        top_row.addWidget(self.toggle_btn)
        outer.addLayout(top_row)

        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(4)
        self.volt_gauge = GaugeWidget("V", 0.0, 5.0, 6, self.accent)
        self.volt_gauge.setMinimumSize(220, 220)   # bigger
        self.cal_gauge = GaugeWidget(cfg["unit"], cfg["cal_min"], cfg["cal_max"],
                                     6, self.accent)
        self.cal_gauge.setMinimumSize(220, 220)    # bigger

        def _wrapped(gauge, text):
            box = QVBoxLayout()
            box.setSpacing(1)
            box.addWidget(gauge)
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color:{self.accent.name()}; font-family:'Segoe UI'; "
                f"font-size:10px; font-weight:bold; background:transparent;")
            box.addWidget(lbl)
            return box, lbl

        volt_box, _volt_lbl = _wrapped(self.volt_gauge, "VOLTAGE")
        cal_box, cal_lbl = _wrapped(self.cal_gauge, cfg["name"].upper())
        self._cal_name_lbl = cal_lbl

        gauge_row.addLayout(volt_box)
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        div.setStyleSheet(f"background:{BORDER.name()}; max-width:1px; border:none;")
        gauge_row.addWidget(div)
        gauge_row.addLayout(cal_box)
        outer.addLayout(gauge_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        outer.addWidget(sep)

        readout_row = QHBoxLayout()
        readout_row.setSpacing(6)
        volt_box2, self.volt_display = self._make_field("VOLTAGE", "V")
        cal_box2, self.cal_display = self._make_field("VALUE", cfg["unit"])
        readout_row.addWidget(volt_box2)
        readout_row.addWidget(cal_box2)
        outer.addLayout(readout_row)

    def setTitle(self, name):
        super().setTitle(name)
        if hasattr(self, "_cal_name_lbl"):
            self._cal_name_lbl.setText(name.upper())

    def _style_toggle(self, active):
        if active:
            self.toggle_btn.setText("● ACTIVE")
            self.toggle_btn.setStyleSheet(
                f"QPushButton {{ background:{self.accent.name()}; color:white; "
                f"border:none; border-radius:4px; font-family:'Segoe UI'; "
                f"font-size:10px; font-weight:bold; padding:2px 10px; }}")
        else:
            self.toggle_btn.setText("○ INACTIVE")
            self.toggle_btn.setStyleSheet(
                f"QPushButton {{ background:{BG_INNER.name()}; color:{TEXT_MID.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:4px; "
                f"font-family:'Segoe UI'; font-size:10px; font-weight:bold; "
                f"padding:2px 10px; }} QPushButton:hover {{ background:{BORDER.name()}; }}")

    def _on_toggle(self):
        self._active = self.toggle_btn.isChecked()
        self._style_toggle(self._active)
        self.volt_gauge.setActive(self._active)
        self.cal_gauge.setActive(self._active)
        if not self._active:
            self.volt_display.setText("—")
            self.cal_display.setText("—")
        else:
            self.setVoltage(self._last_volts)

    def isActive(self):
        return self._active

    def getCurrentVoltage(self):
        return self._last_volts

    def _make_field(self, label_text, unit_text):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(self._label_style())
        lbl.setAlignment(Qt.AlignCenter)
        val = QLabel("—")
        val.setStyleSheet(self._field_style())
        val.setAlignment(Qt.AlignCenter)
        unit_lbl = QLabel(unit_text)
        unit_lbl.setStyleSheet(
            f"color:{TEXT_LITE.name()}; font-family:'Segoe UI'; font-size:9px; "
            f"border:none; background:transparent;")
        unit_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        lay.addWidget(val)
        lay.addWidget(unit_lbl)
        if not hasattr(self, "_label_widgets"):
            self._label_widgets = []
            self._figure_widgets = []
        self._label_widgets.append(lbl)
        self._figure_widgets.append(val)
        return box, val

    def setVoltage(self, volts):
        volts = max(0.0, min(5.0, float(volts)))
        self._last_volts = volts
        if not self._active:
            return
        frac = volts / 5.0
        cal = self.cfg["cal_min"] + frac * (self.cfg["cal_max"] - self.cfg["cal_min"])
        self.volt_gauge.setValue(volts)
        self.cal_gauge.setValue(cal)
        self.volt_display.setText(f"{volts:.3f}")
        span = self.cfg["cal_max"] - self.cfg["cal_min"]
        self.cal_display.setText(f"{int(round(cal))}" if span >= 1000 else f"{cal:.1f}" if span >= 10 else f"{cal:.2f}")

    def updateCalibration(self, cal_min, cal_max):
        self.cfg["cal_min"] = cal_min
        self.cfg["cal_max"] = cal_max
        self.cal_gauge.min_val = float(cal_min)
        self.cal_gauge.max_val = float(cal_max)
        self.setVoltage(self._last_volts)



class SpmCard(QGroupBox):
    def __init__(self, name, color, on_reset, parent=None):
        super().__init__(name, parent)
        self.setStyleSheet(f"""
            QGroupBox {{ color:{color.name()}; font-family:'Georgia'; font-size:12px; font-weight:bold;
                border:1.5px solid {BORDER.name()}; border-radius:10px; margin-top:10px;
                padding-top:6px; background:{BG_CARD.name()}; }}
            QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; color:{color.name()}; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 14, 8, 8)
        outer.setSpacing(4)
        self.setMinimumSize(320, 380)   # bigger

        self.gauge = GaugeWidget("SPM", 0, 150, 6, color)
        self.gauge.setMinimumSize(250, 250)   # bigger
        outer.addWidget(self.gauge)

        row = QHBoxLayout()
        row.setSpacing(6)
        c_box, self.counter_val = self._field("COUNTER", "")
        s_box, self.spm_val = self._field("SPM", "strokes/min")
        row.addWidget(c_box)
        row.addWidget(s_box)
        outer.addLayout(row)

        self.reset_btn = QPushButton("Reset Counter")
        self.reset_btn.setStyleSheet(f"QPushButton {{ background:{color.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:11px; font-weight:bold; padding:6px; }} QPushButton:hover {{ background:#333; }}")
        self.reset_btn.clicked.connect(on_reset)
        outer.addWidget(self.reset_btn)

    @staticmethod
    def _field(label_text, unit_text):
        box = QWidget()
        box.setStyleSheet(f"background:{BG_INNER.name()}; border:1px solid {BORDER.name()}; border-radius:6px;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(0)
        lbl = QLabel(label_text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:9px; font-weight:bold; letter-spacing:1px; border:none; background:transparent;")
        val = QLabel("—")
        val.setAlignment(Qt.AlignCenter)
        val.setStyleSheet(f"color:{TEXT_DARK.name()}; font-family:'Georgia'; font-size:20px; font-weight:bold; border:none; background:transparent;")
        unit_lbl = QLabel(unit_text)
        unit_lbl.setAlignment(Qt.AlignCenter)
        unit_lbl.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:9px; border:none; background:transparent;")
        lay.addWidget(lbl)
        lay.addWidget(val)
        lay.addWidget(unit_lbl)
        return box, val

    def update_values(self, counter, spm):
        self.gauge.setValue(max(0.0, min(150.0, spm)))
        self.counter_val.setText(f"{int(counter)}")
        self.spm_val.setText(f"{spm:.1f}")


class RpmCard(QGroupBox):
    def __init__(self, color, on_reset, on_select, parent=None):
        super().__init__("RPM Monitor", parent)
        self.setStyleSheet(f"""
            QGroupBox {{ color:{color.name()}; font-family:'Georgia'; font-size:12px; font-weight:bold;
                border:1.5px solid {BORDER.name()}; border-radius:10px; margin-top:10px;
                padding-top:6px; background:{BG_CARD.name()}; }}
            QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; color:{color.name()}; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 14, 8, 8)
        outer.setSpacing(4)
        self.setMinimumSize(380, 420)   # bigger

        top = QHBoxLayout()
        lbl = QLabel("Source:")
        lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:11px; background:transparent;")
        self.combo = QComboBox()
        for cfg in RPM_CONFIG:
            self.combo.addItem(cfg["name"])
        self.combo.setStyleSheet(f"""
            QComboBox {{ background:{BG_INNER.name()}; color:{TEXT_DARK.name()}; border:1px solid {BORDER.name()};
                border-radius:4px; font-family:'Georgia'; font-size:11px; padding:4px 8px; }}
            QComboBox QAbstractItemView {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; }}
        """)
        self.combo.currentIndexChanged.connect(on_select)
        top.addWidget(lbl)
        top.addWidget(self.combo)
        top.addStretch()
        outer.addLayout(top)

        self.gauge = GaugeWidget("RPM", 0, 150, 6, color)
        self.gauge.setMinimumSize(270, 270)   # bigger
        outer.addWidget(self.gauge)

        row = QHBoxLayout()
        row.setSpacing(6)
        c_box, self.counter_val = SpmCard._field("COUNTER", "")
        r_box, self.rpm_val = SpmCard._field("RPM", "rev/min")
        row.addWidget(c_box)
        row.addWidget(r_box)
        outer.addLayout(row)

        self.reset_btn = QPushButton("Reset Counter")
        self.reset_btn.setStyleSheet(f"QPushButton {{ background:{color.name()}; color:white; border:none; border-radius:4px; font-family:'Georgia'; font-size:11px; font-weight:bold; padding:6px; }} QPushButton:hover {{ background:#a03020; }}")
        self.reset_btn.clicked.connect(on_reset)
        outer.addWidget(self.reset_btn)

    def selected_index(self):
        return self.combo.currentIndex()

    def update_values(self, counter, rpm):
        self.gauge.setValue(max(0.0, min(150.0, rpm)))
        self.counter_val.setText(f"{int(counter)}")
        self.rpm_val.setText(f"{rpm:.1f}")