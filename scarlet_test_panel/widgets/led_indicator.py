from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt
from ..config import BORDER, TEXT_MID

class LedIndicator(QWidget):
    def __init__(self, label, on_color, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        lay.setContentsMargins(4, 4, 4, 4)
        self.dot = QLabel()
        self.dot.setFixedSize(24, 24)
        self._on_color = on_color
        self._set(False)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:10px; font-weight:bold; letter-spacing:1px; background:transparent;")
        lay.addWidget(self.dot, alignment=Qt.AlignCenter)
        lay.addWidget(lbl)

    def _set(self, on):
        c = self._on_color.name() if on else "#D8D0C0"
        self.dot.setStyleSheet(f"background:{c}; border:2px solid {BORDER.name()}; border-radius:12px;")

    def setOn(self, on):
        self._set(on)