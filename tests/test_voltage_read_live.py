"""Live-integration suite for voltage_read.txt (16-channel analog).

Replays the firmware's periodic-report wire format end-to-end (fletcher-16
checksummed JSON -> decode_message -> _apply_protocol -> monitor tab) and
verifies the spec's §18 acceptance tests against the REAL pipeline:

  Test 1 — A0 only:   one real channel, the rest disabled. Only A0 moves; the
                      unused channels read DISABLED (never a 0 or a random
                      floating voltage).
  Test 2 — coupling:  changing A0 must not change A1..A15.
  Test 3 — multiple:  A0..A3 each report their own voltage, fully independent.
  Test 4 — unused:    A4..A15 read DISABLED, never numbers.
  Test 5 — stability: stable input stays stable (no artificial locking).

Run:   python -u tests/test_voltage_read_live.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys
import json
import tempfile

os.environ.setdefault("SCARLET_PANEL_DATA_DIR",
                       tempfile.mkdtemp(prefix="voltage_read_live_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import NUM_ANALOG, SENSOR_CONFIG  # noqa: E402
from scarlet_test_panel.services.protocol import fletcher16, decode_message  # noqa: E402

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")
    else:
        print(f"  PASS: {name}")


def wire(d):
    body = json.dumps(d, separators=(",", ":"))
    payload = body[1:-1]
    return body[:-1] + ',"crc":' + str(fletcher16(payload.encode("ascii"))) + "}"


def report(volts, status_chars):
    """A firmware-style `report` carrying sensor1..16 VOLTS + sensorStatus."""
    d = {"type": "report", "currentTicks": 0, "blockPositionFt": 0.0,
         "velocityFtMin": 0.0, "direction": "UP", "calStatus": "VALID",
         "calInRange": 1, "uptime_s": 1}
    for i in range(NUM_ANALOG):
        d[f"sensor{i + 1}"] = volts[i]
    d["sensorStatus"] = status_chars
    return d


class FakeSerial:
    is_open = True


mw = PumpDashboard()
am = mw.analog_monitor_tab
mw.serial = FakeSerial()
mw._demo_mode = False
mw._link_down = False
mw._data_stale = False
for cfg in SENSOR_CONFIG:                 # hermetic: every row locally enabled
    cfg["enabled"] = True
mw.analog_statuses = [""] * NUM_ANALOG

table = am.table


# ---- Test 1 — A0 only (real sensor), A1..A15 disabled ----------------------
volts = [0.0] * NUM_ANALOG
volts[0] = 2.500
chars = "N" + "U" * (NUM_ANALOG - 1)
mw._apply_protocol(decode_message(wire(report(volts, chars))))
cond = (mw.analog_statuses[0] == "NORMAL"
        and all(mw.analog_statuses[i] == "UNUSED" for i in range(1, NUM_ANALOG)))
check("T1: A0=NORMAL, A1..A15=UNUSED", cond, mw.analog_statuses)
cond = (mw.analog_voltages[0] == 2.5
        and all(mw.analog_voltages[i] == 0.0 for i in range(1, NUM_ANALOG)))
check("T1: A0=2.5 V, unused channels read 0.000 V (no floating/phantom)",
      cond, mw.analog_voltages[:4])

am.refresh()
check("T1: monitor shows A0 as a live value", table.item(0, 3).text() not in ("--", "UNUSED"),
      table.item(0, 3).text())
check("T1: monitor shows A1..A3 as DISABLED (never a number)",
      table.item(1, 3).text() == "DISABLED" and table.item(1, 5).text() == "DISABLED"
      and table.item(3, 3).text() == "DISABLED",
      (table.item(1, 3).text(), table.item(1, 5).text()))


# ---- Test 2 — changing A0 must NOT change A1..A15 ---------------------------
volts[0] = 3.500
mw._apply_protocol(decode_message(wire(report(volts, chars))))
cond = (mw.analog_voltages[0] == 3.5
        and all(mw.analog_voltages[i] == 0.0 for i in range(1, NUM_ANALOG)))
check("T2: A0 2.5->3.5 V tracked; A1..A15 stay 0.000 (no channel coupling)",
      cond, mw.analog_voltages[:4])


# ---- Test 3 — multiple channels, each independent ---------------------------
volts = [0.0] * NUM_ANALOG
volts[0], volts[1], volts[2], volts[3] = 1.00, 2.00, 3.00, 4.00
chars = "N" * 4 + "U" * (NUM_ANALOG - 4)
mw._apply_protocol(decode_message(wire(report(volts, chars))))
cond = (abs(mw.analog_voltages[0] - 1.0) < 1e-9
        and abs(mw.analog_voltages[1] - 2.0) < 1e-9
        and abs(mw.analog_voltages[2] - 3.0) < 1e-9
        and abs(mw.analog_voltages[3] - 4.0) < 1e-9
        and all(mw.analog_voltages[i] == 0.0 for i in range(4, NUM_ANALOG)))
check("T3: A0=1.0 A1=2.0 A2=3.0 A3=4.0 reported independently, A4.. unused",
      cond, mw.analog_voltages[:6])

volts[0] = 5.00                      # re-tune ONLY A0
mw._apply_protocol(decode_message(wire(report(volts, chars))))
cond = (mw.analog_voltages[0] == 5.0 and mw.analog_voltages[1] == 2.0
        and mw.analog_voltages[2] == 3.0 and mw.analog_voltages[3] == 4.0)
check("T2/3: re-tuning A0 leaves A1..A3 exactly as they were", cond,
      mw.analog_voltages[:4])


# ---- Test 4 — unused channels read DISABLED, never a number -------------------
am.refresh()
check("T4: A4..A15 rows read DISABLED / DISABLED (never a random floating value)",
      all(table.item(i, 3).text() == "DISABLED" and table.item(i, 5).text() == "DISABLED"
          for i in range(4, NUM_ANALOG)),
      table.item(6, 3).text())


# ---- Test 5 — stability: stable input stays stable (no locking) -------------
volts = [0.0] * NUM_ANALOG
volts[0] = 2.490
chars = "N" + "U" * (NUM_ANALOG - 1)
seen = []
for _ in range(5):
    mw._apply_protocol(decode_message(wire(report(volts, chars))))
    seen.append(mw.analog_voltages[0])
cond = all(abs(v - 2.49) <= 0.005 for v in seen)   # within the ADC noise band
check("T5: stable 2.49 V input stays stable across reports (no jump/lock)",
      cond, seen)

if _failures:
    print(f"\n{_failures}/{_total} checks FAILED")
    sys.exit(1)
print(f"\nVOLTAGE_READ LIVE: {_total}/{_total} checks passed")