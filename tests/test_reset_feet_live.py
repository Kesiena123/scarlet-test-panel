"""LIVE-connection RESET POSITION — end-to-end dashboard round-trip.

Simulates a real firmware link: the dashboard writes `reset_feet` with the
operator-entered starting feet, a firmware-behaving device ACKs with
currentTicks == 0 and blockPositionFt == that same value (mirroring
encoder_execute_reset_feet + encoder_display_position_ft), and subsequent
periodic reports keep reporting the same referenced position. Verifies the
live path actually RETURNS the entered value to the display.

Run:   python tests/test_reset_feet_live.py
Exit:  0 pass / 1 fail.
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.services.security import ROLE_ENGINEER  # noqa: E402

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  FAIL: {name} {detail}")


class FakeSerial:
    """Minimal serial stand-in: is_open=True, records written lines."""

    is_open = True

    def __init__(self):
        self.written = []

    def write(self, payload):
        text = payload.decode("ascii").rstrip("\n")
        self.written.append(text)
        return len(payload)

    def close(self):
        self.is_open = False


def simulate_firmware(mw, ser, start_ft):
    """Firmware-behaving reply to the dashboard's reset_feet command.

    Mirrors TravellingBlockMonitor.ino encoder_execute_reset_feet /
    encoder_display_position_ft: the runtime offset makes the reset instant
    read exactly `start_ft` and hold while the physical counter is parked.
    """
    rid = None
    for ln in ser.written:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if obj.get("cmd") == "reset_feet":
            rid = obj.get("req_id")
    assert rid is not None, "dashboard never sent reset_feet!"
    return {
        "type": "reset_feet_ack",
        "req_id": rid,
        "currentTicks": 0,
        "blockPositionFt": start_ft,
        "source": "COMMAND",
        "velocityFtMin": 0.0,
        "direction": "NONE",
        "onBottom": "false",
        "calStatus": "VALID",
        "calInRange": 1,
        "uptime_s": 123,
        "sequence": 7,
    }


# ------------------------------------------------------------------
# LIVE round-trip: entered 125.5 must reach the display via the ack.
# ------------------------------------------------------------------
mw = PumpDashboard()
mw.roles.set_role(ROLE_ENGINEER)
tab = mw.block_tab
ser = FakeSerial()
mw.serial = ser

mw.device.update({"calCounter1": 0, "calPosition1": 0.0,
                  "calCounter2": 5000, "calPosition2": 50.0,
                  "calCounter3": 10000, "calPosition3": 100.0,
                  "calCounter4": 15000, "calPosition4": 150.0,
                  "confirmed": True})
mw.current_ticks = 8000
mw._set_block_position(80.0)

tab._prompt_starting_ft = lambda: 125.5
tab._local = lambda: False          # force the LIVE (device) branch
tab._on_reset_feet()

check("live path SENT reset_feet over the link",
      any('"cmd": "reset_feet"' in ln for ln in ser.written))
check("live path SENT the entered value",
      any('"value": 125.5' in ln for ln in ser.written))
check("reset pending while awaiting device confirmation",
      tab._ops.get("reset_feet", {}).get("ok") is None)

ack = simulate_firmware(mw, ser, 125.5)
mw._apply_protocol(ack)
tab.refresh()

check("live ack set block position to the ENTERED value",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)
check("live ack zeroed the live counter tick",
      mw.current_ticks == 0, mw.current_ticks)
check("live success banner shows entered starting position",
      "New Starting Position: 125.50 FT" in tab.banner_lbl.text(),
      tab.banner_lbl.text())
check("live op recorded ok",
      bool(tab._ops.get("reset_feet", {}).get("ok")))

# Subsequent periodic reports from the device keep reading the reference.
mw._apply_protocol({"type": "report", "currentTicks": 0,
                    "blockPositionFt": 125.5, "velocityFtMin": 0.0,
                    "sequence": 8})
check("subsequent report keeps the entered reference",
      abs(mw.block_position_ft - 125.5) < 1e-3, mw.block_position_ft)

# A moving block tracks from the reference (device reports 125.5 + 10 ft).
mw._apply_protocol({"type": "report", "currentTicks": 100,
                    "blockPositionFt": 135.5, "velocityFtMin": 5.0,
                    "sequence": 9})
check("live movement tracks relative to the entered reference",
      abs(mw.block_position_ft - 135.5) < 1e-3, mw.block_position_ft)

# Device replies with a MISMATCHED position -> MUST NOT silently show it.
ser2 = FakeSerial()
mw2 = PumpDashboard()
mw2.roles.set_role(ROLE_ENGINEER)
tab2 = mw2.block_tab
mw2.serial = ser2
mw2.device.update({"calCounter1": 0, "calPosition1": 0.0,
                   "calCounter2": 5000, "calPosition2": 50.0,
                   "calCounter3": 10000, "calPosition3": 100.0,
                   "calCounter4": 15000, "calPosition4": 150.0,
                   "confirmed": True})
mw2.current_ticks = 8000
mw2._set_block_position(80.0)
tab2._prompt_starting_ft = lambda: 125.5
tab2._local = lambda: False
tab2._on_reset_feet()
bad = simulate_firmware(mw2, ser2, 0.0)   # firmware ignored the entered value
mw2._apply_protocol(bad)
tab2.refresh()
check("mismatched device ack is reported as FAILED",
      tab2.banner_lbl.text().startswith("RESET POSITION FAILED"),
      tab2.banner_lbl.text())
check("mismatched ack shows device returned vs requested values",
      "device returned 0.00 ft" in tab2.banner_lbl.text(),
      tab2.banner_lbl.text())
check("mismatched ack names the requested starting feet",
      "requested 125.50 ft" in tab2.banner_lbl.text(),
      tab2.banner_lbl.text())
check("mismatched ack does NOT claim the entered value",
      "125.50" not in [tab2.banner_lbl.text()] or
      "requested 125.50 ft" in tab2.banner_lbl.text(),
      tab2.banner_lbl.text())

# ------------------------------------------------------------------
# LEGACY firmware (FW < 4.1) acks that omit `req_id`: the dashboard must
# still verify the reported position against the pending request.
# ------------------------------------------------------------------
def legacy_ack(feet):
    return {
        "type": "reset_feet_ack",
        "currentTicks": 0,
        "blockPositionFt": feet,
        "source": "COMMAND",
        "velocityFtMin": 0.0,
        "direction": "NONE",
        "onBottom": "false",
        "calStatus": "VALID",
        "calInRange": 1,
        "uptime_s": 123,
        "sequence": 7,
    }

# Legacy ack that returned the ENTERED value (pre-req_id, honors value):
# must be verified via the fallback match and resolve as SUCCESS.
ser3 = FakeSerial()
mw3 = PumpDashboard()
mw3.roles.set_role(ROLE_ENGINEER)
tab3 = mw3.block_tab
mw3.serial = ser3
mw3.device.update({"calCounter1": 0, "calPosition1": 0.0,
                   "calCounter2": 5000, "calPosition2": 50.0,
                   "calCounter3": 10000, "calPosition3": 100.0,
                   "calCounter4": 15000, "calPosition4": 150.0,
                   "confirmed": True})
mw3.current_ticks = 8000
mw3._set_block_position(80.0)
tab3._prompt_starting_ft = lambda: 125.5
tab3._local = lambda: False
tab3._on_reset_feet()
rid3 = None
for ln in ser3.written:
    try:
        if json.loads(ln).get("cmd") == "reset_feet":
            rid3 = json.loads(ln).get("req_id")
    except Exception:
        continue
assert rid3 is not None, "dashboard never sent reset_feet (legacy case)!"
mw3._apply_protocol(legacy_ack(125.5))
tab3.refresh()
check("legacy no-req_id ack matching entered value is verified SUCCESS",
      "STARTING POSITION RESET SUCCESSFULLY" in tab3.banner_lbl.text(),
      tab3.banner_lbl.text())
check("legacy verified ack shows the entered value in the banner",
      "New Starting Position: 125.50 FT" in tab3.banner_lbl.text(),
      tab3.banner_lbl.text())
check("legacy verified ack set the block position to the entered value",
      abs(mw3.block_position_ft - 125.5) < 1e-3, mw3.block_position_ft)
check("legacy verified ack resolved the pending request (no late timeout FAILED)",
      len(mw3._cmd._pending) == 0, mw3._cmd._pending)

# Legacy ack that returned 0.00 while 125.5 was requested (old firmware
# ignoring `value`): MUST be FAILED with the diagnostic, not a silent 0.00.
ser4 = FakeSerial()
mw4 = PumpDashboard()
mw4.roles.set_role(ROLE_ENGINEER)
tab4 = mw4.block_tab
mw4.serial = ser4
mw4.device.update({"calCounter1": 0, "calPosition1": 0.0,
                   "calCounter2": 5000, "calPosition2": 50.0,
                   "calCounter3": 10000, "calPosition3": 100.0,
                   "calCounter4": 15000, "calPosition4": 150.0,
                   "confirmed": True})
mw4.current_ticks = 8000
mw4._set_block_position(80.0)
tab4._prompt_starting_ft = lambda: 125.5
tab4._local = lambda: False
tab4._on_reset_feet()
rid4 = None
for ln in ser4.written:
    try:
        if json.loads(ln).get("cmd") == "reset_feet":
            rid4 = json.loads(ln).get("req_id")
    except Exception:
        continue
assert rid4 is not None, "dashboard never sent reset_feet (legacy mismatch)!"
mw4._apply_protocol(legacy_ack(0.0))
tab4.refresh()
check("legacy no-req_id ack returning 0.00 is FAILED, not silent success",
      tab4.banner_lbl.text().startswith("RESET POSITION FAILED"),
      tab4.banner_lbl.text())
check("legacy FAILED diagnostic names returned vs requested",
      "device returned 0.00 ft" in tab4.banner_lbl.text() and
      "requested 125.50 ft" in tab4.banner_lbl.text(),
      tab4.banner_lbl.text())

# The classic 0.00 default RESET FEET (operator entered 0.0) must STILL
# succeed on old firmware — position matches the requested 0.00 reference.
ser5 = FakeSerial()
mw5 = PumpDashboard()
mw5.roles.set_role(ROLE_ENGINEER)
tab5 = mw5.block_tab
mw5.serial = ser5
mw5.device.update({"calCounter1": 0, "calPosition1": 0.0,
                   "calCounter2": 5000, "calPosition2": 50.0,
                   "calCounter3": 10000, "calPosition3": 100.0,
                   "calCounter4": 15000, "calPosition4": 150.0,
                   "confirmed": True})
mw5.current_ticks = 5000
mw5._set_block_position(50.0)
tab5._prompt_starting_ft = lambda: 0.0
tab5._local = lambda: False
tab5._on_reset_feet()
rid5 = None
for ln in ser5.written:
    try:
        if json.loads(ln).get("cmd") == "reset_feet":
            rid5 = json.loads(ln).get("req_id")
    except Exception:
        continue
assert rid5 is not None, "dashboard never sent reset_feet (classic case)!"
mw5._apply_protocol(legacy_ack(0.0))
tab5.refresh()
check("classic 0.00 default RESET FEET still succeeds on old firmware",
      "STARTING POSITION RESET SUCCESSFULLY" in tab5.banner_lbl.text(),
      tab5.banner_lbl.text())
check("classic reset claimed 0.00 FT position",
      "New Starting Position: 0.00 FT" in tab5.banner_lbl.text(),
      tab5.banner_lbl.text())

mw.close()
mw2.close()
mw3.close()
mw4.close()
mw5.close()
app.quit()
print()
print(f"===== RESET FEET (LIVE): PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)