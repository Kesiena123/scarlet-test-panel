"""HOOKLOAD-CONDITIONED BIT POSITION + STRING LENGTH (promt1.txt + promt2.txt)
— acceptance suite.

Pipe load is proven by the SINGLE Analog Monitor channel-0 Hookload
(mw.hookload_klb: V/5 * 1000 klb). With the Slip Window unconfigured the gate
is: hookload > 0 -> TRACK, hookload == 0 -> HOLD (bit frozen).

Verifies:
  CASE 1: no pipe load (channel-0 hookload 0) -> bit HOLDS and does not change
          even if the block position moves; the tracked string length follows
          the block (bit frozen).
  CASE 2: pipe load present (hookload > 0) -> bit moves OPPOSITE to the block
          1:1, while String Length = Block + Bit stays constant for the trip
          (promt2.txt §4/§5/acceptance rule).
  CASE 3: no-pipe -> pipe transition initializes from live values with no
          stale-hold jump for BOTH the bit and the string length.
  HYSTERESIS: brief hookload churn around the 0 threshold cannot flip the state
          back and forth (only a persistent change applied).
  SOURCES: bit logic reads the existing channel-0 Hookload / Block Position
          values — no second hookload or string-length calculation; bit and
          string length are never negative.

Run:   python tests/test_bit_position_state.py
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
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402

_failures = 0
_total = 0


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


def load(mw, klb):
    """Drive the live channel-0 hookload (klb = V/5 * 1000)."""
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call: the
    SENSOR_CONFIG[0]["cal_max"] = 1000.0  # dashboard re-applies operator
    mw.analog_voltages[0] = klb / 200.0   # calibration on construction.
    # 40 klb == 0.20 V


mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab

if mw.pipe_in_hole_ft is not None:
    mw.pipe_in_hole_ft = None          # start from "not configured"
    mw._bit_hold_ft = None
mw._bit_state = "NONE"
check("default (no config) state is NONE",
      mw.bit_position_state == "NONE", mw.bit_position_state)
check("no config -> no bit depth",
      mw.bit_position_ft is None and mw.string_length_ft is None,
      mw.bit_position_ft)

# Rig setup shared by every case: configured string + saved references.
mw.set_pipe_in_hole(6000.0)            # entered flat-string bit depth
mw._set_block_position(200.0)
load(mw, 0.0)
cal_before = snapshot_cal(mw)

check("idle bit = entered flat-string depth 6000.00 before any trip",
      mw.bit_position_ft == 6000.0, mw.bit_position_ft)

# ---- CASE 2: pipe load present -> track inversely, SL constant -------------
load(mw, 40.0)                       # 40 klb > 0 -> TRACK
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("CASE2 state = TRACK under pipe load",
      mw.bit_position_state == "TRACK", mw.bit_position_state)
check("CASE2 bit anchored to current depth (6000.00), no jump",
      mw.bit_position_ft == 6000.0, mw.bit_position_ft)
check("CASE2 string length = block + bit = 6200.00 (constant)",
      mw.string_length_ft == 6200.0, mw.string_length_ft)
check("CASE2 string length card shows 6200.00 FT",
      tab.string_length_lbl.text() == "6200.00 FT",
      tab.string_length_lbl.text())
mw._set_block_position(201.0)
tab.refresh()
check("CASE2 block up 1 -> bit down 1 (5999.00)",
      mw.bit_position_ft == 5999.0, mw.bit_position_ft)
check("CASE2 string length still 6200.00 while tracking",
      mw.string_length_ft == 6200.0, mw.string_length_ft)
mw._set_block_position(199.0)
tab.refresh()
check("CASE2 block down 1 -> bit up 1 (6001.00)",
      mw.bit_position_ft == 6001.0, mw.bit_position_ft)
check("CASE2 string length still 6200.00 while tracking",
      mw.string_length_ft == 6200.0, mw.string_length_ft)

# ---- CASE 1: no pipe load -> bit HOLDS, SL follows the block ---------------
load(mw, 0.0)                        # no load -> HOLD
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("CASE1 state = HOLD after unload",
      mw.bit_position_state == "HOLD", mw.bit_position_state)
mw._set_block_position(205.0)
tab.refresh()
check("CASE1 HOLD: bit frozen at last valid (6001.00) while block moves",
      mw.bit_position_ft == 6001.0, mw.bit_position_ft)
check("CASE1 HOLD: string length follows the block (205 + 6001 = 6206.00)",
      mw.string_length_ft == 6206.0, mw.string_length_ft)
mw._set_block_position(210.0)
tab.refresh()
check("CASE1 HOLD bit still frozen (6001.00)",
      mw.bit_position_ft == 6001.0, mw.bit_position_ft)
check("CASE1 HOLD string length follows the block (210 + 6001 = 6211.00)",
      mw.string_length_ft == 6211.0, mw.string_length_ft)

# ---- CASE 3: no-pipe -> pipe transition, no jump on bit OR string length ----
load(mw, 90.0)                       # > 0 -> TRACK again
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("CASE3 state = TRACK again", mw.bit_position_state == "TRACK",
      mw.bit_position_state)
check("CASE3 transition initializes from live depth 6001.00 (no stale-hold jump)",
      mw.bit_position_ft == 6001.0, mw.bit_position_ft)
check("CASE3 string length re-anchored without a jump (6211.00)",
      mw.string_length_ft == 6211.0, mw.string_length_ft)
mw._set_block_position(211.0)
tab.refresh()
check("CASE3 continues inverse tracking (6000.00)",
      mw.bit_position_ft == 6000.0, mw.bit_position_ft)
check("CASE3 string length constant 6211.00 across the trip",
      mw.string_length_ft == 6211.0, mw.string_length_ft)

# ---- HYSTERESIS: threshold churn must not flip the state --------------------
load(mw, 0.0)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("HYST settled to HOLD", mw.bit_position_state == "HOLD",
      mw.bit_position_state)
for _ in range(20):
    load(mw, 0.0)
    tab.refresh()
    load(mw, 40.0)
    tab.refresh()
check("HYST churn around threshold keeps HOLD (no flip)",
      mw.bit_position_state == "HOLD", mw.bit_position_state)
check("HYST bit stays frozen during churn (6000.00)",
      mw.bit_position_ft == 6000.0, mw.bit_position_ft)
load(mw, 80.0)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("HYST steady high -> TRACK", mw.bit_position_state == "TRACK",
      mw.bit_position_state)
for _ in range(20):
    load(mw, 0.0)
    tab.refresh()
    load(mw, 80.0)
    tab.refresh()
check("HYST brief dips down to 0 keep TRACK",
      mw.bit_position_state == "TRACK", mw.bit_position_state)
load(mw, 0.0)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("HYST sustained unload -> HOLD",
      mw.bit_position_state == "HOLD", mw.bit_position_state)

# ---- SOURCES / no duplicate cal & existing values intact ------------------
check("string length = block + bit invariant (211 + 6000 = 6211.00)",
      mw.string_length_ft == 6000.0 + 211.0, mw.string_length_ft)
check("string length card shows tracked value 6211.00 FT",
      tab.string_length_lbl.text() == "6211.00 FT",
      tab.string_length_lbl.text())
check("bit logic leaves the analog hookload channel alone",
      mw.analog_voltages[0] == 0.0 and mw.hookload_klb == 0.0,
      (mw.analog_voltages[0], mw.hookload_klb))
check("calibration snapshot untouched",
      cal_before == snapshot_cal(mw))
check("bit screen shows HOLD state on the sub line",
      "HOLD" in tab.bit_screen_sub_lbl.text(), tab.bit_screen_sub_lbl.text())
bit = mw.bit_position_ft
sl = mw.string_length_ft
check("bit never negative", bit is not None and bit >= 0.0, bit)
check("string length never negative", sl is not None and sl >= 0.0, sl)

# ---- never-jump under deep block excursion (bit clamps at 0, no negative) --
load(mw, 80.0)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
mw._set_block_position(6400.0)         # over the string reference (6211)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()
check("extreme block above anchor -> bit clamps to 0.00 (no negative)",
      mw.bit_position_ft == 0.0, mw.bit_position_ft)
check("extra string length stays non-negative (6400.00)",
      mw.string_length_ft == 6400.0, mw.string_length_ft)
load(mw, 0.0)
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    tab.refresh()

# ---- READ-ONLY / RBAC: operator still view-only ----------------------------
from scarlet_test_panel.services.security import ROLE_OPERATOR  # noqa: E402
mw.roles.set_role(ROLE_OPERATOR)
tab.refresh()
check("RBAC operator: bit screen still renders", mw.bit_position_state in
      ("NONE", "HOLD", "TRACK"), mw.bit_position_state)
mw.roles.set_role(ROLE_ENGINEER)

mw.close()
app.quit()
print()
print(f"===== BIT POSITION STATE: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)