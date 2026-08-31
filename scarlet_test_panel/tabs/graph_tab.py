from PyQt5.QtWidgets import QWidget, QVBoxLayout
from ..widgets.cards import GraphCard
from ..config import SENSOR_CONFIG

class GraphTab(QWidget):
    def __init__(self, histories, epoch_samples, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(0)
        self.graph_card = GraphCard(histories, SENSOR_CONFIG, epoch_samples)
        layout.addWidget(self.graph_card, stretch=1)