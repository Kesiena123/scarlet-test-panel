"""HOLE DEPTH box — the deepest Bit Position reached on the Block Position
dashboard (accumulated running maximum, NEVER decreased).

Semantics (user-defined):
  * driven by the same live bit_position_ft as the BIT POSITION screen — no
    second depth calculation, no separate engine.
  * non-decreasing: it HOLDS while tripping out / when the bit comes above
    the deepest depth, and only advances again when the bit runs deeper.
  * After every advance the box shows the new max; it never drifts back down.

Verifies:
  1. the HOLE DEPTH screen card exists with cap / figure / status labels.
  2. idle (Pipe in Hole not set) shows "--" + "set pipe in hole".
  3. with a flat string the depth starts at the entered Pipe in Hole.
  4. hookload load -> TRACK: block down (deeper) advances the hole depth.
  5. tripping back up (block up) holds the hole depth at the max.
  6. running deeper again advances to a new max.
  7. the running max never decreases (programmatic + displayed).
  8. FT / M display conversion respects the selected unit.
  9. status line: "AT MAX DEPTH" at the deepest, "HOLDS - TRIPPING" above it.
 10. BOTTOM STATE screen: ON BOTTOM when bit == hole depth, OFF BOTTOM when
     bit < hole depth, "--" idle / after trip, returns to ON BOTTOM.

Run:   python tests/test_hole_depth.py
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

FT_TO_M = 0.3048


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")


def near(a, b, tol=1e-6):
    return abs(float(a) - float(b)) < tol


mw = PumpDashboard()
mw.slip_window_sec = None          # hermetic vs an operator-saved SLIP WINDOW
mw.slip_window_load_klb = None     # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
SENSOR_CONFIG[0]["cal_min"] = 0.0      # re-pin: the dashboard re-applied any
SENSOR_CONFIG[0]["cal_max"] = 1000.0   # operator calibration on construction.
# Start from pristine depth state: no pipe config, no accumulated max.
if mw.pipe_in_hole_ft is not None:
    mw.pipe_in_hole_ft = None
    mw._bit_hold_ft = None
mw._hole_depth_max_ft = None
mw._bit_state = "NONE"


def load(klb):
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call
    SENSOR_CONFIG[0]["cal_max"] = 1000.0
    mw.analog_voltages[0] = klb / 200.0   # 40 klb == 0.20 V


def settle(klb):
    load(klb)
    for _ in range(BIT_POS_STATE_STABLE_TICKS):
        tab.refresh()


def floato(text):
    return text.replace(",", "")


# ---- 1. card structure ----------------------------------------------------
check("hole depth card has a figure label",
      hasattr(tab, "hole_depth_screen_lbl") and hasattr(tab.hole_depth_screen_lbl, "text"),
      getattr(tab, "hole_depth_screen_lbl", None))
check("hole depth card has a unit caption",
      hasattr(tab, "hole_depth_screen_cap") and hasattr(tab.hole_depth_screen_cap, "text"),
      getattr(tab, "hole_depth_screen_cap", None))
check("hole depth card has a status label",
      hasattr(tab, "hole_depth_screen_sub_lbl") and hasattr(tab.hole_depth_screen_sub_lbl, "text"),
      getattr(tab, "hole_depth_screen_sub_lbl", None))

# ---- 2. idle: Pipe in Hole not set ----------------------------------------
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw._hole_depth_max_ft = None
mw._bit_state = "NONE"
tab._position_unit = "FT"
tab.refresh()
check("idle hole depth shows --", tab.hole_depth_screen_lbl.text() == "--",
      tab.hole_depth_screen_lbl.text())
check("idle hole depth caption FT", tab.hole_depth_screen_cap.text() == "HOLE DEPTH (FT)",
      tab.hole_depth_screen_cap.text())
check("idle hole depth status set pipe in hole",
      tab.hole_depth_screen_sub_lbl.text() == "set pipe in hole",
      tab.hole_depth_screen_sub_lbl.text())
check("idle hole_depth_ft is None", mw.hole_depth_ft is None, mw.hole_depth_ft)

# ---- 3. flat string: depth starts at the entered Pipe in Hole --------------
mw.set_pipe_in_hole(6000.0)            # entered flat-string bit depth
mw._set_block_position(200.0)
settle(0.0)                            # no load -> HOLD at the flat string
check("flat-string hole depth = entered pipe in hole 6000.00",
      floato(tab.hole_depth_screen_lbl.text()) == "6000.00",
      tab.hole_depth_screen_lbl.text())
check("hole_depth_ft seeded 6000.0", near(mw.hole_depth_ft, 6000.0),
      mw.hole_depth_ft)
check("bit at max depth while idle", tab.hole_depth_screen_sub_lbl.text() == "● AT MAX DEPTH",
      tab.hole_depth_screen_sub_lbl.text())

# ---- 4. pipe load -> TRACK: block down (deeper) advances the hole depth ----
settle(40.0)                           # 40 klb -> TRACK
mw._set_block_position(199.0)          # block down 1 -> bit up 1 (deeper)
tab.refresh()
check("bit deeper 6001 after block down 1",
      near(mw.bit_position_ft, 6001.0), mw.bit_position_ft)
mw._set_block_position(150.0)          # block down 49 more -> bit 6050
tab.refresh()
check("hole depth advanced to 6050.00",
      floato(tab.hole_depth_screen_lbl.text()) == "6050.00",
      tab.hole_depth_screen_lbl.text())
check("hole_depth_ft 6050.0", near(mw.hole_depth_ft, 6050.0), mw.hole_depth_ft)
check("at max depth while fully down",
      tab.hole_depth_screen_sub_lbl.text() == "● AT MAX DEPTH",
      tab.hole_depth_screen_sub_lbl.text())

# ---- 5. tripping back up: hole depth HOLDS at the max ----------------------
mw._set_block_position(250.0)          # block up 100 -> bit up... bit 5950
tab.refresh()
check("bit shallower 5950 while tripping out",
      near(mw.bit_position_ft, 5950.0), mw.bit_position_ft)
check("hole depth HOLDS 6050.00 (never decreases)",
      floato(tab.hole_depth_screen_lbl.text()) == "6050.00",
      tab.hole_depth_screen_lbl.text())
check("hole_depth_ft still 6050.0", near(mw.hole_depth_ft, 6050.0), mw.hole_depth_ft)
check("status HOLDS - TRIPPING",
      tab.hole_depth_screen_sub_lbl.text() == "● HOLDS - TRIPPING",
      tab.hole_depth_screen_sub_lbl.text())
mw._set_block_position(300.0)          # more of the trip out
tab.refresh()
check("hole depth still HOLDS at 6050.00",
      floato(tab.hole_depth_screen_lbl.text()) == "6050.00",
      tab.hole_depth_screen_lbl.text())

# ---- 6. running deeper again advances to a new max -------------------------
mw._set_block_position(140.0)          # block down 160 -> bit 6060 (5900+160)
tab.refresh()
check("bit deeper 6060",
      near(mw.bit_position_ft, 6060.0), mw.bit_position_ft)
check("hole depth advanced to new max 6060.00",
      floato(tab.hole_depth_screen_lbl.text()) == "6060.00",
      tab.hole_depth_screen_lbl.text())
check("hole_depth_ft 6060.0", near(mw.hole_depth_ft, 6060.0), mw.hole_depth_ft)

# ---- 7. running max is never decreased (programmatic) ----------------------
mw._set_block_position(400.0)          # trip way out -> bit shallower
tab.refresh()
check("programmatic running max never decreases",
      near(mw.hole_depth_ft, 6060.0), mw.hole_depth_ft)
check("displayed hole depth never decreases",
      floato(tab.hole_depth_screen_lbl.text()) == "6060.00",
      tab.hole_depth_screen_lbl.text())

# ---- 8. FT / M display conversion ------------------------------------------
tab._position_unit = "M"
tab.refresh()
check("hole depth caption M", tab.hole_depth_screen_cap.text() == "HOLE DEPTH (M)",
      tab.hole_depth_screen_cap.text())
m_val = 6060.0 * FT_TO_M
check("hole depth 6060 ft shown in M",
      near(floato(tab.hole_depth_screen_lbl.text()), m_val, 1e-2),
      (tab.hole_depth_screen_lbl.text(), m_val))
check("hole_depth_ft internal feet untouched by toggle",
      near(mw.hole_depth_ft, 6060.0), mw.hole_depth_ft)

# ---- 9. idle again after clearing pipe config ------------------------------
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw._hole_depth_max_ft = None
mw._bit_state = "NONE"
tab._position_unit = "FT"
tab.refresh()
check("hole depth clears when pipe config removed",
      tab.hole_depth_screen_lbl.text() == "--", tab.hole_depth_screen_lbl.text())

# ============================================================================
# 10. BOTTOM STATE screen — ON BOTTOM (bit == hole depth) / OFF BOTTOM
#     (bit < hole depth), derived from the same single bit depth.
# ============================================================================
check("bottom state card has a state label",
      hasattr(tab, "bottom_screen_lbl") and hasattr(tab.bottom_screen_lbl, "text"),
      getattr(tab, "bottom_screen_lbl", None))
check("bottom state card has a sub label",
      hasattr(tab, "bottom_screen_sub_lbl") and hasattr(tab.bottom_screen_sub_lbl, "text"),
      getattr(tab, "bottom_screen_sub_lbl", None))

# 10a. idle — no pipe config.
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw._hole_depth_max_ft = None
mw._bit_state = "NONE"
tab.refresh()
check("bottom state idle shows --",
      tab.bottom_screen_lbl.text() == "--", tab.bottom_screen_lbl.text())
check("bottom state idle sub set pipe in hole",
      tab.bottom_screen_sub_lbl.text() == "set pipe in hole",
      tab.bottom_screen_sub_lbl.text())

# 10b. flat string, unloaded (HOLD) -> bit == hole depth == ON BOTTOM.
mw.set_pipe_in_hole(6000.0)
mw._set_block_position(200.0)
settle(0.0)
check("bottom state ON BOTTOM at flat string",
      tab.bottom_screen_lbl.text() == "ON BOTTOM", tab.bottom_screen_lbl.text())
check("bottom state ON BOTTOM sub bit==hole depth",
      "Bit 6000.0 ft = Hole Depth 6000.0 ft" in tab.bottom_screen_sub_lbl.text(),
      tab.bottom_screen_sub_lbl.text())

# 10c. TRACK, block down (deeper) -> bit advances to the new max, still ON.
settle(40.0)
mw._set_block_position(199.0)
tab.refresh()
mw._set_block_position(150.0)          # block down -> bit 6050 == max
tab.refresh()
check("bottom state ON BOTTOM while drilling down",
      tab.bottom_screen_lbl.text() == "ON BOTTOM", tab.bottom_screen_lbl.text())
check("bottom state ON BOTTOM at 6050",
      floato(tab.bottom_screen_lbl.text()) == "ON BOTTOM",
      tab.bottom_screen_lbl.text())

# 10d. trip out (block up) -> bit < hole depth -> OFF BOTTOM.
mw._set_block_position(300.0)          # block up -> bit shallower than max
tab.refresh()
check("bit shallower meanwhile", near(mw.bit_position_ft, 5900.0, 1.0),
      mw.bit_position_ft)
check("bottom state OFF BOTTOM while tripping",
      tab.bottom_screen_lbl.text() == "OFF BOTTOM", tab.bottom_screen_lbl.text())
check("bottom state OFF BOTTOM sub bit<hole depth",
      "< Hole Depth" in tab.bottom_screen_sub_lbl.text(),
      tab.bottom_screen_sub_lbl.text())

# 10e. drill back down to the max -> ON BOTTOM again.
mw._set_block_position(140.0)          # block down -> bit 6060 == new max
tab.refresh()
check("bottom state back to ON BOTTOM at the max",
      tab.bottom_screen_lbl.text() == "ON BOTTOM", tab.bottom_screen_lbl.text())

# 10f. idle after clearing -> "--" again.
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw._hole_depth_max_ft = None
mw._bit_state = "NONE"
tab.refresh()
check("bottom state clears when pipe config removed",
      tab.bottom_screen_lbl.text() == "--", tab.bottom_screen_lbl.text())

mw.close()
app.quit()
print()
print(f"===== HOLE DEPTH: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)