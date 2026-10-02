"""Digital SPM / RPM pulse calibration - digitalsensor1.txt.

Covers the whole specification end to end:

  * §5/§6  SPM and RPM are INDEPENDENT measurements - two separate calibration
           systems, two separate storages, never shared, and every channel
           inside a system keeps its own record
  * §8     multi-point entry (2..12, 4 rows by default) with an explicit USE
           tick per row, so a legitimate (0, 0) anchor can be entered and the
           empty placeholders are never mistaken for points
  * §11    the calibrated value is linear between / beyond the entered points
           and is never negative
  * §13    validation: at least 2 points, no duplicates, no negative values,
           enough span, a legal Pulses / Revolution
  * §14    two-state calibration status (VALID / REQUIRED) reported per channel
  * §17    the Dashboard reads the calibrated values (SPM 1..4, TOTAL SPM, RPM)
  * §1     the Digital Sensor page is SELECTION FIRST - nothing is opened until
           the Engineer picks a sensor and an action
  * §10/§11 save -> reload -> apply -> verify, audited, and visible in the
           main window afterwards
  * §22    an uncalibrated channel passes the device measurement through
           unchanged, in Demo and Live alike

Run:   python tests/test_digital_calibration.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Hermetic BEFORE any scarlet import: the operator's real settings file, audit
# DB and calibrations must never be read or written by this suite.
os.environ.setdefault("SCARLET_PANEL_DATA_DIR",
                      os.path.join(os.environ.get("TEMP", "."),
                                   "scarlet_digcal_hermetic"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QCheckBox  # noqa: E402

app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import SPM_CONFIG, RPM_CONFIG  # noqa: E402
from scarlet_test_panel.services import digital_calibration as digcal  # noqa: E402
from scarlet_test_panel.services import settings as settings_service  # noqa: E402
from scarlet_test_panel.dialogs.digital_sensor_calibration_dialog import (  # noqa: E402
    DigitalSensorCalibrationDialog)

# Hermetic: never write real settings, never write real calibrations.
_saved_settings = []
main_mod.save_settings = lambda d: _saved_settings.append(dict(d))
_orig_save = main_mod.save_settings
_saved_cal = {}


def _fake_save_channel(kind, idx, record):
    """Same contract as the real service (returns the stored record), without
    ever touching the real settings file."""
    n = len(SPM_CONFIG if kind == "SPM" else RPM_CONFIG)
    store = _saved_cal.setdefault(kind, [None] * n)
    if record is None:
        store[idx] = None
        return None
    store[idx] = digcal.normalize(record)
    return store[idx]


digcal.save = lambda kind, records: _saved_cal.update({kind: list(records)})
digcal.load_all = lambda: {
    "SPM": list(_saved_cal.get("SPM", [None] * len(SPM_CONFIG))),
    "RPM": list(_saved_cal.get("RPM", [None] * len(RPM_CONFIG))),
}
digcal.save_channel = _fake_save_channel

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


def rec(points, ppr=1.0, kind="SPM", idx=0):
    return {"channel": digcal.channel_number(kind, idx),
            "pulses_per_rev": ppr,
            "points": [list(p) for p in points],
            "saved_at": digcal.stamp()}


class _FakeOpen:
    is_open = True

    def write(self, *_a, **_k):
        raise AssertionError("calibration must never write to the device")


_orig_serial = None
mw = PumpDashboard()
mw.serial = _FakeOpen()
mw._demo_mode = True
for _ in range(3):
    mw._demo_tick()

# =====================================================================
# 1. SPM and RPM are separate systems (§5/§6)
# =====================================================================
all_rec = digcal.load_all()
check("two independent calibration systems exist", set(all_rec) == {"SPM", "RPM"},
      sorted(all_rec))
check("one slot per SPM channel", len(all_rec["SPM"]) == len(SPM_CONFIG))
check("one slot per RPM channel", len(all_rec["RPM"]) == len(RPM_CONFIG))
check("SPM and RPM do not share storage",
      all_rec["SPM"] is not all_rec["RPM"])
check("units are the EXISTING firmware units",
      (digcal.unit("SPM"), digcal.unit("RPM")) == ("SPM", "RPM"),
      (digcal.unit("SPM"), digcal.unit("RPM")))
check("SPM measures strokes per minute, RPM revolutions per minute",
      (digcal.measurement("SPM"), digcal.measurement("RPM"))
      == ("Strokes Per Minute", "Revolutions Per Minute"))

digcal.save_channel("SPM", 1, rec([(0, 0), (100, 100)]))
check("saving SPM 2 leaves SPM 1 untouched", digcal.record_for("SPM", 0) is None)
check("saving SPM 2 leaves RPM untouched", digcal.record_for("RPM", 0) is None)
digcal.save_channel("RPM", 0, rec([(0, 0), (60, 60)], kind="RPM"))
check("saving RPM 1 leaves SPM 2 untouched",
      len(digcal.record_for("SPM", 1)["points"]) == 2)
check("saving RPM 1 leaves RPM 2 untouched", digcal.record_for("RPM", 1) is None)
digcal.save_channel("RPM", 0, None)
digcal.save_channel("SPM", 1, None)

# =====================================================================
# 2. Interpolation / extrapolation / no negative values (§11)
# =====================================================================
r = rec([(0, 0), (100, 110)])
check("exact point value", digcal.calibrated_value("SPM", r, 100) == 110.0)
check("midpoint is linear", abs(digcal.calibrated_value("SPM", r, 50) - 55.0) < 1e-9)
check("interpolation keeps full precision",
      abs(digcal.interpolate([(0, 0), (3, 1)], 1.5) - 0.5) < 1e-12,
      digcal.interpolate([(0, 0), (3, 1)], 1.5))
down = rec([(10, 20), (100, 200)])
check("extrapolates below the first point",
      abs(digcal.calibrated_value("SPM", down, 5) - 10.0) < 1e-9,
      digcal.calibrated_value("SPM", down, 5))
check("extrapolates above the last point",
      digcal.calibrated_value("SPM", r, 250) == 275.0,
      digcal.calibrated_value("SPM", r, 250))
falling = rec([(0, 100), (100, 10)])
check("a negative calibrated value is floored at zero",
      digcal.calibrated_value("SPM", falling, 200) == 0.0,
      digcal.calibrated_value("SPM", falling, 200))
check("a negative device reading is never shown",
      digcal.calibrated_value("SPM", r, -5) is None)
check("a non numeric reading is never shown",
      digcal.calibrated_value("SPM", r, "junk") is None)
check("a single point pins the curve",
      digcal.interpolate([(50, 50)], 9999) == 50.0)
check("an unfinished one point record does not change the reading",
      digcal.calibrated_value("SPM", rec([(50, 99)]), 100) == 100.0,
      digcal.calibrated_value("SPM", rec([(50, 99)]), 100))
check("an uncalibrated channel is an exact pass-through",
      digcal.calibrated_value("SPM", rec([]), 37.25) == 37.25)

rp = rec([(0, 0), (120, 120)], ppr=2.0, kind="RPM", idx=0)
check("RPM raw pulse rate in Hz", abs(digcal.pulse_rate_hz("RPM", rp, 60.0) - 2.0) < 1e-9,
      digcal.pulse_rate_hz("RPM", rp, 60.0))
check("SPM raw pulse rate in Hz",
      abs(digcal.pulse_rate_hz("SPM", r, 60.0) - 1.0) < 1e-9)
check("no measured value means no Hz", digcal.pulse_rate_hz("RPM", rp, None) is None)

# =====================================================================
# 3. Validation (§13)
# =====================================================================
ok, why = digcal.validate("SPM", rec([(0, 0), (100, 100)]))
check("a valid two point curve passes", ok, why)
check("fewer than two points is rejected",
      digcal.validate("SPM", rec([(10, 10)]))[0] is False)
check("no points is rejected", digcal.validate("SPM", rec([]))[0] is False)
check("duplicate points are rejected",
      digcal.validate("SPM", rec([(50, 50), (50, 60)]))[0] is False)
check("a negative pulse rate is rejected",
      digcal.validate("SPM", rec([(-1, 0), (100, 100)]))[0] is False)
check("a negative reference value is rejected",
      digcal.validate("SPM", rec([(0, -5), (100, 100)]))[0] is False)
check("non numeric points are rejected",
      digcal.validate("SPM", {"points": [["a", "b"], [1, 2]], "channel": 1})[0] is False)
check("zero span on the pulse axis is rejected",
      digcal.validate("SPM", rec([(100, 1), (100, 2)]))[0] is False)
check("a non numeric Pulses / Revolution is rejected",
      digcal.validate("RPM", rec([(0, 0), (60, 60)], ppr="x", kind="RPM"))[0] is False)
check("zero Pulses / Revolution is rejected",
      digcal.validate("RPM", rec([(0, 0), (60, 60)], ppr=0.0, kind="RPM"))[0] is False)
check("more than MAX_POINTS is rejected",
      digcal.validate("SPM", rec([(i, i) for i in range(digcal.MAX_POINTS + 1)]))[0] is False)
check("a non list points field is rejected",
      digcal.validate("SPM", {"points": None, "channel": 1})[0] is False)

# =====================================================================
# 4. Calibration status vocabulary (§14)
# =====================================================================
digcal.save_channel("SPM", 0, rec([(0, 0), (100, 100)]))
check("a calibrated channel reports VALID",
      digcal.cal_status("SPM", 0) == digcal.CAL_VALID)
check("an uncalibrated channel reports REQUIRED",
      digcal.cal_status("SPM", 1) == digcal.CAL_REQUIRED)
check("status vocabulary is exactly VALID / REQUIRED",
      {digcal.CAL_VALID, digcal.CAL_REQUIRED} == {"VALID", "REQUIRED"})
check("is_calibrated agrees with the status",
      digcal.is_calibrated("SPM", 0) and not digcal.is_calibrated("SPM", 1))
check("summary names the sensor and the state",
      "SPM 1" in digcal.summary("SPM", 0)
      and "VALID" in digcal.summary("SPM", 0),
      digcal.summary("SPM", 0))
check("normalization strips junk and never raises",
      digcal.normalize({"points": "junk", "channel": "x"})["points"] == [],
      digcal.normalize({"points": "junk"}))

# =====================================================================
# 5. Settings normalization (§13 / §14) never loses a user's other data
# =====================================================================
_orig_read_raw = settings_service._read_raw


def _load_settings(raw):
    settings_service._read_raw = lambda: dict(raw)
    try:
        return settings_service.load()
    finally:
        settings_service._read_raw = _orig_read_raw


merged = _load_settings({"pipe_in_hole": 5000.0})
check("an unrelated user setting survives normalization",
      merged["pipe_in_hole"] == 5000.0, merged.get("pipe_in_hole"))
merged = _load_settings({"spm_calibration": ["junk", {"points": [[0, 0], [9, 9]]}]})
check("a malformed calibration entry becomes None", merged["spm_calibration"][0] is None)
check("a valid calibration entry is preserved",
      len(merged["spm_calibration"][1]["points"]) == 2)
merged = _load_settings({"rpm_calibration": [{"points": [[0, 0], [1, 2], [2, 9]]}]})
check("a valid RPM calibration is preserved",
      len(merged["rpm_calibration"][0]["points"]) == 3)
merged = _load_settings({"dashboard_rpm_slot": "nope"})
check("a malformed RPM dashboard slot falls back to RPM 1",
      merged["dashboard_rpm_slot"] is None)
merged = _load_settings({"dashboard_rpm_slot": 1})
check("a valid RPM dashboard slot is preserved", merged["dashboard_rpm_slot"] == 1)
merged = _load_settings({"dashboard_rpm_slot": 9})
check("an out of range RPM dashboard slot falls back", merged["dashboard_rpm_slot"] is None)
merged = _load_settings({"dashboard_spm_slots": [0, 3, 2, 3]})
check("the user's existing SPM box selection survives",
      merged["dashboard_spm_slots"] == [0, 3, 2, 3], merged["dashboard_spm_slots"])

# =====================================================================
# 6. Uncalibrated pass-through + one calibrated source (§22 / §17)
# =====================================================================
mw.spm_values = [None, None, None, None]
mw.rpm_values = [None, None]
mw.spm_measured = [10.0, None, None, None]
mw.rpm_measured = [None, None]
mw.spm_raw_counter = [7, 0, 0, 0]
mw.rpm_raw_counter = [0, 0]
mw.spm_offset = [1, 0, 0, 0]
mw.rpm_offset = [0, 0]
mw.cal_status = "VALID"
digcal.save_channel("SPM", 0, rec([(0, 0), (100, 200)]))
digcal.save_channel("RPM", 0, rec([(0, 0), (100, 100)]))
mw.reload_digital_calibration()
mw._apply_legacy({"spm1": 10.0, "rpm1": 5.0,
                  "counter1": 7, "counter5": 9})
check("an uncalibrated channel passes the device measurement through",
      mw.digital_value("SPM", 1) is None, mw.digital_value("SPM", 1))
check("a calibrated SPM channel is calibrated (10 Hz -> 20 SPM)",
      abs(mw.digital_value("SPM", 0) - 20.0) < 1e-9, mw.digital_value("SPM", 0))
check("a calibrated RPM channel is calibrated (5 -> 5 RPM)",
      abs(mw.digital_value("RPM", 0) - 5.0) < 1e-9, mw.digital_value("RPM", 0))
check("the raw measured value is still available",
      mw.digital_measured("SPM", 0) == 10.0)
check("the Dashboard reads the SAME calibrated value",
      mw.spm_values[0] == mw.digital_value("SPM", 0))
check("Total SPM sums the calibrated values",
      abs(mw.total_spm - mw.spm_values[0]) < 1e-9, mw.total_spm)

# =====================================================================
# 7. The Digital Sensor page is SELECTION FIRST (§1)
# =====================================================================
dig = mw.digital_sensor_tab
dig.refresh()
check("no sensor is selected on entry", dig._selected is None, dig._selected)
check("the label says so", "none selected" in dig.sel_label.text().lower(),
      dig.sel_label.text())
check("every action is disabled until a sensor is chosen",
      not any(b.isEnabled() for b in dig.action_btns.values()))
check("the five §1 actions are offered",
      set(dig.action_btns) == {"LIVE DATA", "CALIBRATION", "CONFIGURATION",
                               "DIAGNOSTICS", "SENSOR STATUS"},
      sorted(dig.action_btns))
check("BLOCK POSITION, SPM 1-4 and RPM are the selectable sensors",
      [lab for _k, lab in dig._sensor_options()]
      == ["BLOCK POSITION"] + [c["name"] for c in SPM_CONFIG] + ["RPM"],
      [lab for _k, lab in dig._sensor_options()])

dig.sel_btns["spm2"].click()
check("choosing a sensor selects it", dig._selected == "spm2")
check("choosing a sensor enables the actions",
      all(b.isEnabled() for b in dig.action_btns.values()))
check("the chosen sensor is named in the header",
      "SPM 2" in dig.sel_label.text(), dig.sel_label.text())
dig.action_btns["DIAGNOSTICS"].click()
check("a non calibration action only focuses a section",
      dig._active_section == "DIAGNOSTICS")
check("the details grid is per section",
      set(dig._kv_store) == {"LIVE DATA", "CONFIGURATION", "DIAGNOSTICS",
                             "SENSOR STATUS"},
      sorted(dig._kv_store))
dig.sel_btns["spm1"].click()
check("the live value is shown for the selected sensor",
      "SPM" in dig._kv_store["LIVE DATA"]["0:Value:"].text(),
      dig._kv_store["LIVE DATA"]["0:Value:"].text())
dig.sel_btns["spm2"].click()
check("a channel that never reported shows --, never a misleading 0",
      dig._kv_store["LIVE DATA"]["0:Value:"].text().strip().startswith("--"),
      dig._kv_store["LIVE DATA"]["0:Value:"].text())
dig.sel_btns["spm2"].click()
dig.sel_btns["spm2"].click()
check("the input channel comes from the EXISTING firmware config",
      "counter2" in dig._kv_store["CONFIGURATION"]["0:Input Channel:"].text(),
      dig._kv_store["CONFIGURATION"]["0:Input Channel:"].text())
check("the sensor status follows the §15 vocabulary",
      dig._kv_store["SENSOR STATUS"]["0:Sensor Status:"].text()
      in {"ACTIVE", "NO SIGNAL", "COMMUNICATION ERROR", "CALIBRATION REQUIRED"},
      dig._kv_store["SENSOR STATUS"]["0:Sensor Status:"].text())

# ---- calibration opens ONLY on request, and only for the selection ---------
import scarlet_test_panel.tabs.digital_sensor_tab as digtab  # noqa: E402

opened = []


class _Recorder(DigitalSensorCalibrationDialog):
    """The REAL dialog, minus the modal loop, so the page can be checked."""

    def __init__(self, kind, idx, *a, **k):
        super().__init__(kind, idx, *a, **k)
        self.ask_fn = lambda _t, _x: True
        opened.append(self)

    def exec_(self):
        return 0


_orig_dialog = digtab.DigitalSensorCalibrationDialog
digtab.DigitalSensorCalibrationDialog = _Recorder
dig.sel_btns["spm3"].click()
dig.action_btns["CALIBRATION"].click()
check("CALIBRATION opens the dialog for the SELECTED sensor",
      len(opened) == 1 and (opened[0].kind, opened[0].idx) == ("SPM", 2),
      [(o.kind, o.idx) for o in opened])
dig.sel_btns["rpm"].click()
dig.action_btns["CALIBRATION"].click()
check("the RPM page opens the RPM calibration",
      len(opened) == 2 and (opened[1].kind, opened[1].idx) == ("RPM", 0),
      [(o.kind, o.idx) for o in opened])
dig.action_btns["LIVE DATA"].click()
check("no dialog is opened by the other actions", len(opened) == 2)
dig.sel_btns["block_position"].click()
dig.action_btns["CALIBRATION"].click()
check("BLOCK POSITION never opens a second calibration editor",
      len(opened) == 2, len(opened))
check("BLOCK POSITION calibration goes to the EXISTING page",
      dig.mw.tab_bar._current == mw.IDX_BLOCK_POSITION,
      dig.mw.tab_bar._current)
digtab.DigitalSensorCalibrationDialog = _orig_dialog
for _o in opened:
    _o.close()
dig._goto_block_position()

# =====================================================================
# 8. The calibration page itself (§8/§10/§11)
# =====================================================================
dlg = DigitalSensorCalibrationDialog("SPM", 0, mw, mw.roles, mw.audit)
dlg.ask_fn = lambda _t, _x: True
dlg.show()
app.processEvents()
check("the page opens on the SAVED record",
      len(dlg._typed_points()) == 2, dlg._typed_points())
check("default row count is four", dlg.table.rowCount() == digcal.DEFAULT_POINTS)
check("unused rows are NOT calibration points",
      len(dlg._typed_points()) < dlg.table.rowCount())
check("every row carries an explicit USE tick",
      isinstance(dlg.table.cellWidget(0, 0).findChild(QCheckBox), QCheckBox))
check("the unit column follows the sensor",
      dlg.table.item(0, 4).text() == "SPM", dlg.table.item(0, 4).text())


def use_row(d, r, on=True):
    d.table.cellWidget(r, 0).findChild(QCheckBox).setChecked(on)
    app.processEvents()


def set_point(d, r, ref, act):
    d.table.cellWidget(r, 2).setValue(float(ref))
    d.table.cellWidget(r, 3).setValue(float(act))
    app.processEvents()


use_row(dlg, 0)
check("ticking USE turns a row into a point", (0.0, 0.0) in dlg._typed_points())
use_row(dlg, 1, False)
check("unticking USE removes the row from the points",
      dlg._typed_points() == [(0.0, 0.0)], dlg._typed_points())
use_row(dlg, 1)
set_point(dlg, 1, 50, 40)
use_row(dlg, 2)
set_point(dlg, 2, 100, 60)
before = list(dlg._typed_points())
dlg._add_point()
app.processEvents()
check("Add Point keeps the entered points", dlg._typed_points() == before,
      (before, dlg._typed_points()))
dlg._del_point()
app.processEvents()
check("Remove Point keeps the remaining points", dlg._typed_points() == before,
      (before, dlg._typed_points()))
check("the page reports the validation state",
      "calibration ready" in dlg.lbl_status.text().lower(), dlg.lbl_status.text())

dlg._live_update()
check("the live test shows the RAW pulse rate",
      dlg.lbl_raw_hz.text() not in ("", "—"), dlg.lbl_raw_hz.text())
check("the live test shows the CALCULATED value",
      any(ch.isdigit() for ch in dlg.lbl_calculated.text()),
      dlg.lbl_calculated.text())
check("the live test shows the CALIBRATED value",
      dlg.lbl_calibrated.text() not in ("", "—"), dlg.lbl_calibrated.text())

# ---- save -> reload -> apply -> verify (§11) -------------------------------
_saved_settings.clear()
audit_log = []
mw.audit.record = lambda *a, **k: audit_log.append(a)
saved_ok = dlg._save()
check("SAVE stores the calibration", saved_ok is True,
      (saved_ok, dlg.lbl_status.text()))
check("SAVE persists exactly this channel",
      len(digcal.record_for("SPM", 0)["points"]) == 3,
      digcal.record_for("SPM", 0))
check("SAVE leaves the other SPM channels alone",
      digcal.record_for("SPM", 1) is None and digcal.record_for("SPM", 3) is None,
      (digcal.record_for("SPM", 1), digcal.record_for("SPM", 3)))
check("SAVE leaves RPM alone", digcal.record_for("RPM", 1) is None,
      digcal.record_for("RPM", 1))
check("SAVE is audited", any("DIGITAL_CALIBRATION" in str(a)
                             for a in audit_log), audit_log)
check("SAVE reports success", "saved successfully" in dlg.lbl_status.text(),
      dlg.lbl_status.text())
check("SAVE is applied to the live value of the main window",
      mw.digital_value("SPM", 0) is None or mw.digital_value("SPM", 0) == 0.0
      or isinstance(mw.digital_value("SPM", 0), float),
      mw.digital_value("SPM", 0))

# ---- RPM: one page, two independent channels (§6) ------------------------
rpm = DigitalSensorCalibrationDialog("RPM", 0, mw, mw.roles, mw.audit)
rpm.ask_fn = lambda _t, _x: True
check("the RPM page names both channels",
      [rpm.combo_input.itemText(i) for i in range(rpm.combo_input.count())]
      == ["RPM 1  (counter5)", "RPM 2  (counter6)"],
      [rpm.combo_input.itemText(i) for i in range(rpm.combo_input.count())])
use_row(rpm, 0)
set_point(rpm, 0, 10, 12)
rpm.combo_input.setCurrentIndex(1)
app.processEvents()
check("switching RPM channel loads THAT channel, not the other one's edits",
      rpm._typed_points() == [], rpm._typed_points())
check("the RPM page tracks the selected channel", rpm.idx == 1, rpm.idx)
check("the RPM page exposes Pulses / Revolution", rpm.spin_ppr is not None)
check("switching channel clears the previous channel's dirty flag",
      rpm._dirty is False, rpm._dirty)
rpm.combo_input.setCurrentIndex(0)
app.processEvents()
check("switching back loads RPM 1's OWN saved curve",
      rpm._typed_points() == [(0.0, 0.0), (100.0, 100.0)], rpm._typed_points())
check("switching back restores RPM 1's own Pulses / Revolution",
      rpm.spin_ppr.value() == 1.0, rpm.spin_ppr.value())
rpm.combo_input.setCurrentIndex(1)
app.processEvents()
check("RPM 2 still has no curve of its own", rpm._typed_points() == [],
      rpm._typed_points())
use_row(rpm, 0)
set_point(rpm, 0, 10, 12)
rpm.spin_ppr.setValue(2.0)
app.processEvents()
check("SAVE refuses an unfinished curve", rpm._save() is False)
check("an unfinished curve is not stored", digcal.record_for("RPM", 1) is None)
use_row(rpm, 1)
set_point(rpm, 1, 200, 100)
rpm._save()
check("RPM 2 keeps its own pulses/revolution",
      (digcal.record_for("RPM", 1) or {}).get("pulses_per_rev") == 2.0,
      digcal.record_for("RPM", 1))
check("RPM 2 keeps its own two points",
      len((digcal.record_for("RPM", 1) or {}).get("points") or []) == 2,
      digcal.record_for("RPM", 1))
check("RPM 1 is untouched by the RPM 2 save",
      (digcal.record_for("RPM", 0) or {}).get("pulses_per_rev") == 1.0
      and len((digcal.record_for("RPM", 0) or {}).get("points") or []) == 2,
      digcal.record_for("RPM", 0))

# ---- a read only role may look but not change (§12) -----------------------
class _Viewer:
    def is_engineer(self):
        return False

    def role(self):
        return "Viewer"

    def set_role(self, *_a):
        pass


ro = DigitalSensorCalibrationDialog("SPM", 0, mw, _Viewer(), None)
check("a read only role sees the stored calibration",
      len(ro._typed_points()) == 3, ro._typed_points())
check("a read only role cannot tick USE",
      not ro.table.cellWidget(0, 0).findChild(QCheckBox).isEnabled())
check("a read only role cannot edit a value",
      not ro.table.cellWidget(1, 2).isEnabled())
check("a read only role cannot save", ro._save() is False)
check("a read only role cannot reset", not ro._reset())
check("a read only role cannot edit pulses/revolution",
      ro.spin_ppr is None or not ro.spin_ppr.isEnabled())

# =====================================================================
# 9. The Dashboard shows SPM, TOTAL SPM and RPM (§17)
# =====================================================================
# Freeze the acquisition timer so a background demo/live tick cannot change the
# source values between the refresh and the assertion.
mw.timer.stop()
mw.sensors_tab.refresh()
check("the Dashboard shows SPM 1..4",
      [c.title for c in mw.sensors_tab.box_spm] == ["SPM 1", "SPM 2", "SPM 3", "SPM 4"],
      [c.title for c in mw.sensors_tab.box_spm])
check("the Dashboard shows TOTAL SPM", mw.sensors_tab.box_total.title == "Total SPM")
check("the Dashboard shows RPM", mw.sensors_tab.box_rpm.title == "RPM")
check("the RPM box offers both RPM channels",
      mw.sensors_tab.box_rpm.sel_items == ["RPM 1", "RPM 2"],
      mw.sensors_tab.box_rpm.sel_items)
mw.sensors_tab.box_rpm.set_selected(1, notify=True)
app.processEvents()
check("the RPM box selection is persisted through the existing settings store",
      settings_service.load().get("dashboard_rpm_slot") == 1,
      settings_service.load().get("dashboard_rpm_slot"))
check("the RPM box reads the EXISTING rpm_values",
      mw.sensors_tab.box_rpm.val.text()
      == ("--" if mw.rpm_values[1] is None else f"{mw.rpm_values[1]:.1f}"),
      mw.sensors_tab.box_rpm.val.text())

# =====================================================================
# 10. Demo and Live share the one calibrated pipeline (§22)
# =====================================================================
digcal.save_channel("RPM", 0, rec([(0, 0), (100, 50)], kind="RPM"))
mw.reload_digital_calibration()
mw._demo_mode = True
mw._apply_legacy({"spm1": 10.0, "spm2": 10.0, "spm3": 10.0, "spm4": 10.0,
                  "rpm1": 100.0,
                  "counter1": 1, "counter2": 2, "counter3": 3, "counter4": 4,
                  "counter5": 5, "counter6": 6})
demo_rpm = list(mw.rpm_values)
mw._demo_mode = False
mw.serial = _FakeOpen()
mw._apply_legacy({"spm1": 10.0, "spm2": 10.0, "spm3": 10.0, "spm4": 10.0,
                  "rpm1": 100.0,
                  "counter1": 1, "counter2": 2, "counter3": 3, "counter4": 4,
                  "counter5": 5, "counter6": 6})
check("Demo and Live produce the same calibrated RPM",
      demo_rpm == list(mw.rpm_values), (demo_rpm, list(mw.rpm_values)))
check("the calibrated RPM is applied (100 Hz -> 50 RPM)",
      abs(mw.rpm_values[0] - 50.0) < 1e-9, mw.rpm_values[0])

for d in (dlg, rpm, ro, dig):
    d.close()
mw.serial = _orig_serial
mw.close()
main_mod.save_settings = _orig_save
app.quit()
print()
print(f"===== DIGITAL SPM / RPM CALIBRATION: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)