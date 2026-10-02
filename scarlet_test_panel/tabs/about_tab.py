"""Settings / About page.

Surfaces system and device metadata, a light/dark theme toggle (persisted in
settings.json), the *device-confirmed* measurement model, and audit export.
Deliberate states: when the device has never confirmed its model the
calibration block shows an explicit "NOT VERIFIED — awaiting device STATUS"
empty state rather than fabricating values. The theme toggle re-skins the
core operator surfaces live via `main_window.apply_theme()`.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton, QComboBox,
)
from PyQt5.QtCore import Qt

from .. import theme
from ..config import BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_MID, TEXT_ON_DARK
from ..services.settings import PANEL_DATA_DIR

DASHBOARD_VERSION = "3.0.0"


class AboutTab(QWidget):
    def __init__(self, main_window, audit, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.audit = audit
        self._kv_value_widgets = []
        self._hint_widgets = []

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(10, 6, 14, 14)
        page_layout.setSpacing(12)

        # ── Theme (appearance) ───────────────────────────────
        self._gb_theme = QGroupBox("Appearance")
        theme_layout = QVBoxLayout(self._gb_theme)
        theme_layout.addWidget(self._hint(
            "Theme is applied immediately. Persisted to settings.json."))
        self.theme_toggle = QComboBox()
        self.theme_toggle.addItems(["Red", "Light", "Dark"])
        self.theme_toggle.setStyleSheet(
            f"QComboBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:6px; padding:4px; }}")
        self.theme_toggle.currentTextChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(self.theme_toggle)
        page_layout.addWidget(self._gb_theme)

        # ── System / dashboard about ─────────────────────────
        self._gb_about = QGroupBox("About this dashboard")
        about_layout = QVBoxLayout(self._gb_about)
        about_layout.addWidget(self._kv(
            "Dashboard version", f"v{DASHBOARD_VERSION}"))
        self.fw_lbl = self._kv("Firmware version", "--")
        self.build_lbl = self._kv("Firmware build", "--")
        self.protocol_lbl = self._kv("Protocol version", "--")
        self.uptime_lbl = self._kv("Device uptime", "--")
        self.boot_lbl = self._kv("Boot reason", "--")
        about_layout.addWidget(self.fw_lbl)
        about_layout.addWidget(self.build_lbl)
        about_layout.addWidget(self.protocol_lbl)
        about_layout.addWidget(self.uptime_lbl)
        about_layout.addWidget(self.boot_lbl)
        about_layout.addWidget(self._hint(
            "Device has no real-time clock; uptime is seconds since boot."))
        page_layout.addWidget(self._gb_about)

        # ── Device-confirmed measurement model ──────────────
        self._gb_cal = QGroupBox("Device-confirmed measurement model")
        cal_layout = QVBoxLayout(self._gb_cal)
        self.cal_status_lbl = QLabel()
        self.cal_status_lbl.setWordWrap(True)
        self.cal_l1_lbl = self._kv("Cal point 1 (pos / counter)", "--")
        self.cal_l2_lbl = self._kv("Cal point 2 (pos / counter)", "--")
        self.cal_l3_lbl = self._kv("Cal point 3 (pos / counter)", "--")
        self.cal_l4_lbl = self._kv("Cal point 4 (pos / counter)", "--")
        self.wits_lbl = self._kv("WITS correction (ft)", "--")
        self.calstat_lbl = self._kv("Calibration status", "--")
        self.onbottom_lbl = self._kv("On bottom", "--")
        self.pos_lbl = self._kv("Current block position (ft)", "--")
        self.layer_lbl = self._kv("Current wraps", "--")
        cal_layout.addWidget(self.cal_status_lbl)
        cal_layout.addWidget(self.cal_l1_lbl)
        cal_layout.addWidget(self.cal_l2_lbl)
        cal_layout.addWidget(self.cal_l3_lbl)
        cal_layout.addWidget(self.cal_l4_lbl)
        cal_layout.addWidget(self.wits_lbl)
        cal_layout.addWidget(self.calstat_lbl)
        cal_layout.addWidget(self.onbottom_lbl)
        cal_layout.addWidget(self.pos_lbl)
        cal_layout.addWidget(self.layer_lbl)
        page_layout.addWidget(self._gb_cal)

        # ── Audit export ─────────────────────────────────────
        self._gb_export = QGroupBox("Audit log export")
        export_layout = QHBoxLayout(self._gb_export)
        btn_csv = QPushButton("Export CSV")
        btn_pdf = QPushButton("Export PDF")
        self._export_btns = [btn_csv, btn_pdf]
        for b in self._export_btns:
            b.setStyleSheet(
                f"QPushButton {{ background:{BG_INNER.name()}; color:{TEXT_ON_DARK.name()}; "
                f"border:1px solid {BORDER.name()}; border-radius:6px; padding:6px 12px; }} "
                f"QPushButton:hover {{ background:{BORDER.name()}; }}")
        btn_csv.clicked.connect(lambda: self.mw.export_audit("csv"))
        btn_pdf.clicked.connect(lambda: self.mw.export_audit("pdf"))
        export_layout.addWidget(btn_csv)
        export_layout.addWidget(btn_pdf)
        export_layout.addWidget(self._hint(
            f"CSV/PDF include host timestamp + device uptime. Stored under "
            f"{PANEL_DATA_DIR}."), 1)
        page_layout.addWidget(self._gb_export)

        page_layout.addStretch(1)

        # Start at current persisted theme and populate live info.
        self.theme_toggle.blockSignals(True)
        current = theme.current().name
        self.theme_toggle.setCurrentText("Dark" if current == "dark" else "Light")
        self.theme_toggle.blockSignals(False)
        self._restyle()

    # ── layout helpers ───────────────────────────────────────
    def _kv(self, key, value):
        t = theme.current()
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        k = QLabel(key + ":")
        k.setStyleSheet(
            f"color:{t.color_name('TEXT_MID')}; font-family:'Segoe UI'; font-size:12px;")
        k.setMinimumWidth(140)
        k.setWordWrap(True)
        v = QLabel(value)
        v.setWordWrap(True)
        self._kv_value_widgets.append(v)
        l.addWidget(k)
        l.addWidget(v, 1)
        return w

    def _hint(self, text):
        t = theme.current()
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet(
            f"color:{t.color_name('TEXT_MID')}; font-family:'Segoe UI'; font-size:11px;")
        self._hint_widgets.append(l)
        return l

    def _label(self, wrapper):
        return wrapper.layout().itemAt(1).widget()

    # ── behaviour ────────────────────────────────────────────
    def _on_theme_changed(self, name):
        _MAP = {"Red": "red", "Light": "light", "Dark": "dark"}
        self.mw.apply_theme(_MAP.get(name, "red"))

    def _restyle(self):
        """Re-apply theme tokens to every styled control in this surface."""
        t = theme.current()
        _NAMES = {"red": "Red", "light": "Light", "dark": "Dark"}
        idx = ("Red", "Light", "Dark").index(_NAMES.get(theme.current().name, "Red"))
        self.theme_toggle.blockSignals(True)
        self.theme_toggle.setCurrentIndex(idx)
        self.theme_toggle.blockSignals(False)
        self.theme_toggle.setStyleSheet(
            f"QComboBox {{ background:{t.color_name('BG_CARD')}; color:{t.color_name('TEXT_DARK')}; "
            f"border:1px solid {t.color_name('BORDER')}; border-radius:6px; padding:4px; }}")
        for b in getattr(self, "_export_btns", []):
            b.setStyleSheet(
                f"QPushButton {{ background:{t.color_name('BG_INNER')}; color:{t.color_name('TEXT_ON_DARK')}; "
                f"border:1px solid {t.color_name('BORDER')}; border-radius:6px; padding:6px 12px; }} "
                f"QPushButton:hover {{ background:{t.color_name('BORDER')}; }}")
        for v in getattr(self, "_kv_value_widgets", []):
            v.setStyleSheet(
                f"color:{t.color_name('TEXT_DARK')}; font-family:'Segoe UI'; "
                f"font-size:12px; font-weight:bold;")
        for h in getattr(self, "_hint_widgets", []):
            h.setStyleSheet(
                f"color:{t.color_name('TEXT_MID')}; font-family:'Segoe UI'; font-size:11px;")
        for gb in (self._gb_theme, self._gb_about, self._gb_cal, self._gb_export):
            gb.setStyleSheet(
                f"QGroupBox {{ color:{t.color_name('TEXT_DARK')}; font-family:'Segoe UI'; "
                f"font-size:12px; font-weight:bold; border:1px solid {t.color_name('BORDER')}; "
                f"border-radius:10px; margin-top:10px; padding-top:4px; "
                f"background:{t.color_name('BG_CARD')}; }} "
                f"QGroupBox::title {{ subcontrol-origin:margin; left:12px; top:0px; "
                f"padding:2px 8px; color:{t.color_name('ACCENT')}; background:{t.color_name('BG_CARD')}; "
                f"border-radius:5px; }}")
        self.refresh()

    def apply_theme(self, name):
        self._restyle()

    def refresh(self):
        """Re-read live device metadata and the device-confirmed model."""
        t = theme.current()
        mw = self.mw
        fw = getattr(mw, "encoder_fw_version", "") or "--"
        build = getattr(mw, "encoder_build", "") or "--"
        self._label(self.fw_lbl).setText(fw)
        self._label(self.build_lbl).setText(build)
        proto = getattr(mw, "encoder_protocol_version", "") or "--"
        self._label(self.protocol_lbl).setText(proto)
        up = getattr(mw, "encoder_uptime_s", 0)
        self._label(self.uptime_lbl).setText(f"{up}s")
        boot = getattr(mw, "encoder_boot_reason", "") or "--"
        boot_err = getattr(mw, "encoder_boot_error", "") or ""
        self._label(self.boot_lbl).setText(
            boot if not boot_err else f"{boot} ({boot_err})")

        dev = getattr(mw, "device", None) or {}
        confirmed = bool(dev.get("confirmed"))
        if confirmed:
            self.cal_status_lbl.setText("VERIFIED — firmware-confirmed values")
            style = f"color:{t.color_name('OK')}; font-weight:bold; font-size:12px;"
            for i, (w_lbl, ct_lbl) in enumerate(
                    ((self.cal_l1_lbl, "calCounter1"),
                     (self.cal_l2_lbl, "calCounter2"),
                     (self.cal_l3_lbl, "calCounter3"),
                     (self.cal_l4_lbl, "calCounter4")), start=1):
                pos = dev.get(f"calPosition{i}", 0.0) or 0.0
                ctr = int(dev.get(ct_lbl, 0) or 0)
                self._label(w_lbl).setText(
                    f"{pos:.3f} ft / {ctr:}")
            self._label(self.wits_lbl).setText(
                f"{dev.get('witsCorrectionFt', 0.0):.3f}")
            self._label(self.calstat_lbl).setText(
                getattr(mw, "cal_status", None) or "NO_CALIBRATION")
            self._label(self.onbottom_lbl).setText(
                "YES" if getattr(mw, "on_bottom", False) else "NO")
            self._label(self.pos_lbl).setText(
                f"{mw.block_position_ft:.3f}")
            layer_n = int(mw.current_layer)
            self._label(self.layer_lbl).setText(f"Wrap {layer_n}")
        else:
            self.cal_status_lbl.setText(
                "NOT VERIFIED — awaiting device STATUS. Dashboard never shows "
                "a value as applied until the firmware confirms it.")
            style = f"color:{t.color_name('FAULT')}; font-weight:bold; font-size:12px;"
            for wrapper in (self.cal_l1_lbl, self.cal_l2_lbl, self.cal_l3_lbl,
                            self.cal_l4_lbl, self.wits_lbl, self.calstat_lbl,
                            self.onbottom_lbl, self.pos_lbl, self.layer_lbl):
                self._label(wrapper).setText("--")
        self.cal_status_lbl.setStyleSheet(style)