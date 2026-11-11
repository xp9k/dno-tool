"""Подписи хостов в списке: значок и текст под ним.

Поля задаются в настройках (вкладка «Отображение», ``config.app.display``):
состояние (цветная точка перед текстом), имя, IP-адрес и вошедший
пользователь. Текст собирается в одну строку через дефис
(«pc1 - 10.0.0.1 - teacher») и переносится по ширине плитки. Пользователь берётся из кэша ``HostInfoService`` (тот же снимок,
что у подсказки); опрос ведёт ``DeviceListView``, делегат только рисует.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

# Текстовые поля подписи (без «Состояния»: оно рисуется точкой).
TEXT_FIELDS = ("name", "host", "user")
# Разделитель полей в тексте подписи.
SEPARATOR = " - "
ONLINE_COLOR = QColor(46, 160, 67)
OFFLINE_COLOR = QColor(140, 140, 140)
NO_USER_TEXT = "нет входа"
PENDING_TEXT = "…"
# Отступ сверху до значка и зазор между точкой состояния и текстом.
TOP_MARGIN = 2
DOT_GAP = 4


def caption_line_count(fields: Iterable[str]) -> int:
    """Сколько строк отведено под текст: по одной на текстовое поле (текст
    через дефис переносится в них), одно «Состояние» - строка с точкой."""
    chosen = set(fields or ())
    count = sum(1 for key in TEXT_FIELDS if key in chosen)
    if count == 0 and "status" in chosen:
        return 1
    return count


def user_caption(device, snapshot, fetching: bool, full_name: bool = False) -> str:
    """Текст пользователя для подписи.

    Хост не в сети - пусто (пользователь неизвестен). Данных ещё нет -
    многоточие, пока идёт запрос. Данные есть, но сеанса нет - «нет входа».
    full_name - полное имя (GECOS); если его нет (не задано на хосте или
    старая нода не передаёт), показывается учётная запись.
    """
    if device is None or not getattr(device, "is_online", False):
        return ""
    if snapshot is None:
        return PENDING_TEXT if fetching else ""
    user = (getattr(snapshot, "user", "") or "").strip()
    if not user:
        return NO_USER_TEXT
    if full_name:
        return (getattr(snapshot, "user_full", "") or "").strip() or user
    return user


def caption_text(
    device,
    fields: Iterable[str],
    snapshot=None,
    fetching=False,
    full_name: bool = False,
) -> str:
    """Текст подписи: выбранные поля в каноническом порядке через дефис.

    Пустые части (пользователь хоста не в сети) пропускаются вместе
    с разделителем.
    """
    chosen = set(fields or ())
    parts = []
    for key in TEXT_FIELDS:
        if key not in chosen:
            continue
        if key == "name":
            value = getattr(device, "name", "") or ""
        elif key == "host":
            value = getattr(device, "host", "") or ""
        else:
            value = user_caption(device, snapshot, fetching, full_name)
        value = value.strip()
        if value:
            parts.append(value)
    return SEPARATOR.join(parts)


def _fit_prefix(text: str, advance: Callable[[str], int], limit: int) -> int:
    """Длина начала строки для текущей строки подписи: по пробелу, если он
    помещается, иначе посимвольно (не меньше одного символа)."""
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if advance(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    space = text.rfind(" ", 0, low + 1)
    return space if space > 0 else low


def wrap_caption(
    text: str,
    advance: Callable[[str], int],
    first_width: int,
    width: int,
    max_lines: int,
) -> list[str]:
    """Разбить текст подписи на строки не длиннее ширины плитки.

    Первая строка уже остальных на точку состояния. Всё, что не поместилось
    в ``max_lines``, остаётся в последней строке: её обрезает многоточием
    отрисовка.
    """
    rest = " ".join((text or "").split())
    lines: list[str] = []
    if max_lines <= 0:
        return lines
    while rest:
        limit = max(1, first_width if not lines else width)
        if len(lines) == max_lines - 1 or advance(rest) <= limit:
            lines.append(rest)
            break
        cut = _fit_prefix(rest, advance, limit)
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return lines


def item_for_index(index: QModelIndex):
    """Элемент исходной модели по индексу вида (через прокси фильтра «в сети»)."""
    if not index.isValid():
        return None
    model = index.model()
    if model is not None and hasattr(model, "mapToSource"):
        index = model.mapToSource(index)
        model = model.sourceModel()
    if model is None or not hasattr(model, "itemFromIndex"):
        return None
    return model.itemFromIndex(index)


class HostCaptionDelegate(QStyledItemDelegate):
    """Рисует плитку хоста: фон выделения, значок или миниатюру и подпись.

    Делегат на каждый вид свой (Qt не рекомендует делить делегат между
    видами), поля и размер плитки задаёт ``DeviceListView`` через ``set_fields``.
    """

    def __init__(
        self,
        parent=None,
        fields: Iterable[str] = ("name",),
        full_name: bool = False,
    ):
        super().__init__(parent)
        self._fields: tuple[str, ...] = tuple(fields or ())
        self._full_name = bool(full_name)

    # --------- настройки ----------
    def fields(self) -> tuple[str, ...]:
        return self._fields

    def set_fields(self, fields: Iterable[str]) -> None:
        self._fields = tuple(fields or ())

    def full_name(self) -> bool:
        return self._full_name

    def set_full_name(self, enabled: bool) -> None:
        """Показывать полное имя пользователя вместо учётной записи."""
        self._full_name = bool(enabled)

    # --------- геометрия ----------
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        view = self.parent()
        grid = view.gridSize() if view is not None else QSize()
        if grid.isValid():
            return grid
        icon = option.decorationSize
        lines = caption_line_count(self._fields)
        return QSize(
            icon.width() + 8,
            icon.height() + lines * option.fontMetrics.height() + TOP_MARGIN * 2,
        )

    # --------- отрисовка ----------
    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        item = item_for_index(index)
        device = getattr(item, "device", None) if item is not None else None

        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_background(painter, opt)
            icon_rect = self._paint_icon(painter, opt)
            if device is not None:
                self._paint_caption(painter, opt, device, icon_rect.bottom() + 1)
        finally:
            painter.restore()

    @staticmethod
    def _paint_background(painter: QPainter, opt: QStyleOptionViewItem) -> None:
        """Выделение и наведение: полупрозрачная подложка на всю плитку.

        Значок не подкрашивается (QIcon.Selected), чтобы миниатюра экрана
        выделенного хоста оставалась читаемой.
        """
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if not (selected or hovered):
            return
        color = QColor(opt.palette.color(QPalette.ColorRole.Highlight))
        color.setAlpha(110 if selected else 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(opt.rect).adjusted(1, 1, -1, -1), 4, 4)

    @staticmethod
    def _paint_icon(painter: QPainter, opt: QStyleOptionViewItem) -> QRect:
        size = opt.decorationSize
        rect = opt.rect
        icon_rect = QRect(
            rect.left() + (rect.width() - size.width()) // 2,
            rect.top() + TOP_MARGIN,
            size.width(),
            size.height(),
        )
        icon: QIcon = opt.icon
        if not icon.isNull():
            mode = (
                QIcon.Mode.Normal
                if opt.state & QStyle.StateFlag.State_Enabled
                else QIcon.Mode.Disabled
            )
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, mode)
        return icon_rect

    def _snapshot(self, device):
        """Снимок HostInfoService и признак идущего запроса (для строки пользователя)."""
        if "user" not in self._fields:
            return None, False
        try:
            from src.services.hosts.host_info_service import get_host_info_service

            svc = get_host_info_service()
            iid = getattr(device, "iid", None) or ""
            niid = getattr(device, "node_iid", None)
            return svc.get_info(iid, niid), svc.is_fetching(iid, niid)
        except Exception:
            return None, False

    def _paint_caption(
        self, painter: QPainter, opt: QStyleOptionViewItem, device, top: int
    ) -> None:
        snapshot, fetching = self._snapshot(device)
        text = caption_text(
            device, self._fields, snapshot, fetching, self._full_name
        )
        show_status = "status" in self._fields
        if not text and not show_status:
            return

        fm = opt.fontMetrics
        line_h = fm.height()
        rect = opt.rect
        width = max(0, rect.width() - 4)
        dot = max(6, int(line_h * 0.45))
        dot_w = dot + DOT_GAP if (show_status and text) else 0

        if not text:
            # Только состояние (или пустой текст): точка по центру строки.
            self._paint_dot(
                painter,
                rect.left() + (rect.width() - dot) / 2,
                top + (line_h - dot) / 2,
                dot,
                device,
            )
            return

        max_lines = max(1, caption_line_count(self._fields))
        lines = wrap_caption(
            text, fm.horizontalAdvance, width - dot_w, width, max_lines
        )
        painter.setFont(opt.font)
        painter.setPen(QColor(opt.palette.color(QPalette.ColorRole.Text)))
        for row, line in enumerate(lines):
            y = top + row * line_h
            with_dot = row == 0 and dot_w > 0
            avail = width - (dot_w if with_dot else 0)
            shown = fm.elidedText(line, Qt.TextElideMode.ElideRight, max(0, avail))
            text_w = fm.horizontalAdvance(shown)
            total = text_w + (dot_w if with_dot else 0)
            x = rect.left() + (rect.width() - total) / 2
            if with_dot:
                self._paint_dot(painter, x, y + (line_h - dot) / 2, dot, device)
                painter.setPen(QColor(opt.palette.color(QPalette.ColorRole.Text)))
                x += dot_w
            painter.drawText(
                QRectF(x, y, text_w + 1, line_h),
                int(
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignVCenter
                    | Qt.TextFlag.TextDontClip
                ),
                shown,
            )

    @staticmethod
    def _paint_dot(painter: QPainter, x: float, y: float, size: int, device) -> None:
        online = bool(getattr(device, "is_online", False))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ONLINE_COLOR if online else OFFLINE_COLOR)
        radius = size / 2
        painter.drawEllipse(QPointF(x + radius, y + radius), radius, radius)


def find_caption_delegate(view) -> Optional[HostCaptionDelegate]:
    """Делегат подписей вида, если он установлен."""
    delegate = view.itemDelegate() if view is not None else None
    return delegate if isinstance(delegate, HostCaptionDelegate) else None
