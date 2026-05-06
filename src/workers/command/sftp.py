"""
SFTP Worker - Исполнитель SFTP операций.

Выполняет операции передачи файлов через SFTP с поддержкой:
- Загрузки файлов с удалённого хоста
- Выгрузки файлов на удалённый хост
- Прогресс передачи
- Таймаутов и прерываний
"""

import os
import stat
import socket
import time
from typing import Tuple, Optional
from src.config import config
from src.domain.models.device import DeviceModel
from src.logger import logger
import paramiko

from .executor_base import BaseCommandExecutor, get_credentials
from src.domain.utils import command_param_replacer


class SFTPWorker(BaseCommandExecutor):
    """
    Исполнитель SFTP операций.

    Особенности:
    - Поддержка upload/download
    - Прогресс передачи
    - Проверка типов файлов (не поддерживает директории)
    - Прерывание передачи
    """

    def execute(
        self,
        device: DeviceModel,
        command_text: str,
        timeout: int = config.app.ssh.command_timeout
    ) -> Tuple[str, bool]:
        """
        Выполнение SFTP операции.

        Формат команды: source::destination
        Где source и destination могут содержать плейсхолдеры.

        Args:
            device: Устройство для подключения
            command_text: Текст команды в формате source::destination
            timeout: Таймаут выполнения в секундах

        Returns:
            (вывод, успех)
        """
        if self.aborting:
            logger.info(f"[{device.host}] SFTP выполнение прервано до подключения")
            return "Aborted by user", False

        output = []
        result = None
        logger.info(f"[{device.host}] Начало SFTP операции")

        # Создание подключения
        try:
            client = self.get_client(device, timeout)
        except socket.timeout as e:
            error_msg = f"Connection timeout: не удалось подключиться к {device.host} за {config.app.ssh.connect_timeout}с"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            return error_msg, False
        except ConnectionRefusedError as e:
            error_msg = f"Connection refused: подключение отклонено хостом {device.host}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            return error_msg, False
        except ConnectionError as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            return error_msg, False
        except paramiko.AuthenticationException as e:
            error_msg = f"Ошибка аутентификации: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            return error_msg, False
        except Exception as e:
            error_msg = str(e)
            err_lower = error_msg.lower()
            if any(kw in err_lower for kw in ('timed out', 'timeout', 'время ожидания')):
                error_msg = f"Connection timeout: не удалось подключиться к {device.host} за {config.app.ssh.connect_timeout}с"
            elif any(kw in err_lower for kw in ('refused', 'отклонено')):
                error_msg = f"Connection refused: подключение отклонено хостом {device.host}"
            elif any(kw in err_lower for kw in ('unreachable', 'no route', 'недоступен')):
                error_msg = f"Host unreachable: хост {device.host} недоступен"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            return error_msg, False

        try:
            # Парсинг команды
            parts = command_text.split('::')

            if len(parts) != 2:
                error_msg = "Неверный формат команды SFTP"
                logger.error(f"[{device.host}] {error_msg}")
                self.emit_progress(device, error_msg)
                return error_msg, False

            # Замена параметров
            source = command_param_replacer.replace_for_device(parts[0], device)
            destination = command_param_replacer.replace_for_device(parts[1], device)

            logger.info(
                f"[{device.host}] SFTP передача - "
                f"Источник: {source}, Назначение: {destination}"
            )

            # Открытие SFTP сессии
            sftp = client.open_sftp()
            start_time = time.time()

            def progress_callback(transferred: int, total: int):
                """Callback для отслеживания прогресса."""
                if total > 0:
                    percentage = (transferred / total) * 100
                    progress_msg = (
                        f"Передано: {transferred}/{total} байт ({percentage:.1f}%)\n"
                    )
                    output.append(progress_msg)
                    self.emit_progress(device, progress_msg)
                    logger.debug(f"[{device.host}] SFTP прогресс: {percentage:.1f}%")

                # Проверка прерывания или таймаута
                if self.aborting or (time.time() - start_time > timeout):
                    error_msg = (
                        "Передача прервана" if self.aborting
                        else "Перевышено время ожидания"
                    )
                    logger.error(f"[{device.host}] {error_msg}")
                    raise Exception(error_msg)

            # Определение направления передачи
            creds = get_credentials(device)
            remote_prefix = f"{creds.username}@{device.host}:"
            is_upload = not source.startswith(remote_prefix)

            if is_upload:
                # Загрузка на удалённый хост (Local -> Remote)
                destination = destination[len(remote_prefix):]
                result = self._upload_file(
                    sftp, device, source, destination, progress_callback, output
                )
            else:
                # Скачивание с удалённого хоста (Remote -> Local)
                # Удаляем префикс user@host: из пути
                source = source[len(remote_prefix):]
                result = self._download_file(
                    sftp, device, source, destination, progress_callback, output
                )

            success = result[1] if result else True

        except paramiko.AuthenticationException as e:
            error_msg = f"Ошибка аутентификации: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False

        except paramiko.SSHException as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ('session', 'channel', 'transport', 'connection')):
                error_msg = f"Connection lost: потеря связи с хостом {device.host} ({str(e)})"
            else:
                error_msg = f"Ошибка SSH: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False

        except socket.timeout as e:
            error_msg = f"Connection lost: потеря связи с хостом {device.host} (timeout)"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False

        except (ConnectionError, OSError) as e:
            error_msg = f"Connection lost: потеря связи с хостом {device.host} ({str(e)})"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False

        except Exception as e:
            error_msg = f"Ошибка передачи файлов: {str(e)}"
            logger.error(f"[{device.host}] {error_msg}")
            self.emit_progress(device, error_msg)
            success = False

        finally:
            try:
                if 'sftp' in locals():
                    sftp.close()
                client.close()
                logger.info(f"[{device.host}] SFTP соединение закрыто")
            except Exception as e:
                logger.warning(f"[{device.host}] Ошибка при закрытии SFTP соединения: {e}")

            if result is None:
                if success:
                    success_msg = "Передача SFTP успешно завершена\n"
                    logger.info(f"[{device.host}] {success_msg}")
                    output.append(success_msg)
                    self.emit_progress(device, success_msg)
                result = (''.join(output), success)

        return result

    def _upload_file(
        self,
        sftp: paramiko.SFTPClient,
        device: DeviceModel,
        local_path: str,
        remote_path: str,
        progress_callback: callable,
        output: list
    ) -> Tuple[str, bool]:
        """
        Загрузка файла на удалённый хост.

        Args:
            sftp: SFTP клиент
            device: Устройство
            local_path: Локальный путь
            remote_path: Удалённый путь
            progress_callback: Callback для прогресса
            output: Список для накопления вывода

        Returns:
            (вывод, успех)
        """
        if os.path.isdir(local_path):
            error_msg = (
                f"Локальный путь {local_path} является директорией. "
                f"Передача директорий не поддерживается."
            )
            logger.error(f"[{device.host}] {error_msg}")
            output.append(error_msg + "\n")
            self.emit_progress(device, error_msg)
            return error_msg, False

        logger.info(f"[{device.host}] Передача из {local_path} в {remote_path}")
        output.append(f"Передача из {local_path} в {remote_path}\n")
        self.emit_progress(device, f"Передача из {local_path} в {remote_path}\n")

        sftp.put(local_path, remote_path, callback=progress_callback)
        return None, True

    def _download_file(
        self,
        sftp: paramiko.SFTPClient,
        device: DeviceModel,
        remote_path: str,
        local_path: str,
        progress_callback: callable,
        output: list
    ) -> Tuple[str, bool]:
        """
        Скачивание файла с удалённого хоста.

        Args:
            sftp: SFTP клиент
            device: Устройство
            remote_path: Удалённый путь
            local_path: Локальный путь
            progress_callback: Callback для прогресса
            output: Список для накопления вывода

        Returns:
            (вывод, успех)
        """
        # Очистка пути от возможных wildcard символов
        remote_path_clean = remote_path.rstrip('*?[]')
        
        try:
            attr = sftp.stat(remote_path_clean)
        except FileNotFoundError:
            # Пробуем列出 содержимое директории для отладки
            try:
                parent_dir = os.path.dirname(remote_path_clean)
                filename = os.path.basename(remote_path_clean)
                files_in_dir = sftp.listdir(parent_dir)
                logger.error(
                    f"[{device.host}] Файл '{filename}' не найден в {parent_dir}. "
                    f"Доступные файлы: {files_in_dir}"
                )
            except Exception as list_err:
                logger.error(
                    f"[{device.host}] Ошибка при listing директории: {list_err}"
                )
            
            error_msg = f"Удалённый файл {remote_path} не найден"
            logger.error(f"[{device.host}] {error_msg}")
            output.append(error_msg + "\n")
            self.emit_progress(device, error_msg)
            return error_msg, False

        if stat.S_ISDIR(attr.st_mode):
            error_msg = (
                f"Удалённый путь {remote_path} является директорией. "
                f"Передача директорий не поддерживается."
            )
            logger.error(f"[{device.host}] {error_msg}")
            output.append(error_msg + "\n")
            self.emit_progress(device, error_msg)
            return error_msg, False

        logger.info(f"[{device.host}] Загрузка {remote_path} в {local_path}")
        output.append(f"Загрузка {remote_path} в {local_path}\n")
        self.emit_progress(device, f"Загрузка {remote_path} в {local_path}\n")

        sftp.get(remote_path, local_path, callback=progress_callback)
        return None, True
