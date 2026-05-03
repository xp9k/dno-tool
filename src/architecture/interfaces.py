"""
Integration Interfaces - интерфейсы для интеграции с существующим кодом.

Предоставляют абстракции для слабосвязанной интеграции компонентов архитектуры
с существующими классами pyktool.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Callable, Union, TYPE_CHECKING
from PySide6.QtWidgets import QDialog, QWidget
from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from .event_bus import Event, EventType, EventFilter
    from .dialog_manager import DialogConfig, DialogResult
    from .view_state import StateChangeEvent


class IEventHandler(ABC):
    """
    Интерфейс для компонентов, обрабатывающих события.
    
    Пример использования:
        class MyComponent(IEventHandler):
            def __init__(self):
                self._handler_id = event_bus.subscribe(
                    EventType.DATA_CHANGED,
                    self.handle_event
                )
            
            def handle_event(self, event: Event) -> None:
                # Обработка события
                pass
            
            def cleanup(self):
                event_bus.unsubscribe(self._handler_id)
    """
    
    @abstractmethod
    def handle_event(self, event: 'Event') -> None:
        """Обработать событие."""
        pass


class IDialogClient(ABC):
    """
    Интерфейс для компонентов, использующих диалоги.
    
    Пример использования:
        class MyWindow(IDialogClient):
            def show_settings(self):
                config = DialogConfig(
                    dialog_class=ConfigDialog,
                    parent=self,
                    on_result=self.on_settings_result
                )
                dialog_manager.open(config)
            
            def on_settings_result(self, result: DialogResult, data: Any) -> None:
                if result == DialogResult.ACCEPTED:
                    # Применить настройки
                    pass
    """
    
    @abstractmethod
    def on_dialog_result(self, dialog_id: str, result: 'DialogResult', data: Any) -> None:
        """Вызывается при получении результата от диалога."""
        pass
    
    def create_dialog_config(
        self,
        dialog_class: type,
        modal: bool = True,
        data: Optional[Dict[str, Any]] = None
    ) -> 'DialogConfig':
        """Создать конфигурацию диалога."""
        from .dialog_manager import DialogConfig
        return DialogConfig(
            dialog_class=dialog_class,
            parent=self if isinstance(self, QWidget) else None,
            modal=modal,
            data=data or {},
            on_result=lambda r, d: self.on_dialog_result('', r, d)
        )


class IStateObserver(ABC):
    """
    Интерфейс для компонентов, наблюдающих за состоянием.
    
    Пример использования:
        class MyPanel(IStateObserver):
            def __init__(self):
                view_state.add_observer('main_window', self)
            
            def on_state_changed(self, state_id: str, event: StateChangeEvent) -> None:
                if event.change_type == StateChangeType.VALUE_CHANGED:
                    self.update_ui()
    """
    
    @abstractmethod
    def on_state_changed(self, state_id: str, event: 'StateChangeEvent') -> None:
        """Вызывается при изменении состояния."""
        pass


class IWorkerClient(ABC):
    """
    Интерфейс для компонентов, использующих workers.
    
    Обеспечивает централизованную обработку событий от workers.
    
    Пример использования:
        class MainWindow(IWorkerClient):
            def execute_command(self, devices, commands):
                worker = CommandWorker(devices, commands)
                self.register_worker(worker, 'command_execution')
                worker.execute()
            
            def on_worker_progress(self, worker_id: str, progress: Any) -> None:
                # Обновить прогресс
                pass
            
            def on_worker_finished(self, worker_id: str, result: Any) -> None:
                # Обработать результат
                pass
    """
    
    @abstractmethod
    def on_worker_started(self, worker_id: str, context: Dict[str, Any]) -> None:
        """Вызывается при запуске worker."""
        pass
    
    @abstractmethod
    def on_worker_progress(self, worker_id: str, progress: Any) -> None:
        """Вызывается при обновлении прогресса worker."""
        pass
    
    @abstractmethod
    def on_worker_finished(self, worker_id: str, result: Any) -> None:
        """Вызывается при завершении worker."""
        pass
    
    @abstractmethod
    def on_worker_error(self, worker_id: str, error: str) -> None:
        """Вызывается при ошибке worker."""
        pass
    
    def register_worker(
        self,
        worker: QObject,
        worker_id: str,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Зарегистрировать worker для централизованной обработки.
        
        Args:
            worker: Экземпляр worker
            worker_id: Уникальный ID worker
            context: Контекст выполнения
        """
        from src.di.container import get_container
        from src.architecture.worker_bridge import WorkerBridge
        container = get_container()
        if container.is_registered(WorkerBridge):
            bridge = container.resolve(WorkerBridge)
        else:
            bridge = WorkerBridge()
        bridge.register_worker(worker, worker_id, self, context)


class DialogFactory(ABC):
    """
    Абстрактная фабрика для создания диалогов.
    
    Позволяет инкапсулировать логику создания диалогов.
    
    Пример использования:
        class DeviceDialogFactory(DialogFactory):
            def create_dialog(self, parent=None, **kwargs) -> QDialog:
                device = kwargs.get('device')
                return DeviceEditDialog(device, parent)
        
        # Регистрация
        factory = DeviceDialogFactory()
        dialog_manager.register_factory('device_edit', factory.create_dialog)
    """
    
    @abstractmethod
    def create_dialog(self, parent: Optional[QWidget] = None, **kwargs) -> QDialog:
        """Создать экземпляр диалога."""
        pass


class EventFilter(ABC):
    """
    Абстрактный фильтр событий.
    
    Позволяет создавать сложные условия фильтрации событий.
    
    Пример использования:
        class DeviceEventFilter(EventFilter):
            def __init__(self, device_id: str):
                self.device_id = device_id
            
            def matches(self, event: Event) -> bool:
                return event.get('device_id') == self.device_id
        
        # Использование
        filter = DeviceEventFilter('device_001')
        event_bus.subscribe(EventType.DEVICE_UPDATED, handler, filter)
    """
    
    @abstractmethod
    def matches(self, event: 'Event') -> bool:
        """Проверить, соответствует ли событие фильтру."""
        pass


class IComponentLifecycle(ABC):
    """
    Интерфейс жизненного цикла компонента.
    
    Обеспечивает стандартизированную инициализацию и очистку ресурсов.
    
    Пример использования:
        class MyComponent(IComponentLifecycle):
            def initialize(self) -> None:
                # Инициализация
                self._setup_event_handlers()
                self._load_state()
            
            def cleanup(self) -> None:
                # Очистка
                self._save_state()
                self._unsubscribe_events()
    """
    
    @abstractmethod
    def initialize(self) -> None:
        """Инициализировать компонент."""
        pass
    
    @abstractmethod
    def cleanup(self) -> None:
        """Очистить ресурсы компонента."""
        pass


class IDataBinding(ABC):
    """
    Интерфейс для двусторонней привязки данных.
    
    Пример использования:
        class FormBinding(IDataBinding):
            def bind(self, source: Any, target: Any) -> None:
                # Установить привязку
                pass
            
            def unbind(self) -> None:
                # Удалить привязку
                pass
            
            def sync_to_target(self) -> None:
                # Синхронизировать source -> target
                pass
            
            def sync_to_source(self) -> None:
                # Синхронизировать target -> source
                pass
    """
    
    @abstractmethod
    def bind(self, source: Any, target: Any) -> None:
        """Установить привязку между источником и целью."""
        pass
    
    @abstractmethod
    def unbind(self) -> None:
        """Удалить привязку."""
        pass
    
    @abstractmethod
    def sync_to_target(self) -> None:
        """Синхронизировать данные из источника в цель."""
        pass
    
    @abstractmethod
    def sync_to_source(self) -> None:
        """Синхронизировать данные из цели в источник."""
        pass


class ICommand(ABC):
    """
    Интерфейс команды (паттерн Command).
    
    Используется для инкапсуляции действий с поддержкой undo/redo.
    
    Пример использования:
        class DeleteDeviceCommand(ICommand):
            def __init__(self, device_id: str):
                self.device_id = device_id
                self._backup = None
            
            def execute(self) -> None:
                self._backup = get_device(self.device_id)
                delete_device(self.device_id)
            
            def undo(self) -> None:
                if self._backup:
                    restore_device(self._backup)
            
            def redo(self) -> None:
                self.execute()
    """
    
    @abstractmethod
    def execute(self) -> None:
        """Выполнить команду."""
        pass
    
    @abstractmethod
    def undo(self) -> None:
        """Отменить команду."""
        pass
    
    @abstractmethod
    def redo(self) -> None:
        """Повторить команду."""
        pass
    
    def can_execute(self) -> bool:
        """Проверить возможность выполнения."""
        return True
    
    def can_undo(self) -> bool:
        """Проверить возможность отмены."""
        return True


class IService(ABC):
    """
    Интерфейс сервиса приложения.
    
    Базовый интерфейс для всех сервисов приложения.
    
    Пример использования:
        class DeviceService(IService):
            def start(self) -> None:
                self._load_devices()
            
            def stop(self) -> None:
                self._save_devices()
            
            def get_name(self) -> str:
                return 'DeviceService'
    """
    
    @abstractmethod
    def start(self) -> None:
        """Запустить сервис."""
        pass
    
    @abstractmethod
    def stop(self) -> None:
        """Остановить сервис."""
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Получить имя сервиса."""
        pass
    
    def is_running(self) -> bool:
        """Проверить, запущен ли сервис."""
        return False


# Типы для аннотаций
HandlerId = str
DialogId = str
StateId = str
WorkerId = str
