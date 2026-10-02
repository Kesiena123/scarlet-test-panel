"""promt2.txt ACCURACY / ACCEPTANCE VERIFICATION — mirrors the spec's exact
worked examples and the FINAL ACCEPTANCE RULE, and proves the on-screen
readouts are byte-identical to the tracked engine model (no duplicate or
independent calculation). Pipe load is proven by the single Analog Monitor
channel-0 Hookload (mw.hookload_klb); with the Slip Window unconfigured the
gate is hookload > 0 -> TRACK, hookload == 0 -> HOLD.

Sections:
  A) promt2 §2 literal  : String Length 5000, BP 200->199->198->197,
                          Bit 4800->4801->4802->4803 (SL constant 5000).
  B) promt2 §5 literal  : initial SL 1000 (BP 200 / Bit 800); no pipe load
                          BP 200..203 keeps Bit 800 while SL 1000..1003 follows
                          the block; pipe load BP 203..200 -> Bit 800..803 with
                          SL constant for the trip.
  C) FINAL ACCEPTANCE RULE sweep: every step checks
                          Bit = HOLD when hookload == 0, Bit inverse-tracks
                          1:1 when hookload > 0, and
                          String Length == Block Position + Bit Position ALWAYS
                          (no negative values anywhere, no jumps on re-engage).
  D) hookload gate       : the live channel-0 value flips HOLD <-> TRACK and
                          the bit still tracks opposite to the block.
  E) screen sync        : BIT + STRING LENGTH labels equal the engine values.

Run:   python tests/test_promt2_accuracy.py
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


FT_TO_M = 0.3048

mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
if mw.pipe_in_hole_ft is not None:
    mw.pipe_in_hole_ft = None
    mw._bit_hold_ft = None
mw._bit_state = "NONE"
SENSOR_CONFIG[0]["cal_min"] = 0.0     # pin factory range now: the dashboard
SENSOR_CONFIG[0]["cal_max"] = 1000.0  # re-applied operator calibration above.
mw.analog_voltages[0] = 0.0


def settle(ticks=BIT_POS_STATE_STABLE_TICKS):
    for _ in range(ticks):
        tab.refresh()


def load(klb):
    """Drive the live channel-0 Hookload (klb = V/5 * 1000)."""
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call: the
    SENSOR_CONFIG[0]["cal_max"] = 1000.0  # dashboard re-applies operator
    mw.analog_voltages[0] = klb / 200.0   # calibration on construction.


# ===========================================================================
# A) promt2.txt §2 — pipe load, block moving down, exact example numbers.
#    String Length = 5000 -> the operator's Pipe in Hole is the bit depth at
#    the current block = 4800 (BP 200), so SL = 200 + 4800 = 5000.
# ===========================================================================
print("SECTION A - promt2.txt sec2 (String Length 5000, block moving down)")
mw.set_pipe_in_hole(4800.0)
mw._set_block_position(200.0)
load(0.0)
settle()
check("A idle SL = 200 + 4800 = 5000.00", mw.string_length_ft == 5000.0,
      mw.string_length_ft)
check("A idle bit = 4800.00", mw.bit_position_ft == 4800.0,
      mw.bit_position_ft)

load(40.0)                           # pipe load present -> TRACK
settle()
check("A TRACK engaged", mw.bit_position_state == "TRACK",
      mw.bit_position_state)
for bp, want_bit in ((199.0, 4801.0), (198.0, 4802.0), (197.0, 4803.0)):
    mw._set_block_position(bp)
    settle(1)
    check(f"A BP {bp:.0f} -> bit {want_bit:.0f} (inverse)",
          mw.bit_position_ft == want_bit, (bp, mw.bit_position_ft))
    check(f"A SL stays 5000.00 at BP {bp:.0f}",
          mw.string_length_ft == 5000.0, mw.string_length_ft)

# ===========================================================================
# B) promt2.txt §5 — exact narrative: no-load hold, then pipe-load tracking.
# ===========================================================================
print("SECTION B - promt2.txt sec5 (initial SL 1000, BP 200, bit 800)")
mw.set_pipe_in_hole(800.0)
mw._set_block_position(200.0)
load(0.0)
settle()
check("B initial bit = 800.00 (flat string)", mw.bit_position_ft == 800.0,
      mw.bit_position_ft)
check("B initial SL = 200 + 800 = 1000.00", mw.string_length_ft == 1000.0,
      mw.string_length_ft)

for bp in (201.0, 202.0, 203.0):     # no pipe load -> bit must stay 800
    mw._set_block_position(bp)
    settle(1)
    check(f"B no-load BP {bp:.0f}: bit held 800.00",
          mw.bit_position_ft == 800.0, mw.bit_position_ft)
    check(f"B no-load SL follows block {800.0 + bp:.0f}.00",
          mw.string_length_ft == 800.0 + bp, mw.string_length_ft)

load(80.0)                           # pipe load active, block downward
settle()
check("B TRACK engaged at BP 203", mw.bit_position_state == "TRACK",
      mw.bit_position_state)
for bp, want_bit in ((202.0, 801.0), (201.0, 802.0), (200.0, 803.0)):
    mw._set_block_position(bp)
    settle(1)
    check(f"B pipe-load BP {bp:.0f} -> bit {want_bit:.0f}",
          mw.bit_position_ft == want_bit, mw.bit_position_ft)
    check(f"B SL = Block + Bit = {want_bit + bp:.0f}.00 (constant 1003)",
          mw.string_length_ft == want_bit + bp, mw.string_length_ft)

# ===========================================================================
# C) FINAL ACCEPTANCE RULE — exhaustive invariant sweep.
# ===========================================================================
print("SECTION C - FINAL ACCEPTANCE RULE sweep (Bit = SL - BP, SL = BP + Bit)")
mw.set_pipe_in_hole(6000.0)
mw._set_block_position(300.0)
load(0.0)
settle()
bp = 300.0
for i in range(40):                       # 1) no-load walk: bit frozen
    bp += 1.0 if i % 7 < 3 else -0.5
    mw._set_block_position(bp)
    settle(1)
    expect_bit = mw.bit_position_ft       # the frozen hold
    ok = (mw.bit_position_state == "HOLD"
          and mw.string_length_ft == bp + expect_bit)
    check(f"C1 no-load step {i}: HOLD + SL==BP+Bit",
          ok, (mw.bit_position_state, bp, expect_bit, mw.string_length_ft))
load(90.0)                                # 2) load walk: bit == ref - BP exact
settle()
engage_ref = mw.bit_position_ft + bp      # the re-anchored SL reference
check(f"C2 engaged TRACK (reference {engage_ref:0.2f})",
      mw.bit_position_state == "TRACK", mw.bit_position_state)
for step_no in range(60):
    bp += [-1.0, 1.0, -0.5, 0.0, 2.0][step_no % 5]
    mw._set_block_position(bp)
    settle(1)
    want_bit = engage_ref - bp            # full precision, not rounded
    ok = (mw.bit_position_ft == want_bit
          and mw.string_length_ft == bp + want_bit
          and mw.bit_position_ft >= 0.0
          and mw.string_length_ft >= 0.0)
    check(f"C2 load step {step_no}: bit={want_bit:0.2f} exact + SL invariant",
          ok, (mw.bit_position_ft, want_bit, mw.string_length_ft))
load(0.0)                                 # 3) unload freezes, re-engage jumps not
settle()
held_bit = mw.bit_position_ft
mw._set_block_position(bp + 5.0)
settle(1)
check("C3 HOLD frozen while block moves", mw.bit_position_ft == held_bit,
      (mw.bit_position_ft, held_bit))
sl_before_engage = mw.string_length_ft
check("C3 HOLD SL follows the block only",
      sl_before_engage == bp + 5.0 + held_bit, mw.string_length_ft)
load(90.0)
settle()
check("C3 re-engage keeps the same bit (no jump)",
      mw.bit_position_ft == held_bit, (mw.bit_position_ft, held_bit))
check("C3 re-engage keeps the same SL (no jump)",
      mw.string_length_ft == sl_before_engage, mw.string_length_ft)

# ===========================================================================
# D) Hookload gate on the live channel-0 value.
# ===========================================================================
print("SECTION D - hookload gate (live channel-0 value drives the state)")
load(0.0)
settle()
check("D no load -> HOLD", mw.bit_position_state == "HOLD",
      mw.bit_position_state)
mw.set_pipe_in_hole(5000.0)
mw._bit_hold_ft = 5000.0
mw._set_block_position(250.0)
mw._bit_track_block_ft = None
load(40.0)                                # 40 klb -> pipe load -> TRACK
settle()
check("D load -> TRACK", mw.bit_position_state == "TRACK",
      mw.bit_position_state)
before = mw.bit_position_ft
check("D TRACK anchored to live bit (5000.00)", before == 5000.0, before)
mw._set_block_position(249.0)
settle(1)
check("D TRACK bit moves opposite (bp down 1 -> bit up 1)",
      mw.bit_position_ft == (before + 1.0), (before, mw.bit_position_ft))
load(0.0)
settle()
check("D unload -> HOLD again", mw.bit_position_state == "HOLD",
      mw.bit_position_state)

# ===========================================================================
# E) Screen sync — labels equal the engine model exactly (no duplicate math).
# ===========================================================================
print("SECTION E - on-screen sync (BIT + STRING LENGTH == engine)")
if mw._bit_state == "TRACK":
    load(0.0)
    settle()
bpv = float(mw.block_position_ft or 0.0)
bitv = mw.bit_position_ft
slv = mw.string_length_ft
check("E1 BIT label == engine value",
      bitv is not None and
      tab.bit_screen_lbl.text().replace(",", "") == f"{bitv:.2f}",
      (tab.bit_screen_lbl.text(), bitv))
check(f"E2 SL label == engine value ({slv:.2f} FT)",
      slv is not None and
      tab.string_length_lbl.text().replace(",", "") == f"{slv:.2f} FT",
      tab.string_length_lbl.text())
tab._on_unit_toggle()                     # M display
sl_m = slv * FT_TO_M
bit_m = mw.bit_position_ft * FT_TO_M
check("E3 BIT label M == engine*0.3048",
      bitv is not None and
      tab.bit_screen_lbl.text().replace(",", "") == f"{bit_m:.2f}",
      (tab.bit_screen_lbl.text(), bit_m))
check("E4 SL label M == engine*0.3048",
      tab.string_length_lbl.text().replace(",", "") == f"{sl_m:.2f} M",
      (tab.string_length_lbl.text(), sl_m))
cur = mw.bit_position_ft
check("E5 invariant on the board: BP + Bit == SL",
      (bpv + cur if cur is not None else bpv) == slv, (bpv, cur, slv))
tab._on_unit_toggle()

mw.close()
app.quit()
print()
print(f"===== PROMT2 ACCURACY: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)