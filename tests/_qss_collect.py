import os, sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                             QSpinBox, QDoubleSpinBox, QComboBox)
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

# Collect (widget_class, stylesheet) applied during real construction.
captured = []

_orig_setStyleSheet = QWidget.setStyleSheet


def recording(self, qss):
    captured.append((type(self).__name__, qss))
    return _orig_setStyleSheet(self, qss)


QWidget.setStyleSheet = recording

import scarlet_test_panel.main as m

win = m.PumpDashboard()
win.show()
app.processEvents()
QWidget.setStyleSheet = _orig_setStyleSheet

# Deduplicate per class.
styles = {}
for cls, qss in captured:
    styles.setdefault(cls, set()).add(qss)

# Re-apply each stylesheet to a real widget and count parse warnings via a
# Qt message handler.
warned = []
msgs = []


def handler(mode, ctx, message):
    msgs.append(message)


qInstallMessageHandler(handler)

CLASSES = {"QLabel": QLabel, "QPushButton": QPushButton, "QSpinBox": QSpinBox,
           "QDoubleSpinBox": QDoubleSpinBox, "QComboBox": QComboBox}

for cls, qss_set in sorted(styles.items()):
    if cls not in CLASSES:
        continue
    for qss in sorted(qss_set):
        if not qss.strip():
            continue
        msgs.clear()
        w = CLASSES[cls]()
        try:
            w.setStyleSheet(qss)
            app.processEvents()
        except Exception:
            pass
        bad = [m_ for m_ in msgs if "Could not parse stylesheet" in m_]
        if bad:
            # Show unique style substrings so we can read the offending part.
            warned.append((cls, qss))
            print(f"WARN [{cls}]: {len(bad)}x  {qss[:200]!r}")

qInstallMessageHandler(None)
print(f"\ntotal stylesheet variants applied at startup: {sum(len(s) for s in styles.values())}")
print(f"warned variants: {len(warned)}")
win.close()

if not warned:
    print("No stylesheet parse failures found during construction (identical to real path).")
    sys.exit(0)
sys.exit(1)