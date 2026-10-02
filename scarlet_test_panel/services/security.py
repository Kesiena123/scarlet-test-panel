"""Role labels and command sanitization.

ACCESS CONTROL: there is none. Any role may calibrate, reset/tare and issue
device commands - the role is a display/audit label only. The privilege
predicates on RoleManager (is_engineer/can_command/at_least) still exist and
still return True, so every call site keeps working unchanged.

SECURITY NOTE (TLS / exposure):
  This dashboard and firmware are intended for a LOCAL serial link only, on
  controlled hardware. There are NO hardcoded credentials. If this system is
  ever exposed beyond a trusted local network (e.g. remote access, web UI,
  serial-to-IP bridge), you MUST add:
    1. Transport encryption (TLS 1.2+ / mTLS) for any network-exposed path,
    2. Authentication of every supervisor command on the firmware side,
    3. Binding of operator/supervisor privileges to authenticated identities.
  Nothing here gates access, so it is not a substitute for authenticated
  firmware command authorization.
"""

__all__ = ["ROLE_OPERATOR", "ROLE_SUPERVISOR", "ROLE_ENGINEER",
           "RoleManager"]

ROLE_OPERATOR = "operator"
ROLE_SUPERVISOR = "supervisor"
ROLE_ENGINEER = "engineer"

# Valid role labels, kept for display/audit. They no longer carry privileges:
# every role has full access (see RoleManager).
_ROLE_ORDER = [ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ENGINEER]


class RoleManager:
    """Open-access model: every role has full access.

    Calibration (and every other operator action) is available to anybody, so
    the privilege predicates no longer gate anything - they are kept, with the
    same names, signatures and return types, because the UI still calls them
    (block position tab, sensor calibration dialog, analog monitor, sensors
    tab, diagnostics tab, status bar).

    The role is now a *display/audit label only*: `role()` reports the current
    label and `set_role()` still changes it and notifies subscribers, so the
    role indicator keeps working. Nothing is view-only any more.
    """

    def __init__(self, initial_role=ROLE_OPERATOR):
        if initial_role not in _ROLE_ORDER:
            initial_role = ROLE_OPERATOR
        self._role = initial_role
        self._listeners = []

    def role(self):
        return self._role

    def is_supervisor(self):
        """Always True: no action is restricted to a higher role."""
        return True

    def is_engineer(self):
        """Always True: anybody may calibrate."""
        return True

    def can_command(self):
        """Always True: anybody may issue device commands."""
        return True

    def at_least(self, role=None):
        """Always True, for any requested role."""
        return True

    def set_role(self, role):
        if role not in _ROLE_ORDER:
            raise ValueError(f"unknown role: {role}")
        changed = (role != self._role)
        self._role = role
        if changed:
            # notify listeners; they may call role() to read current value
            for cb in list(self._listeners):
                cb(role)
        return role

    def subscribe(self, callback):
        self._listeners.append(callback)
