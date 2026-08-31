import datetime
import math
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QFont, QFontMetrics, QPainterPath

from ..config import (
    SESSION_START, WINDOW_SECS, MS_PER_SAMPLE, BORDER, GRID_CLR,
    TEXT_DARK, TEXT_MID, TEXT_LITE,
    sensor_color, TWO_POINT_CAL,
    CAL_MIN_CLR, CAL_MAX_CLR
)


class _GraphPlot(QWidget):
    def __init__(self, histories, configs, epoch_samples, parent=None):
        super().__init__(parent)
        self.histories = histories
        self.configs = configs
        self.epoch_samples = epoch_samples
        self._idx = 0
        self._hover_pos = None
        self._hover_data = None
        self._show_voltage = True
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(160)
        self.setMouseTracking(True)

    def set_sensor(self, idx):
        self._idx = idx
        self._hover_data = None
        self.update()

    def _plot_geometry(self):
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 86, 18, 20, 58
        return w, h, pl, pr, pt, pb, w - pl - pr, h - pt - pb

    def mouseMoveEvent(self, event):
        w, h, pl, pr, pt, pb, pw, ph = self._plot_geometry()
        mx, my = event.x(), event.y()
        if pl <= mx <= pl + pw and pt <= my <= pt + ph:
            self._hover_pos = QPointF(mx, my)
            hist = list(self.histories[self._idx])
            n = len(hist)
            now_s = (datetime.datetime.now() - SESSION_START).total_seconds()
            if n >= 2:
                best_dist = float('inf')
                best_sample = None
                for j, sample in enumerate(hist):
                    volt, cal, ts = sample[0], sample[1], sample[2]
                    age_s = (n - 1 - j) * MS_PER_SAMPLE / 1000.0
                    abs_s = now_s - age_s
                    x_frac = (abs_s % WINDOW_SECS) / WINDOW_SECS
                    sx = pl + pw * x_frac
                    dist = abs(sx - mx)
                    if dist < best_dist:
                        best_dist = dist
                        best_sample = (volt, cal, ts)
                self._hover_data = best_sample if best_dist < 30 else None
            else:
                self._hover_data = None
        else:
            self._hover_pos = None
            self._hover_data = None
        self.update()

    def leaveEvent(self, event):
        self._hover_pos = None
        self._hover_data = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h, pl, pr, pt, pb, pw, ph = self._plot_geometry()

        painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFB)))
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        cfg = self.configs[self._idx]
        color = sensor_color(self._idx)
        hist = list(self.histories[self._idx])
        cal_min = cfg["cal_min"]
        cal_max = cfg["cal_max"]
        cal_span = max(cal_max - cal_min, 1e-9)
        n = len(hist)

        now_s = (datetime.datetime.now() - SESSION_START).total_seconds()
        window_pos_s = now_s % WINDOW_SECS

        X_TICK_MINS = [10, 20, 30, 40, 50, 60]
        Y_TICKS = 6

        for mins in X_TICK_MINS:
            x = pl + pw * (mins * 60 / WINDOW_SECS)
            painter.setPen(QPen(QColor(0xB0, 0xA4, 0x90), 1.8, Qt.SolidLine))
            painter.drawLine(QPointF(x, pt), QPointF(x, pt + ph))

        painter.setPen(QPen(GRID_CLR, 0.8, Qt.DashLine))
        for i in range(Y_TICKS):
            y = pt + ph * i / (Y_TICKS - 1)
            painter.drawLine(QPointF(pl, y), QPointF(pl + pw, y))

        axis_font = QFont("Georgia", 11, QFont.Bold)
        painter.setFont(axis_font)
        painter.setPen(QPen(TEXT_DARK))
        fm = QFontMetrics(axis_font)

        for i in range(Y_TICKS):
            val = cal_max - cal_span * i / (Y_TICKS - 1)
            y = pt + ph * i / (Y_TICKS - 1)
            lbl = self._short(val)
            tw = fm.horizontalAdvance(lbl)
            painter.drawText(QPointF(pl - tw - 8, y + fm.ascent() / 2 - 1), lbl)

        tick_font = QFont("Georgia", 11, QFont.Bold)
        painter.setFont(tick_font)
        painter.setPen(QPen(TEXT_DARK))
        fm_t = QFontMetrics(tick_font)
        for mins in X_TICK_MINS:
            x = pl + pw * (mins * 60 / WINDOW_SECS)
            lbl = f"{mins} min"
            tw = fm_t.horizontalAdvance(lbl)
            painter.drawText(QPointF(x - tw / 2, pt + ph + fm_t.height() + 4), lbl)

        title_font = QFont("Georgia", 10, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QPen(TEXT_MID))
        fm_tt = QFontMetrics(title_font)
        painter.drawText(QPointF(pl + pw / 2 - fm_tt.horizontalAdvance("Time (minutes)") / 2, h - 8), "Time (minutes)")
        painter.save()
        painter.translate(14, pt + ph / 2)
        painter.rotate(-90)
        lbl_y = f"Cal. Value ({cfg['unit']})"
        painter.drawText(QPointF(-fm_tt.horizontalAdvance(lbl_y) / 2, 0), lbl_y)
        painter.restore()

        tp = TWO_POINT_CAL[self._idx]
        if tp["val_min"] is not None and tp["val_max"] is not None:
            for band_val, band_clr in [(tp["val_min"], CAL_MIN_CLR), (tp["val_max"], CAL_MAX_CLR)]:
                y_frac = (band_val - cal_min) / cal_span
                y_b = pt + ph * (1.0 - y_frac)
                if pt <= y_b <= pt + ph:
                    painter.setPen(QPen(band_clr, 1.0, Qt.DashLine))
                    painter.drawLine(QPointF(pl, y_b), QPointF(pl + pw, y_b))

        if n >= 2:
            pen = QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            first = True
            for j, sample in enumerate(hist):
                _volt, cal = sample[0], sample[1]
                age_s = (n - 1 - j) * MS_PER_SAMPLE / 1000.0
                abs_s = now_s - age_s
                x_frac = (abs_s % WINDOW_SECS) / WINDOW_SECS
                x = pl + pw * x_frac
                y_frac = (cal - cal_min) / cal_span
                y = pt + ph * (1.0 - y_frac)
                y = max(pt, min(pt + ph, y))
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            painter.drawPath(path)

            last_cal = hist[-1][1]
            x_frac = window_pos_s / WINDOW_SECS
            x = pl + pw * x_frac
            y = pt + ph * (1.0 - (last_cal - cal_min) / cal_span)
            y = max(pt, min(pt + ph, y))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(QPointF(x, y), 5, 5)

        prog_x = pl + pw * (window_pos_s / WINDOW_SECS)
        painter.setPen(QPen(QColor(0xD4, 0x7B, 0x0F, 140), 1.5, Qt.DotLine))
        painter.drawLine(QPointF(prog_x, pt), QPointF(prog_x, pt + ph))

        if self._hover_pos and self._hover_data:
            volt, cal, frozen_ts = self._hover_data
            age_s = (datetime.datetime.now() - frozen_ts).total_seconds()
            abs_s = now_s - age_s
            x_frac = (abs_s % WINDOW_SECS) / WINDOW_SECS
            hx = pl + pw * x_frac
            hy = pt + ph * (1.0 - (cal - cal_min) / cal_span)
            hy = max(pt, min(pt + ph, hy))

            painter.setPen(QPen(QColor(0x80, 0x80, 0x80, 160), 1, Qt.DashLine))
            painter.drawLine(QPointF(hx, pt), QPointF(hx, pt + ph))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(QPointF(hx, hy), 5, 5)

            span = cal_max - cal_min
            cal_str = (f"{int(round(cal))}" if span >= 1000 else f"{cal:.1f}" if span >= 10 else f"{cal:.2f}")
            time_str = frozen_ts.strftime("%I:%M:%S %p").lstrip("0")

            lines = [f"Time:  {time_str}", f"Volt:  {volt:.3f} V", f"Value: {cal_str} {cfg['unit']}"]
            if not self._show_voltage:
                lines = [l for l in lines if not l.startswith("Volt:")]

            tip_font = QFont("Georgia", 10)
            painter.setFont(tip_font)
            fm_tip = QFontMetrics(tip_font)
            line_h = fm_tip.height() + 2
            max_w = max(fm_tip.horizontalAdvance(l) for l in lines)
            pad = 7
            tip_w = max_w + pad * 2
            tip_h = line_h * len(lines) + pad * 2
            tx = hx + 10
            ty = max(pt, min(pt + ph - tip_h, hy - tip_h // 2))
            if tx + tip_w > pl + pw:
                tx = hx - tip_w - 10
            painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFA, 230)))
            painter.setPen(QPen(BORDER, 1))
            painter.drawRoundedRect(QRectF(tx, ty, tip_w, tip_h), 4, 4)
            painter.setPen(QPen(TEXT_DARK))
            for k, line in enumerate(lines):
                painter.drawText(QPointF(tx + pad, ty + pad + fm_tip.ascent() + k * line_h), line)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

    @staticmethod
    def _short(val):
        a = abs(val)
        if a >= 10000: return f"{val/1000:.0f}k"
        if a >= 100:   return f"{val:.0f}"
        if a >= 10:    return f"{val:.0f}"
        return f"{val:.1f}"


class _HistorianTrendGraph(QWidget):
    """Historian-backed position/depth/velocity trend with selectable ranges.

    Satisfies spec §13: the chart draws from REAL recorded telemetry (the
    SQLite historian / live sample deque), never fabricated values, and the
    operator can select 1 min / 5 min / 15 min / 1 hr / 8 hr / 24 hr.

    `sample_fn(seconds)` must return an iterable of (timestamp, value) ordered
    oldest->newest covering at least the requested window (older samples are
    simply off-screen). `range_s` is the currently selected window in seconds.
    """

    # (label, seconds)
    RANGES = [
        ("1 min", 60),
        ("5 min", 300),
        ("15 min", 900),
        ("1 hr", 3600),
        ("8 hr", 8 * 3600),
        ("24 hr", 24 * 3600),
    ]

    def __init__(self, sample_fn, y_label, color, default_range_s=60, parent=None):
        super().__init__(parent)
        self.sample_fn = sample_fn
        self.y_label = y_label
        self.color = color
        self.range_s = default_range_s
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(240)

    def set_range_seconds(self, seconds):
        self.range_s = float(seconds)
        self.update()

    def _series(self):
        return list(self.sample_fn(self.range_s))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 70, 18, 16, 46
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)

        painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFB)))
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        now = datetime.datetime.now()
        series = self._series()
        # Keep only samples within the selected window, oldest -> newest.
        cutoff = now - datetime.timedelta(seconds=self.range_s)
        pts = [(ts, val) for (ts, val) in series if ts >= cutoff]
        if len(pts) < 1:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(BORDER, 1))
            painter.drawRoundedRect(0, 0, w, h, 6, 6)
            return

        vals = [v for _, v in pts]
        v_min = min(0.0, min(vals))
        v_max = max(vals) if max(vals) > v_min else v_min + 10.0
        span = max(v_max - v_min, 1e-6)

        # Horizontal grid + time labels. Pick a sane tick spacing for the range.
        n_ticks = 6
        step = self.range_s / n_ticks
        painter.setPen(QPen(GRID_CLR, 0.8, Qt.DashLine))
        for i in range(n_ticks):
            y = pt + ph * i / (n_ticks - 1)
            painter.drawLine(QPointF(pl, y), QPointF(pl + pw, y))

        font = QFont("Georgia", 10, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        painter.setPen(QPen(TEXT_DARK))
        for i in range(n_ticks):
            val = v_max - span * i / (n_ticks - 1)
            y = pt + ph * i / (n_ticks - 1)
            lbl = f"{val:,.1f}"
            tw = fm.horizontalAdvance(lbl)
            painter.drawText(QPointF(pl - tw - 8, y + fm.ascent() / 2 - 1), lbl)

        # Vertical time labels (relative "minutes/hours ago").
        for i in range(1, n_ticks):
            x = pl + pw * (i / n_ticks)
            secs_ago = self.range_s - i * step
            if secs_ago >= 3600:
                lbl = f"-{secs_ago / 3600:.0f}h"
            else:
                lbl = f"-{secs_ago / 60:.0f}m"
            tw = fm.horizontalAdvance(lbl)
            painter.drawText(QPointF(x - tw / 2, pt + ph + fm.height() + 4), lbl)

        title_font = QFont("Georgia", 9, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QPen(TEXT_MID))
        fmtt = QFontMetrics(title_font)
        painter.drawText(QPointF(pl + pw / 2 - fmtt.horizontalAdvance("Time (before now)") / 2, h - 6),
                         "Time (before now)")
        painter.save()
        painter.translate(14, pt + ph / 2)
        painter.rotate(-90)
        painter.drawText(QPointF(-fmtt.horizontalAdvance(self.y_label) / 2, 0), self.y_label)
        painter.restore()

        # Value axes use seconds-from-start so the window maps to [0,1] linearly.
        t0 = (pts[0][0] - cutoff).total_seconds()
        t_end = (now - cutoff).total_seconds()
        denom = max(t_end - t0, 1e-6)

        painter.setPen(QPen(self.color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        first = True
        for ts, val in pts:
            xf = (ts - cutoff).total_seconds() / denom
            x = pl + pw * min(max(xf, 0.0), 1.0)
            y_frac = (val - v_min) / span
            y = pt + ph * (1.0 - y_frac)
            y = max(pt, min(pt + ph, y))
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        painter.drawPath(path)

        last_val = pts[-1][1]
        xf = (now - cutoff).total_seconds() / denom
        x = pl + pw * min(max(xf, 0.0), 1.0)
        y = pt + ph * (1.0 - (last_val - v_min) / span)
        y = max(pt, min(pt + ph, y))
        painter.setBrush(QBrush(self.color))
        painter.setPen(QPen(Qt.white, 1.5))
        painter.drawEllipse(QPointF(x, y), 5, 5)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)


class _IndustrialTrendGraph(QWidget):
    """Industrial rectangular trend for the Block Position Monitor (promt1.txt).

    Plain rectangular chart with a light grid, a black trace and a clear
    plot-frame (the reference HMI look). Supports 1 Minute / 1 Hour history.

    `sample_fn(seconds)` returns an iterable of (timestamp, value) ordered
    oldest->newest. Range selected with set_range_seconds().
    """

    RANGES = [
        ("1 Minute", 60),
        ("1 Hour", 3600),
    ]

    def __init__(self, sample_fn, color, default_range_s=60, parent=None):
        super().__init__(parent)
        self.sample_fn = sample_fn
        self.color = color
        self.range_s = default_range_s
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(240)

    def set_range_seconds(self, seconds):
        self.range_s = float(seconds)
        self.update()

    def _series(self):
        return list(self.sample_fn(self.range_s))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 70, 18, 14, 44
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)

        # Rectangular industrial frame (no rounded corners).
        painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFC)))
        painter.setPen(QPen(QColor(0x44, 0x44, 0x44), 1.2))
        painter.drawRect(0, 0, w - 1, h - 1)

        now = datetime.datetime.now()
        series = self._series()
        cutoff = now - datetime.timedelta(seconds=self.range_s)
        pts = [(ts, val) for (ts, val) in series if ts >= cutoff]
        if len(pts) < 1:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(BORDER, 1))
            painter.drawRect(pl, pt, pw, ph)
            return

        vals = [v for _, v in pts]
        v_min = min(0.0, min(vals))
        v_max = max(vals) if max(vals) > v_min else v_min + 10.0
        span = max(v_max - v_min, 1e-6)

        # Gridlines + y-axis labels (light, rectangular).
        n_ticks = 6
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        for i in range(n_ticks):
            y = pt + ph * i / (n_ticks - 1)
            painter.setPen(QPen(GRID_CLR, 0.8, Qt.SolidLine))
            painter.drawLine(QPointF(pl, y), QPointF(pl + pw, y))
            val = v_max - span * i / (n_ticks - 1)
            lbl = f"{val:,.1f}"
            tw = fm.horizontalAdvance(lbl)
            painter.setPen(QPen(TEXT_DARK))
            painter.drawText(QPointF(pl - tw - 7, y + fm.ascent() / 2 - 1), lbl)

        # Time / x-axis labels (minutes ago).
        step = self.range_s / n_ticks
        for i in range(1, n_ticks):
            x = pl + pw * (i / n_ticks)
            secs_ago = self.range_s - i * step
            lbl = f"-{secs_ago / 60:.0f}m"
            tw = fm.horizontalAdvance(lbl)
            painter.setPen(QPen(TEXT_DARK))
            painter.drawText(QPointF(x - tw / 2, pt + ph + fm.height() + 3), lbl)

        # Axis titles.
        painter.setPen(QPen(TEXT_MID))
        title_font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(title_font)
        fmtt = QFontMetrics(title_font)
        xcap = "min before now"
        painter.drawText(QPointF(pl + pw / 2 - fmtt.horizontalAdvance(xcap) / 2, h - 5), xcap)
        painter.save()
        painter.translate(12, pt + ph / 2)
        painter.rotate(-90)
        painter.drawText(QPointF(-fmtt.horizontalAdvance("Position (ft)") / 2, 0), "Position (ft)")
        painter.restore()

        # Trace (trace color), clipped to the plot frame.
        painter.setClipRect(pl, pt, pw + 1, ph + 1)
        t0 = (pts[0][0] - cutoff).total_seconds()
        t_end = (now - cutoff).total_seconds()
        denom = max(t_end - t0, 1e-6)
        painter.setPen(QPen(self.color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        first = True
        for ts, val in pts:
            xf = (ts - cutoff).total_seconds() / denom
            x = pl + pw * min(max(xf, 0.0), 1.0)
            y_frac = (val - v_min) / span
            y = pt + ph * (1.0 - y_frac)
            y = max(pt, min(pt + ph, y))
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        painter.drawPath(path)
        painter.setClipping(False)

        # Plot frame on top.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0x44, 0x44, 0x44), 1.2))
        painter.drawRect(pl, pt, pw, ph)


class _SingleTrendGraph(QWidget):
    def __init__(self, history_fn, y_label, color, parent=None):
        super().__init__(parent)
        self.history_fn = history_fn
        self.y_label = y_label
        self.color = color
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(240)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 70, 18, 16, 46
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)

        painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFB)))
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        hist = list(self.history_fn())
        if len(hist) < 1:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(BORDER, 1))
            painter.drawRoundedRect(0, 0, w, h, 6, 6)
            return

        vals = [x[0] for x in hist]
        v_min = min(0.0, min(vals))
        v_max = max(vals) if max(vals) > v_min else v_min + 10.0
        span = max(v_max - v_min, 1e-6)

        now_s = (datetime.datetime.now() - SESSION_START).total_seconds()
        window_pos_s = now_s % WINDOW_SECS

        X_TICK_MINS = [10, 20, 30, 40, 50, 60]
        for mins in X_TICK_MINS:
            x = pl + pw * (mins * 60 / WINDOW_SECS)
            painter.setPen(QPen(QColor(0xB0, 0xA4, 0x90), 1.4))
            painter.drawLine(QPointF(x, pt), QPointF(x, pt + ph))
        painter.setPen(QPen(GRID_CLR, 0.8, Qt.DashLine))
        for i in range(6):
            y = pt + ph * i / 5
            painter.drawLine(QPointF(pl, y), QPointF(pl + pw, y))

        font = QFont("Georgia", 10, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        painter.setPen(QPen(TEXT_DARK))
        for i in range(6):
            val = v_max - span * i / 5
            y = pt + ph * i / 5
            lbl = f"{val:.1f}"
            tw = fm.horizontalAdvance(lbl)
            painter.drawText(QPointF(pl - tw - 8, y + fm.ascent() / 2 - 1), lbl)
        for mins in X_TICK_MINS:
            x = pl + pw * (mins * 60 / WINDOW_SECS)
            lbl = f"{mins} min"
            tw = fm.horizontalAdvance(lbl)
            painter.drawText(QPointF(x - tw / 2, pt + ph + fm.height() + 4), lbl)

        title_font = QFont("Georgia", 9, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QPen(TEXT_MID))
        fmtt = QFontMetrics(title_font)
        painter.drawText(QPointF(pl + pw / 2 - fmtt.horizontalAdvance("Time (minutes)") / 2, h - 6), "Time (minutes)")
        painter.save()
        painter.translate(14, pt + ph / 2)
        painter.rotate(-90)
        painter.drawText(QPointF(-fmtt.horizontalAdvance(self.y_label) / 2, 0), self.y_label)
        painter.restore()

        n = len(hist)
        painter.setPen(QPen(self.color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        first = True
        for val, ts in hist:
            age_s = (datetime.datetime.now() - ts).total_seconds()
            abs_s = now_s - age_s
            x_frac = (abs_s % WINDOW_SECS) / WINDOW_SECS
            x = pl + pw * x_frac
            y_frac = (val - v_min) / span
            y = pt + ph * (1.0 - y_frac)
            y = max(pt, min(pt + ph, y))
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        painter.drawPath(path)

        if n >= 1:
            last_val = hist[-1][0]
            x = pl + pw * (window_pos_s / WINDOW_SECS)
            y = pt + ph * (1.0 - (last_val - v_min) / span)
            y = max(pt, min(pt + ph, y))
            painter.setBrush(QBrush(self.color))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(QPointF(x, y), 5, 5)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(BORDER, 1))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)