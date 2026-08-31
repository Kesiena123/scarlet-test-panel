import math
from PyQt5.QtWidgets import QWidget, QSizePolicy
from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QFont, QRadialGradient, QLinearGradient, QPainterPath, QFontMetrics
from ..config import ACCENT, BORDER, TEXT_DARK, TEXT_MID, TEXT_LITE, CAL_MIN_CLR, CAL_MAX_CLR, CAL_BAND_CLR

class GaugeWidget(QWidget):
    def __init__(self, unit, min_val, max_val, tick_count=6, accent_color=None, parent=None):
        super().__init__(parent)
        self.unit = unit
        self.min_val = float(min_val)
        self.max_val = float(max_val)
        self.tick_count = tick_count
        self._value = float(min_val)
        self._active = True
        self.accent = accent_color or ACCENT
        self._cal_band = None
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent;")

    def setValue(self, value):
        self._value = max(self.min_val, min(self.max_val, float(value)))
        self.update()

    def setActive(self, active):
        self._active = active
        self.update()

    def setCalBand(self, frac_lo, frac_hi):
        self._cal_band = (frac_lo, frac_hi)
        self.update()

    def clearCalBand(self):
        self._cal_band = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        size = min(w, h) - 10
        cx, cy = w / 2, h / 2
        r = size / 2

        bz = QRadialGradient(cx, cy, r)
        bz.setColorAt(0.0, QColor(0xFF, 0xFC, 0xF5))
        bz.setColorAt(0.75, QColor(0xE8, 0xDF, 0xD0))
        bz.setColorAt(1.0, QColor(0xBB, 0xAE, 0x9A))
        painter.setBrush(QBrush(bz))
        painter.setPen(QPen(QColor(0xA0, 0x92, 0x80), 2))
        painter.drawEllipse(QPointF(cx, cy), r, r)

        face_r = r * 0.87
        fg = QRadialGradient(cx - face_r * 0.15, cy - face_r * 0.15, face_r)
        fg.setColorAt(0.0, QColor(0xFF, 0xFF, 0xFA))
        fg.setColorAt(0.7, QColor(0xF8, 0xF3, 0xE8))
        fg.setColorAt(1.0, QColor(0xEC, 0xE5, 0xD8))
        painter.setBrush(QBrush(fg))
        painter.setPen(QPen(BORDER, 1.0))
        painter.drawEllipse(QPointF(cx, cy), face_r, face_r)

        if not self._active:
            painter.setBrush(QBrush(QColor(0xEC, 0xE5, 0xD8, 180)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), face_r, face_r)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(BORDER, 0.7))
        painter.drawEllipse(QPointF(cx, cy), face_r * 0.93, face_r * 0.93)

        arc_r = face_r * 0.76
        start_angle = 220
        span_angle = 260
        rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)

        pen_bg = QPen(QColor(0xD0, 0xC8, 0xB8), arc_r * 0.10, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen_bg)
        painter.drawArc(rect, int(start_angle * 16), int(-span_angle * 16))

        if self._cal_band is not None:
            flo, fhi = self._cal_band
            band_start = start_angle - flo * span_angle
            band_span = -(fhi - flo) * span_angle
            pen_band = QPen(CAL_BAND_CLR, arc_r * 0.07, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen_band)
            painter.drawArc(rect, int(band_start * 16), int(band_span * 16))

            a_min_rad = math.radians(start_angle - flo * span_angle)
            mx = cx + arc_r * math.cos(a_min_rad)
            my = cy - arc_r * math.sin(a_min_rad)
            painter.setBrush(QBrush(CAL_MIN_CLR))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(mx, my), arc_r * 0.065, arc_r * 0.065)

            a_max_rad = math.radians(start_angle - fhi * span_angle)
            mxr = cx + arc_r * math.cos(a_max_rad)
            myr = cy - arc_r * math.sin(a_max_rad)
            painter.setBrush(QBrush(CAL_MAX_CLR))
            painter.drawEllipse(QPointF(mxr, myr), arc_r * 0.065, arc_r * 0.065)

        frac = (self._value - self.min_val) / max(self.max_val - self.min_val, 1e-9)
        val_span = frac * span_angle
        if val_span > 0 and self._active:
            pen_val = QPen(self.accent, arc_r * 0.07, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen_val)
            painter.drawArc(rect, int(start_angle * 16), int(-val_span * 16))

        total_minor = (self.tick_count - 1) * 4
        for i in range(total_minor + 1):
            f = i / total_minor
            a_deg = start_angle - f * span_angle
            a_rad = math.radians(a_deg)
            is_maj = (i % 4 == 0)
            outer = face_r * 0.80
            inner = face_r * 0.62 if is_maj else face_r * 0.74
            ca, sa = math.cos(a_rad), -math.sin(a_rad)
            painter.setPen(QPen(TEXT_DARK if is_maj else TEXT_LITE,
                                1.6 if is_maj else 0.8, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(cx + outer * ca, cy + outer * sa),
                             QPointF(cx + inner * ca, cy + inner * sa))
            if is_maj:
                tick_idx = i // 4
                val = self.min_val + (self.max_val - self.min_val) * (tick_idx / (self.tick_count - 1))
                lr = face_r * 0.47
                lx = cx + lr * ca
                ly = cy + lr * sa
                fs = max(8, int(face_r * 0.13))
                font = QFont("Georgia", fs, QFont.Bold)
                painter.setFont(font)
                painter.setPen(QPen(TEXT_DARK))
                txt = self._fmt(val)
                fm = QFontMetrics(font)
                tw = fm.horizontalAdvance(txt)
                th = fm.height()
                painter.drawText(QPointF(lx - tw / 2, ly + th / 3), txt)

        if self._active:
            nf = (self._value - self.min_val) / max(self.max_val - self.min_val, 1e-9)
            n_deg = start_angle - nf * span_angle
            n_rad = math.radians(n_deg)
            n_len = face_r * 0.70
            n_tail = face_r * 0.16
            n_w = face_r * 0.020
            cn, sn = math.cos(n_rad), -math.sin(n_rad)
            cp, sp = math.cos(n_rad + math.pi / 2), -math.sin(n_rad + math.pi / 2)
            tip = QPointF(cx + n_len * cn, cy + n_len * sn)
            tail = QPointF(cx - n_tail * cn, cy - n_tail * sn)
            left = QPointF(cx + n_w * cp, cy + n_w * sp)
            right = QPointF(cx - n_w * cp, cy - n_w * sp)
            path = QPainterPath()
            path.moveTo(tip); path.lineTo(left); path.lineTo(tail); path.lineTo(right)
            path.closeSubpath()
            ng = QLinearGradient(tail, tip)
            ng.setColorAt(0.0, QColor(0x8B, 0x10, 0x0A))
            ng.setColorAt(0.6, QColor(0xE0, 0x20, 0x10))
            ng.setColorAt(1.0, QColor(0xFF, 0x55, 0x44))
            painter.setBrush(QBrush(ng))
            painter.setPen(QPen(QColor(0x60, 0x08, 0x05), 0.6))
            painter.drawPath(path)

        cap_r = face_r * 0.068
        cg = QRadialGradient(cx - cap_r * 0.3, cy - cap_r * 0.3, cap_r)
        cg.setColorAt(0.0, QColor(0xE0, 0xD8, 0xCC))
        cg.setColorAt(1.0, QColor(0x80, 0x72, 0x60))
        painter.setBrush(QBrush(cg))
        painter.setPen(QPen(QColor(0x50, 0x44, 0x38), 1))
        painter.drawEllipse(QPointF(cx, cy), cap_r, cap_r)

        fs_d = max(9, int(face_r * 0.155))
        painter.setFont(QFont("Georgia", fs_d, QFont.Bold))
        painter.setPen(QPen(TEXT_DARK if self._active else TEXT_LITE))
        dig = self._fmt(self._value) if self._active else "—"
        fm3 = QFontMetrics(QFont("Georgia", fs_d, QFont.Bold))
        dw = fm3.horizontalAdvance(dig)
        painter.drawText(QPointF(cx - dw / 2, cy + face_r * 0.60), dig)

        gl = QRadialGradient(cx - face_r * 0.25, cy - face_r * 0.35, face_r * 0.8)
        gl.setColorAt(0.0, QColor(255, 255, 255, 55))
        gl.setColorAt(0.5, QColor(255, 255, 255, 18))
        gl.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(gl))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy), face_r, face_r)

    def _fmt(self, val):
        span = self.max_val - self.min_val
        if span >= 1000: return f"{int(round(val))}"
        elif span >= 10: return f"{val:.1f}"
        else:            return f"{val:.2f}"