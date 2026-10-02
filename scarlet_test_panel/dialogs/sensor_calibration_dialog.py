"""Sensor-specific two-point calibration window (analog_monitor.txt §685-1373).

One reusable modal dialog for ANY analog channel 0..15.  It reads its live
signal from the EXISTING dashboard stream (mw.analog_voltages + the shared
0-5 V -> engineering mapping in SENSOR_CONFIG) — it opens no serial/sensor
connection, creates no second calculation engine, and never touches another
channel's calibration.

Two-point model: (lo_input -> lo_value) and (hi_input -> hi_value).
On "Save Calibration" the points are projected onto the engine's canonical
0-5 V endpoints so the rest of the application (gauges, graph, history,
Analog Monitor Value column) continues to use the SINGLE existing value
formula.  The points themselves are stored per channel in settings.json under
"analog_calibration" and are re-applied on startup.

Safety (RBAC): Operator opens a read-only view (status + info + live signal),
Engineer gets the full capture/edit/save workflow; after a successful save the
system returns to Operator via the existing role behavior.  Cancel discards
everything (the committed calibration is never touched until Save); closing
with unsaved changes asks for confirmation; Reset restores ONLY this channel's
factory defaults after confirmation.
"""
from collections import deque
import math

from PyQt5.QtWidgets import (
    QDialog, QApplication,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QDoubleSpinBox, QGroupBox, QMessageBox, QFrame, QWidget,
)
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt5.QtGui import (QPainter, QPen, QColor, QFont,
                         QFontMetrics, QBrush, QPainterPath, QLinearGradient)

from ..config import (
    SENSOR_CONFIG, _ANALOG_FACTORY,
    BG_CARD, BORDER, ACCENT,
    TEXT_DARK, TEXT_MID, TEXT_LITE, TEXT_ON_DARK,
    CAL_MIN_CLR, CAL_MAX_CLR, CAL_BAND_CLR,
    MS_PER_SAMPLE, GRID_CLR,
)

_S = {
    "card": (f"QGroupBox {{ background:{BG_CARD.name()}; border:1px solid {BORDER.name()}; "
             f"border-radius:6px; margin-top:8px; padding:6px; "
             f"font-family:'Segoe UI'; font-size:12px; color:{TEXT_MID.name()}; }}"),
    "section_title": (f"color:{ACCENT.name()}; font-family:'Georgia'; font-size:11px; "
                      f"font-weight:bold; letter-spacing:1.5px; background:transparent;"),
    "key": (f"color:{TEXT_MID.name()}; font-family:'Segoe UI'; font-size:11px; "
            f"background:transparent;"),
    "value": (f"color:{TEXT_DARK.name()}; font-family:'Segoe UI'; font-size:12px; "
              f"font-weight:bold; background:transparent;"),
    "spin": (f"QDoubleSpinBox {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
             f"border:1px solid {ACCENT.name()}; border-radius:4px; "
             f"font-family:'Segoe UI'; font-size:12px; padding:3px; min-height:26px; }} "
             f"QDoubleSpinBox:disabled {{ background:{BG_CARD.name()}; color:{TEXT_LITE.name()}; "
             f"border:1px solid {BORDER.name()}; }}"),
    "btn_pri": (f"QPushButton {{ background:{ACCENT.name()}; color:white; border:none; "
                f"border-radius:4px; font-family:'Segoe UI'; font-size:12px; "
                f"font-weight:bold; padding:6px 16px; }} "
                f"QPushButton:hover {{ background:#a03020; }} "
                f"QPushButton:disabled {{ background:{BORDER.name()}; color:{TEXT_LITE.name()}; }}"),
    "btn_sec": (f"QPushButton {{ background:{CAL_MAX_CLR.name()}; color:white; border:none; "
                f"border-radius:4px; font-family:'Segoe UI'; font-size:12px; "
                f"font-weight:bold; padding:6px 16px; }} "
                f"QPushButton:hover {{ background:#155e30; }} "
                f"QPushButton:disabled {{ background:{BORDER.name()}; color:{TEXT_LITE.name()}; }}"),
    "btn_flat": (f"QPushButton {{ background:{BG_CARD.name()}; color:{TEXT_DARK.name()}; "
                 f"border:1px solid {BORDER.name()}; border-radius:4px; "
                 f"font-family:'Segoe UI'; font-size:12px; padding:6px 14px; }} "
                 f"QPushButton:hover {{ background:{BORDER.name()}; }} "
                 f"QPushButton:disabled {{ color:{TEXT_LITE.name()}; }}"),
    "btn_danger": (f"QPushButton {{ background:{BG_CARD.name()}; color:{ACCENT.name()}; "
                   f"border:1px solid {ACCENT.name()}; border-radius:4px; "
                   f"font-family:'Segoe UI'; font-size:12px; padding:6px 14px; }} "
                   f"QPushButton:hover {{ background:{ACCENT.name()}; color:white; }} "
                   f"QPushButton:disabled {{ color:{TEXT_LITE.name()}; }}"),
}


def engine_value(cfg, v):
    """THE single existing 0-5 V -> engineering conversion (identical to
    main.py/_push_analog, widgets/cards, graph and monitor refresh)."""
    return (float(cfg["cal_min"]) + (float(v) / 5.0)
            * (float(cfg["cal_max"]) - float(cfg["cal_min"])))


def project_two_point(lo_in, lo_val, hi_in, hi_val):
    """Project a valid two-point (input -> engineering) calibration onto the
    engine's canonical 0 V and 5 V endpoints.
    Returns (cal_at_0v, cal_at_5v), or (None, None) for degenerate points."""
    span = float(hi_in) - float(lo_in)
    if abs(span) < 1e-9:
        return None, None
    slope = (float(hi_val) - float(lo_val)) / span
    return (float(lo_val) + slope * (0.0 - float(lo_in)),
            float(lo_val) + slope * (5.0 - float(lo_in)))


def point_value(v, lo_in, lo_val, hi_in, hi_val):
    """Linear interpolation through the two calibration points."""
    span = float(hi_in) - float(lo_in)
    if abs(span) < 1e-9:
        return None
    return float(lo_val) + (float(v) - float(lo_in)) / span * (
        float(hi_val) - float(lo_val))


_PANEL_BG = QColor(0xFF, 0xFF, 0xFC)      # cream plot background (dashboard look)
_PANEL_FRAME = QColor(0x44, 0x44, 0x44)    # block-position dashboard frame
_NOW_CLR = QColor(0xD4, 0x7B, 0x0F)        # orange "NOW" reference (dashboard)
_TIME_STEPS = (1, 2, 5, 10, 15, 30, 60, 120)


def _nice_step(lo0, hi0, n_ticks=6):
    raw = (hi0 - lo0) / (n_ticks - 1)
    step = 10.0 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if step * m * (n_ticks - 1) >= (hi0 - lo0):
            step = step * m
            break
    return step


def _fmt_y(val, step):
    if step >= 1.0:
        return f"{val:.0f}"
    if step >= 0.1:
        return f"{val:.1f}"
    return f"{val:.2f}"


def _y_px(val, lo, hi, pt, ph):
    span = max(hi - lo, 1e-9)
    y = pt + ph * (1.0 - (val - lo) / span)
    return max(pt, min(pt + ph, y))


class _VCurve(QWidget):
    """Voltage (x) vs SENSOR VALUE (y) live graph — dashboard styling.

    X axis = input voltage (V); Y axis = calibrated sensor value (klb etc.).
    Draws the projected two-point calibration line, the captured LOW/HIGH
    points, the live (V -> value) trace and a CURRENT tag. Repaints from the
    shared ring buffer so every box shows the same accurate curve."""

    def __init__(self, buf, unit, compact=False, parent=None):
        super().__init__(parent)
        self._buf = buf
        self._unit = unit
        self._compact = compact
        self._lo_in, self._lo_val = 0.0, 0.0
        self._hi_in, self._hi_val = 5.0, 0.0
        self.setMinimumHeight(118 if compact else 176)
        self.setMaximumHeight(118 if compact else 176)

    def set_points(self, lo_in, lo_val, hi_in, hi_val):
        self._lo_in, self._lo_val = float(lo_in), float(lo_val)
        self._hi_in, self._hi_val = float(hi_in), float(hi_val)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ---- panel: cream with soft vertical gradient + hairline frame
        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 8, 8)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(0xFF, 0xFF, 0xFA))
        grad.setColorAt(1.0, _PANEL_BG)
        p.fillPath(panel, QBrush(grad))
        p.setPen(QPen(QColor(0xE4, 0xDC, 0xD4), 1.0))
        p.drawPath(panel)

        # ---- plot geometry
        pl, pr, pt, pb = (64 if not self._compact else 50), 14, 30, 30
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)

        pts = list(self._buf)
        c0, c5 = project_two_point(self._lo_in, self._lo_val,
                                   self._hi_in, self._hi_val)
        if c0 is None:
            c0, c5 = 0.0, 1.0

        xs = [v for v, _c in pts] + [0.0, 5.0, self._lo_in, self._hi_in]
        ys = [c for _v, c in pts] + [c0, c5, self._lo_val, self._hi_val]
        x_lo, x_hi = min(xs), max(xs)
        if x_hi - x_lo < 1e-9:
            x_hi = x_lo + 1.0
        y_lo, y_hi = min(ys), max(ys)
        if y_hi - y_lo < 1e-9:
            y_hi = y_lo + 1.0
        step_x = _nice_step(x_lo, x_hi)
        step_y = _nice_step(y_lo, y_hi)
        y_lo_n = math.floor(y_lo / step_y) * step_y

        def X(v):
            return pl + (v - x_lo) / (x_hi - x_lo) * pw

        def Y(v):
            return _y_px(v, y_lo, y_hi, pt, ph)

        def num(v, step):
            if step >= 0.5:
                return f"{v:g}"
            dec = max(1, min(3, int(round(-math.log10(step)))))
            return f"{v:.{dec}f}".rstrip("0").rstrip(".")

        # ---- grid + tick labels (suppressed when they collide)
        f_tick = QFont("Segoe UI", 8)
        p.setFont(f_tick)
        fm = QFontMetrics(f_tick)
        grid_pen = QPen(QColor(GRID_CLR.red(), GRID_CLR.green(),
                               GRID_CLR.blue(), 120), 1.0)

        i = 0
        xx = math.floor(x_lo / step_x) * step_x
        x_last = None
        while xx <= x_hi + 1e-9:
            x = X(xx)
            if pl <= x <= pl + pw:
                p.setPen(grid_pen)
                p.drawLine(QPointF(x, pt), QPointF(x, pt + ph))
                lbl = num(xx, step_x)
                tw = fm.horizontalAdvance(lbl)
                if x_last is None or x - x_last > tw + 10:
                    p.setPen(QPen(TEXT_MID))
                    p.drawText(QPointF(x - tw / 2, pt + ph + fm.ascent() + 6), lbl)
                    x_last = x
            xx += step_x
            i += 1
            if i > 40:
                break

        i = 0
        yy = y_lo_n
        while yy <= y_hi + 1e-9:
            y = Y(yy)
            if pt <= y <= pt + ph:
                p.setPen(grid_pen)
                p.drawLine(QPointF(pl, y), QPointF(pl + pw, y))
                lbl = num(yy, step_y)
                tw = fm.horizontalAdvance(lbl)
                p.setPen(QPen(TEXT_MID))
                p.drawText(QPointF(pl - tw - 7, y + fm.ascent() / 2 - 1), lbl)
            yy += step_y
            i += 1
            if i > 40:
                break

        # ---- axis baselines
        p.setPen(QPen(_PANEL_FRAME, 1.2))
        p.drawLine(QPointF(pl, pt + ph), QPointF(pl + pw, pt + ph))
        p.drawLine(QPointF(pl, pt), QPointF(pl, pt + ph))

        # ---- legend chips (top-right): VOLTAGE + VALUE with swatches
        if len(pts) >= 1:
            lv, lval = pts[-1]
            f_chip = QFont("Segoe UI", 8 if not self._compact else 7, QFont.Bold)
            p.setFont(f_chip)
            fcm = QFontMetrics(f_chip)
            vtxt = f"VOLTAGE  {lv:.3f} V"
            ctxt = f"VALUE  {lval:.2f} {self._unit}"
            sw, gap = 3.2, 8
            y0 = pt + 4
            cy = y0 + fcm.ascent() // 2
            x = pl + pw - 2
            for txt, clr in ((ctxt, ACCENT), (vtxt, CAL_BAND_CLR)):
                tw = fcm.horizontalAdvance(txt)
                x = x - (14 + sw + tw)
                p.setPen(QColor(clr.name()))
                p.drawLine(QPointF(x, cy), QPointF(x + 8, cy))
                p.setBrush(QBrush(clr))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x + 10, cy), sw, sw)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(clr.name())))
                p.drawText(QPointF(x + 12 + sw, y0 + fcm.ascent()), txt)
                x -= gap

        # ---- trace: soft area fill + haloed line + live glow point
        if len(pts) >= 2:
            poly_ = [QPointF(X(v), Y(c)) for v, c in pts]
            ar = QPainterPath()
            ar.moveTo(poly_[0].x(), pt + ph)
            for q in poly_:
                ar.lineTo(q)
            ar.lineTo(poly_[-1].x(), pt + ph)
            ar.closeSubpath()
            fg = QLinearGradient(0, pt, 0, pt + ph)
            fg.setColorAt(0.0, QColor(ACCENT.red(), ACCENT.green(),
                                      ACCENT.blue(), 44))
            fg.setColorAt(1.0, QColor(ACCENT.red(), ACCENT.green(),
                                      ACCENT.blue(), 0))
            p.fillPath(ar, QBrush(fg))
            traj = QPainterPath()
            traj.moveTo(poly_[0])
            for q in poly_[1:]:
                traj.lineTo(q)
            p.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 55),
                          4.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(traj)
            p.setPen(QPen(QColor(ACCENT.name()), 2.0, Qt.SolidLine,
                          Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(traj)
            lx, ly = X(pts[-1][0]), Y(pts[-1][1])
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(CAL_BAND_CLR.red(), CAL_BAND_CLR.green(),
                                CAL_BAND_CLR.blue(), 70), 2.0))
            p.drawEllipse(QPointF(lx, ly), 7, 7)
            p.setBrush(QBrush(CAL_BAND_CLR))
            p.setPen(QPen(Qt.white, 1.4))
            p.drawEllipse(QPointF(lx, ly), 4, 4)

        # ---- LOW / HIGH reference rings
        for xv, yv, clr in ((self._lo_in, self._lo_val, CAL_MIN_CLR),
                            (self._hi_in, self._hi_val, CAL_MAX_CLR)):
            mx, my = X(xv), Y(yv)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(clr.name()), 1.6))
            p.drawEllipse(QPointF(mx, my), 3.4, 3.4)

        if not pts:
            f_empty = QFont("Segoe UI", 8, QFont.Bold)
            p.setFont(f_empty)
            fm2 = QFontMetrics(f_empty)
            msg = "ACQUIRING LIVE DATA"
            mw = fm2.horizontalAdvance(msg)
            cx = pl + pw / 2
            cy = pt + ph / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(CAL_BAND_CLR))
            p.drawEllipse(QPointF(cx - mw / 2 - 12, cy), 3.4, 3.4)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(TEXT_MID))
            p.drawText(QPointF(cx - mw / 2, cy - fm2.ascent() / 2), msg)

        # ---- axis titles
        t_ax = QFont("Segoe UI", 8, QFont.Bold)
        t_ax.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        p.setFont(t_ax)
        ftm = QFontMetrics(t_ax)
        p.setPen(QPen(TEXT_MID))
        xlab = "INPUT VOLTAGE (V)"
        p.drawText(QPointF(pl + pw / 2 - ftm.horizontalAdvance(xlab) / 2, h - 4), xlab)
        p.save()
        p.translate(11, pt + ph / 2)
        p.rotate(-90)
        ylab = f"SENSOR VALUE ({self._unit})"
        p.drawText(QPointF(-ftm.horizontalAdvance(ylab) / 2, 0), ylab)
        p.restore()

        # ---- plot hairline frame
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0x8A, 0x7A, 0x6E), 1.0))
        p.drawRect(pl, pt, pw, ph)


class SensorCalibrationDialog(QDialog):
    """Modal per-channel calibration window. The commit path is split from the
    modal accept so acceptance scripts can drive it without a message loop:
    set spins -> validate() -> commit()/on_commit callback."""

    def __init__(self, sensor_idx, device_id, get_voltage, get_status,
                 on_commit, roles, audit, parent=None):
        super().__init__(parent)
        self._idx = sensor_idx
        self._device = str(device_id)
        self._cfg = SENSOR_CONFIG[sensor_idx]
        self._get_voltage = get_voltage
        self._get_status = get_status
        self._commit_cb = on_commit          # called(idx, cal_min, cal_max)
        self.roles = roles
        self.audit = audit
        self._editable = bool(roles and roles.is_engineer())
        self._dirty = False
        self.ask_fn = None                   # test hook for confirmation boxes
        self._input_type = str(self._cfg.get("input_type", "0\u20135 V"))
        self._factory = _ANALOG_FACTORY[sensor_idx]

        lo_in = self._cfg.get("cal_in_lo")
        lo_val = self._cfg.get("cal_val_lo")
        hi_in = self._cfg.get("cal_in_hi")
        hi_val = self._cfg.get("cal_val_hi")
        if lo_in is None or hi_in is None:
            self._points = {
                "lo_in": 0.0, "lo_val": float(self._cfg["cal_min"]),
                "hi_in": 5.0, "hi_val": float(self._cfg["cal_max"]),
            }
            self._calibrated = False
        else:
            self._points = {
                "lo_in": float(lo_in), "lo_val": float(lo_val),
                "hi_in": float(hi_in), "hi_val": float(hi_val),
            }
            self._calibrated = True

        self.setWindowTitle(f"Sensor Calibration \u2014 {self._cfg['name']}")
        self.setModal(True)
        self.setMinimumSize(1060, 720)
        self.setStyleSheet(f"background:{BG_CARD.name()};")
        # Flat frameless panel (consistent with the rest of the industrial HMI):
        # no OS title bar / raised window frame — the dialog sits flat on the
        # screen, with the app's own header + close/drag controls instead.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._drag_off = None
        # one shared ring buffer painted identically at the bottom of every box
        self._trend_buf = deque(maxlen=320)
        # Wide on screen: fill most of the available desktop width.
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                self.resize(min(g.width() - 120, 1360), min(g.height() - 120, 820))
                self.move(g.left() + max((g.width() - self.width()) // 2, 40),
                          g.top() + max((g.height() - self.height()) // 2, 40))
            else:
                self.resize(1320, 800)
        except Exception:
            self.resize(1320, 800)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(20, 16, 20, 14)

        # ---- header: flat title bar (drag handle = title row, close = ✕) ---
        head = QHBoxLayout()
        title = QLabel("SENSOR CALIBRATION")
        title.setStyleSheet(_S["section_title"] + "font-size:15px;")
        head.addWidget(title)
        head.addStretch()
        if not self._editable:
            view = QLabel("VIEW ONLY")
            view.setStyleSheet(f"color:{TEXT_ON_DARK.name()}; background:{ACCENT.name()}; "
                               "border-radius:8px; font-family:'Segoe UI'; font-size:10px; "
                               "font-weight:bold; padding:4px 12px;")
            head.addWidget(view)
        self.btn_close = QPushButton("\u2715")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setToolTip("Close")
        self.btn_close.setStyleSheet(
            f"QPushButton {{ background:{BG_CARD.name()}; color:{TEXT_MID.name()}; "
            f"border:1px solid {BORDER.name()}; border-radius:4px; "
            f"font-family:'Segoe UI'; font-size:13px; font-weight:bold; }} "
            f"QPushButton:hover {{ background:{ACCENT.name()}; color:white; border:none; }}")
        self.btn_close.clicked.connect(self.close)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        ident = QLabel(
            f"Device: <b>{self._device}</b>    Channel: <b>{sensor_idx}</b>    "
            f"Sensor: <b>{self._cfg['name']}</b>    Unit: <b>{self._cfg['unit']}</b>    "
            f"Enable: <b>{'Enabled' if self._cfg.get('enabled', True) else 'Disabled'}</b>")
        ident.setTextFormat(Qt.RichText)
        ident.setStyleSheet(_S["key"])
        root.addWidget(ident)
        root.addWidget(self._rule())

# ---- body: LIVE full width, LOW/HIGH side by side, SUMMARY full width --
        body = QVBoxLayout()
        body.setSpacing(8)

        # ---- LIVE INPUT (updates from the existing stream, no new serial) ---
        live_card = QGroupBox("LIVE INPUT")
        live_card.setStyleSheet(_S["card"])
        ll = QVBoxLayout(live_card)
        ll.setSpacing(4)
        self.lbl_adc = QLabel("\u2014")
        self.lbl_voltage = QLabel("\u2014")
        self.lbl_curval = QLabel("\u2014")
        self.lbl_live_status = QLabel("\u2014")
        live_row = QHBoxLayout()
        live_row.setSpacing(18)
        for label, widget in (
                ("Raw ADC:", self.lbl_adc),
                ("Current Voltage:", self.lbl_voltage),
                ("Current Value:", self.lbl_curval),
                ("Input Signal Type:", QLabel(self._input_type)),
                ("Status:", self.lbl_live_status),
        ):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setStyleSheet(_S["key"])
            row.addWidget(lab)
            if isinstance(widget, QLabel):
                widget.setStyleSheet(_S["value"])
            row.addWidget(widget, 1)
            row.addStretch(2)
            live_row.addLayout(row, 1)
        ll.addLayout(live_row)
        body.addWidget(live_card)

        # ---- LOW / HIGH POINTS (engineer editable), side by side ------------
        self.spin_lo_in = self._mk_spin(0.0, -10.0, 10.0, 3, 0.001)
        self.spin_lo_val = self._mk_spin(self._points["lo_val"], -1e6, 1e6, 2, 1.0)
        self.spin_hi_in = self._mk_spin(self._points["hi_in"], -10.0, 10.0, 3, 0.001)
        self.spin_hi_val = self._mk_spin(self._points["hi_val"], -1e6, 1e6, 2, 1.0)
        self.spin_lo_in.setValue(self._points["lo_in"])
        self.spin_hi_val.setValue(self._points["hi_val"])

        pair = QHBoxLayout()
        pair.setSpacing(10)

        low_card = QGroupBox("LOW CALIBRATION POINT")
        low_card.setStyleSheet(_S["card"])
        low_lay = QGridLayout(low_card)
        low_lay.setHorizontalSpacing(8)
        low_lay.setVerticalSpacing(4)
        low_lay.addWidget(self._k("Input Voltage (V):"), 0, 0)
        low_lay.addWidget(self.spin_lo_in, 0, 1)
        self.lbl_lo_cap = QLabel("")
        self.lbl_lo_cap.setStyleSheet(_S["key"])
        low_lay.addWidget(self.lbl_lo_cap, 0, 2)
        low_lay.addWidget(self._k(f"Engineering Value ({self._cfg['unit']}):"), 1, 0)
        low_lay.addWidget(self.spin_lo_val, 1, 1)
        self.btn_set_lo = QPushButton("Set Low Point")
        self.btn_set_lo.setStyleSheet(_S["btn_sec"])
        self.btn_set_lo.clicked.connect(self._set_low_capture)
        low_lay.addWidget(self.btn_set_lo, 0, 3)
        pair.addWidget(low_card, 1)

        high_card = QGroupBox("HIGH CALIBRATION POINT")
        high_card.setStyleSheet(_S["card"])
        high_lay = QGridLayout(high_card)
        high_lay.setHorizontalSpacing(8)
        high_lay.setVerticalSpacing(4)
        high_lay.addWidget(self._k("Input Voltage (V):"), 0, 0)
        high_lay.addWidget(self.spin_hi_in, 0, 1)
        self.lbl_hi_cap = QLabel("")
        self.lbl_hi_cap.setStyleSheet(_S["key"])
        high_lay.addWidget(self.lbl_hi_cap, 0, 2)
        high_lay.addWidget(self._k(f"Engineering Value ({self._cfg['unit']}):"), 1, 0)
        high_lay.addWidget(self.spin_hi_val, 1, 1)
        self.btn_set_hi = QPushButton("Set High Point")
        self.btn_set_hi.setStyleSheet(_S["btn_pri"])
        self.btn_set_hi.clicked.connect(self._set_high_capture)
        high_lay.addWidget(self.btn_set_hi, 0, 3)
        pair.addWidget(high_card, 1)
        body.addLayout(pair)

        # ---- SUMMARY / PREVIEW (full width, big curve chart) ----------------
        sum_card = QGroupBox("CALIBRATION SUMMARY")
        sum_card.setStyleSheet(_S["card"])
        sl = QVBoxLayout(sum_card)
        sl.setSpacing(4)
        self.lbl_sum_lo = QLabel("\u2014")
        self.lbl_sum_hi = QLabel("\u2014")
        self.lbl_sum_status = QLabel("\u2014")
        for w in (self.lbl_sum_lo, self.lbl_sum_hi):
            w.setStyleSheet(_S["key"])
        self.lbl_sum_status.setStyleSheet(_S["value"])
        self.lbl_preview = QLabel("")
        self.lbl_preview.setStyleSheet(_S["key"])
        sum_row = QHBoxLayout()
        sum_row.setSpacing(24)
        for w in (self.lbl_sum_lo, self.lbl_sum_hi, self.lbl_preview):
            sum_row.addWidget(w, 1)
        sl.addLayout(sum_row)
        sl.addWidget(self.lbl_sum_status)
        body.addWidget(sum_card)
        root.addLayout(body)
        root.addWidget(self._rule())

        # ---- CONTROLS -------------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_read = QPushButton("Read Current Input")
        self.btn_read.setStyleSheet(_S["btn_flat"])
        self.btn_read.clicked.connect(self._read_current)
        self.btn_apply = QPushButton("Apply Calibration")
        self.btn_apply.setStyleSheet(_S["btn_pri"])
        self.btn_apply.clicked.connect(self._apply)
        self.btn_save = QPushButton("Save Calibration")
        self.btn_save.setStyleSheet(_S["btn_sec"])
        self.btn_save.clicked.connect(self._save)
        self.btn_reset = QPushButton("Reset Calibration")
        self.btn_reset.setStyleSheet(_S["btn_danger"])
        self.btn_reset.clicked.connect(self._reset)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(_S["btn_flat"])
        self.btn_cancel.clicked.connect(self.reject)
        for b in (self.btn_read, self.btn_apply, self.btn_save, self.btn_reset,
                  self.btn_cancel):
            btn_row.addWidget(b)
        root.addLayout(btn_row)

        # ---- single trend: full-width footer at the very bottom of the window
        self.page_trend = _VCurve(self._trend_buf, self._cfg["unit"])
        root.addWidget(self.page_trend)

        # RBAC: Operator = view-only (Read/Cancel still usable).
        if not self._editable:
            for w in (self.spin_lo_in, self.spin_lo_val,
                      self.spin_hi_in, self.spin_hi_val):
                w.setReadOnly(True)
                w.setEnabled(False)
            for b in (self.btn_set_lo, self.btn_set_hi, self.btn_apply,
                      self.btn_save, self.btn_reset):
                b.setEnabled(False)

        self._lock_inputs = not self._editable
        for sig in (self.spin_lo_in.valueChanged, self.spin_lo_val.valueChanged,
                    self.spin_hi_in.valueChanged, self.spin_hi_val.valueChanged):
            sig.connect(self._on_edited)

        self._update_summary()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._live_update)
        self._timer.start(MS_PER_SAMPLE)

    # ------------------------------------------------------------------ util
    def _rule(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER.name()}; max-height:1px; border:none;")
        return sep

    def _k(self, text, color=None):
        lab = QLabel(text)
        lab.setStyleSheet(_S["key"] if color is None else
                          f"color:{color.name()}; font-family:'Segoe UI'; font-size:11px;")
        return lab

    def _mk_spin(self, value, lo, hi, dec, step):
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(dec)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        spin.setFixedWidth(150)
        spin.setStyleSheet(_S["spin"])
        return spin

    def stop_live(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    # --------------------------------------------------- frameless move/drag
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 64:
            self._drag_off = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        self._drag_off = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_off)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_off = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------ live input
    def _live_update(self):
        try:
            v = float(self._get_voltage() or 0.0)
        except (TypeError, ValueError):
            return
        cfg = self._cfg
        self.lbl_adc.setText(f"{int(round(v / 5.0 * 1023))}")
        self.lbl_voltage.setText(f"{v:.3f} V")
        eng = engine_value(cfg, v)
        self.lbl_curval.setText(f"{eng:.2f} {cfg['unit']}")
        self._trend_buf.append((float(v), float(eng)))
        self.page_trend.update()
        try:
            label, color = self._get_status()
        except Exception:
            label, color = "NORMAL", CAL_MAX_CLR
        self.lbl_live_status.setText(label)
        self.lbl_live_status.setStyleSheet(
            f"color:{color.name() if hasattr(color, 'name') else color}; "
            "font-family:'Segoe UI'; font-size:12px; font-weight:bold;")

    def _read_current(self):
        self._live_update()
        self._note("Current input read from the live sensor stream.",
                   CAL_MAX_CLR)

    # ------------------------------------------------------ point capture
    def _set_low_capture(self):
        v = float(self._get_voltage() or 0.0)
        self.spin_lo_in.setValue(round(v, 3))
        self._warn_if_no_signal()
        self.spin_lo_val.setFocus()

    def _set_high_capture(self):
        v = float(self._get_voltage() or 0.0)
        self.spin_hi_in.setValue(round(v, 3))
        self._warn_if_no_signal()
        self.spin_hi_val.setFocus()

    def _warn_if_no_signal(self):
        self._dirty = True
        self._update_summary()
        try:
            label, _color = self._get_status()
        except Exception:
            label = "NORMAL"
        if label not in ("NORMAL", "SIMULATION"):
            self._note(f"No live signal ({label}) \u2014 verify the sensor "
                       "connection before saving.", CAL_BAND_CLR)

    # ------------------------------------------------------------- staging
    def _on_edited(self, *_):
        self._dirty = True
        self._update_summary()

    def _stage(self):
        self._points.update({
            "lo_in": self.spin_lo_in.value(),
            "lo_val": self.spin_lo_val.value(),
            "hi_in": self.spin_hi_in.value(),
            "hi_val": self.spin_hi_val.value(),
        })

    def validate(self):
        """Return (ok, reason). All spec §12 failure modes."""
        p = self._points
        if not all(k in p for k in ("lo_in", "lo_val", "hi_in", "hi_val")):
            return False, "Calibration points are missing."
        try:
            lo_in, lo_val = float(p["lo_in"]), float(p["lo_val"])
            hi_in, hi_val = float(p["hi_in"]), float(p["hi_val"])
        except (TypeError, ValueError):
            return False, "Numeric values are invalid."
        if abs(hi_in - lo_in) < 1e-6:
            return (False, "Low and high input values are identical \u2014 "
                    "enter two different input voltages.")
        return True, "Valid"

    def _update_summary(self):
        p = self._points
        unit = self._cfg["unit"]
        lo_in, lo_val = p["lo_in"], p["lo_val"]
        hi_in, hi_val = p["hi_in"], p["hi_val"]
        self.lbl_sum_lo.setText(f"Low Point:  {lo_in:.3f} V \u2192 {lo_val:.2f} {unit}")
        self.lbl_sum_hi.setText(f"High Point: {hi_in:.3f} V \u2192 {hi_val:.2f} {unit}")
        ok, reason = self.validate()
        self.lbl_sum_status.setText(f"Status: {'Valid' if ok else 'Invalid'}")
        self.lbl_sum_status.setStyleSheet(
            _S["value"] + (f"color:{CAL_MAX_CLR.name()};" if ok else f"color:{ACCENT.name()};"))
        live_v = self._get_voltage_safe()
        pv = point_value(live_v, lo_in, lo_val, hi_in, hi_val)
        if pv is not None and ok:
            self.lbl_preview.setText(f"Preview value at current input: {pv:.2f} {unit}")
        else:
            self.lbl_preview.setText("")
        self.page_trend.set_points(lo_in, lo_val, hi_in, hi_val)

    def _get_voltage_safe(self):
        try:
            return float(self._get_voltage() or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _note(self, text, color):
        self.lbl_sum_status.setText(text)
        self.lbl_sum_status.setStyleSheet(
            f"color:{color.name()}; font-family:'Segoe UI'; font-size:11px; "
            "font-weight:bold;");

    # ------------------------------------------------------------ controls
    def _apply(self):
        self._stage()
        ok, reason = self.validate()
        if not ok:
            self._note(reason, CAL_MAX_CLR)
            return
        self._dirty = True
        self._update_summary()
        self._note("Calibration applied \u2014 press Save Calibration to commit.",
                   CAL_MAX_CLR)

    def _commit(self):
        """Stage + write into SENSOR_CONFIG (runtime engine) without closing.
        Returns (cal_min, cal_max) projected onto 0-5 V, or None if invalid."""
        self._stage()
        ok, reason = self.validate()
        if not ok:
            self._note(reason, CAL_MAX_CLR)
            return None
        p = self._points
        cal_at_0v, cal_at_5v = project_two_point(
            p["lo_in"], p["lo_val"], p["hi_in"], p["hi_val"])
        if cal_at_0v is None:
            self._note("Invalid calibration \u2014 points are degenerate.",
                       CAL_MAX_CLR)
            return None
        cfg = self._cfg
        cfg["cal_min"] = cal_at_0v
        cfg["cal_max"] = cal_at_5v
        cfg["cal_in_lo"] = p["lo_in"]
        cfg["cal_val_lo"] = p["lo_val"]
        cfg["cal_in_hi"] = p["hi_in"]
        cfg["cal_val_hi"] = p["hi_val"]
        self._calibrated = True
        self._dirty = False
        return cal_at_0v, cal_at_5v

    def _commit_and_notify(self):
        res = self._commit()
        if res is None:
            return False
        try:
            self._commit_cb(self._idx, res[0], res[1])
        except Exception:
            pass
        return True

    def _save(self):
        if not self._editable:
            return
        if self._commit_and_notify():
            self.stop_live()
            self.accept()

    def _reset(self):
        if not self._editable:
            return
        if not self._ask("Reset Calibration",
                         "Restore this sensor's default calibration?\n"
                         "Only this channel is affected \u2014 device, channel, "
                         "enable state and other calibrations are unchanged."):
            return
        fmin, fmax = self._factory
        self._points = {
            "lo_in": 0.0, "lo_val": float(fmin),
            "hi_in": 5.0, "hi_val": float(fmax),
        }
        self.spin_lo_in.setValue(0.0)
        self.spin_lo_val.setValue(float(fmin))
        self.spin_hi_in.setValue(5.0)
        self.spin_hi_val.setValue(float(fmax))
        cfg = self._cfg
        for k in ("cal_in_lo", "cal_val_lo", "cal_in_hi", "cal_val_hi"):
            cfg.pop(k, None)
        self._calibrated = False
        self._dirty = True
        self._update_summary()
        self._note("Factory defaults restored \u2014 press Save Calibration to "
                   "commit or Cancel to discard.", CAL_BAND_CLR)

    def _ask(self, title, text):
        if self.ask_fn is not None:
            return bool(self.ask_fn(title, text))
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.addButton("OK", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is not None and \
            box.buttonRole(box.clickedButton()) == QMessageBox.AcceptRole

    # ------------------------------------------------------------ window
    def closeEvent(self, event):
        if self._dirty and not self._ask(
                "Unsaved Changes",
                "Calibration changes have not been saved.\nDiscard them?"):
            event.ignore()
            return
        self.stop_live()
        super().closeEvent(event)