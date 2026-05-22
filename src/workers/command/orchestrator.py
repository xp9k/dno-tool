"""
Command Worker - Оркестратор выполнения команд на устройствах.

Управляет выполнением команд на множестве устройств через пул потоков,
делегируя выполнение специализированным worker'ам:
- SSHWorker - SSH команды
- SFTPWorker - SFTP операции
- LocalWorker - Локальные команды

Архитектура:
- CommandWorker отвечает за оркестрацию и управление потоками
- CommandExecutor базовый класс для исполнителей
- Конкретные исполнители реализуют логику выполнения
"""

from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from PySide6.QtCore import Signal
from src.config import config
from src.domain.models.device import DeviceModel
from src.logger import logger
from src.domain.utils import command_param_replacer

from src.architecture import EventBus, EventType

from .executor_base import BaseCommandExecutor
from ..base import BaseWorker
from src.domain.models.command import Command, CommandType
from .ssh import SSHWorker
from .sftp import SFTPWorker
from .local import LocalWorker


class CommandWorker(BaseWorker):
    """
    Оркестратор выполнения команд на устройствах.

    Особенности:
    - Параллельное выполнение на множестве устройств
    - Поддержка прерывания выполнения
    - Интеграция с EventBus
    - Детальный прогресс выполнения
    """

    result_ready = Signal(DeviceModel, bool, str)
    progress_update = Signal(DeviceModel, str)
    device_started = Signal(DeviceModel)

    started = Signal()
    finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        devices: List[DeviceModel],
        commands: List[Command],
        timeout: int = config.app.ssh.command_timeout,
        params: Optional[Dict[str, Any]] = None,
        event_bus: Optional['EventBus'] = None
    ):
        super().__init__()
        self.devices = devices
        self.commands = commands
        self.timeout = timeout
        self.params = params or {}

        self.event_bus = event_bus

        self._active_executors: List[BaseCommandExecutor] = []
        self._executor_lock = threading.Lock()

    def execute(self):
        logger.info(f"CommandWorker: Запуск выполнения для {len(self.devices)} устройств")

        self._abort_event.clear()
        with self._executor_lock:
            self._active_executors.clear()

        self.started.emit()
        if self.event_bus:
            self.event_bus.publish_typed(
                EventType.COMMAND_STARTED,
                source='CommandWorker',
                data={
                    'device_count': len(self.devices),
                    'command_count': len(self.commands),
                    'timeout': self.timeout
                }
            )

        for device in self.devices:
            logger.info(
                f"Информация об устройстве - Хост: {device.host}, "
                f"Логин: {device.login}, Порт: {device.port or config.app.ssh.port}"
            )

        success_count = 0
        error_count = 0

        if self.is_aborting:
            logger.info("CommandWorker: Выполнение прервано до запуска")
            for device in self.devices:
                self.result_ready.emit(device, False, "Aborted by user")
                error_count += 1
            self.finished.emit()
            return

        with ThreadPoolExecutor(max_workers=config.app.network.thread_count) as executor:
            future_to_device = {}
            for device in self.devices:
                if self.is_aborting:
                    logger.info("CommandWorker: Прерывание - пропуск оставшихся устройств")
                    break

                future = executor.submit(
                    self._execute_on_device,
                    device,
                    self.commands,
                    self.timeout
                )
                future_to_device[future] = device

            for future in as_completed(future_to_device):
                device = future_to_device[future]
                try:
                    final_output, success = future.result()

                    if success:
                        success_count += 1
                        error_msg = final_output
                    else:
                        error_count += 1
                        error_msg = final_output if final_output else "Неизвестная ошибка"

                    logger.info(
                        f"Выполнение команд для {device.host} завершено - Успешно: {success}"
                    )

                    self.result_ready.emit(device, success, error_msg)

                except Exception as e:
                    error_count += 1
                    error_msg = f"Ошибка выполнения команд на {device.host}: {str(e)}"
                    logger.error(error_msg)
                    self.error.emit(error_msg)

                    self.result_ready.emit(device, False, error_msg)

                    if self.event_bus:
                        self.event_bus.publish_typed(
                            EventType.WORKER_ERROR,
                            source='CommandWorker',
                            data={'device': device.host, 'error': str(e)}
                        )

            if self.is_aborting:
                for future, device in future_to_device.items():
                    if not future.done():
                        self.result_ready.emit(device, False, "Aborted by user")
                        error_count += 1

        with self._executor_lock:
            self._active_executors.clear()

        logger.info("CommandWorker: Выполнение завершено")

        if self.event_bus:
            self.event_bus.publish_typed(
                EventType.COMMAND_FINISHED,
                source='CommandWorker',
                data={
                    'total_devices': len(self.devices),
                    'success_count': success_count,
                    'error_count': error_count
                }
            )

        self.finished.emit()

    def _execute_on_device(
        self,
        device: DeviceModel,
        commands: List[Command],
        timeout: int
    ) -> tuple[str, bool]:
        if self.is_aborting:
            logger.info(f"[{device.host}] Выполнение прервано")
            return "Aborted by user", False

        self.device_started.emit(device)

        ssh_worker = SSHWorker(progress_callback=self)
        sftp_worker = SFTPWorker(progress_callback=self)
        local_worker = LocalWorker(progress_callback=self)

        with self._executor_lock:
            self._active_executors.extend([ssh_worker, sftp_worker, local_worker])

        output = ""
        try:
            output_all = []
            success = True

            for cmd in commands:
                output = ""
                cmd_success = True

                command_text = command_param_replacer.replace(cmd.text, device, self.params)

                processed_cmd = Command({"type": cmd.commandType, "text": command_text})

                if self.event_bus:
                    self.event_bus.publish_typed(
                        EventType.COMMAND_STARTED,
                        source='CommandWorker',
                        data={
                            'device': device.host,
                            'command_type': cmd.commandType.value,
                            'command': command_text[:100]
                        }
                    )

                if cmd.commandType == CommandType.SSH:
                    output, cmd_success = ssh_worker.execute(
                        device, processed_cmd.text, timeout
                    )
                elif cmd.commandType == CommandType.SFTP:
                    output, cmd_success = sftp_worker.execute(
                        device, processed_cmd.text, timeout
                    )
                elif cmd.commandType == CommandType.LOCAL:
                    output, cmd_success = local_worker.execute(
                        device, processed_cmd.text, timeout
                    )
                else:
                    cmd_success = False
                    output = f"Неизвестный тип команды: {cmd.commandType}"

                output_all.append(output)
                success = success and cmd_success

                if not success:
                    logger.info(
                        f"[{device.host}] Остановка выполнения из-за ошибки в команде"
                    )
                    break

            final_output = '\n'.join(filter(None, output_all))
            return final_output, success

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(error_msg)
            self.progress_update.emit(device, error_msg)
            return error_msg, False

    def abort(self):
        self._abort_event.set()
        with self._executor_lock:
            for executor in self._active_executors:
                try:
                    executor.abort()
                except Exception as e:
                    logger.debug(f"CommandWorker: Error aborting executor: {e}")
            self._active_executors.clear()

        if self.event_bus:
            self.event_bus.publish_typed(
                EventType.COMMAND_ABORTED,
                source='CommandWorker',
                data={'aborted': True}
            )

        self.error.emit("Выполнение команд прервано")
        logger.info("CommandWorker: Флаг прерывания установлен и все executors прерваны")
