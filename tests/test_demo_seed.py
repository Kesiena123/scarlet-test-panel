"""Regression: a FRESH app must show believable hookload values in DEMO mode
from the single Analog Monitor channel-0 pipeline (mw.hookload_klb =
analog_voltages[0] / 5 * 1000 klb), then the app returns to its real (idle)
state when demo stops.

Covers the fresh-app gap: with no separate hookload engine, DEMO mode walks
the same 0-5 V ADC channel-0 path the LIVE firmware feeds, so an uncalibrated
app shows a believable hookload in demo (never 0 / NO DATA stuck) while demo
never writes the settings file.

Run:   python tests/test_demo_seed.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import (  # noqa: E402
    NUM_ANALOG, SENSOR_CONFIG)
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402

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


# Hermetic: no writes to the real settings file during the demo cycle.
saved = []
_orig_save = main_mod.save_settings
main_mod.save_settings = lambda d: saved.append(dict(d))

mw = PumpDashboard()
# Pin channel-0 to the factory 0..1000 klb range now that the dashboard has
# finished re-applying any operator calibration on construction (0 V -> 0 klb).
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0
mw.analog_voltages = [0.0] * NUM_ANALOG    # fresh: nothing on the wire yet

print("== fresh app: channel-0 quiet until the first live/demo report ==")
check("fresh app: all channels 0 V",
      all(v == 0.0 for v in mw.analog_voltages), mw.analog_voltages)
check("fresh app: hookload_klb 0 (no data, never misleading)",
      mw.hookload_klb == 0.0, mw.hookload_klb)

print("== DEMO ON: believable channel-0 hookload through the live path ==")
mw.roles.set_role(ROLE_ENGINEER)
mw._toggle_demo()
app.processEvents()
check("demo mode on", mw._demo_mode is True)
mw._demo_tick()
v0 = mw.analog_voltages[0]
hk = mw.hookload_klb
check("demo fed channel-0 a believable voltage (0.2..4.8 V)",
      0.2 <= v0 <= 4.8, v0)
check("demo hookload believable klb (> 0, within the 1000 klb span)",
      hk > 0.0 and hk <= 1000.0, hk)
check("demo keeps every channel clamped to the 0..5 V ADC span",
      all(0.0 <= v <= 5.0 for v in mw.analog_voltages),
      f"span=[{min(mw.analog_voltages)}, {max(mw.analog_voltages)}]")
for _ in range(3):
    mw._demo_tick()
    app.processEvents()
check("demo hookload stays believable across ticks",
      mw.hookload_klb > 0.0 and mw.hookload_klb <= 1000.0, mw.hookload_klb)

print("== while demo runs, the settings file is never touched ==")
check("no settings writes during demo", len(saved) == 0, f"writes={len(saved)}")

print("== DEMO OFF: real (idle) mode returns, nothing leaks ==")
mw._toggle_demo()
app.processEvents()
check("demo mode off", mw._demo_mode is False)
check("demo off leaves the app readable (hookload_klb still live)",
      isinstance(mw.hookload_klb, float), mw.hookload_klb)
check("no settings writes across the full cycle", len(saved) == 0,
      f"writes={len(saved)}")

print("== RE-ENABLE demo after off still drives the channel (idempotent) ==")
mw._toggle_demo()
app.processEvents()
check("demo re-enabled", mw._demo_mode is True)
mw._demo_tick()
check("demo re-drives a believable hookload",
      mw.hookload_klb > 0.0 and mw.hookload_klb <= 1000.0, mw.hookload_klb)
mw._toggle_demo()
app.processEvents()
check("demo off again", mw._demo_mode is False)
check("settings still untouched", len(saved) == 0, f"writes={len(saved)}")

main_mod.save_settings = _orig_save
mw.close()
app.quit()

print()
print(f"===== DEMO-SEED: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)