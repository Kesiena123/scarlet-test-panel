from PyQt5.QtWidgets import QWidget, QHBoxLayout, QPushButton
from PyQt5.QtCore import Qt
from ..config import ACCENT, BG_CARD, BORDER, TEXT_MID, TEXT_DARK

class TabBar(QWidget):
    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self._current = 0
        self._callbacks = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._btns = []
        for i, name in enumerate(tabs):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(38)
            btn.setMinimumWidth(140)
            btn.setCursor(Qt.PointingHandCursor)
            idx = i
            btn.clicked.connect(lambda _, n=idx: self._select(n))
            self._btns.append(btn)
            layout.addWidget(btn)
        layout.addStretch()
        self._refresh_styles()

    def _select(self, idx):
        self._current = idx
        self._refresh_styles()
        for cb in self._callbacks:
            cb(idx)

    def _refresh_styles(self):
        for i, btn in enumerate(self._btns):
            if i == self._current:
                btn.setChecked(True)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {ACCENT.name()}; color: white; border: none;
                        border-radius: 8px; font-family: 'Segoe UI';
                        font-size: 12px; font-weight: bold; letter-spacing: 0.5px;
                        padding: 0 18px;
                    }}
                    QPushButton:hover {{ background: #a03020; }}
                """)
            else:
                btn.setChecked(False)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {BG_CARD.name()}; color: {TEXT_MID.name()};
                        border: 1px solid {BORDER.name()}; border-radius: 8px;
                        font-family: 'Segoe UI'; font-size: 12px; font-weight: 600;
                        padding: 0 18px;
                    }}
                    QPushButton:hover {{ background: {BORDER.name()}; color: {TEXT_DARK.name()}; }}
                """)

    def on_change(self, callback):
        self._callbacks.append(callback)

    def set_tab_visible(self, index, visible):
        """Show or hide a tab button without rebuilding the whole bar."""
        if 0 <= index < len(self._btns):
            self._btns[index].setVisible(visible)