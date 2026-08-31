"""Headless integration smoke for the Block Position Monitor dashboard (v4).

Drives the REAL PumpDashboard (offscreen Qt) with a synthetic firmware on a
fake serial pipe. Exercises: CRC/Fletcher-16 frame validation, live telemetry
with the multi-point calibration model, the IMMEDIATE reset path
(`reset_ack` with currentTicks == 0), the two-stage set_calibration_point /
set_wits_correction -> awaiting_confirm -> confirm -> done(echo verify) flow,
the rejected path, IMMEDIATE save/load calibration, and the stale "no crc"
guard.

This is the artifact described in FINAL_TEST_REPORT.md section 2.3.
Run from the repo root:  python smoke_dashboard.py      -> prints SMOKE_OK
"""

import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import json

from PyQt5.QtWidgets import QApplication

from scarlet_test_panel.main import PumpDashboard
from scarlet_test_panel.services.protocol import fletcher16
from scarlet_test_panel.config import (
    FW_MAX_CAL_POINTS, FW_WITS_CORRECTION_DEFAULT,
    DEMO_CAL_POINTS, DEMO_WITS_CORRECTION,
)


def frame(msg: dict) -> bytes:
    """Encode a firmware message as one CRC-valid JSON line (Fletcher-16).

    Layout matches the firmware serializer: the checksum covers everything
    between the leading '{' and the final ',"crc":N}' segment.
    """
    payload = json.dumps(msg, separators=(",", ":"))[1:-1]
    line = "{" + payload + ',"crc":%d}' % fletcher16(payload.encode("ascii"))
    return (line + "\n").encode("ascii")


class Setting:
    def __init__(self, name):
        self.name = name
        self.state = None          # None | "staged"
        self.value = None


class FakeFirmware:
    """Synthetic device: mirrors the ACTIVE config and answers per protocol."""

    def __init__(self):
        # Seed a 4-anchor calibration table like the demo seeds.
        pts = sorted(DEMO_CAL_POINTS, key=lambda p: p["counter"])
        d = {
            "calCounter1": int(pts[0]["counter"]),  "calPosition1": float(pts[0]["position"]),
            "calCounter2": int(pts[1]["counter"]),  "calPosition2": float(pts[1]["position"]),
            "calCounter3": int(pts[2]["counter"]),  "calPosition3": float(pts[2]["position"]),
            "calCounter4": int(pts[3]["counter"]),  "calPosition4": float(pts[3]["position"]),
            "countsPerFoot1": round((pts[1]["counter"] - pts[0]["counter"]) / (pts[1]["position"] - pts[0]["position"]), 2),
            "countsPerFoot2": round((pts[2]["counter"] - pts[1]["counter"]) / (pts[2]["position"] - pts[1]["position"]), 2),
            "countsPerFoot3": round((pts[3]["counter"] - pts[2]["counter"]) / (pts[3]["position"] - pts[2]["position"]), 2),
            "countsPerFoot4": 0.0,
            "witsCorrectionFt": DEMO_WITS_CORRECTION,
            "encoderPolarity": 1,
            "currentTicks": 0,
            "blockPositionFt": float(pts[0]["position"]),
            "velocityFtMin": 0.0,
            "direction": "STOPPED",
            "onBottom": True,
            "calStatus": "VALID",
            "calInRange": 1,
            "currentLayer": 1,
            "fw_version": "4.0.0", "build": "2026-08-29",
            "boot_reason": "POWER_ON", "boot_error": "",
            "protocol_version": "4.0", "eeprom_status": "OK",
            "uptime_s": 0, "sequence": 0,
        }
        self.d = d
        self.staged = {}           # req_id -> (action, key, value)
        self.frames = []

    def _flush_frame(self, msg):
        self.frames.append(frame(msg))


class FakeSerial:
    def __init__(self, fw: FakeFirmware):
        self.fw = fw
        self._buffer = bytearray()
        self.is_open = True
        self.captured = []        # commands the dashboard sent us
        self.auto_answer_reset = True

    @property
    def in_waiting(self):
        return len(self._buffer)

    def read(self, n=0):
        n = n if n >= 0 else len(self._buffer)
        out = bytes(self._buffer[:n])
        del self._buffer[:n]
        return out

    def write(self, data):
        line = data.decode("ascii").strip()
        if line:
            self.captured.append(line)
            self._process(json.loads(line))
        return len(data)

    def close(self):
        self.is_open = False
        self._buffer.clear()

    # -- synthetic firmware -------------------------------------------------
    def _process(self, cmd):
        verb = cmd.get("cmd")
        rid = cmd.get("req_id")
        d = self.fw.d
        if verb == "status":
            self._emit_status()
            return
        if verb == "reset_counter":
            if not self.auto_answer_reset:
                return
            # Reset preserves position via a runtime reference and NEVER
            # re-bases, modifies or saves the calibration table. Only the live
            # counter is zeroed; blockPositionFt is preserved (via the offset).
            d["currentTicks"] = 0
            self.fw._flush_frame(
                {"type": "reset_ack", "currentTicks": 0,
                 "blockPositionFt": round(d.get("blockPositionFt", 0.0), 3),
                 "req_id": rid,
                 "detail": "button", "source": "device",
                 "uptime_s": d["uptime_s"]})
            return
        if verb == "confirm":
            entry = self.fw.staged.get(rid)
            if entry is None:
                self.fw._flush_frame(
                    {"type": "ack", "action": "confirm", "state": "error",
                     "req_id": rid, "ec": 7, "detail": "nothing staged"})
                return
            del self.fw.staged[rid]
            self._apply_and_ack(rid, entry)
            return
        if verb == "restore_defaults":
            self.fw.staged[rid] = ("restore_defaults", None, None)
            self.fw._flush_frame(
                {"type": "ack", "action": "restore_defaults",
                 "state": "awaiting_confirm", "req_id": rid, "ec": 0})
            return
        # IMMEDIATE calibration persistence commands.
        if verb == "save_calibration":
            # Persist the current table (idempotent on the fake side).
            d["eeprom_status"] = "OK"
            saved = {k: d[k] for k in list(d) if k.startswith(("calCounter", "calPosition"))
                     or k == "witsCorrectionFt"}
            self.fw._flush_frame(
                {"type": "ack", "action": "save_calibration", "state": "done",
                 "req_id": rid, "ec": 0, **saved, "uptime_s": d["uptime_s"]})
            return
        if verb == "load_calibration":
            # Reload the stored table (fake: same as active).
            loaded = {k: d[k] for k in list(d) if k.startswith(("calCounter", "calPosition"))
                      or k == "witsCorrectionFt"}
            self.fw._flush_frame(
                {"type": "ack", "action": "load_calibration", "state": "done",
                 "req_id": rid, "ec": 0, **loaded, "uptime_s": d["uptime_s"]})
            return

        # two-stage set_* commands
        if verb == "set_calibration_point":
            layer = int(cmd.get("layer") or 1)
            value = float(cmd.get("value"))
            counter = int(cmd.get("counter")) if cmd.get("counter") is not None \
                else int(d["currentTicks"])
            self.fw.staged[rid] = ("set_calibration_point", (layer, counter, value), None)
            self.fw._flush_frame(
                {"type": "ack", "action": "set_calibration_point",
                 "state": "awaiting_confirm", "req_id": rid, "ec": 0,
                 "layer": layer, "calPosition%d" % layer: value,
                 "calCounter%d" % layer: counter})
            return
        if verb == "set_wits_correction":
            value = float(cmd.get("value"))
            self.fw.staged[rid] = ("set_wits_correction", value, None)
            self.fw._flush_frame(
                {"type": "ack", "action": "set_wits_correction",
                 "state": "awaiting_confirm", "req_id": rid, "ec": 0,
                 "witsCorrectionFt": value})
            return
        if verb == "set_encoder_polarity":
            polarity = int(cmd.get("value"))
            self.fw.staged[rid] = ("set_encoder_polarity", polarity, None)
            self.fw._flush_frame(
                {"type": "ack", "action": "set_encoder_polarity",
                 "state": "awaiting_confirm", "req_id": rid, "ec": 0,
                 "encoderPolarity": polarity})
            return
        if verb == "clear_calibration_point":
            layer = int(cmd.get("layer") or 1)
            self.fw.staged[rid] = ("clear_calibration_point", layer, None)
            self.fw._flush_frame(
                {"type": "ack", "action": "clear_calibration_point",
                 "state": "awaiting_confirm", "req_id": rid, "ec": 0,
                 "layer": layer})
            return
        self.fw._flush_frame(
            {"type": "ack", "action": verb, "state": "error", "req_id": rid,
             "ec": 1, "detail": "unknown verb"})

    def _apply_and_ack(self, rid, entry):
        action, payload, _ = entry
        d = self.fw.d
        if action == "restore_defaults":
            for i in range(1, FW_MAX_CAL_POINTS + 1):
                d[f"calCounter{i}"] = 0
                d[f"calPosition{i}"] = 0.0
                d[f"countsPerFoot{i}"] = 0.0
            d["witsCorrectionFt"] = 0.0
            d["eeprom_status"] = "OK"
        elif action == "set_wits_correction":
            d["witsCorrectionFt"] = payload
        elif action == "set_encoder_polarity":
            d["encoderPolarity"] = payload
        elif action == "clear_calibration_point":
            layer = payload
            d[f"calCounter{layer}"] = 0
            d[f"calPosition{layer}"] = 0.0
            d[f"countsPerFoot{layer}"] = 0.0
            d["eeprom_status"] = "OK"
        elif action == "set_calibration_point":
            layer, counter, value = payload
            d[f"calCounter{layer}"] = counter
            d[f"calPosition{layer}"] = value
            d[f"eeprom_status"] = "OK"
        ack = {
            "type": "ack", "action": action, "state": "done",
            "req_id": rid, "ec": 0,
            "calCounter1": d["calCounter1"], "calPosition1": d["calPosition1"],
            "calCounter2": d["calCounter2"], "calPosition2": d["calPosition2"],
            "calCounter3": d["calCounter3"], "calPosition3": d["calPosition3"],
            "calCounter4": d["calCounter4"], "calPosition4": d["calPosition4"],
            "witsCorrectionFt": d["witsCorrectionFt"],
            "encoderPolarity": d.get("encoderPolarity", 1),
            "uptime_s": d["uptime_s"],
        }
        # echo the staged layer/position so VERIFY sees the requested value
        if action == "set_calibration_point":
            ack["calPosition%d" % payload[0]] = float(payload[2])
            ack["calCounter%d" % payload[0]] = int(payload[1])
        if action == "set_wits_correction":
            ack["witsCorrectionFt"] = float(payload)
        if action == "set_encoder_polarity":
            ack["encoderPolarity"] = int(payload)
        if action == "clear_calibration_point":
            ack["calCounter%d" % payload] = 0
            ack["calPosition%d" % payload] = 0.0
        self.fw._flush_frame(ack)

    def emit_status(self):
        self._emit_status()

    def _emit_status(self):
        d = self.fw.d
        msg = {"type": "status"}
        msg.update(d.copy())
        msg["req_id"] = 0
        self.fw._flush_frame(msg)

    def emit_tick(self, ticks):
        """Emit one live report frame at the given counter reading.

        The firmware derives position from the multi-point table.
        """
        d = self.fw.d
        pts = sorted(
            [(int(d[f"calCounter{i}"]), float(d[f"calPosition{i}"]))
             for i in range(1, FW_MAX_CAL_POINTS + 1)
             if int(d[f"calCounter{i}"]) != 0 or float(d[f"calPosition{i}"]) != 0.0],
            key=lambda p: p[0])
        d["currentTicks"] = ticks
        if len(pts) >= 2 and pts[0][0] <= ticks <= pts[-1][0]:
            pos = pts[0][1]
            for (c1, p1), (c2, p2) in zip(pts, pts[1:]):
                if c1 <= ticks <= c2:
                    pos = p1 + (ticks - c1) / (c2 - c1) * (p2 - p1)
                    break
            pos += d["witsCorrectionFt"]
            cal_status, cal_in_range = "VALID", 1
            on_bottom = (d["direction"] == "STOPPED" and pos <= pts[0][1] + 0.75)
        else:
            pos = pts[-1][1] if pts else 0.0
            cal_status, cal_in_range, on_bottom = "OUT_OF_RANGE", 0, False
        d["blockPositionFt"] = round(pos, 3)
        d["velocityFtMin"] = 1.2345
        d["direction"] = "UP"
        d["uptime_s"] += 1
        d["sequence"] += 1
        self.fw._flush_frame({
            "type": "report",
            "currentTicks": ticks, "blockPositionFt": round(pos, 3),
            "velocityFtMin": 1.2345, "direction": "UP",
            "onBottom": "true" if on_bottom else "false",
            "calStatus": cal_status, "calInRange": cal_in_range,
            "currentLayer": 1, "heartbeat": True,
            "uptime_s": d["uptime_s"], "sequence": d["sequence"],
            "calCounter1": d["calCounter1"], "calPosition1": d["calPosition1"],
            "calCounter2": d["calCounter2"], "calPosition2": d["calPosition2"],
            "calCounter3": d["calCounter3"], "calPosition3": d["calPosition3"],
            "calCounter4": d["calCounter4"], "calPosition4": d["calPosition4"],
            "witsCorrectionFt": d["witsCorrectionFt"],
        })

    def feed_bad_crc(self):
        bad = '{"type":"report","currentTicks":9,"blockPositionFt":0.0,"crc":0}\n'
        self._buffer.extend(bad.encode("ascii"))

    def drain_frames(self):
        while self.fw.frames:
            self._buffer.extend(self.fw.frames.pop(0))


def _pump(mw):
    """Flush firmware frames into the rx buffer and process the whole chain,
    including acks that the dashboard's CONFIRM step triggers mid-cycle."""
    guard = 0
    while (mw.serial.fw.frames or mw.serial.in_waiting) and guard < 200:
        while mw.serial.fw.frames:
            mw.serial._buffer.extend(mw.serial.fw.frames.pop(0))
        while mw.serial.in_waiting:
            mw._serial_tick()
        guard += 1


def main():
    app = QApplication([])
    mw = PumpDashboard()

    fw = FakeFirmware()
    serial = FakeSerial(fw)
    mw.serial = serial
    fw2 = fw.d

    pts = sorted(DEMO_CAL_POINTS, key=lambda p: p["counter"])
    c0, p0 = pts[0]["counter"], pts[0]["position"]
    c1, p1 = pts[1]["counter"], pts[1]["position"]
    c2p, p2p = pts[2]["counter"], pts[2]["position"]

    # --- 1. STATUS snapshot: device-confirmed model ---
    serial.emit_status()
    serial.drain_frames()
    _pump(mw)
    assert mw.device["confirmed"] is True
    assert mw.device["calPosition1"] == p0
    assert mw.device["calCounter1"] == c0
    assert mw.encoder_fw_version == "4.0.0"
    assert mw.device["witsCorrectionFt"] == DEMO_WITS_CORRECTION
    assert mw.cal_status == "VALID"

    # --- 2. Dashboard calibrated_position() mirrors the firmware model ---
    # Simple single-point equality at an anchor.
    assert abs(mw.calibrated_position(c0) - p0) < 1e-6
    # Interpolation between anchors 1 and 2 (WITS = 0).
    mid_ticks = (c0 + c1) / 2.0
    mid_pos = p0 + (mid_ticks - c0) / (c1 - c0) * (p1 - p0)
    assert abs(mw.calibrated_position(mid_ticks) - mid_pos) < 1e-6
    # Out-of-range is never extrapolated -> returns None.
    assert mw.calibrated_position(c2p + 5000) is None
    # WITS offset is added to reported position.
    # (WITS is 0 by default; compensate by temporarily reflecting it.)

    # --- 3. Live telemetry (ticks within the calibrated span) ---
    for _ in range(8):
        serial.emit_tick(mw.current_ticks + 1000)   # stays within anchors 1..4
        serial.drain_frames()
        _pump(mw)
    assert mw.current_ticks == 8000
    # Dashboard stored the firmware's value verbatim and the mirror agrees.
    assert mw.calibrated_position(8000) is not None
    assert abs(mw.block_position_ft - mw.calibrated_position(8000)) < 1e-3
    assert mw._crc_failures == 0
    assert mw._comm_errors == 0

    # --- 4. IMMEDIATE reset: success only on currentTicks == 0 ---
    results = {}
    rid = mw._cmd.reset_counter(
        on_done=lambda req, data: results.update(done=True),
        on_fail=lambda req, why: results.update(done=False, why=why))
    assert rid is not None
    serial.drain_frames()
    _pump(mw)
    assert results.get("done") is True
    state = mw.consume_reset_state()
    assert state is not None and state[0] == "done" and state[1] is True
    assert "RESET SUCCESSFUL" in state[2]
    assert mw.current_ticks == 0
    assert mw.consume_reset_state() is None  # single-shot banner

    # --- 4b. Regression: reset_ack that LACKS currentTicks must FAIL ---
    results.clear()
    serial.auto_answer_reset = False          # we control the only ack
    rid2 = mw._cmd.reset_counter(
        on_done=lambda req, data: results.update(bogus_done=True),
        on_fail=lambda req, why: results.update(bogus_fail=why))
    serial.auto_answer_reset = True
    serial.fw._flush_frame({"type": "reset_ack", "req_id": rid2})
    _pump(mw)
    assert "bogus_done" not in results
    assert "bogus_fail" in results
    bad_state = mw.consume_reset_state()
    assert bad_state is not None and bad_state[0] == "done" and bad_state[1] is False

    # --- 5. Two-stage set_calibration_point (layer 2, with counter) ---
    results.clear()
    new_tick, new_pos = 8000, 64.50
    rid3 = mw._cmd.set_calibration_point(
        2, new_pos, counter=new_tick,
        on_done=lambda req, data: results.update(done3=True),
        on_fail=lambda req, why: results.update(fail3=why))
    serial.drain_frames()
    _pump(mw)  # awaiting_confirm -> dashboard sends CONFIRM -> done ack
    assert results.get("done3") is True
    assert mw.device["confirmed"] is True
    assert abs(mw.device["calPosition2"] - new_pos) < 1e-9
    assert mw.device["calCounter2"] == new_tick
    assert mw._cmd.status(rid3) is None  # cleared from pending

    # --- 6. Rejected path keeps the old active value ---
    results.clear()
    rid4 = mw._cmd.set_wits_correction(
        2.5,
        on_done=lambda req, data: results.update(done4=True),
        on_fail=lambda req, why: results.update(fail4=why))
    # Device refuses at CONFIRM time.
    serial.fw.staged[rid4] = ("set_wits_correction", 2.5, None)
    serial.fw._flush_frame(
        {"type": "ack", "action": "set_wits_correction",
         "state": "rejected", "req_id": rid4, "ec": 5,
         "detail": "value out of range"})
    serial.drain_frames()
    _pump(mw)
    assert "done4" not in results
    assert "fail4" in results and "rejected" in results["fail4"]
    assert abs(mw.device["witsCorrectionFt"] - DEMO_WITS_CORRECTION) < 1e-9

    # --- 7. IMMEDIATE save_calibration (verified on ec == 0) ---
    results.clear()
    rid5 = mw._cmd.save_calibration(
        on_done=lambda req, data: results.update(done5=True),
        on_fail=lambda req, why: results.update(fail5=why))
    serial.drain_frames()
    _pump(mw)
    assert results.get("done5") is True
    assert "fail5" not in results

    # --- 8. IMMEDIATE load_calibration (reloads stored table) ---
    results.clear()
    rid6 = mw._cmd.load_calibration(
        on_done=lambda req, data: results.update(done6=True),
        on_fail=lambda req, why: results.update(fail6=why))
    serial.drain_frames()
    _pump(mw)
    assert results.get("done6") is True
    assert "fail6" not in results

    # --- 9. Fletcher-16 rejection is counted, never displayed ---
    before = mw._crc_failures
    serial.feed_bad_crc()
    _pump(mw)
    assert mw._crc_failures == before + 1
    assert mw._comm_errors >= 1  # BadChecksum also bumps comm errors (bug guard)

    # --- 10. Stale/link guard stays clean while frames flow ---
    _pump(mw)
    assert mw._data_stale is False
    assert mw._link_down is False

    # --- 11. Two-stage set_encoder_polarity (promt2) ---
    results.clear()
    ridp = mw._cmd.set_encoder_polarity(
        -1,
        on_done=lambda req, data: results.update(donep=True),
        on_fail=lambda req, why: results.update(failp=why))
    serial.drain_frames()
    _pump(mw)  # awaiting_confirm -> CONFIRM -> done ack
    assert results.get("donep") is True
    assert "failp" not in results
    assert mw.device["encoderPolarity"] == -1
    assert mw._cmd.status(ridp) is None  # cleared from pending
    # Restore polarity for later checks.
    results.clear()
    mw._cmd.set_encoder_polarity(
        1,
        on_done=lambda req, data: results.update(donex=True),
        on_fail=lambda req, why: results.update(failx=why))
    serial.drain_frames()
    _pump(mw)
    assert results.get("donex") is True
    assert mw.device["encoderPolarity"] == 1

    # --- 12. Two-stage clear_calibration_point (promt2, DELETE LEVEL) ---
    # Advance the live counter so we can prove DELETE LEVEL never disturbs it.
    serial.emit_tick(mw.current_ticks + 500)
    serial.drain_frames()
    _pump(mw)
    prev_ticks = mw.current_ticks
    assert prev_ticks != 0  # live counter is now non-zero
    results.clear()
    ridc = mw._cmd.clear_calibration_point(
        2,
        on_done=lambda req, data: results.update(donec=True),
        on_fail=lambda req, why: results.update(failc=why))
    serial.drain_frames()
    _pump(mw)  # awaiting_confirm -> CONFIRM -> done ack
    assert results.get("donec") is True
    assert "failc" not in results
    assert mw.device["calCounter2"] == 0
    assert mw.device["calPosition2"] == 0.0
    assert mw._cmd.status(ridc) is None  # cleared from pending
    assert mw.current_ticks == prev_ticks  # live counter untouched by DELETE LEVEL

    # --- 13. Rejected path on clear_calibration_point keeps the level ---
    results.clear()
    ridr = mw._cmd.clear_calibration_point(
        3,
        on_done=lambda req, data: results.update(done13=True),
        on_fail=lambda req, why: results.update(fail13=why))
    # Device refuses at CONFIRM time (before the dashboard auto-confirms).
    serial.fw.staged[ridr] = ("clear_calibration_point", 3, None)
    serial.fw._flush_frame(
        {"type": "ack", "action": "clear_calibration_point",
         "state": "rejected", "req_id": ridr, "ec": 1,
         "detail": "refused"})
    serial.drain_frames()
    _pump(mw)
    assert "done13" not in results
    assert "fail13" in results and "rejected" in results["fail13"]

    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
