# Main Dashboard tab: the DRILLING MONITOR overview (promt3 §15) rendered as a
# compact INDUSTRIAL MONITORING TABLE (dashboardtab.txt). The eleven live
# measurements are table cells with the drilling-table value-over-label
# structure — a large live value, a small unit, and the parameter name on a
# coloured band directly underneath:
#
#     +---------------------+
#     |    4,262.1  ft      |  value area (big number) + status line
#     |    ● TRACKING       |
#     +---------------------+
#     |   MEASURED DEPTH    |  parameter band (group colour, uppercase)
#     +---------------------+
#
#     row 1  MEASURED DEPTH | TVD | BIT DEPTH | BLOCK POSITION | SAMPLE LAG DEPTH
#     row 2  HOOKLOAD VALUE | SPM 1 | SPM 2 | SPM 3 | SPM 4
#     row 3  TOTAL SPM (spans the full table width)
#
# The column count adapts to the window (5 -> 4 -> 3 -> 2 -> 1), so the table
# fills a large monitor, a laptop or a small window without horizontal
# scrolling. The header row keeps the existing DRILLING MONITOR title, the
# Sample Lag offset (Engineer only) and the FT/M toggle.
#
# Colour is a controlled system, not decoration: one family per parameter
# group (depth/position = blue, hookload = amber, pumps/SPM = green), taken
# straight from the existing theme tokens.
#
# The Dashboard is a pure DISPLAY layer. It never owns a calculation:
#   * MEASURED DEPTH / TVD / SAMPLE LAG DEPTH are single-source derived
#     display values on the application model (PumpDashboard.measured_depth_ft
#     / tvd_ft / sample_lag_depth_ft) that reuse the existing Bit
#     Position/Bit Depth engine — they do NOT add a second measurement
#     calculation and never hard-code a value. The refresh below reads the
#     engine exactly ONCE per tick (bit_position_ft is the per-tick engine
#     read) and reuses that value for all four depth boxes.
#   * BIT DEPTH mirrors the existing Bit Position/Bit Depth engine value.
#   * BLOCK POSITION mirrors block_position_ft (firmware-authoritative).
#   * HOOKLOAD VALUE comes from the existing channel-0 analog -> hookload
#     chain (mw.hookload_klb) with its fault / status vocabulary.
#   * SPM 1..4 mirror mw.spm_values; TOTAL SPM = mw.total_spm (the sum of the
#     four existing values — no separate pump calculation).
#   * RPM mirrors the selected channel of mw.rpm_values (digitalsensor1.txt §17),
#     the independently calibrated digital RPM reading.
#
# No second serial/sensor loop is created (the main dashboard tick drives this
# tab via mw._tick). Units follow the existing Block Position FT/M toggle
# (single source of truth). With no valid data the cells show '--' and an
# explicit status (e.g. DISCONNECTED / SENSOR FAULT / PIPE IN HOLE NOT SET),
# never a believable value.
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QFrame, QGroupBox, QGridLayout, QPushButton, QDoubleSpinBox, QMenu,
)
from PyQt5.QtCore import Qt, QEvent, QPoint
from PyQt5.QtGui import QFontMetrics

from ..config import FT_TO_M, SPM_CONFIG, RPM_CONFIG
from .. import theme

# The old dashboard's status palette (identical names/colours to the previous
# KEY SENSOR VALUES cards): green normal, amber warn, red bad, faint idle.
def _normal():
    return theme.current().color("CAL_MAX_CLR")
def _warn():
    return theme.current().color("CAL_BAND_CLR")
def _bad():
    return theme.current().color("ACCENT")
def _soft():
    return theme.current().color("TEXT_LITE")


# ── Cell geometry ───────────────────────────────────────────────────────
# The cells are laid out as one continuous table: every cell keeps the SAME
# heights, so rows and columns line up exactly. The chrome mirrors the Block
# Position value boxes (rounded card, bold title, white readout, unit below).
CELL_GAP = 6
MIN_CELL_W = 165
CELL_LABEL_H = 15          # parameter title
CELL_VALUE_H = 44          # white digital readout screen
CELL_UNIT_H = 13           # unit under the readout
CELL_STAT_H = 13           # status / fault line
CELL_PAD_X = 10            # card side padding
CELL_PAD_Y = 5             # card top/bottom padding
CELL_SPACING = 2
SEL_BTN_W = 16            # width of the slot selector triangle
CELL_H = (CELL_LABEL_H + CELL_VALUE_H + CELL_UNIT_H + CELL_STAT_H
          + 3 * CELL_SPACING + 2 * CELL_PAD_Y)
MAX_COLUMNS = 5

# The four EXISTING SPM parameters a Dashboard slot may display
# (spmselection.txt). Names come from SPM_CONFIG, so the selector can only ever
# name the parameters the application already computes - it never introduces a
# new SPM calculation. Default mapping: box 1 -> SPM 1 ... box 4 -> SPM 4.
SPM_SLOT_LABELS = [cfg["name"] for cfg in SPM_CONFIG]
SPM_SLOT_DEFAULT = list(range(len(SPM_SLOT_LABELS)))

# The single RPM box is the SAME kind of configurable display slot (digitalsensor1.txt
# §17: the Dashboard shows SPM 1..4, TOTAL SPM and RPM). It reads the EXISTING
# rpm_values array and only changes what is displayed.
RPM_SLOT_LABELS = [cfg["name"] for cfg in RPM_CONFIG]
RPM_SLOT_DEFAULT = 0

# Readout font size per column count: (primary, secondary). Narrower windows
# get a slightly smaller number so a long value (e.g. a 4-digit hookload) is
# never clipped inside the readout screen.
VALUE_SIZES = {5: (26, 22), 4: (25, 21), 3: (23, 19), 2: (21, 18), 1: (21, 18)}

# Controlled colour system (dashboardtab.txt §7): one colour FAMILY per
# parameter group, taken from the existing theme palette - no invented colours
# and nothing random.
#   Depth / position  -> blue, with a cyan/turquoise variation
#   Hookload          -> its own purple / magenta family
#   Pumps             -> green family
GROUP_TOKENS = {
    "depth": "CAL_MIN_CLR",      # Measured/TVD/Bit depth  -> blue
    "pump": "CAL_MAX_CLR",       # SPM 1-4 + Total SPM     -> green
}
# Groups that use a colour from the theme's sensor trace palette, which already
# carries the purple and turquoise hues the reference uses.
GROUP_SENSOR_IDX = {
    "position": 5,               # Block position / lag    -> turquoise
    "hookload": 4,               # Hookload               -> purple
}


def _group_color(group):
    """Parameter-title colour for a group, resolved for the active theme."""
    t = theme.current()
    idx = GROUP_SENSOR_IDX.get(group)
    if idx is not None:
        return t.sensor_color(idx).name()
    return t.color_name(GROUP_TOKENS.get(group, "CAL_MIN_CLR"))


class _DashCell(QFrame):
    """One monitoring cell, styled like a Block Position screen box.

        +---------------------------+
        | MEASURED DEPTH           |   parameter title (top, group colour)
        | +-----------------------+ |
        | |        4,262.1        | |   white digital readout screen
        | +-----------------------+ |
        |           ft             |   unit
        |      ● TRACKING          |   status (existing fault vocabulary)
        +---------------------------+

    Same chrome as the Block Position value cells (`_cell_box_style` +
    `_block_figure_style` in block_position_tab.py): rounded card, small bold
    title, white Consolas readout, unit underneath, status line below it.

    The API (`set_value` / `set_status` / `clear_status` / `apply_theme` /
    `set_value_size`) and every attribute name are unchanged from the previous
    dashboard card, so the value binding in `refresh()` and the re-skin in
    `apply_theme()` are untouched - only the presentation changed.
    """

    def __init__(self, title, group="depth", primary=True, unit="",
                 selector_items=None, selected=0, on_selected=None,
                 parent=None):
        super().__init__(parent)
        self.primary = primary
        self.title = title
        self.unit_text = unit
        self.group = group
        self.setObjectName("dashCell")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(CELL_PAD_X, CELL_PAD_Y, CELL_PAD_X, CELL_PAD_Y)
        lay.setSpacing(CELL_SPACING)

        # -- parameter title (+ optional slot selector, spmselection.txt) --
        # The triangle lives in the TITLE row, so it can never cover the
        # numeric readout and never overlaps the parameter name.
        self.sel_items = list(selector_items) if selector_items else None
        self.sel_index = 0
        self.on_selected = on_selected
        self.sel_btn = None
        self.sel_menu = None

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(0)
        self.cap = QLabel(title.upper())
        self.cap.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.cap.setFixedHeight(CELL_LABEL_H)
        title_row.addWidget(self.cap, 1)
        if self.sel_items:
            self.sel_btn = QPushButton("▼")
            self.sel_btn.setFixedSize(SEL_BTN_W, CELL_LABEL_H)
            self.sel_btn.setCursor(Qt.PointingHandCursor)
            self.sel_btn.setFocusPolicy(Qt.NoFocus)
            self.sel_btn.setToolTip("Select the SPM parameter shown in this box")
            self.sel_btn.clicked.connect(self._open_selector)
            title_row.addWidget(self.sel_btn, 0)
        lay.addLayout(title_row)
        if self.sel_items:
            self.set_selected(selected)

        # -- white digital readout screen ----------------------------------
        self._last_value = ""      # last REAL reading, kept for HELD display
        self._last_unit = ""
        self.value_area = QFrame(self)
        self.value_area.setObjectName("dashReadout")
        self.value_area.setFrameShape(QFrame.NoFrame)
        va = QHBoxLayout(self.value_area)
        va.setContentsMargins(6, 0, 6, 0)
        va.setSpacing(0)
        self.val = QLabel("--")
        self.val.setAlignment(Qt.AlignCenter)
        va.addWidget(self.val)
        self.value_area.setFixedHeight(CELL_VALUE_H)
        lay.addWidget(self.value_area)

        # -- unit + status ---------------------------------------------------
        self.unit = QLabel(unit)
        self.unit.setAlignment(Qt.AlignCenter)
        self.unit.setFixedHeight(CELL_UNIT_H)
        lay.addWidget(self.unit)

        self.stat = QLabel("")
        self.stat.setAlignment(Qt.AlignCenter)
        self.stat.setFixedHeight(CELL_STAT_H)
        lay.addWidget(self.stat)

        self._val_size = 26 if primary else 22
        # Identical height in every row/column: the table lines up exactly.
        self.setFixedHeight(CELL_H)
        self.setMinimumWidth(MIN_CELL_W)
        self._apply_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The readout is narrower after a column-count change: re-fit the
        # digit size to the new width. The cell's height is fixed and its
        # width comes from the grid, so this cannot feed back on the layout.
        self._fit_value_font()

    def set_value_size(self, size):
        """Adapt the readout font to the current column count (narrow tables
        need a slightly smaller number so digits are never clipped)."""
        size = int(size)
        self._val_size = size
        self._fit_value_font()

    # -- configurable SPM slot (spmselection.txt) --------------------------
    def selected(self):
        """Index of the SPM parameter this box currently displays."""
        return self.sel_index

    def set_selected(self, index, notify=False):
        """Point this slot at one of its selectable parameters and update the
        parameter name with it. The VALUE follows on the next refresh() from
        the existing data source - no calculation happens here."""
        if not self.sel_items:
            return
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        self.sel_index = index if 0 <= index < len(self.sel_items) else 0
        self.cap.setText(self.sel_items[self.sel_index].upper())
        self._sync_selector_menu()
        if notify and self.on_selected is not None:
            self.on_selected(self)

    def _selector_style(self):
        """Small, flat, borderless triangle in the group's parameter colour."""
        return ("QPushButton { background:transparent; border:none;"
                f" color:{_group_color(self.group)}; font-family:'Segoe UI';"
                " font-size:9px; font-weight:bold; padding:0px;"
                " margin-left:2px; }"
                "QPushButton:hover { background:rgba(0,0,0,0.06);"
                " border-radius:3px; }"
                "QPushButton:pressed { background:rgba(0,0,0,0.14); }")

    def _menu_style(self):
        """Compact industrial menu that matches the Dashboard card chrome."""
        t = theme.current()
        return (f"QMenu {{ background:{t.color_name('BG_CARD')};"
                f" border:1px solid {t.color_name('BORDER')};"
                " border-radius:4px; padding:3px; }"
                f"QMenu::item {{ color:{t.color_name('TEXT_DARK')};"
                " background:transparent; padding:5px 20px 5px 8px;"
                " font-family:'Segoe UI'; font-size:11px; }"
                f"QMenu::item:selected {{ background:{_group_color(self.group)};"
                " color:#FFFFFF; }"
                "QMenu::item:checked { font-weight:bold; }"
                f"QMenu::separator {{ height:1px; background:{t.color_name('BORDER')};"
                " margin:3px 4px; }")

    def _build_selector_menu(self):
        self.sel_menu = QMenu(self)
        self.sel_menu.setObjectName("spmSlotMenu")
        for i, label in enumerate(self.sel_items):
            act = self.sel_menu.addAction(label)
            act.setCheckable(True)
            act.setData(i)
            act.triggered.connect(
                lambda _checked=False, a=act: self.set_selected(a.data(), notify=True))
        self.sel_menu.setStyleSheet(self._menu_style())
        self._sync_selector_menu()

    def _sync_selector_menu(self):
        if self.sel_menu is None:
            return
        for i, act in enumerate(self.sel_menu.actions()):
            act.setChecked(i == self.sel_index)

    def _open_selector(self):
        """Open the SPM 1-4 menu right under the triangle. QMenu is a popup:
        it closes on selection and when clicking outside it."""
        if self.sel_menu is None:
            self._build_selector_menu()
        self._sync_selector_menu()
        self.sel_menu.exec_(
            self.sel_btn.mapToGlobal(QPoint(0, self.sel_btn.height() + 1)))

    # -- styling (matches the Block Position value boxes) -----------------
    def _value_style(self, size, held=False):
        # Fixed white readout screen + near-black Consolas digits, exactly as
        # _block_figure_style() renders the Block Position cell. A HELD value
        # (link stopped) is greyed so it can never read as live.
        color = "#8A93A0" if held else "#111111"
        return (f"color:{color}; background:transparent; font-family:'Consolas';"
                f" font-size:{int(size)}px; font-weight:bold;")

    def _apply_style(self, held=False):
        t = theme.current()
        accent = _group_color(self.group)

        self.setStyleSheet(
            f"QFrame#dashCell {{ background:{t.color_name('BG_CARD')};"
            f" border:1px solid {t.color_name('BORDER')};"
            " border-radius:8px; }")
        self.value_area.setStyleSheet(
            "QFrame#dashReadout { background:#FFFFFF;"
            " border:1px solid #E4DCCE; border-radius:8px; }")
        self.val.setStyleSheet(self._value_style(self._val_size, held=held))
        self.unit.setStyleSheet(
            f"color:{t.color_name('TEXT_MID')}; background:transparent;"
            " font-family:'Segoe UI'; font-size:10px; font-weight:600;")
        self.stat.setStyleSheet(self._stat_style())
        self.cap.setStyleSheet(
            f"color:{accent}; background:transparent; font-family:'Segoe UI';"
            " font-size:10px; font-weight:bold;")
        if self.sel_btn is not None:
            self.sel_btn.setStyleSheet(self._selector_style())
        if self.sel_menu is not None:
            self.sel_menu.setStyleSheet(self._menu_style())

    def _fit_value_font(self):
        """Never elide a live measurement: if the number is wider than the
        readout, step the font down (2px at a time) until it fits. The
        column-count size set by _relayout() is the starting point."""
        width = self.width()
        if width < MIN_CELL_W:
            return          # not laid out yet - keep the designed size
        avail = width - 2 * CELL_PAD_X - 14
        size = self._val_size
        while size > 14:
            self.val.setStyleSheet(self._value_style(size))
            if self.val.sizeHint().width() <= avail:
                break
            size -= 2
        if size != self._val_size:
            self._val_size = size
        self.val.setStyleSheet(self._value_style(size))

    def _stat_style(self, color=None):
        color = color or _soft()
        return (f"color:{color.name()}; font-family:'Segoe UI'; font-size:10px;"
                " font-weight:600; background:transparent;")

    def apply_theme(self):
        self._apply_style()
        self._fit_value_font()

    # -- value binding -----------------------------------------------------
    def set_value(self, text, unit=None):
        self.val.setText(str(text))
        if unit is not None:
            self.unit.setText(unit)
        # Remember the last REAL reading so stopping Demo/Live mode can freeze
        # the display instead of blanking every box to '--'.
        if str(text) != "--":
            self._last_value = str(text)
            self._last_unit = self.unit.text()
        self._fit_value_font()

    def show_held(self):
        """Re-display the last real reading, marked as HELD. Used when the data
        link stops (Demo off and no device): the value stays visible instead of
        disappearing, but it is dimmed and labelled so it can never be mistaken
        for a live number."""
        if not getattr(self, "_last_value", None):
            self.set_value("--")
            self.clear_status()
            return False
        self.val.setText(self._last_value)
        self.unit.setText(self._last_unit)
        self._apply_style(held=True)
        self.stat.setText("HELD — LINK STOPPED")
        self.stat.setStyleSheet(self._stat_style(_warn()))
        self._fit_value_font()
        return True

    def clear_held(self):
        self._apply_style(held=False)

    def set_status(self, text, color=_soft()):
        self.clear_held()
        self.stat.setText(str(text))
        self.stat.setStyleSheet(self._stat_style(color))

    def clear_status(self):
        self.stat.setText("")
        self.stat.setStyleSheet(self._stat_style())


class SensorsTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.container = None
        self._cols = 0
        self._laid_out_width = -1
        self._build()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build(self):
        container = QWidget()
        self.container = container
        container.installEventFilter(self)
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(8)

        # Flat host for the header row + the monitoring table (the cells carry
        # their own borders, so the panel itself stays borderless).
        key_box = QGroupBox("")
        self.key_box = key_box
        key_lay = QVBoxLayout(key_box)
        key_lay.setContentsMargins(2, 2, 2, 2)
        key_lay.setSpacing(6)

        # ── Header: title + FT/M toggle + Sample Lag offset (Engineer) ─────
        # Two compact rows: the title/UNIT row and the offset/note row. A
        # single row would force the table wider than the Dashboard viewport
        # and produce a horizontal scrollbar (dashboardtab.txt §15).
        self.key_title = QLabel("DRILLING MONITOR")
        self.key_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.lag_off_lbl = QLabel("SAMPLE LAG OFFSET:")
        self.lag_off_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lag_off_spin = QDoubleSpinBox()
        self.lag_off_spin.setRange(0.0, 999999.9)
        self.lag_off_spin.setDecimals(1)
        self.lag_off_spin.setSingleStep(1.0)
        self.lag_off_spin.setKeyboardTracking(False)
        self.lag_off_spin.setFixedWidth(140)
        self.lag_off_spin.setValue(
            float(getattr(self.mw, "sample_lag_offset_ft", 0.0) or 0.0))
        self.lag_off_spin.editingFinished.connect(self._on_lag_offset_change)

        self.lag_off_hint = QLabel("(ft)")
        self.note = QLabel("FT/M toggle matches Block Position")
        # Ignored + elided: the hint must never widen the table.
        self._note_text = self.note.text()
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.key_unit_btn = QPushButton("UNIT: FT")
        self.key_unit_btn.setFixedHeight(24)
        self.key_unit_btn.setCursor(Qt.PointingHandCursor)
        self.key_unit_btn.clicked.connect(self._on_key_unit_toggle)

        head = QHBoxLayout()
        head.setSpacing(10)
        # Ignored policy keeps the minimum width at 0 (the header must never
        # widen the table), but it also suppresses the sizeHint, so the label
        # has to be given the slack explicitly via a stretch factor or it is
        # laid out at 0 width and never paints.
        head.addWidget(self.key_title, 1)
        head.addWidget(self.key_unit_btn)
        key_lay.addLayout(head)

        sub = QHBoxLayout()
        sub.setSpacing(8)
        sub.addWidget(self.lag_off_lbl, 1)
        sub.addWidget(self.lag_off_spin)
        sub.addWidget(self.lag_off_hint)
        sub.addSpacing(8)
        sub.addWidget(self.note, 2)
        key_lay.addLayout(sub)

        # ── THE MONITORING TABLE (dashboardtab.txt) ───────────────────────
        # Row order is the required field order: the five depth/position
        # cells, the hookload cell, SPM 1-4, then TOTAL SPM, which always
        # spans the full table width. The column count is recomputed on
        # resize, so 5 -> 4 -> 3 -> 2 -> 1 columns fit the window.
        self.box_md = _DashCell("Measured Depth", "depth", True, "FT")
        self.box_tvd = _DashCell("TVD", "depth", True, "FT")
        self.box_bit = _DashCell("Bit Depth", "depth", True, "FT")
        self.box_block = _DashCell("Block Position", "position", True, "FT")
        self.box_lag = _DashCell("Sample Lag Depth", "position", True, "FT")
        self.box_hook = _DashCell("Hookload Value", "hookload", True, "KLB")
        # SPM 1-4 are CONFIGURABLE DISPLAY SLOTS (spmselection.txt): each box
        # owns an independent selection of which of the four EXISTING SPM
        # parameters it shows, chosen with the small triangle in its top-right
        # corner. Defaults: box 1 -> SPM 1 ... box 4 -> SPM 4. The selection is
        # restored from the existing settings store and persisted on change;
        # duplicates are allowed. The SPM calculation is NOT touched - the box
        # just reads a different index of mw.spm_values.
        self._spm_slots = self._load_spm_slots()
        self.box_spm = [
            _DashCell(SPM_SLOT_LABELS[i], "pump", False, "SPM",
                      selector_items=SPM_SLOT_LABELS,
                      selected=self._spm_slots[i],
                      on_selected=self._on_spm_slot_changed)
            for i in range(len(SPM_SLOT_LABELS))]
        self.box_total = _DashCell("Total SPM", "pump", True, "SPM")
        # RPM box: same selectable-slot pattern, showing the EXISTING rpm_values.
        self._rpm_slot = self._load_rpm_slot()
        self.box_rpm = _DashCell(
            "RPM", "pump", False, "RPM",
            selector_items=RPM_SLOT_LABELS, selected=self._rpm_slot,
            on_selected=self._on_rpm_slot_changed)

        self._body_cells = [self.box_md, self.box_tvd, self.box_bit,
                            self.box_block, self.box_lag, self.box_hook]
        self._body_cells.extend(self.box_spm)
        self._body_cells.append(self.box_rpm)
        self._total_cell = self.box_total

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(CELL_GAP)
        self._grid.setVerticalSpacing(CELL_GAP)
        key_lay.addLayout(self._grid)
        self._relayout(force=True)

        root.addWidget(key_box)
        root.addStretch()

        # ── Scrolling container (fits the window; scrolls only if needed) ──
        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # AsNeeded (not AlwaysOff): the responsive column count already keeps
        # the table inside the window, so the bar only appears if a value is
        # genuinely too wide to fit — a cell is never silently clipped.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("background: transparent; border: none;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

        self._apply_panel_style()

        # RBAC: the lag offset is a configuration — Operator (view only) may
        # not change it from the Dashboard; Engineer/Supervisor may.
        self.mw.roles.subscribe(lambda _role: self._apply_role_state())
        self._apply_role_state()

    def _apply_panel_style(self):
        t = theme.current()
        # The table cells carry the borders, so the host panel is borderless.
        # (No `letter-spacing` here: Qt's QSS parser does not support it and
        # warns on it.)
        self.key_box.setStyleSheet(
            f"QGroupBox {{ background:transparent; border:none;"
            f" margin-top:0px; font-family:'Georgia'; font-size:13px;"
            f" font-weight:bold; color:{t.color_name('TEXT_DARK')};"
            " padding:2px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left:8px;"
            " padding:0 4px; }")
        self.key_title.setStyleSheet(
            f"color:{t.color_name('TEXT_DARK')}; font-family:'Georgia';"
            " font-size:14px; font-weight:900;"
            " background:transparent;")
        self.lag_off_lbl.setStyleSheet(
            f"color:{t.color_name('TEXT_MID')}; font-family:'Segoe UI';"
            " font-size:11px; background:transparent;")
        self.lag_off_hint.setStyleSheet(
            f"color:{t.color_name('TEXT_LITE')}; font-family:'Segoe UI';"
            " font-size:11px; background:transparent;")
        self.note.setStyleSheet(
            f"color:{t.color_name('TEXT_LITE')}; font-family:'Segoe UI';"
            " font-size:10px; background:transparent;")

    # ------------------------------------------------------------------
    # Responsive table layout
    # ------------------------------------------------------------------
    def _columns_for(self, width):
        """How many equal cells fit `width` without squeezing or scrolling."""
        usable = max(0, int(width))
        for cols in range(MAX_COLUMNS, 1, -1):
            if usable >= cols * MIN_CELL_W + (cols - 1) * CELL_GAP:
                return cols
        return 1

    def _relayout(self, force=False):
        """Place the cells row-major in `cols` columns, TOTAL SPM spanning the
        full table width. Called on every resize; only re-places the widgets
        when the column count actually changes."""
        width = self.container.width() if self.container else 0
        if not force and width == self._laid_out_width:
            return
        self._laid_out_width = width
        cols = self._columns_for(width)
        if not force and cols == self._cols:
            return
        self._cols = cols

        g = self._grid
        while g.count():
            g.takeAt(g.count() - 1)
        for c in range(MAX_COLUMNS):
            g.setColumnStretch(c, 0)
            g.setColumnMinimumWidth(c, 0)

        for i, cell in enumerate(self._body_cells):
            row, col = divmod(i, cols)
            g.addWidget(cell, row, col)
        rows = (len(self._body_cells) + cols - 1) // cols
        g.addWidget(self._total_cell, rows, 0, 1, cols)
        for c in range(cols):
            g.setColumnStretch(c, 1)
        g.invalidate()

        # Match the value font to the column count so digits are never clipped.
        primary_px, secondary_px = VALUE_SIZES.get(cols, VALUE_SIZES[1])
        for cell in self._body_cells:
            cell.set_value_size(primary_px if cell.primary else secondary_px)
        self._total_cell.set_value_size(primary_px)

    def eventFilter(self, obj, event):
        # The container grows with the scroll viewport (widgetResizable), so
        # the tab's own resizeEvent is not enough to keep the column count in
        # step with the real available width.
        if obj is self.container and event.type() == QEvent.Resize:
            self._relayout()
            self._elide_note()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
        self._elide_note()

    def _elide_note(self):
        """Shorten the header hint instead of letting it stretch the table."""
        if not hasattr(self, "note"):
            return
        fm = QFontMetrics(self.note.font())
        self.note.setText(fm.elidedText(
            self._note_text, Qt.ElideRight, max(0, self.note.width())))

    def _all_cards(self):
        cards = [self.box_md, self.box_tvd, self.box_bit, self.box_block,
                 self.box_lag, self.box_hook, self.box_total]
        cards.extend(self.box_spm)
        cards.append(self.box_rpm)
        return cards

    # ------------------------------------------------------------------
    # Configurable SPM slots (spmselection.txt)
    # ------------------------------------------------------------------
    def _load_spm_slots(self):
        """The persisted SPM selection per box; defaults box i -> SPM i+1.

        Read through the existing settings store (no new storage), and any
        missing / malformed / out-of-range entry falls back to the default so a
        corrupt file can never hide a parameter.
        """
        raw = (getattr(self.mw, "_settings", None) or {}).get("dashboard_spm_slots")
        n = len(SPM_SLOT_LABELS)
        out = []
        for i in range(n):
            val = SPM_SLOT_DEFAULT[i]
            if isinstance(raw, list) and i < len(raw):
                try:
                    v = int(raw[i])
                except (TypeError, ValueError):
                    v = val
                if 0 <= v < n:
                    val = v
            out.append(val)
        return out

    def _on_spm_slot_changed(self, _cell):
        """One box picked a different SPM parameter. Record every box's choice,
        persist it through the existing settings store, and re-read the existing
        SPM values so the label and the live value change together."""
        self._spm_slots = [c.selected() for c in self.box_spm]
        try:
            from ..services import settings as settings_service
            settings_service.save({"dashboard_spm_slots": list(self._spm_slots)})
        except OSError:
            pass
        self.refresh()

    def _load_rpm_slot(self):
        """The persisted RPM channel for the Dashboard RPM box; out-of-range or
        malformed entries fall back to RPM 1."""
        raw = (getattr(self.mw, "_settings", None) or {}).get("dashboard_rpm_slot")
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return RPM_SLOT_DEFAULT
        return val if 0 <= val < len(RPM_SLOT_LABELS) else RPM_SLOT_DEFAULT

    def _on_rpm_slot_changed(self, _cell):
        """The RPM box picked a different RPM channel: persist the choice through
        the existing settings store and re-read the existing rpm_values."""
        self._rpm_slot = _cell.selected()
        try:
            from ..services import settings as settings_service
            settings_service.save({"dashboard_rpm_slot": self._rpm_slot})
        except OSError:
            pass
        self.refresh()

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------
    def _apply_role_state(self):
        can = self.mw.roles.can_command()
        self.lag_off_spin.setEnabled(can)

    def _on_lag_offset_change(self):
        mw = self.mw
        val = self.lag_off_spin.value()
        if mw.set_sample_lag_offset(val):
            self.refresh()
        else:
            self.lag_off_spin.setValue(
                float(getattr(mw, "sample_lag_offset_ft", 0.0) or 0.0))

    # ------------------------------------------------------------------
    # FT/M toggle (single unit system — Block Position toggle)
    # ------------------------------------------------------------------
    def _on_key_unit_toggle(self):
        mw = self.mw
        if hasattr(mw.block_tab, "_on_unit_toggle"):
            mw.block_tab._on_unit_toggle()

    def _position_unit(self):
        tab = self.mw.block_tab
        unit = getattr(tab, "_position_unit", "FT")
        return unit if unit in ("FT", "M") else "FT"

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def _cal_status(self):
        status = (self.mw.cal_status or "NO_CALIBRATION").upper()
        if status == "VALID":
            return "● NORMAL", _normal()
        if status == "OUT_OF_RANGE":
            return "● OUT OF RANGE", _bad()
        return "● NO CALIBRATION", _warn()

    def _link_override(self, own_text, own_color):
        """Return the live-link status when the data link cannot vouch for the
        value (stale); otherwise the box's own status.

        A dropped link is NOT spelled out on the boxes: the cell simply shows
        '--' with an empty status line, so the Dashboard never displays a
        "DISCONNECTED" message (and never a believable-looking number
        either)."""
        mw = self.mw
        if mw._demo_mode:
            return own_text, own_color
        if mw.serial and mw.serial.is_open and not mw._link_down:
            if mw._data_stale:
                return "● STALE DATA", _warn()
            return own_text, own_color
        return "", _soft()

    # ------------------------------------------------------------------
    # Refresh (pure display binding — no calculation engine lives here)
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt(value, decimals=1):
        try:
            return f"{float(value):,.{decimals}f}"
        except (TypeError, ValueError):
            return "--"

    def refresh(self):
        mw = self.mw
        unit = self._position_unit()
        self.key_unit_btn.setText(f"UNIT: {unit}")
        conv = FT_TO_M if unit == "M" else 1.0

        self.lag_off_spin.blockSignals(True)
        self.lag_off_spin.setValue(
            float(getattr(mw, "sample_lag_offset_ft", 0.0) or 0.0))
        self.lag_off_spin.blockSignals(False)
        off = float(getattr(mw, "sample_lag_offset_ft", 0.0) or 0.0)

        link_ok = bool(mw._demo_mode or (mw.serial and mw.serial.is_open
                                         and not mw._link_down))

        # Link stopped (Demo off AND no device): FREEZE the display. Every box
        # keeps the value it last showed, greyed out and labelled HELD, so
        # nothing on the Dashboard silently disappears when the mode stops.
        if not link_ok:
            for card in self._all_cards():
                card.show_held()
            return

        # ── ONE read of the existing Bit Position/Bit Depth engine per tick.
        #    Measured Depth / TVD / Sample Lag / Bit Depth all reuse this value
        #    (the dashboard is a display layer, never a second calculation).
        md = mw.measured_depth_ft
        state = mw.bit_position_state
        if state == "TRACK":
            state_text, state_col = "● TRACKING - PIPE LOAD", _normal()
        elif state == "HOLD":
            state_text, state_col = "● HOLD - FROZEN DEPTH", _warn()
        else:
            state_text, state_col = "● STANDBY", _soft()

        # MEASURED DEPTH / BIT DEPTH / TVD / SAMPLE LAG DEPTH --------------
        if md is None:
            self.box_md.set_value("--", unit)
            self.box_md.set_status("● PIPE IN HOLE NOT SET", _warn())
            tvd = None
        else:
            md_disp = self._fmt(md * conv)
            self.box_md.set_value(md_disp, unit)
            tvd = md
            self.box_md.set_status(*self._link_override(state_text, state_col))
        # TVD — True Vertical Depth (no survey: vertical well, TVD = MD).
        if tvd is None:
            self.box_tvd.set_value("--", unit)
            self.box_tvd.set_status("● PIPE IN HOLE NOT SET", _warn())
        else:
            self.box_tvd.set_value(self._fmt(tvd * conv), unit)
            self.box_tvd.set_status(*self._link_override("● VERTICAL - NO SURVEY", _soft()))

        # BIT DEPTH — exactly the existing Bit engine value this tick.
        if md is None:
            self.box_bit.set_value("--", unit)
            self.box_bit.set_status("● PIPE IN HOLE NOT SET", _warn())
        else:
            self.box_bit.set_value(self._fmt(md * conv), unit)
            if state == "NONE":
                self.box_bit.set_status(*self._link_override(
                    "● PIPE IN HOLE NOT SET", _warn()))
            else:
                self.box_bit.set_status(*self._link_override(state_text, state_col))

        # SAMPLE LAG DEPTH — Measured Depth minus the persisted lag offset.
        lag = None if md is None else max(0.0, md - off)
        self.box_lag.set_value("--" if lag is None else self._fmt(lag * conv), unit)
        if lag is None:
            self.box_lag.set_status("● PIPE IN HOLE NOT SET", _warn())
        else:
            lag_status = (f"● LAG {off * conv:,.1f} {unit}"
                          if off > 0.0 else "● FOLLOWS BIT DEPTH")
            self.box_lag.set_status(*self._link_override(lag_status, _soft()))

        # BLOCK POSITION -----------------------------------------------------
        block = float(mw.block_position_ft or 0.0)
        self.box_block.set_value(self._fmt(block * conv), unit)
        self.box_block.set_status(*self._link_override(*self._cal_status()))

        # HOOKLOAD VALUE -----------------------------------------------------
        # The existing channel-0 analog -> hookload chain (single source).
        sstat = ""
        if mw.analog_statuses:
            sstat = str(mw.analog_statuses[0] or "").upper()
        hk = mw.hookload_klb
        if sstat in ("FAULT", "INVALID", "DISCONNECTED", "NO DATA"):
            self.box_hook.set_value("--", "KLB")
            self.box_hook.set_status("● SENSOR FAULT", _bad())
        elif sstat == "UNUSED":
            self.box_hook.set_value("--", "KLB")
            self.box_hook.set_status("● SENSOR DISABLED", _warn())
        else:
            self.box_hook.set_value(self._fmt(hk), "KLB")
            if not mw._demo_mode and mw._data_stale:
                self.box_hook.set_status("● STALE DATA", _warn())
            elif mw._demo_mode:
                self.box_hook.set_status("● SIMULATED", _normal())
            else:
                self.box_hook.set_status("● LIVE", _normal())

        # SPM 1..4 + TOTAL SPM ----------------------------------------------
        # Each SPM box reads the EXISTING value of the parameter it currently
        # selects (mw.spm_values), so the selector only changes what is
        # displayed - the pump calculation and acquisition are untouched, and
        # the value keeps updating live on every tick (demo included).
        for card in self.box_spm:
            card.set_value(self._fmt(mw.spm_values[card.selected()]), "SPM")
            if not mw._demo_mode and mw._data_stale:
                card.set_status("● STALE DATA", _warn())
            elif mw._demo_mode:
                card.set_status("● SIMULATED", _normal())
            else:
                card.set_status("● LIVE", _normal())
        total = mw.total_spm  # sum of the four existing SPM values
        self.box_total.set_value(self._fmt(total), "SPM")
        if not mw._demo_mode and mw._data_stale:
            self.box_total.set_status("● STALE DATA", _warn())
        elif mw._demo_mode:
            self.box_total.set_status("● SIMULATED", _normal())
        else:
            self.box_total.set_status("● SUM 1-4", _normal())

        # RPM box (digitalsensor1.txt §17) — reads the EXISTING, independently
        # calibrated rpm_values of the channel it currently selects.
        self.box_rpm.set_value(self._fmt(mw.rpm_values[self.box_rpm.selected()]),
                               "RPM")
        if not mw._demo_mode and mw._data_stale:
            self.box_rpm.set_status("● STALE DATA", _warn())
        elif mw._demo_mode:
            self.box_rpm.set_status("● SIMULATED", _normal())
        else:
            self.box_rpm.set_status("● LIVE", _normal())

    def apply_theme(self, name):
        """Re-skin the Dashboard when the app theme is toggled (Light/Dark/Red).
        Matches the previous dashboard's light-theme look in every theme."""
        self._apply_panel_style()
        for card in self._all_cards():
            card.apply_theme()
        self.refresh()