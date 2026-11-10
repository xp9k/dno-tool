"""Компактный отчёт удалённой установки (как в окне диагностики).

Подробности выбранного хоста: сначала проблемы одной строкой, полный вывод
сервиса открывается ссылкой; успешные сервисы свёрнуты. Функции без состояния
страницы: она передаёт коды, выводы и раскрытые записи.
"""

import html

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QTextBrowser

from src.ui.icons import _icon_from_key
from src.workers.setup.types import (
    RESULT_ABORT,
    RESULT_ERROR,
    RESULT_IGNORE,
    RESULT_SUCCESS,
    RESULT_WAITING,
)

ICON_SIZE = 16
STATUS_ICONS = {
    RESULT_SUCCESS: "result_success",
    RESULT_ERROR: "result_failure",
    RESULT_IGNORE: "result_skip",
    RESULT_ABORT: "result_cancelled",
    RESULT_WAITING: "result_pending",
}
STATUS_TEXTS = {
    RESULT_SUCCESS: "Успех",
    RESULT_ERROR: "Неудача",
    RESULT_IGNORE: "Пропуск",
    RESULT_ABORT: "Отмена",
    RESULT_WAITING: "Ожидает готовности",
}
PROBLEMS = (RESULT_ERROR, RESULT_ABORT)
DONE = (RESULT_SUCCESS, RESULT_IGNORE)
SHORT_LIMIT = 160


class ReportView(QTextBrowser):
    """Подробности; значки ``icon:<ключ>`` берутся из темы иконок."""

    def loadResource(self, kind, url):
        if url.scheme() == "icon":
            return _icon_from_key(url.path()).pixmap(
                QSize(ICON_SIZE, ICON_SIZE), self.devicePixelRatioF()
            )
        return super().loadResource(kind, url)


def short_line(output, code) -> str:
    """Одна строка вывода: причина ошибки обычно в конце, итог успеха - в начале."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[-1] if code in PROBLEMS else lines[0]
    return line if len(line) <= SHORT_LIMIT else line[: SHORT_LIMIT - 1] + "…"


def host_status(codes) -> tuple:
    """(текст, значок) итога хоста по кодам сервисов (None - ожидание)."""
    values = list(codes)
    if any(code == RESULT_ERROR for code in values):
        return "Ошибки", "result_failure"
    if any(code == RESULT_ABORT for code in values):
        return "Отменено", "result_cancelled"
    if not values or any(code not in DONE for code in values):
        if any(code in DONE for code in values):
            return "Выполняется", "result_executing"
        return "Ожидание", "result_pending"
    return "Успешно", "result_success"


def _icon(key) -> str:
    return f'<img src="icon:{html.escape(key)}" width="{ICON_SIZE}" height="{ICON_SIZE}">'


def _entry(service, label, code, output, expanded) -> str:
    status = STATUS_TEXTS.get(code, "ожидание") if code is not None else "ожидание"
    short = short_line(output, code)
    parts = [
        f'<a name="{html.escape(service)}"></a>'
        '<p style="margin-top:6px; margin-bottom:0">'
        f"{_icon(STATUS_ICONS.get(code, 'result_pending'))} <b>{html.escape(label)}</b>"
        f" — {html.escape(status)}"
        + (f" · {html.escape(short)}" if short else "")
    ]
    full = (output or "").strip()
    if full and full != short:
        link = "скрыть" if service in expanded else "подробнее"
        parts.append(f' <a href="toggle:{html.escape(service)}">{link}</a>')
    parts.append("</p>")
    if full and full != short and service in expanded:
        parts.append(
            '<p style="margin-left:22px; margin-top:2px; margin-bottom:0;'
            f' white-space:pre-wrap; font-family:monospace">{html.escape(full)}</p>'
        )
    return "".join(parts)


def details_html(name, host, services, codes, outputs, info, expanded, show_successes) -> str:
    """HTML подробностей хоста.

    services - [(ключ, подпись)] в порядке установки; codes/outputs - по ключу;
    info - сообщения о подключении; expanded - раскрытые ключи (и "_info").
    """
    status, icon = host_status(codes.get(key) for key, _ in services)
    address = (
        f' <span style="color:gray">{html.escape(host)}</span>' if host and host != name else ""
    )
    parts = [f"<p>{_icon(icon)} <b>{html.escape(name)}</b>{address} — {status}</p>"]
    problems, waiting, done = [], [], []
    for key, label in services:
        code = codes.get(key)
        bucket = problems if code in PROBLEMS else done if code in DONE else waiting
        bucket.append((key, label, code))
    for key, label, code in problems + waiting:
        parts.append(_entry(key, label, code, outputs.get(key), expanded))
    if done:
        if problems or waiting:
            link = "свернуть" if show_successes else "показать"
            parts.append(
                '<p style="margin-top:8px; margin-bottom:0">'
                f"{_icon('result_success')} Выполнено: {len(done)} — "
                f'<a href="toggle:successes">{link}</a></p>'
            )
        if show_successes or not (problems or waiting):
            for key, label, code in done:
                parts.append(_entry(key, label, code, outputs.get(key), expanded))
    footer = []
    if info:
        link = "скрыть" if "_info" in expanded else "показать"
        footer.append(f'Подключение: <a href="toggle:_info">{link}</a>')
    footer.append('<a href="log:open">полный журнал хоста</a>')
    parts.append(
        '<p style="margin-top:10px; color:gray">' + " · ".join(footer) + "</p>"
    )
    if info and "_info" in expanded:
        parts.append(
            '<p style="margin-left:22px; margin-top:0; white-space:pre-wrap;'
            f' font-family:monospace">{html.escape(chr(10).join(info))}</p>'
        )
    return "".join(parts)


def text_report(hosts, services) -> str:
    """Текстовый отчёт: hosts - [(имя, адрес, коды, выводы, info)]."""
    lines = []
    for name, host, codes, outputs, info in hosts:
        status, _ = host_status(codes.get(key) for key, _ in services)
        lines += ["", f"{name} ({host}) — {status}"]
        for key, label in services:
            code = codes.get(key)
            lines.append(
                f"[{label}] {STATUS_TEXTS.get(code, 'ожидание') if code is not None else 'ожидание'}"
            )
            if outputs.get(key):
                lines.append(outputs[key].strip())
        if info:
            lines.append("[Подключение]")
            lines.extend(info)
    return "\n".join(lines).strip() + "\n"
