"""
KeyInstallerWorker — worker для установки SSH ключей на устройства.

Интегрирован с worker_pool для эффективного управления потоками.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import Signal
from .base import BaseWorker
from typing import List, Dict, Any
from dataclasses import dataclass
import threading

from src.logger import logger
from src.domain.models.device import DeviceModel
from src.config import config
from src.workers.command.executor_base import get_credentials
import os

RESULT_SUCCESS = 0
RESULT_ERROR = 1
RESULT_IGNORE = 2
RESULT_ABORT = 3


class KeyInstallerWorker(BaseWorker):
    """Worker для установки SSH ключей"""
    
    log_signal = Signal(str)
    result_signal = Signal(DeviceModel, int, str)
    error = Signal(str)
    
    # Сигналы для WorkerBridge
    started = Signal()
    progress_update = Signal(object)

    def __init__(
            self,
            devices: List[DeviceModel],
            key_path: str = None,
            parent=None):
        super().__init__()

        self.devices = devices
        self.key_path = key_path
        self._local_abort = threading.Event()

    def execute(self):
        """Установка ключа на всех хостах"""
        logger.info(f"KeyInstallerWorker: Starting for {len(self.devices)} devices")

        self.started.emit()
        
        with ThreadPoolExecutor(max_workers=config.app.network.thread_count) as executor:
            future_to_device = {}
            for device in self.devices:
                if self._local_abort.is_set():
                    break

                def install_for_device(dev):
                    if self._local_abort.is_set():
                        return dev, RESULT_ABORT, "Aborted"
                    _, result, output = self.install_key(dev, self.key_path)
                    return dev, result, output

                future = executor.submit(install_for_device, device)
                future_to_device[future] = device

            # Обработка результатов по мере завершения (as_completed)
            for future in as_completed(future_to_device):
                if self._local_abort.is_set():
                    break

                device = future_to_device[future]
                try:
                    result = future.result()
                    if isinstance(result, tuple) and len(result) == 3:
                        device_result, result_code, output = result
                        logger.info(f"KeyInstallerWorker: Result for {device_result.host}: {result_code}")
                        self.result_signal.emit(device_result, result_code, output)
                    else:
                        logger.error(f"KeyInstallerWorker: Invalid result format for {device.host}")
                        self.result_signal.emit(device, RESULT_ERROR, "Invalid result format")
                except Exception as e:
                    error_msg = f"Error installing key on {device.host}: {str(e)}"
                    logger.error(error_msg)
                    self.error.emit(error_msg)
                    self.result_signal.emit(device, RESULT_ERROR, error_msg)

        logger.info("KeyInstallerWorker: Completed")
        self.finished.emit()


    def install_key(self, device: DeviceModel, key_path: str = None):
        if self._local_abort.is_set():
            return device.host, RESULT_ABORT, "Установка ключа прервана пользователем"
        return self._install_key_paramiko(device, key_path)
        

    def _install_key_paramiko(self, device: DeviceModel, key_path: str = None):
        import paramiko

        output = ''
        result_code = RESULT_SUCCESS

        creds = get_credentials(device)

        if not device.host:
            output = f"Не указан host для устройства: {getattr(device, 'name', str(device))}"
            self.log_signal.emit(output)
            result_code = RESULT_ERROR
            return device.host, result_code, output
        try:
            with open(key_path, 'r', encoding='utf-8') as f:
                pubkey = f.read().strip()

            port = device.port or config.app.ssh.port
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                ssh.connect(hostname=device.host,
                            username=creds.username,
                            password=creds.password,
                            pkey=creds.private_key,
                            port=port,
                            timeout=15)
                sftp = ssh.open_sftp()
                try:
                    sftp.mkdir('.ssh')
                except IOError:
                    pass
                try:
                    with sftp.open('.ssh/authorized_keys', 'r') as ak_file:
                        existing = ak_file.read().decode('utf-8')
                except IOError:
                    existing = ''
                if pubkey not in existing:
                    with sftp.open('.ssh/authorized_keys', 'a') as ak_file:
                        ak_file.write(pubkey + '\n')
                    result_code = RESULT_SUCCESS
                    output = f"Ключ добавлен на {device.host}"
                    self.log_signal.emit(output)
                else:
                    result_code = RESULT_IGNORE
                    output = f"Ключ уже есть на {device.host}"
                    self.log_signal.emit(output)
                sftp.close()
                ssh.close()
            except Exception as e:
                output = str(e)
                result_code = RESULT_ERROR

        except Exception as e:
            output = str(e)
            result_code = RESULT_ERROR

        return device.host, result_code, output
    

    def abort(self):
        self._local_abort.set()
        self._abort_event.set()
        logger.info("KeyInstallerWorker: Abort requested")