import serial.tools.list_ports

from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QLabel, QComboBox,
                             QPushButton, QMessageBox)
from PyQt5.QtCore import Qt
from ..config import BG_INNER, BG_CARD, BORDER, ACCENT, TEXT_MID, TEXT_LITE
from ..services.security import (ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ENGINEER,
                                 _ROLE_ORDER)
from ..services.settings import load as load_settings

BAUD_OPTIONS = ["9600", "19200", "38400", "57600", "115200"]


class StatusBar(QWidget):
    def __init__(self, roles, audit, main_window, parent=None):
        super().__init__(parent)
        self.roles = roles
        self.audit = audit
        self.mw = main_window

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.lbl = QLabel("● DISCONNECTED")
        self.lbl.setStyleSheet("color:#C03920; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        layout.addWidget(self.lbl)

        # Link / data-integrity indicator (supports stale & checksum health)
        self.link_lbl = QLabel("LINK: --")
        self.link_lbl.setStyleSheet("color:#8A8A8A; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        layout.addWidget(self.link_lbl)

        layout.addStretch()

        S_CB = (f"QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:6px; font-family:'Segoe UI'; "
                f"font-size:11px; padding:4px; }} "
                f"QComboBox:hover {{ border:1px solid #B5A999; }} "
                f"QComboBox::drop-down {{ border:none; width:20px; }} "
                f"QComboBox QAbstractItemView {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:4px; selection-background-color:{ACCENT.name()}; selection-color:white; }}")
        B = (f"background:{BG_CARD.name()}; border:1px solid {BORDER.name()}; border-radius:6px; "
             f"font-family:'Segoe UI'; font-size:11px; padding:6px 10px;")

        self.port_cb = QComboBox()
        self.port_cb.setFixedWidth(140)
        self.port_cb.addItems(self._detect_ports())
        self.port_cb.setStyleSheet(S_CB)

        self.baud_cb = QComboBox()
        self.baud_cb.setFixedWidth(88)
        self.baud_cb.addItems(BAUD_OPTIONS)
        self.baud_cb.setStyleSheet(S_CB)

        # Restore last-used port/baud from persistent settings (if still present).
        _saved = load_settings()
        _saved_port = str(_saved.get("port", ""))
        if _saved_port:
            if self.port_cb.findText(_saved_port) >= 0:
                self.port_cb.setCurrentText(_saved_port)
            else:
                self.port_cb.insertItem(0, _saved_port)
                self.port_cb.setCurrentText(_saved_port)
        _saved_baud = str(_saved.get("baud", "9600"))
        if _saved_baud in BAUD_OPTIONS:
            self.baud_cb.setCurrentText(_saved_baud)

        # Selecting "⟳ Refresh ports…" triggers a rescan of serial devices.
        self.port_cb.activated.connect(self._on_port_activated)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setFixedWidth(84)
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT.name()}; color:white; border:none; "
            f"border-radius:6px; font-family:'Segoe UI'; font-size:11px; font-weight:bold; "
            f"padding:6px 10px; }} QPushButton:hover {{ background:#a03020; }} "
            f"QPushButton:pressed {{ background:#8e2a1c; }}")

        self.demo_btn = QPushButton("Demo Mode")
        self.demo_btn.setFixedWidth(96)
        self.demo_btn.setCursor(Qt.PointingHandCursor)
        self.demo_btn.setStyleSheet(f"QPushButton {{ {B} color:#7A5C10; font-weight:bold; }} QPushButton:hover {{ background:{BORDER.name()}; }}")

        # Role toggle (simple switch — see security notes; not a substitute for auth)
        self.role_btn = QPushButton(f"ROLE: {ROLE_OPERATOR.upper()}")
        self.role_btn.setFixedWidth(160)
        self.role_btn.setStyleSheet(self._role_style())
        self.role_btn.clicked.connect(self._toggle_role)
        self.role_btn.setText(f"ROLE: {roles.role().upper()}")

        export_csv = QPushButton("Export CSV")
        export_csv.setFixedWidth(84)
        export_csv.setStyleSheet(f"QPushButton {{ {B} color:#1A5C94; }} QPushButton:hover {{ background:{BORDER.name()}; }}")
        export_csv.clicked.connect(lambda: self.mw.export_audit("csv"))
        self._export_csv = export_csv

        export_pdf = QPushButton("PDF")
        export_pdf.setFixedWidth(48)
        export_pdf.setStyleSheet(f"QPushButton {{ {B} color:#1A5C94; }} QPushButton:hover {{ background:{BORDER.name()}; }}")
        export_pdf.clicked.connect(lambda: self.mw.export_audit("pdf"))
        self._export_pdf = export_pdf

        def lbl2(t):
            l = QLabel(t)
            l.setStyleSheet(f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px;")
            return l

        for w in [lbl2("Port:"), self.port_cb, lbl2("Baud:"), self.baud_cb,
                  self.connect_btn, self.demo_btn, self.role_btn,
                  lbl2("Audit:"), export_csv, export_pdf]:
            layout.addWidget(w)

    def apply_theme(self, name):
        """Re-skin the neutral chrome (combos, action buttons) to the active
        theme. Status hues (connected/link/fault) are fixed solid colors with
        explicit text, so they read correctly on either theme."""
        from PyQt5.QtGui import QColor as _Q
        from ..config import BG_CARD, BG_INNER, BORDER, ACCENT, TEXT_MID, TEXT_DARK

        S_CB = (f"QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:6px; font-family:'Segoe UI'; "
                f"font-size:11px; padding:4px; }} "
                f"QComboBox:hover {{ border:1px solid #B5A999; }} "
                f"QComboBox::drop-down {{ border:none; width:20px; }} "
                f"QComboBox QAbstractItemView {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:4px; selection-background-color:{ACCENT.name()}; selection-color:white; }}")
        B = (f"background:{BG_CARD.name()}; border:1px solid {BORDER.name()}; border-radius:6px; "
             f"font-family:'Segoe UI'; font-size:11px; padding:6px 10px;")
        self.port_cb.setStyleSheet(S_CB)
        self.baud_cb.setStyleSheet(S_CB)
        self.demo_btn.setStyleSheet(
            f"QPushButton {{ {B} color:#7A5C10; font-weight:bold; }} "
            f"QPushButton:hover {{ background:{BORDER.name()}; }}")
        self.role_btn.setStyleSheet(self._role_style())
        info = "#1A5C94"
        for b in (self._export_csv, self._export_pdf):
            b.setStyleSheet(
                f"QPushButton {{ {B} color:{info}; }} "
                f"QPushButton:hover {{ background:{BORDER.name()}; }}")
    @staticmethod
    def _detect_ports():
        """Return available serial ports (device + description) plus a 'Refresh'
        sentinel. Falls back to a reasonable default list if the OS API fails.
        """
        ports = []
        try:
            for p in serial.tools.list_ports.comports():
                label = p.device
                if p.description and p.description != "n/a":
                    label = f"{p.device}  [{p.description}]"
                ports.append(label)
        except Exception:
            ports = []
        if not ports:
            # Fallback: provide common aliases so the UI is usable even when no
            # device is present, without a hardcoded single default.
            ports = [
                "COM1", "COM2", "COM3", "COM4",
                "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0",
            ]
        ports.append("⟳ Refresh ports…")
        return ports

    def refresh_ports(self):
        current = self.port_cb.currentText()
        raw = current.split("  [")[0] if "  [" in current else current
        self.port_cb.blockSignals(True)
        self.port_cb.clear()
        self.port_cb.addItems(self._detect_ports())
        idx = self.port_cb.findText(current)
        if idx < 0:
            for i in range(self.port_cb.count()):
                candidate = self.port_cb.itemText(i)
                cand_raw = candidate.split("  [")[0] if "  [" in candidate else candidate
                if cand_raw == raw:
                    idx = i
                    break
        if idx >= 0:
            self.port_cb.setCurrentIndex(idx)
        self.port_cb.blockSignals(False)

    def _on_port_activated(self, index):
        if (index >= 0 and "Refresh" in self.port_cb.itemText(index)):
            self.refresh_ports()

    def _role_style(self):
        role = self.roles.role()
        if role == ROLE_ENGINEER:
            return ("QPushButton { background:#2C6E49; color:white; border:none; "
                    "border-radius:4px; font-family:'Segoe UI'; font-size:11px; "
                    "font-weight:bold; padding:4px; }")
        if self.roles.is_supervisor():
            return ("QPushButton { background:#1A5C94; color:white; border:none; "
                    "border-radius:4px; font-family:'Segoe UI'; font-size:11px; "
                    "font-weight:bold; padding:4px; }")
        return ("QPushButton { background:#6B5E4E; color:white; border:none; "
                "border-radius:4px; font-family:'Segoe UI'; font-size:11px; "
                "font-weight:bold; padding:4px; }")

    def _toggle_role(self):
        cur = self.roles.role()
        idx = _ROLE_ORDER.index(cur) if cur in _ROLE_ORDER else 0
        new_role = _ROLE_ORDER[(idx + 1) % len(_ROLE_ORDER)]
        old = self.roles.role()
        self.roles.set_role(new_role)
        self.role_btn.setText(f"ROLE: {new_role.upper()}")
        self.role_btn.setStyleSheet(self._role_style())
        from ..services.settings import save as save_settings
        save_settings({"role": new_role})
        self.audit.record("ROLE_CHANGE", f"role changed {old} -> {new_role}", new_role)
        cap = {
            ROLE_OPERATOR: "Operator: view-only dashboards, alarms, trends, events.",
            ROLE_SUPERVISOR: "Supervisor: Operator access + reset/tare, calibrate, "
                             "configure layers/params, export, diagnostics.",
            ROLE_ENGINEER: "Engineer: Supervisor access + raw encoder information, "
                           "advanced measurement parameters, communication & "
                           "firmware diagnostics.",
        }[new_role]
        QMessageBox.information(self.window(), "Role changed",
                                f"Active role: {new_role.upper()}\n{cap}")

    # -- state setters ------------------------------------------------------
    def set_connected(self, connected, port=""):
        if connected:
            self.lbl.setText(f"● CONNECTED  {port}")
            self.lbl.setStyleSheet("color:#1A7A30; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        else:
            self.lbl.setText("● DISCONNECTED")
            self.lbl.setStyleSheet("color:#C03920; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def set_demo(self, on):
        if on:
            self.demo_btn.setText("Stop Demo")
            self.lbl.setText("● DEMO MODE")
            self.lbl.setStyleSheet("color:#8A6010; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        else:
            self.demo_btn.setText("Demo Mode")
            self.set_connected(False)

    def set_error(self):
        self.lbl.setStyleSheet("color:#C03920; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def set_link_quality(self, q):
        # q: -1 integrity issue, 0 disconnected, 0.5 legacy, 1.0 good
        if q < 0:
            self.link_lbl.setText("DATA INTEGRITY ISSUE")
            self.link_lbl.setStyleSheet("color:#C03920; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        elif q <= 0:
            self.link_lbl.setText("LINK: --")
            self.link_lbl.setStyleSheet("color:#8A8A8A; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        elif q < 1:
            self.link_lbl.setText("LINK: LEGACY (NO CRC)")
            self.link_lbl.setStyleSheet("color:#7A5C10; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        else:
            self.link_lbl.setText("LINK: OK")
            self.link_lbl.setStyleSheet("color:#1A7A30; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def refresh_state(self, stale, link_down):
        """Render data-freshness state with proper recovery.

        Priority: link_lost > stale > legacy > ok. Because link_lost/stale are
        transient anomalies, this must be able to *recover* back to a green
        LINK: OK / DATA: OK once fresh data resumes — it must not latch red.
        """
        if self._demo_mode_active():
            return
        if link_down:
            self.link_lbl.setText("LINK: LOST")
            self.link_lbl.setStyleSheet("color:#C03920; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        elif stale:
            self.link_lbl.setText("DATA: STALE")
            self.link_lbl.setStyleSheet("color:#D47B0F; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
        else:
            # Data is fresh again — recover the indicator (do not keep prior red/amber).
            if self._legacy_source():
                self.link_lbl.setText("DATA: OK  (LEGACY, NO CRC)")
                self.link_lbl.setStyleSheet("color:#7A5C10; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")
            else:
                self.link_lbl.setText("DATA: OK")
                self.link_lbl.setStyleSheet("color:#1A7A30; font-family:'Segoe UI'; font-size:11px; font-weight:bold;")

    def _legacy_source(self):
        try:
            return bool(getattr(self.mw, "_legacy_mode", False))
        except Exception:
            return False

    def _demo_mode_active(self):
        try:
            return self.mw._demo_mode and self.mw._demo_mode
        except Exception:
            return False
