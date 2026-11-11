"""Подписи хостов в списке: текст под значком.

Поля и их порядок задаются в настройках (вкладка «Отображение»,
``config.app.display``): имя, IP-адрес, имя компьютера и вошедший
пользователь. Текст собирается в одну строку через дефис
(«pc1 - 10.0.0.1 - teacher»). Плитку рисует стандартный делегат Qt
(вид как без подписей): переносит текст по ширине и обрезает лишнее.
Пользователь и имя компьютера берутся из кэша ``HostInfoService``;
сбор ведёт ``DeviceListView``, делегат только подставляет текст.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QModelIndex, QSize
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

# Текстовые поля подписи.
TEXT_FIELDS = ("name", "host", "hostname", "user")
# Разделитель полей в тексте подписи.
SEPARATOR = " - "
NO_USER_TEXT = "нет входа"
PENDING_TEXT = "…"


def caption_line_count(fields: Iterable[str]) -> int:
    """Сколько строк отведено под текст: по одной на поле (текст через
    дефис переносится в них)."""
    chosen = set(fields or ())
    return sum(1 for key in TEXT_FIELDS if key in chosen)


def _collected_value(device, snapshot, fetching: bool):
    """Общее для полей из сведений хоста: None - снимок есть, иначе текст.

    Хост не в сети - пусто (значение неизвестно); данных ещё нет -
    многоточие, пока идёт запрос.
    """
    if device is None or not getattr(device, "is_online", False):
        return ""
    if snapshot is None:
        return PENDING_TEXT if fetching else ""
    return None


def user_caption(device, snapshot, fetching: bool, user_mode: str = "login") -> str:
    """Текст пользователя для подписи.

    Данные есть, но сеанса нет - «нет входа». user_mode: ``login`` -
    учётная запись, ``full`` - полное имя (GECOS), ``both`` - «логин (имя)».
    Без полного имени (не задано на хосте или старая нода не передаёт)
    показывается учётная запись.
    """
    early = _collected_value(device, snapshot, fetching)
    if early is not None:
        return early
    user = (getattr(snapshot, "user", "") or "").strip()
    if not user:
        return NO_USER_TEXT
    full = (getattr(snapshot, "user_full", "") or "").strip()
    if not full or full == user or user_mode == "login":
        return user
    if user_mode == "full":
        return full
    return f"{user} ({full})"


def hostname_caption(device, snapshot, fetching: bool) -> str:
    """Имя компьютера из сведений (вывод hostname на хосте)."""
    early = _collected_value(device, snapshot, fetching)
    if early is not None:
        return early
    return (getattr(snapshot, "hostname", "") or "").strip()


def caption_text(
    device,
    fields: Iterable[str],
    snapshot=None,
    fetching=False,
    user_mode: str = "login",
) -> str:
    """Текст подписи: текстовые поля в порядке ``fields`` через дефис.

    Пустые части (пользователь хоста не в сети) пропускаются вместе
    с разделителем.
    """
    parts = []
    seen = set()
    for key in fields or ():
        if key not in TEXT_FIELDS or key in seen:
            continue
        seen.add(key)
        if key == "name":
            value = getattr(device, "name", "") or ""
        elif key == "host":
            value = getattr(device, "host", "") or ""
        elif key == "hostname":
            value = hostname_caption(device, snapshot, fetching)
        else:
            value = user_caption(device, snapshot, fetching, user_mode)
        value = value.strip()
        if value:
            parts.append(value)
    return SEPARATOR.join(parts)


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
    """Стандартная плитка Qt, текст которой собран из полей подписи.

    Делегат на каждый вид свой (Qt не рекомендует делить делегат между
    видами), поля задаёт ``DeviceListView`` через ``set_fields``.
    """

    def __init__(
        self,
        parent=None,
        fields: Iterable[str] = ("name",),
        user_mode: str = "login",
    ):
        super().__init__(parent)
        self._fields: tuple[str, ...] = tuple(fields or ())
        self._user_mode = user_mode

    # --------- настройки ----------
    def fields(self) -> tuple[str, ...]:
        return self._fields

    def set_fields(self, fields: Iterable[str]) -> None:
        self._fields = tuple(fields or ())

    def user_mode(self) -> str:
        return self._user_mode

    def set_user_mode(self, mode: str) -> None:
        """Как показывать пользователя: login, full или both."""
        self._user_mode = mode

    # --------- текст ----------
    def caption_for(self, device) -> str:
        """Текст подписи устройства по текущим полям."""
        snapshot, fetching = self._snapshot(device)
        return caption_text(device, self._fields, snapshot, fetching, self._user_mode)

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        item = item_for_index(index)
        device = getattr(item, "device", None) if item is not None else None
        if device is not None:
            option.text = self.caption_for(device)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        # Плитка фиксированного размера: сетку задаёт DeviceListView по числу
        # полей, иначе длинный текст растягивал бы её.
        view = self.parent()
        grid = view.gridSize() if view is not None else QSize()
        if grid.isValid():
            return grid
        return super().sizeHint(option, index)

    def _snapshot(self, device):
        """Снимок HostInfoService и признак идущего запроса."""
        if "user" not in self._fields and "hostname" not in self._fields:
            return None, False
        try:
            from src.services.hosts.host_info_service import get_host_info_service

            svc = get_host_info_service()
            iid = getattr(device, "iid", None) or ""
            niid = getattr(device, "node_iid", None)
            return svc.get_info(iid, niid), svc.is_fetching(iid, niid)
        except Exception:
            return None, False


def find_caption_delegate(view) -> Optional[HostCaptionDelegate]:
    """Делегат подписей вида, если он установлен."""
    delegate = view.itemDelegate() if view is not None else None
    return delegate if isinstance(delegate, HostCaptionDelegate) else None
