"""Demo-mode ENCODER TICK regression suite.

Exercises PumpDashboard._demo_tick() — the simulated ENCODER path:

  * first tick seeds the DEMO calibration table (rebased so the bottom anchor
    reads 0 and the top anchor reads DEMO_MAX_COUNT, positions preserved)
  * the simulated counter sweeps 0 -> DEMO_MAX_COUNT -> 0 on a smooth cosine
    profile and resets every 60 ticks (the period of the sweep cycle)
  * the reported block position is derived from the ACTIVE calibration each
    tick (tick value -> interpolated position -> rounded report field), and the
    dashboard stores exactly the reported value
  * velocity = (pos - prev) / 50 ms converted to ft/min, direction is derived
    from the live tick delta (UP/DOWN only, never NONE), on-bottom follows the
    code's own predicate, calStatus stays VALID, encoder_recovered fires once
  * RESET FEET re-anchors at tick 0 == the operator's start feet and the block
    CONTINUES moving along the calibration (it never freezes at the datum)
  * every tick flows through the SAME protocol apply path as real firmware
  * analog/legacy signals stay clamped to the 0..5 V ADC span

Run:   python tests/test_demo_encoder_tick.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import (  # noqa: E402
    DEMO_CAL_POINTS, DEMO_MAX_COUNT, DEMO_WITS_CORRECTION,
    FW_MAX_CAL_POINTS, MS_PER_SAMPLE, ON_BOTTOM_EPS_FT, NUM_ANALOG)

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  [FAIL] {name}  :: {safe}")
    else:
        print(f"  [PASS] {name}")


mw = PumpDashboard()

# Hermetic: clear any persisted block-position calibration from the in-memory
# device table so `_demo_tick` MUST seed the demo table (never reuse leftovers
# written by an earlier run/test) but no disk state is touched.
for i in range(1, FW_MAX_CAL_POINTS + 1):
    mw.device[f"calCounter{i}"] = 0
    mw.device[f"calPosition{i}"] = 0.0
mw.device["witsCorrectionFt"] = 0.0
mw.device["confirmed"] = True
mw._demo_init = False
mw._demo_counter = 0
mw._toggle_demo()
check("demo mode enabled", mw._demo_mode is True)
check("device table cleared first", not mw.cal_points(), mw.cal_points())

print("== first tick seeds the scaled DEMO calibration ==")
mw._demo_tick()
pts = mw.cal_points()
check("seed happened on first tick", len(pts) >= 2, repr(pts))
check("4 demo anchors seeded", len(mw.cal_points()) == 4, repr(mw.cal_points()))
check("bottom anchor rebased to counter 0", pts[0][0] == 0, repr(pts[0]))
check("top anchor reaches DEMO_MAX_COUNT",
      pts[-1][0] == DEMO_MAX_COUNT, repr(pts[-1]))
check("physical positions preserved (sorted order)",
      [p[1] for p in pts] == [p["position"] for p in
                              sorted(DEMO_CAL_POINTS, key=lambda p: p["counter"])],
      repr([p[1] for p in pts]))
check("per-interval counts/ft computed for rows 1..3",
      all(mw.device.get(f"countsPerFoot{i}") for i in (1, 2, 3)),
      [mw.device.get(f"countsPerFoot{i}") for i in (1, 2, 3)])
check("wits correction seeded", mw.device.get("witsCorrectionFt", None)
      == DEMO_WITS_CORRECTION, mw.device.get("witsCorrectionFt"))
check("seed confirmed", mw.device.get("confirmed") is True)

print("== counter sweeps 0 -> DEMO_MAX_COUNT -> 0 and wraps every 60 ticks ==")
# Reset to the START of a fresh cycle for a deterministic trace.
mw._demo_counter = 0
trace = []
for _ in range(240):                       # 4 complete cycles
    c_before = mw._demo_counter
    mw._demo_tick()
    post = (c_before + 1) % 60             # counter is incremented BEFORE use
    frac = 0.5 - 0.5 * math.cos(post / 7.0)
    exp_ticks = int(round(frac * DEMO_MAX_COUNT))
    expected_pos = mw.calibrated_position(exp_ticks) or 0.0
    trace.append({
        "c": c_before,
        "ticks": mw.current_ticks,
        "exp_ticks": exp_ticks,
        "pos": mw.block_position_ft,
        "exp_pos": round(expected_pos, 3),
        "vel": mw.velocity_ft_min,
        "dir": mw.direction,
        "onb": mw.on_bottom,
        "st": mw.cal_status,
        "inr": mw.cal_in_range,
        "rec": mw.encoder_recovered,
        "layer": mw.current_layer,
        "seq": mw.encoder_sequence,
        "up": mw.encoder_uptime_s,
    })

check("every counter stays in the reset window [0,60)",
      all(0 <= t["c"] < 60 for t in trace))
check("tick values stay within [0, DEMO_MAX_COUNT]",
      all(0 <= t["ticks"] <= DEMO_MAX_COUNT for t in trace))
check("tick profile matches the cosine sweep exactly",
      all(t["ticks"] == t["exp_ticks"] for t in trace),
      f"first mismatch: {[t for t in trace if t['ticks'] != t['exp_ticks']][:3]}")
check("sweep reaches DEMO_MAX_COUNT once per cycle (post-counter 22)",
      [i for i, t in enumerate(trace) if t["ticks"] == DEMO_MAX_COUNT]
      == [21, 81, 141, 201])
check("sweep touches 0 at both troughs per cycle (post 0 boundary + post 44)",
      [i for i, t in enumerate(trace) if t["ticks"] == 0]
      == [43, 59, 103, 119, 163, 179, 223, 239])
check("block position derived from ACTIVE calibration each tick",
      all(abs(t["pos"] - t["exp_pos"]) < 1e-9 for t in trace),
      f"bad={[t for t in trace if abs(t['pos'] - t['exp_pos']) >= 1e-9][:2]}")
check("calStatus stays VALID across the sweep",
      all(t["st"] == "VALID" for t in trace))
check("calInRange stays 1 across the sweep",
      all(t["inr"] == 1 for t in trace))

print("== velocity & direction derived from the live tick delta ==")
dt_sec = MS_PER_SAMPLE / 1000.0
prev_pos = None
prev_ticks = None
dir_mismatch = []
vel_mismatch = []
for t in trace:
    pos_raw = mw.calibrated_position(t["ticks"]) or 0.0
    if prev_pos is None:
        exp_vel = 0.0
    else:
        exp_vel = (pos_raw - prev_pos) / dt_sec * 60.0
    if abs(t["vel"] - round(exp_vel, 1)) > 0.051:
        vel_mismatch.append((t["c"], t["vel"], round(exp_vel, 1)))
    if prev_ticks is not None:
        exp_dir = "UP" if t["ticks"] > prev_ticks else (
            "DOWN" if t["ticks"] < prev_ticks else t["dir"])
        if t["dir"] != exp_dir:
            dir_mismatch.append((t["c"], t["dir"], exp_dir))
    prev_pos = pos_raw
    prev_ticks = t["ticks"]
check("velocity matches (pos-prev)/50ms converted to ft/min",
      len(vel_mismatch) == 0, vel_mismatch[:5])
check("direction is UP/DOWN only (never NONE)",
      all(t["dir"] in ("UP", "DOWN") for t in trace),
      sorted({t["dir"] for t in trace}))
check("direction follows the live tick delta (rising=UP, falling=DOWN)",
      len(dir_mismatch) == 0, dir_mismatch[:5])

print("== on-bottom predicate, layer and recovery flag ==")
pred_bad = []
for t in trace:
    expect_onb = abs(t["vel"]) <= 0.05 and t["ticks"] <= 1
    if t["onb"] != bool(expect_onb):
        pred_bad.append((t["c"], t["onb"], expect_onb))
check("on-bottom always equals (|vel|<=0.05 and ticks<=1)",
      len(pred_bad) == 0, pred_bad[:5])

anchors = [p[0] for p in mw.cal_points()]
from scarlet_test_panel.services import layers as layers_service  # noqa: E402
layer_bad = []
for t in trace:
    exp_layer = layers_service.derive_layer(t["ticks"], anchors)
    if t["layer"] != exp_layer:
        layer_bad.append((t["c"], t["layer"], exp_layer))
check("current layer derived from the tick against the anchors",
      len(layer_bad) == 0, layer_bad[:5])

rec_first = next((i for i, t in enumerate(trace) if t["rec"]), None)
rec_at_c58 = trace[58]["c"] == 58 and trace[58]["rec"] is True
check("encoder_recovered fires (and only) at the first post-counter 59 tick",
      rec_first == 58 and rec_at_c58,
      f"first={rec_first} c58={rec_at_c58}")

print("== every tick flows through the real protocol apply path ==")
check("current_ticks propagated to the dashboard",
      all(t["ticks"] == t["exp_ticks"] for t in trace))
check("sequence mirrored from the post-increment demo counter",
      all(t["seq"] == (t["c"] + 1) % 60 for t in trace),
      [t for t in trace if t["seq"] != (t["c"] + 1) % 60][:3])
check("uptime mirrored (post-increment counter + 100)",
      all(t["up"] == (t["c"] + 1) % 60 + 100 for t in trace),
      [t for t in trace if t["up"] != (t["c"] + 1) % 60 + 100][:3])
check("legacy analog signals clamp to the 0..5 V ADC span",
      all(0.0 <= v <= 5.0 for v in mw.analog_voltages),
      f"span=[{min(mw.analog_voltages)}, {max(mw.analog_voltages)}]")
check("analog samples accumulated across the sweep",
      all(s >= 240 for s in mw.epoch_samples),
      mw.epoch_samples[:5])

print("== RESET FEET re-anchors at tick 0 and the block CONTINUES moving ==")
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402
mw.roles.set_role(ROLE_ENGINEER)
mw.block_tab._prompt_starting_ft = lambda: 0.0
mw.block_tab._on_reset_feet()
check("reset: block reads 0.00 ft at the reset instant",
      abs(mw.block_position_ft - 0.0) < 1e-9, mw.block_position_ft)
check("reset: velocity is zero at the reset instant",
      mw.velocity_ft_min == 0.0, mw.velocity_ft_min)
check("reset: encoder tick zeroed", mw.current_ticks == 0, mw.current_ticks)
check("reset: no lingering datum lock (attribute removed)",
      not hasattr(mw, "_demo_datum"))
prev = mw.block_position_ft
moved = False
rising = True
for _ in range(12):
    mw._demo_tick()
    now = mw.block_position_ft
    if now != prev:
        moved = True
    if now < prev:
        rising = False
    if now < -1e-9:
        rising = False
    prev = now
check("block keeps moving after RESET FEET (not frozen at the datum)",
      moved, prev)
check("resumed sweep rises from the new reference (no jumps below it)",
      rising, prev)
check("velocity recomputed after the reset (wheel turning again)",
      mw.velocity_ft_min != 0.0, mw.velocity_ft_min)
check("direction derived live after the reset",
      mw.direction in ("UP", "DOWN"), mw.direction)

mw._toggle_demo()
mw.close()
app.quit()

print()
print(f"===== DEMO-ENCODER-TICK: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)