import datetime
import math
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QFont, QFontMetrics, QPainterPath

from ..config import (
    SESSION_START, WINDOW_SECS, MS_PER_SAMPLE, BORDER, GRID_CLR,
    TEXT_DARK, TEXT_MID, TEXT_LITE,
    sensor_color, TWO_POINT_CAL,
    CAL_MIN_CLR, CAL_MAX_CLR,
    FT_TO_M, TREND_LIVE_WINDOW_S,
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
            lbl = f"{val:.1f}"
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
    """Industrial rectangular BLOCK POSITION vs TIME trend (promt1.txt).

    READ-ONLY trend chart: it only visualizes the existing authoritative
    block position recorded by the sampler. Supports the professional time
    ranges (LIVE / 1 / 5 / 10 / 30 MIN / 1 HOUR), FT/M chart units (feet stay
    internal; meters = feet x FT_TO_M converted only at draw time), automatic
    stable Y scaling, real-clock time labels, hover data inspection, a CURRENT
    position marker and a PAUSED VIEW freeze (background recording always
    continues).

    `sample_fn(seconds)` returns an iterable of (timestamp, feet) ordered
    oldest->newest covering at least the requested window.
    """

    RANGES = [
        ("LIVE", 0),
        ("1 MIN", 60),
        ("5 MIN", 300),
        ("10 MIN", 600),
        ("30 MIN", 1800),
        ("1 HOUR", 3600),
    ]

    # Time-tick steps (seconds) chosen so a window shows at most ~6 labels,
    # which keeps the clock labels readable and non-overlapping.
    TIME_STEPS = (5, 10, 15, 30, 60, 150, 300, 450, 600, 900, 1800, 3600)

    def __init__(self, sample_fn, color, default_range_s=0, parent=None):
        super().__init__(parent)
        self.sample_fn = sample_fn
        self.color = color
        self.range_s = default_range_s
        self.unit = "FT"
        self.paused = False
        self.pause_anchor = None
        self._hover_ts = None
        self._hover_val = None
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

    # -- public controls -----------------------------------------------------
    def set_range_seconds(self, seconds):
        self.range_s = float(seconds or 0)
        self.update()

    def set_unit(self, unit):
        """Chart display unit (FT / M). Internal feet values are untouched;
        only the drawn labels/values convert (feet x 0.3048)."""
        unit = unit if unit in ("FT", "M") else "FT"
        if unit != self.unit:
            self.unit = unit
            self.update()

    def set_paused(self, paused):
        """Freeze ONLY the view. Sampling/recording keeps running (new samples
        still land in the rolling buffer); RESUME shows the newest data again."""
        self.paused = bool(paused)
        self.pause_anchor = datetime.datetime.now() if self.paused else None
        self._hover_ts = None
        self._hover_val = None
        self.update()

    # -- helpers -------------------------------------------------------------
    def _eff_now(self):
        if self.paused and self.pause_anchor:
            return self.pause_anchor
        return datetime.datetime.now()

    def _window_s(self):
        if self.range_s and self.range_s > 0:
            return float(self.range_s)
        return float(TREND_LIVE_WINDOW_S)

    def _is_center_mode(self):
        # LIVE mode (range_s == 0) keeps the newest point at the horizontal
        # center so the trace scrolls left into the past with blank future on
        # the right (a real-time DCS / oscilloscope trace). Fixed ranges fill
        # the full width with the newest point at the right edge.
        return not (self.range_s and self.range_s > 0)

    def _time_mapping(self, now):
        """Return (left_ts, right_ts, window_s) describing the x-axis extent."""
        window = self._window_s()
        if self._is_center_mode():
            half = window / 2.0
            return now - datetime.timedelta(seconds=half), \
                   now + datetime.timedelta(seconds=half), window
        return now - datetime.timedelta(seconds=window), now, window

    def _x_for(self, ts, left_ts, right_ts):
        denom = (right_ts - left_ts).total_seconds()
        if denom <= 1e-9:
            return 0.0
        return (ts - left_ts).total_seconds() / denom

    def _unit_factor(self):
        return FT_TO_M if self.unit == "M" else 1.0

    def _series(self, window_s):
        return list(self.sample_fn(window_s))

    @staticmethod
    def _time_step(window_s):
        chosen = _IndustrialTrendGraph.TIME_STEPS[-1]
        for s in _IndustrialTrendGraph.TIME_STEPS:
            if window_s / s <= 6:
                chosen = s
                break
        return chosen

    @staticmethod
    def _fmt_y(val, step):
        if step >= 1.0:
            return f"{val:.0f}"
        if step >= 0.1:
            return f"{val:.1f}"
        return f"{val:.2f}"

    def _y_px(self, val, lo, hi, pt, ph):
        span = max(hi - lo, 1e-9)
        y = pt + ph * (1.0 - (val - lo) / span)
        return max(pt, min(pt + ph, y))

    def _geometry(self):
        return 70, 14, 16, 48

    def mouseMoveEvent(self, event):
        w, h = self.width(), self.height()
        pl, pr, pt, pb = self._geometry()
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)
        mx, my = event.x(), event.y()
        if pl <= mx <= pl + pw and pt <= my <= pt + ph:
            now = self._eff_now()
            left_ts, right_ts, window = self._time_mapping(now)
            pts = [(ts, v) for ts, v in self._series(window)
                   if left_ts <= ts <= right_ts]
            best_dist = float('inf')
            best = None
            for ts, val in pts:
                xf = self._x_for(ts, left_ts, right_ts)
                dist = abs(pl + pw * xf - mx)
                if dist < best_dist:
                    best_dist = dist
                    best = (ts, val)
            if best is not None and best_dist <= 30:
                self._hover_ts, self._hover_val = best
            else:
                self._hover_ts = None
                self._hover_val = None
        else:
            self._hover_ts = None
            self._hover_val = None
        self.update()

    def leaveEvent(self, event):
        self._hover_ts = None
        self._hover_val = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = self._geometry()
        pw, ph = max(w - pl - pr, 10), max(h - pt - pb, 10)

        frame = QColor(0x44, 0x44, 0x44)
        painter.setBrush(QBrush(QColor(0xFF, 0xFF, 0xFC)))
        painter.setPen(QPen(frame, 1.2))
        painter.drawRect(0, 0, w - 1, h - 1)

        now = self._eff_now()
        left_ts, right_ts, window = self._time_mapping(now)
        pts = [(ts, v) for ts, v in self._series(window) if left_ts <= ts <= right_ts]
        if len(pts) < 1:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(frame, 1.2))
            painter.drawRect(pl, pt, pw, ph)
            empty_font = QFont("Segoe UI", 9, QFont.Bold)
            painter.setFont(empty_font)
            fm = QFontMetrics(empty_font)
            base = "LIVE" if self._is_center_mode() else "TREND"
            msg = f"AWAITING {base} DATA" if not self.paused \
                else f"AWAITING {base} DATA  (PAUSED)"
            painter.setPen(QPen(TEXT_MID))
            painter.drawText(QPointF(pl + pw / 2 - fm.horizontalAdvance(msg) / 2,
                                     pt + ph / 2 + fm.ascent() / 2 - 1), msg)
            return

        cf = self._unit_factor()
        vals = [v * cf for _, v in pts]
        n_ticks = 6

        # Stable auto-scaling: data range + 10% margin, snapped to "nice" tick
        # values so labels stay clean and tiny movements don't rescale wildly.
        v_lo = min(vals)
        v_hi = max(vals)
        span = max(v_hi - v_lo, 1e-9)
        pad = span * 0.10
        lo0, hi0 = v_lo - pad, v_hi + pad
        if hi0 <= lo0:
            hi0 = lo0 + 1.0
        raw = (hi0 - lo0) / (n_ticks - 1)
        step_y = 10.0 ** math.floor(math.log10(raw))
        for m in (1, 2, 5, 10):
            if step_y * m * (n_ticks - 1) >= (hi0 - lo0):
                step_y = step_y * m
                break
        lo = math.floor(lo0 / step_y) * step_y
        hi = lo + step_y * (n_ticks - 1)
        while hi < hi0:
            hi += step_y
        while lo > lo0:
            lo -= step_y

        # Grid + y-axis labels.
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        for i in range(n_ticks):
            y = pt + ph * i / (n_ticks - 1)
            val = hi - (hi - lo) * i / (n_ticks - 1)
            painter.setPen(QPen(GRID_CLR, 0.8, Qt.SolidLine))
            painter.drawLine(QPointF(pl, y), QPointF(pl + pw, y))
            lbl = self._fmt_y(val, step_y)
            tw = fm.horizontalAdvance(lbl)
            painter.setPen(QPen(TEXT_DARK))
            painter.drawText(QPointF(pl - tw - 7, y + fm.ascent() / 2 - 1), lbl)

        # Time axis: real-clock labels aligned to the visible extent.
        # (In LIVE/center mode the "now" line sits at the horizontal middle;
        #  left = past, right = future.)
        left_ts_ep = left_ts.timestamp()
        right_ts_ep = right_ts.timestamp()
        step_t = self._time_step(window)
        font_t = QFont("Segoe UI", 8)
        painter.setFont(font_t)
        fm_t = QFontMetrics(font_t)
        t0 = int(left_ts_ep) - (int(left_ts_ep) % step_t)
        prev_right = None
        for t in range(t0, int(right_ts_ep) + 1, step_t):
            xf = self._x_for(datetime.datetime.fromtimestamp(t), left_ts, right_ts)
            if xf < -0.02 or xf > 1.02:
                continue
            x = pl + pw * xf
            painter.setPen(QPen(GRID_CLR, 0.8, Qt.SolidLine))
            painter.drawLine(QPointF(x, pt), QPointF(x, pt + ph))
            if abs(t - now.timestamp()) < step_t / 2:
                lbl_t = "NOW"
                painter.setPen(QPen(QColor(0xC0, 0x39, 0x2B)))
            elif step_t < 60:
                lbl_t = datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")
                painter.setPen(QPen(TEXT_DARK))
            else:
                lbl_t = datetime.datetime.fromtimestamp(t).strftime("%H:%M")
                painter.setPen(QPen(TEXT_DARK))
            lw = fm_t.horizontalAdvance(lbl_t)
            lx = x - lw / 2
            if prev_right is not None and lx < prev_right + 8:
                continue
            painter.drawText(QPointF(lx, pt + ph + fm_t.height() + 3), lbl_t)
            prev_right = lx + lw

        # Slim "NOW" reference line at the current instant (center in LIVE).
        now_xf = self._x_for(now, left_ts, right_ts)
        now_x = pl + pw * now_xf
        painter.setPen(QPen(QColor(0xD4, 0x7B, 0x0F, 120), 1.0, Qt.DotLine))
        painter.drawLine(QPointF(now_x, pt), QPointF(now_x, pt + ph))

        # Axis titles.
        painter.setPen(QPen(TEXT_MID))
        title_font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(title_font)
        fmtt = QFontMetrics(title_font)
        painter.drawText(QPointF(pl + pw / 2 - fmtt.horizontalAdvance("TIME") / 2, h - 5), "TIME")
        y_title = f"BLOCK POSITION ({self.unit})"
        painter.save()
        painter.translate(12, pt + ph / 2)
        painter.rotate(-90)
        painter.drawText(QPointF(-fmtt.horizontalAdvance(y_title) / 2, 0), y_title)
        painter.restore()

        # Trace (converted only for pixel placement; internal feet untouched).
        painter.setClipRect(pl, pt, pw + 1, ph + 1)
        painter.setPen(QPen(self.color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        first = True
        for ts, raw_val in pts:
            xf = self._x_for(ts, left_ts, right_ts)
            x = pl + pw * min(max(xf, 0.0), 1.0)
            y = self._y_px(raw_val * cf, lo, hi, pt, ph)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        painter.drawPath(path)
        painter.setClipping(False)

        # CURRENT-position tag (top-right inside the plot) + end dot.
        last_val = vals[-1]
        last_xf = self._x_for(pts[-1][0], left_ts, right_ts)
        dot_x = pl + pw * min(max(last_xf, 0.0), 1.0)
        dot_y = self._y_px(vals[-1], lo, hi, pt, ph)
        painter.setBrush(QBrush(self.color))
        painter.setPen(QPen(Qt.white, 1.5))
        painter.drawEllipse(QPointF(dot_x, dot_y), 4, 4)
        tag_font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(tag_font)
        fm_b = QFontMetrics(tag_font)
        tag = f"CURRENT  {last_val:.2f} {self.unit}"
        tag_w = fm_b.horizontalAdvance(tag)
        tag_x = pl + pw - tag_w - 4
        if tag_x > pl:
            painter.setPen(QPen(TEXT_DARK))
            painter.drawText(QPointF(tag_x, pt + 3 + fm_b.ascent()), tag)

        # PAUSED VIEW tag (top-left inside the plot) when the view is frozen.
        if self.paused:
            paused_font = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(paused_font)
            fm_p = QFontMetrics(paused_font)
            painter.setPen(QPen(QColor(0xD4, 0x7B, 0x0F)))
            painter.drawText(QPointF(pl + 4, pt + 3 + fm_p.ascent()), "PAUSED VIEW")

        # Hover data inspection (Time + Position), never blocks live updates.
        if self._hover_ts is not None and self._hover_val is not None:
            hval = self._hover_val * cf
            hf = self._x_for(self._hover_ts, left_ts, right_ts)
            hx = pl + pw * min(max(hf, 0.0), 1.0)
            hy = self._y_px(hval, lo, hi, pt, ph)
            painter.setPen(QPen(QColor(0x80, 0x80, 0x80, 160), 1, Qt.DashLine))
            painter.drawLine(QPointF(hx, pt), QPointF(hx, pt + ph))
            painter.setBrush(QBrush(self.color))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(QPointF(hx, hy), 4, 4)
            lines = [
                f"Time:  {self._hover_ts.strftime('%H:%M:%S')}",
                f"Position: {hval:.2f} {self.unit}",
            ]
            tip_font = QFont("Segoe UI", 8)
            painter.setFont(tip_font)
            fm_tip = QFontMetrics(tip_font)
            line_h = fm_tip.height() + 2
            max_w = max(fm_tip.horizontalAdvance(l) for l in lines)
            pad = 6
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

        # Plot frame on top.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(frame, 1.2))
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