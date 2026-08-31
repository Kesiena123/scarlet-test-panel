from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QScrollArea, QFrame
from PyQt5.QtCore import Qt
from ..config import SPM_CONFIG, ACCENT, BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_MID, sensor_color
from ..widgets.cards import SpmCard, RpmCard

class StrokeRpmTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.mw = main_window

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(12)

        spm_title = QLabel("STROKES PER MINUTE")
        spm_title.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:12px; font-weight:900; letter-spacing:3px; background:transparent;")
        root.addWidget(spm_title)

        spm_grid = QGridLayout()
        spm_grid.setSpacing(10)
        self.spm_cards = []
        for i, cfg in enumerate(SPM_CONFIG):
            card = SpmCard(cfg["name"], sensor_color(i), on_reset=(lambda idx=i: self.mw.reset_spm(idx)))
            self.spm_cards.append(card)
            row, col = divmod(i, 2)
            spm_grid.addWidget(card, row, col)
        root.addLayout(spm_grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        root.addWidget(sep)

        rpm_title = QLabel("RPM MONITOR")
        rpm_title.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Georgia'; font-size:12px; font-weight:900; letter-spacing:3px; background:transparent;")
        root.addWidget(rpm_title)

        rpm_wrap = QHBoxLayout()
        rpm_wrap.addStretch()
        self.rpm_card = RpmCard(ACCENT,
                                 on_reset=(lambda: self.mw.reset_rpm(self.rpm_card.selected_index())),
                                 on_select=(lambda _i: self.refresh()))
        rpm_wrap.addWidget(self.rpm_card)
        rpm_wrap.addStretch()
        root.addLayout(rpm_wrap)

        root.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def refresh(self):
        for i, card in enumerate(self.spm_cards):
            counter = self.mw.spm_raw_counter[i] - self.mw.spm_offset[i]
            card.update_values(counter, self.mw.spm_values[i])
        sel = self.rpm_card.selected_index()
        counter = self.mw.rpm_raw_counter[sel] - self.mw.rpm_offset[sel]
        self.rpm_card.update_values(counter, self.mw.rpm_values[sel])