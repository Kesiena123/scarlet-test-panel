"""Unit tests for the CURRENT LAYER calculation (promt.txt).

Rule: currentLayer = NUMBER OF FULLY COMPLETED LEVELS.
A level is counted only once its END boundary (anchor counter) is reached.

  while live < P1                  -> 0  (Level 1 not complete)
  P1 <= live < P2                  -> 1  (Level 1 complete)
  P2 <= live < P3                  -> 2
  P3 <= live < P4                  -> 3
  P4 <= live                       -> 4
  live > P4 -> 4 + floor((live-P4)/(P4-P3))

Uses the promt.txt FINAL TEST axes: P1=10000, P2=20000, P3=30000, P4=40000
(Gap34 = 40000 - 30000 = 10000).

Run:   python tests/test_current_layer.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scarlet_test_panel.services.layers import derive_layer  # noqa: E402

P1, P2, P3, P4 = 10000, 20000, 30000, 40000
GAP34 = P4 - P3
AXES = [P1, P2, P3, P4]

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  FAIL: {name} {detail}")


def L(live):
    return derive_layer(live, AXES)


print("== promt.txt FINAL TEST axis (P1..P4 = 10k/20k/30k/40k, Gap34=10k) ==")

check("LiveTick < P1 -> 0", L(0) == 0, f"got {L(0)}")
check("LiveTick < P1 (just under) -> 0", L(P1 - 1) == 0, f"got {L(P1-1)}")
check("no calibration yet -> 0", derive_layer(5000, []) == 0)

check("LiveTick == P1 -> 1", L(P1) == 1, f"got {L(P1)}")
check("P1 < LiveTick < P2 -> 1", L(15000) == 1, f"got {L(15000)}")
check("LiveTick == P2 -> 2", L(P2) == 2, f"got {L(P2)}")
check("P2 < LiveTick < P3 -> 2", L(25000) == 2, f"got {L(25000)}")
check("LiveTick == P3 -> 3", L(P3) == 3, f"got {L(P3)}")
check("P3 < LiveTick < P4 -> 3", L(35000) == 3, f"got {L(35000)}")

check("LiveTick == P4 -> 4", L(P4) == 4, f"got {L(P4)}")

# Extend beyond P4 using Gap34 = 10000 pulses / level.
check("LiveTick P4+1 (below next full level) -> 4", L(P4 + 1) == 4, f"got {L(P4+1)}")
check("LiveTick P4+Gap34-1 -> 4", L(P4 + GAP34 - 1) == 4, f"got {L(P4+GAP34-1)}")
check("LiveTick P4+Gap34 -> 5", L(P4 + GAP34) == 5, f"got {L(P4+GAP34)}")
check("LiveTick P4+2*Gap34 -> 6", L(P4 + 2 * GAP34) == 6, f"got {L(P4+2*GAP34)}")
check("LiveTick P4+10*Gap34 -> 14", L(P4 + 10 * GAP34) == 14, f"got {L(P4+10*GAP34)}")

# Monotonic behaviour: never decreases as the counter rises.
prev = -1
monotonic = True
for t in [0, P1, P2, P3, P4, P4 + GAP34, P4 + 2 * GAP34, P4 + 5 * GAP34]:
    v = L(t)
    if v < prev:
        monotonic = False
    prev = v
check("layer never decreases as counter rises", monotonic)

# Subset configurations (fewer than 4 anchors configured).
check("single layer P1 only: below -> 0", derive_layer(P1 - 1, [P1]) == 0)
check("single layer P1 only: at P1 -> 1", derive_layer(P1, [P1]) == 1)

print()
print(f"===== CURRENT-LAYER: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)
