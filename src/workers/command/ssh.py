"""
SSH Worker - Исполнитель SSH команд.

Выполняет команды на удалённых хостах через SSH с поддержкой:
- Интерактивного вывода
- Таймаутов
- Прерывания выполнения
- Кодировки UTF-8/CP1251
"""

import socket
import time
from typing import Tuple, Optional
from src.config import config, SSH_RECV_BUFFER_SIZE
from src.domain.models.device import DeviceModel
from src.logger import logger
import paramiko

from .executor_base import BaseCommandExecutor
from src.domain.utils import command_param_replacer


class SSHWorker(BaseCommandExecutor):
    """
    Исполнитель SSH команд.

    Особенности:
    - Поддержка псевдо-терминала (PTY)
    - Обработка таймаутов
    - Автоматическая кодировка вывода
    - Прерывание выполнения
    """

    def execute(
        self,
        device: DeviceModel,
        command_text: str,
        timeout: int = config.app.ssh.command_timeout
    ) -> Tuple[str, bool]:
        """
        Выполнение SSH команды на удалённом хосте.

        Args:
            device: Устройство для подключения
            command_text: Текст команды
            timeout: Таймаут выполнения в секундах

        Returns:
            (вывод, успех)
        """
        if self.aborting:
            logger.info(f"[{device.host}] SSH выполнение прервано до подключения")
            return "Aborted by user", False

        output = []
        result = None
        logger.info(f"[{device.host}] Начало выполнения SSH команды")

        # Создание подключения
        try:
            client = self.get_client(device, timeout)
            transport = client.get_transport()
            transport.set_keepalive(15)
            session = self.get_session(client)
        except Exception as e:
            return str(e), False

        start_time = time.time()
        timed_out = False
        overall_timeout = timeout or config.app.ssh.command_timeout

        logger.info(f"[{device.host}] Выполнение команды: {command_text}")

        # Принудительная установка локали для кириллицы
        locale_cmd = (
            "export LANG=ru_RU.UTF-8; "
            "export LC_ALL=ru_RU.UTF-8; "
            "export LC_CTYPE=ru_RU.UTF-8; "
            f"{command_text}"
        )
        session.exec_command(locale_cmd)

        success = True

        try:
            while not self.aborting:
                # Проверка живости соединения
                if not transport.is_active():
                    error_msg = "SSH connection lost"
                    logger.error(f"[{device.host}] {error_msg}")
                    success = False
                    break

                # Проверка таймаута
                if time.time() - start_time > overall_timeout:
                    timed_out = True
                    error_msg = (
                        f"Command did not complete within {overall_timeout} seconds (timeout)"
                    )
                    logger.error(f"[{device.host}] {error_msg}")
                    break

                # Чтение вывода
                if session.recv_ready():
                    raw_data = session.recv(SSH_RECV_BUFFER_SIZE)
                    try:
                        data = raw_data.decode("utf-8")
                    except UnicodeDecodeError:
                        data = raw_data.decode("cp1251", errors="replace")
                        logger.warning(
                            f"[{device.host}] Не удалось декодировать вывод как UTF-8, "
                            f"используется CP1251"
                        )

                    output.append(data)
                    self.emit_progress(device, data)

                # Проверка завершения команды
                if session.exit_status_ready():
                    exit_status = session.recv_exit_status()
                    success = exit_status == 0
                    logger.info(f"[{device.host}] Команда завершилась с кодом: {exit_status}")
                    break

                time.sleep(0.1)

            # Обработка результатов
            if timed_out:
                session.close()
                error_msg = (
                    f"\nCommand execution timeout\n"
                    f"Command did not complete within {overall_timeout} seconds (timeout).\n"
                )
                logger.error(f"[{device.host}] {error_msg}")
                output.append(error_msg)
                self.emit_progress(device, error_msg)
                success = False
                result = (error_msg, False)

            elif self.aborting:
                error_msg = "\nAborted by user\n"
                logger.info(f"[{device.host}] {error_msg}")
                output.append(error_msg)
                self.emit_progress(device, error_msg)
                success = False
                result = (error_msg, False)

        except socket.timeout as e:
            error_msg = f"Connection timeout: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False
            result = (error_msg, False)

        except paramiko.AuthenticationException as e:
            error_msg = f"Authentication error: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False
            result = (error_msg, False)

        except paramiko.SSHException as e:
            error_msg = f"SSH error: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False
            result = (error_msg, False)

        except Exception as e:
            error_msg = f"Command execution error: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False
            result = (error_msg, False)

        finally:
            try:
                session.close()
                client.close()
                logger.info(f"[{device.host}] Соединение закрыто")
            except Exception as e:
                logger.warning(f"[{device.host}] Ошибка при закрытии соединения: {e}")

            if result is None:
                result = (''.join(output), success)

        return result
