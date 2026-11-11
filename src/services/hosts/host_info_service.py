"""Host Info Service — фоновый сбор данных хоста для живых тултипов.

Сбор запускает каждый пинг хоста «в сети» при настройке «Запрашивать
сведения при пинге» (DeviceListView); наведение мыши только показывает
кэш. Живые поля в подсказке - при «Информации о хосте», состав
выбирается кнопкой тулбара. Сервис выполняет одну
короткую SSH-команду (batch-скрипт) и кэширует результат с TTL. Тултип собирается из
кэша + статических полей устройства (Имя/Хост/Порт/Нода — выбор в меню
тулбара ViewToolbar). Офлайн-хосты не опрашиваются.

Node-хосты удалённых нод (device.node_iid ≠ "local") обслуживаются тем же
сервисом: вместо SSH выполняется GET /api/node/host_info/{iid} через
NodeClientWorker ноды-владельца (client.host_info); результат — dict полей,
конвертируется в HostInfoSnapshot. Оба пути разделяют кэш/TTL/dedup и
сигнал info_updated — представления перерисовывают тултип единообразно.

Синглтон — через фабрику get_host_info_service() (без __new__: PySide6
падает на Linux, если QObject-наследник переопределяет __new__).
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QObject, Signal

from src.config import config
from src.domain.models.device import DeviceModel
from src.domain.device_identity import DeviceKey, device_identity, device_key
from src.logger import logger

# TTL кэша данных хоста (секунды). По истечении — повторный запрос при наведении.
INFO_TTL_SEC = 60

# iid локальной ноды (дублирует device_tree.LOCAL_NODE_IID: импорт UI-модуля
# из сервиса недопустим — Qt-цикл импортов).
_LOCAL_NODE_IID = "local"

# Статические поля тултипа (из DeviceModel, без опроса). Порядок = порядок
# в тултипе и в меню тулбара. «Нода» применима только к хостам удалённых нод.
STATIC_TOOLTIP_FIELDS = [
    ("name", "Имя"),
    ("host", "Хост"),
    ("port", "Порт"),
    ("node", "Нода"),
]

# Живые поля (сбор по SSH локально или через ноду — HostInfo API).
# Ключи сохраняются в view_state. Порядок = порядок в тултипе.
TOOLTIP_FIELDS = [
    ("user", "Текущий пользователь"),
    ("user_full", "Полное имя пользователя"),
    ("hostname", "Имя компьютера"),
    ("login_time", "Время входа"),
    ("uptime", "Время работы системы"),
    ("boot_time", "Время загрузки"),
    ("os", "Операционная система"),
    ("load", "Нагрузка CPU"),
    ("memory", "Память"),
    ("sessions", "Активных сеансов"),
    ("vnc", "VNC-сервер"),
]

# Полное меню тулбара «Информация в тултипе хоста»: статика + живые поля
# (разделитель между группами рисует ViewToolbar).
TOOLTIP_MENU_FIELDS = STATIC_TOOLTIP_FIELDS + TOOLTIP_FIELDS

_STATIC_KEYS = {key for key, _label in STATIC_TOOLTIP_FIELDS}

DEFAULT_FIELDS = ["name", "host", "port", "node", "user", "login_time", "uptime", "vnc"]

# Метки полей (для build_tooltip) — статические + живые.
_FIELD_LABELS = dict(TOOLTIP_MENU_FIELDS)


@dataclass
class HostInfoSnapshot:
    """Данные хоста, собранные одной SSH-командой."""

    user: str = ""
    user_full: str = ""  # GECOS (поле 5 passwd до запятой) для user
    hostname: str = ""  # вывод hostname на хосте (может отличаться от имени в списке)
    login_time: str = ""
    uptime: str = ""
    boot_time: str = ""
    os: str = ""
    load: str = ""
    memory: str = ""
    sessions: str = ""
    vnc: str = ""
    fetched_at: float = 0.0

    def field(self, key: str) -> str:
        return getattr(self, key, "") or ""

    def is_expired(self, max_age: float | None = None) -> bool:
        limit = INFO_TTL_SEC if max_age is None else max_age
        return (time.time() - self.fetched_at) > limit


def snapshot_from_fields(fields: dict) -> HostInfoSnapshot:
    """dict полей (формат /api/node/host_info) → HostInfoSnapshot.

    Ключи вне TOOLTIP_FIELDS игнорируются; значения приводятся к str.
    """
    snap = HostInfoSnapshot(fetched_at=time.time())
    for key, value in (fields or {}).items():
        if key in _FIELD_LABELS and key not in _STATIC_KEYS:
            try:
                setattr(snap, key, "" if value is None else str(value))
            except Exception:
                pass
    return snap


# Batch-скрипт: один SSH-вызов собирает все поля. Каждая строка данных
# помечена тегом "DNO|TAG|" и валидируется по типу при парсинге — смешивание
# полей (сдвиг позиционных блоков, пустые блоки, эхо/промпт PTY) невозможно.
# PTY SSHWorker эхом повторяет команду и промпт — такие строки не содержат
# тега и отбрасываются автоматически.
_DNO_TAG = "DNO|"

# Каждое поле — отдельный echo с тегом; тег всегда в НАЧАЛЕ строки вывода.
# PTY-эхо команды начинается с промпта (тег не в начале строки) → отсекается.
# Перенос длинного эха по ширине терминала (PTY default 80 колонок) может
# уронить продолжение на начало строки, поэтому литерал тега в команде
# отсутствует: он собирается shell-переменной $D (эхо печатает '$D|WHO|...' —
# строка никогда не начинается с 'DNO|').
_HOST_INFO_SCRIPT = (
    "D=DNO; "
    "echo \"$D|WHO|$(who 2>/dev/null | head -20 | tr '\\n' '~')\"; "
    'echo "$D|UPT|$(cat /proc/uptime 2>/dev/null)"; '
    'echo "$D|BOOT|$(uptime -s 2>/dev/null)"; '
    'echo "$D|OS|$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"; '
    'echo "$D|HOSTNAME|$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null)"; '
    'echo "$D|LOAD|$(cat /proc/loadavg 2>/dev/null)"; '
    "echo \"$D|MEM|$(free -m 2>/dev/null | awk '/^Mem:/{print $2, $3}')\"; "
    # VNC-сервер: тот же порядок, что у KRFBService._host_server — включённая
    # служба kmsvnc, затем x11vnc, затем KRFB (служба мастера krfb-dno или
    # процесс krfb; бинарник KRFB есть в МОС всегда и признаком не считается).
    # Строка: «имя состояние [версия]», без сервера — «none».
    "V=; for s in kmsvnc x11vnc; do "
    '[ "$(systemctl is-enabled $s.service 2>/dev/null)" = enabled ] && '
    '{ V="$s $(systemctl is-active $s.service 2>/dev/null) '
    "$(rpm -q --qf '%{VERSION}-%{RELEASE}' $s 2>/dev/null | grep -E '^[0-9]')\"; break; }; "
    "done; "
    'if [ -z "$V" ]; then K=; '
    "systemctl --global is-enabled krfb-dno.service 2>/dev/null | grep -qx enabled && K=1; "
    "ls /home/*/.config/systemd/user/*.wants/krfb-dno.service >/dev/null 2>&1 && K=1; "
    "if pgrep -x krfb >/dev/null 2>&1; then V='krfb active'; "
    "elif [ -n \"$K\" ]; then V='krfb inactive'; fi; fi; "
    'echo "$D|VNC|${V:-none}"; '
    # Полные имена (GECOS до первой запятой) всех вошедших: «логин:имя~...»;
    # нужный логин выбирает разбор (_pick_user_session).
    "echo \"$D|GECOS|$(for u in $(who 2>/dev/null | awk '{print $1}' | sort -u); do "
    "printf '%s:%s~' \"$u\" \"$(getent passwd \"$u\" 2>/dev/null | cut -d: -f5 | cut -d, -f1)\"; "
    'done)"'
)


def _format_uptime(seconds: float) -> str:
    """Секунды → 'N д N ч N мин' (компактно, без нулевых компонентов)."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


# Удалённый сеанс who: псевдотерминал и адрес в скобках, не номер дисплея
# («root pts/0 2026-11-11 10:00 (192.168.1.5)»). Сюда же попадает сеанс самого
# опроса: SSHWorker запрашивает PTY, и sshd пишет его в utmp.
_REMOTE_SESSION_RE = re.compile(r"^\S+\s+pts/\S+.*\((?!:)[^)]*\)\s*$")


def _pick_user_session(sessions: List[str]) -> str:
    """Сеанс для поля «Текущий пользователь»: первый локальный (графический
    или консольный); только сеансы по SSH - пусто (за компьютером никого).

    Сеанс самого опроса всегда есть в who (SSHWorker с PTY), поэтому
    вход по SSH пользователем за компьютером не считается: иначе у хоста
    без входа показывался бы логин связки опроса (проверено на МОС).
    """
    for line in sessions:
        if not _REMOTE_SESSION_RE.match(line):
            return line
    return ""


def _parse_host_info_script(output: str) -> HostInfoSnapshot:
    """Разобрать tagged-вывод batch-скрипта.

    Каждая строка данных имеет вид ``DNO|TAG|значение`` — парсится строго
    по тегу, позиционные предположения отсутствуют. Эхо команды и промпт
    шелла (PTY) не содержат тега и игнорируются. Повторные строки с тем же
    тегом игнорируются (берётся первая). Смешивание полей невозможно.
    """
    snap = HostInfoSnapshot(fetched_at=time.time())
    seen = set()
    gecos: Dict[str, str] = {}
    # Тег обязан быть в НАЧАЛЕ строки — эхо команды (промпт + весь скрипт)
    # не начинается с тега и отсекается этим условием.
    tag_re = re.compile(r"^DNO\|([A-Z]+)\|(.*)")

    for raw_line in output.splitlines():
        line = raw_line.strip()
        m = tag_re.match(line)
        if not m:
            continue  # эхо команды / промпт / служебный вывод
        tag, payload = m.group(1), m.group(2).strip()
        if tag in seen:
            continue
        seen.add(tag)

        if tag == "WHO":
            # payload: строки сеансов, склеенные '~' (tr '\n' '~').
            # Эхо-фрагменты (содержат 'who |' или 'head -') отбрасываем.
            sessions = [
                s.strip()
                for s in payload.split("~")
                if s.strip() and "who" not in s and "head" not in s
            ]
            if sessions:
                parts = _pick_user_session(sessions).split()
                if parts:
                    snap.user = parts[0]
                if len(parts) >= 4:
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", parts[2]):
                        snap.login_time = _format_login_time_iso(parts[2], parts[3])
                    elif len(parts) >= 5 and re.match(r"^\w{3}$", parts[2]):
                        snap.login_time = f"{parts[3]} {parts[2]} {parts[4]}"
                snap.sessions = str(len(sessions))
        elif tag == "UPT":
            nums = payload.split()
            if nums:
                snap.uptime = _format_uptime(nums[0])
        elif tag == "BOOT":
            snap.boot_time = _format_boot_time(payload)
        elif tag == "OS":
            snap.os = payload.strip().strip('"')
        elif tag == "HOSTNAME":
            name = payload.strip()
            # Имя узла без пробелов; эхо и мусор PTY отбрасываются.
            if name and " " not in name and len(name) <= 253:
                snap.hostname = name
        elif tag == "LOAD":
            load_parts = payload.split()
            if len(load_parts) >= 3:
                snap.load = f"{load_parts[0]} / {load_parts[1]} / {load_parts[2]}"
        elif tag == "MEM":
            mem_parts = payload.split()
            if len(mem_parts) >= 2:
                try:
                    total_mb = int(mem_parts[0])
                    used_mb = int(mem_parts[1])
                    if total_mb > 0:
                        snap.memory = f"{used_mb} / {total_mb} МБ"
                except ValueError:
                    pass
        elif tag == "VNC":
            snap.vnc = _format_vnc(payload)
        elif tag == "GECOS":
            gecos = _parse_gecos(payload)

    if snap.user:
        snap.user_full = gecos.get(snap.user, "")
    return snap


# Имя учётной записи Linux: отсекает эхо команды и мусор PTY в записях GECOS.
_LOGIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*\$?$")


def _parse_gecos(payload: str) -> Dict[str, str]:
    """«логин:полное имя~...» → {логин: полное имя}; пустые имена пропускаются."""
    result: Dict[str, str] = {}
    for entry in (payload or "").split("~"):
        login, sep, full = entry.partition(":")
        login, full = login.strip(), full.strip()
        if sep and full and _LOGIN_RE.match(login) and login not in result:
            result[login] = full
    return result


# Состояние службы (systemctl is-active) → текст подсказки.
_VNC_STATES = {
    "active": "работает",
    "inactive": "остановлен",
    "failed": "ошибка запуска",
    "activating": "запускается",
    "deactivating": "останавливается",
}
_VNC_NAMES = {"kmsvnc": "kmsvnc", "x11vnc": "x11vnc", "krfb": "KRFB"}


def _format_vnc(payload: str) -> str:
    """'kmsvnc active 0.0.6-11.2024…dno' → 'kmsvnc 0.0.6-11, работает'.

    'none' → 'не найден'. Незнакомый вывод (эхо, мусор) → пусто: поле не
    показывается, а не показывает неверное.
    """
    parts = (payload or "").split()
    if parts == ["none"]:
        return "не найден"
    if len(parts) < 2 or parts[0] not in _VNC_NAMES:
        return ""
    name = _VNC_NAMES[parts[0]]
    if len(parts) >= 3 and re.match(r"^\d[\w.+~-]*$", parts[2]):
        # VERSION-RELEASE без даты и коммита (как short_evr мастера): 0.0.6-11.
        version, _, release = parts[2].partition("-")
        build = release.split(".", 1)[0]
        name = f"{name} {version}-{build}" if build else f"{name} {version}"
    state = _VNC_STATES.get(parts[1])
    if state is None and re.match(r"^[a-z-]+$", parts[1]):
        state = parts[1]
    return f"{name}, {state}" if state else name


def _format_login_time_iso(day: str, time_str: str) -> str:
    """('2026-09-03', '10:24') → '03.09 10:24' — компактный формат."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", day or "")
    if m:
        return f"{m.group(3)}.{m.group(2)} {time_str}"
    return f"{day} {time_str}".strip()


def _format_boot_time(value: str) -> str:
    """'2026-09-03 08:15:00' → '03.09.2026 08:15' (без секунд); не-ISO — как есть."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2})", (value or "").strip())
    if m:
        year, month, day, hm = m.groups()
        return f"{day}.{month}.{year} {hm}"
    return (value or "").strip()


class HostInfoService(QObject):
    """Ленивый сбор данных хоста для тултипов (кэш + TTL + dedup).

    Локальные хосты - SSH batch-скрипт (служебный пул); node-хосты удалённых нод
    (device.node_iid задан и ≠ "local") — GET /api/node/host_info/{iid} через
    NodeClientWorker (SSH-опрос выполняет нода-владелец). Один кэш (iid →
    snapshot) для обоих путей; тултип всегда собирает build_tooltip.
    """

    # Данные хоста обновлены (host: str) — UI перерисовывает тултип.
    info_updated = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._cache: Dict[DeviceKey, HostInfoSnapshot] = {}  # (владелец, iid) → снимок
        self._in_flight: Set[DeviceKey] = set()
        self._lock = threading.Lock()
        self._enabled_fields: List[str] = list(DEFAULT_FIELDS)

    # ========== Конфигурация полей ==========

    def get_enabled_fields(self) -> List[str]:
        """Включённые ЖИВЫЕ поля (для /api/node/host_info на стороне ноды)."""
        return [f for f in self._enabled_fields if f not in _STATIC_KEYS]

    def set_enabled_fields(self, fields: List[str]) -> None:
        """Установить включённые поля (статические + живые; порядок сохраняется)."""
        static = [f for f in fields or [] if f in _STATIC_KEYS]
        live = [f for f in fields or [] if f not in _STATIC_KEYS]
        self._enabled_fields = static + live
        logger.debug(f"HostInfoService: enabled fields = {self._enabled_fields}")

    # ========== Кэш ==========

    def get_info(self, iid: str, node_iid: str | None = None) -> Optional[HostInfoSnapshot]:
        with self._lock:
            return self._cache.get(device_key(iid, node_iid))

    def invalidate(self, iid: str, node_iid: str | None = None) -> None:
        with self._lock:
            self._cache.pop(device_key(iid, node_iid), None)

    def is_fetching(self, iid: str, node_iid: str | None = None) -> bool:
        with self._lock:
            return device_key(iid, node_iid) in self._in_flight

    # ========== Запрос данных ==========

    def request_info(
        self,
        device: DeviceModel,
        *,
        background: bool = False,
        max_age: float | None = None,
    ) -> None:
        """Запросить данные хоста асинхронно (лениво, без блокировки GUI).

        Пропускает: дубли (in-flight), свежий кэш (TTL). Офлайн-хосты
        НЕ опрашиваются, если только запрос не пришёл от серверного пути
        (node API уже сделал собственный tcp_ping — force=True).
        Node-хосты удалённых нод маршрутизируются в _request_node_info
        (SSH-опрос невозможен — подсеть ноды недоступна оператору).

        background=True - периодический опрос подписей списка: пакетная
        задача пула (одна очередь batch_id на все хосты), отказ пишется
        в журнал на уровне DEBUG. max_age - допустимый возраст кэша вместо
        INFO_TTL_SEC.
        """
        if device is None:
            return
        iid = getattr(device, "iid", None)
        if not iid:
            return

        niid = getattr(device, "node_iid", None)
        if niid and niid != _LOCAL_NODE_IID:
            self._request_node_info(
                device, niid, background=background, max_age=max_age
            )
            return

        if not device.is_online:
            return

        self._start_local_fetch(
            device, source="application", background=background, max_age=max_age
        )

    @staticmethod
    def _task_metadata(host: str, kind: str, background: bool) -> dict:
        """Метаданные задачи пула: фоновый опрос - пакетный тип с общим batch_id."""
        if background:
            return {
                "host": host,
                "type": f"{kind}_poll",
                "batch_id": "host_captions",
            }
        return {"host": host, "type": kind}

    def _start_local_fetch(
        self,
        device: DeviceModel,
        *,
        source: str | None = None,
        background: bool = False,
        max_age: float | None = None,
    ) -> None:
        """Локальный SSH-сбор без гейта is_online (серверный путь: нода уже
        проверила доступность tcp_ping'ом — HostPingTimer у ноды пингует
        только отмеченные хосты, is_online неотмеченных всегда False)."""
        iid = device_identity(device)
        with self._lock:
            cached = self._cache.get(iid)
            if cached is not None and not cached.is_expired(max_age):
                return
            if iid in self._in_flight:
                return
            self._in_flight.add(iid)

        try:
            from src.workers.network import get_service_worker_pool
            from src.workers.task_metadata import operation_source

            pool = get_service_worker_pool()
            source = source or operation_source.get()
        except Exception as e:
            logger.error(f"HostInfoService: cannot get service pool: {e}")
            with self._lock:
                self._in_flight.discard(iid)
            return

        host = device.host

        def on_complete(task_id, result):
            self._on_fetched(iid, host, result)

        def on_error(task_id, error):
            self._on_fetch_failed(iid, host, error, background=background)

        metadata = self._task_metadata(host, "host_info", background)
        metadata["source"] = source
        try:
            pool.submit(
                self._fetch_sync,
                device,
                task_id=f"hostinfo_{host}_{int(time.time() * 1000)}",
                on_complete=on_complete,
                on_error=on_error,
                metadata=metadata,
            )
            logger.debug(f"HostInfoService: requested info for {host}")
        except Exception as e:
            logger.error(f"HostInfoService: submit failed for {host}: {e}")
            with self._lock:
                self._in_flight.discard(iid)

    def _request_node_info(
        self,
        device: DeviceModel,
        node_iid: str,
        *,
        background: bool = False,
        max_age: float | None = None,
    ) -> None:
        """Живые поля node-хоста через ноду-владельца (client.host_info).

        Тот же контракт кэша/TTL/dedup, что у локального пути. В on_complete
        (GUI-поток пула) — только запись в кэш и info_updated: Qt-виджеты
        из pool-потока не трогаются (тултип перерисуют подписчики
        info_updated: DeviceListView).

        Гейт is_online: нод-хост, не отмеченный галочкой в дереве оператора,
        не пингуется (статусы только для checked) — is_online может быть
        False при живом хосте. Если хост помечен офлайн, но нода доступна —
        всё равно пробуем (нода сама вернёт offline/no_data по факту).
        """
        with self._lock:
            cached = self._cache.get(device_identity(device))
            if cached is not None and not cached.is_expired(max_age):
                return
            if device_identity(device) in self._in_flight:
                return
            self._in_flight.add(device_identity(device))

        host = device.host
        try:
            from src.di import get_container
            from src.services import NodeService

            svc = get_container().resolve_optional(NodeService)
        except Exception:
            svc = None
        client = svc.get_client(node_iid) if svc is not None else None
        if client is None:
            # Нода недоступна/клиент не запущен — INFO-лог (причина «тултип
            # пуст» должна быть видна) + тихий отказ без виджета-обновлений.
            (logger.debug if background else logger.info)(
                f"HostInfoService: нода {node_iid} недоступна — "
                f"live-данные {host} не получены"
            )
            with self._lock:
                self._in_flight.discard(device_identity(device))
            return

        iid = device_identity(device)

        def on_complete(task_id, result):
            self._on_node_fetched(iid, host, result)

        def on_error(task_id, error):
            self._on_fetch_failed(iid, host, error, background=background)

        try:
            from src.workers.network import get_service_worker_pool

            pool = get_service_worker_pool()
            # Запрашиваем ВСЕ живые поля (кэш общий для обоих путей) —
            # смена выбора полей в меню тулбара отражается сразу
            # (build_tooltip фильтрует по enabled), не ждёт TTL кэша.
            # Нода возвращает только поддерживаемые ею ключи.
            live_fields = [key for key, _label in TOOLTIP_FIELDS]
            pool.submit(
                lambda: client.host_info(iid[1], live_fields),
                task_id=f"node_info_{iid}_{int(time.time() * 1000)}",
                on_complete=on_complete,
                on_error=on_error,
                metadata=self._task_metadata(host, "node_info", background),
            )
            logger.debug(f"HostInfoService: node info requested for {host}")
        except Exception as e:
            logger.debug(f"HostInfoService: node submit failed for {host}: {e}")
            with self._lock:
                self._in_flight.discard(iid)

    def _on_node_fetched(self, iid: DeviceKey, host: str, info) -> None:
        """Результат client.host_info (dict полей) → кэш + info_updated."""
        fields = (info or {}).get("fields") if isinstance(info, dict) else None
        with self._lock:
            self._in_flight.discard(iid)
            if fields:
                self._cache[iid] = snapshot_from_fields(fields)
        if not fields:
            # Нода вернула offline/no_data (ok=False → client вернёт None):
            # INFO на уровне оператора — иначе «тултип пуст» не диагностируется.
            reason = (info or {}).get("reason") if isinstance(info, dict) else "offline"
            logger.info(f"HostInfoService: нет live-данных {host}: {reason}")
        self.info_updated.emit(host)

    def _fetch_sync(self, device: DeviceModel) -> HostInfoSnapshot:
        """SSH-сбор данных (выполняется в потоке служебного пула)."""
        from src.workers.command.ssh import SSHWorker

        worker = SSHWorker()
        output, ok = worker.execute(device, _HOST_INFO_SCRIPT, timeout=10)
        if not ok and not output.strip():
            raise RuntimeError(output or "SSH failed")
        snap = _parse_host_info_script(output)
        # SSHWorker возвращает ok=False при ненулевом exit-коде (например,
        # если free отсутствует) — данные при этом всё равно пригодны.
        snap.fetched_at = time.time()
        return snap

    def _on_fetched(self, iid: DeviceKey, host: str, snap: HostInfoSnapshot) -> None:
        with self._lock:
            self._in_flight.discard(iid)
            if snap is not None:
                self._cache[iid] = snap
        self.info_updated.emit(host)

    def _on_fetch_failed(
        self, iid: DeviceKey, host: str, error: str, *, background: bool = False
    ) -> None:
        with self._lock:
            self._in_flight.discard(iid)
        # Фоновый опрос подписей повторяется каждый период: INFO засорял бы
        # журнал по каждому хосту, где SSH не проходит.
        (logger.debug if background else logger.info)(
            f"HostInfoService: сбор данных не удался для {host}: {error}"
        )
        # Эмитим пустой host — представления перерисуют тултип
        # (уйдёт приписка «данные недоступны» при следующем наведении).
        self.info_updated.emit("")

    # ========== Тултип ==========

    def build_tooltip(self, device: DeviceModel) -> str:
        """Собрать текст тултипа: статика + включённые поля из кэша.

        Единый путь для локальных и node-хостов. Статические поля (Имя/Хост/
        Порт/Нода) берутся из DeviceModel («Нода» — только для хостов
        удалённых нод). Живые поля — из кэша; для онлайн-хоста без данных
        приписка «(данные запрашиваются…)».
        """
        if device is None:
            return ""
        niid = getattr(device, "node_iid", None)
        is_remote_node = bool(niid) and niid != _LOCAL_NODE_IID

        static_values = {
            "name": device.name,
            "host": device.host,
            "port": device.port or config.app.ssh.port,
        }
        if is_remote_node:
            static_values["node"] = self._node_display_name(niid)

        iid = getattr(device, "iid", None) or ""
        lines = []
        for key in self._enabled_fields:
            if key not in _STATIC_KEYS:
                continue  # живые поля добавляются после статических
            val = static_values.get(key)
            if key == "node" and not is_remote_node:
                continue  # «Нода» только у хостов удалённых нод
            if val:
                lines.append(f"{_FIELD_LABELS.get(key, key)}: {val}")

        # Живые поля из кэша (после статических). Гейт is_online — только
        # для локальных хостов (пинг): у нод-хостов, не отмеченных галочкой,
        # is_online всегда False, но данные доступны через ноду.
        live_lines = []
        from src.config.settings import host_tooltip_info_enabled

        show_live = host_tooltip_info_enabled(getattr(config.app, "display", None))
        if show_live and (device.is_online or is_remote_node):
            live_keys = [f for f in self._enabled_fields if f not in _STATIC_KEYS]
            snap = self.get_info(iid, niid)
            if snap is not None:
                for key in live_keys:
                    val = snap.field(key)
                    if val:
                        live_lines.append(f"{_FIELD_LABELS.get(key, key)}: {val}")
            elif live_keys and self.is_fetching(iid, niid):
                live_lines.append("Данные запрашиваются…")

        return "\n".join(lines + live_lines)

    @staticmethod
    def _node_display_name(node_iid: str) -> str:
        """Имя ноды для тултипа (NodeService из DI; fallback — iid[:8])."""
        try:
            from src.di import get_container
            from src.services import NodeService

            svc = get_container().resolve_optional(NodeService)
            if svc is not None:
                return svc.get_display_name(node_iid)
        except Exception:
            pass
        return (node_iid or "")[:8]


# Фабрика синглтона (как get_host_ping_timer_manager)
_host_info_service: Optional[HostInfoService] = None
_host_info_service_lock = threading.Lock()


def get_host_info_service() -> HostInfoService:
    global _host_info_service
    if _host_info_service is None:
        with _host_info_service_lock:
            if _host_info_service is None:
                _host_info_service = HostInfoService()
    return _host_info_service


def reset_host_info_service() -> None:
    global _host_info_service
    _host_info_service = None
