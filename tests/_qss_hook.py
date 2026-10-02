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

bad = []  # (widget_class, qss)
cur = []


def handler(mode, ctx, msg):
    if "Could not parse stylesheet of object" in msg:
        cur.append(msg)


qInstallMessageHandler(handler)

from PyQt5.QtWidgets import QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QComboBox, QFrame, QGroupBox
TARGETS = {"QLabel": QLabel, "QPushButton": QPushButton, "QSpinBox": QSpinBox,
           "QDoubleSpinBox": QDoubleSpinBox, "QComboBox": QComboBox,
           "QFrame": QFrame, "QGroupBox": QGroupBox}

_real = QWidget.setStyleSheet
_seen_qss = set()


def rec(self, qss):
    s = str(qss)
    _seen_qss.add(s)
    cur.clear()
    r = _real(self, qss)
    if cur:
        bad.append((type(self).__name__, s))
    return r


QWidget.setStyleSheet = rec

from scarlet_test_panel.main import PumpDashboard

win = PumpDashboard()
win.show()


def analyze():
    QWidget.setStyleSheet = _real
    qInstallMessageHandler(None)

    # 1) Synchronously-captured bad calls
    print(f"setStyleSheet calls that produced parse warnings: {len(bad)}")
    seen = {}
    for cls, qss in bad:
        seen.setdefault(cls, []).append(qss)
    for cls, lst in seen.items():
        uniq = set(lst)
        print(f"\n{class_name}(): {len(lst)} occurrences, {len(uniq)} unique strings")
        for q in uniq:
            print(f"    {q[:220]!r}")

    # 2) Re-verify every unique string applied to fresh widgets of every class
    print("\n--- cross-reverify all unique strings ---")
    fails = {}
    for qss in sorted(_seen_qss):
        for tname, tcls in TARGETS.items():
            cur.clear()
            qInstallMessageHandler(handler)
            w = tcls()
            w.setStyleSheet(qss)
            app.processEvents()
            qInstallMessageHandler(lambda m, c, msg: None)
            n = sum(1 for x in cur if "Could not parse" in x)
            if n:
                fails.setdefault(qss, set()).add(tname)
    for qss, tset in fails.items():
        print(f"FAILS on {sorted(tset)}: {qss[:180]!r}")
    print(f"total unique strings: {len(_seen_qss)}, failing strings: {len(fails)}")
    win.close()
    app.quit()


QTimer.singleShot(3000, analyze)
app.exec_()