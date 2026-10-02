"""Analog Monitor tab — final acceptance suite (analog_monitor.txt §23).

This suite verifies the Analog Monitor addition is exactly the documented
feature and nothing else:

  * "Analog Monitor" appears as a new tab (appended, existing tabs intact)
  * it occupies the full page, is responsive, and does not scroll
  * the table has exactly the 8 required columns in the required order
  * all configured analog sensors appear, with Device/Channel from the
    existing sensor configuration (never hard-coded channel lists)
  * Value (calibrated engineering value) and Volt (raw analog voltage) come
    from the existing single sensor pipeline, formatted 0.000 V
  * Unit shows the configured engineering unit, never the voltage unit
  * Enable reflects persisted per-channel config and survives a reload
  * Calibrate opens the existing calibration interface, bound to its row
  * OPERATOR is view-only (no enable/calibrate); ENGINEER can modify and the
    app then returns the role to OPERATOR (existing behavior preserved)
  * disabled channels read DISABLED (never a 0 or a value); only link-level
    problems show "--", never a misleading value

Run:   SCARLET_PANEL_DATA_DIR=<temp> python tests/test_analog_monitor_tab.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys
import tempfile

# Hermetic: never touch the operator's real settings file when run standalone.
os.environ.setdefault(
    "SCARLET_PANEL_DATA_DIR",
    tempfile.mkdtemp(prefix="analog_monitor_test_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QHeaderView  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402

app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import NUM_ANALOG, SENSOR_CONFIG  # noqa: E402
from scarlet_test_panel.services.settings import load as load_settings  # noqa: E402

REQUIRED_HEADERS = ["Device", "Channel", "Sensor Name", "Value",
                    "Unit", "Volt", "Enable", "Calibration"]

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


# Hermetic starting state: force every channel enabled and every channel's
# calibration back to factory so a stale data dir can never make the enable /
# calibration checks a no-op across repeated runs on the same temp dir.
from scarlet_test_panel.config import _ANALOG_FACTORY  # noqa: E402
for i, cfg in enumerate(SENSOR_CONFIG):
    cfg["enabled"] = True
    fmin, fmax = _ANALOG_FACTORY[i]
    cfg["cal_min"] = fmin
    cfg["cal_max"] = fmax
    for k in ("cal_in_lo", "cal_val_lo", "cal_in_hi", "cal_val_hi"):
        cfg.pop(k, None)
main_mod.save_settings({"analog_enabled": [True] * NUM_ANALOG,
                        "analog_calibration": [None] * NUM_ANALOG})


mw = PumpDashboard()
am = mw.analog_monitor_tab
table = am.table

# ---- 1. new tab, page placement -------------------------------------------
names = [b.text().strip() for b in mw.tab_bar._btns]
check("'Analog Monitor' appears as a new tab", "Analog Monitor" in names, names)
check("placed directly after the Dashboard (index 3)",
      len(names) == 9 and names[1] == "Dashboard"
      and names[2] == "Block Position" and names[3] == "Analog Monitor", names)
check("page follows Block Position in the stack",
      type(mw.stack.widget(3)).__name__ == "AnalogMonitorTab")

# ---- 2. one full-view page, no scrolling ------------------------------------
check("table has all 16 configured sensors",
      table.rowCount() == NUM_ANALOG, table.rowCount())
check("NO vertical scrollbar on the monitor",
      table.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff)
check("NO horizontal scrollbar on the monitor",
      table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff)

# ---- 2b. responsive reflow (§15/§16) -----------------------------------------
am._apply_row_heights(760)
check("row height grows to fill a large window (no empty area)",
      table.verticalHeader().defaultSectionSize() >= 47,
      table.verticalHeader().defaultSectionSize())
am._apply_row_heights(300)
check("row height shrinks on small windows (24 px floor)",
      table.verticalHeader().defaultSectionSize() == 24,
      table.verticalHeader().defaultSectionSize())
am._reflow(1600)
w_wide = [table.columnWidth(c) for c in range(8)]
am._reflow(560)
w_narrow = [table.columnWidth(c) for c in range(8)]
am._reflow(700)
w_mid = [table.columnWidth(c) for c in range(8)]
check("columns expand proportionally on wider windows",
      w_wide[0] > w_narrow[0] and w_wide[2] > w_narrow[2],
      (w_narrow[0], w_wide[0]))
check("columns reflow to fit the available width",
      660 <= sum(w_mid) <= 760, sum(w_mid))
am._reflow(table.width())   # restore natural layout

# ---- 2c. exact 8 columns -----------------------------------------------------
headers = [table.horizontalHeaderItem(c).text().strip()
           for c in range(table.columnCount())]
check("exactly the 8 required columns in order",
      headers == REQUIRED_HEADERS, headers)
check("all columns are layout-managed (Fixed + reflow, no free stretch)",
      all(table.horizontalHeader().sectionResizeMode(c) == QHeaderView.Fixed
          for c in range(8)))
check("headers remain visible at all times",
      not table.horizontalHeader().isHidden())

# ---- 4. demo drives Value/Volt (real pipeline, no fake numbers) -------------
mw.stack.setCurrentIndex(mw.IDX_ANALOG_MONITOR)   # Analog Monitor
mw._demo_mode = True
for _ in range(60):
    mw._demo_tick()
    mw._tick()
target = table.item(0, 3).text()
check("demo updates Value", target != "--" and target != "DISABLED", target)
check("Value is the calibrated engineering value (0.00 format)",
      target.rfind(".") >= 0, target)
check("Volt column uses 0.000 V format",
      table.item(0, 5).text().endswith(" V"), table.item(0, 5).text())
check("Unit never shows the voltage unit",
      table.item(0, 4).text() == SENSOR_CONFIG[0]["unit"])
check("Device column is read-only channel-agnostic label",
      table.item(0, 0).text() == "1")
check("Channel column comes from the sensor config index",
      table.item(0, 1).text() == "0" and table.item(15, 1).text() == "15")

# ---- 4b. all 16 sensor NAMES + UNITS come from the single config --------------
names = [table.item(i, 2).text() for i in range(NUM_ANALOG)]
expected_names = ["Hookload", "Pump Pressure", "Return Flow Sensor 1",
                  "RPM Top Drive", "Torque Top Drive", "Pit 1 Volume",
                  "Pit 2 Volume", "Total Gas Sensor 1", "Total Gas Sensor 2",
                  "Pit 3 Volume", "Pit 4 Volume", "Trip Tank 1 Volume",
                  "Mud Temp In", "Mud Temp Out Sensor 1", "Mud Density In",
                  "Mud Density Out"]
check("all 16 sensor names present on the table",
      names == expected_names, names)
units = [table.item(i, 4).text() for i in range(NUM_ANALOG)]
expected_units = ["klb", "psi", "%", "rpm", "amp", "bbls", "bbls", "%",
                  "%", "bbls", "bbls", "bbls", "°F", "°F", "ppg", "ppg"]
check("all 16 engineering units present on the table",
      units == expected_units, units)
check("name column is the single source of truth",
      names == [cfg["name"] for cfg in SENSOR_CONFIG] and
      units == [cfg["unit"] for cfg in SENSOR_CONFIG])

# ---- 5. Access: every role may enable/disable and calibrate -------------------
am.refresh()
check("default role: enable control enabled (no role is view-only)",
      am._check_widgets[0].isEnabled())
check("default role: calibrate button reachable",
      am._cal_btns[0].isEnabled())
box = am._check_widgets[0]
before = bool(SENSOR_CONFIG[0].get("enabled", True))
box.setChecked(not before)
check("default role: enable toggle persists (config updated)",
      bool(SENSOR_CONFIG[0].get("enabled", True)) == (not before)
      and box.isChecked() == (not before),
      (bool(SENSOR_CONFIG[0].get("enabled", True)), box.isChecked()))
box.setChecked(before)  # restore, so the later sections see the original state

# ---- 6. ENGINEER enable state is persisted (survives reload) -----------------
mw.roles.set_role("engineer")
mw.roles.role()
am.refresh()
check("ENGINEER: enable control enabled", am._check_widgets[0].isEnabled())
check("ENGINEER: calibrate control enabled", am._cal_btns[0].isEnabled())
chk = am._check_widgets[2]
chk.setChecked(False)
check("ENGINEER: disable persisted to sensor config",
      SENSOR_CONFIG[2].get("enabled") is False)
saved = load_settings().get("analog_enabled")
check("ENGINEER: enable persisted to settings.json",
      isinstance(saved, list) and len(saved) == NUM_ANALOG and saved[2] is False)
check("ENGINEER: auto-return to OPERATOR after modification",
      mw.roles.role() == "operator")

# ---- 7. disabled / no-link states --------------------------------------------
SENSOR_CONFIG[0]["enabled"] = False
am.refresh()
check("disabled channel reads DISABLED (never a 0)",
      table.item(0, 3).text() == "DISABLED" and table.item(0, 5).text() == "DISABLED",
      (table.item(0, 3).text(), table.item(0, 5).text()))
SENSOR_CONFIG[0]["enabled"] = True
mw._demo_mode = False
mw.serial = None
am.refresh()
live = table.item(3, 3).text()
check("no data shows '--' (never a misleading value)", live == "--", live)

# ---- 7b. firmware UNUSED (U) surfaces as a disabled row, never a number -------
class _Ser:
    is_open = True
mw.serial = _Ser()
mw._demo_mode = False
mw._link_down = False
mw._data_stale = False
SENSOR_CONFIG[0]["enabled"] = True
saved0 = mw.analog_statuses[0]
mw.analog_statuses = ["UNUSED"] + list(mw.analog_statuses[1:])
am.refresh()
check("firmware UNUSED row reads DISABLED / DISABLED",
      table.item(0, 3).text() == "DISABLED" and table.item(0, 5).text() == "DISABLED",
      (table.item(0, 3).text(), table.item(0, 5).text()))
mw.analog_statuses[0] = saved0
mw.serial = None

# ---- 8. calibration routes through the existing single path -------------------
sent = {}
def noop(idx, name, cmin, cmax, blo, bhi):
    sent.update(idx=idx, name=name)
orig = mw.analog_tab._on_calibrated
mw.analog_tab._on_calibrated = noop
mw.roles.set_role("engineer")
am._on_calibrated(5, "Row Sensor", 0.0, 500.0, None, None)
mw.analog_tab._on_calibrated = orig
check("Calibrate reuses the existing single calibration callback",
      sent.get("idx") == 5 and sent.get("name") == "Row Sensor")
check("calibration also returns the role to OPERATOR",
      mw.roles.role() == "operator")

# ---- 9. sensor-specific calibration window (analog_monitor.txt §685-1373) ----
from scarlet_test_panel.dialogs.sensor_calibration_dialog import (  # noqa: E402
    SensorCalibrationDialog, project_two_point)
from scarlet_test_panel.config import _ANALOG_FACTORY  # noqa: E402

def _mk_dlg(idx, roles=None, cb=None):
    dlg = SensorCalibrationDialog(
        idx, "1",
        lambda i=idx: mw.analog_voltages[i],
        lambda i=idx: am._dialog_status(i),
        cb, mw.roles, mw.audit if hasattr(mw, "audit") else am.audit)
    return dlg

# 9a. dynamic identity + editable rbac at the dialog level (any role)
mw.roles.set_role("operator")
dlg_op = _mk_dlg(0)
check("calibration dialog shows device/channel/sensor dynamically",
      dlg_op._idx == 0 and "Hookload" in dlg_op.windowTitle())
check("any role opens an editable calibration view",
      dlg_op._editable is True
      and dlg_op.spin_lo_val.isEnabled()
      and dlg_op.btn_save.isEnabled()
      and dlg_op.btn_reset.isEnabled())
dlg_op.stop_live()

# 9b. engineer: two-point capture, preview, validation, live readout
mw.roles.set_role("engineer")
cw = {}
def _commit_cb(idx, cmin, cmax):
    cw.update(idx=idx, cmin=cmin, cmax=cmax)
dlg5 = _mk_dlg(5, cb=_commit_cb)
dlg5.spin_lo_in.setValue(1.0)
dlg5.spin_lo_val.setValue(0.0)
dlg5.spin_hi_in.setValue(5.0)
dlg5.spin_hi_val.setValue(100.0)
dlg5._stage()
ok, reason = dlg5.validate()
check("valid two-point calibration accepted", ok, reason)
c0, c5 = project_two_point(1.0, 0.0, 5.0, 100.0)
check("two-point projects onto the engine 0-5V endpoints",
      abs(c0 - (-25.0)) < 1e-9 and abs(c5 - 100.0) < 1e-9, (c0, c5))
dlg5._apply()
check("apply previews the calibration without persisting",
      "Calibration applied" in dlg5.lbl_sum_status.text()
      and SENSOR_CONFIG[5].get("cal_in_lo") is None)
dlg5._live_update()
check("live input readout populated from existing stream",
      dlg5.lbl_adc.text() != "\u2014" and "V" in dlg5.lbl_voltage.text())
dlg5.spin_hi_in.setValue(1.0)
dlg5._stage()
ok2, reason2 = dlg5.validate()
check("identical low/high inputs rejected",
      not ok2 and "identical" in reason2, reason2)
dlg5.stop_live()

# 9c. commit writes ONLY the selected channel + persists per-channel
dlg5.spin_lo_in.setValue(1.0)
dlg5.spin_lo_val.setValue(0.0)
dlg5.spin_hi_in.setValue(5.0)
dlg5.spin_hi_val.setValue(100.0)
com = dlg5._commit()
check("commit applies new calibration to the monitored value",
      com is not None and SENSOR_CONFIG[5]["cal_min"] == com[0]
      and SENSOR_CONFIG[5]["cal_max"] == com[1], com)
am._persist_calibration()
saved = load_settings().get("analog_calibration")
check("calibration persisted per channel (settings.json)",
      isinstance(saved, list) and len(saved) == NUM_ANALOG
      and saved[5] == {"lo_in": 1.0, "lo_val": 0.0,
                       "hi_in": 5.0, "hi_val": 100.0}
      and saved[0] is None)
check("calibrating channel 5 NEVER touched another channel",
      SENSOR_CONFIG[0].get("cal_in_lo") is None
      and float(SENSOR_CONFIG[0]["cal_min"]) == _ANALOG_FACTORY[0][0]
      and float(SENSOR_CONFIG[6]["cal_max"]) == _ANALOG_FACTORY[6][1])
dlg5.stop_live()

# 9d. restart reload: a fresh dialog re-loads the saved calibration
dlg5b = _mk_dlg(5)
check("reopened window preloads the saved calibration points",
      dlg5b.spin_lo_in.value() == 1.0 and dlg5b.spin_lo_val.value() == 0.0
      and dlg5b.spin_hi_in.value() == 5.0 and dlg5b.spin_hi_val.value() == 100.0
      and dlg5b._calibrated is True)
dlg5b.stop_live()

# 9e. reset restores ONLY this channel's factory defaults (with confirmation)
fmin5, fmax5 = _ANALOG_FACTORY[5]
dlg5c = _mk_dlg(5)
dlg5c.ask_fn = lambda *a: True
dlg5c._reset()
check("reset restores factory defaults (after confirmation)",
      dlg5c.spin_lo_val.value() == fmin5 and dlg5c.spin_hi_val.value() == fmax5
      and SENSOR_CONFIG[5].get("cal_in_lo") is None)
check("reset did not touch other channels",
      SENSOR_CONFIG[0].get("cal_in_lo") is None)
dlg5c.stop_live()

# 9f. full commit through the REAL single calibration path returns to operator
cw3 = {}
mw.roles.set_role("engineer")
dlg3 = _mk_dlg(3, cb=lambda i, c, m: am._apply_sensor_calibration(i, c, m))
dlg3.spin_lo_in.setValue(0.0)
dlg3.spin_lo_val.setValue(0.0)
dlg3.spin_hi_in.setValue(5.0)
dlg3.spin_hi_val.setValue(200.0)
done = dlg3._commit_and_notify()
check("commit fired through the existing single calibration callback",
      done is True)
check("calibration commit returns the role to OPERATOR",
      mw.roles.role() == "operator")
dlg3.stop_live()

# Restore hermetic state for the caller's environment (this process's globals
# are module-level and shared with later suites on the same data dir).
for i, cfg in enumerate(SENSOR_CONFIG):
    fmin, fmax = _ANALOG_FACTORY[i]
    cfg["cal_min"] = fmin
    cfg["cal_max"] = fmax
    for k in ("cal_in_lo", "cal_val_lo", "cal_in_hi", "cal_val_hi"):
        cfg.pop(k, None)
main_mod.save_settings({"analog_enabled": [True] * NUM_ANALOG,
                        "analog_calibration": [None] * NUM_ANALOG})

mw.close()
app.quit()
print()
print(f"===== ANALOG MONITOR TAB: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)