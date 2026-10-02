import os, sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox
from PyQt5.QtCore import qInstallMessageHandler

app = QApplication(sys.argv)
app.setStyle("Fusion")

from scarlet_test_panel.theme import BG_MAIN, TEXT_DARK, BG_CARD, BG_INNER
from PyQt5.QtGui import QPalette
pal = QPalette()
pal.setColor(QPalette.Window, BG_MAIN)
pal.setColor(QPalette.WindowText, TEXT_DARK)
pal.setColor(QPalette.Base, BG_CARD)
pal.setColor(QPalette.AlternateBase, BG_INNER)
pal.setColor(QPalette.Text, TEXT_DARK)
pal.setColor(QPalette.Button, BG_INNER)
pal.setColor(QPalette.ButtonText, TEXT_DARK)
app.setPalette(pal)

from scarlet_test_panel.tabs.block_position_tab import BlockPositionTab

hl = BlockPositionTab.__new__(BlockPositionTab)
names = ["_btn_style"]

for n in names:
    fn = getattr(hl, n)
    qss = fn()
    msgs = []

    def h(mode, ctx, msg):
        msgs.append(msg)

    qInstallMessageHandler(h)
    w = QPushButton()
    w.setStyleSheet(qss)
    app.processEvents()
    qInstallMessageHandler(None)
    bad = sum(1 for m in msgs if "Could not parse" in m)
    print(f"{n}: parse_fail={bad}")
    if bad:
        print(f"    OUTPUT: {qss!r}")