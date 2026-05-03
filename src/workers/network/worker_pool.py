"""
WorkerPoolManager — централизованный менеджер пулов потоков.

Предоставляет:
- Единый ThreadPoolExecutor на всё приложение
- Ограничение максимального числа одновременных задач
- Трекинг активных задач
- Интеграцию с WorkerBridge для Qt workers
"""

from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Dict, Optional, Callable, Any, List, Set
from PySide6.QtCore import QObject, Signal, Slot, QThread, QMetaObject, Qt
from dataclasses import dataclass, field
from enum import Enum, auto
import time
import threading
import weakref

from src.config import config

from src.logger import logger


class TaskState(Enum):
    """Состояния задачи"""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    FAILED = auto()


@dataclass
class TaskInfo:
    """Информация о задаче"""
    task_id: str
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    future: Optional[Future] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorkerPoolManager(QObject):
    """
    Централизованный менеджер пулов потоков.
    
    НЕ синглтон - можно создавать несколько независимых пулов.
    """
    
    # Сигналы для мониторинга задач
    task_started = Signal(str)  # task_id
    task_completed = Signal(str, object)  # task_id, result
    task_failed = Signal(str, str)  # task_id, error
    task_cancelled = Signal(str)  # task_id
    pool_stats_updated = Signal(dict)  # stats

    def __init__(self, max_workers: int = 8, thread_name_prefix: str = 'pyktool'):
        super().__init__()

        # Пул потоков
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix
        )

        # Трекинг задач
        self._tasks: Dict[str, TaskInfo] = {}
        self._task_counter = 0
        self._active_task_ids: Set[str] = set()

        # Блокировка для всей потокобезопасной статистики и разделяемых данных
        self._lock = threading.Lock()

        # Callbacks для завершения задач
        self._completion_callbacks: Dict[str, List[Callable]] = {}

        # Статистика
        self._stats = {
            'submitted': 0,
            'completed': 0,
            'failed': 0,
            'cancelled': 0,
            'active': 0
        }

        logger.info(f"WorkerPoolManager initialized with max_workers={max_workers}")
    
    def submit(
        self,
        fn: Callable,
        *args,
        task_id: Optional[str] = None,
        on_complete: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        Отправить задачу в пул.
        
        Args:
            fn: Функция для выполнения
            *args: Аргументы функции
            task_id: Уникальный ID задачи (генерируется если не указан)
            on_complete: Callback при успешном завершении
            on_error: Callback при ошибке
            metadata: Дополнительные данные
            **kwargs: Именованные аргументы функции
            
        Returns:
            task_id: ID отправленной задачи
        """
        with self._lock:
            if task_id is None:
                self._task_counter += 1
                task_id = f"task_{self._task_counter}_{int(time.time() * 1000)}"
            
            if task_id in self._tasks:
                logger.warning(f"WorkerPoolManager: Task {task_id} already exists, generating new ID")
                self._task_counter += 1
                task_id = f"task_{self._task_counter}_{int(time.time() * 1000)}"
            
            task_info = TaskInfo(
                task_id=task_id,
                fn=fn,
                args=args,
                kwargs=kwargs,
                metadata=metadata or {}
            )
            
            if on_complete:
                self._completion_callbacks[task_id] = self._completion_callbacks.get(task_id, [])
                self._completion_callbacks[task_id].append(('complete', on_complete))
            if on_error:
                self._completion_callbacks[task_id] = self._completion_callbacks.get(task_id, [])
                self._completion_callbacks[task_id].append(('error', on_error))
            
            try:
                future = self._executor.submit(self._execute_with_tracking, task_id)
                task_info.future = future
                task_info.state = TaskState.PENDING
                
                self._tasks[task_id] = task_info
                self._active_task_ids.add(task_id)
                self._stats['submitted'] += 1
                self._stats['active'] = len(self._active_task_ids)
                
                logger.debug(f"WorkerPoolManager: Submitted task {task_id}")
                
            except Exception as e:
                logger.error(f"WorkerPoolManager: Error submitting task {task_id}: {e}")
                self._tasks.pop(task_id, None)
                raise
        
        self._emit_stats()
        return task_id
    
    def submit_batch(
        self,
        fn: Callable,
        items: List[Any],
        batch_id: Optional[str] = None,
        on_item_complete: Optional[Callable] = None,
        on_batch_complete: Optional[Callable] = None,
        **common_kwargs
    ) -> List[str]:
        """
        Отправить пакет задач.
        
        Args:
            fn: Функция для выполнения
            items: Список элементов для обработки
            batch_id: ID пакета (для группировки)
            on_item_complete: Callback для каждого элемента
            on_batch_complete: Callback после завершения всех
            **common_kwargs: Общие аргументы для всех задач
            
        Returns:
            List[task_id]: ID отправленных задач
        """
        if not items:
            return []
        
        batch_id = batch_id or f"batch_{int(time.time() * 1000)}"
        task_ids = []
        
        # Создаем обертку для отслеживания завершения пакета
        completed_count = [0]
        total_count = len(items)
        results = []
        errors = []
        
        def on_complete_wrapper(task_id: str, result: Any):
            completed_count[0] += 1
            results.append((task_id, result))
            
            if on_item_complete:
                on_item_complete(task_id, result)
            
            # Проверяем завершение пакета
            if completed_count[0] >= total_count and on_batch_complete:
                on_batch_complete(batch_id, results, errors)
        
        def on_error_wrapper(task_id: str, error: str):
            completed_count[0] += 1
            errors.append((task_id, error))
            
            if on_item_complete:
                on_item_complete(task_id, None)
            
            if completed_count[0] >= total_count and on_batch_complete:
                on_batch_complete(batch_id, results, errors)
        
        # Отправляем задачи
        for i, item in enumerate(items):
            task_id = f"{batch_id}_item_{i}"
            
            # Обертка для передачи item
            def make_wrapper(it, tid):
                def wrapper():
                    return fn(it, **common_kwargs)
                wrapper.__name__ = f"{fn.__name__}_item_{i}"
                return wrapper
            
            submitted_id = self.submit(
                make_wrapper(item, task_id),
                task_id=task_id,
                on_complete=lambda tid, res, tid_orig=task_id: on_complete_wrapper(tid_orig, res),
                on_error=lambda tid, err, tid_orig=task_id: on_error_wrapper(tid_orig, err),
                metadata={'batch_id': batch_id, 'item_index': i}
            )
            task_ids.append(submitted_id)
        
        logger.info(f"WorkerPoolManager: Submitted batch {batch_id} with {len(task_ids)} tasks")
        return task_ids
    
    def cancel(self, task_id: str, wait: bool = True, timeout: float = 5.0) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                logger.warning(f"WorkerPoolManager: Task {task_id} not found")
                return False
            
            task_info = self._tasks[task_id]
            
            if task_info.state in (TaskState.COMPLETED, TaskState.CANCELLED):
                logger.debug(f"WorkerPoolManager: Task {task_id} already finished")
                return False
            
            cancelled = False
            if task_info.future:
                cancelled = task_info.future.cancel()
            
            if cancelled:
                task_info.state = TaskState.CANCELLED
                task_info.completed_at = time.time()
                self._active_task_ids.discard(task_id)
                self._stats['cancelled'] += 1
                self._stats['active'] = len(self._active_task_ids)
            else:
                logger.debug(f"WorkerPoolManager: Task {task_id} is running, cannot cancel immediately")
        
        if cancelled:
            self._safe_emit(self.task_cancelled, task_id)
            logger.debug(f"WorkerPoolManager: Cancelled task {task_id}")
            self._emit_stats()
        return cancelled
    
    def cancel_batch(self, batch_id: str) -> int:
        """Отменить все задачи пакета"""
        cancelled = 0
        for task_id, task_info in list(self._tasks.items()):
            if task_info.metadata.get('batch_id') == batch_id:
                if self.cancel(task_id, wait=False):
                    cancelled += 1
        logger.info(f"WorkerPoolManager: Cancelled {cancelled} tasks from batch {batch_id}")
        return cancelled
    
    def cancel_all(self, wait: bool = True, timeout: float = 5.0) -> int:
        """
        Отменить все активные задачи.
        
        Returns:
            Количество отмененных задач
        """
        cancelled = 0
        for task_id in list(self._active_task_ids):
            if self.cancel(task_id, wait=False):
                cancelled += 1
        
        if wait:
            logger.info(f"WorkerPoolManager: Waiting for {cancelled} tasks to stop...")
        
        return cancelled
    
    def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """Получить информацию о задаче"""
        return self._tasks.get(task_id)
    
    def get_task_state(self, task_id: str) -> Optional[TaskState]:
        """Получить состояние задачи"""
        task_info = self._tasks.get(task_id)
        return task_info.state if task_info else None
    
    def is_running(self, task_id: str) -> bool:
        """Проверить, выполняется ли задача"""
        task_info = self._tasks.get(task_id)
        return task_info.state == TaskState.RUNNING if task_info else False
    
    def get_active_tasks(self) -> List[str]:
        """Получить ID активных задач"""
        return list(self._active_task_ids)
    
    def get_batch_tasks(self, batch_id: str) -> List[str]:
        """Получить ID задач пакета"""
        return [
            task_id for task_id, info in self._tasks.items()
            if info.metadata.get('batch_id') == batch_id
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                **self._stats,
                'total_tasks': len(self._tasks),
                'max_workers': self._executor._max_workers
            }
    
    def get_detailed_stats(self) -> Dict[str, Any]:
        """Получить подробную статистику с метриками производительности"""
        now = time.time()
        active_tasks = []
        pending_tasks = []
        completed_tasks = []
        
        for task_id, task_info in self._tasks.items():
            task_data = {
                'task_id': task_id,
                'fn': task_info.fn.__name__ if hasattr(task_info.fn, '__name__') else str(task_info.fn),
                'metadata': task_info.metadata
            }
            
            if task_info.state == TaskState.RUNNING:
                if task_info.started_at:
                    task_data['duration'] = now - task_info.started_at
                active_tasks.append(task_data)
            elif task_info.state == TaskState.PENDING:
                task_data['waiting_time'] = now - task_info.created_at
                pending_tasks.append(task_data)
            elif task_info.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                if task_info.started_at and task_info.completed_at:
                    task_data['duration'] = task_info.completed_at - task_info.started_at
                completed_tasks.append(task_data)
        
        return {
            **self.get_stats(),
            'active_tasks': active_tasks,
            'pending_tasks': pending_tasks,
            'completed_tasks_count': len(completed_tasks),
            'avg_task_duration': (
                sum(t.get('duration', 0) for t in completed_tasks) / len(completed_tasks)
                if completed_tasks else 0
            ),
            'total_execution_time': sum(t.get('duration', 0) for t in completed_tasks),
        }
    
    def shutdown(self, wait: bool = True, cancel_futures: bool = True):
        """
        Остановить пул.
        
        Args:
            wait: Ждать завершения задач
            cancel_futures: Отменить незавершенные задачи
        """
        logger.info("WorkerPoolManager: Shutting down...")
        
        if cancel_futures:
            self.cancel_all(wait=False)
        
        self._executor.shutdown(wait=wait)
        logger.info("WorkerPoolManager: Shutdown complete")
    
    # ========== INTERNAL METHODS ==========
    
    def _execute_with_tracking(self, task_id: str) -> Any:
        with self._lock:
            task_info = self._tasks.get(task_id)
            if not task_info:
                raise RuntimeError(f"Task {task_id} not found")
            task_info.started_at = time.time()
            task_info.state = TaskState.RUNNING
            self._stats['active'] = len(self._active_task_ids)
        
        self._safe_emit(self.task_started, task_id)
        logger.debug(f"WorkerPoolManager: Task {task_id} started")
        
        try:
            result = task_info.fn(*task_info.args, **task_info.kwargs)
            
            with self._lock:
                task_info.result = result
                task_info.state = TaskState.COMPLETED
                task_info.completed_at = time.time()
                self._stats['completed'] += 1
                self._active_task_ids.discard(task_id)
            
            self._safe_emit(self.task_completed, task_id, result)
            logger.debug(f"WorkerPoolManager: Task {task_id} completed")
            
            self._invoke_callbacks(task_id, 'complete', result)
            
            return result
            
        except Exception as e:
            with self._lock:
                task_info.error = str(e)
                task_info.state = TaskState.FAILED
                task_info.completed_at = time.time()
                self._stats['failed'] += 1
                self._active_task_ids.discard(task_id)
            
            self._safe_emit(self.task_failed, task_id, str(e))
            logger.error(f"WorkerPoolManager: Task {task_id} failed: {e}")
            
            self._invoke_callbacks(task_id, 'error', str(e))
            
            raise
        
        finally:
            with self._lock:
                self._stats['active'] = len(self._active_task_ids)
            self._emit_stats()
    
    def _invoke_callbacks(self, task_id: str, callback_type: str, *args):
        with self._lock:
            callbacks = list(self._completion_callbacks.get(task_id, []))
            self._completion_callbacks.pop(task_id, None)
        for cb_type, callback in callbacks:
            if cb_type == callback_type:
                try:
                    callback(task_id, *args)
                except Exception as e:
                    logger.error(f"WorkerPoolManager: Error in callback for {task_id}: {e}")
    
    def _emit_stats(self):
        """Отправка статистики"""
        try:
            self._safe_emit(self.pool_stats_updated, self.get_stats())
        except Exception as e:
            logger.error(f"WorkerPoolManager: Error emitting stats: {e}")
    
    def _safe_emit(self, signal, *args):
        """Безопасное испускание сигнала из любого потока.
        
        Если сигнал испускается из ThreadPoolExecutor (не из Qt-потока),
        используем QueuedConnection для маршаллинга в главный поток.
        Qt автоматически использует QueuedConnection при испускании
        из другого потока, но явный вызов через QMetaObject.invokeMethod
        гарантирует безопасность.
        """
        try:
            signal.emit(*args)
        except RuntimeError:
            logger.debug(f"WorkerPoolManager: Signal emit failed (object may be deleted)")


# Глобальные экземпляры - РАЗДЕЛЬНЫЕ для пингов и команд
_ping_worker_pool: Optional[WorkerPoolManager] = None
_command_worker_pool: Optional[WorkerPoolManager] = None

def get_ping_worker_pool() -> WorkerPoolManager:
    """Получить или создать WorkerPoolManager для пингов"""
    global _ping_worker_pool
    if _ping_worker_pool is None:
        _ping_worker_pool = WorkerPoolManager(
            max_workers=config.app.network.thread_count,
            thread_name_prefix='pyktool-ping'
        )
    return _ping_worker_pool

def get_command_worker_pool() -> WorkerPoolManager:
    """Получить или создать WorkerPoolManager для команд"""
    global _command_worker_pool
    if _command_worker_pool is None:
        _command_worker_pool = WorkerPoolManager(
            max_workers=config.app.network.thread_count,
            thread_name_prefix='pyktool-cmd'
        )
    return _command_worker_pool

# Для обратной совместимости
def get_worker_pool() -> WorkerPoolManager:
    """Получить worker_pool для команд (по умолчанию)"""
    return get_command_worker_pool()
