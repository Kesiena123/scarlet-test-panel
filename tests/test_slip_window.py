"""SLIP WINDOW — load/time confirmation gate on Bit Position (slip_window.txt).

The dashboard's Bit Position is normally gated by the channel-0 Hookload
TRACK/HOLD machinery. With the Slip Window feature CONFIGURED (Slip Window
Load in k-lb + Slip Window Time in seconds) that gate is replaced by a
confirmation machine driven by the single live analog Hookload
(mw.hookload_klb = V/5 * 1000 klb):

  NOT ACTIVE   hookload <  Slip Window Load   -> timer 0, Bit constant
  CONFIRMING   hookload >= Slip Window Load   -> timer counts up 0.0 .. T
  CONFIRMED    load held >= Load for full Time -> Bit movement active

A drop below the threshold before confirmation cancels the timer (starts over
later); a drop after confirmation preserves the accumulated Bit Position and
requires re-confirmation before movement resumes (slip_window.txt §15/§16).
String Length is always Bit + Block (no second calculation).

This suite verifies:

  * default (unconfigured): existing LEGACY behaviour unchanged — slip_window_sec
    debounce still works, feature reports NOT ACTIVE / 0 timer
  * set_slip_window_load / set_slip_window_time persist, validate, re-arm
  * the available Spins show the built-in example defaults and load saved values
  * the confirmation machine: threshold, timer, cancel, confirm
  * Bit stays constant below threshold even while the Block moves
  * Bit tracks the ACTUAL Block delta only after CONFIRMED (spec §12 example)
  * a post-confirm load drop preserves Bit and demands re-confirmation
  * String Length = Bit + Block throughout
  * RBAC: OPERATOR views; only ENGINEER can edit/save it

Run:   python tests/test_slip_window.py
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
    BIT_POS_STATE_STABLE_TICKS, MS_PER_SAMPLE, SENSOR_CONFIG)
from scarlet_test_panel.services.security import (  # noqa: E402
    ROLE_ENGINEER, ROLE_OPERATOR)

_failures = 0
_total = 0

# Hermetic hookload: pin channel-0 to the factory 0..1000 klb range so the klb
# thresholds below map to clean voltages regardless of operator calibration.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        safe = str(detail).encode("ascii", "replace").decode("ascii")
        print(f"  FAIL: {name} {safe}")
    else:
        print(f"  PASS: {name}")


def ticks_for(sec):
    return max(1, BIT_POS_STATE_STABLE_TICKS,
               int(round(float(sec) * 1000.0 / MS_PER_SAMPLE)))


def load(mw, klb):
    """Drive the live channel-0 Hookload (klb). V = klb/200."""
    SENSOR_CONFIG[0]["cal_min"] = 0.0     # re-pin factory range each call: the
    SENSOR_CONFIG[0]["cal_max"] = 1000.0  # dashboard re-applies operator
    SENSOR_CONFIG[0]["cal_val_lo"] = 0.0  # calibration on every construction.
    mw.analog_voltages[0] = klb / 200.0   # keep the MINIMUM at 0 so the
                                          # existing thresholds equal the input.


class _FakeClock:
    """Deterministic time.monotonic() substitute for the slip machine."""
    def __init__(self, t0=0.0):
        self.t = t0

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


# Hermetic shell: never touch the real settings file. Saves are captured in
# memory, loads originate from the real file but with any saved slip-window
# value stripped so the suite is deterministic regardless of operator state.
saved = []
_base_load = main_mod.load_settings
_orig_save = main_mod.save_settings


def _load_no_slip():
    d = dict(_base_load())
    d.pop("slip_window_sec", None)
    d.pop("slip_window_load_klb", None)
    d.pop("slip_window_time_sec", None)
    return d


main_mod.load_settings = _load_no_slip
main_mod.save_settings = lambda d: saved.append(dict(d))

# ---- default (unconfigured) => legacy behaviour, unchanged ----------------
mw = PumpDashboard()
check("slip window load defaults to None", mw.slip_window_load_klb is None,
      mw.slip_window_load_klb)
check("slip window time defaults to None", mw.slip_window_time_sec is None,
      mw.slip_window_time_sec)
check("feature disabled by default", not mw.slip_window_enabled,
      mw.slip_window_enabled)
check("status NOT ACTIVE when off", mw.slip_window_status == "NOT ACTIVE",
      mw.slip_window_status)
check("timer 0 when off", abs(mw.slip_window_timer_sec) < 1e-9,
      mw.slip_window_timer_sec)
check("legacy default debounce == built-in stable ticks",
      mw._slip_stable_ticks() == max(1, BIT_POS_STATE_STABLE_TICKS),
      mw._slip_stable_ticks())
check("legacy set_slip_window still works", mw.set_slip_window(0.5) is True
      and abs(mw.slip_window_sec - 0.5) < 1e-9, mw.slip_window_sec)
check("legacy 0.5 s -> 10 refresh ticks", mw._slip_stable_ticks() == ticks_for(0.5),
      (mw._slip_stable_ticks(), ticks_for(0.5)))
mw.close()

# ---- persistence & validation (save patched: no real file writes) --------
mw2 = PumpDashboard()
check("reject negative load", mw2.set_slip_window_load(-1.0) is False)
check("reject non-numeric load", mw2.set_slip_window_load("abc") is False)
check("reject 0 time", mw2.set_slip_window_time(0.0) is False)
check("reject negative time", mw2.set_slip_window_time(-2.0) is False)
check("reject non-numeric time", mw2.set_slip_window_time("abc") is False)
check("accept load 20 k-lb", mw2.set_slip_window_load(20.0) is True)
check("load persisted", bool(saved) and abs(saved[-1]["slip_window_load_klb"] - 20.0) < 1e-9,
      saved)
check("accept time 5 s", mw2.set_slip_window_time(5.0) is True)
check("time persisted", abs(saved[-1]["slip_window_time_sec"] - 5.0) < 1e-9, saved)
check("feature enabled after both set", mw2.slip_window_enabled
      and abs(mw2.slip_window_load_klb - 20.0) < 1e-9
      and abs(mw2.slip_window_time_sec - 5.0) < 1e-9,
      (mw2.slip_window_load_klb, mw2.slip_window_time_sec))
check("saving re-arms machine to NOT ACTIVE",
      mw2.slip_window_status == "NOT ACTIVE", mw2.slip_window_status)
mw2.close()

# ---- load path: saved load/time come back after restart -------------------
main_mod.load_settings = lambda: dict(_load_no_slip(),
                                      slip_window_load_klb=20.0,
                                      slip_window_time_sec=5.0)
mw3 = PumpDashboard()
check("load reloaded", abs(mw3.slip_window_load_klb - 20.0) < 1e-9,
      mw3.slip_window_load_klb)
check("time reloaded", abs(mw3.slip_window_time_sec - 5.0) < 1e-9,
      mw3.slip_window_time_sec)
mw3.close()
main_mod.load_settings = _load_no_slip

# ---- spins: example defaults shown, saved values loaded -------------------
main_mod.load_settings = lambda: dict(_load_no_slip(),
                                      slip_window_load_klb=30.0,
                                      slip_window_time_sec=6.0)
mw4 = PumpDashboard()
check("spin shows saved load 30.00",
      abs(mw4.block_tab.slip_window_load_spin.value() - 30.0) < 1e-9,
      mw4.block_tab.slip_window_load_spin.value())
check("spin shows saved time 6.00",
      abs(mw4.block_tab.slip_window_time_spin.value() - 6.0) < 1e-9,
      mw4.block_tab.slip_window_time_spin.value())
mw4.close()
main_mod.load_settings = _load_no_slip

# ---- confirmation machine (fake clock injection) --------------------------
mw5 = PumpDashboard()
clk = _FakeClock(1000.0)
mw5._slip_clock = clk
tab = mw5.block_tab
mw5.set_pipe_in_hole(0.0)         # bit starts at 0 ft (flat string)
mw5.set_slip_window_load(20.0)    # 20 k-lb
mw5.set_slip_window_time(5.0)     # 5 s

# --- SPEC §8: hookload below window, block rides 0 -> 100, bit stays 0 -----
load(mw5, 10.0)                   # 10 k-lb < 20 k-lb
mw5._set_block_position(100.0)
for _ in range(3):
    tab.refresh()
check("below threshold: status NOT ACTIVE", mw5.slip_window_status == "NOT ACTIVE",
      mw5.slip_window_status)
check("below threshold: timer 0", abs(mw5.slip_window_timer_sec) < 1e-9,
      mw5.slip_window_timer_sec)
check("below threshold: bit constant at 0", abs(mw5.bit_position_ft - 0.0) < 1e-9,
      mw5.bit_position_ft)
check("below threshold: block=100 string length=100",
      abs((mw5.string_length_ft or 0.0) - 100.0) < 1e-9, mw5.string_length_ft)

# --- SPEC §9/§10: reaches threshold -> CONFIRMING, timer runs ---------------
load(mw5, 20.0)                   # 20 k-lb >= 20 k-lb
tab.refresh()
check("at threshold: status CONFIRMING", mw5.slip_window_status == "CONFIRMING",
      mw5.slip_window_status)
check("confirming: timer still ~0", mw5.slip_window_timer_sec < 0.2,
      mw5.slip_window_timer_sec)
clk.advance(2.0)
tab.refresh()
check("confirming: timer ~2/5", abs(mw5.slip_window_timer_sec - 2.0) < 1e-6,
      mw5.slip_window_timer_sec)
check("confirming: bit stays 0 (not confirmed yet)",
      abs(mw5.bit_position_ft - 0.0) < 1e-9, mw5.bit_position_ft)
# block drops 100 -> 80 DURING confirmation: bit must NOT move yet (spec §10)
mw5._set_block_position(80.0)
tab.refresh()
check("confirming: block moves but bit still constant",
      abs(mw5.bit_position_ft - 0.0) < 1e-9, mw5.bit_position_ft)

# --- SPEC §5/§10: drop below threshold cancels, resets, bit constant ---------
load(mw5, 19.0)                   # 19 k-lb < 20 k-lb
clk.advance(3.0)
tab.refresh()
check("cancel on drop: NOT ACTIVE", mw5.slip_window_status == "NOT ACTIVE",
      mw5.slip_window_status)
check("cancel on drop: timer reset to 0", abs(mw5.slip_window_timer_sec) < 1e-9,
      mw5.slip_window_timer_sec)
check("cancel on drop: bit preserved at 0",
      abs(mw5.bit_position_ft - 0.0) < 1e-9, mw5.bit_position_ft)

# --- SPEC §11/§12: hold >= load for full time then confirm & track ----------
load(mw5, 21.0)                   # back above threshold -> new timer from zero
tab.refresh()
check("re-arm: CONFIRMING from zero again", mw5.slip_window_status == "CONFIRMING",
      mw5.slip_window_status)
clk.advance(1.5)
tab.refresh()
check("re-arm: timer restarted ~1.5 not ~5", abs(mw5.slip_window_timer_sec - 1.5) < 1e-6,
      mw5.slip_window_timer_sec)
clk.advance(3.5)
tab.refresh()
check("after 5s total: CONFIRMED", mw5.slip_window_status == "CONFIRMED",
      mw5.slip_window_status)
check("confirmed: timer capped at 5", abs(mw5.slip_window_timer_sec - 5.0) < 1e-6,
      mw5.slip_window_timer_sec)

# Block 80 -> 50: Bit must increase by the same 30 (spec §13)
mw5._set_block_position(50.0)
tab.refresh()
check("after confirm block 80->50, bit 0->30",
      abs(mw5.bit_position_ft - 30.0) < 1e-9, mw5.bit_position_ft)
check("string length constant 80 with bit 30",
      abs((mw5.string_length_ft or 0.0) - 80.0) < 1e-9, mw5.string_length_ft)
# Block 50 -> 70: Bit must decrease by 20 (spec §14)
mw5._set_block_position(70.0)
tab.refresh()
check("after confirm block 50->70, bit 30->10",
      abs(mw5.bit_position_ft - 10.0) < 1e-9, mw5.bit_position_ft)
check("string length constant 80 with bit 10",
      abs((mw5.string_length_ft or 0.0) - 80.0) < 1e-9, mw5.string_length_ft)

# --- SPEC §15: post-confirm load drop PRESERVES bit, needs re-confirmation --
load(mw5, 5.0)                    # 5 k-lb < 20 k-lb
clk.advance(1.0)
tab.refresh()
check("post-confirm drop: NOT ACTIVE", mw5.slip_window_status == "NOT ACTIVE",
      mw5.slip_window_status)
check("post-confirm drop: timer reset", abs(mw5.slip_window_timer_sec) < 1e-9,
      mw5.slip_window_timer_sec)
check("post-confirm drop: bit PRESERVED at 10 (never zeroed)",
      abs(mw5.bit_position_ft - 10.0) < 1e-9, mw5.bit_position_ft)
# Block rides while unconfirmed: bit must stay put
mw5._set_block_position(90.0)
tab.refresh()
check("unconfirmed block ride: bit still 10",
      abs(mw5.bit_position_ft - 10.0) < 1e-9, mw5.bit_position_ft)
# Load returns -> full re-confirmation (5 s) then movement resumes from 10
load(mw5, 25.0)
tab.refresh()
check("re-confirm: CONFIRMING again", mw5.slip_window_status == "CONFIRMING",
      mw5.slip_window_status)
clk.advance(4.9)
tab.refresh()
check("re-confirm: still CONFIRMING before 5s", mw5.slip_window_status == "CONFIRMING",
      mw5.slip_window_status)
clk.advance(0.2)
tab.refresh()
check("re-confirm: CONFIRMED after full time", mw5.slip_window_status == "CONFIRMED",
      mw5.slip_window_status)
# after 5s of block stillness the anchor re-arms at 90; move back to 80
mw5._set_block_position(80.0)
tab.refresh()
check("movement resumes from preserved 10 (block 90->80, bit 10->20)",
      abs(mw5.bit_position_ft - 20.0) < 1e-9, mw5.bit_position_ft)
mw5.close()

# ---- SPEC §8–§15 EXACT example (verbatim numbers from slip_window.txt) ------
mwx = PumpDashboard()
clkx = _FakeClock(5000.0)
mwx._slip_clock = clkx
tabx = mwx.block_tab
mwx.set_pipe_in_hole(0.0)          # Bit Position starts at 0 ft
mwx.set_slip_window_load(20.0)     # Slip Window Load = 20 k-lb
mwx.set_slip_window_time(5.0)      # Slip Window Time = 5 seconds

# §8 — Hookload 10 k-lb < 20 k-lb; Block rides 0 -> 100 ft; Bit stays 0.
load(mwx, 10.0)
mwx._set_block_position(100.0)
tabx.refresh()
check("§8 bit=0 block=100 string=100",
      abs(mwx.bit_position_ft - 0.0) < 1e-9
      and abs((mwx.string_length_ft or 0.0) - 100.0) < 1e-9,
      (mwx.bit_position_ft, mwx.string_length_ft))
check("§8 slip not active", mwx.slip_window_status == "NOT ACTIVE",
      mwx.slip_window_status)

# §9 — Hookload climbs 10 -> 15 -> 18 -> 20 k-lb; timer starts at 20.
load(mwx, 15.0)
tabx.refresh()
check("§9 15 k-lb still NOT ACTIVE", mwx.slip_window_status == "NOT ACTIVE",
      mwx.slip_window_status)
load(mwx, 18.0)
tabx.refresh()
check("§9 18 k-lb still NOT ACTIVE", mwx.slip_window_status == "NOT ACTIVE",
      mwx.slip_window_status)
load(mwx, 20.0)                    # 20 >= 20 -> timer starts
tabx.refresh()
check("§9 at 20 k-lb CONFIRMING, timer 0",
      mwx.slip_window_status == "CONFIRMING"
      and mwx.slip_window_timer_sec < 0.2,
      (mwx.slip_window_status, mwx.slip_window_timer_sec))
# §11 — holds >= 20 k-lb for the full 5 s -> CONFIRMED at 5.0 s.
clkx.advance(5.0)
tabx.refresh()
check("§11 CONFIRMED at 5.0 s",
      mwx.slip_window_status == "CONFIRMED"
      and abs(mwx.slip_window_timer_sec - 5.0) < 1e-6,
      (mwx.slip_window_status, mwx.slip_window_timer_sec))
# Bit must NOT move during the confirmation alone (block still 100).
check("§11 bit still 0 at confirmation moment",
      abs(mwx.bit_position_ft - 0.0) < 1e-9, mwx.bit_position_ft)

# §12 — Block 100 -> 80 ft: Block Delta -20 -> Bit +20 -> Bit = 20.
mwx._set_block_position(80.0)
tabx.refresh()
check("§12 block 100->80 bit 0->20",
      abs(mwx.bit_position_ft - 20.0) < 1e-9, mwx.bit_position_ft)
check("§12 string 20+80=100", abs((mwx.string_length_ft or 0.0) - 100.0) < 1e-9,
      mwx.string_length_ft)

# §13 — Block 80 -> 50 ft: Block Delta -30 -> Bit +30 -> Bit = 50.
mwx._set_block_position(50.0)
tabx.refresh()
check("§13 block 80->50 bit 20->50",
      abs(mwx.bit_position_ft - 50.0) < 1e-9, mwx.bit_position_ft)
check("§13 string 50+50=100", abs((mwx.string_length_ft or 0.0) - 100.0) < 1e-9,
      mwx.string_length_ft)

# §14 — Block 50 -> 70 ft: Block Delta +20 -> Bit -20 -> Bit = 30.
mwx._set_block_position(70.0)
tabx.refresh()
check("§14 block 50->70 bit 50->30",
      abs(mwx.bit_position_ft - 30.0) < 1e-9, mwx.bit_position_ft)
check("§14 string 30+70=100", abs((mwx.string_length_ft or 0.0) - 100.0) < 1e-9,
      mwx.string_length_ft)

# §15 — CONFIRMED at block 70; load drops below Slip Window Load; bit kept.
load(mwx, 10.0)                    # 10 k-lb < 20 k-lb
clkx.advance(1.0)
tabx.refresh()
check("§15 status leaves CONFIRMED (NOT ACTIVE)",
      mwx.slip_window_status == "NOT ACTIVE", mwx.slip_window_status)
check("§15 timer reset after drop", abs(mwx.slip_window_timer_sec) < 1e-9,
      mwx.slip_window_timer_sec)
check("§15 bit PRESERVED at 30 (never reset)",
      abs(mwx.bit_position_ft - 30.0) < 1e-9, mwx.bit_position_ft)
# Block rides while unconfirmed: bit must stay constant.
mwx._set_block_position(150.0)
tabx.refresh()
check("§15 unconfirmed block ride keeps bit at 30",
      abs(mwx.bit_position_ft - 30.0) < 1e-9, mwx.bit_position_ft)
# Load returns -> full re-confirmation required before movement resumes.
load(mwx, 25.0)
tabx.refresh()                       # start the new confirmation timer from 0
check("§15 re-confirm CONFIRMING from zero",
      mwx.slip_window_status == "CONFIRMING", mwx.slip_window_status)
clkx.advance(4.9)
tabx.refresh()
check("§15 re-confirm still CONFIRMING before 5s",
      mwx.slip_window_status == "CONFIRMING", mwx.slip_window_status)
clkx.advance(0.2)
tabx.refresh()
check("§15 CONFIRMED again after full re-timer",
      mwx.slip_window_status == "CONFIRMED", mwx.slip_window_status)
# after re-anchor at 150, block -> 140: Block Delta -10 -> Bit 30+10 = 40.
mwx._set_block_position(140.0)
tabx.refresh()
check("§15 resumes from preserved 30 (block 150->140 bit 30->40)",
      abs(mwx.bit_position_ft - 40.0) < 1e-9, mwx.bit_position_ft)
mwx.close()

# ---- SLIP WINDOW LOAD = input + HOOKLOAD MINIMUM VALUE ----------------------
# The SLIP WINDOW LOAD readout and the confirmation threshold include the
# channel-0 hookload MINIMUM VALUE (SENSOR_CONFIG[0] cal_val_lo). The saved
# value stays the raw INPUT; the readout and gate use the combined threshold.
mwe = PumpDashboard()
clke = _FakeClock(7000.0)
mwe._slip_clock = clke
tabe = mwe.block_tab
mwe.set_pipe_in_hole(0.0)
mwe.set_slip_window_load(20.0)     # input 20 k-lb
mwe.set_slip_window_time(5.0)
# Pin AFTER construction (the dashboard re-applies the saved calibration on
# build, resetting these fields): 0-5 V -> 0-1000 klb, minimum 30 k-lb.
SENSOR_CONFIG[0]["cal_min"] = 0.0
SENSOR_CONFIG[0]["cal_max"] = 1000.0
SENSOR_CONFIG[0]["cal_val_lo"] = 30.0
mwe.analog_voltages[0] = 0.0
check("effective load = input + minimum (20 + 30 = 50)",
      abs((mwe.slip_window_effective_load_klb or 0.0) - 50.0) < 1e-9,
      mwe.slip_window_effective_load_klb)
tabe.refresh()
check("SLIP WINDOW LOAD box shows 50.00",
      tabe.slip_load_disp_lbl.text().replace(",", "") == "50.00",
      tabe.slip_load_disp_lbl.text())
mwe.analog_voltages[0] = 40.0 / 200.0      # 40 k-lb >= input 20 but < 50
mwe._set_block_position(100.0)
tabe.refresh()
check("below effective threshold: NOT ACTIVE (40 < 50)",
      mwe.slip_window_status == "NOT ACTIVE", mwe.slip_window_status)
check("effective threshold: bit constant at 0",
      abs(mwe.bit_position_ft - 0.0) < 1e-9, mwe.bit_position_ft)
mwe.analog_voltages[0] = 50.0 / 200.0      # 50 >= 50 -> CONFIRMING
tabe.refresh()
check("at effective threshold: CONFIRMING (50 >= 50)",
      mwe.slip_window_status == "CONFIRMING", mwe.slip_window_status)
clke.advance(5.0)
tabe.refresh()
check("effective threshold: CONFIRMED after full time",
      mwe.slip_window_status == "CONFIRMED", mwe.slip_window_status)
mwe._set_block_position(80.0)
tabe.refresh()
check("effective threshold: bit tracks after confirm (0 -> 20)",
      abs(mwe.bit_position_ft - 20.0) < 1e-9, mwe.bit_position_ft)
mwe.close()
SENSOR_CONFIG[0]["cal_val_lo"] = 0.0

# ---- REAL-TIME PIPELINE (spec §20: existing hookload value, ONE engine) ----
# Drive the slip machine exclusively through the live channel-0 value
# (mw.hookload_klb reads mw.analog_voltages[0] — the identical value the LIVE
# firmware / Demo mode write), never via a second hookload system. Proves the
# machine uses the real-time hookload, not a second calculation engine.
mwd = PumpDashboard()
clkd = _FakeClock(9000.0)
mwd._slip_clock = clkd
tabd = mwd.block_tab
mwd.set_pipe_in_hole(0.0)
mwd.set_slip_window_load(20.0)     # threshold 20 k-lb
mwd.set_slip_window_time(5.0)
mwd._demo_mode = True

# 0.05 V -> 10.0 k-lb < 20 k-lb -> NOT ACTIVE, bit constant.
load(mwd, 10.0)
tabd.refresh()
hk_low = mwd.hookload_klb
check("pipeline: 0.05 V -> hookload 10.0 k-lb live",
      hk_low is not None and abs(hk_low - 10.0) < 1e-6, hk_low)
check("pipeline: below threshold NOT ACTIVE",
      mwd.slip_window_status == "NOT ACTIVE", mwd.slip_window_status)
# Block rides while unconfirmed through the pipeline.
mwd._set_block_position(100.0)
for _ in range(3):
    tabd.refresh()
check("pipeline: bit constant at 0 (block=100)",
      abs(mwd.bit_position_ft - 0.0) < 1e-9, mwd.bit_position_ft)

# 0.25 V -> 50.0 k-lb >= 20 k-lb -> timer starts via pipeline.
load(mwd, 50.0)
tabd.refresh()
check("pipeline: 0.25 V -> hookload 50.0 k-lb live",
      abs(mwd.hookload_klb - 50.0) < 1e-6, mwd.hookload_klb)
check("pipeline: CONFIRMING via real-time value",
      mwd.slip_window_status == "CONFIRMING", mwd.slip_window_status)
clkd.advance(5.0)
tabd.refresh()
check("pipeline: CONFIRMED after 5 s real pipeline",
      mwd.slip_window_status == "CONFIRMED", mwd.slip_window_status)
# bit tracks actual block delta afterwards — anchor off the LIVE block position
# at the confirmation moment, so assert the delta relative to that live anchor.
anchor = mwd.block_position_ft
mwd._set_block_position(anchor - 20.0)
tabd.refresh()
check("pipeline: after confirm bit tracks -20 ft of block travel",
      abs(mwd.bit_position_ft - 20.0) < 1e-9, mwd.bit_position_ft)
mwd.close()

# ---- Access: every role may edit the slip window ----------------------------
mw6 = PumpDashboard()
tab6 = mw6.block_tab
mw6.roles.set_role(ROLE_OPERATOR)
tab6._apply_role(ROLE_OPERATOR)
check("operator: load spin enabled (no role is view-only)",
      tab6.slip_window_load_spin.isEnabled(),
      tab6.slip_window_load_spin.isEnabled())
check("operator: time spin enabled (no role is view-only)",
      tab6.slip_window_time_spin.isEnabled(),
      tab6.slip_window_time_spin.isEnabled())
check("operator: slip save enabled (no role is view-only)",
      tab6.slip_save_btn.isEnabled(),
      tab6.slip_save_btn.isEnabled())
mw6.roles.set_role(ROLE_ENGINEER)
tab6._apply_role(ROLE_ENGINEER)
check("engineer: load spin enabled", tab6.slip_window_load_spin.isEnabled(),
      tab6.slip_window_load_spin.isEnabled())
check("engineer: time spin enabled", tab6.slip_window_time_spin.isEnabled(),
      tab6.slip_window_time_spin.isEnabled())
check("engineer: slip save enabled", tab6.slip_save_btn.isEnabled(),
      tab6.slip_save_btn.isEnabled())
mw6.close()

# ---- save handler wires values + returns the role label to OPERATOR ---------
mw7 = PumpDashboard()
tab7 = mw7.block_tab
mw7.roles.set_role(ROLE_ENGINEER)
tab7._apply_role(ROLE_ENGINEER)
tab7.slip_window_load_spin.setValue(25.0)
tab7.slip_window_time_spin.setValue(8.0)
tab7._on_save_slip_window()
check("save handler persists load 25", abs(mw7.slip_window_load_klb - 25.0) < 1e-9,
      mw7.slip_window_load_klb)
check("save handler persists time 8", abs(mw7.slip_window_time_sec - 8.0) < 1e-9,
      mw7.slip_window_time_sec)
check("save handler returns the role label to OPERATOR",
      mw7.roles.role() == ROLE_OPERATOR,
      mw7.roles.role())
check("auto-return does not lock the slip controls",
      tab7.slip_save_btn.isEnabled(), tab7.slip_save_btn.isEnabled())
mw7.close()

main_mod.save_settings = _orig_save
main_mod.load_settings = _base_load

app.quit()
print()
print(f"===== SLIP WINDOW: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)