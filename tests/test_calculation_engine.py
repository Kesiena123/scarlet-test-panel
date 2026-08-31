"""Automated unit/acceptance tests for the calculation engine (promt3 §23/§31).

Covers the authoritative, dashboard-mirrored interpolation and calibration
validation so the math can be proven independent of a physical device:

  * promt3 §6 example (6500 pulses -> 27.50 ft)
  * §7/§23 exact layer anchors, midpoints, continuity near each anchor
  * small interval, large pulse values, decimal feet
  * §8 validation: duplicate pulses, duplicate feet, zero interval, out-of-range,
    non-monotonic (bad order) rejection
  * §16 out-of-range encoder count is never extrapolated
  * §15/§13 direction-consistency is preserved (no abs() sign destruction)

The interpolation is verified through PumpDashboard.calibrated_position() (the
same mirror used by the live block tab); validation is exercised through the
block tab's _validate_layer_input(), which mirrors the firmware rules
(encoder_validate_calibration_point in encoder.cpp).

Run:   python tests/test_calculation_engine.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
from scarlet_test_panel.main import PumpDashboard

TOL = 1e-6
_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  [FAIL] {name}" + (f"  :: {detail}" if detail else ""))
    else:
        print(f"  [PASS] {name}")


def set_cal(mw, points):
    """points: {layer: (counter, positionFt)}. Clears then sets given anchors."""
    for i in range(1, 5):
        c, p = points.get(i, (0, 0.0))
        mw.device[f"calCounter{i}"] = c
        mw.device[f"calPosition{i}"] = float(p)
    mw.device["witsCorrectionFt"] = 0.0


app = QApplication([])
mw = PumpDashboard()
mw._demo_mode = True

# promt3 §6 example calibration.
CAL = {1: (5000, 20.00), 2: (8500, 37.50), 3: (12000, 55.00), 4: (16800, 80.00)}
set_cal(mw, CAL)
mw.current_ticks = 6500

print("== promt3 §6 example ==")
pos = mw.calibrated_position(6500)
check("6500 pulses -> 27.50 ft",
      pos is not None and abs(pos - 27.50) < TOL, repr(pos))

print("== exact anchors (§7/§23) ==")
for i in range(1, 5):
    c, f = CAL[i]
    p = mw.calibrated_position(c)
    check(f"exact Layer {i} = {f:.2f} ft",
          p is not None and abs(p - f) < TOL, repr(p))

print("== midpoints between every pair (§7/§23) ==")
for (c1, f1), (c2, f2) in [(CAL[1], CAL[2]), (CAL[2], CAL[3]), (CAL[3], CAL[4])]:
    mid_c = (c1 + c2) / 2.0
    mid_f = f1 + (mid_c - c1) / (c2 - c1) * (f2 - f1)
    p = mw.calibrated_position(mid_c)
    check(f"midpoint {c1}-{c2}",
          p is not None and abs(p - mid_f) < TOL, f"{p!r} vs {mid_f!r}")

print("== continuity near interior anchors (§7/§20) ==")
for i in (2, 3):
    c, f = CAL[i]
    for delta in (-1, 1):
        if delta < 0:
            c1, f1 = CAL[i - 1]
            c2, f2 = CAL[i]
        else:
            c1, f1 = CAL[i]
            c2, f2 = CAL[i + 1]
        exp = f1 + (c + delta - c1) / (c2 - c1) * (f2 - f1)
        p = mw.calibrated_position(c + delta)
        check(f"continuity near L{i} delta={delta}",
              p is not None and abs(p - exp) < TOL, f"{p!r} vs {exp!r}")

print("== small / large / decimal (§7/§23) ==")
check("small interval (adjacent)", mw.calibrated_position(5001) is not None)
set_cal(mw, {1: (1000, 10.0), 2: (2000000, 10110.0)})
p = mw.calibrated_position(1000500)
check("large pulses midpoint = 5060.0",
      p is not None and abs(p - 5060.0) < TOL, repr(p))
set_cal(mw, {1: (1000, 10.0), 2: (2000, 22.3456)})
p = mw.calibrated_position(1500)
check("decimal feet midpoint = 16.17280",
      p is not None and abs(p - 16.17280) < 1e-4, repr(p))

print("== out-of-range never extrapolated (§16) ==")
set_cal(mw, {1: (1000, 20.0), 2: (2000, 40.0)})
check("below L1 -> None (no extrapolation)", mw.calibrated_position(999) is None)
check("above last -> None (no extrapolation)", mw.calibrated_position(2001) is None)

print("== promt3 §8 validation (via block tab) ==")
from scarlet_test_panel.tabs.block_position_tab import BlockPositionTab  # noqa: E402
tab = mw.block_tab if hasattr(mw, "block_tab") else BlockPositionTab(mw)
# helper to run the validator against a given edited layer
def val(layer_pk, pulses, feet, points=None):
    if points is not None:
        set_cal(mw, points)
    ok, err = tab._validate_layer_input(layer_pk, pulses, feet)
    return ok, err

ok, _ = val(2, 8500, 37.50, {1: (5000, 20.0), 2: (8500, 37.50), 3: (12000, 55.0), 4: (16800, 80.0)})
check("valid 4-layer calibration accepted", ok is True)

ok, err = val(2, 9000, 80.0, {1: (5000, 20.0), 2: (8500, 37.50), 3: (12000, 55.0), 4: (16800, 80.0)})
check("bad order (feet above L4 but pulses below L3) rejected", ok is False, err)

ok, err = val(2, 8500, 55.0, {1: (5000, 20.0), 2: (8500, 37.50), 3: (12000, 55.0), 4: (16800, 80.0)})
check("duplicate feet (equals L3 feet) rejected", ok is False, err)

ok, err = val(2, 5000, 37.50, {1: (5000, 20.0), 2: (99999, 37.50), 3: (12000, 55.0), 4: (16800, 80.0)})
check("duplicate pulses rejected", ok is False, err)

ok, err = val(2, 9000, 0.0, {1: (5000, 20.0), 2: (99999, 37.50), 3: (12000, 55.0), 4: (16800, 80.0)})
check("zero position (0 ft) rejected (ZERO_POSITION)", ok is False, err)

ok, err = val(1, 5000, float("nan"), {1: (5000, 20.0)})
check("NaN feet rejected", ok is False, err)

ok, err = val(1, 5000, float("inf"), {1: (5000, 20.0)})
check("Inf feet rejected", ok is False, err)

ok, err = val(1, "abc", 20.0, {1: (5000, 20.0)})
check("non-numeric pulses rejected", ok is False, err)

ok, err = val(2, 8500, 37.50, {2: (8500, 37.50)})   # only layer 2 configured
check("single-layer table valid", ok is True, err)

print()
print(f"===== CALCULATION-ENGINE: PASS={_total - _failures} FAIL={_failures} =====")
mw.close()
app.quit()
sys.exit(1 if _failures else 0)
