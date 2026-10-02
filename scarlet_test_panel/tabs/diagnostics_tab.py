"""Engineering / Diagnostics page (spec §4, §16, §23).

Technical information that must be kept OFF the operator's main readout lives
here, grouped by concern:

  * Operator measurement model (device-confirmed)
  * Encoder integrity (internal quadrature engine)
  * Communication health
  * Firmware / system status & reliability

Every value the firmware reports (calibration anchors, per-interval counts/ft,
WITS correction, counter, position, velocity, direction, layers, fw/protocol
version, sequence, eeprom status, boot reason) is shown verbatim from the
device — never independently recomputed. Exactly one section ("(firmware
fact)") is a static statement about the encoder driver; a few "dashboard-counted"
values (comm errors, heartbeat age, reboot count) are tracked locally and
clearly labelled. The dashboard never maintains a competing measurement
calculation.

The "Calculation Audit" section (promt3 §22) is the single exception, and it is
explicitly labelled as *dashboard-verified math*: it recomputes the exact
piecewise-linear interpolation from the device-confirmed anchors so an engineer
can independently check the arithmetic. The authoritative displayed position
remains the firmware report — this section is for verification only.

This page is visible to Supervisor and Engineer roles (engineering-only).
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QScrollArea, QFrame,
)
from PyQt5.QtCore import Qt
import time

from ..config import BG_CARD, BG_INNER, BORDER, TEXT_DARK, TEXT_MID


class DiagnosticsTab(QWidget):
    def __init__(self, main_window, roles, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.roles = roles
        self._kv_value_widgets = []
        self._kv_value_tags = {}   # key -> QLabel for flagged styling
        self._hint_widgets = []

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        container = QWidget()
        container.setObjectName("diag_scroll_container")
        container.setStyleSheet("background: transparent;")
        root = QVBoxLayout(container)
        root.setContentsMargins(10, 6, 14, 14)
        root.setSpacing(12)
        scroll.setWidget(container)
        page_layout.addWidget(scroll)

        # ── Operator measurement model ───────────────────────
        gb = QGroupBox("Operator Measurement Model  (device-confirmed)")
        lay = QVBoxLayout(gb)
        self._add_rows(lay, [
            ("calStatus", "Calibration status", "NO_CALIBRATION | VALID | OUT_OF_RANGE (firmware)"),
            ("calInRange", "Calibration in range", "1 when position is within the calibrated span"),
            ("calPosition1", "Cal point 1 position (ft)", "operator-defined, firmware-confirmed"),
            ("calCounter1", "Cal point 1 counter", "operator-defined, firmware-confirmed"),
            ("calPosition2", "Cal point 2 position (ft)", "operator-defined, firmware-confirmed"),
            ("calCounter2", "Cal point 2 counter", "operator-defined, firmware-confirmed"),
            ("calPosition3", "Cal point 3 position (ft)", "operator-defined, firmware-confirmed"),
            ("calCounter3", "Cal point 3 counter", "operator-defined, firmware-confirmed"),
            ("calPosition4", "Cal point 4 position (ft)", "operator-defined, firmware-confirmed"),
            ("calCounter4", "Cal point 4 counter", "operator-defined, firmware-confirmed"),
            ("countsPerFoot1", "Counts / ft (interval 1)", "per-interval, firmware-confirmed"),
            ("countsPerFoot2", "Counts / ft (interval 2)", "per-interval, firmware-confirmed"),
            ("countsPerFoot3", "Counts / ft (interval 3)", "per-interval, firmware-confirmed"),
            ("witsCorrectionFt", "WITS correction (ft)", "operator offset added to reported position"),
            ("currentTicks", "Counter (raw ticks)", "internal measurement value"),
            ("blockPositionFt", "Block position (ft)", "firmware-reported (authoritative)"),
            ("velocityFtMin", "Velocity (ft/min)", "firmware-reported"),
            ("direction", "Direction", "UP | DOWN (tick-derived)"),
            ("onBottom", "On bottom", "stopped near the lowest calibrated anchor"),
            ("currentLayer", "Current wraps", "decided by firmware from calibration anchors"),
        ])
        root.addWidget(gb)

        # ── promt3 §22: Calculation audit (exact per-interval math) ──
        gb = QGroupBox("Calculation Audit  (dashboard-verified, promt3 §22)")
        lay = QVBoxLayout(gb)
        self._calc_audit_items = []
        self._add_calc_audit_rows(lay)
        root.addWidget(gb)

        # ── Encoder integrity (internal driver) ─────────────
        gb = QGroupBox("Encoder Integrity  (internal quadrature driver)")
        lay = QVBoxLayout(gb)
        self._add_rows(lay, [
            ("decoding", "Decoding", "full quadrature, CHANGE on A=D2 and B=D3"),
            ("transition_table", "Transition table", "complete 16-entry table"),
            ("illegal_transitions", "Illegal transitions", "discarded — never counted as motion"),
            ("isr_rules", "ISR rules", "no delay() / Serial.print / EEPROM inside ISR"),
            ("read_safety", "Read safety", "counter reads wrapped in noInterrupts()/interrupts()"),
        ])
        root.addWidget(gb)

        # ── Communication health ────────────────────────────
        gb = QGroupBox("Communication Health")
        lay = QVBoxLayout(gb)
        self._add_rows(lay, [
            ("protocol_version", "Protocol version", "firmware-reported"),
            ("sequence", "Sequence number", "firmware-reported message counter"),
            ("msg_count", "Messages received", "dashboard-counted"),
            ("heartbeat_age", "Heartbeat age (s)", "seconds since last valid message"),
            ("crc_failures", "Checksum errors", "rejected CRC"),
            ("comm_errors", "Communication errors", "malformed / rejected / serial errors"),
        ])
        root.addWidget(gb)

        # ── Reliability / recovery ──────────────────────────
        gb = QGroupBox("Reliability & Recovery")
        lay = QVBoxLayout(gb)
        self._add_rows(lay, [
            ("fw_version", "Firmware version", "firmware-reported"),
            ("build", "Firmware build", "firmware-reported"),
            ("eeprom_status", "EEPROM status", "firmware-reported (OK / DEFAULTED)"),
            ("boot_reason", "Boot reason", "firmware-reported (WATCHDOG = reboot)"),
            ("boot_error", "Boot error", "firmware-reported"),
            ("reboot_count", "Reboot events detected", "when firmware uptime resets"),
            ("uptime_s", "Device uptime (s)", "seconds since firmware boot"),
            ("recovered_count", "Recovered-from-reboot count", "dashboard-counted"),
        ])
        root.addWidget(gb)

        self._hint_widgets.append(self._hint(
            "Values are reported verbatim by the firmware except those "
            "labelled (firmware fact) or (dashboard-counted). The dashboard "
            "never maintains a competing measurement calculation."))
        root.addWidget(self._hint_widgets[-1])

        root.addStretch(1)

        self._restyle()
        self.refresh()

    def _add_rows(self, layout, rows):
        for key, label, note in rows:
            layout.addWidget(self._kv(key, label, note))

    def _kv(self, key, label, note):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        k = QLabel(label + ":")
        k.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:12px;")
        k.setMinimumWidth(170)
        k.setWordWrap(True)
        v = QLabel("--")
        v.setStyleSheet(
            f"color:{TEXT_DARK.name()}; font-family:'Segoe UI'; font-size:12px; "
            f"font-weight:bold;")
        v.setWordWrap(True)
        self._kv_value_widgets.append(v)
        n = QLabel(note)
        n.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:10px;")
        n.setWordWrap(True)
        l.addWidget(k)
        l.addWidget(v, 1)
        l.addWidget(n)
        self._kv_value_tags[key] = v
        return w

    def _add_calc_audit_rows(self, layout):
        """promt3 §22: per-interval calculation breakdown for independent
        engineering verification. Uses the exact same piecewise-linear
        interpolation math as the firmware (mirrored by PumpDashboard.
        calibrated_position), computed from the device-confirmed anchors. It is
        an audit of the *math* only — the authoritative position always comes
        from the firmware report, never this section."""
        self._calc_audit_title = self._kv("audit_title", "Current position", "device-confirmed; computed below for verification")
        layout.addWidget(self._calc_audit_title)
        # Per-interval rows (max 3 intervals for 4 anchors).
        self._calc_audit_items = []
        for i in range(1, 4):
            header = self._kv(f"audit_h{i}", f"Interval L{i} → L{i+1}",
                              "pulse & foot span between consecutive anchors")
            layout.addWidget(header)
            rows = [
                f"audit_lower_p{i}", f"audit_upper_p{i}", f"audit_pdiff{i}",
                f"audit_fdiff{i}", f"audit_ppf{i}",
            ]
            for key in rows:
                layout.addWidget(self._kv(key, self._audit_label(key), " "))
            self._calc_audit_items.append({
                "header": header, "lower": rows[0], "upper": rows[1],
                "pdiff": rows[2], "fdiff": rows[3], "ppf": rows[4],
            })
        self._calc_audit_position_row = self._kv(
            "audit_pos", "Interpolated position (ft)", "dashboard-verified math, full precision")
        layout.addWidget(self._calc_audit_position_row)

    def _audit_label(self, key):
        rep = {
            "audit_lower_p": "Lower point (pulses / ft)",
            "audit_upper_p": "Upper point (pulses / ft)",
            "audit_pdiff": "Pulse difference",
            "audit_fdiff": "Feet difference",
            "audit_ppf": "Pulses per foot",
        }
        for stem, label in rep.items():
            if key.startswith(stem):
                return label
        return key

    def _refresh_calc_audit(self):
        mw = self.mw
        cards = mw.device or {}
        pts = mw.cal_points()  # sorted [(counter, posFt), ...] authoritative
        self.set_value("audit_title",
                       f"{int(mw.current_ticks):} pulses → "
                       f"{mw.block_position_ft:.4f} ft  "
                       f"({(mw.direction or 'DOWN').upper()})")
        # Exactly one interval label per interval position (indicating whether
        # two consecutive anchors exist for it).
        for idx, item in enumerate(self._calc_audit_items):
            span = idx + 1
            if len(pts) >= 2 and idx < len(pts) - 1:
                (c1, f1), (c2, f2) = pts[idx], pts[idx + 1]
                pdiff = c2 - c1
                fdiff = f2 - f1
                ppf = (pdiff / fdiff) if abs(fdiff) > 1e-9 else float("nan")
                # Header: the interval's own pulse & foot span (was left at the
                # "--" placeholder because no set_value call ever filled it).
                self.set_value(f"audit_h{span}",
                               f"{c1:} → {c2:} pulses  ·  "
                               f"{f1:.4f} → {f2:.4f} ft  "
                               f"(Δ {pdiff:} / {fdiff:.4f} ft)")
                self.set_value(f"audit_lower_p{span}", f"{c1:} / {f1:.4f} ft")
                self.set_value(f"audit_upper_p{span}", f"{c2:} / {f2:.4f} ft")
                self.set_value(f"audit_pdiff{span}", f"{pdiff:}")
                self.set_value(f"audit_fdiff{span}", f"{fdiff:.4f} ft")
                self.set_value(f"audit_ppf{span}", f"{ppf:.6f} pulses/ft" if abs(fdiff) > 1e-9 else "--")
            else:
                self.set_value(f"audit_h{span}",
                               "no anchors for this interval "
                               "(needs 2 confirmed calibration points)")
                self.set_value(f"audit_lower_p{span}", "--")
                self.set_value(f"audit_upper_p{span}", "--")
                self.set_value(f"audit_pdiff{span}", "--")
                self.set_value(f"audit_fdiff{span}", "--")
                self.set_value(f"audit_ppf{span}", "--")
        pos = mw.calibrated_position()
        self.set_value("audit_pos",
                       (f"{pos:.6f} ft" if pos is not None
                        else "OUT OF RANGE / NO CALIBRATION"))
        _ = cards

    def _hint(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet(
            f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px;")
        self._hint_widgets.append(l)
        return l

    def set_value(self, key, text):
        lbl = self._kv_value_tags.get(key)
        if lbl is not None:
            lbl.setText(text)

    def _restyle(self):
        for gb in (self.findChildren(QGroupBox)):
            gb.setStyleSheet(
                f"QGroupBox {{ color:{TEXT_DARK.name()}; font-family:'Segoe UI'; "
                f"font-size:12px; font-weight:bold; border:1px solid {BORDER.name()}; "
                f"border-radius:10px; margin-top:10px; padding-top:4px; "
                f"background:{BG_CARD.name()}; }} "
                f"QGroupBox::title {{ subcontrol-origin:margin; left:12px; top:0px; "
                f"padding:2px 8px; color:{TEXT_DARK.name()}; background:{BG_CARD.name()}; "
                f"border-radius:5px; }}")
        for h in self._hint_widgets:
            h.setStyleSheet(
                f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px;")

    def apply_theme(self, name):
        self._restyle()

    def refresh(self):
        mw = self.mw
        dev = mw.device or {}
        f = lambda x: ("--" if x is None else x)

        # operator measurement model
        self.set_value("calStatus", mw.cal_status or "NO_CALIBRATION")
        self.set_value("calInRange", f"{int(mw.cal_in_range or 0)}")
        for i in range(1, 5):
            self.set_value(f"calPosition{i}",
                           f(f"{dev.get(f'calPosition{i}', 0.0):.4f}"))
            self.set_value(f"calCounter{i}",
                           f(f"{int(dev.get(f'calCounter{i}', 0) or 0):}"))
        self.set_value("countsPerFoot1",
                       f(f"{dev.get('countsPerFoot1', 0.0):.4f}"))
        self.set_value("countsPerFoot2",
                       f(f"{dev.get('countsPerFoot2', 0.0):.4f}"))
        self.set_value("countsPerFoot3",
                       f(f"{dev.get('countsPerFoot3', 0.0):.4f}"))
        self.set_value("witsCorrectionFt",
                       f(f"{dev.get('witsCorrectionFt', 0.0):.4f}"))
        self.set_value("currentTicks", f"{int(mw.current_ticks):}")
        self.set_value("blockPositionFt", f"{mw.block_position_ft:.4f}")
        self.set_value("velocityFtMin", f"{mw.velocity_ft_min:.2f}")
        self.set_value("direction", (mw.direction or "DOWN").upper())
        self.set_value("onBottom", "YES" if mw.on_bottom else "NO")
        self.set_value("currentLayer", f"{int(mw.current_layer)}")

        # promt3 §22 — dashboard-verified calculation audit (math verification only)
        self._refresh_calc_audit()

        # encoder integrity (firmware facts)
        self.set_value("decoding", "full quadrature (A=D2 / B=D3)")
        self.set_value("transition_table", "16-entry, CHANGE interrupts")
        self.set_value("illegal_transitions", "discarded (never counted)")
        self.set_value("isr_rules", "compliant")
        self.set_value("read_safety", "protected")

        # communication health
        self.set_value("protocol_version", mw.encoder_protocol_version or "--")
        self.set_value("sequence", f"{int(mw.encoder_sequence)}")
        self.set_value("msg_count", f"{int(mw._msg_count)}")
        age = time.monotonic() - mw._last_valid_time
        self.set_value("heartbeat_age", f"{age:.1f}")
        self.set_value("crc_failures", f"{int(mw._crc_failures)}")
        self.set_value("comm_errors", f"{int(mw._comm_errors)}")

        # reliability / recovery
        self.set_value("fw_version", mw.encoder_fw_version or "--")
        self.set_value("build", mw.encoder_build or "--")
        self.set_value("eeprom_status", mw.encoder_eeprom_status or "--")
        self.set_value("boot_reason", mw.encoder_boot_reason or "--")
        self.set_value("boot_error", mw.encoder_boot_error or "--")
        self.set_value("reboot_count", f"{int(mw._reboot_count)}")
        self.set_value("uptime_s", f"{int(mw.encoder_uptime_s)}")
        self.set_value("recovered_count", f"{int(mw._recovered_count)}")