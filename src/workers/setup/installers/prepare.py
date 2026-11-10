"""Подготовка заводского хоста: учётные записи, пароль и ключи root, вход root.

Выполняется первым сервисом хоста под связкой входа для подготовки (sudo).
Проверка - отдельное подключение со связкой, которую получат записи хостов:
остальные сервисы прогона идут уже под ней.
"""

import json
import shlex
import threading
import time

from src.workers.setup.phases import PhasedInstaller, PhaseResult
from src.workers.setup.types import RESULT_ERROR, RESULT_SUCCESS

# sshd после reload несколько долей секунды не принимает подключения.
VERIFY_ATTEMPTS = 3
VERIFY_PAUSE_SEC = 1.0


class PrepareInstaller(PhasedInstaller):
    service = "prepare"
    recovery_limit = (
        "Созданные учётные записи, пароли и ключи автоматически не откатываются."
    )

    def _apply_install(self, device, ssh, run, write, append):
        from src.workers.setup.templates import PREPARE_SCRIPT

        plan = self.parameters.prepare
        payload = json.dumps(
            {
                "accounts": [
                    {"login": a.login, "password": a.password, "admin": a.admin}
                    for a in plan.accounts
                ],
                "root_password": plan.root_password,
                "root_keys": list(plan.root_keys),
                "permit_root": plan.permit_root,
            },
            ensure_ascii=False,
        )
        self.context.log(device.host, self.service, "Подготовка хоста")
        code, out, _ = run(
            "python3 -I -c " + shlex.quote(PREPARE_SCRIPT),
            timeout=120,
            stdin_data=payload,
            sensitive=True,
        )
        report = None
        if code == 0:
            try:
                report = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                report = None
        if not isinstance(report, dict):
            self._report(
                device,
                self.service,
                RESULT_ERROR,
                "Скрипт подготовки не выполнен с правами root: проверьте python3 "
                "на хосте, sudo и пароль связки входа",
            )
            return
        # Подробный отчёт - в результате сервиса (подробности хоста), в общий
        # журнал - только итог.
        lines = [str(item) for item in report.get("messages") or ()]
        lines += ["Внимание: " + str(item) for item in report.get("warnings") or ()]
        if not report.get("ok"):
            error = str(report.get("error") or "Подготовка не выполнена")
            self.context.log(device.host, self.service, "Ошибка: " + error)
            self._report(device, self.service, RESULT_ERROR, "\n".join(lines + [error]))
            return
        self._report(device, self.service, RESULT_SUCCESS, "\n".join(lines))

    def verify(self, request, applied):
        credentials = self._resolve_credentials(request.device, stage="after")
        error = None
        for attempt in range(VERIFY_ATTEMPTS):
            if self._local_abort.is_set():
                break
            error = self._try_login(request.device, request.ssh, credentials)
            if error is None:
                note = f"Проверочный вход под «{credentials.username}» выполнен"
                self.context.log(request.device.host, self.service, note)
                return PhaseResult(applied.code, applied.message + "\n" + note)
            if attempt + 1 < VERIFY_ATTEMPTS:
                time.sleep(VERIFY_PAUSE_SEC)
        return PhaseResult(
            RESULT_ERROR,
            applied.message
            + "\nПодготовка выполнена, но вход с выбранной связкой "
            + f"(«{credentials.username}») не удался: {error or 'прервано'}",
        )

    def _try_login(self, device, ssh, credentials):
        """Подключиться заново; None при успехе, иначе причина."""
        import paramiko

        client = paramiko.SSHClient()
        timer = threading.Timer(10, client.close)
        timer.daemon = True
        try:
            # Ключ хоста известен по рабочему соединению: подмена не пройдёт.
            host_key = ssh.get_transport().get_remote_server_key()
            port = device.port or self.parameters.ssh_port
            address = device.host if port == 22 else f"[{device.host}]:{port}"
            client.get_host_keys().add(address, host_key.get_name(), host_key)
            self.context.register_connection(client)
            timer.start()
            client.connect(
                device.host,
                port=port,
                username=credentials.username,
                password=credentials.password,
                pkey=credentials.private_key,
                allow_agent=False,
                look_for_keys=False,
                timeout=5,
                banner_timeout=5,
                auth_timeout=5,
            )
            return None
        except paramiko.AuthenticationException:
            return "неверный пароль или ключ"
        except Exception as error:
            return type(error).__name__
        finally:
            timer.cancel()
            client.close()
            self.context.unregister_connection(client)
