"""DIGITAL SENSOR page (digitalsensor.txt).

A centralized, display-and-navigation layer over the application's EXISTING
digital sensors. It organises three groups:

    DIGITAL SENSOR
      |
      +-- BLOCK POSITION  -> existing Block Position / encoder calibration
      +-- SPM             -> SPM 1, SPM 2, SPM 3, SPM 4 (independent channels)
      +-- RPM             -> the existing RPM channels

WHAT THIS PAGE DELIBERATELY DOES NOT DO
    It contains no calculation, no calibration table and no storage of its own.
    Every live number is read straight from the single existing source of truth
    (PumpDashboard.spm_values / rpm_values / block_position_ft / cal_status and
    the firmware-reported encoder state), and every configuration action is a
    navigation to the page that already owns that calibration:

      BLOCK POSITION  -> BlockPositionTab   (BlockPositionTab._on_calibrate)
      SPM 1-4 / RPM   -> StrokeRpmTab       (SPM / RPM monitor table)

    So SPM 1-4 stay four INDEPENDENT channels: this page only links to each of
    them, and changing one never touches the others.

RBAC
    Access control is the existing one - this page introduces no second system.
    Every OPERATOR and ENGINEER may view status, live values and calibration
    state, and open the existing calibration pages (digitalsensor.txt §10).
    Editing stays where it already lives: the destination pages keep their own
    role gates, and this page never writes a calibration value.

STATUS
    Status text is taken from the existing status architecture - the firmware
    `calStatus` vocabulary for the encoder, the dashboard link/stale/demo layer
    for every digital channel, and the existing per-channel status list. No
    duplicate fault-detection logic is invented here.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QGroupBox,
    QPushButton, QScrollArea, QFrame, QSizePolicy,
)
from PyQt5.QtCore import Qt

from ..config import SPM_CONFIG, RPM_CONFIG, BG_CARD, BORDER, TEXT_DARK, TEXT_MID
from ..services import digital_calibration as digcal
from ..dialogs.digital_sensor_calibration_dialog import (
    DigitalSensorCalibrationDialog,
)
from .. import theme


# Status palette taken from the existing theme tokens (identical names to the
# Dashboard status helpers) - no new colours.
def _normal():
    return theme.current().color("CAL_MAX_CLR")


def _warn():
    return theme.current().color("CAL_BAND_CLR")


def _bad():
    return theme.current().color("ACCENT")


def _soft():
    return theme.current().color("TEXT_LITE")


ENTRY_MIN_W = 190
ENTRY_MAX_W = 320
READOUT_H = 44


class _SensorEntry(QGroupBox):
    """One digital-sensor channel: title, live value, status line and a button
    that opens the existing calibration page that owns the channel."""

    def __init__(self, name, caption, on_open, parent=None):
        super().__init__(name, parent)
        self._last_value = ""          # last REAL reading, kept for HELD display
        self._last_unit = ""
        self._caption = caption
        self._on_open = on_open

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        cap = QLabel(caption)
        cap.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px;")
        cap.setWordWrap(True)
        root.addWidget(cap)

        # White digital readout screen - the same look as the Dashboard and
        # Block Position value boxes.
        self.readout = QFrame(self)
        self.readout.setObjectName("digReadout")
        self.readout.setFrameShape(QFrame.StyledPanel)
        self.readout.setFixedHeight(READOUT_H)
        rl = QHBoxLayout(self.readout)
        rl.setContentsMargins(6, 0, 6, 0)
        self.value = QLabel("--")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setStyleSheet(
            "color:#111111; background:transparent; font-family:'Consolas';"
            " font-size:24px; font-weight:bold;")
        rl.addWidget(self.value)
        root.addWidget(self.readout)

        self.unit = QLabel("")
        self.unit.setAlignment(Qt.AlignCenter)
        self.unit.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px; font-weight:600;")
        root.addWidget(self.unit)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        self.open_btn = QPushButton("OPEN")
        self.open_btn.setFixedHeight(26)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._on_open)
        root.addWidget(self.open_btn)

        self.setMinimumWidth(ENTRY_MIN_W)
        # Cap the width so a single-option group (Block Position) does not
        # stretch one readout across the whole page.
        self.setMaximumWidth(ENTRY_MAX_W)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._restyle()

    # -- value binding -----------------------------------------------------
    def set_reading(self, value, unit, status, color):
        """Bind one live value. `value` is None when the existing source has no
        reading for this channel, which is shown honestly as '--'."""
        if value is None:
            self.value.setText("--")
            self.unit.setText("")
            self._restyle(held=False)
        else:
            self.value.setText(value)
            self.unit.setText(unit)
            self._last_value = value
            self._last_unit = unit
            self._restyle(held=False)
        self.status.setText(status)
        self.status.setStyleSheet(self._status_style(color))

    def show_held(self):
        """Freeze the last real reading (link stopped) instead of hiding it, so
        a stopped mode never blanks the page."""
        if not self._last_value:
            self.set_reading(None, "", "-- NO SIGNAL", _soft())
            return False
        self.value.setText(self._last_value)
        self.unit.setText(self._last_unit)
        self._restyle(held=True)
        self.status.setText("HELD — LINK STOPPED")
        self.status.setStyleSheet(self._status_style(_warn()))
        return True

    # -- styling -----------------------------------------------------------
    def _restyle(self, held=False):
        self.setStyleSheet(
            f"QGroupBox {{ color:{TEXT_DARK.name()}; font-family:'Segoe UI';"
            " font-size:12px; font-weight:bold; border:1px solid "
            f"{BORDER.name()}; border-radius:10px; margin-top:10px;"
            f" padding-top:4px; background:{BG_CARD.name()}; }}"
            "QGroupBox::title { subcontrol-origin:margin; left:12px; top:0px;"
            " padding:2px 8px;"
            f" color:{TEXT_DARK.name()}; background:{BG_CARD.name()};"
            " border-radius:5px; }")
        self.readout.setStyleSheet(
            "QFrame#digReadout { background:#FFFFFF; border:1px solid #E4DCCE;"
            " border-radius:8px; }")
        color = "#8A93A0" if held else "#111111"
        self.value.setStyleSheet(
            f"color:{color}; background:transparent; font-family:'Consolas';"
            " font-size:24px; font-weight:bold;")
        self.open_btn.setStyleSheet(self._button_style())

    def _status_style(self, color):
        return (f"color:{color.name()}; background:transparent;"
                " font-family:'Segoe UI'; font-size:10px; font-weight:600;")

    def _button_style(self):
        t = theme.current()
        return (
            f"QPushButton {{ background:{t.color_name('ACCENT')}; color:white;"
            " border:none; border-radius:6px; font-family:'Segoe UI';"
            " font-size:11px; font-weight:bold; padding:2px 14px; }"
            f"QPushButton:hover {{ background:{t.color_name('CAL_BAND_CLR')}; }}"
            f"QPushButton:disabled {{ background:{t.color_name('BORDER')};"
            " color:#8A93A0; }")

    def apply_theme(self):
        self._restyle()
        self.status.setStyleSheet(self._status_style(_soft()))


class DigitalSensorTab(QWidget):
    def __init__(self, main_window, roles, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.roles = roles
        self.entries = {}          # channel key -> _SensorEntry
        self._selected = None      # §1: no sensor is selected on entry
        self._build()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build(self):
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        container = QWidget()
        container.setObjectName("dig_scroll_container")
        inner = QVBoxLayout(container)
        inner.setContentsMargins(12, 10, 12, 12)
        inner.setSpacing(12)
        scroll.setWidget(container)
        page.addWidget(scroll)

        title = QLabel("DIGITAL SENSOR")
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:15px; font-weight:bold;")
        inner.addWidget(title)

        self.note = QLabel(
            "Central view of the existing digital sensors. Every value is read "
            "from the live source of truth and every calibration is opened in "
            "the page that already owns it - nothing is recalculated or stored "
            "here.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:11px;")
        inner.addWidget(self.note)

        # -- 1. SELECT A SENSOR, then 2. pick an action (digitalsensor1.txt §1)
        # The calibration page is NEVER opened automatically here: the Engineer
        # must select the sensor and then press CALIBRATION.
        inner.addWidget(self._build_menu())
        # inner.addWidget(self._build_details())

        # -- BLOCK POSITION --------------------------------------------
        self.block_entry = _SensorEntry(
            "BLOCK POSITION",
            "Digital sensor configuration — encoder pulses, calibration "
            "anchors and pulses/feet.",
            self._goto_block_position)
        self.entries["block_position"] = self.block_entry
        inner.addWidget(self._section("BLOCK POSITION", [self.block_entry]))

        # -- SPM (four independent channels) ----------------------------
        self.spm_entries = []
        for i, cfg in enumerate(SPM_CONFIG):
            entry = _SensorEntry(
                cfg["name"],
                "Independent digital input / calibration channel.",
                self._goto_spm_rpm)
            self.spm_entries.append(entry)
            self.entries[cfg["spm_key"]] = entry
        inner.addWidget(self._section(
            "SPM   —   SPM 1 · SPM 2 · SPM 3 · SPM 4", self.spm_entries))

        # -- RPM --------------------------------------------------------
        self.rpm_entries = []
        for cfg in RPM_CONFIG:
            entry = _SensorEntry(
                cfg["name"],
                "RPM sensor configuration — independent of Block Position "
                "and SPM.",
                self._goto_spm_rpm)
            self.rpm_entries.append(entry)
            self.entries[cfg["rpm_key"]] = entry
        inner.addWidget(self._section("RPM", self.rpm_entries))

        inner.addStretch(1)

        self.footer = QLabel()
        self.footer.setWordWrap(True)
        inner.addWidget(self.footer)

    # ------------------------------------------------------------------
    # §1 DIGITAL SENSOR MENU — select a sensor, THEN choose an action.
    # Nothing is opened automatically: the CALIBRATION page appears only
    # after the Engineer selects a sensor and presses CALIBRATION.
    # ------------------------------------------------------------------
    def _build_menu(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        cap = QLabel("SELECT SENSOR")
        cap.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px; font-weight:bold;"
            " letter-spacing:1px;")
        head.addWidget(cap)
        self.sel_label = QLabel("SELECTED SENSOR: — none selected —")
        self.sel_label.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:12px; font-weight:bold;")
        head.addWidget(self.sel_label)
        head.addStretch(1)
        lay.addLayout(head)

        self.sel_btns = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        for key, label in self._sensor_options():
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda _c, k=key: self._select(k))
            self.sel_btns[key] = btn
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)

        # The five actions from §1. Only CALIBRATION opens a window; the other
        # four focus their section of the detail panel below.
        self.action_btns = {}
        arow = QHBoxLayout()
        arow.setSpacing(6)
        for name in ("LIVE DATA", "CALIBRATION", "CONFIGURATION",
                     "DIAGNOSTICS", "SENSOR STATUS"):
            btn = QPushButton(name)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(False)          # enabled only once a sensor is chosen
            btn.clicked.connect(lambda _c, n=name: self._do_action(n))
            self.action_btns[name] = btn
            arow.addWidget(btn)
        arow.addStretch(1)
        lay.addLayout(arow)
        self._restyle_buttons()
        return box

    def _sensor_options(self):
        """The selectable digital sensors, in §1 order."""
        opts = [("block_position", "BLOCK POSITION")]
        opts += [(cfg["spm_key"], cfg["name"]) for cfg in SPM_CONFIG]
        # §1 lists RPM as ONE option: one RPM calibration page covering the
        # existing RPM channels (the active one is picked inside the page).
        opts.append(("rpm", "RPM"))
        return opts

    def _select(self, key):
        self._selected = key
        for k, btn in self.sel_btns.items():
            btn.setChecked(k == key)
        for btn in self.action_btns.values():
            btn.setEnabled(True)
        if key == "block_position":
            name = "BLOCK POSITION"
        elif key == "rpm":
            name = "RPM"
        else:
            name = self._entry_name_for_key(key)
        self.sel_label.setText(f"SELECTED SENSOR: {name}")
        self.refresh()
        self._focus("LIVE DATA")

    def _entry_name_for_key(self, key):
        for cfg in SPM_CONFIG:
            if cfg["spm_key"] == key:
                return cfg["name"]
        for cfg in RPM_CONFIG:
            if cfg["rpm_key"] == key:
                return cfg["name"]
        return key

    def _do_action(self, name):
        if self._selected is None:
            return
        if name == "CALIBRATION":
            self._open_calibration()
            return
        self._focus(name)

    def _focus(self, section):
        self._active_section = section
        for name, box in self._section_boxes.items():
            box.setProperty("focused", name == section)
        self._restyle_sections()
        box = self._section_boxes.get(section)
        if box is not None:
            try:
                self._details_scroll.ensureWidgetVisible(box, 0, 12)
            except Exception:
                pass
        self.refresh()

    def _open_calibration(self):
        """Open the calibration page for the selected sensor — and nothing else.

        SPM / RPM get the new digital pulse calibration window; BLOCK POSITION
        keeps its EXISTING encoder calibration page (never duplicated)."""
        key = self._selected
        if key is None:
            return None
        if key == "block_position":
            self._goto_block_position()
            return None
        if key == "rpm":
            kind, idx = "RPM", 0
        else:
            kind, idx = "SPM", next(
                (i for i, cfg in enumerate(SPM_CONFIG)
                 if cfg["spm_key"] == key), 0)
        dlg = DigitalSensorCalibrationDialog(
            kind, idx, self.mw, self.roles,
            getattr(self.mw, "audit", None), self)
        if self.window():
            pw = self.window().frameGeometry()
            dlg.move(pw.center() - dlg.rect().center())
        dlg.exec_()
        # The dialog reloads + re-applies on save; refresh anyway so this page
        # always shows the curve that is really active.
        self.refresh()
        return dlg

    # ------------------------------------------------------------------
    # Detail panel — LIVE DATA / CONFIGURATION / DIAGNOSTICS / SENSOR STATUS
    # ------------------------------------------------------------------
    def _build_details(self):
        self._active_section = "LIVE DATA"
        box = QWidget()
        box.setObjectName("dig_details")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head = QLabel(
            "Select a sensor above, then choose an action. CALIBRATION is the "
            "only action that opens a calibration page — it appears only after "
            "you ask for it.")
        head.setWordWrap(True)
        head.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:11px;")
        outer.addWidget(head)

        self._section_boxes = {}
        self._rows = {}
        self._kv_store = {}
        for name in ("LIVE DATA", "CONFIGURATION", "DIAGNOSTICS", "SENSOR STATUS"):
            inner_box = QWidget()
            inner_box.setProperty("section", name)
            lay = QVBoxLayout(inner_box)
            lay.setContentsMargins(8, 6, 8, 8)
            lay.setSpacing(4)
            title = QLabel(name)
            title.setObjectName("digSectionTitle")
            lay.addWidget(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(3)
            self._rows[name] = grid
            lay.addLayout(grid)
            self._section_boxes[name] = inner_box
            outer.addWidget(inner_box)

        # CALIBRATION status is part of SENSOR STATUS but is also shown for the
        # whole rig at the bottom of the page.
        self.cal_status_box = QGroupBox("CALIBRATION STATUS — ALL DIGITAL SENSORS")
        self.cal_status_box.setStyleSheet(self._group_style())
        self.cal_status_lay = QVBoxLayout(self.cal_status_box)
        self.cal_status_lay.setSpacing(2)
        outer.addWidget(self.cal_status_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(box)
        self._details_scroll = scroll
        self._restyle_sections()
        return scroll

    def _kv(self, section, row, label, value="—", color=None, bold=False):
        """Add a label/value pair to a detail grid (created once, updated later).

        Widgets are tracked PER SECTION: the four detail sections reuse the same
        row numbers and some of the same labels ("Sensor:", "Calibration:"), so a
        shared registry would let one section overwrite another's widget."""
        grid = self._rows[section]
        store = self._kv_store.setdefault(section, {})
        key = f"{row}:{label}"
        if key not in store:
            k = QLabel(label)
            k.setStyleSheet(
                f"color:{TEXT_MID.name()}; background:transparent;"
                " font-family:'Segoe UI'; font-size:11px;")
            grid.addWidget(k, row, 0)
            v = QLabel(str(value))
            grid.addWidget(v, row, 1)
            store[key] = v
        self._style_kv(store[key], value, color, bold)
        return store[key]

    @staticmethod
    def _style_kv(widget, text, color=None, bold=False):
        widget.setText(str(text))
        if color is not None:
            fg = color.name()
        else:
            fg = TEXT_DARK.name()
        widget.setStyleSheet(
            f"color:{fg}; background:transparent;"
            " font-family:'Segoe UI'; font-size:11px;"
            f"{' font-weight:bold;' if bold else ''}")

    def _kv_set(self, section, row, label, text, color=None, bold=False):
        store = self._kv_store.setdefault(section, {})
        key = f"{row}:{label}"
        if key not in store:
            return self._kv(section, row, label, text, color, bold)
        widget = store[key]
        self._style_kv(widget, text, color, bold)
        return widget

    def _group_style(self):
        return (f"QGroupBox {{ color:{TEXT_MID.name()}; font-family:'Segoe UI';"
                " font-size:11px; font-weight:bold; border:1px solid "
                f"{BORDER.name()}; border-radius:8px; margin-top:8px;"
                f" padding-top:6px; background:{BG_CARD.name()}; }}")

    def _restyle_buttons(self):
        for btn in list(getattr(self, "sel_btns", {}).values()):
            btn.setStyleSheet(self._menu_btn_style(False))
        for btn in list(getattr(self, "action_btns", {}).values()):
            btn.setStyleSheet(self._menu_btn_style(False, action=True))

    def _menu_btn_style(self, active, action=False):
        t = theme.current()
        base = t.color_name("ACCENT")
        bg = base if active else t.color_name("BG_CARD")
        fg = "white" if active else t.color_name("TEXT_DARK")
        border = "none" if active else f"1px solid {t.color_name('BORDER')}"
        return (f"QPushButton {{ background:{bg}; color:{fg}; border:{border};"
                " border-radius:6px; font-family:'Segoe UI'; font-size:11px;"
                " font-weight:bold; padding:2px 12px; }"
                f"QPushButton:hover {{ background:{t.color_name('CAL_BAND_CLR')};"
                f" color:{'white' if active else t.color_name('TEXT_DARK')}; }}")

    def _restyle_sections(self):
        t = theme.current()
        accent = t.color_name("ACCENT")
        border = t.color_name("BORDER")
        for name, box in getattr(self, "_section_boxes", {}).items():
            focused = (name == getattr(self, "_active_section", ""))
            box.setStyleSheet(
                f"QWidget {{ background:{t.color_name('BG_CARD')};"
                f" border:1px solid {accent if focused else border};"
                f" border-radius:8px; }}"
                f"QLabel#digSectionTitle {{ color:{accent if focused else t.color_name('TEXT_MID')};"
                " background:transparent; font-family:'Segoe UI'; font-size:11px;"
                " font-weight:bold; letter-spacing:1px; }")
        if hasattr(self, "cal_status_box"):
            self.cal_status_box.setStyleSheet(self._group_style())

    # ------------------------------------------------------------------
    # Selected-sensor detail values
    # ------------------------------------------------------------------
    def _selected_channel(self):
        """(kind, idx) for the selected sensor, or (None, None) for
        BLOCK POSITION / nothing selected."""
        key = self._selected
        if key is None or key == "block_position":
            return None, None
        if key == "rpm":
            return "RPM", 0
        for i, cfg in enumerate(SPM_CONFIG):
            if cfg["spm_key"] == key:
                return "SPM", i
        return None, None

    def _sensor_status_text(self, kind, idx, value):
        """§15 status vocabulary, from the EXISTING link/fault architecture."""
        mw = self.mw
        if kind is None:
            status = (mw.cal_status or "NO_CALIBRATION").upper()
            if not self._link_ok():
                return "NO SIGNAL", _soft()
            if status == "VALID":
                return "CONNECTED", _normal()
            if status == "OUT_OF_RANGE":
                return "FAULT", _bad()
            return "CALIBRATION REQUIRED", _warn()
        if mw._demo_mode:
            return ("ACTIVE", _normal()) if value is not None else (
                "NO SIGNAL", _bad())
        if not (mw.serial and mw.serial.is_open):
            return "NO SIGNAL", _bad()
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", _bad()
        if getattr(mw, "_data_stale", False):
            return "NO SIGNAL", _bad()
        if value is None:
            return "NO SIGNAL", _bad()
        if digcal.cal_status(kind, idx) != digcal.CAL_VALID:
            return "CALIBRATION REQUIRED", _warn()
        return "ACTIVE", _normal()

    def _refresh_details(self):
        if not hasattr(self, "_rows") or "LIVE DATA" not in self._rows:
            return
        kind, idx = self._selected_channel()
        mw = self.mw

        # -- LIVE DATA (§10/§11 raw vs calculated vs calibrated) ----------
        if self._selected is None:
            for label in ("Value:", "Raw / Measured:", "Calibrated:",
                          "Counter:", "Offset:"):
                self._kv_set("LIVE DATA", 0, label, "—", _soft())
        elif kind is None:
            pos = self._fmt(mw.block_position_ft, 2) or "—"
            ticks = self._fmt(mw.current_ticks, 0) or "—"
            anchors = list(mw.cal_points() or [])
            zero_ft = 0.0
            for counter, feet in anchors:
                if counter == mw.current_ticks:
                    zero_ft = feet
            self._kv_set("LIVE DATA", 0, "Value:", f"{pos} FT", TEXT_DARK, True)
            self._kv_set("LIVE DATA", 0, "Raw / Measured:",
                         f"encoder counter {ticks} ticks", _soft())
            self._kv_set("LIVE DATA", 0, "Calibrated:",
                         "from the active encoder calibration", _soft())
            self._kv_set("LIVE DATA", 0, "Counter:", ticks)
            self._kv_set("LIVE DATA", 0, "Anchor / Offset:",
                         self._fmt(zero_ft, 2) or "—")
        else:
            measured = mw.digital_measured(kind, idx)
            value = mw.digital_value(kind, idx)
            record = digcal.record_for(kind, idx)
            counter = (mw.spm_raw_counter[idx] if kind == "SPM"
                       else mw.rpm_raw_counter[idx])
            offset = (mw.spm_offset[idx] if kind == "SPM"
                      else mw.rpm_offset[idx])
            hz = digcal.pulse_rate_hz(kind, record, measured)
            shown = self._fmt(value, 1)
            self._kv_set("LIVE DATA", 0, "Value:",
                         "--" if shown is None else f"{shown} {digcal.unit(kind)}",
                         TEXT_DARK if shown is not None else _soft(), True)
            self._kv_set(
                "LIVE DATA", 0, "Raw / Measured:",
                "--" if measured is None
                else f"{measured:,.1f} {digcal.unit(kind)}"
                     f"  ({hz:,.1f} Hz)" if hz is not None else "--",
                TEXT_MID if measured is not None else _soft())
            self._kv_set(
                "LIVE DATA", 0, "Calibrated:",
                "--" if shown is None
                else (f"{shown} {digcal.unit(kind)}" if digcal.is_calibrated(kind, idx)
                      else f"{shown} {digcal.unit(kind)} (uncalibrated passthrough)"),
                TEXT_MID if shown is not None else _soft())
            self._kv_set("LIVE DATA", 0, "Counter:",
                         self._fmt(counter, 0) or "--", TEXT_MID)
            self._kv_set("LIVE DATA", 0, "Offset:",
                         self._fmt(offset, 0) or "--", TEXT_MID)

        # -- CONFIGURATION (§20) ------------------------------------------
        if kind is None:
            self._kv_set("CONFIGURATION", 0, "Sensor:",
                         "BLOCK POSITION" if self._selected else "—")
            self._kv_set("CONFIGURATION", 0, "Type:", "Digital Pulse")
            self._kv_set("CONFIGURATION", 0, "Input Channel:", "encoder")
            self._kv_set("CONFIGURATION", 0, "Measurement:", "Position (FT)")
            self._kv_set("CONFIGURATION", 0, "Configuration owner:",
                         "Block Position page (existing encoder calibration)")
        elif kind:
            record = digcal.record_for(kind, idx) or {}
            ppr = float(record.get("pulses_per_rev", 1.0) or 1.0)
            self._kv_set("CONFIGURATION", 0, "Sensor:",
                         digcal.channel_label(kind, idx))
            self._kv_set("CONFIGURATION", 0, "Type:", "Digital Pulse")
            self._kv_set("CONFIGURATION", 0, "Input Channel:",
                         f"{digcal.counter_key(kind, idx)}  (device 1)")
            self._kv_set("CONFIGURATION", 0, "Measurement:",
                         digcal.measurement(kind))
            if kind == "RPM":
                self._kv_set("CONFIGURATION", 0, "Pulses / Revolution:",
                             f"{ppr:g}")
            else:
                self._kv_set("CONFIGURATION", 0, "Pulse Configuration:",
                             "1 pulse = 1 stroke")

        # -- DIAGNOSTICS -------------------------------------------------
        if self._selected is None:
            self._kv_set("DIAGNOSTICS", 0, "Link:", "select a sensor", _soft())
        else:
            link, link_color = self._link_text()
            self._kv_set("DIAGNOSTICS", 0, "Link:", link, link_color, True)
            if kind is None:
                source = "encoder position (firmware)"
                port = "—"
            else:
                source = f"{digcal.value_key(kind, idx)} / {digcal.counter_key(kind, idx)}"
                port = digcal.counter_key(kind, idx)
            self._kv_set("DIAGNOSTICS", 0, "Source:", source, TEXT_MID)
            self._kv_set("DIAGNOSTICS", 0, "Counter channel:", port, TEXT_MID)
            age = self._link_age()
            self._kv_set("DIAGNOSTICS", 0, "Last update:", age, TEXT_MID)

        # -- SENSOR STATUS (§14/§15) -------------------------------------
        if self._selected is None:
            self._kv_set("SENSOR STATUS", 0, "Sensor Status:", "—", _soft())
            self._kv_set("SENSOR STATUS", 0, "Calibration:", "—", _soft())
        else:
            value = (None if kind is None
                     else self._fmt(mw.digital_value(kind, idx), 1))
            text, color = self._sensor_status_text(kind, idx, value)
            self._kv_set("SENSOR STATUS", 0, "Sensor Status:", text, color, True)
            if kind is None:
                cal = ((mw.cal_status or "NO_CALIBRATION").upper())
                cal_text = "VALID" if cal == "VALID" else "REQUIRED"
                cal_color = _normal() if cal == "VALID" else _warn()
            else:
                cal_text = digcal.cal_status(kind, idx)
                cal_color = (_normal() if cal_text == digcal.CAL_VALID
                             else _warn())
            self._kv_set("SENSOR STATUS", 0, "Calibration:", cal_text,
                         cal_color, True)

        self._refresh_cal_status_list()

    def _link_text(self):
        mw = self.mw
        if mw._demo_mode:
            return "SIMULATION", _normal()
        if not (mw.serial and mw.serial.is_open):
            return "DISCONNECTED", _bad()
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", _bad()
        if getattr(mw, "_data_stale", False):
            return "STALE DATA", _warn()
        return "CONNECTED", _normal()

    def _link_age(self):
        import time as _time
        stamp = getattr(self.mw, "_last_valid_time", None)
        if not stamp:
            return "never"
        try:
            return f"{max(0.0, _time.monotonic() - float(stamp)):.1f} s ago"
        except (TypeError, ValueError):
            return "never"

    def _refresh_cal_status_list(self):
        """§14: the calibration status of EVERY digital sensor."""
        while self.cal_status_lay.count():
            item = self.cal_status_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        rows = [("BLOCK POSITION",
                 "VALID" if (self.mw.cal_status or "").upper() == "VALID"
                 else "REQUIRED")]
        for i, cfg in enumerate(SPM_CONFIG):
            rows.append((cfg["name"], digcal.cal_status("SPM", i)))
        rows.append(("RPM", digcal.cal_status("RPM", 0)))
        for name, status in rows:
            lab = QLabel(f"{name}: {status}")
            color = _normal() if status == digcal.CAL_VALID else _warn()
            lab.setStyleSheet(
                f"color:{color.name()}; background:transparent;"
                " font-family:'Segoe UI'; font-size:11px;")
            self.cal_status_lay.addWidget(lab)

    def _section(self, title, entries):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QLabel(title)
        head.setStyleSheet(
            f"color:{TEXT_DARK.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:12px; font-weight:bold;"
            " letter-spacing:1px;")
        lay.addWidget(head)

        grid = QGridLayout()
        grid.setSpacing(10)
        for i, entry in enumerate(entries):
            grid.addWidget(entry, 0, i)
            grid.setColumnStretch(i, 1)
        lay.addLayout(grid)
        return box

    # ------------------------------------------------------------------
    # Navigation to the EXISTING calibration pages (no duplicate editors)
    # ------------------------------------------------------------------
    def _goto_block_position(self):
        """Open the EXISTING Block Position page and scroll its calibration
        section into view - the calibration table itself stays owned by
        BlockPositionTab (no second editor here)."""
        self.mw.tab_bar.set_current(self.mw.IDX_BLOCK_POSITION)
        reveal = getattr(self.mw.block_tab, "reveal_calibration", None)
        if callable(reveal):
            reveal()

    def _goto_spm_rpm(self):
        self.mw.tab_bar.set_current(self.mw.IDX_SPM_RPM)

    # ------------------------------------------------------------------
    # Status helpers - all reuse the existing status architecture
    # ------------------------------------------------------------------
    def _link_ok(self):
        mw = self.mw
        return bool(mw._demo_mode or (mw.serial and mw.serial.is_open
                                     and not mw._link_down))

    def _channel_status(self, value):
        """Status for a digital channel: link/stale/demo layer first (the same
        one the Dashboard uses), then NO SIGNAL when the source has no value."""
        mw = self.mw
        if not self._link_ok():
            return "", _soft()
        if value is None:
            return "● NO SIGNAL", _warn()
        if mw._demo_mode:
            return "● SIMULATED", _normal()
        if mw._data_stale:
            return "● STALE DATA", _warn()
        return "● LIVE", _normal()

    def _encoder_status(self):
        """Encoder status comes straight from the firmware `calStatus`
        vocabulary - never a locally invented fault. The demo marker is
        appended rather than replacing it, so the calibration state stays
        visible while the source is simulated."""
        mw = self.mw
        status = (mw.cal_status or "NO_CALIBRATION").upper()
        if not self._link_ok():
            return "", _soft()
        if status == "VALID":
            text, color = "● VALID", _normal()
        elif status == "OUT_OF_RANGE":
            text, color = "● OUT OF RANGE", _bad()
        else:
            text, color = "● CALIBRATION REQUIRED", _warn()
        if mw._demo_mode:
            return f"{text} · SIMULATED", color
        return text, color

    @staticmethod
    def _fmt(value, decimals=1):
        try:
            return f"{float(value):,.{decimals}f}"
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Refresh (pure display binding - no calculation lives here)
    # ------------------------------------------------------------------
    def refresh(self):
        mw = self.mw
        self._refresh_details()

        # Link stopped (Demo off AND no device): freeze every entry on its last
        # real reading instead of blanking the page.
        if not self._link_ok():
            for entry in self.entries.values():
                entry.show_held()
            self._footer()
            return

        # -- BLOCK POSITION ---------------------------------------------
        block = self._fmt(mw.block_position_ft, 2)
        status, color = self._encoder_status()
        self.block_entry.set_reading(block, "FT", status, color)

        # -- SPM 1..4 (independent channels, independent selections) -----
        for i, cfg in enumerate(SPM_CONFIG):
            value = mw.spm_values[i]
            shown = self._fmt(value, 1)
            status, color = self._channel_status(shown)
            self.entries[cfg["spm_key"]].set_reading(shown, "SPM", status,
                                                      color)

        # -- RPM ------------------------------------------------------
        for i, cfg in enumerate(RPM_CONFIG):
            value = mw.rpm_values[i]
            shown = self._fmt(value, 1)
            status, color = self._channel_status(shown)
            self.entries[cfg["rpm_key"]].set_reading(shown, "RPM", status,
                                                      color)

        self._footer()

    def _footer(self):
        """Calibration access follows the EXISTING RBAC - the hub only views and
        navigates; the destination pages own every edit."""
        access = ("open and edit the existing calibrations"
                  if self.roles.can_command()
                  else "view the existing calibrations only")
        pts = self.mw.cal_points()
        anchors = f"{len(pts)}/4 anchors" if pts else "not calibrated"
        self.footer.setText(
            f"Encoder calibration: {anchors}.  This role can {access}.  "
            f"Digital pulse calibration (SPM / RPM) is opened from this page "
            f"with the CALIBRATION action, one sensor at a time; every "
            f"channel stores its own independent calibration.")
        self.footer.setStyleSheet(
            f"color:{_soft().name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px;")

    def apply_theme(self, name=None):
        self.note.setStyleSheet(
            f"color:{TEXT_MID.name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:11px;")
        self.footer.setStyleSheet(
            f"color:{_soft().name()}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px;")
        for entry in self.entries.values():
            entry.apply_theme()
        self._restyle_buttons()
        self._restyle_sections()
        self.refresh()