"""Dashboard-side command tracker: REQUEST -> CONFIRM -> ACK(echo) -> VERIFY.

This is the dashboard half of the request/confirm/verify JSON protocol. It
works with the firmware's two-stage command handling (promt.txt §24). The
dashboard NEVER shows a setting as applied based on what the user typed — it
only marks a change as applied (and shows the resulting values) after the
firmware sends a `done` ack whose embedded ACTIVE config echoes (within
tolerance) exactly the value that was requested.

Protocol contract (must match encoder/serial_protocol.cpp + PROTOCOL.md):

  COMMANDS (dashboard -> firmware):
      {"cmd":"status"}
      {"cmd":"set_calibration_point","layer":1,"value":31.58,
       "counter":4437,"req_id":1}                                -> stage
      {"cmd":"set_calibration_point","layer":2,"value":63.21,
       "req_id":2}                  (counter omitted -> use live counter) -> stage
      {"cmd":"set_wits_correction","value":0.0,"req_id":3}       -> stage
      {"cmd":"restore_defaults","req_id":6}                      -> stage
      {"cmd":"confirm","req_id":<original>}                      -> apply
      {"cmd":"save_calibration","req_id":7}                      -> IMMEDIATE
      {"cmd":"load_calibration","req_id":8}                      -> IMMEDIATE
      {"cmd":"reset_counter","req_id":5}                         -> IMMEDIATE

  16-CHANNEL ANALOG BANK (protocol 4.2):
      {"cmd":"sensor_set_param","sensor":3,"param":"value_high",
       "value":75.0,"req_id":11}                                 -> stage
      {"cmd":"sensor_set_param","sensor":5,"param":"enabled",
       "value":0.0,"req_id":14}  # disable a channel (UNUSED, never sampled)
      {"cmd":"confirm","req_id":11}                              -> apply
      {"cmd":"sensor_reset","sensor":4,"req_id":12}              -> IMMEDIATE
      {"cmd":"sensor_factory_reset","req_id":13}                 -> IMMEDIATE

  RESPONSES (firmware -> dashboard), each ends with ",\"crc\":N}":
      {"type":"status","currentTicks":..,"blockPositionFt":..,
       "velocityFtMin":..,"direction":..,"calStatus":"..","calInRange":1,
       "onBottom":false,"currentLayer":..,
       "calCounter1":..,"calPosition1":..,"countsPerFoot1":.., ...
       "witsCorrectionFt":..,"req_id":n}
      {"type":"ack","action":A,"state":"awaiting_confirm","req_id":n,"ec":0,..}
      {"type":"ack","action":A,"state":"done","req_id":n,"value":..,..}
      {"type":"ack","action":A,"state":"rejected"|"error","req_id":n,
       "ec":N,"detail":"<reason>"}
      {"type":"reset_ack","currentTicks":0,..}

State transitions for one staged command:
    REQUESTED  --(on awaiting_confirm)-->  AWAIT_CONFIRM  -> send CONFIRM
    AWAIT_CONFIRM / AWAIT_DONE --(on done+verify)--> DONE
                                --(on rejected/error)--> FAILED(rejected)
                                --(timeout / link lost)--> FAILED(timeout/link)

save_calibration / load_calibration / reset_counter are single-stage, IMMEDIATE
commands: send them, then match the firmware's corresponding `done`/`reset_ack`
to declare success. reset_counter zeroes the counter and preserves the block
position via a runtime reference WITHOUT re-basing or saving the calibration
table (the operator's calibration input stays byte-for-byte identical).
"""

from __future__ import annotations

import enum
import json
import math
import time

__all__ = ["CommandTracker", "ReqPhase", "VERIFY_TOL"]


class ReqPhase(enum.Enum):
    REQUESTED = "requested"          # staging command sent, awaiting ack
    AWAIT_CONFIRM = "awaiting_confirm"  # firmware asked for CONFIRM
    AWAIT_DONE = "awaiting_done"     # CONFIRM sent, awaiting done ack
    DONE = "done"
    FAILED = "failed"


# Relative tolerance used to verify the echoed ACTIVE config matches the value
# the dashboard requested. Covers float formatting/round-trip only.
VERIFY_TOL = 0.002


class _Req:
    __slots__ = ("cmd", "expected", "phase", "req_id",
                 "on_done", "on_fail", "deadline", "started", "detail")

    def __init__(self, cmd, expected, req_id, on_done, on_fail, deadline):
        self.cmd = cmd
        self.expected = expected
        self.phase = ReqPhase.REQUESTED
        self.req_id = req_id
        self.on_done = on_done
        self.on_fail = on_fail
        self.deadline = deadline
        self.started = time.monotonic()
        self.detail = ""


class CommandTracker:
    """Tracks in-flight settings requests and their confirmation/verification."""

    def __init__(self, write, timeout=3.0, tol=VERIFY_TOL):
        self._write = write             # callable(line:str) -> bool
        self._timeout = timeout
        self._tol = tol
        self._pending = {}
        self._next_id = 1

    # -- lifecycle -------------------------------------------------------
    def reset(self):
        """Internal bookkeeping reset — pending callbacks are NOT invoked."""
        self._pending = {}

    def fail_all(self, reason):
        """Called on disconnect/link loss; fails every still-pending request."""
        for req in list(self._pending.values()):
            req.phase = ReqPhase.FAILED
            req.detail = reason
            self._fire_fail(req, reason)
        self._pending.clear()

    # -- request entry points --------------------------------------------
    def set_calibration_point(self, layer, value, on_done, on_fail, counter=None):
        """Stage a calibration-point change (layer is 1-based 1..4).

        `counter` is the encoder counter for that layer. If omitted, the
        firmware uses its LIVE counter (the operator is at the physical
        point). Verified against the firmware's echoed "calPositionN" value.
        """
        payload = {"value": float(value), "layer": int(layer)}
        if counter is not None:
            payload["counter"] = int(counter)
        return self._begin("set_calibration_point", payload,
                           {f"calPosition{int(layer)}": float(value)},
                           on_done, on_fail)

    def set_wits_correction(self, value, on_done, on_fail):
        """Stage a WITS-correction change.
        Verified against the firmware's echoed "witsCorrectionFt" value.
        """
        return self._begin("set_wits_correction", {"value": float(value)},
                           {"witsCorrectionFt": float(value)}, on_done, on_fail)

    def clear_calibration_point(self, layer, on_done, on_fail):
        """Stage the deletion of ONE calibration level (promt2.txt).

        Fully separate from RESET COUNTER — deleting a level never disturbs the
        live encoder counter. Verified against the echoed calPositionN/calCounterN
        for that level reverting to 0.0 / 0 after the delete.
        """
        layer = int(layer)
        expected = {f"calPosition{layer}": 0.0, f"calCounter{layer}": 0}
        return self._begin("clear_calibration_point", {"layer": layer},
                           expected, on_done, on_fail)

    def set_encoder_polarity(self, polarity, on_done, on_fail):
        """Stage an encoder direction-polarity change (+1 or -1, promt2.txt).
        Verified against the firmware's echoed "encoderPolarity" value.
        """
        return self._begin("set_encoder_polarity", {"value": int(polarity)},
                           {"encoderPolarity": int(polarity)}, on_done, on_fail)

    def restore_defaults(self, on_done, on_fail):
        """Stage RESTORE DEFAULTS (clears the calibration table + WITS offset).
        The firmware deliberately preserves the live counter. The done-ack
        echoes the new active calibration (all rows empty, WITS 0), verified
        against the compiled defaults."""
        expected = {
            "calPosition1": 0.0, "calPosition2": 0.0,
            "calPosition3": 0.0, "calPosition4": 0.0,
            "calCounter1": 0,   "calCounter2": 0,
            "calCounter3": 0,   "calCounter4": 0,
            "witsCorrectionFt": 0.0,
        }
        return self._begin("restore_defaults", {}, expected, on_done, on_fail)

    def save_calibration(self, on_done, on_fail):
        """Request an IMMEDIATE save of the live calibration to EEPROM.
        Success is declared ONLY when the firmware's `done` ack (ec==0) returns."""
        return self._begin("save_calibration", {}, {"ec": 0}, on_done, on_fail)

    def load_calibration(self, on_done, on_fail):
        """Request an IMMEDIATE load of the stored calibration from EEPROM.
        The done-ack echoes the reloaded calibration; verification is skipped
        because the stored table is exactly what gets reloaded (no expected
        value available until after the ack)."""
        return self._begin("load_calibration", {}, {}, on_done, on_fail)

    def capture_tick(self, on_done, on_fail):
        """Request the CURRENT encoder tick count from the Arduino (promt2).

        Uses the firmware's `status` command — the reply's `currentTicks` IS the
        Arduino's authoritative live counter; the dashboard never invents or
        caches a tick of its own. Completed by on_status() when the status
        reply arrives; fails on timeout / link loss / missing currentTicks so
        the CAPTURE button can show "✕ CAPTURE FAILED" without touching the
        layer input field.
        """
        return self._begin("status", {}, {}, on_done, on_fail)

    def reset_counter(self, on_done, on_fail):
        """Request an IMMEDIATE counter reset (promt1.txt).

        Success is declared ONLY when the firmware's `reset_ack` reports
        currentTicks == 0. Position is preserved via a runtime reference;
        the calibration table is NEVER modified, re-based or saved.
        No CONFIRM step for reset_counter.
        """
        return self._begin("reset_counter", {}, {"currentTicks": 0}, on_done, on_fail)

    def reset_feet(self, start_ft=None, on_done=None, on_fail=None):
        """Request an IMMEDIATE RESET FEET (runtime position only).

        Zeroes the live encoder counter and establishes a NEW runtime position
        reference. When `start_ft` is provided the block position is
        re-referenced so it reads `start_ft` at the reset instant and tracks
        movement relative to it; when omitted the classic 0.00 FT RESET FEET
        applies. Calibration anchors are NEVER modified, re-based, saved or
        deleted by this command.
        Success is declared ONLY when the firmware's `reset_feet_ack` reports
        currentTicks == 0 AND blockPositionFt == the requested reference.
        No CONFIRM step.
        """
        payload = {}
        expected = {"currentTicks": 0}
        if start_ft is not None:
            payload["value"] = float(start_ft)
            expected["blockPositionFt"] = float(start_ft)
        else:
            expected["blockPositionFt"] = 0.0
        return self._begin("reset_feet", payload, expected, on_done, on_fail)

    def sensor_set_param(self, sensor, param, value, on_done, on_fail):
        """Stage a per-channel analog parameter update (protocol 4.2).

        ``sensor`` is the 0-based channel index 0..15 (matches SENSOR_CONFIG
        and the firmware array index; channel 0 = hookload chain is fixed and
        will be rejected by the firmware). ``param`` is one of
        voltage_low/voltage_high/value_low/value_high/min_eng/max_eng/gain/
        offset/filter/enabled. With ``enabled`` set to 0.0 a channel becomes
        UNUSED (firmware stops sampling it); 1.0 re-enables it.
        Verified against the firmware's echoed ``value``.
        """
        return self._begin("sensor_set_param",
                           {"sensor": int(sensor), "param": str(param),
                            "value": float(value)},
                           {"value": float(value)}, on_done, on_fail)

    # -- send --------------------------------------------------------------
    def _begin(self, cmd, payload, expected, on_done, on_fail):
        req_id = self._next_id
        self._next_id += 1
        req = _Req(cmd, expected, req_id, on_done, on_fail,
                   time.monotonic() + self._timeout)
        self._pending[req_id] = req

        msg = {"cmd": cmd, "req_id": req_id}
        msg.update(payload)
        line = json.dumps(msg)
        ok = self._write(line)
        if not ok:
            self._pending.pop(req_id, None)
            req.phase = ReqPhase.FAILED
            self._fire_fail(req, "write failed (link down?)")
            return None
        return req_id

    # -- inbound firmware messages --------------------------------------
    def on_ack(self, data):
        """Handle a firmware `ack` message (two-stage set_* / restore)."""
        req_id = int(data.get("req_id") or 0)
        req = self._pending.get(req_id)
        if req is None:
            return False
        state = str(data.get("state", "")).lower()

        if state == "awaiting_confirm":
            # Request acknowledged as staged; now issue CONFIRM to apply.
            req.phase = ReqPhase.AWAIT_CONFIRM
            req.deadline = time.monotonic() + self._timeout
            ok = self._write(json.dumps({"cmd": "confirm", "req_id": req_id}))
            if not ok:
                self._finish_fail(req, "write failed during confirm (link down?)")
            else:
                req.phase = ReqPhase.AWAIT_DONE
            return True

        if state in ("done",):
            ok = self._verify(req, data)
            if ok is True:
                req.phase = ReqPhase.DONE
                self._pending.pop(req_id, None)
                if req.on_done:
                    try:
                        req.on_done(req, data)
                    except Exception:
                        pass
                return "verified"
            self._finish_fail(req, f"verification mismatch: {ok}")
            return "failed"

        if state in ("rejected", "error"):
            reason = str(data.get("detail") or
                         ("value rejected" if state == "rejected" else "device error"))
            self._finish_fail(req, f"{state}: {reason}")
            return "failed"

        # any other state — leave pending
        return True

    def on_reset_ack(self, data):
        """Handle a firmware `reset_ack` (immediate reset confirmation)."""
        req_id = int(data.get("req_id") or 0)
        req = self._pending.get(req_id)
        if req is None:
            # Older firmware acks omit req_id. Fall back to the single
            # in-flight reset_counter request (at most one per operator action).
            for r in self._pending.values():
                if r.cmd == "reset_counter":
                    req = r
                    break
        if req is None or req.cmd != "reset_counter":
            return False
        # The only proof is the counter actually reading 0 (promt.txt §8).
        raw = data.get("currentTicks")
        if raw is not None and int(float(raw)) == 0:
            req.phase = ReqPhase.DONE
            self._pending.pop(req.req_id, None)
            if req.on_done:
                try:
                    req.on_done(req, data)
                except Exception:
                    pass
            return "verified"
        self._finish_fail(req, "reset_ack reported a non-zero counter")
        return "failed"

    def on_reset_feet_ack(self, data):
        """Handle a firmware `reset_feet_ack` (immediate RESET FEET confirm).

        Success requires the counter reading 0 AND the reported block
        position equal to the requested reference (default 0.00 ft; any
        user-supplied starting feet from the pending request, otherwise).
        Calibration is untouched.
        """
        req_id = int(data.get("req_id") or 0)
        req = self._pending.get(req_id)
        if req is None:
            # Older firmware acks omit req_id. Match a pending reset_feet by
            # the reported position vs its requested reference (within
            # tolerance); a single pending reset_feet is expected.
            raw_pos = data.get("blockPositionFt")
            if raw_pos is not None:
                try:
                    posf = float(raw_pos)
                except (TypeError, ValueError):
                    posf = None
                if posf is not None:
                    for r in self._pending.values():
                        if r.cmd == "reset_feet":
                            want = float(r.expected.get("blockPositionFt", 0.0))
                            if abs(posf - want) <= max(self._tol, abs(want) * self._tol):
                                req = r
                                break
        if req is None or req.cmd != "reset_feet":
            return False
        raw_ticks = data.get("currentTicks")
        raw_pos = data.get("blockPositionFt")
        want = float(req.expected.get("blockPositionFt", 0.0))
        if (raw_ticks is not None and int(float(raw_ticks)) == 0
                and raw_pos is not None
                and abs(float(raw_pos) - want)
                    <= max(self._tol, abs(want) * self._tol)):
            req.phase = ReqPhase.DONE
            self._pending.pop(req.req_id, None)
            if req.on_done:
                try:
                    req.on_done(req, data)
                except Exception:
                    pass
            return "verified"
        self._finish_fail(req, "reset_feet_ack reported non-zero counter/position")
        return "failed"

    def on_status(self, data):
        """Handle a firmware `status` reply that completes a capture_tick().

        The status reply carries the Arduino's authoritative currentTicks; the
        pending "status" request is marked DONE and its on_done callback fires.
        If the reply lacks currentTicks, the request fails so the caller shows
        "✕ CAPTURE FAILED" without modifying any field.
        """
        req_id = int(data.get("req_id") or 0)
        req = self._pending.get(req_id)
        if req is None or req.cmd != "status":
            return False
        if "currentTicks" not in data:
            self._finish_fail(req, "status reply carried no currentTicks")
            return "failed"
        req.phase = ReqPhase.DONE
        self._pending.pop(req_id, None)
        if req.on_done:
            try:
                req.on_done(req, data)
            except Exception:
                pass
        return "verified"

    # -- timeout sweep ---------------------------------------------------
    def tick(self, now=None):
        now = now if now is not None else time.monotonic()
        for req in list(self._pending.values()):
            if req.phase in (ReqPhase.REQUESTED, ReqPhase.AWAIT_CONFIRM, ReqPhase.AWAIT_DONE):
                if now > req.deadline:
                    self._finish_fail(
                        req,
                        "no acknowledgment from device within "
                        f"{self._timeout:.1f}s (timeout)")

    # -- status for UI ---------------------------------------------------
    def status(self, req_id):
        req = self._pending.get(req_id)
        if req is None:
            return None
        remaining = max(0.0, req.deadline - time.monotonic())
        return {"cmd": req.cmd,
                "phase": req.phase.value,
                "remaining": remaining,
                "detail": req.detail}

    # -- internals -------------------------------------------------------
    def _verify(self, req, data):
        for key, want in req.expected.items():
            if key not in data:
                return f"'{key}' missing from ack"
            got = data[key]
            try:
                gotf = float(got)
                wantf = float(want)
            except (TypeError, ValueError):
                return f"'{key}' is not numeric"
            if not (math.isfinite(gotf) and math.isfinite(wantf)):
                return f"'{key}' is not finite"
            if abs(gotf - wantf) > max(self._tol, abs(wantf) * self._tol):
                return f"'{key}'={got} != requested {want}"
        return True

    def _finish_fail(self, req, reason):
        req.phase = ReqPhase.FAILED
        req.detail = reason
        self._pending.pop(req.req_id, None)
        self._fire_fail(req, reason)

    def _fire_fail(self, req, reason):
        if req.on_fail:
            try:
                req.on_fail(req, reason)
            except Exception:
                pass