"""KRFB Service - управление krfb (KDE Remote Frame Buffer) на хостах.

KRFB - штатный KDE RFB-сервер (security type 2, VNC Auth), альтернатива
x11vnc/Veyon. Сервис предоставляет:
- FEATURE_MAP (krfb_restart, krfb_password) - для GUI, веб и нод;
- change_password(host, password) / restart_service(host) - смена пароля
  и перезапуск VNC-сервера хоста. Сервер определяется по SSH
  (``_host_server``): включённая служба kmsvnc, затем x11vnc, затем KRFB
  (служба мастера krfb-dno или запущенный процесс krfb). kmsvnc и x11vnc -
  файл /etc/<сервер>/passwd в формате сервера и перезапуск системной
  службы; KRFB - kwriteconfig6/kwriteconfig5 + рестарт user unit /
  процесса krfb. Если на хосте нет ни одного из них, операция отказывает,
  а не запускает KRFB;
- open_viewer(device, mode, viewer) - открыть вьювер: Windows - браузер
  (vnc.html?backend=krfb); Linux - на выбор krdc или браузер (default:
  krdc, если установлен, иначе браузер).

Все операции через SSH (root/ключ или sudo-PTY из SSHWorker) - не требуют
локального Veyon. Настоящий пароль хранится в ~/.config/krfbrc пользователя
X-сессии (группа [Security], ключ unattendedPassword - тихий доступ без
подтверждения, наш рабочий путь; шифрование KStringHandler::obscure:
chr(0x1001F - ord(c))). В desktopPassword пишется случайный dummy: главное
окно KRFB показывает ЭТОТ пароль открытым текстом, и он не должен давать
тихое подключение (krfb принимает его только с диалогом подтверждения
на хосте).
"""

from typing import Optional, Tuple

from src.services.base import BaseService
from src.services.operation_errors import ScopedLastError
from src.logger import logger

# Формула KStringHandler::obscure (KDE Frameworks, kstringhandler.cpp):
# каждый символ заменяется на chr(0x1001F - ord(c)). Инволюция: дважды
# применённая obscure возвращает исходный пароль.
OBSCURE_BASE = 0x1001F


def local_kconfig_tool(name: str) -> Optional[str]:
    """Путь к утилите KConfig на этой машине: версия KF6, иначе KF5.

    ``name`` - ``kwriteconfig`` или ``kreadconfig``. В Plasma 6 пакета KF5
    может не быть, формат krfbrc у обеих версий одинаковый.
    """
    import shutil

    return shutil.which(f"{name}6") or shutil.which(f"{name}5")


# То же на хосте: shell-переменные KW/KR с путями к утилитам KConfig.
_KCONFIG_VARS = (
    "KW=$(command -v kwriteconfig6 || command -v kwriteconfig5) "
    "|| { echo 'kwriteconfig not found'; exit 4; }; "
    "KR=$(command -v kreadconfig6 || command -v kreadconfig5) "
    "|| { echo 'kreadconfig not found'; exit 4; }; "
)


def kstring_obscure(text: str) -> str:
    """KStringHandler::obscure - XOR-подобное шифрование пароля krfb.

    Значение записывается в ~/.config/krfbrc [Security] desktopPassword.
    UTF-16 и исключение для значений <= 0x21 соответствуют реализации KDE:
    https://github.com/KDE/kcoreaddons/blob/master/src/lib/text/kstringhandler.cpp
    """
    import struct
    raw = text.encode("utf-16-le")
    units = struct.unpack("<" + "H" * (len(raw) // 2), raw)
    transformed = [unit if unit <= 0x21 else OBSCURE_BASE - unit for unit in units]
    return struct.pack("<" + "H" * len(transformed), *transformed).decode("utf-16-le", "surrogatepass")


# Имя systemd user unit krfb (создаётся RemoteServiceInstallerWorker).
KRFB_UNIT_NAME = "app-org.kde.krfb"

# Ключи конфига krfbrc (krfb.kcfg 23.08): группы и ключи.
KRFB_GROUP_SECURITY = "Security"
KRFB_GROUP_TCP = "TCP"
KRFB_GROUP_MAINWINDOW = "MainWindow"
KRFB_KEY_START_MINIMIZED = "startMinimized"

# Shell-фрагмент для старта процесса от X-пользователя ($XU/$XUID set) БЕЗ
# PAM: setpriv (util-linux) с fallback на runuser. runuser проходит
# PAM-сессию и падает на учётках с лимитами (pam_limits maxlogins -
# школьные guest): "There were too many logins". Используется в
# restart_service для запуска krfb после pkill: строка `setsid <frag> krfb`
# ветка if/else сама применяет "$@" (krfb ...), </dev/null отсоединяет
# процесс от PTY.
_SETPREV_KRFB_START = (
    "if command -v setpriv >/dev/null 2>&1; then "
    'setpriv --reuid="$XUID" --regid="$(id -g "$XU")" --init-groups '
    'env PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" '
    'HOME="$(getent passwd "$XU" | cut -d: -f6)" '
    "XDG_RUNTIME_DIR=/run/user/$XUID DISPLAY=:0 "
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$XUID/bus "
    '"$@"; '
    "else "
    'runuser -u "$XU" -- env XDG_RUNTIME_DIR=/run/user/$XUID DISPLAY=:0 '
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$XUID/bus "
    '"$@"; fi'
)


class KRFBService(BaseService):
    """Управление krfb на удалённых хостах через SSH."""

    _last_error = ScopedLastError()
    _last_error_detail = ScopedLastError()

    # Карта токенов krfb-фичей → (метод, нужны_аргументы).
    # Используется GUI (MainWindow), веб-сервером (/api/veyon/feature) и
    # нод-сервером (маршрутизация по FEATURE_MAP).
    FEATURE_MAP = {
        "krfb_restart": ("restart_service", False),
        "krfb_password": ("change_password", True),  # args=(password,)
    }

    def __init__(self, event_bus: Optional[object] = None) -> None:
        super().__init__(event_bus)
        self._last_error = ""
        self._last_error_detail = ""

    def last_error(self) -> str:
        return self._last_error

    # ========== SSH-хелперы ==========

    def _exec_ssh(
        self, host: str, command_text: str, timeout: int = 20, device=None
    ) -> Tuple[str, bool]:
        """Выполнить SSH-команду (через SSHWorker, с sudo по требованию).

        DeviceModel берётся из аргумента ``device`` (приоритет) или
        резолвится по host через DeviceService. При
        should_use_sudo(device) и отсутствии `sudo` в тексте команда
        оборачивается в `sudo <cmd>` - SSHWorker подаёт пароль через
        PTY-expect.
        """
        try:
            from src.di import get_container
            from src.services import DeviceService
            from src.workers.command.ssh import SSHWorker
            from src.workers.command.sudo_utils import has_sudo, should_use_sudo

            if device is None:
                container = get_container()
                ds = container.resolve(DeviceService)
                device = ds.get_device(host)
            if device is None:
                self._last_error = f"Хост {host} не найден"
                self._logger.error(f"KRFBService: _exec_ssh - host {host} не найден")
                return ("host not found", False)

            if should_use_sudo(device) and not has_sudo(command_text):
                command_text = f"sudo {command_text}"

            worker = SSHWorker()
            output, ok = worker.execute(device, command_text, timeout=timeout)
            if not ok:
                self._logger.debug(
                    f"KRFBService: _exec_ssh failed for {host}: {output}"
                )
            return (output, ok)
        except Exception as e:
            self._last_error = str(e)
            self._logger.debug(f"KRFBService: _exec_ssh error for {host}: {e}")
            return (str(e), False)

    # ========== Утилиты ==========

    @staticmethod
    def generate_password(length: int = 12) -> str:
        """Сгенерировать случайный пароль (буквы + цифры)."""
        import secrets
        import string

        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def resolve_port(device=None) -> int:
        """Порт VNC: device.vnc_port (per-host) или общий config vnc_port."""
        if device is not None and getattr(device, "vnc_port", None):
            return int(device.vnc_port)
        from src.config import config

        return int(getattr(config.app.veyon, "vnc_port", 5900) or 5900)

    # ========== Локальная машина оператора ==========

    @staticmethod
    def local_password_supported() -> bool:
        """True, если на машине оператора есть krfb (kwriteconfig6/5)."""
        return local_kconfig_tool("kwriteconfig") is not None

    @staticmethod
    def apply_password_local(password: str) -> Tuple[bool, str]:
        """Применить пароль krfb ЛОКАЛЬНО на машине оператора.

        krfb на машине оператора - тоже клиент вьювера/цель (когда
        оператор управляет собственной машиной в классе). Настоящий пароль
        пишется в ~/.config/krfbrc ([Security] unattendedPassword), в
        desktopPassword - случайный dummy (окно KRFB показывает его открытым
        текстом). Затем перезапуск krfb (user unit / pkill как на хостах).
        Не требует elevation: конфиг в home пользователя.

        Returns:
            (ok, message) - message для лога установки.
        """
        import os
        import subprocess
        import sys

        if not password:
            return (False, "Пустой пароль недопустим для krfb")
        if sys.platform == "win32":
            # На Windows krfb не запускается - пароль локально не нужен.
            return (True, "Windows - локальный krfb не используется (пропущено)")
        kw = local_kconfig_tool("kwriteconfig")
        if not kw:
            return (True, "на этой машине krfb не установлен (пропущено)")

        rc_dir = os.path.join(os.path.expanduser("~"), ".config")
        rc = os.path.join(rc_dir, "krfbrc")
        try:
            os.makedirs(rc_dir, exist_ok=True)
        except Exception as e:
            return (False, f"Не удалось создать ~/.config: {e}")
        obscured = kstring_obscure(password)
        # desktopPassword показывается окном KRFB открытым текстом: туда
        # случайный dummy, настоящий пароль - только в unattendedPassword.
        import secrets
        import string

        dummy = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(16)
        )
        obscured_dummy = kstring_obscure(dummy)
        # Чистое окружение: frozen-бинарь (PyInstaller) экспортирует
        # LD_LIBRARY_PATH во временный каталог - системный kwriteconfig
        # (Qt5/libicu) подтягивает бандловскую libstdc++ и падает
        # (CXXABI_1.3.15 not found). На Windows build_clean_env() вернёт
        # None (env не фильтруется).
        # wrap_as_user: приложение может работать от root (pkexec) - без
        # обёртки конфиг стал бы root-owned в HOME root, а не в home
        # пользователя оператора.
        from src.utils.system.subprocess_env import build_clean_env, wrap_as_user

        env = build_clean_env()
        try:
            # desktopPassword (виден в окне KRFB) - dummy, unattendedPassword
            # (тихий доступ) - настоящий пароль.
            for key, value in (
                ("desktopPassword", obscured_dummy),
                ("unattendedPassword", obscured),
            ):
                r = subprocess.run(
                    wrap_as_user(
                        [
                            kw,
                            "--file",
                            rc,
                            "--group",
                            KRFB_GROUP_SECURITY,
                            "--key",
                            key,
                            value,
                        ]
                    ),
                    capture_output=True,
                    timeout=10,
                    env=env,
                )
                if r.returncode != 0:
                    return (
                        False,
                        f"kwriteconfig ({key}) failed: "
                        f"{r.stderr.decode(errors='replace').strip()}",
                    )
            r = subprocess.run(
                wrap_as_user(
                    [
                        kw,
                        "--file",
                        rc,
                        "--group",
                        KRFB_GROUP_SECURITY,
                        "--key",
                        "allowUnattendedAccess",
                        "true",
                        "--type",
                        "bool",
                    ]
                ),
                capture_output=True,
                timeout=10,
                env=env,
            )
            if r.returncode != 0:
                return (False, "kwriteconfig (allowUnattendedAccess) failed")
            # Окно "Совместный доступ" не должно восстанавливаться из
            # KDE-сессии: --nodialog это не подавляет, нужен startMinimized.
            r = subprocess.run(
                wrap_as_user(
                    [
                        kw,
                        "--file",
                        rc,
                        "--group",
                        KRFB_GROUP_MAINWINDOW,
                        "--key",
                        KRFB_KEY_START_MINIMIZED,
                        "true",
                        "--type",
                        "bool",
                    ]
                ),
                capture_output=True,
                timeout=10,
                env=env,
            )
            if r.returncode != 0:
                return (False, "kwriteconfig (startMinimized) failed")
        except Exception as e:
            return (False, f"kwriteconfig failed: {e}")

        # Verify: kreadconfig unattendedPassword + обратная obscure =
        # исходный пароль (в desktopPassword теперь dummy).
        kr = local_kconfig_tool("kreadconfig")
        if kr:
            try:
                r = subprocess.run(
                    wrap_as_user(
                        [
                            kr,
                            "--file",
                            rc,
                            "--group",
                            KRFB_GROUP_SECURITY,
                            "--key",
                            "unattendedPassword",
                        ]
                    ),
                    capture_output=True,
                    timeout=10,
                    env=env,
                )
                rb = r.stdout.decode("utf-8", errors="replace").strip()
                dec = "".join(chr(OBSCURE_BASE - ord(c)) for c in rb)
                if dec != password:
                    return (False, "проверка записи пароля не прошла")
            except Exception as e:
                logger.warning(f"KRFBService: local verify failed: {e}")

        # Перезапуск krfb: пароль перечитывается только при старте.
        script = (
            "if systemctl --user list-unit-files 2>/dev/null | grep -q app-org.kde.krfb; then "
            "systemctl --user restart app-org.kde.krfb.service && echo RESTART_UNIT; "
            "elif systemctl --user list-unit-files 2>/dev/null | grep -q krfb-dno; then "
            "systemctl --user restart krfb-dno.service && echo RESTART_UNIT; "
            "elif systemctl --user list-unit-files 2>/dev/null | grep -q krfb.service; then "
            "systemctl --user restart krfb.service && echo RESTART_UNIT; "
            "else pgrep -x krfb >/dev/null && echo KRFB_ALIVE || echo KRFB_OFF; fi"
        )
        try:
            r = subprocess.run(
                wrap_as_user(["bash", "-c", script]),
                capture_output=True,
                timeout=30,
                env=env,
            )
            out = r.stdout.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"KRFBService: local restart failed: {e}")
            out = ""
        if "KRFB_OFF" in out or ("RESTART_UNIT" not in out and "KRFB_OFF" in out):
            return (True, "пароль записан (krfb перезапустится при старте)")
        return (True, "пароль применён локально")

    # ========== Операции на хосте (SSH) ==========

    @staticmethod
    def _as_user_snippet() -> str:
        """Shell-фрагмент смены пользователя: setpriv или fallback runuser.

        setpriv (--reuid/--regid, util-linux) меняет uid БЕЗ PAM-сессии.
        runuser проходит PAM и падает на учётках с лимитами (pam_limits
        maxlogins - школьные guest): "There were too many logins" /
        "cannot open session". Fragment ожидает переменные $XU (пользователь)
        и $XUID (его uid); команда применяется через "$@" в обеих ветках:
        <snippet> bash -c "$CMD" - ветка if/else сама исполняет аргументы.
        """
        return (
            "if command -v setpriv >/dev/null 2>&1; then "
            'setpriv --reuid="$XUID" --regid="$(id -g "$XU")" --init-groups '
            'env PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" '
            'HOME="$(getent passwd "$XU" | cut -d: -f6)" '
            "XDG_RUNTIME_DIR=/run/user/$XUID "
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$XUID/bus "
            '"$@"; '
            "else "
            'runuser -u "$XU" -- env XDG_RUNTIME_DIR=/run/user/$XUID '
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$XUID/bus "
            '"$@"; fi'
        )

    def _exec_as_xuser(
        self, host: str, user_script: str, timeout: int = 20, device=None
    ) -> Tuple[str, bool]:
        """Выполнить скрипт в контексте пользователя X-сессии хоста.

        krfb запущен в X-сессии (не root). Конфиг/юнит принадлежат
        пользователю сессии. Определяем его через loginctl (первая
        graphical/seat-сессия), выполняем команду от его имени через
        setpriv (без PAM) с fallback на runuser, с XDG_RUNTIME_DIR +
        DBUS_SESSION_BUS_ADDRESS.

        Args:
            host: адрес хоста.
            user_script: shell-скрипт (выполняется как X-пользователь).
            timeout: таймаут SSH-команды.
            device: DeviceModel (не обязателен; без него резолв по host).
        """
        full = (
            "XU=$(loginctl list-sessions --no-legend 2>/dev/null "
            "| awk '$4==\"seat0\" {print $3; exit}'); "
            '[ -z "$XU" ] && XU=$(loginctl list-sessions --no-legend '
            "2>/dev/null | awk '$2!=\"root\" && $1!~ /^c/ && NF>2 {print $3}' "
            "| sort -u | head -1); "
            'if [ -z "$XU" ]; then echo no-x-user; exit 3; fi; '
            'XUID=$(id -u "$XU" 2>/dev/null) || exit 3; '
            "CMD=" + _shell_quote(user_script) + "; "
            "ASU() { " + self._as_user_snippet() + "; }; "
            'if [ "$(id -un)" = "$XU" ]; then '
            "export XDG_RUNTIME_DIR=/run/user/$XUID; "
            "export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; "
            'bash -c "$CMD"; '
            'else ASU bash -c "$CMD"; '
            "fi"
        )
        return self._exec_ssh(host, full, timeout=timeout, device=device)

    def change_password(self, host: str, password: str, device=None) -> bool:
        """Сменить пароль VNC-сервера на удалённом хосте.

        kmsvnc и x11vnc - файл пароля и перезапуск службы (см.
        ``_host_server``). KRFB: настоящий пароль пишется в krfbrc
        [Security] unattendedPassword
        (тихий доступ без подтверждения - наш рабочий путь), в
        desktopPassword - случайный dummy: окно KRFB показывает ЭТОТ пароль
        открытым текстом. Затем перезапуск krfb (user unit или pkill -
        krfb перечитывает пароль только при старте).

        Args:
            host: адрес хоста.
            password: новый пароль (plain).
            device: DeviceModel (необязателен; без него резолв по host).

        Returns:
            True при успехе.
        """
        self._last_error = ""
        self._last_error_detail = ""
        if not host:
            self._last_error = "Не указан хост"
            return False
        if not password:
            self._last_error = "Пустой пароль VNC недопустим"
            return False
        server = self._host_server(host, device)
        if server is None:
            return False
        if server == "kmsvnc":
            return self._change_kmsvnc_password(host, password, device)
        if server == "x11vnc":
            return self._change_x11vnc_password(host, password, device)

        obscured = kstring_obscure(password)
        # desktopPassword показывается окном KRFB открытым текстом: туда
        # случайный dummy, настоящий пароль - только в unattendedPassword.
        import secrets
        import string

        dummy = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(16)
        )
        obscured_dummy = kstring_obscure(dummy)
        # Экранирование для одинарных кавычек shell
        esc = obscured.replace("'", "'\\''")
        esc_dummy = obscured_dummy.replace("'", "'\\''")
        pw_esc = password.replace("'", "'\\''")

        # Запись парольных ключей + verify: roundtrip obscure должен вернуть
        # исходный пароль (иначе запись/чтение кодировки сломаны). Verify
        # читает unattendedPassword (в desktopPassword теперь dummy).
        # Verify через `python3 -c` + printf (heredoc ломается PTY-эхом
        # SSHWorker: эхо повторяет текст heredoc в stdout).
        verify_py = (
            "import sys;v=sys.stdin.buffer.read().decode('utf-8');"
            "print(''.join(chr(0x1001F-ord(c)) for c in v))"
        )
        script = (
            "RC=$HOME/.config/krfbrc; "
            "mkdir -p $HOME/.config; "
            + _KCONFIG_VARS
            + f'"$KW" --file $RC --group {KRFB_GROUP_SECURITY} --key desktopPassword \'{esc_dummy}\' || exit 4; '
            f'"$KW" --file $RC --group {KRFB_GROUP_SECURITY} --key unattendedPassword \'{esc}\' || exit 4; '
            f'"$KW" --file $RC --group {KRFB_GROUP_SECURITY} --key allowUnattendedAccess true --type bool; '
            f'"$KW" --file $RC --group {KRFB_GROUP_MAINWINDOW} --key {KRFB_KEY_START_MINIMIZED} true --type bool || exit 4; '
            'RB=$("$KR" --file $RC --group '
            + KRFB_GROUP_SECURITY
            + " --key unattendedPassword | tr -d '\\n\\r'); "
            'DEC=$(printf \'%s\' "$RB" | python3 -c "' + verify_py + '"); '
            f"if [ \"$DEC\" = '{pw_esc}' ]; then echo PASSWORD_OK; else echo 'PASSWORD_MISMATCH'; exit 5; fi; "
            "echo PASSWORD_OK"
        )

        out, ok = self._exec_as_xuser(host, script, timeout=30, device=device)
        if not ok or "PASSWORD_OK" not in (out or ""):
            self._last_error = f"Смена пароля krfb не удалась: {out or 'нет вывода'}"
            self._last_error_detail = out or ""
            self._logger.warning(
                f"KRFBService: change_password failed for {host}: {out}"
            )
            return False
        self._logger.info(f"KRFBService: password changed on {host}")

        # Рестарт krfb для применения пароля
        return self._restart_krfb(host, device)

    def restart_service(self, host: str, device=None) -> bool:
        """Перезапустить VNC-сервер хоста (kmsvnc, x11vnc или KRFB).

        Args:
            host: адрес хоста.
            device: DeviceModel (необязателен; без него резолв по host).

        Returns:
            True при успехе.
        """
        self._last_error = ""
        self._last_error_detail = ""
        if not host:
            self._last_error = "Не указан хост"
            return False
        server = self._host_server(host, device)
        if server is None:
            return False
        if server in ("kmsvnc", "x11vnc"):
            return self._restart_system_server(host, server, device)
        return self._restart_krfb(host, device)

    def _restart_krfb(self, host: str, device=None) -> bool:
        """Перезапустить krfb на хосте.

        Приоритет: systemd user unit krfb-dno (если есть) -> pkill krfb
        с запуском ``krfb --nodialog`` (krfb может быть запущен
        вручную/автозапуском). Штатный юнит krfb.service НЕ перезапускается:
        он стартует БЕЗ --nodialog и через D-Bus показывает окно - вместо
        этого он останавливается и гасится, процесс поднимается напрямую.

        systemctl --user выполняется в контексте X-пользователя (через
        setpriv/runuser, если SSH-подключение от другого имени): запуск от
        root трогал бы user-менеджер root, а не графической сессии.
        Перед рестартом принудительно ставится [MainWindow] startMinimized
        (--nodialog не подавляет восстановление окна из KDE-сессии).

        Args:
            host: адрес хоста.
            device: DeviceModel (необязателен; без него резолв по host).

        Returns:
            True при успехе.
        """
        script = (
            "XU=$(loginctl list-sessions --no-legend 2>/dev/null "
            "| awk '$4==\"seat0\" {print $3; exit}'); "
            '[ -z "$XU" ] && XU=$(loginctl list-sessions --no-legend '
            "2>/dev/null | awk '$2!=\"root\" && $1!~ /^c/ && NF>2 {print $3}' "
            "| sort -u | head -1); "
            'XUID=$(id -u "$XU" 2>/dev/null); '
            "ASU() { " + self._as_user_snippet() + "; }; "
            'if [ -n "$XU" ] && [ "$(id -un)" != "$XU" ]; then USE_ASU=1; else USE_ASU=0; fi; '
            'ucmd() { if [ "$USE_ASU" = 1 ]; then ASU "$@"; else "$@"; fi; }; '
            # Окно не должно восстанавливаться из KDE-сессии (best-effort).
            # Через bash -c: $HOME должен раскрыться УЖЕ в контексте
            # X-пользователя (ASU выставляет HOME), а не в outer-shell.
            'if [ -n "$XU" ]; then '
            "ucmd bash -c 'K=$(command -v kwriteconfig6 || command -v kwriteconfig5) "
            '&& "$K" --file $HOME/.config/krfbrc --group '
            + KRFB_GROUP_MAINWINDOW
            + " --key "
            + KRFB_KEY_START_MINIMIZED
            + " true --type bool' 2>/dev/null || true; fi; "
            "if ucmd systemctl --user list-unit-files 2>/dev/null | grep -q app-org.kde.krfb; then "
            "ucmd systemctl --user restart app-org.kde.krfb.service && echo RESTART_UNIT; "
            "elif ucmd systemctl --user list-unit-files 2>/dev/null | grep -q krfb-dno; then "
            "ucmd systemctl --user restart krfb-dno.service && echo RESTART_UNIT; "
            "else "
            # Штатный юнит - конкурент без --nodialog: гасим, процесс
            # поднимем напрямую с --nodialog ниже.
            "ucmd systemctl --user stop krfb.service 2>/dev/null; "
            "ucmd systemctl --user disable krfb.service 2>/dev/null; "
            'pkill -u "$XU" -x krfb 2>/dev/null; pkill -x krfb 2>/dev/null; sleep 1; '
            'if [ -n "$XUID" ]; then '
            # setsid + </dev/null: отсоединить krfb от PTY SSH-сессии,
            # иначе SIGHUP при закрытии канала убивает процесс. Старт
            # без PAM (setpriv, fallback runuser: school guest рушит
            # runuser); if/else-фрагмент обёрнут в bash -c - setsid не
            # принимает compound-команду как префикс.
            "setsid bash -c "
            + _shell_quote(_SETPREV_KRFB_START)
            + ' "$@" '
            + _shell_quote("krfb --nodialog")
            + " </dev/null >/dev/null 2>&1 & "
            "echo RESTART_PKILL; "
            "else "
            "echo NO_DISPLAY; "
            "fi; "
            "fi; sleep 3; "
            "pgrep -x krfb >/dev/null && echo KRFB_RUNNING || echo KRFB_NOT_RUNNING"
        )

        # Скрипт выполняется напрямую (root): сам определяет X-пользователя
        # (seat-сессия) и делает pkill/старт процесса. ВАЖНО: не через
        # _exec_as_xuser - двойная обёртка ломает внутренний
        # setpriv/runuser (runuser не работает от обычного пользователя).
        out, ok = self._exec_ssh(host, script, timeout=30, device=device)
        if not ok:
            self._last_error = f"Перезапуск krfb не удался: {out or 'нет вывода'}"
            self._last_error_detail = out or ""
            return False
        if "NO_DISPLAY" in (out or "") and "KRFB_RUNNING" not in (out or ""):
            # krfb остановлен (pkill), но запустить без DISPLAY нельзя -
            # пароль применится при следующем входе пользователя/старте krfb.
            # Это НЕ ошибка смены пароля: конфиг записан.
            self._logger.info(
                f"KRFBService: krfb stopped on {host} (no DISPLAY), "
                "password applies on next start"
            )
            self._last_error = ""
            return True
        if "KRFB_RUNNING" not in (out or ""):
            self._last_error = "krfb не запущен после перезапуска"
            self._last_error_detail = out or ""
            return False
        self._logger.info(f"KRFBService: service restarted on {host}")
        if self._event_bus:
            try:
                from src.architecture import EventType

                self._event_bus.publish_typed(
                    event_type=EventType.CUSTOM,
                    source=self.__class__.__name__,
                    data={
                        "feature": "krfb_restart",
                        "host": host,
                        "action": "restarted",
                    },
                )
            except Exception:
                pass
        return True

    # ========== Какой сервер на хосте ==========
    # Сервер определяется на хосте, а не по настройке мастера: настройка
    # могла смениться после установки, а хосты ставились в разное время.

    def _host_server(self, host: str, device=None) -> Optional[str]:
        """VNC-сервер хоста: ``kmsvnc``, ``x11vnc`` или ``krfb``.

        Порядок: включённая служба kmsvnc, затем x11vnc (обе ставит мастер,
        одновременно включённой бывает только одна), затем KRFB - служба
        мастера krfb-dno включена или процесс krfb запущен. Пакет KRFB есть в
        МОС по умолчанию, поэтому сам бинарник признаком не считается.
        None - сервер не найден или проверка не удалась, причина в
        ``_last_error``.
        """
        script = (
            "for s in kmsvnc x11vnc; do "
            'echo "SYS $s $(systemctl is-enabled $s.service 2>/dev/null)"; done; '
            'echo "KRFB $(systemctl --global is-enabled krfb-dno.service 2>/dev/null)"; '
            "ls /home/*/.config/systemd/user/*.wants/krfb-dno.service "
            ">/dev/null 2>&1 && echo 'KRFB enabled'; "
            "pgrep -x krfb >/dev/null 2>&1 && echo 'KRFB running'; "
            "echo DETECT_OK"
        )
        out, ok = self._exec_ssh(
            host, "sh -c " + _shell_quote(script), timeout=15, device=device
        )
        if not ok or not _has_line(out, "DETECT_OK"):
            self._last_error = "Не удалось определить VNC-сервер на хосте"
            self._last_error_detail = out or ""
            return None
        found = set()
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "SYS" and parts[2] == "enabled":
                found.add(parts[1])
            elif len(parts) >= 2 and parts[0] == "KRFB" and parts[1] in (
                "enabled",
                "running",
            ):
                found.add("krfb")
        for server in ("kmsvnc", "x11vnc", "krfb"):
            if server in found:
                return server
        self._last_error = (
            "На хосте не найдена служба VNC (kmsvnc, x11vnc или KRFB): "
            "установите VNC мастером настройки"
        )
        return None

    # ========== kmsvnc и x11vnc ==========
    # Системные службы мастера: пароль в /etc/<сервер>/passwd, после смены
    # служба перезапускается (пароль читается при запуске).

    def _change_kmsvnc_password(self, host: str, password: str, device=None) -> bool:
        """Записать пароль kmsvnc и перезапустить службу."""
        script = (
            "umask 077; install -d -m 700 /etc/kmsvnc && "
            f"printf '%s\\n' {_shell_quote(password)} > /etc/kmsvnc/passwd && "
            "chmod 600 /etc/kmsvnc/passwd && echo PASSWORD_OK"
        )
        out, ok = self._exec_ssh(
            host, "sh -c " + _shell_quote(script), timeout=30, device=device
        )
        if not ok or "PASSWORD_OK" not in (out or ""):
            self._last_error = "Смена пароля kmsvnc не удалась"
            self._last_error_detail = out or ""
            self._logger.warning(f"KRFBService: kmsvnc password failed for {host}")
            return False
        self._logger.info(f"KRFBService: kmsvnc password changed on {host}")
        return self._restart_system_server(host, "kmsvnc", device)

    def _change_x11vnc_password(self, host: str, password: str, device=None) -> bool:
        """Записать пароль x11vnc (формат ``-storepasswd``) и перезапустить службу.

        Файл тот же, что пишет мастер: скрипт запуска передаёт его x11vnc
        через ``-rfbauth``. Классический VNC Auth учитывает первые 8 байтов.
        """
        import base64

        from src.utils.security.vnc_password import encode_vnc_password_file

        encoded = base64.b64encode(encode_vnc_password_file(password)).decode("ascii")
        script = (
            "umask 077; install -d -m 700 /etc/x11vnc && "
            f"printf '%s' {encoded} | base64 -d > /etc/x11vnc/passwd && "
            "chmod 600 /etc/x11vnc/passwd && test -s /etc/x11vnc/passwd && "
            "echo PASSWORD_OK"
        )
        out, ok = self._exec_ssh(
            host, "sh -c " + _shell_quote(script), timeout=30, device=device
        )
        if not ok or not _has_line(out, "PASSWORD_OK"):
            self._last_error = "Смена пароля x11vnc не удалась"
            self._last_error_detail = out or ""
            self._logger.warning(f"KRFBService: x11vnc password failed for {host}")
            return False
        self._logger.info(f"KRFBService: x11vnc password changed on {host}")
        return self._restart_system_server(host, "x11vnc", device)

    def _restart_system_server(self, host: str, name: str, device=None) -> bool:
        """Перезапустить службу kmsvnc или x11vnc и дождаться её запуска."""
        out, ok = self._exec_ssh(
            host,
            "sh -c "
            + _shell_quote(
                f"systemctl restart {name}.service && sleep 2 && "
                f"systemctl is-active {name}.service"
            ),
            timeout=30,
            device=device,
        )
        if not ok or "active" not in (out or "").split():
            self._last_error = f"{name} не запущен после перезапуска"
            self._last_error_detail = out or ""
            return False
        self._logger.info(f"KRFBService: {name} restarted on {host}")
        return True

    # ========== Открытие вьювера ==========

    @staticmethod
    def krdc_available() -> bool:
        """True, если на машине оператора установлен krdc (Linux)."""
        return _find_krdc() is not None

    def open_viewer(
        self, device, mode: str = "control", viewer: Optional[str] = None
    ) -> bool:
        """Открыть просмотр/управление krfb-хостом.

        viewer управляет способом открытия на Linux:
          - None       : krdc, если установлен, иначе браузер (default);
          - "krdc"     : принудительно krdc (нет krdc - ошибка);
          - "browser"  : принудительно браузер (vnc.html?backend=krfb).

        Windows всегда браузер (krdc недоступен).

        krdc открывается по адресу vnc://:пароль@host:port (плагин VNC
        принимает только схему vnc; пароль из URL подставляется без
        диалога - KPasswordDialog не показывается).

        Браузерный URL содержит ?token= (base64 первого пользователя
        веб-доступа): без него WS /ws/vnc/{iid} и /api/*/password
        отклонят запрос 401 - вьювер бесконечно «Подключение...».

        Args:
            device: DeviceModel (или None - берётся host из device.host).
            mode: "view" | "control" (viewOnly в браузерном вьювере).
            viewer: "krdc" | "browser" | None (авто).

        Returns:
            True при успехе.
        """
        import sys

        from src.config import config

        if device is None:
            self._last_error = "Устройство не выбрано"
            return False
        host = device.host
        port = self.resolve_port(device)
        view_flag = 1 if mode == "view" else 0

        # Токен веб-доступа: base64(username:password) первого пользователя
        # (тот же формат, что /api/login возвращает браузеру).
        token = self._web_viewer_token()
        if not token:
            self._last_error = (
                "Веб-доступ не настроен (пользователи веб-сервера не заданы) - "
                "браузерный вьювер krfb недоступен. Настройте веб-доступ "
                "(Настройки - Веб-доступ)."
            )
            self._logger.error("KRFBService: no web users - browser viewer unavailable")
            return False

        if sys.platform != "win32" and viewer == "krdc":
            # Принудительный krdc. Схема vnc (плагин VNC krdc принимает
            # только её; rfb-плагина не существует). Пароль в URL - krdc
            # берёт url.password() и не показывает KPasswordDialog.
            krdc = _find_krdc()
            if not krdc:
                self._last_error = (
                    "krdc не установлен - откройте доступ через браузер "
                    "или установите krdc."
                )
                return False
            import subprocess
            from urllib.parse import quote

            from src.config import config as _config
            from src.utils.system.subprocess_env import (
                build_clean_env,
                safe_cwd_for_user,
                wrap_as_user,
            )

            vnc_password = getattr(_config.app.veyon, "vnc_password", "") or ""
            userinfo = f":{quote(vnc_password, safe='')}" if vnc_password else ""
            try:
                kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                clean_env = build_clean_env()
                if clean_env is not None:
                    kwargs["env"] = clean_env
                kwargs["cwd"] = safe_cwd_for_user()
                subprocess.Popen(
                    wrap_as_user([krdc, f"vnc://{userinfo}@{host}:{port}"]),
                    **kwargs,
                )
                self._logger.info(
                    f"KRFBService: krdc opened vnc://{host}:{port} ({mode})"
                )
                return True
            except Exception as e:
                self._last_error = f"Не удалось запустить krdc: {e}"
                return False

        # Браузерный вьювер: локальный веб-сервер с ?backend=krfb
        # (Windows всегда, Linux по выбору или как fallback).
        from src.utils.network.urls import web_base_url

        url = (
            f"{web_base_url(config.app.web, 'localhost')}/vnc.html"
            f"?iid={device.iid}&backend=krfb&view={view_flag}"
            f"&token={token}&name={host}"
        )
        try:
            from src.utils.system.subprocess_env import open_url_in_browser

            if not open_url_in_browser(url):
                self._last_error = (
                    "Не удалось открыть браузер. Проверьте, что в системе "
                    "есть браузер по умолчанию (xdg-open)."
                )
                self._logger.error(
                    f"KRFBService: browser viewer open failed for {host}:{port}"
                )
                return False
            self._logger.info(
                f"KRFBService: browser viewer opened {host}:{port} ({mode})"
            )
            return True
        except Exception as e:
            self._last_error = f"Не удалось открыть браузер: {e}"
            return False

    @staticmethod
    def _web_viewer_token() -> str:
        """base64(user:pass) первого пользователя веб-доступа ("" если нет).

        Возвращает ГОТОВОЕ ЗНАЧЕНИЕ токена (уже base64 - безопасно в query).
        Sentinel '********' в пароле не встречается в рантайм-конфиге
        (это UI-плейсхолдер ConfigService.update_web_server) - на всякий
        случай пустой пароль лучше, чем мусор '********' в запросе.
        """
        import base64

        from src.config import config

        users = getattr(config.app.web, "users", None) or []
        for u in users:
            if u.username:
                pwd = u.password or ""
                if pwd == "********":
                    pwd = ""
                tok = base64.b64encode(f"{u.username}:{pwd}".encode("utf-8")).decode(
                    "ascii"
                )
                return tok
        return ""


def _find_krdc() -> Optional[str]:
    """Найти krdc в PATH. None если не установлен."""
    import shutil

    return shutil.which("krdc")


def _has_line(output: Optional[str], marker: str) -> bool:
    """Есть ли в выводе строка, целиком равная ``marker`` (без эха команды)."""
    return any(line.strip() == marker for line in (output or "").splitlines())


def _shell_quote(s: str) -> str:
    """Экранировать строку для одинарных кавычек shell."""
    return "'" + s.replace("'", "'\\''") + "'"


def _esc(s: str) -> str:
    """Экранирование одинарных кавычек для shell (алиас для читаемости)."""
    return _shell_quote(s)
