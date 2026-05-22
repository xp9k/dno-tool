"""Исполнитель локальных команд.

Выполняет команды на локальном хосте через subprocess с поддержкой
потокового вывода stdout, обработки stderr, таймаутов выполнения
и прерывания процесса пользователем."""

import subprocess
import time
from typing import Tuple, Optional
from src.config import config
from src.domain.models.device import DeviceModel
from src.logger import logger

from .executor_base import BaseCommandExecutor
from src.domain.utils import command_param_replacer


class LocalWorker(BaseCommandExecutor):
    """
    Исполнитель локальных команд.

    Особенности:
    - Потоковый вывод stdout
    - Обработка stderr
    - Поддержка таймаутов
    - Прерывание процесса
    """

    def execute(
        self,
        device: DeviceModel,
        command_text: str,
        timeout: int = config.app.ssh.command_timeout
    ) -> Tuple[str, bool]:
        """
        Выполнение локальной команды.

        Args:
            device: Устройство (используется для замены параметров)
            command_text: Текст команды
            timeout: Таймаут выполнения в секундах

        Returns:
            (вывод, успех)
        """
        if self.aborting:
            logger.info(f"[{device.host}] Выполнение локальной команды прербано")
            return "Aborted by user", False

        cmd_success = False
        result = None

        try:
            # Замена параметров в команде
            command_text = command_param_replacer.replace_for_device(command_text, device)
            logger.info(f"[{device.host}] Запуск локальной команды: {command_text}")

            output = []

            # Запуск процесса
            proc = subprocess.Popen(
                command_text,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            start_time = time.time()

            logger.debug(f"[{device.host}] Локальная команда запущена")

            # Чтение stdout построчно
            for line in iter(proc.stdout.readline, ""):
                if self.aborting:
                    logger.info(f"[{device.host}] Локальная команда прервана пользователем")
                    proc.kill()
                    output.append("\nAborted by user\n")
                    break

                if timeout and time.time() - start_time > timeout:
                    error_msg = (
                        f"Локальная команда превысила время ожидания {timeout} секунд"
                    )
                    logger.error(f"[{device.host}] {error_msg}")
                    raise Exception(error_msg)

                logger.debug(f"[{device.host}] {line}")
                output.append(line)

                # Отправка текущего фрагмента вывода
                self.emit_progress(device, line)

            # Дождаться завершения процесса перед проверкой returncode
            proc.wait()

            # Чтение stderr
            stderr = proc.stderr.read()

            # Проверка кода завершения (returncode может быть None, если процесс не завершился)
            returncode = proc.returncode
            cmd_success = returncode == 0 if returncode is not None else False

            if cmd_success:
                logger.info(f"[{device.host}] Локальная команда успешно завершена с кодом {returncode}")
            else:
                logger.error(
                    f"[{device.host}] Локальная команда завершилась с кодом {returncode}"
                )
                if stderr:
                    logger.error(f"[{device.host}] Ошибка вывода: {stderr}")
                    output.append(f"STDERR: {stderr}\n")

        except subprocess.TimeoutExpired:
            error_msg = (
                f"Локальная команда превысила время ожидания {timeout} секунд"
            )
            logger.error(f"[{device.host}] {error_msg}")
            cmd_success = False
            output.append(error_msg)
            self.emit_progress(device, error_msg)

            # Завершение процесса при таймауте
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception as e:
                logger.warning(f"[{device.host}] Ошибка при завершении процесса: {e}")

        except Exception as e:
            error_msg = f"Ошибка выполнения локальной команды: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            cmd_success = False
            output.append(error_msg)
            self.emit_progress(device, error_msg)

        finally:
            logger.info(f"[{device.host}] Локальная команда завершена")
            result = (''.join(output), cmd_success)

        return result

    def abort(self):
        """
        Прервать выполнение локальной команды.

        Переопределено для дополнительной логики при необходимости.
        """
        super().abort()
