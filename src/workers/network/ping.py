"""
Network workers — пинг и сетевые операции.

Включает:
- PingWorker: пинг одного хоста
- BatchPingWorker: параллельный пинг группы хостов с семафором
- BatchPingWorker: параллельный пинг группы хостов
"""

from PySide6.QtCore import QThread, Signal, QObject
import socket
import threading
from typing import List, Optional, Dict, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from ..base import BaseWorker
from src.config import config
from src.domain.models.device import DeviceModel
from src.logger import logger

# ARCHITECTURE: Импорт EventBus для публикации событий
from src.architecture import EventBus, EventType


class PingWorker(BaseWorker):
    """Worker для пинга одного хоста"""
    
    result_ready = Signal(bool)  # online status
    
    # ARCHITECTURE: Сигналы для интеграции с WorkerBridge
    started = Signal()
    progress_update = Signal(int)  # Прогресс 0-100

    def __init__(self, device: DeviceModel, port: int = 22, timeout: float = None, event_bus: 'EventBus' = None):
        super().__init__()
        self.device = device
        self.port = port
        self.timeout = timeout or config.app.network.ping_timeout
        
        # ARCHITECTURE: EventBus (инжектируется извне)
        self.event_bus = event_bus

    def execute(self):
        """Выполнение пинга с EventBus событиями"""
        # ARCHITECTURE: Публикуем событие о запуске
        self.started.emit()
        if self.event_bus:

            self.event_bus.publish_typed(
            EventType.WORKER_STARTED,
            source='PingWorker',
            data={
                'device': self.device.host,
                'worker_type': 'PingWorker',
                'port': self.port
            }
        )

        try:
            is_online = self.ping_host(self.device.host)
            self.result_ready.emit(is_online)
            self.progress_update.emit(100)

            # ARCHITECTURE: Публикуем событие о результате
            if self.event_bus:

                self.event_bus.publish_typed(
                EventType.WORKER_FINISHED,
                source='PingWorker',
                data={
                    'device': self.device.host,
                    'online': is_online,
                    'worker_type': 'PingWorker'
                }
            )
        except Exception as e:
            error_msg = f"Error pinging {self.device.host}: {str(e)}"
            self.error.emit(error_msg)
            
            # ARCHITECTURE: Публикуем событие об ошибке
            if self.event_bus:

                self.event_bus.publish_typed(
                EventType.WORKER_ERROR,
                source='PingWorker',
                data={'device': self.device.host, 'error': str(e)}
            )

        self.finished.emit()

    def ping_host(self, host: str) -> bool:
        """Пинг одного хоста через TCP подключение"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            result = sock.connect_ex((host, self.port))
            return (result == 0)
        except Exception as e:
            logger.debug(f"PingWorker: Error pinging {host}: {e}")
            return False
        finally:
            sock.close()


class PingThread(QThread):
    online = Signal(bool)

    def __init__(self, device: DeviceModel, port: int = 22, timeout: float = None, parent=None):
        super().__init__(parent)
        self.device = device
        self.port = port
        self.timeout = timeout
        self.worker = PingWorker(device, port, timeout)
        self.worker.moveToThread(self)
        self.worker.result_ready.connect(self.online.emit)

    def run(self):
        self.worker.execute()


class BatchPingWorker(BaseWorker):
    """
    Параллельный пинг группы устройств с ограничением одновременных операций.
    
    Использует семафор для контроля числа одновременных подключений.
    """
    
    result_ready = Signal(DeviceModel, bool)  # device, online status
    progress_update = Signal(int, int)  # current, total
    device_ping_complete = Signal(DeviceModel, bool)  # device, success
    
    # ARCHITECTURE: Сигналы для WorkerBridge
    started = Signal()

    def __init__(
        self,
        devices: List[DeviceModel],
        port: int = 22,
        timeout: float = None,
        max_concurrent: int = 10,
        event_bus: 'EventBus' = None
    ):
        super().__init__()
        self.devices = devices
        self.port = port
        self.timeout = timeout or config.app.network.ping_timeout
        self.max_concurrent = max_concurrent
        self._abort_event = threading.Event()
        
        # Снимок устройств на момент запуска
        self._devices_snapshot = {d.host: d for d in devices}
        self._aborted_hosts: Set[str] = set()
        
        # ARCHITECTURE: EventBus (инжектируется извне)
        self.event_bus = event_bus

    def execute(self):
        """Параллельный пинг всех устройств"""
        total = len(self.devices)
        
        # ARCHITECTURE: Публикуем событие о запуске
        self.started.emit()
        if self.event_bus:

            self.event_bus.publish_typed(
            EventType.WORKER_STARTED,
            source='BatchPingWorker',
            data={
                'device_count': total,
                'worker_type': 'BatchPingWorker',
                'max_concurrent': self.max_concurrent
            }
        )

        if total == 0:
            self.finished.emit()
            return

        online_count = 0
        completed_count = 0
        
        # Используем ThreadPoolExecutor для параллельного выполнения
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            # Создаем futures для всех устройств
            futures = {
                executor.submit(self._ping_with_semaphore, device): device
                for device in self.devices
            }
            
            # Обрабатываем по мере завершения
            for future in as_completed(futures):
                if self.is_aborting:
                    logger.info("BatchPingWorker: Aborted, stopping remaining pings")
                    break
                
                device = futures[future]
                completed_count += 1
                
                try:
                    is_online = future.result()
                    if is_online:
                        online_count += 1
                    
                    # Эмитим результат
                    self.result_ready.emit(device, is_online)
                    self.device_ping_complete.emit(device, is_online)
                    self.progress_update.emit(completed_count, total)
                    
                    # ARCHITECTURE: Публикуем событие о прогрессе
                    if self.event_bus:

                        self.event_bus.publish_typed(
                        EventType.WORKER_PROGRESS,
                        source='BatchPingWorker',
                        data={
                            'device': device.host,
                            'online': is_online,
                            'progress': completed_count,
                            'total': total,
                            'online_count': online_count
                        }
                    )
                    
                except Exception as e:
                    logger.error(f"BatchPingWorker: Error pinging {device.host}: {e}")
                    self.result_ready.emit(device, False)
                    
                    # ARCHITECTURE: Публикуем событие об ошибке
                    if self.event_bus:

                        self.event_bus.publish_typed(
                        EventType.WORKER_ERROR,
                        source='BatchPingWorker',
                        data={'device': device.host, 'error': str(e)}
                    )

        # ARCHITECTURE: Публикуем событие о завершении
        if self.event_bus:

            self.event_bus.publish_typed(
            EventType.WORKER_FINISHED,
            source='BatchPingWorker',
            data={
                'total': total,
                'online_count': online_count,
                'offline_count': total - online_count,
                'completed_count': completed_count
            }
        )

        self.finished.emit()

    def _ping_with_semaphore(self, device: DeviceModel) -> bool:
        """Пинг устройства с проверкой отмены"""
        if device.host in self._aborted_hosts:
            logger.debug(f"BatchPingWorker: Ping aborted for {device.host}")
            return False
        
        return self._ping_device_internal(device)

    def _ping_device_internal(self, device: DeviceModel) -> bool:
        """Внутренний пинг устройства"""
        logger.debug(f"BatchPingWorker: Pinging {device.host}:{self.port} (timeout={self.timeout}s)")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            result = sock.connect_ex((device.host, self.port))
            is_success = (result == 0)
            logger.debug(f"BatchPingWorker: {device.host} result={result} -> {'ONLINE' if is_success else 'OFFLINE'}")
            return is_success
        except Exception as e:
            logger.debug(f"BatchPingWorker: Error pinging {device.host}: {e}")
            return False
        finally:
            sock.close()

    def abort(self):
        self._abort_event.set()
        logger.info("BatchPingWorker: Abort requested")

    def abort_device(self, host: str):
        """
        Мягкая отмена пинга для конкретного устройства.
        
        Устройство будет пропущено если еще не началось.
        """
        self._aborted_hosts.add(host)
        logger.debug(f"BatchPingWorker: Ping aborted for specific host {host}")

    def get_aborted_hosts(self) -> Set[str]:
        """Получить множество отмененных хостов"""
        return self._aborted_hosts.copy()



