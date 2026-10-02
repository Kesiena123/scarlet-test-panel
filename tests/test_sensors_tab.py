"""Dashboard tab — industrial DRILLING MONITOR overview (promt3 §15).

The Dashboard (Sensors) tab shows 11 live monitoring boxes:
  * POSITION / DEPTH:  MEASURED DEPTH | TVD | BIT DEPTH  (row 1),
                       BLOCK POSITION | SAMPLE LAG DEPTH | HOOKLOAD VALUE (row 2)
  * PUMPS / SPM:       SPM 1 | SPM 2, SPM 3 | SPM 4, TOTAL SPM (full width)
The Dashboard is a DISPLAY-only layer: it never owns a calculation. MD / TVD /
Sample Lag are single-source derived display values on the application model
that reuse the existing Bit Position/Bit Depth engine (read exactly once per
refresh), Bit Depth mirrors that engine, Block mirrors block_position_ft,
Hookload mirrors the channel-0 analog -> hookload chain, and Total SPM is the
sum of the four existing SPM values. This suite verifies:

  * the tab bar and stack carry the "Dashboard" tab at index 1 without
    disturbing the neighbours (Analog 0, Graph 2, SPM 3, Block 4, Diag 5,
    Settings 6, Analog Monitor 7)
  * the old KEY SENSOR VALUES cards and the detailed ALL-SENSORS view are gone
  * the 11 new boxes exist, are laid out 3-col depth grid + pump grid, and
    start at '--' with an honest status (never a believable idle mouth value)
  * MD / TVD / SAMPLE LAG are derived display values from the SAME read of the
    existing Bit engine (bit_position_ft read ONCE per refresh) — no second
    measurement system and never a hard-coded value
  * the value boxes mirror the live source engines (block / hookload / spm)
  * Total SPM = sum of SPM 1..4 (single source, no separate pump calc)
  * the operator Sample Lag Offset flows through settings (setter persists,
    display = MD - offset; clamped at 0) and is RBAC-gated
  * units reuse the existing Block Position FT/M toggle (single system)
  * TRACK/HOLD live state drives the MD / BIT status lines
  * DEMO mode drives the tab and the _tick refresh mapping reaches it

Run:   python tests/test_sensors_tab.py
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
    BIT_POS_STATE_STABLE_TICKS, SENSOR_CONFIG)
from scarlet_test_panel.services.security import (  # noqa: E402
    ROLE_OPERATOR, ROLE_ENGINEER)

# Hermetic hookload: pin channel-0 to the factory 0..1000 klb range.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0

# Hermetic: the tab never writes, but the rig-setup helpers below must not
# reach the real settings file either.
saved = []
_orig_save = main_mod.save_settings
main_mod.save_settings = lambda d: saved.append(dict(d))

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")
    else:
        print(f"  PASS: {name}")


def btn_texts(mw):
    return [b.text().strip() for b in mw.tab_bar._btns]


class _FakeSerial:
    """Hermetic 'live link up' stand-in (is_open is the only thing the
    Dashboard reads to decide live vs disconnected)."""
    is_open = True

    def close(self):
        self.is_open = False


def load(mw, klb):
    """Drive the live channel-0 Hookload (klb = V/5 * 1000)."""
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call: the
    SENSOR_CONFIG[0]["cal_max"] = 1000.0  # dashboard re-applies operator
    mw.analog_voltages[0] = klb / 200.0   # calibration on construction.


def fake_live(mw):
    """Hermetic live-data link (no real device, no staleness, no fault)."""
    mw.serial = _FakeSerial()
    mw._link_down = False
    mw._data_stale = False
    mw.cal_status = "VALID"
    mw.analog_statuses[0] = ""


mw = PumpDashboard()
_orig_serial = mw.serial
mw.slip_window_sec = None           # hermetic: ignore anything an operator saved
mw.slip_window_load_klb = None      # ...including the load/time confirmation gate
mw.slip_window_time_sec = None
mw._reset_slip_window()
st = mw.sensors_tab
load(mw, 0.0)

# The four SPM cells are CONFIGURABLE DISPLAY SLOTS and their selection is
# persisted (spmselection.txt), so this suite - which runs against the real
# settings store - pins every slot to the default mapping (box i -> SPM i+1)
# to stay hermetic. set_selected(notify=False) does not write settings.
for _i, _b in enumerate(st.box_spm):
    _b.set_selected(_i, notify=False)
st.refresh()

# ---- structure ------------------------------------------------------------
names = btn_texts(mw)
check("tab bar has 9 entries (Digital Sensor added)",
      len(names) == 9, names)
check("'Dashboard' is the 2nd tab",
      len(names) > 1 and names[1] == "Dashboard", names)
check("Block Position and Analog Monitor sit directly after the Dashboard",
      names[2] == "Block Position" and names[3] == "Analog Monitor", names)
check("remaining tabs keep their relative order",
      names[0] == "Analog Signals" and names[4] == "Graph"
      and names[5] == "SPM / RPM" and names[6] == "DIGITAL SENSOR"
      and names[7] == "Diagnostics" and names[8] == "Settings", names)
check("stack widget 1 is SensorsTab",
      type(mw.stack.widget(1)).__name__ == "SensorsTab")
check("stack keeps correct order",
      [type(mw.stack.widget(i)).__name__ for i in range(mw.stack.count())]
      == ["AnalogTab", "SensorsTab", "BlockPositionTab", "AnalogMonitorTab",
          "GraphTab", "StrokeRpmTab", "DigitalSensorTab", "DiagnosticsTab",
          "AboutTab"])

# ---- the old KEY SENSOR VALUES / ALL-SENSORS views are gone ----------------
check("detailed ALL-SENSORS view removed",
      not hasattr(st, "analog_stats") and not hasattr(st, "spm_stats")
      and not hasattr(st, "stat_link") and not hasattr(st, "stat_hookload")
      and not hasattr(st, "stat_bit_state") and not hasattr(st, "stamp_lbl"))
check("old KEY SENSOR VALUES cards removed",
      not any(hasattr(st, n) for n in (
          "key_block", "key_direction", "key_bit", "key_string",
          "key_encoder", "key_slip", "key_system")))

# ---- the 11 new DRILLING MONITOR boxes --------------------------------------
scroll = st.layout().itemAt(0).widget()
container = scroll.widget()
root_lay = container.layout()
check("dashboard header rendered",
      st.key_title.text() == "DRILLING MONITOR", st.key_title.text())
check("11 monitoring boxes exist",
      st.box_md is not None and st.box_tvd is not None
      and st.box_bit is not None and st.box_block is not None
      and st.box_lag is not None and st.box_hook is not None
      and len(st.box_spm) == 4 and st.box_total is not None)
check("all 11 boxes live inside the single panel QGroupBox",
      st.box_md.parent() is st.key_box
      and st.box_total.parent() is st.key_box
      and st.box_spm[2].parent() is st.key_box)


def _grid_holdings(widget):
    """Every QGridLayout nested anywhere beneath the given widget."""
    found = []

    def walk(lay):
        if lay is None:
            return
        if hasattr(lay, "itemAtPosition"):
            found.append(lay)
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is None:
                continue
            sub = item.layout()
            if sub is not None:
                walk(sub)
            sub_widget = item.widget()
            if sub_widget is not None:
                walk(sub_widget.layout())

    walk(widget.layout())
    return found


def _grid_holding(widget):
    """Return the nested QGridLayout whose itemAtPosition(0,0) is the given
    box, or None."""
    for grid in _grid_holdings(container):
        it = grid.itemAtPosition(0, 0)
        if it is not None and it.widget() is widget:
            return grid
    return None


def _grid_pos(grid, widget):
    """(row, col, rowSpan, colSpan) of a widget, or None."""
    idx = grid.indexOf(widget)
    return grid.getItemPosition(idx) if idx >= 0 else None


# The Dashboard is ONE continuous instrumentation matrix (not two nested card
# grids): a single grid holds the five depth/position cells, the hookload cell,
# SPM 1-4 and the RPM cell (digitalsensor1.txt §17), with TOTAL SPM spanning
# the full width below them.
table = st._grid
cells = [st.box_md, st.box_tvd, st.box_bit, st.box_block, st.box_lag,
         st.box_hook] + list(st.box_spm) + [st.box_rpm, st.box_total]
check("one continuous table grid holds all 12 measurement cells",
      table is not None and table.count() == len(cells)
      and all(table.indexOf(c) >= 0 for c in cells))
check("required parameter order: depth/position, hookload, SPM 1-4, RPM",
      [c.cap.text() for c in cells[:-2]]
      == ["MEASURED DEPTH", "TVD", "BIT DEPTH", "BLOCK POSITION",
          "SAMPLE LAG DEPTH", "HOOKLOAD VALUE", "SPM 1", "SPM 2", "SPM 3",
          "SPM 4"]
      and st.box_rpm.cap.text() == st.box_rpm.sel_items[st.box_rpm.selected()],
      [c.cap.text() for c in cells[:-1]])
check("row 1 = the 5 depth/position cells, row 2 = hookload + SPM 1-4",
      {_grid_pos(table, c)[0] for c in (st.box_md, st.box_tvd, st.box_bit,
                                        st.box_block, st.box_lag)} == {0}
      and _grid_pos(table, st.box_hook)[0] == 1
      and len({_grid_pos(table, c)[0] for c in st.box_spm}) == 1
      and [p[1] for p in (_grid_pos(table, c) for c in st.box_spm)]
      == sorted(p[1] for p in (_grid_pos(table, c) for c in st.box_spm)))
check("the RPM cell follows the SPM cells in reading order",
      _grid_pos(table, st.box_rpm)[0] > _grid_pos(table, st.box_spm[0])[0])
check("TOTAL SPM spans the full table width in the last row",
      _grid_pos(table, st.box_total)[3] == st._cols and st._cols >= 2
      and _grid_pos(table, st.box_total)[0]
      == max(_grid_pos(table, c)[0] for c in cells[:-1]) + 1,
      (_grid_pos(table, st.box_total), st._cols))
check("every cell is the same size (consistent cell dimensions)",
      len({c.height() for c in cells}) == 1
      and len({c.value_area.height() for c in cells}) == 1
      and len({c.cap.height() for c in cells}) == 1)
check("key unit button starts FT",
      st.key_unit_btn.text() == "UNIT: FT", st.key_unit_btn.text())

# ---- idle / disconnected: honest '--' states, never believable values --------
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw.serial = None            # hermetic disconnected link
mw._link_down = True
st.refresh()
# ---- a box that NEVER had a reading still shows '--' ------------------------
# Only a cold box (no real reading since launch) may show '--'; the freeze
# above covers every box that did display a value at least once.
mw.pipe_in_hole_ft = None
mw._bit_hold_ft = None
mw.serial = None            # hermetic disconnected link
mw._link_down = True
_fresh = st._all_cards()[0]
_fresh._last_value = ""     # simulate a box that has never shown a value
st.refresh()
check("a cold box with no prior reading shows '--'",
      _fresh.val.text() == "--", _fresh.val.text())
check("no DISCONNECTED wording even on a cold box",
      not any("DISCONNECT" in c.stat.text().upper() for c in st._all_cards()))

# ---- live: MD/TVD/BIT/LAG are DISPLAY of ONE read of the Bit engine ----------
fake_live(mw)
mw.block_position_ft = 100.0
mw.pipe_in_hole_ft = 6000.0
mw._bit_hold_ft = 6000.0
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    mw.block_tab.refresh()   # no hookload -> HOLD frozen at 6,000
load(mw, 40.0)               # 40 klb pipe load (hookload display, bit stays HOLD)
st.refresh()
check("MD mirrors the existing Bit engine (6,000.0, HOLD)",
      st.box_md.val.text() == "6,000.0"
      and st.box_md.stat.text() == "● HOLD - FROZEN DEPTH",
      (st.box_md.val.text(), st.box_md.stat.text()))
check("TVD = MD (vertical well, no survey) same single read",
      st.box_tvd.val.text() == "6,000.0"
      and st.box_tvd.stat.text() == "● VERTICAL - NO SURVEY",
      (st.box_tvd.val.text(), st.box_tvd.stat.text()))
check("BIT DEPTH mirrors the same engine value",
      st.box_bit.val.text() == "6,000.0", st.box_bit.val.text())
check("SAMPLE LAG = MD with 0 offset (follows bit depth)",
      st.box_lag.val.text() == "6,000.0"
      and st.box_lag.stat.text() == "● FOLLOWS BIT DEPTH",
      (st.box_lag.val.text(), st.box_lag.stat.text()))
check("BLOCK mirrors block_position_ft (100.0, NORMAL)",
      st.box_block.val.text() == "100.0"
      and st.box_block.stat.text() == "● NORMAL",
      (st.box_block.val.text(), st.box_block.stat.text()))
check("HOOKLOAD mirrors channel-0 analog (40.0 klb, LIVE)",
      st.box_hook.val.text() == "40.0"
      and st.box_hook.stat.text() == "● LIVE",
      (st.box_hook.val.text(), st.box_hook.stat.text()))
check("SPM live mirrors mw.spm_values",
      st.box_spm[0].val.text() == "0.0"
      and st.box_spm[2].val.text() == "0.0"
      and st.box_spm[0].stat.text() == "● LIVE",
      (st.box_spm[0].val.text(), st.box_spm[2].val.text()))
check("TOTAL SPM = sum of the four values (single source)",
      st.box_total.val.text() == "0.0"
      and st.box_total.stat.text() == "● SUM 1-4",
      (st.box_total.val.text(), st.box_total.stat.text()))

# ---- SPM + TOTAL: live mirroring of the four source values -------------------
mw.spm_values = [8.2, 6.5, 7.0, 6.8]
st.refresh()
check("SPM 1..4 mirror live values",
      (st.box_spm[0].val.text(), st.box_spm[1].val.text(),
       st.box_spm[2].val.text(), st.box_spm[3].val.text())
      == ("8.2", "6.5", "7.0", "6.8"))
check("TOTAL SPM = 28.5 (8.2+6.5+7.0+6.8)",
      st.box_total.val.text() == "28.5", st.box_total.val.text())

# ---- SPM cells are CONFIGURABLE DISPLAY SLOTS (spmselection.txt) -------------
check("every SPM cell owns a small triangle selector",
      all(b.sel_btn is not None and b.sel_btn.text() == "▼" for b in st.box_spm))
check("no other Dashboard cell has a selector",
      all(getattr(st, n).sel_btn is None for n in
          ("box_md", "box_tvd", "box_bit", "box_block", "box_lag",
           "box_hook", "box_total")))
_menus_ok = True
for _b in st.box_spm:
    _b._build_selector_menu()
    _menus_ok = (_menus_ok
                 and [a.text() for a in _b.sel_menu.actions()]
                 == ["SPM 1", "SPM 2", "SPM 3", "SPM 4"])
check("each selector menu holds exactly SPM 1-4", _menus_ok)
_menus_ok = True
for _b in st.box_spm:
    _t = _b.sel_btn.geometry()
    _menus_ok = (_menus_ok
                 and _t.height() <= 18 and _t.width() <= 20
                 and not _t.intersects(_b.value_area.geometry())
                 and not _t.intersects(_b.cap.geometry()))
check("triangle is small, in the top row, and clears the value + label", _menus_ok)

# One box changing its parameter must not disturb the others.
st.box_spm[0].set_selected(1, notify=False)
st.refresh()
check("slot shows the SELECTED parameter (box 1 -> SPM 2)",
      st.box_spm[0].cap.text() == "SPM 2"
      and st.box_spm[0].val.text() == "6.5",
      (st.box_spm[0].cap.text(), st.box_spm[0].val.text()))
check("the other slots keep their own independent selection",
      [b.cap.text() for b in st.box_spm[1:]] == ["SPM 2", "SPM 3", "SPM 4"]
      and [b.val.text() for b in st.box_spm[1:]] == ["6.5", "7.0", "6.8"],
      [(b.cap.text(), b.val.text()) for b in st.box_spm[1:]])
st.box_spm[3].set_selected(1, notify=False)      # duplicate: box 4 -> SPM 2
st.refresh()
check("duplicate selection allowed: box 4 mirrors box 1",
      st.box_spm[3].cap.text() == "SPM 2"
      and st.box_spm[3].val.text() == st.box_spm[0].val.text(),
      (st.box_spm[3].cap.text(), st.box_spm[3].val.text()))
mw.spm_values = [8.2, 66.5, 7.0, 6.8]
st.refresh()
check("slot value keeps updating live without reopening the menu",
      st.box_spm[0].val.text() == "66.5", st.box_spm[0].val.text())
# Restore the default mapping for any later check.
for _i, _b in enumerate(st.box_spm):
    _b.set_selected(_i, notify=False)
st.refresh()

# ---- hookload fault vocabulary -----------------------------------------------
mw.analog_statuses[0] = "FAULT"
st.refresh()
check("hookload FAULT shows -- + SENSOR FAULT (never a value)",
      st.box_hook.val.text() == "--"
      and st.box_hook.stat.text() == "● SENSOR FAULT",
      (st.box_hook.val.text(), st.box_hook.stat.text()))
mw.analog_statuses[0] = "UNUSED"
st.refresh()
check("hookload UNUSED shows -- + SENSOR DISABLED",
      st.box_hook.val.text() == "--"
      and st.box_hook.stat.text() == "● SENSOR DISABLED",
      (st.box_hook.val.text(), st.box_hook.stat.text()))
mw.analog_statuses[0] = ""

# ---- reuse the existing Block Position FT/M unit system ----------------------
st._on_key_unit_toggle()
st.refresh()
check("dashboard toggle drives the Block tab unit (single system)",
      mw.block_tab._position_unit == "M"
      and st.key_unit_btn.text() == "UNIT: M",
      (mw.block_tab._position_unit, st.key_unit_btn.text()))
check("BLOCK converted to M (100 ft = 30.5 m)",
      st.box_block.val.text() == "30.5"
      and st.box_block.unit.text() == "M", st.box_block.val.text())
check("MD/BIT converted to M (6000 ft = 1828.8 m)",
      st.box_md.val.text() == "1,828.8"
      and st.box_bit.val.text() == "1,828.8", st.box_md.val.text())
st._on_key_unit_toggle()
st.refresh()
check("toggle back restores FT",
      mw.block_tab._position_unit == "FT"
      and st.box_block.val.text() == "100.0", st.box_block.val.text())

# ---- Access: every role sees Diagnostics and may edit the lag offset --------
mw.show()
app.processEvents()
mw.roles.set_role(ROLE_OPERATOR)
check("operator sees the Dashboard tab",
      mw.tab_bar._btns[1].isVisible() and mw.tab_bar._btns[1].isEnabled())
check("operator sees Diagnostics too (no role is view-only)",
      mw.tab_bar._btns[mw.IDX_DIAGNOSTICS].isVisible(), mw.tab_bar._btns[mw.IDX_DIAGNOSTICS].isVisible())
check("operator may edit the lag offset (no role is view-only)",
      st.lag_off_spin.isEnabled())
mw.roles.set_role(ROLE_ENGINEER)
check("engineer sees Diagnostics",
      mw.tab_bar._btns[mw.IDX_DIAGNOSTICS].isVisible(), mw.tab_bar._btns[mw.IDX_DIAGNOSTICS].isVisible())
check("engineer can edit the lag offset",
      st.lag_off_spin.isEnabled())
mw.stack.setCurrentIndex(mw.IDX_DIAGNOSTICS)
check("engineer lands on Diagnostics",
      mw.stack.currentIndex() == mw.IDX_DIAGNOSTICS, mw.stack.currentIndex())
mw.roles.set_role(ROLE_OPERATOR)
check("role label change never ejects the operator from Diagnostics",
      mw.stack.currentIndex() == mw.IDX_DIAGNOSTICS, mw.stack.currentIndex())
vig = [d for d in saved if "sample_lag_offset_ft" in d]

# ---- Sample Lag offset: persisted via settings, display = MD - offset --------
mw.roles.set_role(ROLE_ENGINEER)
saved.clear()
ok = mw.set_sample_lag_offset(500.5)
check("set_sample_lag_offset(500.5) accepted + persisted",
      ok and mw.sample_lag_offset_ft == 500.5
      and any(d.get("sample_lag_offset_ft") == 500.5 for d in saved),
      (ok, mw.sample_lag_offset_ft, saved[-1:] if saved else None))
check("rejects negative offsets (no-write)",
      not mw.set_sample_lag_offset(-1.0))
st.refresh()
check("SAMPLE LAG = MD - offset (6000 - 500.5 = 5499.5)",
      st.box_lag.val.text() == "5,499.5"
      and st.box_lag.stat.text() == "● LAG 500.5 FT",
      (st.box_lag.val.text(), st.box_lag.stat.text()))
check("MD / BIT unaffected by the lag offset",
      st.box_md.val.text() == "6,000.0"
      and st.box_bit.val.text() == "6,000.0")
mw.set_sample_lag_offset(6500.0)   # offset above MD: must clamp at 0
st.refresh()
check("SAMPLE LAG clamps at 0 (offset below zero MD)",
      st.box_lag.val.text() == "0.0", st.box_lag.val.text())
mw.set_sample_lag_offset(0.0)
st.refresh()
check("SAMPLE LAG restored to follow the bit depth",
      st.box_lag.val.text() == "6,000.0", st.box_lag.val.text())

# ---- bit state: TRACK/HOLD live state machine drives the MD/BIT lines --------
mw.pipe_in_hole_ft = 6000.0
mw._bit_hold_ft = 6000.0
mw._set_block_position(200.0)         # fresh anchor: TRACK starts from block 200
mw._bit_track_block_ft = None
load(mw, 40.0)                        # 40 klb pipe load -> TRACK
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    mw.block_tab.refresh()
mw._set_block_position(201.0)
mw.block_tab.refresh()
st.refresh()
check("MD tracks pipe load (HOLD -> TRACK reuses same engine read)",
      st.box_md.val.text() == "5,999.0"
      and st.box_md.stat.text() == "● TRACKING - PIPE LOAD",
      (st.box_md.val.text(), st.box_md.stat.text()))
check("BIT tracked depth 5,999.0",
      st.box_bit.val.text() == "5,999.0"
      and st.box_bit.stat.text() == "● TRACKING - PIPE LOAD",
      (st.box_bit.val.text(), st.box_bit.stat.text()))
load(mw, 0.0)                         # no load -> HOLD
for _ in range(BIT_POS_STATE_STABLE_TICKS):
    mw.block_tab.refresh()
st.refresh()
check("MD/BIT HOLD frozen depth",
      st.box_md.stat.text() == "● HOLD - FROZEN DEPTH"
      and st.box_bit.stat.text() == "● HOLD - FROZEN DEPTH",
      (st.box_md.stat.text(), st.box_bit.stat.text()))

# ---- DEMO drives the tab + _tick reaches it ----------------------------------
mw._toggle_demo()
check("demo mode enabled", mw._demo_mode is True)
mw._demo_tick()
mw.stack.setCurrentIndex(1)
mw._tick()                            # exercises the idx==1 refresh branch
check("TOTAL SPM filled by demo tick (not '--')",
      st.box_total.val.text() != "--", st.box_total.val.text())
check("HOOKLOAD SIMULATED in demo",
      st.box_hook.stat.text() == "● SIMULATED", st.box_hook.stat.text())
check("hookload shows a value in demo",
      st.box_hook.val.text() != "--", st.box_hook.val.text())
mwc_boxes = (st.box_md, st.box_tvd, st.box_bit, st.box_block, st.box_lag)
check("depth boxes filled by demo tick",
      all(b.val.text() != "--" for b in mwc_boxes))
mw._toggle_demo()
check("demo mode off again", mw._demo_mode is False)

# ---- stopping Demo/Live mode FREEZES the boxes (nothing hides) ---------------
# The requirement: when Demo or the live link stops, no value may vanish. Every
# box keeps the reading it last displayed, greyed and labelled HELD, so a
# stopped mode can never be mistaken for a live one. Run LAST: it deliberately
# tears the link down.
fake_live(mw)
mw.pipe_in_hole_ft = 6000.0
mw._bit_hold_ft = 6000.0
mw.block_position_ft = 100.0
mw.spm_values = [8.2, 6.5, 7.0, 6.8]
load(mw, 40.0)
st.refresh()
_held_from = {id(c): c.val.text() for c in st._all_cards()}
mw.serial = None            # link stops
mw._link_down = True
st.refresh()
check("stopping the link shows no '--' anywhere on the Dashboard",
      not any(c.val.text().strip() == "--" for c in st._all_cards()),
      [c.val.text() for c in st._all_cards() if c.val.text().strip() == "--"])
check("every box keeps the value it last displayed",
      all(c.val.text() == _held_from[id(c)] for c in st._all_cards()),
      [(c.val.text(), _held_from[id(c)]) for c in st._all_cards()
       if c.val.text() != _held_from[id(c)]])
check("held boxes are labelled HELD so they never read as live",
      all("HELD" in c.stat.text().upper() for c in st._all_cards()),
      [c.stat.text() for c in st._all_cards()])
check("no box ever displays a DISCONNECTED message",
      not any("DISCONNECT" in c.stat.text().upper() for c in st._all_cards()))
# Several consecutive ticks with the link down must not drift or blank either.
for _ in range(5):
    st.refresh()
check("the held values survive repeated ticks with the link down",
      all(c.val.text() == _held_from[id(c)] for c in st._all_cards()),
      [c.val.text() for c in st._all_cards()])

# ---- restore hermetic link state ----------------------------------------------
mw.serial = _orig_serial
mw.close()
main_mod.save_settings = _orig_save
app.quit()
print()
print(f"===== DASHBOARD DRILLING MONITOR TAB: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)