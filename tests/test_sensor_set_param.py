"""CommandTracker.sensor_set_param regression suite (protocol 4.2).

Locks in the dashboard half of the per-channel analog enable/disable command:

  * sensor_set_param(sensor, "enabled", 0.0/1.0) sends the staged request with
    the firmware's exact field set (sensor/param/value/req_id)
  * awaiting_confirm -> CONFIRM re-issued with the same req_id
  * the done ack is verified against the echoed `value` before on_done fires
  * a rejected/done-mismatch ack routes to on_fail, never on_done

Run:   python -u tests/test_sensor_set_param.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scarlet_test_panel.services.commands import CommandTracker  # noqa: E402

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


class _Wire:
    def __init__(self):
        self.lines = []

    def write(self, line):
        self.lines.append(json.loads(line))
        return True


# ---- 1. staged enable=0 request carries the exact wire fields -------------------
wire = _Wire()
tr = CommandTracker(write=wire.write, timeout=3.0)
done = {}
failed = {}
tr.sensor_set_param(5, "enabled", 0.0,
                    on_done=lambda _req, _data=None: done.__setitem__("ok", True),
                    on_fail=lambda _req, reason=None: failed.__setitem__("reason", reason))
req = wire.lines.pop(0)
cond = (req["cmd"] == "sensor_set_param" and req["sensor"] == 5
        and req["param"] == "enabled" and req["value"] == 0.0
        and isinstance(req["req_id"], int))
check("sensor_set_param stages enable=0 with the exact wire fields", cond, req)
rid = req["req_id"]

# ---- 2. awaiting_confirm -> CONFIRM with the same req_id -------------------------
check("no verdict before the firmware acks", not done and not failed)
tr.on_ack({"type": "ack", "action": "sensor_set_param", "state": "awaiting_confirm",
           "value": 0.0, "ec": 0, "req_id": rid})
c = wire.lines.pop(0)
check("confirm re-issued with the same req_id",
      c == {"cmd": "confirm", "req_id": rid}, c)

# ---- 3. done ack echoes the value -> DONE, on_done fires --------------------------
tr.on_ack({"type": "ack", "action": "sensor_set_param", "state": "done",
           "value": 0.0, "ec": 0, "req_id": rid})
check("verified done fires on_done only", done.get("ok") is True
      and "reason" not in failed, (done, failed))

# ---- 4. enable=1 round-trip is symmetric -----------------------------------------
wire2 = _Wire()
tr2 = CommandTracker(write=wire2.write, timeout=3.0)
done2 = {}
failed2 = {}
tr2.sensor_set_param(2, "enabled", 1.0,
                     on_done=lambda _req, _data=None: done2.__setitem__("ok", True),
                     on_fail=lambda _req, reason=None: failed2.__setitem__("reason", reason))
r2 = wire2.lines.pop(0)
check("enable=1 request carries value 1.0", r2["value"] == 1.0, r2)
tr2.on_ack({"type": "ack", "action": "sensor_set_param", "state": "awaiting_confirm",
            "value": 1.0, "ec": 0, "req_id": r2["req_id"]})
wire2.lines.pop(0)                       # the confirm
tr2.on_ack({"type": "ack", "action": "sensor_set_param", "state": "done",
            "value": 1.0, "ec": 0, "req_id": r2["req_id"]})
check("enable=1 verified done fires on_done", done2.get("ok") is True
      and "reason" not in failed2, (done2, failed2))

# ---- 5. rejected ack routes to on_fail, never on_done ------------------------------
wire3 = _Wire()
tr3 = CommandTracker(write=wire3.write, timeout=3.0)
done3 = {}
failed3 = {}
tr3.sensor_set_param(0, "enabled", 0.0,   # channel 0 (hookload) is fixed
                     on_done=lambda _req, _data=None: done3.__setitem__("ok", True),
                     on_fail=lambda _req, reason=None: failed3.__setitem__("reason", reason))
r3 = wire3.lines.pop(0)
tr3.on_ack({"type": "ack", "action": "sensor_set_param", "state": "rejected",
            "ec": 4, "detail": "sensor must be 1..15", "req_id": r3["req_id"]})
check("rejected ack routes to on_fail (hookload stays fixed)",
      "reason" in failed3 and "ok" not in done3, (done3, failed3))


if _failures:
    print(f"\n{_failures}/{_total} checks FAILED")
    sys.exit(1)
print(f"\n{_total}/{_total} checks passed")