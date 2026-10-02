"""FT/M UNIT TOGGLE — BLOCK POSITION card display-only conversion (promt1.txt).

Verifies:
  TEST 1: internal 100 FT + FT mode -> "100.00 FT"
  TEST 2: toggle -> "30.48 M"
  TEST 3: toggle back -> "100.00 FT" (no recovered/stale meters conversion)
  TEST 4: live position change updates in the selected unit (FT and M)
  TEST 5: switching to M mid-live keeps updating in M
  TEST 6: RESET FEET 125.50 FT -> "125.50 FT" (FT) / "38.25 M" (M)
  TEST 7: encoder ticks / calibration / current layer unchanged by the toggle

Internal feet value (block_position_ft) must never be altered by the toggle.

Run:   python tests/test_unit_toggle.py
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
from scarlet_test_panel.config import SENSOR_CONFIG  # noqa: E402
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402

_failures = 0
_total = 0

# Hermetic hookload: pin channel-0 to the factory 0..1000 klb range.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")


def snapshot_cal(mw):
    return {k: mw.device.get(k) for k in
            ("calCounter1", "calPosition1", "calCounter2", "calPosition2",
             "calCounter3", "calPosition3", "calCounter4", "calPosition4")}


FT_TO_M = 0.3048

mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
# Pin channel-0 to the factory 0..1000 klb range (dashboard re-applied any
# operator calibration on construction), so the 0.0/0.25/0.45 V loads below
# yield 0/50/90 klb exactly.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0

# Seed calibration (values must remain byte-identical through the toggle).
mw.device.update({"calCounter1": 0, "calPosition1": 0.0,
                  "calCounter2": 5000, "calPosition2": 50.0,
                  "calCounter3": 10000, "calPosition3": 100.0,
                  "calCounter4": 15000, "calPosition4": 150.0,
                  "confirmed": True})
cal_before = snapshot_cal(mw)

# TEST 1 — 100 FT in FT mode.
mw._set_block_position(100.0)
tab._position_unit = "FT"
tab.refresh()
check("TEST1 100 FT -> '100.00 FT'",
      tab.pos_value_lbl.text() == "100.00 FT", tab.pos_value_lbl.text())
check("TEST1 internal feet kept", abs(mw.block_position_ft - 100.0) < 1e-9)

# TEST 2 — toggle to M.
tick_before = mw.current_ticks
tab._on_unit_toggle()
check("TEST2 toggles unit to M", tab._position_unit == "M")
check("TEST2 100 FT -> '30.48 M'",
      tab.pos_value_lbl.text() == "30.48 M", tab.pos_value_lbl.text())
check("TEST2 internal feet unchanged after toggle",
      abs(mw.block_position_ft - 100.0) < 1e-9)
check("TEST2 ticks unchanged by toggle",
      mw.current_ticks == tick_before)
check("TEST2 calibration unchanged by toggle", cal_before == snapshot_cal(mw))

# TEST 3 — toggle back to FT.
tab._on_unit_toggle()
check("TEST3 toggles back to FT", tab._position_unit == "FT")
check("TEST3 back -> '100.00 FT' (no stale meters)",
      tab.pos_value_lbl.text() == "100.00 FT", tab.pos_value_lbl.text())
check("TEST3 internal feet exact 100.0",
      abs(mw.block_position_ft - 100.0) < 1e-9)

# TEST 4/5 — live updates in the selected unit.
mw._set_block_position(110.0)
tab._position_unit = "FT"
tab.refresh()
check("TEST4 live 110 FT -> '110.00 FT'",
      tab.pos_value_lbl.text() == "110.00 FT", tab.pos_value_lbl.text())
tab._position_unit = "M"
tab.refresh()
check("TEST5 live 110 FT in M -> '33.53 M'",
      tab.pos_value_lbl.text() == "33.53 M", tab.pos_value_lbl.text())
mw._set_block_position(120.0)
tab.refresh()
check("TEST5 live 120 FT in M -> '36.58 M'",
      tab.pos_value_lbl.text() == "36.58 M", tab.pos_value_lbl.text())

# TEST 6 — RESET FEET 125.50 FT integrated (local/demo path).
tab._position_unit = "FT"
tab._prompt_starting_ft = lambda: 125.5
tab._on_reset_feet()
check("TEST6 RESET FEET FT displays '125.50 FT'",
      tab.pos_value_lbl.text() == "125.50 FT", tab.pos_value_lbl.text())
check("TEST6 internal feet 125.5",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)
tab._position_unit = "M"
tab.refresh()
check("TEST6 RESET FEET M displays '38.25 M'",
      tab.pos_value_lbl.text() == "38.25 M", tab.pos_value_lbl.text())
tab._position_unit = "FT"
tab.refresh()
check("TEST6 back to FT still '125.50 FT'",
      tab.pos_value_lbl.text() == "125.50 FT", tab.pos_value_lbl.text())

# TEST 7 — nothing else modified by the toggle.
check("TEST7 calibration unchanged at end", cal_before == snapshot_cal(mw))
check("TEST7 current_layer valid (non-negative int)",
      isinstance(mw.current_layer, int) and mw.current_layer >= 0, mw.current_layer)
check("TEST7 ticks untouched by toggle",
      mw.current_ticks == 0, mw.current_ticks)

# TEST 8 — BIT POSITION + STRING LENGTH screens (promt1.txt + promt2.txt).
# No pipe load (channel-0 hookload == 0) -> HOLD the last valid depth; pipe
# load present (hookload > 0, klb = V/5 * 1000) -> bit moves opposite to the
# block 1:1, string length constant for the trip. Re-entering TRACK re-anchors
# from the live values (no stale-hold jump).
if mw.pipe_in_hole_ft is not None:
    mw.pipe_in_hole_ft = None        # test starts from "not configured"
    mw._bit_hold_ft = None
mw.analog_voltages[0] = 0.0
tab.refresh()
check("TEST8 bit screen idle -- before pipe in hole is set",
      tab.bit_screen_lbl.text() == "--" and tab.bit_screen_sub_lbl.text() == "set pipe in hole",
      f"lbl={tab.bit_screen_lbl.text()!r} sub={tab.bit_screen_sub_lbl.text()!r}")
check("TEST8 bit screen caption FT", tab.bit_screen_cap.text() == "BIT POSITION (FT)",
      tab.bit_screen_cap.text())
mw.set_pipe_in_hole(5000.0)
tab._position_unit = "FT"
mw._set_block_position(125.5)
tab.refresh()
check("TEST8 no pipe load -> HOLD shows the entered pipe in hole 5000.00",
      tab.bit_screen_lbl.text().replace(",", "") == "5000.00"
      and "HOLD" in tab.bit_screen_sub_lbl.text(),
      f"lbl={tab.bit_screen_lbl.text()!r} sub={tab.bit_screen_sub_lbl.text()!r}")
mw.analog_voltages[0] = 0.25          # 50 klb -> TRACK (already loaded)
for _ in range(4):
    tab.refresh()
check("TEST8 pipe load -> bit anchored to current depth 5000.00 (no jump)",
      tab.bit_screen_lbl.text().replace(",", "") == "5000.00",
      tab.bit_screen_lbl.text())
check("TEST8 bit screen sub shows feet + TRACKING",
      "Block 125.5 ft" in tab.bit_screen_sub_lbl.text()
      and "Pipe in Hole 5000.0 ft" in tab.bit_screen_sub_lbl.text()
      and "TRACKING" in tab.bit_screen_sub_lbl.text(),
      tab.bit_screen_sub_lbl.text())
check("TEST8 string length constant for the trip (5125.50 FT)",
      tab.string_length_lbl.text().replace(",", "") == "5125.50 FT",
      tab.string_length_lbl.text())
mw._set_block_position(126.5)        # block up 1 -> bit down 1 (inverse)
tab.refresh()
check("TEST8 inverse: block up -> bit 4999.00",
      tab.bit_screen_lbl.text().replace(",", "") == "4999.00",
      tab.bit_screen_lbl.text())
tab._on_unit_toggle()                # M = 4999.00 * 0.3048 = 1523.70
check("TEST8 bit screen follows the M unit -> 1523.70",
      tab.bit_screen_lbl.text().replace(",", "") == "1523.70",
      tab.bit_screen_lbl.text())
check("TEST8 caption M", tab.bit_screen_cap.text() == "BIT POSITION (M)",
      tab.bit_screen_cap.text())
tab._on_unit_toggle()                # back to FT = 4999.00
check("TEST8 back to FT still 4999.00",
      tab.bit_screen_lbl.text().replace(",", "") == "4999.00",
      tab.bit_screen_lbl.text())
mw.analog_voltages[0] = 0.0           # remove pipe load -> HOLD freezes
for _ in range(4):
    tab.refresh()
mw._set_block_position(130.0)        # block moves while holding
tab.refresh()
check("TEST8 HOLD: bit frozen at last valid despite block move",
      tab.bit_screen_lbl.text().replace(",", "") == "4999.00",
      tab.bit_screen_lbl.text())
mw.analog_voltages[0] = 0.45          # re-enter TRACK at BP=130 -> no jump
for _ in range(4):
    tab.refresh()
check("TEST8 re-enter TRACK starts from the live depth 4999.00 (no stale jump)",
      tab.bit_screen_lbl.text().replace(",", "") == "4999.00",
      tab.bit_screen_lbl.text())
mw._set_block_position(131.0)        # block up -> bit down (4998.00)
tab.refresh()
check("TEST8 continues inverse tracking after re-anchor -> 4998.00",
      tab.bit_screen_lbl.text().replace(",", "") == "4998.00",
      tab.bit_screen_lbl.text())
check("TEST8 string length card = block + bit = 5129.00 FT",
      tab.string_length_lbl.text().replace(",", "") == "5129.00 FT",
      tab.string_length_lbl.text())
check("TEST8 bit logic does not modify calibration",
      cal_before == snapshot_cal(mw))

mw.close()
app.quit()
print()
print(f"===== UNIT TOGGLE: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)
