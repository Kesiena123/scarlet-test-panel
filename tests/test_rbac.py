"""Access-control verification: every role has full access, headless/offscreen.

Covers:
  * every role may command and may calibrate (no role is view-only)
  * the status-bar label still cycles between the two roles (OPERATOR <-> ENGINEER)
  * block tab _apply_role keeps calibration/config controls enabled for BOTH roles
  * _auto_return_to_operator / _return_role_to_operator still drop the label back
    to OPERATOR after a modification, and that re-enables nothing (nothing was
    ever locked)

Run:   python tests/test_rbac.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from scarlet_test_panel.services.security import (  # noqa: E402
    RoleManager, ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ENGINEER)

_failures = 0
_total = 0


def check(name, cond, detail=""):
    global _failures, _total
    _total += 1
    if not cond:
        _failures += 1
        print(f"  FAIL: {name} {detail}")


# --- RoleManager: the role is a label, nothing is gated --------------------
rm = RoleManager(ROLE_OPERATOR)
check("default role is operator", rm.role() == ROLE_OPERATOR, rm.role())
check("operator may command", rm.can_command())
check("operator may calibrate (is_engineer)", rm.is_engineer())
check("operator at_least(supervisor)", rm.at_least(ROLE_SUPERVISOR))

rm.set_role(ROLE_ENGINEER)
check("engineer may command", rm.can_command())
check("engineer may calibrate (is_engineer)", rm.is_engineer())
check("role label switched to engineer", rm.role() == ROLE_ENGINEER, rm.role())
rm.set_role(ROLE_OPERATOR)
check("back to operator", rm.role() == ROLE_OPERATOR)
check("operator still may command after the switch", rm.can_command())

# --- Full dashboard: every role has the calibration/config controls ---------
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
mw = PumpDashboard()

check("dashboard starts in operator", mw.roles.role() == ROLE_OPERATOR)

tab = mw.block_tab if hasattr(mw, "block_tab") else None
if tab is not None:
    controls = (tab.reset_btn, tab.reset_feet_btn, tab.delete_level_btn,
                tab.set_polarity_btn, tab.set_wits_btn, tab.calibrate_btn,
                tab.set_layer_btn, tab.set_height_btn, tab.save_btn, tab.load_btn)

    # OPERATOR has full access: nothing is disabled.
    enabled_ok = all(w.isEnabled() for w in controls)
    check("operator: all calibration/config controls enabled", enabled_ok,
          [w.objectName() or type(w).__name__ for w in controls if not w.isEnabled()])
    check("operator: pulse input enabled",
          all(p.isEnabled() for _b, _l, p, _f, _c, _i in tab._cal_rows))
    check("operator: feet input enabled",
          all(f.isEnabled() for _b, _l, _p, f, _c, _i in tab._cal_rows))

    # Switching the label must not change what is available.
    mw.roles.set_role(ROLE_ENGINEER)
    check("engineer: full-access controls enabled", all(w.isEnabled() for w in controls))
    check("engineer pulse input enabled",
          all(p.isEnabled() for _b, _l, p, _f, _c, _i in tab._cal_rows))

    # Auto-return after a modification drops the label back to OPERATOR, but
    # the controls stay enabled (the role never locked anything).
    tab._auto_return_to_operator()
    check("auto-return -> operator label", mw.roles.role() == ROLE_OPERATOR)
    check("auto-return keeps the controls enabled",
          all(w.isEnabled() for w in controls))
    check("auto-return keeps the pulse inputs enabled",
          all(p.isEnabled() for _b, _l, p, _f, _c, _i in tab._cal_rows))

    # The main window has the same auto-return; it must not lock anything.
    mw._return_role_to_operator()
    check("_return_role_to_operator keeps controls enabled",
          all(w.isEnabled() for w in controls))
else:
    check("block tab exists", False, "no block_tab")

mw.close()
app.quit()
print()
print(f"===== RBAC: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)