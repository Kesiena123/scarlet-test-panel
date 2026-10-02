"""RESET FEET — new starting-feet-position behavior (headless/offscreen).

Verifies the modified RESET FEET feature:
  * the reset_feet command carries `value` (start_ft) and expects that reference
  * on_reset_feet_ack accepts the new (non-zero) reference
  * CANCEL (dialog returns None) changes nothing
  * CONFIRM with a start value sets the block position to that value (local)
  * the runtime reference makes position track from the new starting feet
  * calibration table is left byte-for-byte unchanged
  * the success banner shows the new starting position

Run:   python tests/test_reset_feet.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  FAIL: {name} {detail}")


def snapshot_cal(mw):
    return {k: mw.device.get(k) for k in
            ("calCounter1", "calPosition1", "calCounter2", "calPosition2",
             "calCounter3", "calPosition3", "calCounter4", "calPosition4")}


# ---- Command serialization: reset_feet carries start_ft -----------------
mw0 = PumpDashboard()
sent = []
monkey = lambda line: (sent.append(line), True)[1]
orig_write = mw0._cmd._write
mw0._cmd._write = monkey
rid = mw0._cmd.reset_feet(start_ft=125.5)
check("reset_feet sent with value field", any('"value": 125.5' in ln for ln in sent),
      sent)
check("reset_feet sent cmd name", any('"cmd": "reset_feet"' in ln for ln in sent),
      sent)
if rid is not None:
    req = mw0._cmd._pending.get(rid)
    check("expected references currentTicks=0",
          req is not None and req.expected.get("currentTicks") == 0)
    check("expected references blockPositionFt=125.5",
          req is not None and abs(float(req.expected.get("blockPositionFt", 0)) - 125.5) < 1e-9)

    # Feed a confirming ack and check it verifies + calls on_done.
    done_called = []
    req.on_done = lambda r, d: done_called.append(True)
    res = mw0._cmd.on_reset_feet_ack(
        {"req_id": rid, "currentTicks": 0, "blockPositionFt": 125.5})
    check("non-zero start ack verifies", res == "verified", res)
    check("on_done fired", bool(done_called))

    # A wrong position must fail.
    rid2 = mw0._cmd.reset_feet(start_ft=125.5)
    res2 = mw0._cmd.on_reset_feet_ack(
        {"req_id": rid2, "currentTicks": 0, "blockPositionFt": 999.0})
    check("mismatched start ack fails", res2 == "failed", res2)
mw0._cmd._write = orig_write
mw0.close()

# ---- Local (no device) RESET FEET ---------------------------------------
mw = PumpDashboard()
tab = mw.block_tab
mw.roles.set_role(ROLE_ENGINEER)

# Seed a calibration so a position base exists.
mw.device.update({"calCounter1": 0, "calPosition1": 0.0,
                  "calCounter2": 5000, "calPosition2": 50.0,
                  "calCounter3": 10000, "calPosition3": 100.0,
                  "confirmed": True})
mw.current_ticks = 5000

# CANCEL -> returns None -> nothing changes.
before = snapshot_cal(mw)
before_pos = mw.block_position_ft
tab._prompt_starting_ft = lambda: None
tab._on_reset_feet()
check("CANCEL leaves position unchanged",
      mw.block_position_ft == before_pos, mw.block_position_ft)
check("CANCEL leaves calibration unchanged", snapshot_cal(mw) == before)

# CONFIRM 125.5 -> block position becomes 125.5, calibration untouched.
before = snapshot_cal(mw)
tab._prompt_starting_ft = lambda: 125.5
tab._on_reset_feet()
check("position set to 125.5",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)
check("calibration unchanged after confirm", snapshot_cal(mw) == before)
check("success banner shows new starting position",
      "New Starting Position: 125.50 FT" in tab.banner_lbl.text(),
      tab.banner_lbl.text())
check("op recorded ok",
      bool(tab._ops.get("reset_feet", {}).get("ok")))

# RESET FEET resume (regression for "reset feet freezes the stick at the
# datum"): after a local RESET FEET the block must READ the entered value at
# the reset instant and then CONTINUE moving along the calibration — never sit
# frozen at the datum across subsequent demo ticks.
mw.roles.set_role(ROLE_ENGINEER)
tab._prompt_starting_ft = lambda: 125.5
tab._on_reset_feet()
check("value reads 125.5 immediately after reset",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)
check("velocity zero at the reset instant",
      abs(mw.velocity_ft_min) < 1e-6, mw.velocity_ft_min)
prev = mw.block_position_ft
moved = False
min_ok = True
for _ in range(8):
    mw._demo_tick()
    now = mw.block_position_ft
    if abs(now - prev) >= 1e-3:
        moved = True
    if now < -1e-9:
        min_ok = False
    prev = now
check("block CONTINUES moving after reset (not frozen at the datum)",
      moved, prev)
check("resumed sweep never dips below zero", min_ok, prev)

# CANCEL must NOT change anything (no reference touched, no freeze).
mw.roles.set_role(ROLE_ENGINEER)
mw._set_block_position(0.0)
tab._prompt_starting_ft = lambda: None
tab._on_reset_feet()
check("CANCEL leaves position unchanged",
      abs(mw.block_position_ft - 0.0) < 1e-9, mw.block_position_ft)

# Runtime reference: from 125.5, +10 ft movement -> 135.5.
mw.set_runtime_reference(125.5 - 0.0, 125.5)  # simulate recent confirm base
pos_base = mw.calibrated_position(5000)  # absolute at current tick
mw.set_runtime_reference(pos_base, 125.5)
# Move the demo-sweep absolute position up by 10 ft.
abs_up = pos_base + 10.0
check("tracks +10 ft from start -> 135.5",
      abs(mw.display_position_from_calibration(abs_up) - 135.5) < 1e-3,
      mw.display_position_from_calibration(abs_up))
abs_dn = pos_base - 5.0
check("tracks -5 ft from start -> 120.5",
      abs(mw.display_position_from_calibration(abs_dn) - 120.5) < 1e-3,
      mw.display_position_from_calibration(abs_dn))

mw.close()
app.quit()
print()
print(f"===== RESET FEET: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)
