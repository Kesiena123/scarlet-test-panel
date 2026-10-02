"""Protocol 4.2 analog-sensor regression suite (analog_sensor.txt).

Locks in the dashboard half of the 16-channel live analog bank:

  * the periodic `report` carries self-describing `sensor1`..`sensor16`
    voltage keys plus the compact position-indexed `sensorStatus` string;
    `char[i]` is the status of Sensor i (index 0 == Sensor 0)
  * sensor voltages are parsed into `mw.analog_voltages` (clamped 0..5 V)
  * the compact status chars N/L/H/F/D/C/I/X decode into the long status
    names in `mw.analog_statuses`, aligned to `analog_voltages`
  * NO DATA (X) never overwrites a channel's prior status; every reported
    char still updates normally
  * unknown chars become INVALID, missing fields are a clean no-op
  * the Analog Monitor surfaces a per-channel diagnostic as a visible tag
    (e.g. "FAULT 1,234.56") only when the link is live — a NORMAL channel
    stays a plain number; no 9th status column is added
  * the legacy no-CRC path still feeds voltages (clamped) and never touches
    per-sensor statuses

Run:   python -u tests/test_analog_sensor_protocol.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys
import tempfile
import re
import json

os.environ.setdefault("SCARLET_PANEL_DATA_DIR",
                       tempfile.mkdtemp(prefix="analog_sensor_test_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.services.protocol import (  # noqa: E402
    fletcher16, decode_message, SENSOR_STATUS_CHARS, SENSOR_STATUS_NAMES)
from scarlet_test_panel.config import NUM_ANALOG, SENSOR_CONFIG  # noqa: E402

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
    """Wire a report exactly like the firmware: fletcher-16 over the payload
    that precedes the recv-side `,"crc":` marker."""
    body = json.dumps(d, separators=(",", ":"))
    payload = body[1:-1]
    return body[:-1] + ',"crc":' + str(fletcher16(payload.encode("ascii"))) + "}"


class FakeSerial:
    is_open = True


_SENSORS_ONLY = {
    **{f"sensor{i + 1}": round(0.123 * (i % 40), 3) for i in range(NUM_ANALOG)},
    "sensorStatus": "NNNNNNNNLLNHFNNH",
}
_REPORT = {
    "type": "report", "currentTicks": 4437, "blockPositionFt": 31.58,
    "velocityFtMin": 0.0, "direction": "UP", "calStatus": "VALID",
    "calInRange": 1, "onBottom": False, "currentLayer": 1,
    "calCounter1": 1000, "calPosition1": 0.0, "countsPerFoot1": 31.7,
    "calCounter2": 2000, "calPosition2": 10.0, "countsPerFoot2": 31.7,
    "calCounter3": 3000, "calPosition3": 20.0, "countsPerFoot3": 31.7,
    "calCounter4": 4000, "calPosition4": 30.0, "countsPerFoot4": 31.7,
    "calCounter5": 5000, "calPosition5": 40.0, "countsPerFoot5": 31.7,
    "calCounter6": 6000, "calPosition6": 50.0, "countsPerFoot6": 31.7,
    "witsCorrectionFt": 0.5, "encoderPolarity": 1,
    "uptime_s": 123, "sequence": 13,
}
_REPORT.update(_SENSORS_ONLY)

mw = PumpDashboard()
am = mw.analog_monitor_tab


# ---- 1. voltage ingestion through the real wire path -----------------------
mw._apply_protocol(decode_message(wire(_REPORT)))
v = mw.analog_voltages
cond = (len(v) == NUM_ANALOG and v[0] == 0.0 and v[2] == round(0.123 * 2, 3)
        and v[15] == round(0.123 * 15, 3))
check("sensor1..sensor16 volts ingested via checksummed report", cond, v[:4])

# Clamping: out-of-range volts are clipped to the 0..5 V instrument range.
bad = dict(_REPORT)
bad["sensor2"] = 9.99
bad["sensor3"] = -1.0
mw._apply_protocol(decode_message(wire(bad)))
cond = (mw.analog_voltages[1] == 5.0 and mw.analog_voltages[2] == 0.0)
check("sensor volts clamped to 0..5 V", cond,
      (mw.analog_voltages[1], mw.analog_voltages[2]))


# ---- 2. compact sensorStatus decode ----------------------------------------
s = mw.analog_statuses
# "NNNNNNNN" + "LLNHFNNH" -> chars[8..15] = L L N H F N N H
cond = (s[0] == "NORMAL" and s[8] == "LOW" and s[9] == "LOW"
        and s[10] == "NORMAL" and s[11] == "HIGH" and s[12] == "FAULT"
        and s[15] == "HIGH")
check("compact sensorStatus aligns char[i] to Sensor i (index 0 == Sensor 0)",
      cond, s)
check("char map covers the full spec vocabulary",
      set(SENSOR_STATUS_CHARS) == {"N", "L", "H", "F", "D", "C", "I", "X", "U"}
      and SENSOR_STATUS_NAMES["NORMAL"] == "N"
      and SENSOR_STATUS_NAMES["NO DATA"] == "X"
      and SENSOR_STATUS_NAMES["UNUSED"] == "U", SENSOR_STATUS_CHARS)

prior0 = mw.analog_statuses[0]
xrep = dict(_REPORT)
xrep["sensorStatus"] = "XNNNNNNNNNNNNNNN"
mw._apply_protocol(decode_message(wire(xrep)))
cond = (mw.analog_statuses[0] == prior0          # X = NO DATA, keep prior
        and mw.analog_statuses[8] == "NORMAL")   # reported chars still update
check("NO DATA (X) leaves prior status alone; others update", cond,
      (mw.analog_statuses[0], mw.analog_statuses[8]))

unk = dict(_REPORT)
unk["sensorStatus"] = "?NNNNNNNNNNNNNNN"
mw._apply_protocol(decode_message(wire(unk)))
cond = mw.analog_statuses[0] == "INVALID"
check("unknown status char maps to INVALID", cond, mw.analog_statuses[0])

# ---- 2b. UNUSED (U) channels are flagged and NEVER ingested -----------------
seed = dict(_REPORT)
seed["sensor1"] = 2.500                       # a real live value on ch0
mw._apply_protocol(decode_message(wire(seed)))
unused = dict(_REPORT)
unused["sensorStatus"] = "UNNNNNNNNNNNNNNN"
unused["sensor1"] = 4.000
mw._apply_protocol(decode_message(wire(unused)))
cond = (mw.analog_statuses[0] == "UNUSED"
        and mw.analog_voltages[0] == 0.0)   # voltage_read.txt §4: unused = 0.000 V,
check("UNUSED (U) decodes AND zeroes the channel (never 4.000, never stale)",
      cond, (mw.analog_statuses[0], mw.analog_voltages[0]))


# ---- 3. missing fields are clean no-ops ------------------------------------
mw.analog_statuses = [""] * NUM_ANALOG
plain = {k: x for k, x in _REPORT.items()
         if k not in _SENSORS_ONLY}
mw._apply_protocol(decode_message(wire(plain)))
check("report without sensorStatus leaves statuses untouched",
      all(x == "" for x in mw.analog_statuses), mw.analog_statuses[:3])

short = dict(_REPORT)
short["sensorStatus"] = "NH"       # shorter than 16: only valid chars applied
mw._apply_protocol(decode_message(wire(short)))
cond = (mw.analog_statuses[0] == "NORMAL" and mw.analog_statuses[1] == "HIGH"
        and mw.analog_statuses[2] == "")
check("short sensorStatus decodes only available chars, no crash", cond,
      mw.analog_statuses[:3])


# ---- 4. monitor surfacing (live link only) ---------------------------------
# The factory default enables only instrumented channel 0; the faulted
# channel under test must be enabled so the tag is exerciseable.
SENSOR_CONFIG[12]["enabled"] = True
mw._demo_mode = False
mw.serial = FakeSerial()
mw._link_down = False
mw._data_stale = False
mw._apply_protocol(decode_message(wire(_REPORT)))   # FAULT at Sensor 12
am.refresh()
faulted = am.table.item(12, 3).text()
cond = faulted.startswith("FAULT ")
check("faulted channel surfaces a visible 'FAULT <value>' tag on a live link",
      cond, faulted)
plain_v = am.table.item(0, 3).text()
cond = (plain_v not in ("--", "DISABLED")
        and re.fullmatch(r"-?[\d,]+\.\d{2}", plain_v) is not None
        and not re.search(r"\b[A-Z]{2,}\b", plain_v))
check("NORMAL channel stays a plain engineering number, no tag", cond, plain_v)

# Demo mode: no per-sensor tags (SIMULATION verdict wins).
mw._demo_mode = True
am.refresh()
check("demo mode keeps the SIMULATION verdict (no firmware tags)",
      not am.table.item(12, 3).text().startswith("FAULT "),
      am.table.item(12, 3).text())
mw._demo_mode = False


# ---- 5. legacy no-CRC path -------------------------------------------------
mw.analog_statuses = [""] * NUM_ANALOG
leg = {"sensor1": 1.234, "sensor9": 4.999, "sensor2": -0.5}
mw._apply_legacy(leg)
cond = (abs(mw.analog_voltages[0] - 1.234) < 1e-9
        and abs(mw.analog_voltages[8] - 4.999) < 1e-9
        and abs(mw.analog_voltages[1] - 0.0) < 1e-9
        and all(x == "" for x in mw.analog_statuses))
check("legacy path feeds clamped volts and never touches statuses", cond,
      (mw.analog_voltages[0], mw.analog_voltages[1], mw.analog_statuses[:2]))

mw._apply_analog_telemetry(None)
mw._apply_analog_telemetry(42)
cond = mw.analog_voltages[0] == 1.234
check("non-dict telemetry is a silent no-op", cond, mw.analog_voltages[0])

print(f"\n===== ANALOG SENSOR PROTOCOL (4.2): PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(0 if _failures == 0 else 1)