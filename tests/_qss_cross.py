import os, sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                             QSpinBox, QDoubleSpinBox, QComboBox, QFrame, QGroupBox)
from PyQt5.QtGui import QPalette
from PyQt5.QtCore import qInstallMessageHandler

app = QApplication(sys.argv)
app.setStyle("Fusion")
from scarlet_test_panel.theme import BG_MAIN, TEXT_DARK, BG_CARD, BG_INNER
pal = QPalette()
pal.setColor(QPalette.Window, BG_MAIN)
pal.setColor(QPalette.WindowText, TEXT_DARK)
pal.setColor(QPalette.Base, BG_CARD)
pal.setColor(QPalette.AlternateBase, BG_INNER)
pal.setColor(QPalette.Text, TEXT_DARK)
pal.setColor(QPalette.Button, BG_INNER)
pal.setColor(QPalette.ButtonText, TEXT_DARK)
app.setPalette(pal)

captured = []
_orig = QWidget.setStyleSheet


def recording(self, qss):
    captured.append((type(self).__name__, qss))
    return _orig(self, qss)


QWidget.setStyleSheet = recording

import scarlet_test_panel.main as m

win = m.PumpDashboard()
win.show()
app.processEvents()
QWidget.setStyleSheet = _orig

styles = {}
for cls, qss in captured:
    styles.setdefault(cls, set()).add(qss)

CLASSES = {"QLabel": QLabel, "QPushButton": QPushButton, "QSpinBox": QSpinBox,
           "QDoubleSpinBox": QDoubleSpinBox, "QComboBox": QComboBox,
           "QFrame": QFrame, "QGroupBox": QGroupBox}
msgs = []


def handler(mode, ctx, msg):
    msgs.append(msg)


qInstallMessageHandler(handler)

bad = []
for cls, qss_set in styles.items():
    for qss in sorted(qss_set):
        if not qss.strip():
            continue
        for tgt, tcls in CLASSES.items():
            msgs.clear()
            w = tcls()
            try:
                w.setStyleSheet(qss)
                app.processEvents()
            except Exception:
                pass
            n = sum(1 for mm in msgs if "Could not parse stylesheet" in mm)
            if n:
                bad.append((cls, tgt, n, qss[:160]))
                print(f"FAIL src[{cls}] -> [{tgt}] x{n}: {qss[:160]!r}")

qInstallMessageHandler(None)
print(f"\ntotal variants: {sum(len(s) for s in styles.values())}  failures: {len(bad)}")
win.close()