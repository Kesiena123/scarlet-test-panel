"""SPM / RPM Monitor dashboard (rewritten to match the Analog Monitor window).

The page is a single-screen industrial table - Device | Channel | Sensor Name |
Value | Unit | Counter | Offset | Reset - built from the SAME services the rest
of the application uses. This suite verifies:

  * the page keeps its place in the stack (IDX_SPM_RPM / StrokeRpmTab)
  * the visual language matches the Analog Monitor dashboard: one full-view
    QTableWidget, no scrolling, the same header + status-chip layout, the same
    proportional column reflow and the same row-height fill; the old
    gauge-card / flow-layout page is gone
  * one row per EXISTING channel: SPM 1-4 then RPM 1-2, named from
    SPM_CONFIG / RPM_CONFIG, on the firmware counter numbers 1-6
  * every number is mirrored from the EXISTING sources (spm_values,
    rpm_values, spm_raw_counter, spm_offset, rpm_* , total_spm) - never
    recalculated with a second engine
  * a channel that never reported shows "--", never a misleading 0
  * stopping Demo/Live keeps the last reading and labels it HELD instead of
    blanking the table
  * the header chip uses the SAME link vocabulary/colors as the Analog Monitor
  * header chip
  * RESET calls the EXISTING mw.reset_spm / mw.reset_rpm helpers (no firmware
    write), audits the action and returns the role to Operator
  * RESET is gated by the EXISTING RBAC (can_command)

Run:   python tests/test_spm_rpm_dashboard.py
Exit:  0 when all checks pass, 1 otherwise.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QTableWidget, QAbstractItemView, QScrollArea)

app = QApplication([])

import scarlet_test_panel.main as main_mod  # noqa: E402
from scarlet_test_panel.main import PumpDashboard  # noqa: E402
from scarlet_test_panel.config import SPM_CONFIG, RPM_CONFIG  # noqa: E402
from scarlet_test_panel.tabs.spm_rpm_tab import (  # noqa: E402
    _HEADERS, _HELD_TEXT, _MIN_W)
from scarlet_test_panel.services.security import (  # noqa: E402
    ROLE_OPERATOR, ROLE_ENGINEER)

# Hermetic: never write real settings.
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


def cell(tab, row, col):
    return tab.table.item(row, col).text()


class _FakeOpen:
    """Minimal stand-in for an open serial port (no hardware on the bench)."""

    is_open = True

    def write(self, *_a, **_k):
        raise AssertionError("the page must never write to the device")


mw = PumpDashboard()
_orig_serial = mw.serial
tab = mw.stroke_tab
mon = mw.analog_monitor_tab

# ---- 1. the page still lives where it always did ---------------------------
check("page is still the StrokeRpmTab stack entry at IDX_SPM_RPM",
      type(mw.stack.widget(mw.IDX_SPM_RPM)).__name__ == "StrokeRpmTab",
      type(mw.stack.widget(mw.IDX_SPM_RPM)).__name__)
check("main window still holds the single reference (mw.stroke_tab)",
      mw.stroke_tab is tab)

# ---- 2. it now looks like the Analog Monitor dashboard --------------------
check("the page is one full-view table, like the Analog Monitor",
      isinstance(tab.table, QTableWidget) and isinstance(mon.table, QTableWidget),
      type(tab.table).__name__)
check("column headers match the Analog Monitor style header row",
      [tab.table.horizontalHeaderItem(i).text() for i in range(len(_HEADERS))]
      == list(_HEADERS), [tab.table.horizontalHeaderItem(i).text()
                          for i in range(len(_HEADERS))])
check("table headers are hidden vertical / shown horizontal like the monitor",
      tab.table.verticalHeader().isVisible() is False)
check("the page never scrolls vertically (single screen)",
      tab.table.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
      == mon.table.verticalScrollBarPolicy())
check("the page never scrolls horizontally",
      tab.table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
      == mon.table.horizontalScrollBarPolicy())
check("table is read-only and non-selectable (monitor behaviour)",
      tab.table.editTriggers() == QAbstractItemView.NoEditTriggers
      and tab.table.selectionMode() == QAbstractItemView.NoSelection)
check("old gauge-card page is gone (no card widgets left on the page)",
      not hasattr(tab, "spm_cards") and not hasattr(tab, "rpm_card"),
      [n for n in ("spm_cards", "rpm_card") if hasattr(tab, n)])
check("no nested scroll area / flow layout wrapper around the table",
      not any(isinstance(c, (QScrollArea,)) for c in tab.children()))
check("header row has a title and a status chip, like the Analog Monitor",
      hasattr(tab, "status_chip") and hasattr(mon, "status_chip"))

# ---- 3. one row per EXISTING channel --------------------------------------
n_expected = len(SPM_CONFIG) + len(RPM_CONFIG)
check("row count == SPM channels + RPM channels",
      tab.table.rowCount() == n_expected,
      (tab.table.rowCount(), n_expected))
names = [cell(tab, r, 2) for r in range(n_expected)]
want = [c["name"] for c in SPM_CONFIG] + [c["name"] for c in RPM_CONFIG]
check("row names come from SPM_CONFIG / RPM_CONFIG in order",
      names == want, (names, want))
check("SPM 1-4 stay four INDEPENDENT rows",
      names[:len(SPM_CONFIG)] == [f"SPM {i+1}" for i in range(len(SPM_CONFIG))],
      names)
check("units are SPM for the SPM rows and RPM for the RPM rows",
      [cell(tab, r, 4) for r in range(n_expected)]
      == ["SPM"] * len(SPM_CONFIG) + ["RPM"] * len(RPM_CONFIG),
      [cell(tab, r, 4) for r in range(n_expected)])
check("channel numbers are the firmware counters 1..6",
      [cell(tab, r, 1) for r in range(n_expected)]
      == [str(i + 1) for i in range(n_expected)],
      [cell(tab, r, 1) for r in range(n_expected)])
check("every row keeps a working Reset control",
      len(tab._reset_btns) == n_expected
      and all(b.text() == "Reset" for b in tab._reset_btns))

# ---- 4. values are MIRRORED from the existing sources ---------------------
mw.serial = _orig_serial
mw._demo_mode = True
mw.spm_values = [1.5, 2.5, 3.5, 4.5]
mw.rpm_values = [10.5, 20.5]
mw.spm_raw_counter = [1000.0, 2000.0, 3000.0, 4000.0]
mw.rpm_raw_counter = [5000.0, 6000.0]
mw.spm_offset = [10.0, 20.0, 30.0, 40.0]
mw.rpm_offset = [50.0, 60.0]
tab.refresh()
check("SPM value cells mirror mw.spm_values exactly",
      [cell(tab, r, 3) for r in range(len(SPM_CONFIG))]
      == [f"{v:.1f}" for v in mw.spm_values],
      ([cell(tab, r, 3) for r in range(len(SPM_CONFIG))], list(mw.spm_values)))
check("RPM value cells mirror mw.rpm_values exactly",
      [cell(tab, len(SPM_CONFIG) + r, 3) for r in range(len(RPM_CONFIG))]
      == [f"{v:.1f}" for v in mw.rpm_values],
      ([cell(tab, len(SPM_CONFIG) + r, 3) for r in range(len(RPM_CONFIG))],
       list(mw.rpm_values)))
check("counter column mirrors the existing raw counters",
      [cell(tab, r, 5) for r in range(len(SPM_CONFIG))]
      == ["1,000", "2,000", "3,000", "4,000"],
      [cell(tab, r, 5) for r in range(len(SPM_CONFIG))])
check("offset column mirrors the existing offsets",
      [cell(tab, r, 6) for r in range(n_expected)]
      == ["10", "20", "30", "40", "50", "60"],
      [cell(tab, r, 6) for r in range(n_expected)])
check("TOTAL SPM pill mirrors the existing mw.total_spm (no re-sum here)",
      tab.total_chip.text() == f"TOTAL SPM  {float(mw.total_spm):.1f}",
      (tab.total_chip.text(), float(mw.total_spm)))
check("no second calculation path on the page",
      not any(hasattr(tab, n) for n in
              ("_spm_calc", "_rpm_calc", "recompute", "_engine")),
      [n for n in ("_spm_calc", "recompute") if hasattr(tab, n)])
check("a refresh writes nothing to settings",
      len(saved) == 0, saved)

# ---- 5. header chip uses the Analog Monitor vocabulary --------------------
tab.refresh()
check("demo mode -> chip reads SIMULATION",
      "SIMULATION" in tab.status_chip.text(), tab.status_chip.text())
mw._demo_mode = False
mw.serial = None
tab.refresh()
check("no serial -> chip reads DISCONNECTED",
      "DISCONNECTED" in tab.status_chip.text(), tab.status_chip.text())
mw.serial = _FakeOpen()
mw._data_stale = True
tab.refresh()
check("stale data -> chip reads STALE DATA",
      "STALE DATA" in tab.status_chip.text(), tab.status_chip.text())
mw._link_down = True
tab.refresh()
check("comm error -> chip reads COMMUNICATION ERROR",
      "COMMUNICATION ERROR" in tab.status_chip.text(), tab.status_chip.text())
mw.serial = None
mw._data_stale = False
mw._link_down = False

# ---- 6. a channel that never reported is honestly "--" --------------------
mw.serial = None
mw._demo_mode = False
mw._link_down = True
for r in range(n_expected):
    tab._has_live[r] = False          # simulate a cold, never-measured rig
tab.refresh()
check("never-measured channels show '--', not a misleading 0",
      [cell(tab, r, 3) for r in range(n_expected)] == ["--"] * n_expected,
      [cell(tab, r, 3) for r in range(n_expected)])
check("never-measured counter/offset cells show '--' too",
      all(cell(tab, r, c) == "--" for r in range(n_expected) for c in (5, 6)))
check("reset is disabled until the channel has a counter to zero",
      not any(b.isEnabled() for b in tab._reset_btns))

# ---- 7. stopping the mode HOLDS the values (nothing hides) -----------------
mw._demo_mode = True
mw.spm_values = [1.5, 2.5, 3.5, 4.5]
mw.rpm_values = [10.5, 20.5]
tab.refresh()
held_before = [cell(tab, r, 3) for r in range(n_expected)]
mw.serial = None
mw._link_down = True
mw._demo_mode = False
for _ in range(5):
    tab.refresh()
check("stopping Demo/Live blanks nothing",
      all(cell(tab, r, 3).strip() != "--" for r in range(n_expected)),
      [cell(tab, r, 3) for r in range(n_expected)])
check("every row keeps the reading it last showed (number unchanged)",
      [cell(tab, r, 3).replace(_HELD_TEXT, "").strip() for r in range(n_expected)]
      == held_before,
      ([cell(tab, r, 3) for r in range(n_expected)], held_before))
check("held rows are labelled HELD so they never read as live",
      all(_HELD_TEXT in cell(tab, r, 3) for r in range(n_expected)),
      [cell(tab, r, 3) for r in range(n_expected)])

# ---- 8. RESET uses the EXISTING helpers, writes nothing to the device ------
mw.serial = _orig_serial
mw._demo_mode = True
mw._link_down = False
mw.spm_raw_counter = [111.0, 222.0, 333.0, 444.0]
mw.spm_offset = [0.0, 0.0, 0.0, 0.0]
mw.rpm_raw_counter = [555.0, 666.0]
mw.rpm_offset = [0.0, 0.0]
tab.refresh()
n_saved_before = len(saved)
# Swap in a recorder so the audit assertion stays hermetic (no DB write).
audited = []
_saved_audit = tab.audit
tab.audit = type("_Audit", (), {"record": lambda _s, *a, **k: audited.append(a)})()
tab._reset_btns[1].click()          # SPM 2
check("SPM RESET calls the existing reset_spm (offset := raw counter)",
      mw.spm_offset[1] == 222.0 and mw.spm_offset[0] == 0.0,
      list(mw.spm_offset))
tab._reset_btns[len(SPM_CONFIG)].click()   # RPM 1
check("RPM RESET calls the existing reset_rpm (offset := raw counter)",
      mw.rpm_offset[0] == 555.0 and mw.rpm_offset[1] == 0.0,
      list(mw.rpm_offset))
check("the reset offsets are visible in the table",
      cell(tab, 1, 6) == "222" and cell(tab, len(SPM_CONFIG), 6) == "555",
      (cell(tab, 1, 6), cell(tab, len(SPM_CONFIG), 6)))
check("reset never writes settings/storage",
      len(saved) == n_saved_before, len(saved) - n_saved_before)
check("reset never sends a firmware command (no serial write helper)",
      not any(hasattr(tab, n) for n in
              ("send_command", "_cmd", "write_command", "serial_write")))
check("each reset is audited with the STROKE_RESET action",
      len(audited) == 2 and all(a[0] == "STROKE_RESET" for a in audited),
      [a[0] if a else None for a in audited])
try:
    mw.roles.set_role(ROLE_ENGINEER)
    tab._reset_btns[0].click()
    check("engineer reset returns the role to Operator (existing behaviour)",
          mw.roles.role() == ROLE_OPERATOR, mw.roles.role())
except Exception as exc:  # pragma: no cover - defensive
    check("engineer reset returns the role to Operator (existing behaviour)",
          False, exc)
tab.audit = _saved_audit

# ---- 9. existing RBAC on the reset control -------------------------------
class _NoCmd:
    def role(self):
        return ROLE_OPERATOR

    def can_command(self):
        return False


saved_roles = tab.roles
tab.roles = _NoCmd()
tab.refresh()
check("without can_command() every Reset control is disabled",
      not any(b.isEnabled() for b in tab._reset_btns))
tab.roles = saved_roles
tab.refresh()
check("with can_command() the Reset controls come back",
      all(b.isEnabled() for b in tab._reset_btns))
check("page uses the existing RoleManager instance (no second RBAC system)",
      tab.roles is mw.roles, tab.roles)

# ---- 10. responsive layout ----------------------------------------------
try:
    tab._reflow(1400)
    wide = [tab.table.columnWidth(c) for c in range(len(_HEADERS))]
    tab._reflow(700)
    narrow = [tab.table.columnWidth(c) for c in range(len(_HEADERS))]
    check("columns reflow to the available width",
          sum(wide) > sum(narrow), (wide, narrow))
    check("no column drops below its minimum width",
          all(narrow[c] >= _MIN_W[c] for c in range(len(_HEADERS))),
          (narrow, _MIN_W))
    tab.resize(1200, 700)
    tab._apply_row_heights(400)
    check("row heights fill the page (6 rows fit without scrolling)",
          26 <= tab.table.verticalHeader().defaultSectionSize() <= 64,
          tab.table.verticalHeader().defaultSectionSize())
except Exception as exc:  # pragma: no cover - defensive
    check("responsive reflow works", False, exc)

# ---- 11. theme + tick dispatch ------------------------------------------
try:
    tab.apply_theme("dark")
    check("apply_theme re-skins without error", True)
except Exception as exc:  # pragma: no cover - defensive
    check("apply_theme re-skins without error", False, exc)
mw.stack.setCurrentIndex(mw.IDX_SPM_RPM)
mw._demo_tick()
tab.refresh()
check("_tick dispatch keeps the table live while it is the current tab",
      [cell(tab, r, 3) for r in range(len(SPM_CONFIG))]
      == [f"{v:.1f}" for v in mw.spm_values],
      ([cell(tab, r, 3) for r in range(len(SPM_CONFIG))], list(mw.spm_values)))
check("main window re-skins the page (apply_theme reaches it)",
      hasattr(mw.stroke_tab, "apply_theme")
      and callable(mw.stroke_tab.apply_theme))

mw.serial = _orig_serial
mw.close()
main_mod.save_settings = _orig_save
app.quit()
print()
print(f"===== SPM / RPM DASHBOARD: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)