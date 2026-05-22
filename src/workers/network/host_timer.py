"""Индивидуальные таймеры пинга для хостов и менеджер таймеров.

HostPingTimer создаёт QTimer для каждого отмеченного хоста и периодически
опрашивает его через централизованный WorkerPoolManager. HostPingTimerManager
— синглтон, владеющий всеми таймерами, эмитит сигнал online_updated(DeviceModel, bool)
для подписчиков UI. Поддерживает ref counting: таймер удаляется, когда последний
подписчик вызывает stop_ping."""

from PySide6.QtCore import QObject, QTimer, Signal
from typing import Optional, Callable
import random
import socket
import time
import threading

from src.config import config
from src.domain.models.device import DeviceModel
from src.logger import logger


class HostPingTimer(QObject):
    """
    Индивидуальный таймер пинга для одного хоста.

    Создает собственный QTimer, который с заданной периодичностью
    отправляет задачу пинга в WorkerPoolManager.
    Результат пинга передаёт через callback менеджеру, не эмитит собственный сигнал.
    """

    _ping_result_ready = Signal(bool)

    MAX_CONCURRENT_PER_HOST = 1

    def __init__(
        self,
        device: DeviceModel,
        on_result: Callable[[DeviceModel, bool], None],
        ping_interval: Optional[int] = None,
        ping_timeout: Optional[float] = None,
        ping_port: Optional[int] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)

        self._device = device
        self._host = device.host
        self._ping_interval = (ping_interval or config.app.network.ping_interval) * 1000
        self._ping_timeout = ping_timeout or config.app.network.ping_timeout
        self._ping_port = ping_port or config.app.ssh.port
        self._on_result = on_result

        self._timer = QTimer()
        self._timer.timeout.connect(self._execute_ping)
        self._timer.setInterval(self._ping_interval)

        self._is_running = False
        self._pending_ping = False
        self._lock = threading.Lock()

        self._ping_result_ready.connect(self._handle_ping_result)

        logger.debug(f"HostPingTimer: Created for {self._host} (interval={self._ping_interval}ms, port={self._ping_port})")

    @property
    def host(self) -> str:
        return self._host

    @property
    def device(self) -> DeviceModel:
        return self._device

    @property
    def is_running(self) -> bool:
        return self._is_running

    def update_settings(
        self,
        ping_interval: Optional[int] = None,
        ping_timeout: Optional[float] = None,
        ping_port: Optional[int] = None
    ) -> None:
        settings_changed = False

        if ping_interval is not None:
            new_interval = ping_interval * 1000
            if new_interval != self._ping_interval:
                self._ping_interval = new_interval
                self._timer.setInterval(self._ping_interval)
                settings_changed = True
                logger.debug(f"HostPingTimer: Updated interval to {self._ping_interval}ms for {self._host}")

        if ping_timeout is not None:
            if ping_timeout != self._ping_timeout:
                self._ping_timeout = ping_timeout
                settings_changed = True
                logger.debug(f"HostPingTimer: Updated timeout to {self._ping_timeout}s for {self._host}")

        if ping_port is not None:
            if ping_port != self._ping_port:
                self._ping_port = ping_port
                settings_changed = True
                logger.debug(f"HostPingTimer: Updated port to {self._ping_port} for {self._host}")

        return settings_changed

    def reset_timer(self) -> None:
        if self._is_running:
            was_active = self._timer.isActive()
            self._timer.stop()
            if was_active:
                self._timer.start()

    def _get_jittered_interval(self) -> int:
        jitter = int(self._ping_interval * 0.1)
        return self._ping_interval + random.randint(-jitter, jitter)

    def _reschedule_with_jitter(self) -> None:
        self._timer.stop()
        self._timer.setInterval(self._get_jittered_interval())
        self._timer.start()

    def start(self, immediate: bool = True) -> None:
        if self._is_running:
            return

        self._is_running = True
        self._pending_ping = False
        self._timer.start()

        logger.debug(f"HostPingTimer: Started for {self._host} (interval={self._ping_interval}ms)")

        if immediate:
            initial_delay = random.randint(0, 3000)
            QTimer.singleShot(initial_delay, self._execute_ping)

    def stop(self) -> None:
        self._is_running = False
        self._timer.stop()
        with self._lock:
            self._pending_ping = False

        logger.debug(f"HostPingTimer: Stopped for {self._host}")

    def ping_now(self) -> None:
        self._execute_ping()

    def _execute_ping(self) -> None:
        if not self._is_running:
            return

        with self._lock:
            if self._pending_ping:
                logger.debug(f"HostPingTimer: Previous ping still pending for {self._host}, skipping")
                return
            self._pending_ping = True

        try:
            from src.workers.network import get_ping_worker_pool
            pool = get_ping_worker_pool()
        except Exception as e:
            logger.error(f"HostPingTimer: Cannot get ping pool for {self._host}: {e}")
            with self._lock:
                self._pending_ping = False
            return

        host = self._host
        port = self._ping_port
        timeout = self._ping_timeout

        def ping_fn():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                result = sock.connect_ex((host, port))
                return (result == 0)
            except Exception:
                return False
            finally:
                sock.close()

        timer_ref = self

        def on_complete(task_id, result):
            try:
                timer_ref._ping_result_ready.emit(result)
            except RuntimeError:
                pass

        def on_error(task_id, error):
            try:
                timer_ref._ping_result_ready.emit(False)
            except RuntimeError:
                pass

        pool.submit(
            ping_fn,
            task_id=f"ping_{host}_{int(time.time() * 1000)}",
            on_complete=on_complete,
            on_error=on_error,
            metadata={'host': host, 'type': 'host_ping'}
        )

        logger.debug(f"HostPingTimer: Submitted ping for {self._host}")

    def _handle_ping_result(self, is_online: bool) -> None:
        with self._lock:
            self._pending_ping = False

        if not self._is_running:
            return

        self._device.set_online(is_online)

        try:
            self._on_result(self._device, is_online)
        except Exception:
            pass

        logger.debug(f"HostPingTimer: {self._host} -> {'ONLINE' if is_online else 'OFFLINE'}")

        self._reschedule_with_jitter()


class HostPingTimerManager(QObject):
    """
    Глобальный менеджер индивидуальных таймеров пинга для отмеченных хостов.

    Синглтон на приложение. Владеет всеми таймерами, эмитит единый сигнал
    online_updated(DeviceModel, bool), на который подписываются UI-представления.

    Подписчики вызывают start_ping(device)/stop_ping(host) для управления
    таймерами. Менеджер ведёт подсчёт подписок (ref counting): таймер реально
    удаляется когда последний подписчик вызывает stop_ping.
    """

    online_updated = Signal(DeviceModel, bool)

    _instance: Optional['HostPingTimerManager'] = None
    _instance_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        if HostPingTimerManager._initialized:
            return
        HostPingTimerManager._initialized = True
        super().__init__(parent)
        self._timers: dict[str, HostPingTimer] = {}
        self._ref_counts: dict[str, int] = {}
        logger.debug("HostPingTimerManager: Created (singleton)")

    def start_ping(self, device: DeviceModel) -> None:
        host = device.host

        if host in self._timers:
            self._ref_counts[host] += 1
            logger.debug(f"HostPingTimerManager: start_ping reuse for {host} (refs={self._ref_counts[host]})")
            timer = self._timers[host]
            if not timer.is_running:
                timer.start(immediate=True)
            return

        timer = HostPingTimer(
            device=device,
            on_result=self._on_timer_result,
            parent=self
        )
        self._timers[host] = timer
        self._ref_counts[host] = 1
        timer.start(immediate=True)

        logger.debug(f"HostPingTimerManager: start_ping created for {host}")

    def stop_ping(self, host: str) -> None:
        if host not in self._ref_counts:
            return
        self._ref_counts[host] -= 1
        if self._ref_counts[host] <= 0:
            del self._ref_counts[host]
            if host in self._timers:
                self._timers[host].stop()
                del self._timers[host]
                logger.debug(f"HostPingTimerManager: stop_ping removed timer for {host} (no more refs)")
        else:
            logger.debug(f"HostPingTimerManager: stop_ping released ref for {host} (refs={self._ref_counts[host]})")

    def stop_all(self) -> None:
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        self._ref_counts.clear()
        logger.debug("HostPingTimerManager: Stopped all timers")

    def _on_timer_result(self, device: DeviceModel, is_online: bool) -> None:
        self.online_updated.emit(device, is_online)

    def get_timer(self, host: str) -> Optional[HostPingTimer]:
        return self._timers.get(host)

    @property
    def active_count(self) -> int:
        return len(self._timers)

    def update_all_timers_settings(
        self,
        ping_interval: Optional[int] = None,
        ping_timeout: Optional[float] = None,
        ping_port: Optional[int] = None
    ) -> None:
        updated_count = 0
        for host, timer in self._timers.items():
            if timer.update_settings(ping_interval, ping_timeout, ping_port):
                timer.reset_timer()
                updated_count += 1

        if updated_count > 0:
            logger.info(f"HostPingTimerManager: Updated settings for {updated_count} active timers")


_host_ping_timer_manager: Optional[HostPingTimerManager] = None


def get_host_ping_timer_manager() -> HostPingTimerManager:
    global _host_ping_timer_manager
    if _host_ping_timer_manager is None:
        _host_ping_timer_manager = HostPingTimerManager()
    return _host_ping_timer_manager


def reset_host_ping_timer_manager() -> None:
    global _host_ping_timer_manager
    if _host_ping_timer_manager:
        _host_ping_timer_manager.stop_all()
    _host_ping_timer_manager = None
    HostPingTimerManager._instance = None
    HostPingTimerManager._initialized = False