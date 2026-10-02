"""SPM / RPM Monitor tab (strokes / revolutions).

Redesigned to match the Analog Monitor dashboard: a single-screen,
non-scrolling industrial table (Device | Channel | Sensor Name | Value | Unit
| Counter | Offset | Reset) with the same header status chip, the same chip /
value colouring vocabulary and the same responsive column reflow.

Design rules kept from the rest of the application:
  * Every number is read FRESH from the existing services on refresh() -
    nothing is recalculated with a second engine and no new serial/sensor loop
    is created (the main dashboard tick drives this tab via mw._tick).
  * SPM/RPM stay INDEPENDENT channels (SPM_CONFIG / RPM_CONFIG are the single
    source of truth); RESET sets that channel's offset in the existing
    mw.reset_spm / mw.reset_rpm helpers, it never writes to the firmware.
  * A channel that never had a live reading shows "--", never a misleading 0.
    Once a channel HAS reported a value, a stopped Demo/Live link keeps that
    last reading on screen and labels it HELD - LINK STOPPED instead of
    blanking it.
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QPushButton,
    QFrame, QSizePolicy,
)
from PyQt5.QtCore import Qt

from ..config import (
    SPM_CONFIG, RPM_CONFIG, BG_CARD, BG_INNER, BORDER, ACCENT,
    TEXT_DARK, TEXT_MID, TEXT_LITE, CAL_MAX_CLR, CAL_BAND_CLR,
)

# Exactly the Analog Monitor colour vocabulary, so both dashboards read alike.
_OK = CAL_MAX_CLR          # green  — normal / simulation
_WARN = CAL_BAND_CLR       # amber  — stale / held / no fresh data
_BAD = ACCENT              # red    — comm error / no data
_SOFT = TEXT_LITE          # faint  — idle / never measured

_HEADERS = ["Device", "Channel", "Sensor Name", "Value", "Unit",
            "Counter", "Offset", "Reset"]
# Same proportional reflow approach as the Analog Monitor table, so both
# dashboards line up column-for-column on any window width.
_BASE_W = {0: 56, 1: 66, 2: 190, 3: 118, 4: 66, 5: 104, 6: 104, 7: 96}
_MIN_W = {0: 48, 1: 56, 2: 124, 3: 96, 4: 54, 5: 84, 6: 84, 7: 84}
_DEVICE_ID = "1"
_HELD_TEXT = "HELD — LINK STOPPED"


def _channels():
    """One flat, ordered list of every digital sensor channel: SPM 1-4 then
    RPM 1-2, with the firmware counter number it belongs to."""
    rows = []
    for i, cfg in enumerate(SPM_CONFIG):
        rows.append({"kind": "SPM", "idx": i, "name": cfg["name"],
                     "channel": i + 1, "unit": "SPM", "type": "STROKES"})
    base = len(SPM_CONFIG)
    for i, cfg in enumerate(RPM_CONFIG):
        rows.append({"kind": "RPM", "idx": i, "name": cfg["name"],
                     "channel": base + i + 1, "unit": "RPM",
                     "type": "REVOLUTIONS"})
    return rows


class StrokeRpmTab(QWidget):
    def __init__(self, main_window, roles=None, audit=None, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.roles = roles if roles is not None else getattr(main_window, "roles", None)
        self.audit = audit if audit is not None else getattr(main_window, "audit", None)
        self.channels = _channels()
        self._num = len(self.channels)
        self._last_row_h = 0
        # True once a channel has reported a real reading; drives the HELD
        # rule (keep the last value) instead of blanking the row.
        self._has_live = [False] * self._num

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(6)

        # -- compact page header (same shape as the Analog Monitor) -------
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("STROKES / RPM MONITOR")
        title.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Georgia'; font-size:15px; "
            "font-weight:900; letter-spacing:2px; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        self.total_chip = QLabel("")
        self.total_chip.setAlignment(Qt.AlignCenter)
        self.total_chip.setStyleSheet(self._pill_style())
        head.addWidget(self.total_chip)
        self.status_chip = QLabel("")
        self.status_chip.setAlignment(Qt.AlignCenter)
        self.status_chip.setStyleSheet(self._chip_style(_SOFT, "NORMAL"))
        head.addWidget(self.status_chip)
        root.addLayout(head)

        # -- the single full-view table -----------------------------------
        self.table = QTableWidget(self._num, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setMinimumSize(0, 0)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        hh.setHighlightSections(False)
        hh.setSectionsMovable(False)
        hh.setSectionsClickable(False)
        for col in range(len(_HEADERS)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)

        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(34)

        self._build_rows()
        self._apply_table_style()
        self._reflow(self.table.width())
        root.addWidget(self.table, stretch=1)
        self.refresh()

    # ------------------------------------------------------------------
    # Table construction (built once; refresh() only rewrites cells)
    # ------------------------------------------------------------------
    def _build_rows(self):
        self._reset_btns = []
        for r, ch in enumerate(self.channels):
            def _mk_item(text, align, color=None, bold=False):
                it = QTableWidgetItem(str(text))
                it.setTextAlignment(align)
                if color is not None:
                    it.setForeground(color)
                if bold:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                return it

            self.table.setItem(r, 0, _mk_item(_DEVICE_ID, Qt.AlignCenter, TEXT_MID))
            self.table.setItem(r, 1, _mk_item(ch["channel"], Qt.AlignCenter, TEXT_MID))
            name = _mk_item(ch["name"], Qt.AlignLeft | Qt.AlignVCenter, TEXT_DARK)
            name.setToolTip(f"{ch['name']}  ·  {ch['type']}")
            self.table.setItem(r, 2, name)
            self.table.setItem(r, 3, _mk_item("--", Qt.AlignRight | Qt.AlignVCenter,
                                             TEXT_DARK, bold=True))
            self.table.setItem(r, 4, _mk_item(ch["unit"], Qt.AlignCenter, TEXT_MID))
            self.table.setItem(r, 5, _mk_item("--", Qt.AlignRight | Qt.AlignVCenter,
                                             TEXT_MID))
            self.table.setItem(r, 6, _mk_item("--", Qt.AlignRight | Qt.AlignVCenter,
                                             TEXT_MID))

            btn = QPushButton("Reset")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._btn_style())
            btn.setToolTip(
                f"Set {ch['name']} offset to the current counter "
                f"(returns it to zero). Does not send anything to the device.")
            btn.clicked.connect(lambda _c, r=r: self._on_reset(r))
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.setContentsMargins(4, 0, 4, 0)
            lay.setAlignment(Qt.AlignCenter)
            lay.addWidget(btn)
            self.table.setCellWidget(r, 7, wrap)
            self._reset_btns.append(btn)

    # ------------------------------------------------------------------
    # Styling (mirrors the Analog Monitor table)
    # ------------------------------------------------------------------
    def _apply_table_style(self):
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {BG_CARD.name()};
                border: 1px solid {BORDER.name()};
                border-radius: 8px;
                gridline-color: {BORDER.name()};
                font-family: 'Segoe UI'; font-size: 12px;
            }}
            QTableWidget::item {{ padding: 2px 6px; }}
            QTableWidget::item:alternate {{
                background: #F7F0E8;
            }}
            QHeaderView::section {{
                background: {ACCENT.name()};
                color: #FFFFFF;
                border: none;
                border-right: 1px solid #A03020;
                padding: 7px 6px;
                font-family: 'Segoe UI'; font-size: 11px;
                font-weight: bold; letter-spacing: 0.6px;
            }}
        """)

    def _btn_style(self):
        return (f"QPushButton {{ background:{CAL_MAX_CLR.name()}; color:white; "
                "border:none; border-radius:5px; font-family:'Segoe UI'; "
                "font-size:11px; font-weight:bold; padding:5px 12px; }} "
                "QPushButton:hover { background:#155e30; } "
                f"QPushButton:disabled {{ background:#D9CFC3; color:{TEXT_LITE.name()}; }}")

    def _chip_style(self, color, text):
        return (f"color:#FFFFFF; background:{color.name()}; font-family:'Segoe UI'; "
                "font-size:10px; font-weight:bold; border-radius:8px; "
                "padding:4px 14px;")

    def _pill_style(self):
        return (f"color:{TEXT_DARK.name()}; background:{BG_INNER.name()}; "
                f"border:1px solid {BORDER.name()}; font-family:'Segoe UI'; "
                "font-size:11px; font-weight:bold; border-radius:8px; "
                "padding:4px 14px;")

    def apply_theme(self, name):
        # Same fixed industrial palette as the Analog Monitor table; re-apply so
        # a Light/Dark toggle cannot leave stale styles behind.
        self._apply_table_style()

    # ------------------------------------------------------------------
    # Responsive layout: no scrollbars — columns and rows reflow to fill the
    # page on every resize (identical to the Analog Monitor).
    # ------------------------------------------------------------------
    def _reflow(self, total_w):
        if not total_w or total_w < 10:
            return
        scale = float(total_w) / float(sum(_BASE_W.values()))
        for col in range(len(_HEADERS)):
            width = max(_MIN_W[col], int(round(_BASE_W[col] * scale)))
            self.table.setColumnWidth(col, width)

    def _apply_row_heights(self, avail=None):
        if avail is None:
            avail = max(0, self.table.height()
                        - self.table.horizontalHeader().height() - 4)
        per = max(26, min(64, avail // max(1, self._num)))
        if per != self._last_row_h:
            self._last_row_h = per
            self.table.verticalHeader().setDefaultSectionSize(per)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._reflow(self.table.width())
            self._apply_row_heights()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Status (same vocabulary / colours as the Analog Monitor header chip)
    # ------------------------------------------------------------------
    def _can_edit(self):
        if self.roles is None:
            return False
        try:
            return bool(self.roles.can_command())
        except Exception:
            return False

    def _link_text(self):
        mw = self.mw
        if mw._demo_mode:
            return "SIMULATION", _OK
        ser_ok = bool(mw.serial and mw.serial.is_open)
        if not ser_ok:
            return "DISCONNECTED", _BAD
        if getattr(mw, "_link_down", False):
            return "COMMUNICATION ERROR", _BAD
        if getattr(mw, "_data_stale", False):
            return "STALE DATA", _WARN
        return "NORMAL", _OK

    def _row_state(self, r):
        """(label, color, show_numbers) for channel row `r`.

        There is no per-SPM/RPM firmware status field in the protocol, so only
        the link-level verdict is used - no duplicate status logic is invented.
        """
        mw = self.mw
        if mw._demo_mode:
            return "NORMAL", _OK, True
        ser_ok = bool(mw.serial and mw.serial.is_open)
        if not ser_ok:
            if self._has_live[r]:
                return _HELD_TEXT, _WARN, True
            return "NO DATA", _BAD, False
        if getattr(mw, "_link_down", False):
            if self._has_live[r]:
                return _HELD_TEXT, _WARN, True
            return "COMMUNICATION ERROR", _BAD, False
        if getattr(mw, "_data_stale", False):
            if self._has_live[r]:
                return _HELD_TEXT, _WARN, True
            return "STALE DATA", _WARN, False
        return "NORMAL", _OK, True

    def _reading(self, ch):
        """The channel's live value / raw counter / offset straight from the
        existing services (no second calculation path)."""
        mw = self.mw
        if ch["kind"] == "SPM":
            return (mw.spm_values[ch["idx"]],
                    mw.spm_raw_counter[ch["idx"]],
                    mw.spm_offset[ch["idx"]])
        return (mw.rpm_values[ch["idx"]],
                mw.rpm_raw_counter[ch["idx"]],
                mw.rpm_offset[ch["idx"]])

    # ------------------------------------------------------------------
    # Per-tick refresh (only rewrites live cells, never rebuilds the table)
    # ------------------------------------------------------------------
    def refresh(self):
        mw = self.mw
        editable = self._can_edit()
        label, color = self._link_text()
        self.status_chip.setText("● " + label)
        self.status_chip.setStyleSheet(self._chip_style(color, label))

        for r, ch in enumerate(self.channels):
            state, scolor, show = self._row_state(r)
            value, counter, offset = self._reading(ch)

            val_item = self.table.item(r, 3)
            cnt_item = self.table.item(r, 5)
            off_item = self.table.item(r, 6)

            if show:
                self._has_live[r] = True
                txt = f"{float(value):.1f}"
                if state != "NORMAL":
                    txt = f"{state} {txt}"
                val_item.setText(txt)
                val_item.setForeground(TEXT_DARK if state == "NORMAL" else scolor)
                cnt_item.setText(f"{float(counter):,.0f}")
                cnt_item.setForeground(TEXT_DARK if state == "NORMAL" else scolor)
                off_item.setText(f"{float(offset):,.0f}")
                off_item.setForeground(TEXT_MID if state == "NORMAL" else scolor)
            else:
                # Never measured: "--", never a misleading 0.
                val_item.setText("--")
                val_item.setForeground(scolor)
                cnt_item.setText("--")
                cnt_item.setForeground(_SOFT)
                off_item.setText("--")
                off_item.setForeground(_SOFT)

            # Reset works as soon as there is a counter to zero out.
            self._reset_btns[r].setEnabled(editable and self._has_live[r])

        # Total SPM: the existing single source (mw.total_spm), never re-summed
        # with different rounding here.
        try:
            self.total_chip.setText(f"TOTAL SPM  {float(mw.total_spm):.1f}")
        except Exception:
            self.total_chip.setText("TOTAL SPM  --")

    # ------------------------------------------------------------------
    # Reset action (existing helpers keep ownership of the offsets)
    # ------------------------------------------------------------------
    def _on_reset(self, r):
        ch = self.channels[r]
        try:
            if ch["kind"] == "SPM":
                self.mw.reset_spm(ch["idx"])
            else:
                self.mw.reset_rpm(ch["idx"])
        except Exception:
            return
        if self.audit is not None:
            try:
                self.audit.record("STROKE_RESET",
                                 f"{ch['name']} offset set to counter "
                                 f"({ch['channel']})",
                                 self.roles.role() if self.roles else "-")
            except Exception:
                pass
        # Existing application behaviour after an Engineer operation.
        try:
            self.mw._return_role_to_operator()
        except Exception:
            pass
        self.refresh()