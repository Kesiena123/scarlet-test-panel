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

parse_warnings = []


def handler(mode, ctx, msg):
    if "Could not parse stylesheet of object" in msg:
        parse_warnings.append(msg)


qInstallMessageHandler(handler)

from scarlet_test_panel.main import PumpDashboard

# Capture all setStyleSheet calls: (widget_type, address, qss_string)

from PyQt5.QtWidgets import QWidget

ss_log = []
_real_setStyleSheet = QWidget.setStyleSheet


def logging_ss(self, qss):
    ss_log.append((type(self).__name__, getattr(self, "__objaddr__", lambda: 0)(), str(qss)))
    return _real_setStyleSheet(self, qss)


QWidget.setStyleSheet = logging_ss

win = PumpDashboard()
win.show()

QWidget.setStyleSheet = _real_setStyleSheet  # restore for clean timer loop


def analyze():
    qInstallMessageHandler(None)

    print(f"total setStyleSheet calls: {len(ss_log)}")
    print(f"parse warnings: {len(parse_warnings)}")

    # Extract widget addresses that warned.
    warned_addrs = set()
    for w in parse_warnings:
        # e.g. "Could not parse stylesheet of object QDoubleSpinBox(0x28edd0f1460)"
        parts = w.split("(0x")
        if len(parts) == 2:
            addr = parts[1].rstrip(")")
            try:
                warned_addrs.add(int(addr, 16))
            except ValueError:
                pass

    print(f"warned widget addresses: {len(warned_addrs)}")

    # Match each warned address to the setStyleSheet calls that targeted it.
    ss_by_addr = {}
    for typ, addr_int, qss in ss_log:
        ss_by_addr.setdefault(addr_int, []).append((typ, qss))

    for addr in sorted(warned_addrs):
        calls = ss_by_addr.get(addr, [])
        if calls:
            print(f"\n  0x{addr:x} [{calls[0][0]}]: {len(calls)} calls")
            # Show the QSS string (first 200 chars)
            for i, (typ, qss) in enumerate(calls[-3:]):  # last 3 calls
                print(f"    call[-{len(calls)-i}]: {qss[:200]!r}")
        else:
            print(f"\n  0x{addr:x}: NO direct setStyleSheet call (child inheriting parent)")

    # Now test each unique QSS against each widget class to find parse failures.
    print("\n--- cross-testing all QSS strings ---")
    unique_qss = set(qss for _, _, qss in ss_log)
    print(f"unique QSS strings: {len(unique_qss)}")
    for qss in sorted(unique_qss, key=len, reverse=True)[:5]:
        print(f"  {len(qss):>4} chars: {qss[:100]!r}")

    win.close()
    app.quit()


QTimer.singleShot(3000, analyze)
app.exec_()