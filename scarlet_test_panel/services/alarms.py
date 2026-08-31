"""Alarm management (ISA-18.2-aligned).

Distinct, unambiguous alarm states: NORMAL, WARNING, FAULT.
Each alarm, once raised, must be acknowledged by a user action. It is never
auto-dismissed simply because the condition clears. This prevents a brief
condition change from silently erasing an alarm from the operator's awareness.

Severity -> visual treatment (color + icon/text, never color-only):
    NORMAL    green   "OK"
    WARNING   amber   "!"  (warning triangle)
    FAULT     red     "X"  (solid alert banner)

Alarms are exposed via callbacks so the UI can subscribe.
"""

import enum
import datetime
import threading

__all__ = ["AlarmState", "AlarmManager", "Severity"]

import enum as _enum


class Severity(_enum.Enum):
    NORMAL = 0
    WARNING = 1
    FAULT = 2


class AlarmState(_enum.Enum):
    CLEAR = 0
    ACTIVE = 1      # raised, not yet acknowledged
    ACKNOWLEDGED = 2  # acknowledged, condition may still be active


class AlarmManager:
    def __init__(self, name="depth", warning_enabled=False):
        self._name = name
        self._state = AlarmState.CLEAR
        self._severity = Severity.NORMAL
        self._raised_ts = None
        self._ack_ts = None
        self._ack_actor = ""
        self._warning_enabled = warning_enabled
        self._lock = threading.Lock()
        self._listeners = []

    # -- public API ------------------------------------------------------
    def update(self, fault: bool, warning: bool = False, actor=""):
        """Feed current condition. Returns the resulting state.

        Acknowledgment is sticky: once a fault/warning is raised it stays in
        a raised state until the operator calls acknowledge() explicitly.
        """
        with self._lock:
            if fault:
                if self._severity != Severity.FAULT:
                    self._severity = Severity.FAULT
                    self._raised_ts = datetime.datetime.now()
                    self._ack_ts = None
                    self._state = AlarmState.ACTIVE
            elif self._warning_enabled and warning:
                if self._severity in (Severity.NORMAL, Severity.WARNING):
                    self._severity = Severity.WARNING
                    self._raised_ts = datetime.datetime.now()
                    self._ack_ts = None
                    # do not downgrade an active FAULT
                    if self._state == AlarmState.CLEAR:
                        self._state = AlarmState.ACTIVE
            else:
                # No fault and no warning. Keep the alarm flagged as active
                # until acknowledged — it must not auto-dismiss.
                pass
            res = (self._severity, self._state)
        self._notify(res)
        return res

    def acknowledge(self, actor=""):
        with self._lock:
            if self._state == AlarmState.ACTIVE:
                self._state = AlarmState.ACKNOWLEDGED
                self._ack_ts = datetime.datetime.now()
                self._ack_actor = actor
            res = (self._severity, self._state)
        self._notify(res)
        return res

    def reset(self):
        """Only for explicit supervisor reset of a fully cleared condition."""
        with self._lock:
            self._severity = Severity.NORMAL
            self._state = AlarmState.CLEAR
            self._ack_ts = None
            res = (self._severity, self._state)
        self._notify(res)
        return res

    # -- introspection ----------------------------------------------------
    def snapshot(self):
        with self._lock:
            return {
                "name": self._name,
                "severity": self._severity.name,
                "state": self._state.name,
                "raised_ts": self._raised_ts,
                "ack_ts": self._ack_ts,
                "ack_actor": self._ack_actor,
            }

    def is_active(self):
        with self._lock:
            return self._state == AlarmState.ACTIVE

    def needs_ack(self):
        with self._lock:
            return self._state == AlarmState.ACTIVE

    def subscribe(self, cb):
        self._listeners.append(cb)

    def _notify(self, res):
        for cb in list(self._listeners):
            try:
                cb(res)
            except Exception:
                pass
