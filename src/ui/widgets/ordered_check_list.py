"""Столбец обычных галочек с ручным порядком (перетаскивание за ручку).

Используется для порядка полей подписи хостов (вкладка «Отображение»
настроек). Строка - ручка «⠿», обычный ``QCheckBox`` (вид как у остальных
галочек окна) и, при необходимости, виджет справа (выбор режима у
«Вошедший пользователь»). Порядок меняется перетаскиванием за ручку:
строка переезжает сразу, пока кнопка мыши нажата.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

HANDLE_SIZE = 16


def drag_handle_icon(color: QColor, size: int = HANDLE_SIZE) -> QIcon:
    """Значок-ручка «⠿»: две колонки по три точки цветом текста палитры.

    Рисуется, а не берётся из темы иконок: цвет должен совпадать с текстом
    в любой теме оформления.
    """
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = size / 12
        for column in (size * 0.36, size * 0.64):
            for row in (size * 0.25, size * 0.5, size * 0.75):
                painter.drawEllipse(QPointF(column, row), radius, radius)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class _Handle(QLabel):
    """Ручка строки: нажатие начинает перетаскивание, движение двигает строку."""

    def __init__(self, row: "_Row"):
        super().__init__(row)
        self._row = row
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Перетащите, чтобы изменить порядок")
        color = self.palette().color(QPalette.ColorRole.WindowText)
        self.setPixmap(drag_handle_icon(color).pixmap(HANDLE_SIZE, HANDLE_SIZE))
        self.setFixedWidth(HANDLE_SIZE + 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._row.owner._begin_drag(self._row)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            owner = self._row.owner
            owner._drag_to(owner.mapFromGlobal(event.globalPosition().toPoint()).y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._row.owner._end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _Row(QWidget):
    """Строка: ручка, галочка, место под виджет справа."""

    def __init__(self, owner: "OrderedCheckList", key: str, label: str, checked: bool):
        super().__init__(owner)
        self.owner = owner
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.handle = _Handle(self)
        self.check = QCheckBox(label, self)
        self.check.setChecked(checked)
        self.check.toggled.connect(owner._on_toggled)
        layout.addWidget(self.handle)
        layout.addWidget(self.check)
        self.extra = None
        layout.addStretch(1)

    def set_extra(self, widget: QWidget) -> None:
        layout = self.layout()
        if self.extra is not None:
            layout.removeWidget(self.extra)
        self.extra = widget
        widget.setParent(self)
        # Сразу после галочки, перед растяжкой.
        layout.insertWidget(2, widget)
        widget.show()


class OrderedCheckList(QWidget):
    """Галочки в столбец, порядок меняется перетаскиванием за ручку."""

    # Сменилась отметка или порядок.
    itemChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._rows: list[_Row] = []
        self._dragging: _Row | None = None

    # --------- заполнение ----------
    def set_rows(self, rows: Iterable[tuple[str, str, bool]], tooltips=None) -> None:
        """rows: (ключ, подпись, отмечено) в нужном порядке."""
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        tooltips = tooltips or {}
        for key, label, checked in rows:
            row = _Row(self, key, label, checked)
            if key in tooltips:
                row.check.setToolTip(tooltips[key])
            self._rows.append(row)
            self._layout.addWidget(row)

    def _row(self, key: str):
        for row in self._rows:
            if row.key == key:
                return row
        return None

    # --------- состояние ----------
    def order(self) -> list[str]:
        """Ключи всех строк в текущем порядке."""
        return [row.key for row in self._rows]

    def checked(self) -> list[str]:
        """Отмеченные ключи в текущем порядке."""
        return [row.key for row in self._rows if row.check.isChecked()]

    def is_checked(self, key: str) -> bool:
        row = self._row(key)
        return row is not None and row.check.isChecked()

    def set_checked(self, key: str, checked: bool) -> None:
        row = self._row(key)
        if row is not None:
            row.check.setChecked(checked)

    def set_checkable(self, key: str, enabled: bool) -> None:
        """Разрешить или запретить менять галочку строки; порядок меняется
        всегда, отметка сохраняется."""
        row = self._row(key)
        if row is not None:
            row.check.setEnabled(enabled)

    def is_checkable(self, key: str) -> bool:
        row = self._row(key)
        return bool(row and row.check.isEnabled())

    def check_box(self, key: str):
        row = self._row(key)
        return row.check if row is not None else None

    def handle(self, key: str):
        row = self._row(key)
        return row.handle if row is not None else None

    def set_row_widget(self, key: str, widget: QWidget) -> None:
        """Поставить виджет сразу после галочки строки ``key``."""
        row = self._row(key)
        if row is not None:
            row.set_extra(widget)

    def row_widget(self, key: str):
        row = self._row(key)
        return row.extra if row is not None else None

    def move_row(self, key: str, index: int) -> None:
        """Переместить строку на позицию ``index``."""
        row = self._row(key)
        if row is None:
            return
        index = max(0, min(index, len(self._rows) - 1))
        if self._rows.index(row) == index:
            return
        self._rows.remove(row)
        self._rows.insert(index, row)
        self._layout.removeWidget(row)
        self._layout.insertWidget(index, row)
        self.itemChanged.emit()

    # --------- перетаскивание ----------
    def _begin_drag(self, row: _Row) -> None:
        self._dragging = row

    def _drag_to(self, y: int) -> None:
        """Строка встаёт туда, где курсор прошёл середину соседней строки."""
        row = self._dragging
        if row is None:
            return
        current = self._rows.index(row)
        target = current
        for index, other in enumerate(self._rows):
            if other is row:
                continue
            middle = other.geometry().center().y()
            if index < current and y < middle:
                target = min(target, index)
            elif index > current and y > middle:
                target = max(target, index)
        self.move_row(row.key, target)

    def _end_drag(self) -> None:
        self._dragging = None

    def _on_toggled(self, _checked: bool) -> None:
        self.itemChanged.emit()
