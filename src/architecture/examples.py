"""
Examples - примеры использования архитектурных компонентов.

Демонстрирует интеграцию EventBus, DialogManager, ViewState и WorkerBridge
с существующими компонентами pyktool.
"""

from typing import Dict, Any, Optional, List
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QDialog, QPushButton, 
    QVBoxLayout, QLabel, QMessageBox, QLineEdit
)
from PySide6.QtCore import QObject, Signal

# Импорты архитектурных компонентов
from src.architecture import (
    EventBus, EventType, Event,
    DialogManager, DialogConfig, DialogResult,
    ViewState, StateChangeEvent,
    WorkerBridge,
    IEventHandler, IDialogClient, IStateObserver, IWorkerClient
)

# Импорты существующих классов pyktool
from src.domain.models.device import DeviceModel
from src.workers import CommandWorker, Command
from src.ui.dialogs.common.common import DeviceEditDialog, ConfigDialog
from src.ui.dialogs.command.result import CommandResultDialog


# =============================================================================
# Пример 1: Интеграция EventBus с MainWindow
# =============================================================================

class MainWindowWithEventBus(QMainWindow, IEventHandler):
    """
    Пример MainWindow с использованием EventBus.
    
    Демонстрирует:
    - Подписку на события
    - Публикацию событий
    - Обработку событий от workers
    """
    
    def __init__(self):
        super().__init__()
        self.event_bus = EventBus()
        self._setup_event_handlers()
        self._setup_ui()
    
    def _setup_event_handlers(self):
        """Настройка обработчиков событий."""
        # Подписываемся на события устройств
        self.event_bus.subscribe(
            EventType.DEVICE_SELECTED,
            self._on_device_selected
        )
        
        # Подписываемся на события команд
        self.event_bus.subscribe(
            EventType.COMMAND_STARTED,
            self._on_command_started
        )
        self.event_bus.subscribe(
            EventType.COMMAND_PROGRESS,
            self._on_command_progress
        )
        self.event_bus.subscribe(
            EventType.COMMAND_FINISHED,
            self._on_command_finished
        )
        
        # Подписываемся на события workers
        self.event_bus.subscribe(
            EventType.WORKER_ERROR,
            self._on_worker_error
        )
        
        # Подписываемся на все события для логирования
        self._log_handler_id = self.event_bus.subscribe_all(
            self._log_all_events,
            priority=-100  # Низкий приоритет - вызывается последним
        )
    
    def _setup_ui(self):
        """Настройка интерфейса."""
        self.setWindowTitle("MainWindow with EventBus")
        self.resize(800, 600)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Кнопка для публикации тестового события
        test_btn = QPushButton("Publish Test Event")
        test_btn.clicked.connect(self._publish_test_event)
        layout.addWidget(test_btn)
        
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
    
    def _on_device_selected(self, event: Event):
        """Обработчик выбора устройства."""
        device = event.get('device')
        self.status_label.setText(f"Selected device: {device}")
    
    def _on_command_started(self, event: Event):
        """Обработчик запуска команды."""
        self.status_label.setText("Command started...")
    
    def _on_command_progress(self, event: Event):
        """Обработчик прогресса команды."""
        progress = event.get('progress')
        self.status_label.setText(f"Progress: {progress}%")
    
    def _on_command_finished(self, event: Event):
        """Обработчик завершения команды."""
        result = event.get('result')
        self.status_label.setText(f"Command finished: {result}")
    
    def _on_worker_error(self, event: Event):
        """Обработчик ошибки worker."""
        error = event.get('error')
        QMessageBox.critical(self, "Worker Error", f"Error: {error}")
    
    def _log_all_events(self, event: Event):
        """Логирование всех событий."""
        print(f"[EVENT] {event.event_type.name} from {event.source}")
    
    def _publish_test_event(self):
        """Публикация тестового события."""
        self.event_bus.publish_typed(
            event_type=EventType.DATA_CHANGED,
            source='MainWindow',
            data={'test': True, 'value': 42}
        )
    
    def handle_event(self, event: Event) -> None:
        """Реализация IEventHandler."""
        # Общий обработчик (если нужен)
        pass
    
    def closeEvent(self, event):
        """Очистка при закрытии."""
        # Отписываемся от событий
        self.event_bus.unsubscribe(self._log_handler_id)
        super().closeEvent(event)


# =============================================================================
# Пример 2: Интеграция DialogManager
# =============================================================================

class MainWindowWithDialogManager(QMainWindow, IDialogClient):
    """
    Пример MainWindow с использованием DialogManager.
    
    Демонстрирует:
    - Открытие диалогов через менеджер
    - Обработку результатов диалогов
    - Использование синглтон-диалогов
    """
    
    def __init__(self):
        super().__init__()
        self.dialog_manager = DialogManager()
        self._setup_ui()
        self._register_dialog_factories()
    
    def _setup_ui(self):
        """Настройка интерфейса."""
        self.setWindowTitle("MainWindow with DialogManager")
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Кнопки для открытия диалогов
        btn_config = QPushButton("Open Config (Singleton)")
        btn_config.clicked.connect(self._open_config_dialog)
        layout.addWidget(btn_config)
        
        btn_device = QPushButton("Edit Device")
        btn_device.clicked.connect(self._open_device_dialog)
        layout.addWidget(btn_device)
        
        btn_result = QPushButton("Show Result")
        btn_result.clicked.connect(self._open_result_dialog)
        layout.addWidget(btn_result)
    
    def _register_dialog_factories(self):
        """Регистрация фабрик диалогов."""
        # Регистрируем фабрику для редактирования устройства
        self.dialog_manager.register_factory(
            'device_edit',
            lambda parent=None, **kwargs: DeviceEditDialog(
                kwargs.get('device'),
                parent
            )
        )
    
    def _open_config_dialog(self):
        """Открыть диалог настроек (синглтон)."""
        config = DialogConfig(
            dialog_class=ConfigDialog,
            parent=self,
            singleton=True,  # Только один экземпляр
            modal=True,
            title="Application Settings",
            on_result=self._on_config_result
        )
        self.dialog_manager.open(config)
    
    def _open_device_dialog(self):
        """Открыть диалог редактирования устройства."""
        device = DeviceModel({"name": "Test Device", "host": "192.168.1.1"})
        
        # Способ 1: Через DialogConfig
        config = DialogConfig(
            dialog_class=DeviceEditDialog,
            parent=self,
            modal=True,
            data={'device': device},
            on_result=lambda result, data: self._on_device_result(result, data, device)
        )
        self.dialog_manager.open(config)
        
        # Способ 2: Через фабрику
        # self.dialog_manager.open_typed(
        #     dialog_type='device_edit',
        #     parent=self,
        #     data={'device': device}
        # )
    
    def _open_result_dialog(self):
        """Открыть диалог результата."""
        config = DialogConfig(
            dialog_class=CommandResultDialog,
            parent=self,
            modal=True,
            data={
                'hostname': 'server01',
                'output': 'Command output here...',
                'status': 'Success'
            }
        )
        self.dialog_manager.open(config)
    
    def _on_config_result(self, result: DialogResult, data: Any):
        """Обработка результата диалога настроек."""
        if result == DialogResult.ACCEPTED:
            print("Settings saved")
            # Применяем настройки
        else:
            print("Settings cancelled")
    
    def _on_device_result(self, result: DialogResult, data: Any, original_device: DeviceModel):
        """Обработка результата диалога устройства."""
        if result == DialogResult.ACCEPTED and data:
            print(f"Device updated: {data}")
            # Обновляем устройство
    
    def on_dialog_result(self, dialog_id: str, result: DialogResult, data: Any) -> None:
        """Реализация IDialogClient."""
        print(f"Dialog {dialog_id} result: {result}")


# =============================================================================
# Пример 3: Интеграция ViewState
# =============================================================================

class MainWindowWithViewState(QMainWindow, IStateObserver):
    """
    Пример MainWindow с использованием ViewState.
    
    Демонстрирует:
    - Сохранение состояния UI
    - Привязку виджетов к состоянию
    - Undo/redo
    """
    
    def __init__(self):
        super().__init__()
        self.view_state = ViewState()
        self._setup_ui()
        self._setup_state()
    
    def _setup_ui(self):
        """Настройка интерфейса."""
        self.setWindowTitle("MainWindow with ViewState")
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Поля ввода
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("name")
        layout.addWidget(QLabel("Name:"))
        layout.addWidget(self.name_edit)
        
        self.host_edit = QLineEdit()
        self.host_edit.setObjectName("host")
        layout.addWidget(QLabel("Host:"))
        layout.addWidget(self.host_edit)
        
        # Кнопки управления
        btn_undo = QPushButton("Undo")
        btn_undo.clicked.connect(self._undo)
        layout.addWidget(btn_undo)
        
        btn_redo = QPushButton("Redo")
        btn_redo.clicked.connect(self._redo)
        layout.addWidget(btn_redo)
        
        btn_save = QPushButton("Save State")
        btn_save.clicked.connect(self._save_state)
        layout.addWidget(btn_save)
        
        btn_load = QPushButton("Load State")
        btn_load.clicked.connect(self._load_state)
        layout.addWidget(btn_load)
    
    def _setup_state(self):
        """Настройка состояния."""
        # Регистрируем состояние
        self.view_state.register(
            'main_window',
            default_value={
                'name': '',
                'host': '',
                'window_size': (800, 600)
            },
            persist=True
        )
        
        # Добавляем наблюдателя
        self.view_state.add_observer('main_window', self)
        
        # Привязываем виджеты к состоянию
        self.view_state.bind_widget('main_window', self.name_edit)
        self.view_state.bind_widget('main_window', self.host_edit)
    
    def on_state_changed(self, state_id: str, event: StateChangeEvent) -> None:
        """Реализация IStateObserver."""
        print(f"State changed: {state_id}.{event.change_type}")
        print(f"  Old: {event.old_value}")
        print(f"  New: {event.new_value}")
    
    def _undo(self):
        """Отменить изменение."""
        if self.view_state.undo('main_window'):
            print("Undo successful")
        else:
            print("Nothing to undo")
    
    def _redo(self):
        """Повторить изменение."""
        if self.view_state.redo('main_window'):
            print("Redo successful")
        else:
            print("Nothing to redo")
    
    def _save_state(self):
        """Сохранить состояние в файл."""
        if self.view_state.save_to_file('main_window', 'window_state.json'):
            QMessageBox.information(self, "Success", "State saved")
        else:
            QMessageBox.critical(self, "Error", "Failed to save state")
    
    def _load_state(self):
        """Загрузить состояние из файла."""
        if self.view_state.load_from_file('main_window', 'window_state.json'):
            QMessageBox.information(self, "Success", "State loaded")
        else:
            QMessageBox.critical(self, "Error", "Failed to load state")


# =============================================================================
# Пример 4: Интеграция WorkerBridge
# =============================================================================

class MainWindowWithWorkerBridge(QMainWindow, IWorkerClient):
    """
    Пример MainWindow с использованием WorkerBridge.
    
    Демонстрирует:
    - Регистрацию workers
    - Централизованную обработку событий
    - Управление жизненным циклом workers
    """
    
    def __init__(self):
        super().__init__()
        self.worker_bridge = WorkerBridge()
        self._setup_ui()
    
    def _setup_ui(self):
        """Настройка интерфейса."""
        self.setWindowTitle("MainWindow with WorkerBridge")
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        btn_execute = QPushButton("Execute Command")
        btn_execute.clicked.connect(self._execute_command)
        layout.addWidget(btn_execute)
        
        btn_abort = QPushButton("Abort All")
        btn_abort.clicked.connect(self._abort_all)
        layout.addWidget(btn_abort)
        
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
    
    def _execute_command(self):
        """Выполнить команду через worker."""
        # Создаем устройства и команды
        devices = [
            DeviceModel({"name": "Device 1", "host": "192.168.1.1"}),
            DeviceModel({"name": "Device 2", "host": "192.168.1.2"})
        ]
        commands = [Command({"type": "ssh", "text": "uptime"})]
        
        # Создаем worker
        worker = CommandWorker(devices, commands)
        
        # Регистрируем с автоматическим управлением потоком
        self.worker_bridge.register_threaded_worker(
            worker=worker,
            worker_id='cmd_001',
            client=self,
            context={'command': 'uptime', 'devices': len(devices)}
        )
        
        self.status_label.setText("Command started...")
    
    def _abort_all(self):
        """Прервать все workers."""
        aborted = self.worker_bridge.abort_all()
        self.status_label.setText(f"Aborted {aborted} workers")
    
    # Реализация IWorkerClient
    
    def on_worker_started(self, worker_id: str, context: Dict[str, Any]) -> None:
        """Worker запущен."""
        print(f"Worker {worker_id} started with context: {context}")
        self.status_label.setText(f"Worker {worker_id} started")
    
    def on_worker_progress(self, worker_id: str, progress: Any) -> None:
        """Прогресс worker."""
        device, output = progress if isinstance(progress, tuple) else (None, progress)
        print(f"Worker {worker_id} progress: {device} -> {output[:50]}...")
    
    def on_worker_finished(self, worker_id: str, result: Any) -> None:
        """Worker завершен."""
        print(f"Worker {worker_id} finished")
        self.status_label.setText(f"Worker {worker_id} finished")
        
        # Получаем контекст
        ctx = self.worker_bridge.get_context(worker_id)
        if ctx:
            print(f"Execution time: {ctx.finished_at - ctx.started_at}")
    
    def on_worker_error(self, worker_id: str, error: str) -> None:
        """Ошибка worker."""
        print(f"Worker {worker_id} error: {error}")
        QMessageBox.critical(self, "Worker Error", f"Worker {worker_id}: {error}")


# =============================================================================
# Пример 5: Полная интеграция всех компонентов
# =============================================================================

class FullyIntegratedMainWindow(
    QMainWindow,
    IEventHandler,
    IDialogClient,
    IStateObserver,
    IWorkerClient
):
    """
    Полностью интегрированное MainWindow.
    
    Использует все архитектурные компоненты совместно.
    """
    
    def __init__(self):
        super().__init__()
        
        # Инициализация компонентов архитектуры
        self.event_bus = EventBus()
        self.dialog_manager = DialogManager(self.event_bus)
        self.view_state = ViewState()
        self.worker_bridge = WorkerBridge(self.event_bus)
        
        # Связываем компоненты
        self._setup_architecture()
        self._setup_ui()
    
    def _setup_architecture(self):
        """Настройка архитектурных компонентов."""
        # Регистрируем состояния
        self.view_state.register('main_window', persist=True)
        self.view_state.register('selected_devices', default_value=[])
        
        # Добавляем наблюдателя
        self.view_state.add_observer('main_window', self)
        
        # Подписываемся на события
        self.event_bus.subscribe(EventType.DEVICE_SELECTED, self._on_device_selected_event)
        self.event_bus.subscribe(EventType.COMMAND_FINISHED, self._on_command_finished_event)
        
        # Регистрируем фабрики диалогов
        self.dialog_manager.register_factory(
            'device_edit',
            lambda parent=None, **kwargs: DeviceEditDialog(kwargs.get('device'), parent)
        )
    
    def _setup_ui(self):
        """Настройка интерфейса."""
        self.setWindowTitle("Fully Integrated MainWindow")
        self.resize(1024, 768)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # UI компоненты...
        info_label = QLabel("This window uses all architecture components")
        layout.addWidget(info_label)
    
    # Реализация интерфейсов
    
    def handle_event(self, event: Event) -> None:
        pass
    
    def on_dialog_result(self, dialog_id: str, result: DialogResult, data: Any) -> None:
        # Публикуем событие о результате диалога
        self.event_bus.publish_typed(
            event_type=EventType.DIALOG_RESULT,
            source='MainWindow',
            data={'dialog_id': dialog_id, 'result': result.name, 'data': data}
        )
    
    def on_state_changed(self, state_id: str, event: StateChangeEvent) -> None:
        # Публикуем событие об изменении состояния
        self.event_bus.publish_typed(
            event_type=EventType.UI_STATE_CHANGED,
            source='MainWindow',
            data={'state_id': state_id, 'change': event.change_type.name}
        )
    
    def on_worker_started(self, worker_id: str, context: Dict[str, Any]) -> None:
        pass
    
    def on_worker_progress(self, worker_id: str, progress: Any) -> None:
        pass
    
    def on_worker_finished(self, worker_id: str, result: Any) -> None:
        # Сохраняем результаты в состояние
        self.view_state.set('main_window', f'last_result_{worker_id}', result)
    
    def on_worker_error(self, worker_id: str, error: str) -> None:
        # Публикуем событие об ошибке
        self.event_bus.publish_typed(
            event_type=EventType.WORKER_ERROR,
            source='MainWindow',
            data={'worker_id': worker_id, 'error': error}
        )
    
    # Обработчики событий
    
    def _on_device_selected_event(self, event: Event):
        """Обработка события выбора устройства."""
        device = event.get('device')
        if device:
            # Обновляем состояние
            devices = self.view_state.get('selected_devices', default=[])
            devices.append(device.to_dict())
            self.view_state.set('selected_devices', 'list', devices)
    
    def _on_command_finished_event(self, event: Event):
        """Обработка события завершения команды."""
        # Можно открыть диалог с результатом
        pass


# =============================================================================
# Пример 6: Фильтрация событий
# =============================================================================

class DeviceSpecificEventFilter:
    """Фильтр событий для конкретного устройства."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
    
    def matches(self, event: Event) -> bool:
        """Проверить, относится ли событие к нашему устройству."""
        return event.get('device_id') == self.device_id


class DeviceMonitor:
    """Монитор конкретного устройства."""
    
    def __init__(self, device_id: str, event_bus: EventBus):
        self.device_id = device_id
        self.event_bus = event_bus
        
        # Создаем фильтр
        from src.architecture.event_bus import EventFilter
        device_filter = EventFilter(
            event_types={EventType.DEVICE_UPDATED, EventType.COMMAND_PROGRESS},
            data_filter=lambda data: data.get('device_id') == device_id
        )
        
        # Подписываемся с фильтром
        self._handler_id = event_bus.subscribe(
            [EventType.DEVICE_UPDATED, EventType.COMMAND_PROGRESS],
            self._on_device_event,
            event_filter=device_filter
        )
    
    def _on_device_event(self, event: Event):
        """Обработка событий устройства."""
        print(f"[Monitor {self.device_id}] {event.event_type.name}: {event.data}")
    
    def stop(self):
        """Остановить мониторинг."""
        self.event_bus.unsubscribe(self._handler_id)


# =============================================================================
# Пример 7: Команды с undo/redo
# =============================================================================

from src.architecture.interfaces import ICommand

class DeleteDeviceCommand(ICommand):
    """Команда удаления устройства с поддержкой undo."""
    
    def __init__(self, device_id: str, view_state: ViewState, event_bus: EventBus):
        self.device_id = device_id
        self.view_state = view_state
        self.event_bus = event_bus
        self._backup = None
    
    def execute(self) -> None:
        """Выполнить удаление."""
        # Сохраняем бэкап
        self._backup = self.view_state.get('devices', self.device_id)
        
        # Удаляем
        devices = self.view_state.get('devices', default={})
        if self.device_id in devices:
            del devices[self.device_id]
            self.view_state.update('devices', devices)
        
        # Публикуем событие
        self.event_bus.publish_typed(
            event_type=EventType.DEVICE_REMOVED,
            source='DeleteDeviceCommand',
            data={'device_id': self.device_id}
        )
    
    def undo(self) -> None:
        """Отменить удаление."""
        if self._backup:
            devices = self.view_state.get('devices', default={})
            devices[self.device_id] = self._backup
            self.view_state.update('devices', devices)
            
            self.event_bus.publish_typed(
                event_type=EventType.DEVICE_ADDED,
                source='DeleteDeviceCommand',
                data={'device_id': self.device_id, 'restored': True}
            )
    
    def redo(self) -> None:
        """Повторить удаление."""
        self.execute()


# =============================================================================
# Пример использования
# =============================================================================

if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    # Создаем окно с полной интеграцией
    window = FullyIntegratedMainWindow()
    window.show()
    
    sys.exit(app.exec())
