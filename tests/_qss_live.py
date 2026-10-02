import os, sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette
from PyQt5.QtCore import QTimer, qInstallMessageHandler

from scarlet_test_panel.theme import BG_MAIN, TEXT_DARK, BG_CARD, BG_INNER

app = QApplication(sys.argv)
app.setStyle("Fusion")
pal = QPalette()
pal.setColor(QPalette.Window, BG_MAIN)
pal.setColor(QPalette.WindowText, TEXT_DARK)
pal.setColor(QPalette.Base, BG_CARD)
pal.setColor(QPalette.AlternateBase, BG_INNER)
pal.setColor(QPalette.Text, TEXT_DARK)
pal.setColor(QPalette.Button, BG_INNER)
pal.setColor(QPalette.ButtonText, TEXT_DARK)
app.setPalette(pal)

msgs = []


def handler(mode, ctx, msg):
    msgs.append(msg)


qInstallMessageHandler(handler)

from scarlet_test_panel.main import PumpDashboard

win = PumpDashboard()
win.show()


def quit_later():
    qInstallMessageHandler(None)
    parse = [m for m in msgs if "Could not parse stylesheet" in m]
    print(f"parse warnings under live exec: {len(parse)}")
    from collections import Counter
    c = Counter()
    for e in parse:
        for k in ("QDoubleSpinBox", "QSpinBox", "QLabel", "QPushButton",
                  "QComboBox", "QFrame", "QGroupBox"):
            if k in e:
                c[k] += 1
                break
        else:
            c["other"] += 1
    print(dict(c))
    win.close()
    app.quit()


QTimer.singleShot(3000, quit_later)
app.exec_()