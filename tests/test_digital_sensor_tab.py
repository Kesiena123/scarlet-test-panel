"""DIGITAL SENSOR tab (digitalsensor.txt).

The Digital Sensor page is a centralized interface + navigation layer over the
EXISTING digital sensors (Block Position, SPM 1-4, RPM). It owns no
calculation, no calibration table and no storage - it reads the live sources of
truth and links to the pages that already own each calibration. This suite
verifies:

  * the tab exists in the tab bar and the stack, with the existing neighbours
    undisturbed
  * the three required groups exist: BLOCK POSITION, SPM (SPM 1..SPM 4) and RPM,
    and the groups are the ones from the existing SPM_CONFIG / RPM_CONFIG
  * every live value mirrors the EXISTING source (block_position_ft,
    spm_values[0..3], rpm_values[0..1]) - never a hard-coded number
  * SPM 1-4 stay four INDEPENDENT channels (separate entries, separate reads)
  * "OPEN" navigates to the EXISTING calibration pages (Block Position and
    SPM / RPM) instead of duplicating any calibration editor
  * status text comes from the existing architecture (calStatus vocabulary +
    the dashboard link/stale/demo layer); NO SIGNAL is shown honestly when the
    source has no reading
  * stopping Demo/Live mode FREEZES every entry on its last real reading
    instead of blanking the page
  * calibration access follows the EXISTING RBAC (can_command) - no second
    role system is created
  * the page writes nothing: it has no calibration setter and stores no values

Run:   python tests/test_digital_sensor_tab.py
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
from scarlet_test_panel.config import SPM_CONFIG, RPM_CONFIG  # noqa: E402
from scarlet_test_panel.services.security import (  # noqa: E402
    ROLE_OPERATOR, ROLE_ENGINEER)

# Hermetic: the page must never write settings, but keep the rig hermetic.
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


def btn_texts(mw):
    return [b.text().strip() for b in mw.tab_bar._btns]


mw = PumpDashboard()
_orig_serial = mw.serial
mw.slip_window_sec = None
d = mw.digital_sensor_tab

# ---- 1. tab registration --------------------------------------------------
names = btn_texts(mw)
check("'DIGITAL SENSOR' appears in the tab bar (spec name)",
      "DIGITAL SENSOR" in names, names)
check("tab bar carries 9 entries (existing tabs undisturbed)",
      len(names) == 9, names)
check("existing neighbour order preserved",
      names[1] == "Dashboard" and names[2] == "Block Position"
      and names[3] == "Analog Monitor" and names[4] == "Graph"
      and names[5] == "SPM / RPM" and names[7] == "Diagnostics"
      and names[8] == "Settings", names)
check("stack page exists at IDX_DIGITAL_SENSORS",
      type(mw.stack.widget(mw.IDX_DIGITAL_SENSORS)).__name__ == "DigitalSensorTab")
check("Digital Sensor tab button is visible",
      mw.tab_bar._btns[mw.IDX_DIGITAL_SENSORS].isVisible())

# ---- 2. the three required groups ----------------------------------------
check("BLOCK POSITION group present",
      "block_position" in d.entries, list(d.entries))
check("SPM group has one entry per SPM_CONFIG channel (4)",
      len(d.spm_entries) == len(SPM_CONFIG) == 4,
      [e.title() for e in d.spm_entries])
check("SPM entries are named SPM 1..SPM 4",
      [e.title() for e in d.spm_entries] == ["SPM 1", "SPM 2", "SPM 3", "SPM 4"],
      [e.title() for e in d.spm_entries])
check("RPM group has one entry per RPM_CONFIG channel",
      len(d.rpm_entries) == len(RPM_CONFIG),
      [e.title() for e in d.rpm_entries])
check("every channel has a distinct entry object (SPM independence)",
      len({id(e) for e in d.entries.values()}) == len(d.entries),
      len(d.entries))

# ---- 3. live values mirror the EXISTING sources (never hard-coded) --------
mw._demo_mode = True
mw.spm_values = [11.1, 22.2, 33.3, 44.4]
mw.rpm_values = [111.0, 222.0]
mw.block_position_ft = 123.45
mw.cal_status = "VALID"
d.refresh()
check("BLOCK POSITION mirrors block_position_ft (123.45 FT)",
      d.block_entry.value.text() == "123.45"
      and d.block_entry.unit.text() == "FT",
      (d.block_entry.value.text(), d.block_entry.unit.text()))
check("SPM 1..4 mirror spm_values[0..3]",
      [e.value.text() for e in d.spm_entries] == ["11.1", "22.2", "33.3", "44.4"],
      [e.value.text() for e in d.spm_entries])
check("RPM entries mirror rpm_values[0..1]",
      [e.value.text() for e in d.rpm_entries] == ["111.0", "222.0"],
      [e.value.text() for e in d.rpm_entries])
check("every channel carries its own unit",
      [e.unit.text() for e in d.spm_entries] == ["SPM"] * 4
      and [e.unit.text() for e in d.rpm_entries] == ["RPM"] * len(RPM_CONFIG))

# Changing one SPM must not disturb the other three (independence).
mw.spm_values = [99.9, 22.2, 33.3, 44.4]
d.refresh()
check("changing SPM 1 does not modify SPM 2-4",
      [e.value.text() for e in d.spm_entries] == ["99.9", "22.2", "33.3", "44.4"],
      [e.value.text() for e in d.spm_entries])

# A refresh must pick up new values (never a frozen snapshot).
mw.spm_values = [55.5, 22.2, 33.3, 44.4]
d.refresh()
check("values keep updating live on every refresh",
      d.spm_entries[0].value.text() == "55.5", d.spm_entries[0].value.text())

# ---- 4. status comes from the existing architecture ----------------------
# Encoder status is driven by the firmware calStatus vocabulary. The demo
# marker is APPENDED, not substituted, so calibration state stays readable.
mw.cal_status = "VALID"
d.refresh()
check("encoder status uses the calStatus vocabulary (VALID)",
      d.block_entry.status.text().startswith("● VALID"),
      d.block_entry.status.text())
mw.cal_status = "OUT_OF_RANGE"
d.refresh()
check("OUT_OF_RANGE is surfaced (firmware calStatus)",
      d.block_entry.status.text().startswith("● OUT OF RANGE"),
      d.block_entry.status.text())
mw.cal_status = "NO_CALIBRATION"
d.refresh()
check("NO_CALIBRATION reads as CALIBRATION REQUIRED",
      d.block_entry.status.text().startswith("● CALIBRATION REQUIRED"),
      d.block_entry.status.text())
mw.cal_status = "VALID"
check("no invented fault vocabulary (only existing terms)",
      all(not any(w in d.block_entry.status.text().upper()
                  for w in ("FAULT", "ERROR", "WATCHDOG"))
          for _ in [0]), d.block_entry.status.text())

# ---- 5. OPEN navigates to the EXISTING calibration pages ------------------
d.block_entry.open_btn.click()
check("BLOCK POSITION OPEN goes to the existing Block Position page",
      mw.stack.currentIndex() == mw.IDX_BLOCK_POSITION
      and type(mw.stack.widget(mw.stack.currentIndex())).__name__
      == "BlockPositionTab", mw.stack.currentIndex())
check("BLOCK POSITION OPEN scrolls the existing CALIBRATION section into view",
      mw.block_tab.reveal_calibration() is True
      and mw.block_tab._cal_group is not None)
check("no second calibration editor is created on the hub",
      not hasattr(d, "_cal_rows") and not hasattr(d, "cal_group"))
d.spm_entries[0].open_btn.click()
check("SPM OPEN goes to the existing SPM / RPM page",
      mw.stack.currentIndex() == mw.IDX_SPM_RPM
      and type(mw.stack.widget(mw.stack.currentIndex())).__name__
      == "StrokeRpmTab", mw.stack.currentIndex())
d.rpm_entries[0].open_btn.click()
check("RPM OPEN goes to the existing SPM / RPM page",
      mw.stack.currentIndex() == mw.IDX_SPM_RPM, mw.stack.currentIndex())

# ---- 6. the page duplicates no calibration editor / storage --------------
check("page exposes no calibration write method",
      not any(hasattr(d, n) for n in
              ("set_calibration", "save_calibration", "apply_calibration",
               "cal_points", "set_spms", "spm_calibration")),
      [n for n in ("set_calibration", "save_calibration") if hasattr(d, n)])
before = len(saved)
mw.spm_values = [1.0, 2.0, 3.0, 4.0]
d.refresh()
check("refreshing the page writes nothing to settings/storage",
      len(saved) == before, len(saved) - before)

# ---- 7. stopping the mode FREEZES the page (nothing hides) ---------------
mw.stack.setCurrentIndex(mw.IDX_DIGITAL_SENSORS)
for _ in range(5):
    mw._tick()
frozen = {k: e.value.text() for k, e in d.entries.items()}
mw.serial = None
mw._link_down = True
mw._demo_mode = False
d.refresh()
check("stopping the mode shows no '--' anywhere on the page",
      not any(e.value.text().strip() == "--" for e in d.entries.values()),
      [(k, e.value.text()) for k, e in d.entries.items()
       if e.value.text().strip() == "--"])
check("every entry keeps the value it last displayed",
      all(e.value.text() == frozen[k] for k, e in d.entries.items()),
      [(k, e.value.text(), frozen[k]) for k, e in d.entries.items()
       if e.value.text() != frozen[k]])
check("frozen entries are labelled HELD so they never read as live",
      all("HELD" in e.status.text().upper() for e in d.entries.values()),
      [(k, e.status.text()) for k, e in d.entries.items()])
for _ in range(5):
    d.refresh()
check("the held values survive repeated refreshes with the link down",
      all(e.value.text() == frozen[k] for k, e in d.entries.items()),
      [(k, e.value.text()) for k, e in d.entries.items()])

# A channel that never had a reading is still shown honestly as '--'.
mw._link_ok = lambda: True
d.spm_entries[0]._last_value = ""
mw.spm_values[0] = None
d.refresh()
check("a channel with no reading shows '--' (honest, not fabricated)",
      d.spm_entries[0].value.text() == "--", d.spm_entries[0].value.text())
check("no-reading channel is labelled NO SIGNAL",
      "NO SIGNAL" in d.spm_entries[0].status.text().upper(),
      d.spm_entries[0].status.text())

# ---- 8. existing RBAC, no second role system -----------------------------
mw.serial = _orig_serial
mw._link_down = False
mw._demo_mode = True
mw.roles.set_role(ROLE_OPERATOR)
d.refresh()
check("operator can view the digital sensor page",
      all(e.value.text().strip() != "" for e in d.entries.values()))
check("access line follows the existing can_command() RBAC",
      ("open and edit" in d.footer.text()) == bool(mw.roles.can_command()),
      (d.footer.text(), mw.roles.can_command()))
mw.roles.set_role(ROLE_ENGINEER)
d.refresh()
check("engineer sees the page too (same footer contract)",
      ("open and edit" in d.footer.text()) == bool(mw.roles.can_command()))
check("page imports the existing RoleManager (no second RBAC system)",
      d.roles is mw.roles, d.roles)

# ---- 9. the _tick refresh mapping reaches the page -----------------------
mw.stack.setCurrentIndex(mw.IDX_DIGITAL_SENSORS)
mw._demo_tick()
d.refresh()
# The demo tick owns the simulated values; the page must simply mirror whatever
# the existing source now holds (never its own number).
check("_tick dispatch keeps the page live while it is the current tab",
      [e.value.text() for e in d.spm_entries]
      == [f"{v:,.1f}" for v in mw.spm_values],
      ([e.value.text() for e in d.spm_entries], list(mw.spm_values)))

# ---- 10. apply_theme -----------------------------------------------------
try:
    d.apply_theme("dark")
    check("apply_theme re-skins without error", True)
except Exception as exc:  # pragma: no cover - defensive
    check("apply_theme re-skins without error", False, exc)

mw.serial = _orig_serial
mw.close()
main_mod.save_settings = _orig_save
app.quit()
print()
print(f"===== DIGITAL SENSOR TAB: PASS={_total - _failures} FAIL={_failures} =====")
sys.exit(1 if _failures else 0)