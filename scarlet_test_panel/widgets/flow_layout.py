"""A compact, dependency-free FlowLayout for responsive PyQt dashboards.

Implemented from the canonical Qt FlowLayout example: lays widgets out
left-to-right and wraps them to additional rows when the available width
can no longer hold them at their minimum/hint size. This is the Qt-native
equivalent of a web `flex-wrap` / `auto-fit` grid: more items fit per row on
wide desktop monitors and they wrap onto fewer-per-row on smaller laptops,
so cards never get squeezed too narrow or force horizontal overflow.

Use it anywhere you currently hard-code a fixed number of columns in a
QGridLayout and want the row count to adapt to the window width.
"""

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout


class FlowLayout(QLayout):
    """Lays child widgets LTR, wrapping to the next line when space runs out.

    Mirrors the behaviour of the reference Qt FlowLayout example while staying
    minimal: it supports `setSpacing`, `setContentsMargins` and item
    minimum/hint sizes, and reports a growing height hint so the enclosing
    scroll area expands vertically (never horizontally) as items wrap.
    """

    def __init__(self, parent=None, margin=0, spacing=-1, owner=None):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(6 if spacing < 0 else spacing)
        self._items = []
        self._owner = owner

    def setOwner(self, widget):
        """Explicitly bind the FlowLayout to the widget whose width drives
        wrapping (recommended for nested layouts, where parentWidget() can be
        ambiguous before the first layout pass)."""
        self._owner = widget

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        """Report the height needed to lay the items out in `width` pixels.

        `width` is the usable (content-margin-excluded) area width; item rows
        wrap inside it, so a narrower `width` yields a taller result. This is
        what drives Qt to grow the outer scroll view vertically instead of
        horizontally when the widgets wrap.
        """
        return self._do_layout(QRect(0, 0, max(0, width), 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        m = self.contentsMargins()
        self._do_layout(
            rect.adjusted(+m.left(), +m.top(), -m.right(), -m.bottom()), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size.setWidth(size.width() + m.left() + m.right())
        size.setHeight(size.height() + m.top() + m.bottom())
        return size

    def _avail(self):
        if self._owner is not None:
            return self._owner.width()
        if self.parentWidget() is not None:
            return self.parentWidget().width()
        return 0

    def _do_layout(self, effective, test_only):
        # `effective` is already the margin-excluded usable content rect.
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            w = hint.width()
            h = hint.height()
            if w <= 0 or h <= 0:
                if not test_only:
                    item.setGeometry(effective)
                continue
            if x + w > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + self.spacing()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = x + w + self.spacing()
            line_height = max(line_height, h)

        return y + line_height - effective.y() + self.contentsMargins().bottom()