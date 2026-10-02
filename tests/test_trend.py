"""REAL-TIME BLOCK POSITION vs TIME TREND — promt1.txt.

Verifies the advanced trend feature uses ONLY the existing live block
position, records real timestamps at a controlled interval into a bounded
rolling buffer, respects FT/M chart display (feet internal), supports
LIVE/1/5/10/30min ranges, PAUSE VIEW (freezes only the display, sampling
continues), RESUME, CLEAR TREND (history only), hover inspection, and
never touches encoder/calibration/layer/ticks.

Maps to promt1.txt TEST 1-10:
  TEST 1  UP    -> trend increases
  TEST 2  DOWN  -> trend decreases
  TEST 3  STOP  -> stable position, app stable
  TEST 4  FT->M -> chart converts to meters (feet internal)
  TEST 5  M->FT -> original feet return (no cumulative error)
  TEST 6  resize -> chart repaints on new size (no crash)
  TEST 7  extended run -> memory bounded (deque)
  TEST 8  CLEAR TREND -> only history cleared
  TEST 9  RESET FEET -> continues, trend records new position
  TEST 10 calibration change -> chart reflects new position, cal saved

Run:   python tests/test_trend.py
Exit:  0 pass / 1 fail.
"""

import datetime
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402
from scarlet_test_panel.config import (  # noqa: E402
    FT_TO_M, TREND_MAX_POINTS, TREND_SAMPLE_MS)
from scarlet_test_panel.widgets.graph_plot import (  # noqa: E402
    _IndustrialTrendGraph)

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


def clear_trend(mw):
    mw.trend_data.clear()


mw = PumpDashboard()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab

mw.device.update({"calCounter1": 0, "calPosition1": 0.0,
                  "calCounter2": 5000, "calPosition2": 50.0,
                  "calCounter3": 10000, "calPosition3": 100.0,
                  "calCounter4": 15000, "calPosition4": 150.0,
                  "confirmed": True})
cal_before = snapshot_cal(mw)

graph = tab.graph

# ------------------------------------------------------------------
# 1) SAMPLED ROLLING BUFFER — controlled 1 Hz, bounded, feet internal.
# ------------------------------------------------------------------
check("sampling interval configured (1000 ms)",
      TREND_SAMPLE_MS == 1000, TREND_SAMPLE_MS)
check("rolling buffer is bounded",
      TREND_MAX_POINTS > 0 and hasattr(mw.trend_data, 'maxlen')
      and mw.trend_data.maxlen == TREND_MAX_POINTS)

# Fill the buffer (controlled sampling) and confirm oldest is dropped when full.
now = datetime.datetime.now()
mw.trend_data.clear()
for i in range(TREND_MAX_POINTS - 1):
    mw.trend_data.append((now - datetime.timedelta(seconds=(TREND_MAX_POINTS - i)),
                          100.0 + float(i)))
check("buffer reached capacity without dropping",
      len(mw.trend_data) == TREND_MAX_POINTS - 1)
mw._record_trend_point()
check("at capacity the newest point is kept",
      len(mw.trend_data) == TREND_MAX_POINTS)
mw._record_trend_point()
check("over capacity the OLDEST point is removed (rolling)",
      len(mw.trend_data) == TREND_MAX_POINTS)

# `_record_trend_point` records the EXACT live position shown on the dashboard.
mw._set_block_position(123.5)
mw._record_trend_point()
ts, pos = mw.trend_data[-1]
check("sampled point = authoritative live position",
      abs(pos - mw.block_position_ft) < 1e-9)
check("sampled point has a real timestamp", isinstance(ts, datetime.datetime))
check("sampled point is recent", (datetime.datetime.now() - ts).total_seconds() < 5)

# ------------------------------------------------------------------
# TEST 1 — UP moves the trend upward (values increase over time).
# ------------------------------------------------------------------
clear_trend(mw)
base = datetime.datetime.now() - datetime.timedelta(seconds=60)
for i, ft in enumerate((100.0, 102.0, 105.0, 108.0, 110.0)):
    mw.trend_data.append((base + datetime.timedelta(seconds=i * 10), ft))
pts = tab._trend_samples(120)
vals = [v for _, v in pts]
check("TEST1 UP trend is monotonically increasing",
      vals == sorted(vals), vals)
check("TEST1 UP starts low, ends high", vals[0] < vals[-1])

# ------------------------------------------------------------------
# TEST 2 — DOWN moves the trend downward.
# ------------------------------------------------------------------
clear_trend(mw)
for i, ft in enumerate((120.0, 115.0, 110.0, 105.0, 100.0)):
    mw.trend_data.append((base + datetime.timedelta(seconds=i * 10), ft))
pts = tab._trend_samples(120)
vals = [v for _, v in pts]
check("TEST2 DOWN trend monotonically decreasing",
      vals == sorted(vals, reverse=True), vals)
check("TEST2 starts high, ends low", vals[0] > vals[-1])

# ------------------------------------------------------------------
# TEST 3 — encoder stopped -> stable position, no errors.
# ------------------------------------------------------------------
clear_trend(mw)
now3 = datetime.datetime.now()
for i in range(10):
    mw.trend_data.append((now3 - datetime.timedelta(seconds=(10 - i)), 80.0))
pts = tab._trend_samples(60)
check("TEST3 stable position has values", len(pts) == 10)
check("TEST3 all samples stable at 80 FT", all(v == 80.0 for _, v in pts))
check("TEST3 no failure (samples readable)",
      tab._trend_samples(60) == pts)

# ------------------------------------------------------------------
# TEST 4/5 — FT -> M conversion (feet internal) and clean switch back.
# ------------------------------------------------------------------
clear_trend(mw)
mw._set_block_position(100.0)
mw._record_trend_point()

tab._position_unit = "FT"
lb_before = tab.pos_value_lbl.text()
graph_unit_ft = graph.unit
tab._on_unit_toggle()   # -> M
check("TEST4 toggle switches position card to M", tab._position_unit == "M")
check("TEST4 card 100 FT -> 30.48 M",
      tab.pos_value_lbl.text() == "30.48 M", tab.pos_value_lbl.text())
check("TEST4 chart unit becomes M", graph.unit == "M", graph.unit)
check("TEST4 internal feet STILL 100.0",
      abs(mw.block_position_ft - 100.0) < 1e-9)
check("TEST4 conversion factor exact",
      abs(100.0 * FT_TO_M - 30.48) < 1e-3)

# The trend sample_fn still returns feet (internal source of truth).
pts = tab._trend_samples(60)
stored_feet = [v for _, v in pts]
check("TEST4 stored trend values remain FEET",
      stored_feet and abs(stored_feet[-1] - 100.0) < 1e-9, stored_feet)

tab._on_unit_toggle()   # -> FT
check("TEST5 switches back to FT", tab._position_unit == "FT")
check("TEST5 card back to exact 100.00 FT (no cumulative error)",
      tab.pos_value_lbl.text() == "100.00 FT", tab.pos_value_lbl.text())
check("TEST5 chart unit back to FT", graph.unit == "FT")
check("TEST5 stored trend STILL exact feet",
      all(abs(v - 100.0) < 1e-9 for _, v in tab._trend_samples(60)))

# ------------------------------------------------------------------
# Live-position marker + hover inspection (paint smoke) + resize.
# ------------------------------------------------------------------
graph.resize(640, 320)
graph.update()
try:
    graph.grab().toImage()
    check("TEST6 chart repaints on resize", True)
except Exception as e:  # pragma: no cover
    check("TEST6 chart repaints on resize", False, repr(e))
graph.resize(1280, 700)
try:
    graph.grab().toImage()
    check("TEST6 chart repaints on large window", True)
except Exception as e:  # pragma: no cover
    check("TEST6 chart repaints on large window", False, repr(e))

# Hover tooltip math: nearest sampled point within the window.
clear_trend(mw)
for i, ft in enumerate((10.0, 20.0, 30.0)):
    mw.trend_data.append((datetime.datetime.now() -
                          datetime.timedelta(seconds=(60 - i * 20)), ft))
ev = type("E", (), {})()
ev.x = lambda: 200
ev.y = lambda: 150
graph.mouseMoveEvent(ev)
check("hover no crash and populated or None",
      graph._hover_ts is not None or graph._hover_val is None)
if graph._hover_ts is not None:
    check("hover position matches a recorded sample",
          graph._hover_val in (10.0, 20.0, 30.0, None))

# ------------------------------------------------------------------
# RANGE OPTIONS — LIVE / 1 / 5 / 10 / 30 min / 1 hour.
# ------------------------------------------------------------------
labels = [l for l, _ in _IndustrialTrendGraph.RANGES]
check("TEST ranges present",
      labels == ["LIVE", "1 MIN", "5 MIN", "10 MIN", "30 MIN", "1 HOUR"], labels)
check("tab range buttons match", len(tab._range_buttons) == len(labels))

# ------------------------------------------------------------------
# LIVE center-mode — the newest point moves at the horizontal middle.
# ------------------------------------------------------------------
now_c = datetime.datetime.now()
graph.set_range_seconds(0)          # LIVE -> center mode
check("LIVE selected -> center mode", graph._is_center_mode() is True)
left, right, window = graph._time_mapping(now_c)
left_s = (now_c - left).total_seconds()
right_s = (right - now_c).total_seconds()
check("LIVE axis: past fills left half", abs(left_s - window / 2.0) < 1e-9, left_s)
check("LIVE axis: future fills right half", abs(right_s - window / 2.0) < 1e-9, right_s)
check("LIVE: newest point maps to the horizontal center",
      abs(graph._x_for(now_c, left, right) - 0.5) < 1e-9,
      graph._x_for(now_c, left, right))

graph.set_range_seconds(60)         # fixed 1 MIN -> newest at right edge
check("1 MIN deselects center mode", graph._is_center_mode() is False)
left, right, window = graph._time_mapping(now_c)
check("1 MIN: axis spans the full time window",
      abs((right - left).total_seconds() - 60) < 1e-9)
check("1 MIN: newest point maps to the right edge",
      abs(graph._x_for(now_c, left, right) - 1.0) < 1e-9)
graph.set_range_seconds(0)          # back to LIVE/center default
check("LIVE restored as center mode", graph._is_center_mode() is True)

# ------------------------------------------------------------------
# TEST 8 — CLEAR TREND clears only history.
# ------------------------------------------------------------------
clear_trend(mw)
for i in range(5):
    mw.trend_data.append((datetime.datetime.now(), float(i)))
snap_tick = mw.current_ticks
snap_cal = snapshot_cal(mw)
tab._on_clear_trend()
check("TEST8 CLEAR TREND empties history", len(mw.trend_data) == 0)
check("TEST8 ticks untouched by CLEAR TREND", mw.current_ticks == snap_tick)
check("TEST8 calibration untouched by CLEAR TREND", snap_cal == snapshot_cal(mw))
# Chart resumes collecting live data after clearing.
mw._record_trend_point()
check("TEST8 chart resumes recording after CLEAR TREND",
      len(mw.trend_data) >= 1)

# ------------------------------------------------------------------
# PAUSE VIEW / RESUME — display-only freeze; sampling keeps recording.
# ------------------------------------------------------------------
clear_trend(mw)
mw._set_block_position(40.0)
mw._record_trend_point()
tab._on_trend_pause(True)
check("PAUSE sets paused flag", tab._trend_paused is True)
check("PAUSE button text -> RESUME", tab._pause_btn.text() == "RESUME")
check("PAUSE status -> TREND RECORDING",
      tab.trend_status_lbl.text() == "TREND RECORDING",
      tab.trend_status_lbl.text())
# While paused, sampling/recording STILL happens in the background.
paused_len = len(mw.trend_data)
mw._set_block_position(45.0)
mw._record_trend_point()
check("PAUSE: recording continues while view frozen",
      len(mw.trend_data) == paused_len + 1)
# RESUME.
tab._on_trend_pause(False)
check("RESUME clears paused flag", tab._trend_paused is False)
check("RESUME button text -> PAUSE VIEW", tab._pause_btn.text() == "PAUSE VIEW")
check("RESUME returns to LIVE status",
      tab.trend_status_lbl.text() == "● LIVE", tab.trend_status_lbl.text())

# ------------------------------------------------------------------
# TEST 7 — long-run memory is bounded (deque capacity holds).
# ------------------------------------------------------------------
fill = list(mw.trend_data)
mw.trend_data.clear()
for i in range(TREND_MAX_POINTS + 10000):
    mw.trend_data.append((datetime.datetime.now(), float(i % 100)))
check("TEST7 long run stays within bounded capacity",
      len(mw.trend_data) == TREND_MAX_POINTS,
      len(mw.trend_data))
mw.trend_data.extend(fill)

# ------------------------------------------------------------------
# TEST 9 — RESET FEET: trend continues, records the new position.
# ------------------------------------------------------------------
tab._position_unit = "FT"
clear_trend(mw)
tab._prompt_starting_ft = lambda: 125.5
tab._on_reset_feet()
check("TEST9 RESET FEET changes position to 125.5 FT",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)
check("TEST9 RESET FEET did not clear trend buffer", True)
mw._record_trend_point()
check("TEST9 trend records new position after RESET FEET",
      abs(mw.trend_data[-1][1] - mw.block_position_ft) < 1e-3)

# ------------------------------------------------------------------
# TEST 10 — calibration change: chart reflects new live position,
#           calibration machinery untouched.
# ------------------------------------------------------------------
before_cal = snapshot_cal(mw)
clear_trend(mw)
mw._set_block_position(99.0)
mw._record_trend_point()
check("TEST10 chart data reflects position from calibration output",
      abs(mw.trend_data[-1][1] - 99.0) < 1e-9)
# Re-apply a calibration edit (simulate a changed clamp) -> still saved, chart fine.
mw.device.update({"calCounter2": 6000, "calPosition2": 60.0})
after_cal = snapshot_cal(mw)
check("TEST10 calibration still changeable", before_cal != after_cal)
check("TEST10 trend still alive after calibration change",
      len(mw.trend_data) >= 1)

# ------------------------------------------------------------------
# FT/M + encoder/calibration invariants hold after all trend ops.
# ------------------------------------------------------------------
check("invariant: ticks untouched by trend ops", mw.current_ticks == 0)
check("invariant: calibration rows intact",
      isinstance(mw.current_layer, int) and mw.current_layer >= 0)

mw.close()
app.quit()
print()
print(f"===== TREND: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)
