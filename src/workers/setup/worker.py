"""Execute a registry snapshot in the bounded shared services pool.

Each host owns one SSH transport and runs selected phased installers in order.
SSH key verification uses a separate connection with exactly the selected key.
Transport operations share cancellation/deadlines; VNC replacement owns a
recoverable configuration snapshot. Every selected cell gets one terminal
result, including admission failures and operations never started after cancel.
"""

from concurrent.futures import as_completed
from PySide6.QtCore import Signal
from src.workers.base import BaseWorker
from typing import List, Dict, Optional
import threading
import shlex
import uuid

from src.logger import logger
from src.domain.models.device import DeviceModel
from src.config import config
from src.workers.command.executor_base import get_credentials, CredentialsResult
from src.workers.application_pools import get_application_pool


# Шаблоны для VNC (wrapper + systemd-юнит x11vnc).


# Порядок сервисов в логе/таблице.
# packages — единая колонка для всех доп. RPM-пакетов (устанавливаются
# после SSH-ключа, перед Veyon/VNC — базовая настройка хоста).


from src.workers.setup.types import (
    INSTALLERS,
    SERVICE_ORDER as SERVICE_ORDER,
    RESULT_SUCCESS as RESULT_SUCCESS,
    RESULT_ERROR,
    RESULT_IGNORE as RESULT_IGNORE,
    RESULT_ABORT,
    RESULT_WAITING as RESULT_WAITING,
    parse_dnf_available as parse_dnf_available,
    parse_apt_candidate as parse_apt_candidate,
)


class RemoteServiceInstallerWorker(BaseWorker):
    """Единый worker потоковой установки сервисов на удалённые хосты.

    Для каждого хоста открывает одно SSH-соединение и выполняет выбранные
    сервисы последовательно. Результаты по каждому сервису эмитятся
    отдельно через ``service_result``; краткие сообщения о ходе — через
    ``host_log``. Детальный per-host/per-service лог включается в output
    ``service_result``.
    """

    # device, service_name, result_code, output (детальный лог сервиса)
    service_result = Signal(object, str, int, str)
    # host, service_name, message (краткое сообщение для общего лога)
    host_log = Signal(str, str, str)
    error = Signal(str)

    def __init__(
        self,
        devices: List[DeviceModel],
        services: Dict[str, bool],
        ssh_public_key: str = "",
        veyon_public_key_pem: str = "",
        veyon_key_name: str = "",
        veyon_access_group: Optional[str] = None,
        vnc_password: str = "",
        vnc_port: int = 5900,
        veyon_server_port: int = 11100,
        vnc_server: str = "kmsvnc",  # DEFAULT_VNC_SERVER
        extra_packages: List[str] = None,
        use_sudo_override: Optional[bool] = None,
        default_bundle_iid: Optional[str] = None,
        ssh_private_key_path: str = "",
        credential_bundles=None,
        veyon_private_key: str = "",
        ssh_settings=None,
        service_options=(),
        prepare=None,
        parent=None,
    ) -> None:
        super().__init__()
        self.devices = devices
        self.services = dict(services)
        self.installers = tuple(INSTALLERS)
        self.service_order = tuple(item.key for item in self.installers)
        if set(self.services) - set(self.service_order):
            raise ValueError("Неизвестный сервис установки")
        # PreparePlan: подготовка выполняется под своей связкой, остальные
        # сервисы хоста - под связкой для подготовленных хостов.
        self.prepare = prepare if self.services.get("prepare") else None
        if self.services.get("prepare") and self.prepare is None:
            raise ValueError("Не заданы параметры подготовки")
        # iid -> "prepare" | "after": стадия учётных данных хоста.
        self._stages = {}
        # (iid, service) -> итоговый код.
        self._codes = {}
        option_values = dict(service_options)
        self.service_options = tuple((item.key, item.build_parameters(dict(option_values.get(item.key, ()))))
                                     for item in self.installers if self.services.get(item.key) and item.parameters)
        self.ssh_public_key = ssh_public_key or ""
        self.veyon_public_key_pem = veyon_public_key_pem or ""
        self.veyon_key_name = veyon_key_name or ""
        self.veyon_access_group = veyon_access_group
        self.vnc_password = vnc_password or ""
        self.vnc_port = vnc_port
        self.veyon_server_port = veyon_server_port
        # Выбранный VNC-сервер из VNC_SERVERS: определяет, что ставится на
        # шаге "vnc". Пароль ОДИН на все бекенды (self.vnc_password).
        self.vnc_server = vnc_server
        self.extra_packages = list(extra_packages) if extra_packages else []
        # Связка по умолчанию из мастера (могла не быть сохранена в config) +
        # override флага sudo из мастера.
        self.use_sudo_override = use_sudo_override
        # None = наследовать конфиг, "" = явно без общей связки.
        self.default_bundle_iid = default_bundle_iid
        self.ssh_private_key_path = ssh_private_key_path
        self.credential_bundles = credential_bundles
        self.veyon_private_key = veyon_private_key
        from dataclasses import asdict
        from types import SimpleNamespace

        self._ssh_settings = SimpleNamespace(
            **dict(ssh_settings or asdict(config.app.ssh))
        )
        self._result_lock = threading.Lock()
        self._terminal_results = set()
        self._connections = set()
        self._connecting = set()
        self._local_abort = threading.Event()

    # ------------------------------------------------------------ public API

    def execute(self) -> None:
        try:
            self._execute_install()
        except Exception:
            logger.exception("RemoteServiceInstallerWorker: orchestration failed")
            self.error.emit("Не удалось завершить установку. Подробности в журнале.")
        finally:
            for device in self.devices:
                for service in self.service_order:
                    if self.services.get(service):
                        self._report(
                            device,
                            service,
                            RESULT_ABORT
                            if self._local_abort.is_set()
                            else RESULT_ERROR,
                            "Прервано"
                            if self._local_abort.is_set()
                            else "Операция не завершилась успешно",
                        )
            self.finished.emit()

    def _report(self, device, service, code, output):
        key = (device.iid, service)
        with self._result_lock:
            if key in self._terminal_results:
                return
            self._terminal_results.add(key)
            self._codes[key] = code
        self.service_result.emit(device, service, code, output)

    def _execute_install(self) -> None:
        logger.info(
            "RemoteServiceInstallerWorker: start for %d devices, services=%s",
            len(self.devices),
            {k: v for k, v in self.services.items() if v},
        )

        batch_id = uuid.uuid4().hex
        with get_application_pool("services").lease(source="application") as ex:
            future_to_dev = {}
            for device in self.devices:
                if self._local_abort.is_set():
                    break

                def run_for_device(dev):
                    if self._local_abort.is_set():
                        return
                    self._install_on_host(dev)

                try:
                    future = ex.submit(
                        run_for_device,
                        device,
                        metadata={
                            "host": device.host,
                            "type": "service_install",
                            "device": device.name or device.host,
                            "batch_id": batch_id,
                        },
                    )
                except RuntimeError as error:
                    self.error.emit(f"{device.host}: {error}")
                    for service, enabled in self.services.items():
                        if enabled:
                            self._report(device, service, RESULT_ERROR, str(error))
                    continue
                future_to_dev[future] = device

            for future in as_completed(future_to_dev):
                if self._local_abort.is_set():
                    break
                device = future_to_dev[future]
                try:
                    future.result()
                except Exception as e:
                    msg = f"Error on {device.host}: {e}"
                    logger.error(msg)
                    self.error.emit(msg)

        logger.info("RemoteServiceInstallerWorker: completed")

    def abort(self) -> None:
        already_requested = self._local_abort.is_set()
        self._local_abort.set()
        self._abort_event.set()
        if already_requested:
            return
        with self._result_lock:
            transports = tuple(self._connections)
            clients = tuple(self._connecting)

        def close_connections():
            for transport in transports:
                try:
                    transport.cancel()
                except Exception:
                    logger.warning("Could not cancel a setup transport")
            for ssh in clients:
                try:
                    ssh.close()
                except Exception:
                    logger.warning("Could not close a setup connection")

        # Paramiko close may wait for its transport lock/key exchange.
        # Never execute it in the GUI caller of abort().
        self._cancel_thread = threading.Thread(target=close_connections, daemon=True)
        self._cancel_thread.start()
        logger.info("RemoteServiceInstallerWorker: abort requested")

    def _bundle(self, iid):
        """(login, password) связки из плана мастера или сервиса связок."""
        if not iid:
            return None
        if self.credential_bundles is not None:
            for bundle_iid, login, password in self.credential_bundles:
                if bundle_iid == iid:
                    return login, password
            return None
        from src.di import get_container
        from src.services.configuration.credential_service import CredentialService

        service = get_container().resolve_optional(CredentialService)
        bundle = service.get_bundle_by_iid(iid) if service is not None else None
        return (bundle.login, bundle.password) if bundle is not None else None

    def _global_bundle_iid(self):
        """Связка по умолчанию: выбранная в мастере, иначе из настроек."""
        if self.default_bundle_iid is not None:
            return self.default_bundle_iid
        return config.app.credentials.default_bundle_iid

    def _stage(self, device):
        """Стадия учётных данных хоста: None - подготовка не выбрана."""
        if self.prepare is None:
            return None
        return self._stages.get(device.iid, "prepare")

    def _resolve_credentials(self, device: DeviceModel, stage=None):
        """Учётные данные из связок (get_credentials).

        Связка по умолчанию, выбранная в мастере (ещё не сохранённая в config),
        применяется до вызова get_credentials: если у хоста нет собственной
        связки — временно резолвим выбранную связку напрямую.
        SSH-ключ (private_key) резолвится как раньше.

        С подготовкой: стадия "prepare" - связка входа для подготовки без
        ключа (на заводском хосте его нет), "after" - связка для
        подготовленных хостов и выбранный ключ; по умолчанию - текущая стадия
        хоста.
        """
        stage = stage or self._stage(device)
        if stage is not None:
            iid = (
                self.prepare.connect_bundle_iid
                if stage == "prepare"
                else self.prepare.after_bundle_iid
            )
            # Пустой iid - наследовать глобальную связку; своя связка хоста не
            # учитывается: после подготовки запись хоста её теряет.
            chosen = self._bundle(iid or self._global_bundle_iid())
            if chosen is None:
                raise ValueError("Связка подготовки не найдена в плане")
            key = None
            if stage == "after":
                key = get_credentials(
                    device, key_path=self.ssh_private_key_path or None
                ).private_key
            return CredentialsResult(
                username=chosen[0] or "root", password=chosen[1], private_key=key
            )
        base = get_credentials(device, key_path=self.ssh_private_key_path or None)
        if self.credential_bundles is not None:
            bundles = {
                iid: (login, password)
                for iid, login, password in self.credential_bundles
            }
            iid = getattr(device, "credential_bundle_iid", None)
            chosen = bundles.get(iid) if iid else None
            if chosen is None and self.default_bundle_iid:
                chosen = bundles.get(self.default_bundle_iid)
                if chosen is None:
                    raise ValueError("Связка не найдена в плане")
            return CredentialsResult(
                username=(chosen[0] or "root") if chosen else "root",
                password=chosen[1] if chosen else None,
                private_key=base.private_key,
            )
        # Связка по умолчанию, выбранная в мастере: применяем только если
        # у хоста нет собственной связки.
        if self.default_bundle_iid is not None:
            try:
                from src.di import get_container
                from src.services.configuration.credential_service import CredentialService

                svc = get_container().resolve_optional(CredentialService)
                host_iid = getattr(device, "credential_bundle_iid", None)
                if svc is None:
                    raise RuntimeError("Сервис связок недоступен")
                b = svc.get_bundle_by_iid(host_iid) if host_iid else None
                if b is None and self.default_bundle_iid:
                    b = svc.get_bundle_by_iid(self.default_bundle_iid)
                    if b is None:
                        raise ValueError("Выбранная связка больше не существует")
                return CredentialsResult(
                    username=(b.login or "root") if b is not None else "root",
                    password=b.password if b is not None else None,
                    private_key=base.private_key,
                )
            except Exception as e:
                logger.warning(
                    "RemoteServiceInstallerWorker: bundle resolve failed: %s", e
                )
                raise
        return base

    def _resolve_use_sudo(self, device: DeviceModel) -> bool:
        """use_sudo: хост → общее значение мастера → конфиг."""
        from src.workers.command.sudo_utils import should_use_sudo

        # С подготовкой вход не под root всегда идёт через sudo: заводская
        # учётка - администратор, учётке «после» права проверены на шаге.
        if self.prepare is not None:
            return True
        if getattr(device, "use_sudo", None) is not None:
            return bool(device.use_sudo)
        if self.use_sudo_override is not None:
            return bool(self.use_sudo_override)
        return should_use_sudo(device)

    @staticmethod
    def _shell_escape(s: str) -> str:
        """Экранировать строку для безопасного использования в shell-команде."""
        return shlex.quote(s)

    @staticmethod
    def _validate_package_names(packages):
        from src.workers.setup.packages import validate_package_names
        validate_package_names(packages)

    # ------------------------------------------------------------ per-host

    def _install_on_host(self, device: DeviceModel) -> None:
        """Установить все выбранные сервисы на один хост.

        С подготовкой сервисы идут двумя группами: сначала «prepare» под
        связкой входа для подготовки, затем остальные в новом подключении под
        связкой для подготовленных хостов. Неудачная подготовка пропускает
        остальные сервисы хоста.
        """
        from src.workers.setup.context import OperationContext
        from src.workers.setup.types import validate_port
        operation_context = OperationContext.from_worker(self)
        try:
            validate_port(getattr(device, "port", None) or self._ssh_settings.port)
            if self.services.get("vnc"):
                validate_port(getattr(device, "vnc_port", None) or self.vnc_port)
            for descriptor in self.installers:
                if self.services.get(descriptor.key):
                    descriptor.validate(operation_context.parameters)
        except (ValueError, TypeError):
            for service in self.service_order:
                if self.services.get(service):
                    self._report(device, service, RESULT_ERROR, "Недопустимые параметры плана установки")
            return

        if self._local_abort.is_set():
            for svc in self.service_order:
                if self.services.get(svc):
                    self._report(device, svc, RESULT_ABORT, "Прервано")
            return

        if not device.host:
            for svc in self.service_order:
                if self.services.get(svc):
                    self._report(device, svc, RESULT_ERROR, "Не указан host")
            return

        node_iid = getattr(device, "node_iid", None)
        if node_iid and node_iid != "local":
            for service in self.service_order:
                if self.services.get(service):
                    self._report(
                        device,
                        service,
                        RESULT_ERROR,
                        "Установка доступна только локальным хостам",
                    )
            return

        selected = tuple(
            descriptor
            for descriptor in self.installers
            if self.services.get(descriptor.key)
        )
        if self.prepare is not None:
            groups = (
                tuple(d for d in selected if d.key == "prepare"),
                tuple(d for d in selected if d.key != "prepare"),
            )
        else:
            groups = (selected,)
        for index, group in enumerate(groups):
            if not group or self._local_abort.is_set():
                continue
            if index:
                code = self._codes.get((device.iid, "prepare"))
                if code not in (RESULT_SUCCESS, RESULT_IGNORE):
                    for descriptor in group:
                        self._report(
                            device,
                            descriptor.key,
                            RESULT_ERROR,
                            "Пропущено: подготовка хоста не выполнена",
                        )
                    return
                # Остальные сервисы - новое подключение под связкой
                # для подготовленных хостов.
                self._stages[device.iid] = "after"
            if not self._run_installers(device, group, operation_context):
                return

    def _run_installers(self, device, descriptors, operation_context) -> bool:
        """Одно SSH-подключение и установщики группы; False - не подключились.

        Ошибки подключения и предварительной проверки получают только ячейки
        этой группы: ячейки следующих групп закрывает вызывающий.
        """
        import paramiko

        from dataclasses import replace

        services = tuple(descriptor.key for descriptor in descriptors)
        stage = self._stage(device)
        try:
            creds = self._resolve_credentials(device)
        except Exception:
            for svc in services:
                self._report(
                    device,
                    svc,
                    RESULT_ERROR,
                    "Не удалось разрешить выбранную связку",
                )
            return False
        port = device.port or self._ssh_settings.port

        # sudo-параметры для non-root логина (PTY-expect через channel.get_pty)
        use_sudo = self._resolve_use_sudo(device)
        # sudo от root бессмысленен (иначе sudo-пароль уходит первой строкой
        # stdin в команды вида tee - порча записываемых файлов).
        if creds.username == "root":
            use_sudo = False
        sudo_password = creds.password if use_sudo else None
        # Сведения о подключении страница собирает по сервису "ssh".
        log_service = "ssh"

        # Лог про sudo — только для non-root логина (root не требует sudo).
        # Пишем всегда (вкл/выкл), чтобы пользователь видел состояние в детальном логе хоста.
        if creds.username != "root":
            if use_sudo:
                self.host_log.emit(
                    device.host,
                    log_service,
                    f"sudo включён (non-root логин: {creds.username})",
                )
            else:
                self.host_log.emit(
                    device.host,
                    log_service,
                    f"sudo выключен (логин: {creds.username})",
                )

        ssh = None
        connection_timer = None
        connected = False
        try:
            ssh = paramiko.SSHClient()
            with self._result_lock:
                self._connecting.add(ssh)
            connection_timer = threading.Timer(
                max(1, self._ssh_settings.connect_timeout), ssh.close
            )
            connection_timer.daemon = True
            connection_timer.start()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            if self._local_abort.is_set():
                raise InterruptedError("Setup cancelled")
            # Подготовка входит только паролем заводской учётки: ключи агента
            # и ~/.ssh расходовали бы попытки входа (MaxAuthTries).
            extra = (
                dict(allow_agent=False, look_for_keys=False)
                if stage == "prepare"
                else {}
            )
            ssh.connect(
                hostname=device.host,
                username=creds.username,
                password=creds.password,
                pkey=creds.private_key,
                port=port,
                timeout=self._ssh_settings.connect_timeout,
                banner_timeout=self._ssh_settings.connect_timeout,
                auth_timeout=self._ssh_settings.ssh_connect_timeout,
                **extra,
            )
            connected = True
        except Exception as e:
            err = f"SSH connection failed: {e}"
            if stage is not None:
                # Логин и способ входа: иначе не понять, какая связка не подошла.
                how = "паролем" if stage == "prepare" else "ключом и паролем"
                when = "для подготовки" if stage == "prepare" else "после подготовки"
                err = (
                    f"Не удалось подключиться {when} под «{creds.username}» "
                    f"({how}): {type(e).__name__}: {e}"
                )
            for svc in services:
                self.host_log.emit(device.host, svc, "Ошибка SSH-подключения")
                self._report(
                    device,
                    svc,
                    RESULT_ABORT if self._local_abort.is_set() else RESULT_ERROR,
                    err,
                )
            return False

        finally:
            if connection_timer is not None:
                connection_timer.cancel()
            with self._result_lock:
                self._connecting.discard(ssh)
            if not connected and ssh is not None:
                ssh.close()

        from src.workers.setup.transport import SetupTransport

        transport = SetupTransport(ssh, self._local_abort, use_sudo, sudo_password)
        user_transport = SetupTransport(ssh, self._local_abort)
        with self._result_lock:
            self._connections.update((transport, user_transport))
        run_cmd, write_file = transport.run, transport.write_file

        def sftp_append(remote_rel_path, content):
            from src.workers.setup.templates import AUTHORIZED_KEYS_SCRIPT

            script = AUTHORIZED_KEYS_SCRIPT
            code, out, error = user_transport.run(
                "python3 -c " + shlex.quote(script), stdin_data=content
            )
            return out == "PRESENT", error if code else ""

        try:
            code, platform, _ = run_cmd(
                "uname -s; test -d /run/systemd/system && command -v systemctl; command -v dnf || command -v apt-get",
                timeout=10,
            )
            if (
                code != 0
                or not platform.startswith("Linux\n")
                or "systemctl" not in platform
            ):
                for service in services:
                    self._report(
                        device,
                        service,
                        RESULT_ERROR,
                        "Нужен Linux с systemd и dnf/apt-get",
                    )
                return False
            from importlib import import_module

            for descriptor in descriptors:
                if self._local_abort.is_set():
                    return False
                if (device.iid, descriptor.key) in self._terminal_results:
                    continue
                module, name = descriptor.implementation.split(":")
                try:
                    context = replace(operation_context, options=dict(self.service_options).get(descriptor.key, ()))
                    installer = getattr(import_module(module), name)(context)
                    installer.install(device, ssh, run_cmd, write_file, sftp_append)
                except Exception:
                    self._report(device, descriptor.key, RESULT_ERROR, "Не удалось выполнить установщик сервиса")
            return True
        finally:
            with self._result_lock:
                self._connections.discard(transport)
                self._connections.discard(user_transport)
            try:
                ssh.close()
            except Exception:
                pass
