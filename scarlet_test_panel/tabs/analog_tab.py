from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QFrame, QDialog, QComboBox,
    QDialogButtonBox, QMessageBox
)
from PyQt5.QtCore import Qt

from ..config import (
    NUM_ANALOG, SENSOR_CONFIG, ACCENT, BG_MAIN, BG_INNER, BG_CARD,
    BORDER, TEXT_MID, TEXT_LITE, TEXT_DARK, sensor_color  # <-- added TEXT_DARK
)
from ..widgets.cards import SensorPanel
from ..widgets.flow_layout import FlowLayout
from ..dialogs.calibration_dialog import VoltageCalibrationDialog


class AnalogTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.panels = [None] * NUM_ANALOG
        self._count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(8)

        top_row = QHBoxLayout()
        self.add_btn = QPushButton("+  Add Analog Signal")
        self.add_btn.setFixedHeight(34)
        self.add_btn.setStyleSheet(f"QPushButton {{ background:{ACCENT.name()}; color:white; border:none; border-radius:6px; font-family:'Georgia'; font-size:12px; font-weight:bold; padding:6px 18px; }} QPushButton:hover {{ background:#a03020; }}")
        self.add_btn.clicked.connect(self._show_add_dialog)
        top_row.addWidget(self.add_btn)

        self.count_lbl = QLabel(f"0 / {NUM_ANALOG} signals added")
        self.count_lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:11px; background:transparent;")
        top_row.addSpacing(14)
        top_row.addWidget(self.count_lbl)
        top_row.addStretch()
        root.addLayout(top_row)

        self.empty_lbl = QLabel("No analog signals added yet. Click \u201cAdd Analog Signal\u201d, choose a channel "
                                 "(Sensor 1–16), then calibrate it to display its gauge here.")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setStyleSheet(f"color:{TEXT_LITE.name()}; font-family:'Georgia'; font-size:13px; font-style:italic; background:transparent; padding:50px;")
        root.addWidget(self.empty_lbl)

        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid = FlowLayout(owner=self.grid_widget)
        self.grid.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidget(self.grid_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("background: transparent; border: none;")
        root.addWidget(scroll, stretch=1)

        # voltage_read.txt: every configured analog channel is on screen from the
        # start (connected sensors show their live voltage, no-sensor channels a
        # plain 0); the "+ Add" flow is therefore fully pre-populated.
        for i in range(NUM_ANALOG):
            self._ensure_panel(i)
            self.panels[i].setVoltage(0.0)
        self.add_btn.setEnabled(False)

    def _show_add_dialog(self):
        available = [i for i in range(NUM_ANALOG) if self.panels[i] is None]
        if not available:
            QMessageBox.information(self, "Add Analog Signal",
                                     f"All {NUM_ANALOG} analog signals have already been added.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Analog Signal")
        dlg.setStyleSheet(f"background:{BG_MAIN.name()};")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lbl = QLabel("Select a sensor channel to add:")
        lbl.setStyleSheet(f"color:{TEXT_DARK.name()}; font-family:'Georgia'; font-size:12px; background:transparent;")
        combo = QComboBox()
        for i in available:
            combo.addItem(SENSOR_CONFIG[i]["name"], i)
        combo.setStyleSheet(f"""
            QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; border:1px solid {BORDER.name()};
                border-radius:4px; font-family:'Georgia'; font-size:12px; padding:6px; }}
            QComboBox QAbstractItemView {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; }}
        """)
        lay.addWidget(lbl)
        lay.addWidget(combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Continue to Calibration")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec_() == QDialog.Accepted:
            idx = combo.currentData()
            self._open_calibration(idx)

    def _open_calibration(self, idx):
        dlg = VoltageCalibrationDialog(idx, lambda: self.mw.analog_voltages[idx], self._on_calibrated, self)
        if self.window():
            pw = self.window().frameGeometry()
            dlg.move(pw.center() - dlg.rect().center())
        dlg.exec_()

    def _ensure_panel(self, idx):
        if self.panels[idx] is None:
            panel = SensorPanel(SENSOR_CONFIG[idx], sensor_color(idx),
                                calibrate_callback=lambda: self._open_calibration(idx))
            self.panels[idx] = panel
            self.grid.addWidget(panel)
            self._count += 1
            self.empty_lbl.hide()
            self.count_lbl.setText(f"{self._count} / {NUM_ANALOG} signals added")
        return self.panels[idx]

    def _on_calibrated(self, idx, name, cal_min, cal_max, band_lo, band_hi):
        panel = self._ensure_panel(idx)
        panel.setTitle(name)
        panel.updateCalibration(cal_min, cal_max)
        if band_lo is not None:
            panel.cal_gauge.setCalBand(band_lo, band_hi)
        panel.setVoltage(self.mw.analog_voltages[idx])

        if self.mw.graph_card is not None:
            self.mw.graph_card.refresh_sensor_names()

    def setVoltage(self, idx, v):
        panel = self.panels[idx]
        if panel is not None:
            panel.setVoltage(v)

    def setDisabled(self, idx, disabled):
        panel = self.panels[idx]
        if panel is not None:
            panel.setDisabled(disabled)

    def apply_theme(self, name):
        for panel in self.panels:
            if panel is not None:
                panel.apply_theme(name)