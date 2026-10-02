import os, sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QWidget
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

# Record every setStyleSheet string seen (from construction through live ticks).
ss_seen = set()
_real = QWidget.setStyleSheet


def rec(self, qss):
    ss_seen.add(str(qss))
    return _real(self, qss)


QWidget.setStyleSheet = rec

parse_msgs = []


def handler(mode, ctx, msg):
    if "Could not parse stylesheet of object" in msg:
        parse_msgs.append(msg)


qInstallMessageHandler(handler)

from scarlet_test_panel.main import PumpDashboard

win = PumpDashboard()
win.show()


def analyze():
    qInstallMessageHandler(None)
    QWidget.setStyleSheet = _real
    print(f"parse warnings in live exec: {len(parse_msgs)}")
    print(f"unique stylesheet strings recorded: {len(ss_seen)}")

    # Test each unique string on every widget class.
    from PyQt5.QtWidgets import (QLabel, QPushButton, QSpinBox, QDoubleSpinBox,
                                 QComboBox, QFrame, QGroupBox)
    targets = {
        "QLabel": QLabel, "QPushButton": QPushButton, "QSpinBox": QSpinBox,
        "QDoubleSpinBox": QDoubleSpinBox, "QComboBox": QComboBox,
        "QFrame": QFrame, "QGroupBox": QGroupBox,
    }
    del QWidget.setStyleSheet
    from collections import Counter
    fails = Counter()
    samples = {}
    for qss in ss_seen:
        for tname, tcls in targets.items():
            msgs = []
            h2 = lambda mode, ctx, msg, _ms=msgs: _ms.append(msg)
            qInstallMessageHandler(h2)
            w = tcls()
            w.setStyleSheet(qss)
            app.processEvents()
            qInstallMessageHandler(lambda m, c, msg: None)
            n = sum(1 for x in msgs if "Could not parse" in x)
            if n:
                fails[tname] += 1
                samples.setdefault(tname, []).append((qss, n))
    qInstallMessageHandler(None)
    print(f"failed-by-target: {dict(fails)}")
    printed = set()
    for tname, lst in samples.items():
        for qss, n in lst:
            if qss not in printed:
                printed.add(qss)
                print(f"\n[{tname}] ({n}x): {qss[:260]!r}")
    win.close()
    app.quit()


QTimer.singleShot(3000, analyze)
app.exec_()