"""promt.txt — Bit Position / String Length acceptance suite.

Drives the REAL PumpDashboard (offscreen) through the exact examples the
promt.txt spec requires. Pipe load is proven by the single Analog Monitor
channel-0 Hookload (mw.hookload_klb); with the Slip Window unconfigured the
gate is hookload > 0 -> TRACK, hookload == 0 -> HOLD.

  * The complete 0 -> 100 -> 0 cycle table (promt.txt §7-§10): bit accumulates
    on every LOADED descent and stays frozen on every UNLOADED rise, while
    String Length = Bit Position + Block Position holds at every step.
  * The partial-movement example (promt.txt §11): full floating-point
    precision on non-even block deltas.
  * The critical transition (promt.txt §12): hookload unloading and re-loading
    mid-trip must never reset the accumulated bit.
  * Acceptance Rules 1-12 (promt.txt §20) asserted against the real engine.

Run:   python tests/test_promt_acceptance.py
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

_failures = 0
_total = 0

# Hermetic hookload: pin channel-0 to the factory 0..1000 klb range (0 V -> 0
# klb) so the unloaded/loaded gate behaves deterministically.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")


def settle(mw, ticks=None):
    """Run enough tab refreshes to let the debounced bit state settle."""
    for _ in range(ticks or BIT_POS_STATE_STABLE_TICKS):
        mw.block_tab.refresh()


def load(mw, klb):
    """Drive the live channel-0 hookload (klb = V/5 * 1000)."""
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call: the
    SENSOR_CONFIG[0]["cal_max"] = 1000.0  # dashboard re-applies operator
    mw.analog_voltages[0] = klb / 200.0   # calibration on construction.


mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
tab = mw.block_tab

# Rig setup: flat-string "pipe in hole" of 0 ft.
if mw.pipe_in_hole_ft is not None:
    mw.pipe_in_hole_ft = None
    mw._bit_hold_ft = None
mw._bit_state = "NONE"
mw.set_pipe_in_hole(0.0)
load(mw, 0.0)
mw._set_block_position(0.0)

STATE_UNLOADED = "HOLD"     # hookload == 0 -> HOLD
STATE_LOADED = "TRACK"      # hookload > 0 -> TRACK


def drive(block_ft, klb):
    """Settle hookload state, seat the block, and settle the position."""
    load(mw, klb)
    settle(mw)
    mw._set_block_position(block_ft)
    settle(mw)


# ---------------------------------------------------------------------------
# Complete cycle table (promt.txt §7-§10) — bit accumulates across cycles.
#                  Hookload   Block   Bit   String   Calc
#   Start       unloaded     0       0     0       0+0
#   Block rises unloaded     100     0     100     0+100
#   Block desc  loaded       0       100   100     100+0
#   Block rises unloaded     100     100   200     100+100
#   Block desc  loaded       0       200   200     200+0
#   Block rises unloaded     100     200   300     200+100
#   Block desc  loaded       0       300   300     300+0
# ---------------------------------------------------------------------------
table = [
    # (block, hookload_klb, expected_bit, expected_string, label)
    (0.0,   0.0,   0.0,   0.0,   "start"),
    (100.0, 0.0,   0.0,   100.0, "block rises unloaded"),
    (0.0,   50.0,  100.0, 100.0, "block descends loaded (cycle 1)"),
    (100.0, 0.0,   100.0, 200.0, "block rises unloaded (cycle 2)"),
    (0.0,   50.0,  200.0, 200.0, "block descends loaded (cycle 2)"),
    (100.0, 0.0,   200.0, 300.0, "block rises unloaded (cycle 3)"),
    (0.0,   50.0,  300.0, 300.0, "block descends loaded (cycle 3)"),
]

bit = ""
for block, klb, want_bit, want_sl, label in table:
    drive(block, klb)
    bit = mw.bit_position_ft
    sl = mw.string_length_ft
    check(f"CYCLE {label}: block={block} -> bit={want_bit}",
          bit is not None and abs(float(bit) - want_bit) < 1e-6,
          f"got bit={bit}")
    check(f"CYCLE {label}: string length={want_sl}",
          sl is not None and abs(float(sl) - want_sl) < 1e-6,
          f"got sl={sl}")
    if bit is not None and sl is not None:
        check(f"CYCLE {label}: String = Bit + Block invariant",
              abs(float(sl) - (float(bit) + float(block))) < 1e-6,
              f"sl={sl} bit={bit} block={block}")
    check(f"CYCLE {label}: state={STATE_LOADED if klb > 0 else STATE_UNLOADED}",
          mw.bit_position_state == (STATE_LOADED if klb > 0
                                    else STATE_UNLOADED),
          mw.bit_position_state)

# ---------------------------------------------------------------------------
# Partial movement example (promt.txt §11) — full float precision.
#   Previous Block = 75.50, Current Block = 42.25, Previous Bit = 124.75,
#   Hookload > 0 -> Block Delta = -33.25 -> Bit = 158.00, SL = 200.25.
# ---------------------------------------------------------------------------
mw._set_block_position(75.50)
mw.set_pipe_in_hole(124.75)          # flat-string bit depth before the trip
load(mw, 50.0)                       # loaded
settle(mw)
check("PARTIAL: engaged TRACK anchored at prev bit 124.75",
      abs(float(mw.bit_position_ft) - 124.75) < 1e-6, mw.bit_position_ft)
drive(42.25, 50.0)                   # block descends 75.50 -> 42.25, still loaded
check("PARTIAL: bit = 158.00 after -33.25 ft block delta",
      abs(float(mw.bit_position_ft) - 158.00) < 1e-9, mw.bit_position_ft)
check("PARTIAL: string length = 200.25",
      abs(float(mw.string_length_ft) - 200.25) < 1e-9, mw.string_length_ft)

# ---------------------------------------------------------------------------
# Critical state transition (promt.txt §12) — no reset across unload/reload.
#   Bit = 250, Block = 0. Unload: block -> 150, bit stays 250, SL = 400.
#   Reload: block -> 50, bit = 350, SL = 400.
# ---------------------------------------------------------------------------
mw.set_pipe_in_hole(250.0)
mw._set_block_position(0.0)
load(mw, 50.0)                       # loaded to anchor SL = 0 + 250
settle(mw)
check("TRANS: anchored bit 250.00 at block 0",
      abs(float(mw.bit_position_ft) - 250.0) < 1e-6, mw.bit_position_ft)
drive(150.0, 0.0)                    # unload, block rises 0 -> 150
check("TRANS: unloaded bit frozen at 250.00",
      abs(float(mw.bit_position_ft) - 250.0) < 1e-6, mw.bit_position_ft)
check("TRANS: string length = 250 + 150 = 400",
      abs(float(mw.string_length_ft) - 400.0) < 1e-6, mw.string_length_ft)
drive(50.0, 50.0)                    # reload, block descends 150 -> 50
check("TRANS: reloaded bit = 350.00 (never reset)",
      abs(float(mw.bit_position_ft) - 350.0) < 1e-6, mw.bit_position_ft)
check("TRANS: string length stays 400.00",
      abs(float(mw.string_length_ft) - 400.0) < 1e-6, mw.string_length_ft)

# ---------------------------------------------------------------------------
# Acceptance Rules 1-12 (promt.txt §20) — symbolically re-asserted.
# ---------------------------------------------------------------------------
drive(0.0, 0.0)                      # unloaded
prev_bit = mw.bit_position_ft
drive(50.0, 0.0)                     # block moves while unloaded
check("RULE 1+5+6: unloaded block movement leaves bit unchanged",
      mw.bit_position_state == "HOLD"
      and abs(float(mw.bit_position_ft) - float(prev_bit)) < 1e-9,
      (mw.bit_position_state, mw.bit_position_ft, prev_bit))

# Rule 8: Bit = 0, Block = 100, unloaded -> Bit = 0, String = 100.
mw.set_pipe_in_hole(0.0)
drive(100.0, 0.0)
check("RULE 8: unloaded Bit=0 Block=100 -> String=100",
      abs(float(mw.bit_position_ft) - 0.0) < 1e-9
      and abs(float(mw.string_length_ft) - 100.0) < 1e-9,
      (mw.bit_position_ft, mw.string_length_ft))

# Rule 9: loaded 100 -> 0 -> Bit = 100, Block = 0, String = 100.
drive(0.0, 50.0)
check("RULE 9: loaded descent to block 0 -> Bit=100 String=100",
      abs(float(mw.bit_position_ft) - 100.0) < 1e-9
      and abs(float(mw.string_length_ft) - 100.0) < 1e-9,
      (mw.bit_position_ft, mw.string_length_ft))

# Rule 10: unloaded 0 -> 100 -> Bit stays 100, String = 200.
drive(100.0, 0.0)
check("RULE 10: unloaded rise -> Bit=100 Block=100 String=200",
      abs(float(mw.bit_position_ft) - 100.0) < 1e-9
      and abs(float(mw.string_length_ft) - 200.0) < 1e-9,
      (mw.bit_position_ft, mw.string_length_ft))

# Rule 11: loaded 100 -> 0 -> Bit = 200, Block = 0, String = 200.
drive(0.0, 50.0)
check("RULE 11: loaded descent -> Bit=200 Block=0 String=200",
      abs(float(mw.bit_position_ft) - 200.0) < 1e-9
      and abs(float(mw.string_length_ft) - 200.0) < 1e-9,
      (mw.bit_position_ft, mw.string_length_ft))

# Rule 12 accumulates indefinitely: one more loaded descent should give 300.
drive(100.0, 0.0)
drive(0.0, 50.0)
check("RULE 12: further loaded descents keep accumulating (Bit=300)",
      abs(float(mw.bit_position_ft) - 300.0) < 1e-9
      and abs(float(mw.string_length_ft) - 300.0) < 1e-9,
      (mw.bit_position_ft, mw.string_length_ft))

# Duplicate encoder reading while loaded: block seated twice at 0.
drive(0.0, 50.0)
before = mw.bit_position_ft
drive(0.0, 50.0)
check("RULE 3+4+7: no movement -> bit does not move",
      abs(float(mw.bit_position_ft) - float(before)) < 1e-9,
      (mw.bit_position_ft, before))

mw.close()
app.quit()
print()
print(f"===== PROMT.TXT ACCEPTANCE: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)