"""
Worker Bridge - мост для интеграции workers с EventBus.

Предоставляет централизованную обработку событий от workers,
абстрагируя UI компоненты от прямой работы с сигналами workers.
"""

from typing import Dict, List, Optional, Any, Callable, Union, TYPE_CHECKING
from PySide6.QtCore import QObject, Signal, Slot, QThread
from dataclasses import dataclass, field
import weakref
import time
import threading
from src.logger import logger

if TYPE_CHECKING:
    from .event_bus import EventBus, Event, EventType
    from .interfaces import IWorkerClient


@dataclass
class WorkerContext:
    """Контекст выполнения worker."""
    worker_id: str
    worker_type: str
    client: Optional['IWorkerClient'] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class WorkerEventAdapter(QObject):
    """
    Адаптер для преобразования сигналов worker в события EventBus.
    
    Позволяет автоматически транслировать сигналы worker в события,
    которые могут быть обработаны любыми компонентами приложения.
    """
    
    def __init__(self, event_bus: Optional['EventBus'] = None):
        super().__init__()
        self._event_bus = event_bus
        self._worker_mappings: Dict[str, Dict[str, str]] = {}
    
    def adapt_worker(
        self,
        worker: QObject,
        worker_id: str,
        signal_mappings: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Адаптировать worker для отправки событий.
        
        Args:
            worker: Экземпляр worker
            worker_id: Уникальный ID worker
            signal_mappings: Сопоставление сигналов -> типы событий
                {'progress_update': 'WORKER_PROGRESS', ...}
        """
        default_mappings = {
            'started': 'WORKER_STARTED',
            'progress_update': 'WORKER_PROGRESS',
            'finished': 'WORKER_FINISHED',
            'error': 'WORKER_ERROR',
            'result_ready': 'WORKER_RESULT',
        }
        
        mappings = signal_mappings or default_mappings
        self._worker_mappings[worker_id] = mappings
        
        for signal_name, event_type_name in mappings.items():
            signal = getattr(worker, signal_name, None)
            if signal and hasattr(signal, 'connect'):
                # Создаем обработчик для этого сигнала
                handler = self._create_handler(worker_id, event_type_name, signal_name)
                signal.connect(handler)
                logger.debug(f"WorkerEventAdapter: Connected {signal_name} -> {event_type_name}")
    
    def _create_handler(
        self,
        worker_id: str,
        event_type_name: str,
        signal_name: str
    ) -> Callable:
        """Создать обработчик сигнала."""
        def handler(*args):
            if not self._event_bus:
                return
            
            try:
                from .event_bus import EventType
                event_type = getattr(EventType, event_type_name, EventType.CUSTOM)
                
                # Формируем данные события
                data = {
                    'worker_id': worker_id,
                    'signal': signal_name,
                    'args': args
                }
                
                # Если есть один аргумент, добавляем его как 'data'
                if len(args) == 1:
                    data['data'] = args[0]
                elif len(args) > 1:
                    data['data'] = args
                
                self._event_bus.publish_typed(
                    event_type=event_type,
                    source=f'worker_{worker_id}',
                    data=data
                )
                
            except Exception as e:
                logger.error(f"WorkerEventAdapter: Error handling signal {signal_name}: {e}")
        
        return handler


class WorkerBridge(QObject):
    """
    Мост для управления workers и их интеграции с системой событий.
    
    Предоставляет централизованное управление жизненным циклом workers
    и обработку их событий.
    """
    
    # Сигналы для внутреннего использования
    _worker_started = Signal(str, dict)  # worker_id, context
    _worker_progress = Signal(str, object)  # worker_id, progress
    _worker_finished = Signal(str, object)  # worker_id, result
    _worker_error = Signal(str, str)  # worker_id, error
    
    def __init__(self, event_bus: Optional['EventBus'] = None):
        super().__init__()
        self._event_bus = event_bus
        self._workers: Dict[str, QObject] = {}
        self._threads: Dict[str, QThread] = {}
        self._contexts: Dict[str, WorkerContext] = {}
        self._clients: Dict[str, weakref.ref] = {}
        self._event_adapter = WorkerEventAdapter(event_bus)
        self._lock = threading.Lock()
        
        self._worker_started.connect(self._on_worker_started_internal)
        self._worker_progress.connect(self._on_worker_progress_internal)
        self._worker_finished.connect(self._on_worker_finished_internal)
        self._worker_error.connect(self._on_worker_error_internal)
    
    def register_worker(
        self,
        worker: QObject,
        worker_id: str,
        client: Optional['IWorkerClient'] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_adapt: bool = True
    ) -> WorkerContext:
        """
        Зарегистрировать worker для управления.
        
        Args:
            worker: Экземпляр worker
            worker_id: Уникальный ID
            client: Клиент для обратных вызовов
            context: Контекст выполнения
            auto_adapt: Автоматически адаптировать сигналы
            
        Returns:
            Контекст worker
        """
        if worker_id in self._workers:
            logger.warning(f"WorkerBridge: Worker {worker_id} already registered, replacing")
            self.unregister_worker(worker_id)
        
        worker_type = type(worker).__name__
        
        ctx = WorkerContext(
            worker_id=worker_id,
            worker_type=worker_type,
            client=client,
            metadata=context or {}
        )
        
        self._workers[worker_id] = worker
        self._contexts[worker_id] = ctx
        
        if client:
            self._clients[worker_id] = weakref.ref(client)
        
        # Адаптируем сигналы
        if auto_adapt:
            self._event_adapter.adapt_worker(worker, worker_id)
        
        # Подключаем стандартные сигналы
        self._connect_worker_signals(worker, worker_id)
        
        logger.debug(f"WorkerBridge: Registered worker {worker_id} ({worker_type})")
        return ctx
    
    def register_threaded_worker(
        self,
        worker: QObject,
        worker_id: str,
        client: Optional['IWorkerClient'] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_start: bool = True
    ) -> WorkerContext:
        """
        Зарегистрировать worker с выделенным потоком.
        
        Args:
            worker: Экземпляр worker
            worker_id: Уникальный ID
            client: Клиент для обратных вызовов
            context: Контекст выполнения
            auto_start: Автоматически запустить поток
            
        Returns:
            Контекст worker
        """
        ctx = self.register_worker(worker, worker_id, client, context, auto_adapt=True)
        
        # Валидация: worker должен иметь метод execute
        if not hasattr(worker, 'execute') or not callable(getattr(worker, 'execute')):
            logger.error(f"WorkerBridge: Worker {worker_id} has no execute method, cannot start thread")
            self.unregister_worker(worker_id)
            raise ValueError(f"Worker {worker_id} must have an execute() method")
        
        # Создаем поток
        thread = QThread()
        self._threads[worker_id] = thread
        
        # Перемещаем worker в поток
        worker.moveToThread(thread)
        
        # Подключаем сигналы потока
        thread.started.connect(worker.execute)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        
        # Подключаем сигнал завершения для очистки
        def make_thread_finished_handler(wid):
            def handler():
                self._on_thread_finished(wid)
            return handler
        worker.finished.connect(make_thread_finished_handler(worker_id))
        
        if auto_start:
            thread.start()
            ctx.started_at = time.time()
            self._worker_started.emit(worker_id, context or {})
        
        return ctx
    
    def unregister_worker(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id not in self._workers:
                return False
            
            thread = self._threads.pop(worker_id, None)
            worker = self._workers.pop(worker_id, None)
            self._contexts.pop(worker_id, None)
            self._clients.pop(worker_id, None)
        
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(5000)
                thread.deleteLater()
            except RuntimeError:
                logger.debug(f"WorkerBridge: Thread for worker {worker_id} already deleted")
        
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception as e:
                logger.error(f"WorkerBridge: Error deleting worker {worker_id}: {e}")
        
        logger.debug(f"WorkerBridge: Unregistered worker {worker_id}")
        return True
    
    def abort_worker(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id not in self._workers:
                logger.warning(f"WorkerBridge: Worker {worker_id} not found for abort")
                return False
            
            worker = self._workers[worker_id]
        
        logger.debug(f"WorkerBridge: Aborting worker {worker_id}")
        
        if hasattr(worker, 'abort') and callable(worker.abort):
            try:
                worker.abort()
                logger.info(f"WorkerBridge: Aborted worker {worker_id}")
            except Exception as e:
                logger.error(f"WorkerBridge: Error calling abort on worker {worker_id}: {e}")
        
        thread_stopped = False
        if worker_id in self._threads:
            thread = self._threads[worker_id]
            try:
                if thread.isRunning():
                    logger.debug(f"WorkerBridge: Stopping thread for worker {worker_id}")
                    thread.quit()
                    thread_stopped = True
            except RuntimeError:
                logger.debug(f"WorkerBridge: Thread for worker {worker_id} already deleted")
                thread_stopped = True
        
        self._worker_finished.emit(worker_id, {'aborted': True, 'thread_stopped': thread_stopped})
        
        return True
    
    def get_worker(self, worker_id: str) -> Optional[QObject]:
        """Получить экземпляр worker."""
        return self._workers.get(worker_id)
    
    def get_context(self, worker_id: str) -> Optional[WorkerContext]:
        """Получить контекст worker."""
        return self._contexts.get(worker_id)
    
    def is_running(self, worker_id: str) -> bool:
        """Проверить, выполняется ли worker."""
        if worker_id in self._threads:
            try:
                return self._threads[worker_id].isRunning()
            except RuntimeError:
                # Thread already deleted
                return False
        return worker_id in self._workers
    
    def get_active_workers(self) -> List[str]:
        """Получить список активных workers."""
        return [
            worker_id for worker_id in self._workers.keys()
            if self.is_running(worker_id)
        ]
    
    def abort_all(self) -> int:
        """
        Прервать все активные workers.
        
        Returns:
            Количество прерванных workers
        """
        aborted = 0
        for worker_id in list(self._workers.keys()):
            if self.abort_worker(worker_id):
                aborted += 1
        return aborted
    
    def _connect_worker_signals(self, worker: QObject, worker_id: str) -> None:
        if hasattr(worker, 'finished') and hasattr(worker.finished, 'connect'):
            def make_finished_handler(wid):
                def handler():
                    self._worker_finished.emit(wid, None)
                return handler
            worker.finished.connect(make_finished_handler(worker_id))
        
        if hasattr(worker, 'error') and hasattr(worker.error, 'connect'):
            def make_error_handler(wid):
                def handler(msg):
                    self._worker_error.emit(wid, msg)
                return handler
            worker.error.connect(make_error_handler(worker_id))
        
        if hasattr(worker, 'progress_update') and hasattr(worker.progress_update, 'connect'):
            def make_progress_handler(wid):
                def handler(*args):
                    self._worker_progress.emit(wid, args[0] if len(args) == 1 else args)
                return handler
            worker.progress_update.connect(make_progress_handler(worker_id))
    
    def _on_worker_started_internal(self, worker_id: str, context: Dict[str, Any]) -> None:
        """Внутренний обработчик запуска worker."""
        ctx = self._contexts.get(worker_id)
        if ctx:
            ctx.started_at = time.time()
        
        client_ref = self._clients.get(worker_id)
        if client_ref:
            client = client_ref()
            if client and hasattr(client, 'on_worker_started'):
                try:
                    client.on_worker_started(worker_id, context)
                except Exception as e:
                    logger.error(f"WorkerBridge: Error in on_worker_started: {e}")
            elif not client:
                del self._clients[worker_id]
        
        # Публикуем событие
        if self._event_bus:
            try:
                from .event_bus import EventType
                self._event_bus.publish_typed(
                    event_type=EventType.WORKER_STARTED,
                    source='WorkerBridge',
                    data={'worker_id': worker_id, 'context': context}
                )
            except Exception as e:
                logger.error(f"WorkerBridge: Error publishing event: {e}")
    
    def _on_worker_progress_internal(self, worker_id: str, progress: Any) -> None:
        client_ref = self._clients.get(worker_id)
        if client_ref:
            client = client_ref()
            if client and hasattr(client, 'on_worker_progress'):
                try:
                    client.on_worker_progress(worker_id, progress)
                except Exception as e:
                    logger.error(f"WorkerBridge: Error in on_worker_progress: {e}")
            elif not client:
                self._clients.pop(worker_id, None)
    
    def _on_worker_finished_internal(self, worker_id: str, result: Any) -> None:
        with self._lock:
            ctx = self._contexts.get(worker_id)
            if ctx:
                ctx.finished_at = time.time()
        
        client_ref = self._clients.get(worker_id)
        if client_ref:
            client = client_ref()
            if client and hasattr(client, 'on_worker_finished'):
                try:
                    client.on_worker_finished(worker_id, result)
                except Exception as e:
                    logger.error(f"WorkerBridge: Error in on_worker_finished: {e}")
            elif not client:
                self._clients.pop(worker_id, None)
        
        # Публикуем событие
        if self._event_bus:
            try:
                from .event_bus import EventType
                self._event_bus.publish_typed(
                    event_type=EventType.WORKER_FINISHED,
                    source='WorkerBridge',
                    data={'worker_id': worker_id, 'result': result}
                )
            except Exception as e:
                logger.error(f"WorkerBridge: Error publishing event: {e}")
    
    def _on_worker_error_internal(self, worker_id: str, error: str) -> None:
        client_ref = self._clients.get(worker_id)
        if client_ref:
            client = client_ref()
            if client and hasattr(client, 'on_worker_error'):
                try:
                    client.on_worker_error(worker_id, error)
                except Exception as e:
                    logger.error(f"WorkerBridge: Error in on_worker_error: {e}")
            elif not client:
                self._clients.pop(worker_id, None)
        
        # Публикуем событие
        if self._event_bus:
            try:
                from .event_bus import EventType
                self._event_bus.publish_typed(
                    event_type=EventType.WORKER_ERROR,
                    source='WorkerBridge',
                    data={'worker_id': worker_id, 'error': error}
                )
            except Exception as e:
                logger.error(f"WorkerBridge: Error publishing event: {e}")
    
    def _on_thread_finished(self, worker_id: str) -> None:
        """Обработчик завершения потока."""
        logger.debug(f"WorkerBridge: Thread finished for worker {worker_id}")
        # Опционально: автоматическая очистка
        # self.unregister_worker(worker_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику workers."""
        active_count = 0
        for t in self._threads.values():
            try:
                if t.isRunning():
                    active_count += 1
            except RuntimeError:
                # Thread already deleted
                pass
        return {
            'total_workers': len(self._workers),
            'active_threads': active_count,
            'registered_workers': list(self._workers.keys())
        }



