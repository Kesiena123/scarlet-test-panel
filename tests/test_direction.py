"""Two-direction (UP/DOWN ONLY) verification — headless/offscreen.

Direction is derived from the live tick delta: current > prev -> UP,
current < prev -> DOWN, equal -> keep last valid. NONE/IDLE/STOPPED are
never produced or displayed; startup defaults to DOWN.

Run:   python tests/test_direction.py
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

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  FAIL: {name} {detail}")


def allowed(d):
    return d in ("UP", "DOWN")


mw = PumpDashboard()

# Startup state: valid default, never NONE.
check("startup direction is DOWN", mw.direction == "DOWN", mw.direction)
check("startup direction is not NONE", allowed(mw.direction), mw.direction)

# Example 1: 1000 -> 1010 = UP
mw._derive_direction(1000)
mw._derive_direction(1010)
check("1000->1010 = UP", mw.direction == "UP", mw.direction)

# Example 2: continue up 1010 -> 1020 = UP
mw._derive_direction(1020)
check("1010->1020 = UP (stays up)", mw.direction == "UP", mw.direction)

# Example 3: reverse 1020 -> 1015 = DOWN
mw._derive_direction(1015)
check("1020->1015 = DOWN", mw.direction == "DOWN", mw.direction)

# Example 4: stop / equal tick 1015 -> 1015, keep DOWN
mw._derive_direction(1015)
check("1015->1015 keeps DOWN (stopped)", mw.direction == "DOWN", mw.direction)

# Example 5: reverse to up 1015 -> 1030 = UP
mw._derive_direction(1030)
check("1015->1030 = UP (reverse to up)", mw.direction == "UP", mw.direction)

# Stop again, keep UP.
mw._derive_direction(1030)
check("1030->1030 keeps UP (stopped)", mw.direction == "UP", mw.direction)

# Never NONE across all transitions sampled.
check("never NONE after movement",
      allowed(mw.direction), mw.direction)

# Downward streak then stop keeps DOWN.
mw._derive_direction(1029)
mw._derive_direction(1000)
mw._derive_direction(1000)
check("long downdrift then stop keeps DOWN", mw.direction == "DOWN", mw.direction)

# ---- Display card only ever shows UP or DOWN ---------------------------
tab = mw.block_tab if hasattr(mw, "block_tab") else None
if tab is not None:
    mw.direction = "UP"
    tab.refresh()
    check("card shows UP only", tab.dir_lbl.text() == "▲ UP", tab.dir_lbl.text())
    mw.direction = "DOWN"
    tab.refresh()
    check("card shows DOWN only", tab.dir_lbl.text() == "▼ DOWN", tab.dir_lbl.text())
    # Any defensive non-UP input still renders as DOWN, never NONE.
    mw.direction = None
    tab.refresh()
    check("card never NONE on None input",
          tab.dir_lbl.text() == "▼ DOWN", tab.dir_lbl.text())
else:
    check("block tab exists", False, "no block_tab")

mw.close()
app.quit()
print()
print(f"===== DIRECTION: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)
