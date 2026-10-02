"""Theme token system for the Detect Sensor Signal Dashboard.

Design tokens are the single source of truth for color, spacing, and type
scale. UI code should read colors/tokens from `theme.current()` (or the
shortcuts below) instead of hardcoding hex / QColor values, so a theme
switch (light <-> dark) re-skins the whole app without touching component
code.

Two palettes are shipped:
  * "light"  — the original warm-paper industrial scheme (DEFAULT).
  * "dark"   — a high-contrast panel display scheme for harsh ambient light.

ANSI contrast guidance: status colors are chosen so that on their intended
background they exceed ~4.5:1 for text and are distinguishable by an
accompanying glyph (never color-only signalling).

The active theme name is persisted in settings.json ("theme").
"""
from PyQt5.QtGui import QColor

# ── Typography scale (px) ────────────────────────────────────
class Type:
    XS  = 10
    SM  = 11
    MD  = 12
    LG  = 14
    XL  = 18
    XXL = 24
    DISPLAY = 72

# ── Spacing scale (px) ───────────────────────────────────────
class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24


def _c(*args):
    """Build a QColor from 3 or 4 ints."""
    return QColor(*args)


def _name(color):
    """Return the hex '#RRGGBB' name (opaque)."""
    return color.name()


# ─────────────────────────────────────────────────────────────
# Light palette (the original scheme → default)
# ─────────────────────────────────────────────────────────────
_LIGHT = {
    "BG_MAIN":           _c(0xF5, 0xF0, 0xE8),
    "BG_CARD":           _c(0xFF, 0xFD, 0xF7),
    "BG_INNER":          _c(0xEC, 0xE5, 0xD8),
    "ACCENT":            _c(0xC0, 0x39, 0x2B),   # signal red
    "ACCENT_HOVER":      _c(0xA0, 0x30, 0x20),
    "ACCENT_PRESSED":    _c(0x8E, 0x2A, 0x1C),
    "TEXT_DARK":         _c(0x1A, 0x14, 0x0A),
    "TEXT_MID":          _c(0x6B, 0x5E, 0x4E),
    "TEXT_LITE":         _c(0xA0, 0x92, 0x80),
    "TEXT_ON_DARK":      _c(0xFF, 0xF5, 0xF0),   # light text on dark surfaces
    "BORDER":            _c(0xC8, 0xBD, 0xAD),
    "BORDER_HOVER":      _c(0xB5, 0xA9, 0x99),
    "GRID_CLR":          _c(0xD8, 0xCF, 0xC0),
    "CAL_MIN_CLR":       _c(0x1A, 0x5C, 0x94),   # blue
    "CAL_MAX_CLR":       _c(0x1E, 0x7A, 0x3E),   # green
    "CAL_BAND_CLR":      _c(0xD4, 0x7B, 0x0F),   # amber
    "OK":                _c(0x1E, 0x7A, 0x3E),   # normal / ok
    "WARN":              _c(0xD4, 0x7B, 0x0F),   # warning
    "FAULT":             _c(0xC0, 0x39, 0x2B),   # fault
    "STALE":             _c(0x8A, 0x5A, 0x00),   # stale / no data
    "RECOVER":           _c(0xD4, 0x7B, 0x0F),   # recovered (amber w/ text)
    "INFO_LINK":         _c(0x1A, 0x5C, 0x94),   # hyperlink / info text
    "TEXT_ON_STATUS":    _c(0x7A, 0x5C, 0x10),   # e.g. demo-amber text
    "ON_OK":             _c(0x1A, 0x7A, 0x30),
    "ON_DANGER":         _c(0xC0, 0x39, 0x20),
    "ON_PENDING":        _c(0x8A, 0x60, 0x10),
    # sensor trace colors
    "SENSOR_COLORS": [
        _c(0xC0, 0x39, 0x2B),
        _c(0x1A, 0x5C, 0x94),
        _c(0x1E, 0x7A, 0x3E),
        _c(0xD4, 0x7B, 0x0F),
        _c(0x6B, 0x2D, 0x8B),
        _c(0x0F, 0x7A, 0x8A),
    ],
    # custom-painted backgrounds (gauge / graph)
    "GAUGE_BG_TOP": _c(0xFF, 0xFC, 0xF5),
    "GAUGE_BG_MID": _c(0xE8, 0xDF, 0xD0),
    "GAUGE_BG_BTM": _c(0xBB, 0xAE, 0x9A),
    "GAUGE_RIM":    _c(0xA0, 0x92, 0x80),
    "GRAPH_BG":     _c(0xFF, 0xFF, 0xFB),
    "GRAPH_GRID":   _c(0xB0, 0xA4, 0x90),
}

# ─────────────────────────────────────────────────────────────
# Dark palette (high-contrast panel display)
# Text-on-dark is light; status hues shift lighter to hold contrast
# against dark backgrounds while remaining distinct from one another.
# ─────────────────────────────────────────────────────────────
_DARK = {
    "BG_MAIN":       _c(0x1E, 0x22, 0x2B),
    "BG_CARD":       _c(0x2A, 0x30, 0x3A),
    "BG_INNER":      _c(0x24, 0x2A, 0x33),
    "ACCENT":        _c(0xE5, 0x4B, 0x3D),
    "ACCENT_HOVER":  _c(0xF2, 0x5C, 0x4E),
    "ACCENT_PRESSED":_c(0xC9, 0x3A, 0x2E),
    "TEXT_DARK":     _c(0xF2, 0xF0, 0xEA),
    "TEXT_MID":      _c(0xC7, 0xC2, 0xB8),
    "TEXT_LITE":     _c(0x9A, 0x96, 0x8C),
    "TEXT_ON_DARK":  _c(0xFF, 0xFF, 0xFF),
    "BORDER":        _c(0x45, 0x4C, 0x59),
    "BORDER_HOVER":  _c(0x5A, 0x63, 0x73),
    "GRID_CLR":      _c(0x3A, 0x41, 0x4E),
    "CAL_MIN_CLR":   _c(0x5D, 0xA9, 0xE8),   # lighter blue
    "CAL_MAX_CLR":   _c(0x6F, 0xD0, 0x86),   # lighter green
    "CAL_BAND_CLR":  _c(0xF5, 0xA6, 0x2E),   # lighter amber
    "OK":            _c(0x6F, 0xD0, 0x86),
    "WARN":          _c(0xF5, 0xA6, 0x2E),
    "FAULT":         _c(0xF0, 0x6B, 0x5C),
    "STALE":         _c(0xD9, 0xB3, 0x5A),
    "RECOVER":       _c(0xF5, 0xA6, 0x2E),
    "INFO_LINK":     _c(0x5D, 0xA9, 0xE8),
    "TEXT_ON_STATUS":_c(0xE8, 0xC7, 0x6A),
    "ON_OK":         _c(0x6F, 0xD0, 0x86),
    "ON_DANGER":     _c(0xF0, 0x6B, 0x5C),
    "ON_PENDING":    _c(0xE8, 0xC7, 0x6A),
    "SENSOR_COLORS": [
        _c(0xF0, 0x6B, 0x5C),
        _c(0x5D, 0xA9, 0xE8),
        _c(0x6F, 0xD0, 0x86),
        _c(0xF5, 0xA6, 0x2E),
        _c(0xC4, 0x7F, 0xE0),
        _c(0x4F, 0xC7, 0xD6),
    ],
    "GAUGE_BG_TOP": _c(0x35, 0x3C, 0x48),
    "GAUGE_BG_MID": _c(0x2C, 0x32, 0x3C),
    "GAUGE_BG_BTM": _c(0x22, 0x27, 0x30),
    "GAUGE_RIM":    _c(0x9A, 0x96, 0x8C),
    "GRAPH_BG":     _c(0x2A, 0x30, 0x3A),
    "GRAPH_GRID":   _c(0x4A, 0x52, 0x60),
}

# ─────────────────────────────────────────────────────────────
# Red palette (professional industrial Oil & Gas control HMI)
# A dark-red structural chrome combined with white content cards for
# maximum readability. Hierarchy:
#   BG_MAIN  dark red        — window / structure / major areas
#   BG_INNER deep red        — panels, section bars, status & button bg
#   ACCENT   industrial red  — primary action buttons & emphasis
#   BORDER   light red       — outlines, separators, highlights
#   BG_CARD  white/light     — content, inputs, tables, data entry
# Text is light on the dark-red surfaces and dark on white surfaces.
# ─────────────────────────────────────────────────────────────
_RED = {
    "BG_MAIN":       _c(0x7B, 0x1A, 0x1A),   # dark industrial red
    "BG_CARD":       _c(0xFF, 0xFD, 0xF7),   # white content / inputs / tables
    "BG_INNER":      _c(0x8E, 0x23, 0x23),   # deep industrial red
    "ACCENT":        _c(0xC0, 0x39, 0x2B),   # industrial red
    "ACCENT_HOVER":  _c(0xA9, 0x30, 0x26),
    "ACCENT_PRESSED":_c(0x8E, 0x24, 0x1C),
    "TEXT_DARK":     _c(0x1A, 0x14, 0x0A),   # on-white text
    "TEXT_MID":      _c(0x5A, 0x48, 0x3C),   # muted on-white text
    "TEXT_LITE":     _c(0xA0, 0x96, 0x8C),   # faint on-white text
    "TEXT_ON_DARK":  _c(0xFF, 0xF5, 0xF0),   # light text on dark red
    "BORDER":        _c(0xD9, 0x53, 0x4F),   # light red accent
    "BORDER_HOVER":  _c(0xE0, 0x66, 0x63),
    "GRID_CLR":      _c(0xC8, 0xB0, 0xAC),
    "CAL_MIN_CLR":   _c(0x2A, 0x7F, 0xBD),   # blue marker
    "CAL_MAX_CLR":   _c(0x2E, 0x8B, 0x4E),   # green marker
    "CAL_BAND_CLR":  _c(0xD4, 0x7B, 0x0F),   # amber band
    "OK":            _c(0x3E, 0xA0, 0x5C),   # normal / ok
    "WARN":          _c(0xD9, 0x8A, 0x1C),   # warning
    "FAULT":         _c(0xC0, 0x39, 0x2B),   # fault
    "STALE":         _c(0x9A, 0x66, 0x14),   # stale / no data
    "RECOVER":       _c(0xD9, 0x8A, 0x1C),   # recovered (amber + text)
    "INFO_LINK":     _c(0x2A, 0x7F, 0xC0),   # hyperlink / info
    "TEXT_ON_STATUS":_c(0xFF, 0xFF, 0xFF),   # text on status chips
    "ON_OK":         _c(0xFF, 0xFF, 0xFF),
    "ON_DANGER":     _c(0xFF, 0xFF, 0xFF),
    "ON_PENDING":    _c(0xFF, 0xFF, 0xFF),
    "SENSOR_COLORS": [
        _c(0xC0, 0x39, 0x2B),
        _c(0x2A, 0x7F, 0xBD),
        _c(0x2E, 0x8B, 0x4E),
        _c(0xD9, 0x8A, 0x1C),
        _c(0x7B, 0x3F, 0xA0),
        _c(0x2F, 0x8B, 0x94),
    ],
    "GAUGE_BG_TOP": _c(0x9E, 0x30, 0x28),
    "GAUGE_BG_MID": _c(0x86, 0x26, 0x22),
    "GAUGE_BG_BTM": _c(0x6E, 0x1C, 0x1C),
    "GAUGE_RIM":    _c(0xD9, 0x53, 0x4F),
    "GRAPH_BG":     _c(0xFF, 0xFF, 0xFB),
    "GRAPH_GRID":   _c(0xBE, 0xA6, 0xA2),
}

_STYLES = {"light": _LIGHT, "dark": _DARK, "red": _RED}


class Theme:
    """Resolves palette + spacing/type tokens and exposes helpers."""

    def __init__(self, name="light"):
        self._name = name if name in _STYLES else "light"

    @property
    def name(self):
        return self._name

    def color(self, token):
        pal = _STYLES[self._name]
        if token in pal:
            val = pal[token]
            return val if isinstance(val, QColor) else val[0]
        raise KeyError(f"unknown theme token: {token}")

    def color_name(self, token):
        return self.color(token).name()

    def sensor_color(self, idx):
        pal = _STYLES[self._name]
        colors = pal["SENSOR_COLORS"]
        return colors[idx % len(colors)]

    def tokens(self):
        return dict(_STYLES[self._name])

    def type_scale(self):
        return Type

    def spacing(self):
        return Space

    # Convenience lookup for values that are stored as plain hex strings.
    def hex(self, token):
        return self.color_name(token)


# Global current-theme holder (set once at app start / on toggle).
_CURRENT = Theme("light")


def set_theme(name):
    global _CURRENT
    _CURRENT = Theme(name)


def current():
    """Return the active Theme instance."""
    return _CURRENT


# ── Backward-compatible shortcuts used by existing UI code ───
# These resolve to the ACTIVE theme so un-migrated code still follows the
# theme. They mirror the historical config.py constant names.
def _tok(name):
    return current().color(name)


BG_MAIN   = None  # populated by _refresh_shortcuts() at import time below
BG_CARD   = None
BG_INNER  = None
ACCENT    = None
TEXT_DARK = None
TEXT_MID  = None
TEXT_LITE = None
TEXT_ON_DARK = None
BORDER    = None
GRID_CLR  = None
CAL_MIN_CLR  = None
CAL_MAX_CLR  = None
CAL_BAND_CLR = None


def refresh_shortcuts():
    """Rebind the module-level color shortcuts to the active theme.

    Existing UI modules do `from .theme import ACCENT`; rebinding these
    module attributes on theme switch keeps them in sync (they must call
    current()/re-style, but resolve correctly at render time).
    """
    global BG_MAIN, BG_CARD, BG_INNER, ACCENT, TEXT_DARK, TEXT_MID
    global TEXT_LITE, BORDER, GRID_CLR, CAL_MIN_CLR, CAL_MAX_CLR, CAL_BAND_CLR
    global TEXT_ON_DARK
    t = current()
    BG_MAIN   = t.color("BG_MAIN")
    BG_CARD   = t.color("BG_CARD")
    BG_INNER  = t.color("BG_INNER")
    ACCENT    = t.color("ACCENT")
    TEXT_DARK = t.color("TEXT_DARK")
    TEXT_MID  = t.color("TEXT_MID")
    TEXT_LITE = t.color("TEXT_LITE")
    TEXT_ON_DARK = t.color("TEXT_ON_DARK")
    BORDER    = t.color("BORDER")
    GRID_CLR  = t.color("GRID_CLR")
    CAL_MIN_CLR  = t.color("CAL_MIN_CLR")
    CAL_MAX_CLR  = t.color("CAL_MAX_CLR")
    CAL_BAND_CLR = t.color("CAL_BAND_CLR")


refresh_shortcuts()
