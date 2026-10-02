"""promt.txt — COMPLETE 100% ACCURACY acceptance suite.

Drives the REAL PumpDashboard (offscreen) through EVERY worked example and
every rule in promt.txt, section by section. Pipe load is proven by the single
Analog Monitor channel-0 Hookload (mw.hookload_klb); with the Slip Window
unconfigured the gate is hookload > 0 -> TRACK, hookload == 0 -> HOLD.

  §1   String Length = Bit + Block (all four examples).
  §3   Unloaded: bit frozen while block moves 0->20->50->100.
  §5   Loaded, block moving down: 100->80 -> bit 0->20; 80->50 -> bit 20->50.
  §6   Loaded, block moving up: 50->70 -> bit 50->30.
  §7-§10  Complete cycle table (0->100->0 x3).
  §11  Partial movement: 75.50->42.25, bit 124.75->158.00, SL 200.25.
  §12  Critical transition: unload/reload, bit 250->350, SL 400 constant.
  §14  No-pipe-load overrides block movement (any direction).
  §15  Pipe-load tracks actual movement (10/40/-30 changes in a row).
  §16  String Length = Bit + Block after EVERY valid update.
  §17  Full float precision (0.01-ft deltas, no rounding, no int conversion).
  §18  Edge cases: zero/very-small/large/direction-reversal/duplicate reads.
  §20  Acceptance Rules 1-12, asserted symbolically.
  §21  Pipe in Hole is LIVE: always equals the current Bit Position (TRACK and
      HOLD), never a static entered number.
  §22  String Length readout is EXPLICIT: TRACK flags "STRING LENGTH CONSTANT",
      HOLD flags "PIPE IN HOLE ... BIT FROZEN - SL FOLLOWS BLOCK".

Run:   python tests/test_promt_full_accuracy.py
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


def near(a, b, tol=1e-9):
    return a is not None and abs(float(a) - float(b)) <= tol


mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
# Pin channel-0 to the factory 0..1000 klb range: the dashboard re-applied any
# operator calibration on construction, so re-pin before driving volts.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0

V_OFF = 0.0        # unloaded: channel-0 hookload == 0 -> HOLD
V_LOAD = 0.20      # loaded: channel-0 hookload > 0 (40 klb) -> TRACK


def settle(ticks=None):
    for _ in range(ticks or BIT_POS_STATE_STABLE_TICKS):
        tab.refresh()


def unloaded():
    mw.analog_voltages[0] = V_OFF
    settle()


def loaded():
    mw.analog_voltages[0] = V_LOAD
    settle()


def drive(block_ft, want_bit, want_sl, label):
    """Seat the block, settle, then assert bit + SL + the §1/§16 invariant."""
    mw._set_block_position(block_ft)
    settle()
    bit = mw.bit_position_ft
    sl = mw.string_length_ft
    check(f"{label}: bit = {want_bit:g}", near(bit, want_bit), bit)
    check(f"{label}: string length = {want_sl:g}", near(sl, want_sl), sl)
    check(f"{label}: String = Bit + Block ({want_sl:g})",
          near(sl, float(bit) + float(block_ft)),
          f"sl={sl} bit={bit} block={block_ft}")


def rig(block_ft, pipe_in_hole, volts):
    """Re-seat the rig at a flat-string depth under a hookload state."""
    mw.set_pipe_in_hole(pipe_in_hole)
    mw._set_block_position(block_ft)
    mw.analog_voltages[0] = volts
    settle()


# ===========================================================================
# §1 FUNDAMENTAL CALCULATION — all four documented examples.
#   Example 1: bit 100 block 10   -> SL 110
#   Example 2: bit 100 block 0    -> SL 100
#   Example 3: bit 0   block 100  -> SL 100
#   Example 4: bit 250.50 block 25.25 -> SL 275.75
# ===========================================================================
print("SECTION 1 - String Length = Bit + Block (examples 1-4)")
rig(10.0, 100.0, V_OFF)
check("S1e1: bit = 100.00", near(mw.bit_position_ft, 100.0), mw.bit_position_ft)
check("S1e1: SL = 100 + 10 = 110.00", near(mw.string_length_ft, 110.0),
      mw.string_length_ft)
rig(0.0, 100.0, V_OFF)
check("S1e2: bit = 100.00", near(mw.bit_position_ft, 100.0), mw.bit_position_ft)
check("S1e2: SL = 100 + 0 = 100.00", near(mw.string_length_ft, 100.0),
      mw.string_length_ft)
rig(100.0, 0.0, V_OFF)
check("S1e3: bit = 0.00", near(mw.bit_position_ft, 0.0), mw.bit_position_ft)
check("S1e3: SL = 0 + 100 = 100.00", near(mw.string_length_ft, 100.0),
      mw.string_length_ft)
rig(25.25, 250.50, V_OFF)
check("S1e4: bit = 250.50", near(mw.bit_position_ft, 250.50), mw.bit_position_ft)
check("S1e4: SL = 250.50 + 25.25 = 275.75", near(mw.string_length_ft, 275.75),
      mw.string_length_ft)

# ===========================================================================
# §3 STATE 1 (hookload 0) — bit frozen while block moves 0->100.
# ===========================================================================
print("SECTION 3 - unloaded freeze (block 0 -> 20 -> 50 -> 100)")
rig(0.0, 0.0, V_OFF)
for bp in (20.0, 50.0, 100.0):
    drive(bp, 0.0, bp, f"S3 block {bp:g}")
check("S3 state is HOLD (unloaded)", mw.bit_position_state == "HOLD",
      mw.bit_position_state)

# ===========================================================================
# §5 STATE 2, block moving down — exact example numbers.
#   100->80 (delta -20) -> bit 0->20, SL 20+80=100
#    80->50 (delta -30) -> bit 20->50, SL 50+50=100
# ===========================================================================
print("SECTION 5 - loaded descent (100 -> 80 -> 50)")
rig(100.0, 0.0, V_LOAD)
check("S5 anchored bit 0.00 at block 100", near(mw.bit_position_ft, 0.0),
      mw.bit_position_ft)
drive(80.0, 20.0, 100.0, "S5 block 100->80")
drive(50.0, 50.0, 100.0, "S5 block 80->50")
check("S5 total string length stays 100.00", near(mw.string_length_ft, 100.0),
      mw.string_length_ft)

# ===========================================================================
# §6 block moving UP under load — 50->70 => bit 50->30, SL 100 constant.
# ===========================================================================
print("SECTION 6 - loaded ascent (50 -> 70)")
rig(50.0, 50.0, V_LOAD)
check("S6 anchored bit 50.00 at block 50", near(mw.bit_position_ft, 50.0),
      mw.bit_position_ft)
drive(70.0, 30.0, 100.0, "S6 block 50->70")
check("S6 bit decreased by exactly the block upward distance",
      near(mw.bit_position_ft, 30.0), mw.bit_position_ft)

# ===========================================================================
# §7-§10 COMPLETE CYCLE TABLE — accumulation continues forever.
# ===========================================================================
print("SECTION 7-10 - complete cycle table (0->100->0 x3)")
table = [
    # (block, volts, want_bit, want_sl, label)
    (0.0, V_OFF, 0.0, 0.0, "start"),
    (100.0, V_OFF, 0.0, 100.0, "rise unloaded"),
    (0.0, V_LOAD, 100.0, 100.0, "descend loaded (cycle 1)"),
    (100.0, V_OFF, 100.0, 200.0, "rise unloaded (cycle 2)"),
    (0.0, V_LOAD, 200.0, 200.0, "descend loaded (cycle 2)"),
    (100.0, V_OFF, 200.0, 300.0, "rise unloaded (cycle 3)"),
    (0.0, V_LOAD, 300.0, 300.0, "descend loaded (cycle 3)"),
]
rig(0.0, 0.0, V_OFF)
for bp, volts, wb, ws, label in table:
    mw.analog_voltages[0] = volts
    settle()
    drive(bp, wb, ws, f"S7-10 {label}")

# ===========================================================================
# §11 PARTIAL MOVEMENT — full floating-point precision.
#   prev block 75.50, prev bit 124.75, block -> 42.25:
#   delta -33.25 -> bit 158.00, SL 158.00 + 42.25 = 200.25.
# ===========================================================================
print("SECTION 11 - partial movement (75.50 -> 42.25)")
rig(75.50, 124.75, V_LOAD)
check("S11 anchored bit 124.75 at block 75.50", near(mw.bit_position_ft, 124.75),
      mw.bit_position_ft)
drive(42.25, 158.00, 200.25, "S11 partial descent")

# ===========================================================================
# §12 CRITICAL STATE TRANSITION — unload/reload never resets the bit.
#   bit 250 / block 0 loaded; unload; block -> 150 (bit 250, SL 400);
#   reload; block -> 50 (bit 350, SL 400).
# ===========================================================================
print("SECTION 12 - critical transition (250 -> 350, SL 400)")
rig(0.0, 250.0, V_LOAD)
check("S12 anchored bit 250.00 at block 0", near(mw.bit_position_ft, 250.0),
      mw.bit_position_ft)
mw.analog_voltages[0] = V_OFF
settle()                                  # unload while block sits at 0
mw._set_block_position(150.0)
settle()
check("S12 unloaded rise 0->150: bit frozen at 250.00",
      near(mw.bit_position_ft, 250.0), mw.bit_position_ft)
check("S12 string length = 250 + 150 = 400",
      near(mw.string_length_ft, 400.0), mw.string_length_ft)
check("S12 state is HOLD while unloaded", mw.bit_position_state == "HOLD",
      mw.bit_position_state)
mw.analog_voltages[0] = V_LOAD
settle()                                  # reload while block sits at 150
mw._set_block_position(50.0)
settle()
check("S12 reloaded descent 150->50: bit = 350.00",
      near(mw.bit_position_ft, 350.0), mw.bit_position_ft)
check("S12 string length stays 400", near(mw.string_length_ft, 400.0),
      mw.string_length_ft)
check("S12 bit never reset across the transition", near(mw.bit_position_ft, 350.0),
      mw.bit_position_ft)

# ===========================================================================
# §14 NO-PIPE-LOAD OVERRIDES block movement at ANY magnitude / direction.
# ===========================================================================
print("SECTION 14 - unloaded block movement never changes the bit")
rig(0.0, 0.0, V_OFF)
for bp in (10.0, 100.0, 50.0, 200.0, 0.0):
    mw._set_block_position(bp)
    settle()
    check(f"S14 block {bp:g}: bit unchanged", near(mw.bit_position_ft, 0.0),
          mw.bit_position_ft)
    check(f"S14 block {bp:g}: SL = bit + block",
          near(mw.string_length_ft, 0.0 + bp), mw.string_length_ft)

# ===========================================================================
# §15 PIPE-LOAD TRACKS ACTUAL MOVEMENT (no fixed increments).
#   100->90  (+10 load on bit), 90->50 (+40), 50->80 (-30).
# ===========================================================================
print("SECTION 15 - loaded movement tracks every delta")
rig(100.0, 0.0, V_LOAD)
prev_bit = mw.bit_position_ft
prev_block = 100.0
for bp in (90.0, 50.0, 80.0, 80.0):
    mw._set_block_position(bp)
    settle()
    delta = bp - prev_block
    bit = mw.bit_position_ft
    check(f"S15 block {bp:g}: bit change = -({delta:+g})",
          near(bit, prev_bit - delta), f"bit={bit} prev={prev_bit} delta={delta}")
    check(f"S15 block {bp:g}: SL stays constant",
          near(mw.string_length_ft, prev_bit + prev_block),
          mw.string_length_ft)
    prev_bit, prev_block = bit, bp

# ===========================================================================
# §16 CALCULATION INTEGRITY — String Length = Bit + Block after EVERY update.
# ===========================================================================
print("SECTION 16 - invariant held after every valid update")
rig(100.0, 0.0, V_LOAD)
invariant_holds = True
for i in range(50):
    bp = (i * 37) % 500           # large, alternating block moves under load
    mw._set_block_position(bp)
    settle()
    bit = mw.bit_position_ft
    sl = mw.string_length_ft
    if not (bit is not None and sl is not None
            and near(sl, float(bit) + float(bp))):
        invariant_holds = False
        print(f"    invariant broken at block {bp}: bit={bit} sl={sl}")
        break
check("S16 SL == Bit + Block across 50 loaded updates", invariant_holds)
rig(100.0, 0.0, V_OFF)
invariant_holds = True
for i in range(50):
    bp = (i * 37) % 500           # same sweep, unloaded
    mw._set_block_position(bp)
    settle()
    bit = mw.bit_position_ft
    sl = mw.string_length_ft
    if not (bit is not None and sl is not None
            and near(sl, float(bit) + float(bp))):
        invariant_holds = False
        break
check("S16 SL == Bit + Block across 50 unloaded updates", invariant_holds)

# ===========================================================================
# §17 PRECISION — 0.01-ft deltas preserved through full float precision.
# ===========================================================================
print("SECTION 17 - 0.01-ft precision")
rig(123.45, 50.0, V_LOAD)
drive(121.00, 52.45, 173.45, "S17 delta -2.45")
drive(121.01, 52.44, 173.45, "S17 delta +0.01")
check("S17 bit stays a float (no int rounding)",
      isinstance(mw.bit_position_ft, float), type(mw.bit_position_ft))

# ===========================================================================
# §18 EDGE CASES — zero / tiny / large / reversal / duplicate reads.
# ===========================================================================
print("SECTION 18 - edge cases")
rig(0.0, 10.0, V_LOAD)                     # bit 10 at block 0, under load
check("S18 both positions defined at zero",
      near(mw.bit_position_ft, 10.0) and near(mw.block_position_ft, 0.0),
      (mw.bit_position_ft, mw.block_position_ft))
mw._set_block_position(0.001)              # very small change
settle()
check("S18 tiny delta moves the bit by the same tiny amount",
      near(mw.bit_position_ft, 9.999), mw.bit_position_ft)
mw._set_block_position(6400.0)             # very large change
settle()
check("S18 large delta -> bit clamps at 0.00 (no negative)",
      near(mw.bit_position_ft, 0.0), mw.bit_position_ft)
mw._set_block_position(6300.0)             # reversal back down
settle()
check("S18 reversal restores the bit", near(mw.bit_position_ft, 100.0),
      mw.bit_position_ft)
before = mw.bit_position_ft
mw._set_block_position(6300.0)             # duplicate reading
settle()
check("S18 duplicate reading -> no movement", near(mw.bit_position_ft, before),
      (mw.bit_position_ft, before))

# ===========================================================================
# §20 ACCEPTANCE RULES 1-12, symbolic.
# ===========================================================================
print("SECTION 20 - acceptance rules 1-12")
# Rule 1/5/6: unloaded -> bit constant; block movement does not change it.
rig(100.0, 0.0, V_OFF)
held = mw.bit_position_ft
mw._set_block_position(50.0)
settle()
check("RULE 1/5/6: unloaded bit constant", mw.bit_position_state == "HOLD"
      and near(mw.bit_position_ft, held), (mw.bit_position_state, held,
                                           mw.bit_position_ft))
# Rule 2/3/4: loaded -> moves opposite by exactly the same distance.
rig(100.0, 0.0, V_LOAD)
mw._set_block_position(90.0); settle()      # block down 10 -> bit up 10
check("RULE 2/3: block down 10 -> bit up 10", near(mw.bit_position_ft, 10.0),
      mw.bit_position_ft)
mw._set_block_position(92.0); settle()      # block up 2 -> bit down 2
check("RULE 4: block up 2 -> bit down 2", near(mw.bit_position_ft, 8.0),
      mw.bit_position_ft)
# Rule 7: String Length = Bit + Block, always.
check("RULE 7: SL = Bit + Block", near(mw.string_length_ft,
                                       8.0 + 92.0), mw.string_length_ft)
# Rule 8/9/10/11: the exact narrative.
rig(100.0, 0.0, V_OFF)
check("RULE 8: unloaded Bit=0 Block=100 -> String=100",
      near(mw.bit_position_ft, 0.0) and near(mw.string_length_ft, 100.0),
      (mw.bit_position_ft, mw.string_length_ft))
mw.analog_voltages[0] = V_LOAD; settle()    # Rule 9: load at block 100
drive(0.0, 100.0, 100.0, "RULE 9: loaded 100->0")
mw.analog_voltages[0] = V_OFF; settle()     # Rule 10: unload at block 0
drive(100.0, 100.0, 200.0, "RULE 10: unloaded 0->100")
mw.analog_voltages[0] = V_LOAD; settle()    # Rule 11: load at block 100
drive(0.0, 200.0, 200.0, "RULE 11: loaded 100->0")
# Rule 12: accumulation continues indefinitely.
for i in range(3):
    mw.analog_voltages[0] = V_OFF; settle()
    drive(100.0, 200.0 + i * 100, 300.0 + i * 100, f"RULE 12 rise {i}")
    mw.analog_voltages[0] = V_LOAD; settle()
    drive(0.0, 300.0 + i * 100, 300.0 + i * 100, f"RULE 12 descend {i}")

# ===========================================================================
# §21 PIPE IN HOLE IS LIVE — Pipe in Hole always mirrors the current Bit
#   Position, never staying static at the entered seed value.
# ===========================================================================
print("SECTION 21 - Pipe in Hole tracks the live Bit Position")
rig(200.0, 5000.0, V_LOAD)
check("S21: pipe in hole == bit at seed", near(mw.pipe_in_hole_ft, mw.bit_position_ft),
      (mw.pipe_in_hole_ft, mw.bit_position_ft))
check("S21: spin box shows the seeded value",
      near(tab.pipe_in_hole_spin.value(), mw.pipe_in_hole_ft),
      tab.pipe_in_hole_spin.value())
mw._set_block_position(190.0); settle()     # loaded descent -> bit up 10
check("S21: bit advanced to 5010", near(mw.bit_position_ft, 5010.0), mw.bit_position_ft)
check("S21: pipe in hole auto-updated to bit",
      near(mw.pipe_in_hole_ft, mw.bit_position_ft),
      (mw.pipe_in_hole_ft, mw.bit_position_ft))
check("S21: spin box synced to live bit",
      near(tab.pipe_in_hole_spin.value(), mw.bit_position_ft),
      tab.pipe_in_hole_spin.value())
mw.analog_voltages[0] = V_OFF; settle()     # unload -> bit freezes in HOLD
mw._set_block_position(195.0); settle()
check("S21: bit frozen by HOLD", near(mw.bit_position_ft, 5010.0), mw.bit_position_ft)
check("S21: pipe in hole follows the frozen bit",
      near(mw.pipe_in_hole_ft, 5010.0), mw.pipe_in_hole_ft)

# ===========================================================================
# §22 STRING LENGTH READOUT IS EXPLICIT — the figure stays Bit + Block, and
#   the status line explains a flat number instead of it looking broken.
# ===========================================================================
print("SECTION 22 - String Length readout calls out TRACK/HOLD")
mw.analog_voltages[0] = V_LOAD; settle()    # re-engage TRACK, block stays 195
check("S22: TRACK flags String Length constant",
      "STRING LENGTH CONSTANT" in tab.sl_status_lbl.text()
      and "TRIPPING" in tab.sl_status_lbl.text(),
      tab.sl_status_lbl.text())
mw.analog_voltages[0] = V_OFF; settle()     # unload -> HOLD
check("S22: HOLD points at pipe in hole + bit frozen",
      "PIPE IN HOLE" in tab.sl_status_lbl.text()
      and "BIT FROZEN" in tab.sl_status_lbl.text()
      and "SL FOLLOWS BLOCK" in tab.sl_status_lbl.text(),
      tab.sl_status_lbl.text())

mw.close()
app.quit()
print()
print(f"===== PROMT.TXT FULL ACCURACY: PASS={_total - _failures} "
      f"FAIL={_failures} =====")
sys.exit(1 if _failures else 0)