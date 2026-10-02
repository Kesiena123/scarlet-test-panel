"""HOOKLOAD BOXES — four readouts (Low Point / Current / High Point /
Voltage) on the Block Position dashboard fed from the SINGLE channel-0 analog
Hookload (mw.hookload_klb, klb = V/5 * 1000).

One source, four readouts:
  * CURRENT mirrors the live channel-0 hookload.
  * VOLTAGE mirrors the live channel-0 input voltage (V).
  * LOW POINT / HIGH POINT are the ENGINEERING VALUES set in the Hookload
    two-point calibration — SENSOR_CONFIG[0] cal_val_lo / cal_val_hi, the
    "Engineering Value (klb)" fields on the calibrate tab for the LOW / HIGH
    CALIBRATION POINT.
  * There is no separate hookload engine — only the Analog Monitor value.

Verifies:
  1. the HOOKLOAD card exists on the Block Position tab with LOW POINT /
     CURRENT / HIGH POINT / VOLTAGE readouts.
  2. CURRENT tracks the live value 1:1 across a sweep, and VOLTAGE mirrors
     the live channel-0 input voltage.
  3. LOW POINT / HIGH POINT display the channel-0 calibration engineering
     values, and follow them when the calibration changes.
  4. single source: the boxes read ONLY analog_voltages[0]; nothing else
     recomputes or stores a second hookload.
  5. RBAC: read-only for the operator, still live for the engineer.

Run:   python tests/test_hookload_boxes.py
Exit:  0 pass / 1 fail.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import (  # noqa: E402
    BIT_POS_STATE_STABLE_TICKS, SENSOR_CONFIG)
from scarlet_test_panel.services.security import (  # noqa: E402
    ROLE_OPERATOR, ROLE_ENGINEER)

_failures = 0
_total = 0

# Hermetic hookload: pin channel-0 to the factory 0..1000 klb range (0 V -> 0
# klb) so the klb thresholds map to clean voltages.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")


mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
SENSOR_CONFIG[0]["cal_min"] = 0.0      # re-pin: the dashboard re-applied any
SENSOR_CONFIG[0]["cal_max"] = 1000.0   # operator calibration on construction.


def refresh(times=BIT_POS_STATE_STABLE_TICKS):
    for _ in range(times):
        tab.refresh()


def load(klb):
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call
    SENSOR_CONFIG[0]["cal_max"] = 1000.0
    mw.analog_voltages[0] = klb / 200.0   # 40 klb == 0.20 V


# ---- 1. card structure ----------------------------------------------------
check("hookload card has a VOLTAGE readout",
      hasattr(tab, "hook_voltage_lbl") and hasattr(tab.hook_voltage_lbl, "text"),
      getattr(tab, "hook_voltage_lbl", None))
check("hookload card has a CURRENT readout",
      hasattr(tab, "hook_current_lbl") and hasattr(tab.hook_current_lbl, "text"),
      getattr(tab, "hook_current_lbl", None))
check("hookload card has a LOW POINT readout",
      hasattr(tab, "hook_low_lbl") and hasattr(tab.hook_low_lbl, "text"),
      getattr(tab, "hook_low_lbl", None))
check("hookload card has a HIGH POINT readout",
      hasattr(tab, "hook_high_lbl") and hasattr(tab.hook_high_lbl, "text"),
      getattr(tab, "hook_high_lbl", None))

# ---- 2. ENGINEERING VALUES of the Hookload calibration --------------------
# Set a known two-point calibration for channel-0 (the values an Engineer
# enters in the LOW / HIGH CALIBRATION POINT "Engineering Value (klb)" fields).
SENSOR_CONFIG[0]["cal_val_lo"] = 40.0
SENSOR_CONFIG[0]["cal_val_hi"] = 90.0
refresh()
check("Low Point shows the calibration engineering low (40.00)",
      tab.hook_low_lbl.text().replace(",", "") == "40.00",
      tab.hook_low_lbl.text())
check("High Point shows the calibration engineering high (90.00)",
      tab.hook_high_lbl.text().replace(",", "") == "90.00",
      tab.hook_high_lbl.text())

# The readouts follow the calibration when the Engineer rewrites it.
SENSOR_CONFIG[0]["cal_val_lo"] = 25.0
SENSOR_CONFIG[0]["cal_val_hi"] = 120.0
refresh()
check("Low Point follows a recalibration (25.00)",
      tab.hook_low_lbl.text().replace(",", "") == "25.00",
      tab.hook_low_lbl.text())
check("High Point follows a recalibration (120.00)",
      tab.hook_high_lbl.text().replace(",", "") == "120.00",
      tab.hook_high_lbl.text())

# ---- 3. CURRENT tracks the live value 1:1, VOLTAGE mirrors the input -----
load(0.0)
refresh()
check("idle Current mirrors the value at 0 V (0.00)",
      tab.hook_current_lbl.text().replace(",", "") == "0.00",
      tab.hook_current_lbl.text())
check("idle Voltage reads 0.000 V",
      tab.hook_voltage_lbl.text() == "0.000",
      tab.hook_voltage_lbl.text())
load(20.0)
refresh()
check("Current shows 20.00 k-lb at 0.10 V",
      tab.hook_current_lbl.text().replace(",", "") == "20.00",
      tab.hook_current_lbl.text())
check("Voltage mirrors the live channel-0 input (0.100 V)",
      tab.hook_voltage_lbl.text() == "0.100",
      tab.hook_voltage_lbl.text())
load(50.0)
refresh()
check("Current shows 50.00 k-lb at 0.25 V",
      tab.hook_current_lbl.text().replace(",", "") == "50.00",
      tab.hook_current_lbl.text())
check("Voltage mirrors the live channel-0 input (0.250 V)",
      tab.hook_voltage_lbl.text() == "0.250",
      tab.hook_voltage_lbl.text())

# ---- 4. single source — only the channel-0 analog value ------------------
# Sanity: the boxes are derived purely from analog_voltages[0]; mutating that
# channel (and nothing else) is what moved Current above. Prove the reverse:
# touching a DIFFERENT channel must leave the readouts untouched.
load(50.0)
before = (tab.hook_low_lbl.text(), tab.hook_current_lbl.text(),
          tab.hook_high_lbl.text(), tab.hook_voltage_lbl.text())
mw.analog_voltages[1] = 5.0
mw.analog_voltages[2] = 0.0
tab.refresh()
check("boxes ignore every channel except channel 0",
      (tab.hook_low_lbl.text(), tab.hook_current_lbl.text(),
       tab.hook_high_lbl.text(), tab.hook_voltage_lbl.text()) == before,
      (tab.hook_low_lbl.text(), tab.hook_current_lbl.text(),
       tab.hook_high_lbl.text(), tab.hook_voltage_lbl.text()))
check("hookload_klb reads only channel 0",
      mw.hookload_klb == 50.0, mw.hookload_klb)

# ---- 5. Access: live values visible to every role, card has no own input ----
mw.roles.set_role(ROLE_OPERATOR)
tab.refresh()
check("operator sees the live Current value",
      tab.hook_current_lbl.text().replace(",", "") == "50.00",
      tab.hook_current_lbl.text())
check("operator sees the live Voltage value",
      tab.hook_voltage_lbl.text() == "0.250",
      tab.hook_voltage_lbl.text())
check("operator sees the Low Point value",
      tab.hook_low_lbl.text().replace(",", "") == "25.00",
      tab.hook_low_lbl.text())
check("operator sees the High Point value",
      tab.hook_high_lbl.text().replace(",", "") == "120.00",
      tab.hook_high_lbl.text())
# Display-only: the HOOKLOAD section itself holds no input widget. Walk up from
# a hookload readout to its section and look for any spin box inside it.
from PyQt5.QtWidgets import QAbstractSpinBox  # noqa: E402

section = tab.hook_current_lbl
while section is not None and type(section).__name__ != "QGroupBox":
    section = section.parentWidget()
check("hookload readout belongs to a section", section is not None)
inputs = section.findChildren(QAbstractSpinBox) if section is not None else []
check("hookload card holds no editable control of its own",
      not inputs,
      [(type(w).__name__, w.objectName()) for w in inputs])

mw.close()
app.quit()
print()
print(f"===== HOOKLOAD BOXES: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)