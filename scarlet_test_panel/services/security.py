"""Role-based access control and command sanitization.

SECURITY NOTE (TLS / exposure):
  This dashboard and firmware are intended for a LOCAL serial link only, on
  controlled hardware. There are NO hardcoded credentials. If this system is
  ever exposed beyond a trusted local network (e.g. remote access, web UI,
  serial-to-IP bridge), you MUST add:
    1. Transport encryption (TLS 1.2+ / mTLS) for any network-exposed path,
    2. Authentication of every supervisor command on the firmware side,
    3. Binding of operator/supervisor privileges to authenticated identities.
  Roles here are enforced only at the dashboard UI layer; they are not a
  substitute for authenticated firmware command authorization.
"""

__all__ = ["ROLE_OPERATOR", "ROLE_SUPERVISOR", "ROLE_ENGINEER",
           "RoleManager"]

ROLE_OPERATOR = "operator"
ROLE_SUPERVISOR = "supervisor"
ROLE_ENGINEER = "engineer"

# Privilege ordering (higher = more privileged). Matches spec §23:
#   OPERATOR  -> view dashboards, alarms, trends, events
#   SUPERVISOR-> + reset/tare, calibrate, configure layers/params, export, diagnostics
#   ENGINEER  -> + raw encoder info, advanced measurement params, comm diagnostics,
#                firmware info, system parameters.
_ROLE_ORDER = [ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ENGINEER]


def _rank(role):
    try:
        return _ROLE_ORDER.index(role)
    except ValueError:
        return 0


class RoleManager:
    """Three-tier role model (Operator < Supervisor < Engineer)."""

    def __init__(self, initial_role=ROLE_OPERATOR):
        if initial_role not in _ROLE_ORDER:
            initial_role = ROLE_OPERATOR
        self._role = initial_role
        self._listeners = []

    def role(self):
        return self._role

    def is_supervisor(self):
        """True for Supervisor or any higher role (i.e. can issue commands)."""
        return _rank(self._role) >= _rank(ROLE_SUPERVISOR)

    def is_engineer(self):
        return _rank(self._role) >= _rank(ROLE_ENGINEER)

    def can_command(self):
        """Alias of is_supervisor(): can issue device commands."""
        return self.is_supervisor()

    def at_least(self, role):
        return _rank(self._role) >= _rank(role)

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
