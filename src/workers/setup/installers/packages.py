"""Independent installer using the operation context supplied by the worker."""

import shlex
from typing import List
from src.config import PACKAGE_POST_INSTALL
from src.workers.setup.types import (
    RESULT_SUCCESS,
    RESULT_ERROR,
    RESULT_IGNORE,
    RESULT_ABORT,
)
from src.workers.setup.phases import PhasedInstaller, PhaseResult
from src.workers.setup.packages import command_error


class PackagesInstaller(PhasedInstaller):
    service = "packages"
    recovery_limit = (
        "Установленные пакеты и выполненный post-install автоматически не откатываются."
    )

    def _apply_install(self, device, ssh, run, write, append):
        self._install_packages(device, run)

    def _fail(self, device, svc, output_lines, message) -> None:
        """Ошибка шага: причина в журнал установки и в отчёт хоста."""
        output_lines.append(message)
        self.context.log(device.host, svc, f"Ошибка: {message}")
        self._report(device, svc, RESULT_ERROR, "\n".join(output_lines))

    def verify(self, request, applied):
        from src.workers.setup.packages import PackageManager

        manager = PackageManager(request.run, self._local_abort)
        absent = [
            p
            for p in getattr(self, "_expected_packages", ())
            if not manager.installed(p)
        ]
        if absent:
            message = "Не установились: " + ", ".join(absent)
            self.context.log(request.device.host, self.service, f"Ошибка: {message}")
            return PhaseResult(RESULT_ERROR, message)
        return applied

    def _post_install_packages(
        self, device, run_cmd, packages, output_lines, installed_now=()
    ):
        """Выполнить команды настройки пакетов.

        Отказ - ошибка шага только для пакетов из ``installed_now`` (поставил
        этот запуск). Пакет, стоявший раньше, обычно уже настроен (повторный
        ``mos-auth-config enable`` отвечает «Уже настроено»): его отказ только
        записывается в подробности.
        """
        for package in packages:
            command = PACKAGE_POST_INSTALL.get(package)
            if command:
                code, out, err = run_cmd(command, timeout=60)
                if code and package not in installed_now:
                    output_lines.append(
                        f"{package}: {command} - {command_error(code, out, err, 60)}"
                    )
                elif code:
                    self._fail(
                        device,
                        "packages",
                        output_lines,
                        f"Не удалось настроить установленный пакет {package}: "
                        + command_error(code, out, err, 60),
                    )
                    return False
        return True

    def _install_packages(self, device, run_cmd) -> None:
        """Установить дополнительные пакеты на хост (единая колонка «Пакеты»).

        Список пакетов ``self.parameters.extra_packages`` собирается мастером настройки
        (чекбоксы из ``EXTRA_PACKAGES`` + ручной ввод пользователя). Пакетный
        менеджер определяется на хосте (dnf -> apt-get); проверка наличия -
        ``rpm -q`` (dnf) или ``dpkg -s`` (apt). Пакеты, отсутствующие в
        репозитории, пропускаются с предупреждением (не фатально).
        Недостающие доступные устанавливаются одной командой
        ``dnf/apt-get install -y``; при ее падении - добивка по одному пакету.
        Причины отказов (хвост вывода dnf) идут и в отчёт, и в журнал установки.
        """
        from src.workers.setup.packages import PackageManager, INSTALL_TIMEOUT_SEC

        svc = "packages"
        output_lines: List[str] = []
        log = output_lines.append

        self.context.log(device.host, svc, "Начало установки пакетов")

        if self._local_abort.is_set():
            self._report(device, svc, RESULT_ABORT, "Прервано")
            return

        packages = self.parameters.extra_packages
        try:
            self._validate_package_names(packages)
        except ValueError:
            self._fail(device, svc, output_lines, "Недопустимое имя пакета")
            return
        if not packages:
            self.context.log(device.host, svc, "Пакеты не выбраны (пропуск)")
            self._report(device, svc, RESULT_IGNORE, "Пакеты не выбраны")
            return

        manager = PackageManager(run_cmd, self._local_abort)
        try:
            pkg_mgr = manager.detect()
        except RuntimeError as error:
            self._fail(device, svc, output_lines, str(error))
            return
        log(f"Менеджер пакетов: {pkg_mgr}")

        # 1. Проверка наличия каждого пакета (rpm -q / dpkg -s).
        missing = []
        for pkg in packages:
            if self._local_abort.is_set():
                self._report(device, svc, RESULT_ABORT, "Прервано")
                return
            if manager.installed(pkg):
                log(f"{pkg} уже установлен")
            else:
                missing.append(pkg)

        self._expected_packages = tuple(packages)
        if not missing:
            self._post_install_packages(device, run_cmd, packages, output_lines)
            self.context.log(device.host, svc, "Все пакеты уже установлены")
            self._report(device, svc, RESULT_IGNORE, "\n".join(output_lines))
            return

        # 1b. Проверка наличия в репозитории: отсутствующие имена пропускаем
        # с предупреждением (не фатально), чтобы один неверный пакет не
        # ронял установку остальных.
        try:
            available, unavailable = manager.available(missing)
        except RuntimeError as error:
            self._fail(device, svc, output_lines, str(error))
            return

        self._expected_packages = tuple(p for p in packages if p not in unavailable)
        for pkg in unavailable:
            log(f"{pkg} нет в репозитории - пропуск")
        if unavailable:
            self.context.log(
                device.host, svc, f"Нет в репозитории: {', '.join(unavailable)}"
            )
        missing = available
        present = [p for p in packages if p not in unavailable]
        if not missing:
            self._post_install_packages(device, run_cmd, present, output_lines)
            log("Нет пакетов для установки")
            self._report(device, svc, RESULT_IGNORE, "\n".join(output_lines))
            return

        # 2. Установка недостающих пакетов (одной командой).
        pkgs_str = shlex.join(missing)
        log(f"Устанавливаю: {pkgs_str}")
        self.context.log(device.host, svc, f"Установка: {pkgs_str}")
        code, out, err = manager.install(missing)
        if code != 0:
            # Добивка по одному пакету: изолировать виновника и поставить
            # то, что ставится.
            reason = command_error(code, out, err, INSTALL_TIMEOUT_SEC)
            log(f"Общая установка не удалась: {reason}")
            log("Пробую установить по одному пакету...")
            failed = []
            for pkg in list(missing):
                if self._local_abort.is_set():
                    self._report(device, svc, RESULT_ABORT, "Прервано")
                    return
                icode, iout, ierr = manager.install((pkg,))
                if icode != 0:
                    reason = command_error(icode, iout, ierr, INSTALL_TIMEOUT_SEC)
                    failed.append(pkg)
                    log(f"{pkg}: ошибка: {reason}")
                    self.context.log(device.host, svc, f"{pkg}: {reason}")
                else:
                    log(f"{pkg}: установлен")
            if failed:
                self._fail(
                    device, svc, output_lines, f"Не установились: {', '.join(failed)}"
                )
                return
            log("Все пакеты установлены по одному")

        self.context.log(device.host, svc, f"Установлено: {', '.join(missing)}")

        if not self._post_install_packages(
            device, run_cmd, present, output_lines, installed_now=missing
        ):
            return

        self._report(device, svc, RESULT_SUCCESS, "\n".join(output_lines))
