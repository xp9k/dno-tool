import os
import json
from typing import List, Dict, Optional, Any, Tuple
from PySide6.QtWidgets import (QApplication, QWidget, QTreeView, QListView, QSplitter,
                              QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QPushButton, 
                              QAbstractItemView, QMenuBar, QFileDialog, QMenu,
                              QInputDialog, QLineEdit, QComboBox, QTableWidget,
                              QHeaderView, QMessageBox, QDialog)
from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal
from PySide6.QtGui import QIcon, QStandardItemModel, QAction

from src.ui.widgets import (DeviceTreeView, CustomTreeItem,
                        CustomListItem, DeviceListView,
                        CommandResultTable,
                        TaskComboBox)

from src.domain.models import DeviceModel

from src.ui.dialogs import (DeviceEditDialog, ConfigDialog,
                        CommandResultDialog, DeviceInfoDialog,
                        BashViewerDialog, IPScannerDialog,
                        CommandEditorDialog, RemoteRecordingDialog,
                        PolkitEditorDialog)

from src.workers import CommandWorker, Command

from src.config import *

from PySide6.QtCore import Signal

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Signal

from src.logger import logger
from src.data import datastore

# ARCHITECTURE: Импорт архитектурных компонентов
from src.architecture import (
    EventBus, EventType, Event,
    DialogManager, DialogResult, DialogConfig,
    WorkerBridge, ViewState
)

# ARCHITECTURE: Импорт сервисов и DI контейнера
from src.di import get_container
from src.services import DeviceService, CommandService, ConfigService

class MainWindow(QWidget):
    """Main application window with integrated architecture components"""
    command_progress = Signal(str, str)  # hostname, output
    
    def __init__(self):
        super().__init__()
        
        # ARCHITECTURE: Инициализация архитектурных компонентов
        self._init_architecture()
        

        self.init_ui()
        
        self.load_commands()
        self.data = self.load_initial_data()

        self.setAcceptDrops(True)

    def _init_architecture(self):
        """ARCHITECTURE: Инициализация архитектурных компонентов и сервисов"""
        # Получаем DI контейнер (сервисы уже инициализированы в __main__.py)
        self.container = get_container()
        
        # Получаем сервисы из контейнера
        self.device_service = self.container.resolve(DeviceService)
        self.command_service = self.container.resolve(CommandService)
        self.config_service = self.container.resolve(ConfigService)
        
        # Получаем архитектурные компоненты (event_bus уже передан через конструктор)
        self.event_bus = self.container.resolve(EventBus)
        self.dialog_manager = self.container.resolve(DialogManager)
        self.worker_bridge = self.container.resolve(WorkerBridge)
        self.view_state = self.container.resolve(ViewState)

        # Регистрация фабрик диалогов
        self._register_dialog_factories()

        # Подписка на события
        self._subscribe_to_events()

        # Инициализируем состояние UI
        self._init_view_state()

        logger.debug("MainWindow: Architecture components and services initialized")

    def _register_dialog_factories(self):
        """ARCHITECTURE: Регистрация фабрик для создания диалогов"""
        # Основные диалоги
        self.dialog_manager.register_factory('config', lambda parent=None, **kwargs: ConfigDialog(parent))
        self.dialog_manager.register_factory('about', lambda parent=None, **kwargs: self._create_about_dialog(parent))
        self.dialog_manager.register_factory('scanner', lambda parent=None, **kwargs: self._create_scanner_dialog(parent))
        
        # Диалоги устройств
        self.dialog_manager.register_factory('device_edit', lambda parent=None, device=None, **kwargs: DeviceEditDialog(device, parent))
        self.dialog_manager.register_factory('device_info', lambda parent=None, device=None, **kwargs: DeviceInfoDialog(device, parent))
        
        # Диалоги команд
        self.dialog_manager.register_factory('command_edit', lambda parent=None, **kwargs: CommandEditorDialog(parent))
        self.dialog_manager.register_factory('command_info', lambda parent=None, command=None, **kwargs: CommandInfoDialog(command, parent))
        self.dialog_manager.register_factory('command_result', lambda parent=None, hostname='', output='', status='', device_iid=None, **kwargs: CommandResultDialog(hostname, output, status, parent, device_iid=device_iid))
        
        # Диалоги редакторов
        self.dialog_manager.register_factory('bash_editor', lambda parent=None, **kwargs: BashEditorDialog(parent))
        self.dialog_manager.register_factory('bash_viewer', lambda parent=None, initial_text='', **kwargs: BashViewerDialog(parent, initial_text))
        
        # Диалоги SSH
        self.dialog_manager.register_factory('ssh_manage', lambda parent=None, **kwargs: self._create_ssh_manage_dialog(parent))
        self.dialog_manager.register_factory('known_hosts', lambda parent=None, **kwargs: self._create_known_hosts_dialog(parent))
        
        # Диалоги SFTP
        self.dialog_manager.register_factory('sftp', lambda parent=None, **kwargs: SFTPDialog(parent))
        self.dialog_manager.register_factory('sftp_command_edit', lambda parent=None, **kwargs: SFTPCommandEditorDialog(parent))
        self.dialog_manager.register_factory('remote_file_info', lambda parent=None, **kwargs: RemoteFileInfoDialog(parent))
        self.dialog_manager.register_factory('remote_file_preview', lambda parent=None, **kwargs: RemoteFilePreviewDialog(parent))
        
        # Диалоги параметров
        self.dialog_manager.register_factory('params', lambda parent=None, params=None, **kwargs: ParamsInputDialog(params, parent))
        
        # Диалоги утилит
        self.dialog_manager.register_factory('pinger', lambda parent=None, **kwargs: PingMonitorDialog(parent))
        self.dialog_manager.register_factory('ip_scanner_ports', lambda parent=None, **kwargs: PortsEditorDialog(parent))
        self.dialog_manager.register_factory('remote_recording', lambda parent=None, device=None, **kwargs: RemoteRecordingDialog(device, parent))

        logger.debug("MainWindow: Dialog factories registered")

    def _create_about_dialog(self, parent=None):
        """ARCHITECTURE: Фабричный метод для создания AboutDialog"""
        from src.ui.dialogs.common.about import AboutDialog
        return AboutDialog(parent)

    def _create_scanner_dialog(self, parent=None):
        """ARCHITECTURE: Фабричный метод для создания IPScannerDialog с подключенными сигналами"""
        dialog = IPScannerDialog(parent, main_window=self)
        dialog.exported_device.connect(self._on_scanner_device_import)
        return dialog
    
    def _create_known_hosts_dialog(self, parent=None):
        """ARCHITECTURE: Фабричный метод для создания KnownHostsDialog"""
        from config import LOGS_PATH
        from src.ui.dialogs.tools.known_hosts import KnownHostsDialog
        known_hosts_path = os.path.expanduser('~/.ssh/known_hosts')
        return KnownHostsDialog(known_hosts_path, parent)
    
    def _create_ssh_manage_dialog(self, parent=None):
        """ARCHITECTURE: Фабричный метод для создания SSHManageDialog"""
        from src.ui.dialogs.tools.ssh_manage import SSHManageDialog
        # ARCHITECTURE: Получаем данные через сервис
        tree_data = self.device_service.get_devices_raw()
        return SSHManageDialog(tree_data, parent)

    def _on_scanner_device_import(self, devices, folder):
        """ARCHITECTURE: Обработчик импорта устройств из сканнера"""
        # Определяем текущую выделенную папку в реальном времени
        current_folder = folder
        if current_folder is None and hasattr(self, 'treeview'):
            selected_indexes = self.treeview.selectedIndexes()
            if selected_indexes:
                selected_item = self.treeview._model.itemFromIndex(selected_indexes[0])
                # Проверяем что это папка (не устройство)
                if selected_item and (not hasattr(selected_item, 'device') or selected_item.device is None):
                    current_folder = selected_item

        # Добавляем каждое устройство через treeview (сигнал data_changed сработает автоматически)
        for device in devices:
            if current_folder:
                # Добавляем в текущую выделенную папку
                self.treeview.add_device(current_folder, device)
            else:
                # Добавляем в корень
                self.treeview.add_device(self.treeview._model.invisibleRootItem(), device)

        self.treeview.sort_tree()
        # ARCHITECTURE: Данные сохранятся автоматически через сигнал data_changed

        # Публикуем событие о добавлении устройств
        self.event_bus.publish_typed(
            EventType.DEVICE_ADDED,
            source='MainWindow',
            data={'device_count': len(devices), 'folder': current_folder.text() if current_folder else 'root'}
        )

    def _subscribe_to_events(self):
        """ARCHITECTURE: Подписка на события EventBus"""
        # Подписываемся на события workers
        self.event_bus.subscribe(EventType.WORKER_STARTED, self._on_worker_event)
        self.event_bus.subscribe(EventType.WORKER_FINISHED, self._on_worker_event)
        self.event_bus.subscribe(EventType.WORKER_ERROR, self._on_worker_event)

        # Подписываемся на события команд
        self.event_bus.subscribe(EventType.COMMAND_STARTED, self._on_command_event)
        self.event_bus.subscribe(EventType.COMMAND_FINISHED, self._on_command_event)

        logger.debug("MainWindow: Subscribed to EventBus events")

    def _init_view_state(self):
        """ARCHITECTURE: Инициализация состояния UI"""
        # Регистрируем состояние главного окна
        self.view_state.register('main_window', persist=True)
        
        # Восстанавливаем сохраненные размеры и позицию
        saved_geometry = self.view_state.get('main_window', 'geometry')
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        
        # Привязываем splitter к состоянию
        if hasattr(self, 'right_splitter'):
            self.view_state.bind_widget('main_window', self.right_splitter, 'sizes', 'splitter_sizes')

    def _save_view_state(self):
        """ARCHITECTURE: Сохранение состояния UI"""
        # Сохраняем геометрию окна
        self.view_state.set('main_window', 'geometry', self.saveGeometry())
        
        # Сохраняем состояние splitter
        if hasattr(self, 'right_splitter'):
            self.view_state.set('main_window', 'splitter_sizes', self.right_splitter.sizes())
        
        # Сохраняем состояние дерева (раскрытые узлы)
        if hasattr(self, 'treeview'):
            expanded_state = self._get_tree_expanded_state()
            self.view_state.set('main_window', 'tree_expanded', expanded_state)
        

    def _get_tree_expanded_state(self) -> list:
        """Получить список раскрытых узлов дерева"""
        if not hasattr(self, 'treeview'):
            return []
        
        expanded = []
        model = self.treeview._model
        root = model.invisibleRootItem()
        
        def collect_expanded(item, path=""):
            if item.rowCount() > 0:
                index = model.indexFromItem(item)
                if self.treeview.isExpanded(index):
                    expanded.append(path + item.text())
                for i in range(item.rowCount()):
                    child = item.child(i)
                    collect_expanded(child, path + item.text() + "/")
        
        collect_expanded(root)
        return expanded

    def _restore_tree_expanded_state(self):
        """Восстановить состояние раскрытых узлов дерева"""
        if not hasattr(self, 'treeview'):
            return
        
        expanded_state = self.view_state.get('main_window', 'tree_expanded', [])
        if not expanded_state:
            return
        
        model = self.treeview._model
        
        def find_item_by_path(path):
            parts = path.split('/')
            current = model.invisibleRootItem()
            for part in parts:
                found = None
                for i in range(current.rowCount()):
                    if current.child(i).text() == part:
                        found = current.child(i)
                        break
                if found:
                    current = found
                else:
                    return None
            return current
        
        for item_path in expanded_state:
            item = find_item_by_path(item_path)
            if item:
                index = model.indexFromItem(item)
                self.treeview.setExpanded(index, True)

    def _on_worker_event(self, event: Event):
        """ARCHITECTURE: Обработчик событий workers"""
        worker_id = event.get('worker_id', 'unknown')
        logger.debug(f"MainWindow: Worker event {event.event_type.name} from {worker_id}")

    def _on_command_event(self, event: Event):
        """ARCHITECTURE: Обработчик событий команд"""
        logger.debug(f"MainWindow: Command event {event.event_type.name}")

    # ========== ARCHITECTURE: Обработчики сигналов сервисов ==========

    def _on_tree_data_changed(self, new_data: list):
        """
        Обработчик сигнала изменения данных дерева.

        Args:
            new_data: Новые данные дерева устройств
        """
        # Сохраняем данные через сервис (только в памяти)
        success = self.device_service.save_devices(new_data)

        if success:
            logger.debug("MainWindow: Tree data saved to service (in-memory)")
        else:
            logger.error("MainWindow: Failed to save tree data via DeviceService")
            QMessageBox.critical(
                self,
                "Ошибка",
                "Не удалось сохранить данные устройств. Проверьте лог файл."
            )

    # ========== КОНЕЦ ARCHITECTURE ==========

    def init_ui(self):
        """Инициализация интерфейса"""
        self.setup_menu()
        self.setup_frames()
        self.setup_tree_view()
        self.setup_command_panel()  # Command panel first
        self.setup_list_view()      # List second
        self.setup_result_table()   # Table third
        # self.setup_buttons()
        self.setup_layout()
        self.setup_models()
        self.setup_connections()


    def setup_frames(self):
        """Setup main frames"""
        # Left frame
        self.tree_frame = QFrame()
        self.tree_frame.setFrameShape(QFrame.Shape.NoFrame)
        tree_layout = QVBoxLayout()
        tree_layout.setContentsMargins(2, 2, 2, 2)
        tree_layout.setSpacing(4)
        self.tree_frame.setLayout(tree_layout)

        # Right frame with command panel, list and table
        self.right_frame = QFrame()
        self.right_frame.setFrameShape(QFrame.Shape.NoFrame)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(2, 2, 2, 2)
        right_layout.setSpacing(4)
        self.right_frame.setLayout(right_layout)

    def setup_command_panel(self):
        """Setup command execution panel"""
        command_frame = QFrame()
        command_frame.setFrameShape(QFrame.Shape.NoFrame)
        command_layout = QHBoxLayout()
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.setSpacing(4)
        command_frame.setLayout(command_layout)

        self.command_combo_box = TaskComboBox()
        # self.command_combo_box.setEditable(False)

        command_layout.addWidget(self.command_combo_box, 1)

        self.execute_button = QPushButton("Выполнить")
        self.execute_button.setFixedWidth(100)

        self.command_info_button = QPushButton("Информация")
        self.command_info_button.setFixedWidth(100)

        self.command_abort_button = QPushButton("Отменить")
        self.command_abort_button.setFixedWidth(100)

        self.command_abort_button.setEnabled(False)
        self.command_abort_button.setVisible(False)

        command_layout.addWidget(self.command_info_button)
        command_layout.addWidget(self.command_abort_button)
        command_layout.addWidget(self.execute_button)
        self.right_frame.layout().addWidget(command_frame)

    def setup_menu(self):
        """Настройка главного меню"""
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setNativeMenuBar(False)

        # File menu
        file_menu = self.menu_bar.addMenu("📁 Файл")

        hosts_menu = file_menu.addMenu(QIcon(ICONS.get('menu_hosts', '')), "Хосты")
        load_action = QAction(QIcon(ICONS.get('menu_import_host', '')), "Импортировать список", self)
        load_action.triggered.connect(self.on_menu_load_tree)
        hosts_menu.addAction(load_action)

        save_as_action = QAction(QIcon(ICONS.get('menu_export_host', '')), "Экспортировать список", self)
        save_as_action.triggered.connect(self.save_tree)
        hosts_menu.addAction(save_as_action)

        hosts_menu.addSeparator()

        save_action = QAction(QIcon(ICONS.get('menu_save', '')), "Сохранить список", self)
        save_action.triggered.connect(lambda : self.save_tree(DEFAULT_HOSTS_FILE))
        hosts_menu.addAction(save_action)

        file_menu.addSeparator()
        commands_menu = file_menu.addMenu(QIcon(ICONS.get('menu_commands', '')), "Команды")
        import_commands_action = QAction(QIcon(ICONS.get('menu_import', '')), "Импортировать команды", self)
        import_commands_action.triggered.connect(self.import_commands_from_file)
        commands_menu.addAction(import_commands_action)

        import_server_action = QAction(QIcon(ICONS.get('menu_import', '')), "Импортировать команды с сервера", self)
        import_server_action.triggered.connect(self.import_commands_from_server)
        commands_menu.addAction(import_server_action)

        export_commands_action = QAction(QIcon(ICONS.get('menu_export', '')), "Экспортировать команды", self)
        export_commands_action.triggered.connect(lambda : self.save_commands_to_file())
        commands_menu.addAction(export_commands_action)

        commands_menu.addSeparator()

        save_commands_action = QAction(QIcon(ICONS.get('menu_save', '')), "Сохранить команды", self)
        save_commands_action.triggered.connect(lambda: self.save_commands_to_file(DEFAULT_COMMANDS_FILE))
        commands_menu.addAction(save_commands_action)

        file_menu.addSeparator()
        exit_action = QAction(QIcon(ICONS.get('menu_exit', '')), "Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        settings_menu = self.menu_bar.addMenu("⚙️ Настройки")
        config_action = QAction(QIcon(ICONS.get('menu_tools', '')), "Конфигурация", self)
        config_action.triggered.connect(self.show_config_dialog)
        settings_menu.addAction(config_action)

        command_edit_action = QAction(QIcon(ICONS.get('command', '')), "Редактировать задачи", self)
        command_edit_action.triggered.connect(self.show_command_edit_dialog)
        settings_menu.addAction(command_edit_action)

        tools_menu = self.menu_bar.addMenu("🔧 Инструменты")
        scanner_action = QAction(QIcon(ICONS.get('menu_scanner', '')), "Сканнер хостов", self)
        scanner_action.triggered.connect(self.show_scanner_dialog)
        tools_menu.addAction(scanner_action)

        ssh_manage_action = QAction(QIcon(ICONS.get('key_exists', '')), "Управление SSH ключами", self)
        ssh_manage_action.triggered.connect(self.show_ssh_manage_dialog)
        tools_menu.addAction(ssh_manage_action)

        try:
            import pyqtgraph  # noqa: F401
            pinger_action = QAction(QIcon(ICONS.get('menu_ping', '')), "Пингер", self)
            pinger_action.triggered.connect(self.show_pinger_dialog)
            tools_menu.addAction(pinger_action)
        except ImportError:
            logger.warning("pyqtgraph не установлен - пингер недоступен")

        help_menu = self.menu_bar.addMenu("❓ Помощь")
        about_action = QAction(QIcon(ICONS.get('menu_info', '')), "О программе", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)


    def show_about_dialog(self):
        """ARCHITECTURE: Показать диалог 'О программе' через DialogManager"""
        dialog_id = self.dialog_manager.open_typed(
            'about',
            modal=True,
            parent=self
        )
        if dialog_id:
            logger.debug(f"MainWindow: About dialog opened with id={dialog_id}")
        else:
            # Fallback: открываем напрямую если фабрика не сработала
            from src.ui.dialogs.common.about import AboutDialog
            dialog = AboutDialog(self)
            dialog.exec_()


    def import_commands_from_file(self):
        """Импорт команд из файла"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Импортировать команды",
            "",
            "JSON Files (*.json)"
        )
        if file_path:
            # Используем QMessageBox для выбора действия
            if QMessageBox.question(self, "Импорт команд", "Заменить текущие команды?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
                return
            
            # ARCHITECTURE: Используем сервис для загрузки
            success, message = self.command_service.import_commands_from_file(file_path)
            
            if success:
                # Получаем данные из сервиса
                commands = self.command_service.get_all_commands()
                self.command_combo_box.clear()
                self.command_combo_box.load_tasks(commands)
                self.command_combo_box.select_first_task()
                
                QMessageBox.information(self, "Импорт команд", message)

                # ARCHITECTURE: Публикуем событие об изменении данных
                self.event_bus.publish_typed(
                    EventType.DATA_CHANGED,
                    source='MainWindow',
                    data={'type': 'commands_imported', 'count': len(commands)}
                )
                
                logger.info(f"MainWindow: {message}")
            else:
                logger.error(f"MainWindow: {message}")
                QMessageBox.critical(self, "Ошибка импорта", message)

    def import_commands_from_server(self):
        """Импорт команд с GitHub через Contents API"""
        from src.services.updater import download_commands_json

        self.statusBar().showMessage("Загрузка команд с сервера...", 0) if hasattr(self, 'statusBar') else None
        QApplication.processEvents()

        result = download_commands_json()

        if not result["success"]:
            self.statusBar().showMessage("", 0) if hasattr(self, 'statusBar') else None
            QMessageBox.critical(self, "Ошибка", result["message"])
            return

        if QMessageBox.question(self, "Импорт команд", "Заменить текущие команды загруженными с сервера?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
            self.statusBar().showMessage("", 0) if hasattr(self, 'statusBar') else None
            return

        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        json.dump(result["data"], tmp, ensure_ascii=False, indent=4)
        tmp.close()

        success, message = self.command_service.import_commands_from_file(tmp.name)
        os.unlink(tmp.name)

        if success:
            commands = self.command_service.get_all_commands()
            self.command_combo_box.clear()
            self.command_combo_box.load_tasks(commands)
            self.command_combo_box.select_first_task()
            self.statusBar().showMessage(result["message"], 3000) if hasattr(self, 'statusBar') else None
            logger.info(f"MainWindow: commands imported from server")
        else:
            self.statusBar().showMessage("", 0) if hasattr(self, 'statusBar') else None
            QMessageBox.critical(self, "Ошибка импорта", message)


    def save_commands_to_file(self, file_path: str = None):
        """Сохранение команд в файл"""
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Сохранить файл",
                "",
                "JSON Files (*.json)"
            )
        
        # ARCHITECTURE: Используем сервис для сохранения
        success, message = self.command_service.export_commands_to_file(file_path)
        
        if success:
            logger.info(f"MainWindow: {message}")
            self.statusBar().showMessage(message, 3000) if hasattr(self, 'statusBar') else None
        else:
            logger.error(f"MainWindow: {message}")
            QMessageBox.critical(self, "Ошибка", message)


    def show_config_dialog(self):
        """ARCHITECTURE: Показать диалог конфигурации через DialogManager"""
        config = DialogConfig(
            dialog_class=ConfigDialog,
            parent=self,
            modal=True,
            on_result=self._on_config_dialog_result
        )
        dialog_id = self.dialog_manager.open(config)
        logger.debug(f"MainWindow: Config dialog opened with id={dialog_id}")

    def show_remote_recording_dialog(self, device: DeviceModel):
        """ARCHITECTURE: Показать диалог удалённой записи"""
        if not device:
            QMessageBox.information(self, "Удалённая запись", "Устройство не выбрано.")
            return
        dialog = RemoteRecordingDialog(device, self)
        dialog.show()
        logger.debug(f"MainWindow: Remote recording dialog opened for {device.host}")

    def show_polkit_editor_dialog(self, device: DeviceModel):
        """Показать диалог редактора политик Polkit"""
        if not device:
            QMessageBox.information(self, "Редактор политик", "Устройство не выбрано.")
            return
        dialog = PolkitEditorDialog(device, self)
        dialog.exec()
        logger.debug(f"MainWindow: Polkit editor dialog closed for {device.host}")

    def _on_config_dialog_result(self, result: DialogResult, data: Any):
        """ARCHITECTURE: Обработчик результата диалога конфигурации"""
        if result == DialogResult.ACCEPTED:
            logger.info("MainWindow: Configuration saved")
            # Публикуем событие об изменении конфигурации
            self.event_bus.publish_typed(
                EventType.DATA_SAVED,
                source='MainWindow',
                data={'type': 'config'}
            )

    def show_scanner_dialog(self):
        """ARCHITECTURE: Показать диалог сканнера через DialogManager"""
        # Создаем диалог с подключенными сигналами
        from src.ui.dialogs.tools.ip_scanner import IPScannerDialog
        
        dialog = IPScannerDialog(self)
        dialog.exported_device.connect(self._on_scanner_device_import)
        
        config = DialogConfig(
            dialog_class=type(dialog),
            parent=self,
            modal=False,
            on_close=lambda: logger.debug("MainWindow: Scanner dialog closed")
        )
        
        # Используем диалог напрямую, так как он уже создан
        dialog.setModal(False)
        dialog.show()
        
        # Регистрируем в менеджере для отслеживания
        dialog_id = self.dialog_manager._generate_id(type(dialog))
        logger.debug(f"MainWindow: Scanner dialog opened with id={dialog_id}")

    def setup_tree_view(self):
        """Настройка дерева устройств"""
        self.treeview = DeviceTreeView()
        self.tree_frame.layout().addWidget(self.treeview)


    def setup_list_view(self):
        """Setup device list view"""
        list_frame = QFrame()
        list_frame.setFrameShape(QFrame.Shape.NoFrame)
        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 2)
        list_frame.setLayout(list_layout)

        self.listview = DeviceListView()

        list_layout.addWidget(self.listview)

        # Create splitter between list and table
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(list_frame)
        self.right_frame.layout().addWidget(self.right_splitter)

        # Force initial update
        self.listview.viewport().update()

    def setup_result_table(self):
        """Setup result table"""
        table_frame = QFrame()
        table_frame.setFrameShape(QFrame.Shape.NoFrame)
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 2, 0, 0)
        table_frame.setLayout(table_layout)
        
        self.result_table = CommandResultTable()
        self.result_table.doubleClicked.connect(self.on_result_double_clicked)
        table_layout.addWidget(self.result_table)
        
        # Add table to splitter
        self.right_splitter.addWidget(table_frame)
        
        # Set initial sizes
        self.right_splitter.setStretchFactor(0, 1)  # List gets 1 part
        self.right_splitter.setStretchFactor(1, 1)  # Table gets 1 part

        self.right_splitter.setSizes([50, 50])  # Initial sizes parts


    def setup_buttons(self):
        """Настройка кнопок управления"""
        self.button1 = QPushButton("Button 1")
        self.button2 = QPushButton("Button 2")
        self.button3 = QPushButton("Button 3")
        self.button4 = QPushButton("Button 4")

        button_frame = QFrame()
        button_frame.setLayout(QGridLayout())
        button_frame.layout().setContentsMargins(0, 0, 0, 0)

        button_frame.layout().addWidget(self.button1, 0, 0)
        button_frame.layout().addWidget(self.button2, 0, 1)
        button_frame.layout().addWidget(self.button3, 1, 0)
        button_frame.layout().addWidget(self.button4, 1, 1)

        self.tree_frame.layout().addWidget(button_frame)

    def setup_layout(self):
        """Настройка компоновки главного окна"""
        # Создаем разделитель для дерева и правой части
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.insertWidget(0, self.tree_frame)
        self.splitter.insertWidget(1, self.right_frame)
        self.splitter.setStretchFactor(1, 1)

        # Устанавливаем размер дерева в четверть окна (256 из 1024)
        self.splitter.setSizes([256, 768])

        # Основной компоновщик с отступами
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 0, 4, 4)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.menu_bar)
        main_layout.addWidget(self.splitter, 1)
        self.setLayout(main_layout)


    def setup_models(self):
        """Настройка моделей данных"""      


    def setup_connections(self):
        """Настройка сигналов и слотов"""

        if self.treeview is not None:
            self.treeview.clicked.connect(self.on_treeview_clicked)
            self.treeview.itemDoubleClicked.connect(self.on_treeview_double_clicked)
            self.treeview.fileDropped.connect(self.load_hosts_from_txt_file)
            # ARCHITECTURE: Подключаем сигнал изменений данных для обработки через сервис
            self.treeview.data_changed.connect(self._on_tree_data_changed)
            # Подключаем сигнал удаления устройства для синхронизации со списком
            self.treeview.device_removed.connect(self.remove_from_listview)

        if self.treeview._model is not None:
            self.treeview._model.itemChanged.connect(self.on_tree_item_changed)
            self.treeview._model.itemDataChanged.connect(self.on_tree_ItemDataChanged)

        if self.listview is not None:
            self.listview.clicked.connect(self.on_list_item_clicked)
            self.listview.itemDoubleClicked.connect(self.on_list_item_double_clicked)
            self.listview.commandsExecuted.connect(self.on_single_host_command_executed)
            self.listview.remoteRecordingRequested.connect(self.show_remote_recording_dialog)
            self.listview.polkitEditorRequested.connect(self.show_polkit_editor_dialog)

        self.execute_button.clicked.connect(self.on_execute_clicked)
        self.command_info_button.clicked.connect(self.on_command_info_clicked)
        self.command_abort_button.clicked.connect(self.on_command_abort_clicked)


    def closeEvent(self, event):
        """Handle main window close event"""
        # ARCHITECTURE: Сохранение состояния UI перед закрытием
        self._save_view_state()

        # ARCHITECTURE: Очистка архитектурных компонентов
        self._cleanup_architecture()

        # Останавливаем все индивидуальные таймеры хостов
        from src.workers.network import get_host_ping_timer_manager
        get_host_ping_timer_manager().stop_all()

        self.listview.clear_devices()
        self.treeview.clear_devices()
        # self.result_table.clear_results()

        # Прерываем активный worker при закрытии окна
        if hasattr(self, 'current_worker_id') and self.current_worker_id:
            logger.debug(f"MainWindow: Aborting worker on close: {self.current_worker_id}")
            self.worker_bridge.abort_worker(self.current_worker_id)
        elif hasattr(self, 'worker') and self.command_executed and self.worker is not None:
            logger.debug("MainWindow: Aborting worker directly on close")
            self.worker.abort()

        event.accept()

    def _cleanup_architecture(self):
        """ARCHITECTURE: Очистка архитектурных компонентов"""
        # Прерываем все активные workers
        if hasattr(self, 'worker_bridge'):
            aborted = self.worker_bridge.abort_all()
            if aborted > 0:
                logger.debug(f"MainWindow: Aborted {aborted} workers")
        
        # Закрываем все открытые диалоги
        if hasattr(self, 'dialog_manager'):
            closed = self.dialog_manager.close_all()
            if closed > 0:
                logger.debug(f"MainWindow: Closed {closed} dialogs")
        
        logger.debug("MainWindow: Architecture components cleaned up")


    def on_execute_clicked(self):
        """Handle execute button click"""    

        devices = self.listview.get_all_devices()        
        selected_data = self.command_combo_box.get_selected_task_data()

        if not selected_data:
            return
        
        self.execute_command(devices, selected_data)
        

    
    def on_command_info_clicked(self):
        """Handle command info button click"""
        import html
        selected_data = self.command_combo_box.get_selected_task_data()

        if not selected_data:
            return
        
        parameters = selected_data.get('params', [])

        if not selected_data:
            QMessageBox.warning(self, "Выбор команды", "Команда не выбрана.\n\nПожалуйста, выберите команду из списка.")
            return


        name = selected_data.get('name', 'N/A')
        description = selected_data.get('description', 'Нет описания.')
        
        parameters = ', '.join(str(param) for param in parameters)

        commands_data = selected_data.get('commands', [])
        commands = [
            Command(
                cmd
            ) for cmd in commands_data
        ] 

        if not commands: commands = []
        timeout = selected_data.get('timeout', 'N/A')

        # commands = ''.join(f"{cmd.commandType}:<pre style=\"%TGSTYLE%\">{str(html.escape(cmd.text))}</pre>" for cmd in commands)
        commands = ''.join(f"{cmd.commandType}:<br/>{str(html.escape(cmd.text))}<br/><br/>" for cmd in commands)
        
        message_text = (
            f"<p><b>Имя команды</b>: {name}</p>"            
            f"<p><b>Описание</b>: {description}</p>"
            f"<p><b>Параметры</b>: {parameters}</p>"
            f"<p><b>Команды</b>:<p>{commands}</p>"
            f"<p><b>Таймаут</b>: {timeout} сек.</p>"
        )
        dialog = BashViewerDialog(self, initial_text = message_text)            
        dialog.setWindowTitle("Информация о выбранной команде")
        dialog.editor.setReadOnly(True)
        dialog.exec_()
            


    def on_command_abort_clicked(self):
        """Handle command abort button click"""
        logger.debug("MainWindow: Abort button clicked")
        
        # Mark all pending results as cancelled before aborting worker
        if hasattr(self, 'result_table') and self.result_table:
            self.result_table.mark_pending_as_cancelled()
        
        # Используем WorkerBridge для корректного прерывания worker
        if hasattr(self, 'current_worker_id') and self.current_worker_id:
            logger.debug(f"MainWindow: Aborting worker via WorkerBridge: {self.current_worker_id}")
            aborted = self.worker_bridge.abort_worker(self.current_worker_id)
            if aborted:
                logger.info(f"MainWindow: Worker {self.current_worker_id} aborted successfully")
            else:
                logger.warning(f"MainWindow: Failed to abort worker {self.current_worker_id}")
        elif hasattr(self, 'worker') and self.worker:
            # Fallback: прерываем напрямую если worker_bridge не использовался
            logger.debug("MainWindow: Aborting worker directly (fallback)")
            self.worker.abort()
        else:
            logger.warning("MainWindow: No worker to abort")




    def load_initial_data(self):
        """Загрузка начальных данных"""
        # ARCHITECTURE: Загружаем оригинальные данные с группами через сервис
        raw_data = self.device_service.get_devices_raw()
        self.treeview.load_tree_data(raw_data)
        self.treeview.setCurrentIndex(self.treeview._model.invisibleRootItem().index())

        # Восстанавливаем состояние раскрытых узлов (если не задано expandAll)
        if config.app.expand:
            self.treeview.expandAll()
        else:
            self._restore_tree_expanded_state()




    def save_tree(self, file_path: str = None):
        """Сохранение дерева в JSON файл с сохранением иерархии"""
        # Если путь не указан (вызов из меню "Экспортировать список"), показать диалог сохранения
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Экспортировать список хостов",
                "",
                "JSON Files (*.json)"
            )
            # Если пользователь отменил диалог
            if not file_path:
                return
        
        # Получаем актуальные данные из дерева
        new_data = self.treeview.get_tree_data()

        # Сохраняем данные в datastore (обновляем _hosts_data_raw)
        success = self.device_service.save_devices(new_data)
        
        if not success:
            logger.error("MainWindow: Failed to save devices to datastore")
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить данные")
            return

        # Сохраняем в указанный файл
        success = datastore.save_hosts_to_file(file_path)
        
        if success:
            logger.info(f"MainWindow: Tree saved to {file_path}")
            self.statusBar().showMessage(f"Дерево сохранено в {file_path}", 3000) if hasattr(self, 'statusBar') else None
        else:
            logger.error(f"MainWindow: Failed to save tree to {file_path}")
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить файл")


    def on_menu_load_tree(self):
        """Загрузка дерева из JSON файла"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Загрузить файл",
            "",
            "JSON Files (*.json)"
        )
        if file_path:
            # ARCHITECTURE: Используем сервис для загрузки
            success, message = self.device_service.import_devices_from_file(file_path)
            
            if success:
                # Получаем данные из сервиса
                data = self.device_service.get_devices_raw()
                self.treeview.load_tree_data(data)
                self.treeview.setCurrentIndex(self.treeview._model.invisibleRootItem().index())

                # ARCHITECTURE: Публикуем событие о загрузке данных
                self.event_bus.publish_typed(
                    EventType.DATA_LOADED,
                    source='MainWindow',
                    data={'type': 'hosts', 'file': file_path, 'count': self.device_service.get_devices_count()}
                )
                
                logger.info(f"MainWindow: {message}")
            else:
                logger.error(f"MainWindow: {message}")
                QMessageBox.critical(self, "Ошибка импорта", message)


    def on_tree_ItemDataChanged(self, item: CustomTreeItem, role: Qt.ItemDataRole):
        """
        Обработка изменения состояния чекбокса элемента
        Добавляет/удаляет устройства в список справа
        """
        if role == Qt.ItemDataRole.CheckStateRole:
            if item.hasChildren():
                for i in range(item.rowCount()):
                    child = item.child(i)
                    # child.setCheckState(item.checkState())
                    # self.handleItemDataChanged(child, role)
            else:
                if item.checkState() == Qt.CheckState.Checked:
                    self.add_to_listview(item)
                elif item.checkState() == Qt.CheckState.Unchecked:
                    self.remove_from_listview(item.device)


    def on_tree_item_changed(self, item: CustomTreeItem):
        """Обработка изменения элемента дерева (текст, иконка)"""
        parent = item.parent()
        if parent is not None:
            icon = QIcon(ICONS['folder'])
            parent.setIcon(icon)
        
        # Сохраняем изменения в datastore
        new_data = self.treeview.get_tree_data()
        self.treeview.data_changed.emit(new_data)


    def add_to_listview(self, tree_item: CustomTreeItem):
        """
        Добавление устройства в список справа
        tree_item - элемент дерева с данными устройства
        """
        device = tree_item.device
        parent = tree_item.parent()
        # Build full path from root to the parent using '->' as separator.
        if parent is None:
            category = "Нет категории"
        else:
            parts = []
            node = parent
            while node is not None:
                parts.append(node.text())
                node = node.parent()
            parts.reverse()
            category = " -> ".join(parts) if parts else "Нет категории"

        new_list_item = self.listview.add_device(device, category=category)
        new_list_item.tree_item = tree_item  # Back-reference if needed
        self.listview.viewport().update()

    def remove_from_listview(self, device: DeviceModel):
        """
        Удаление устройства из списка справа
        
        Args:
            device: Объект DeviceModel удаляемого устройства
        """
        if device and device.host:
            self.listview.remove_device_by_host(device.host)


    def on_treeview_clicked(self, index):
        """Обработка клика по элементу дерева"""
        pass 


    def load_hosts_from_txt_file(self, files: list):
        for file in files:
            with open(file, "r", encoding="utf-8") as f:
                for line in f:
                    value = {
                        "name": line.strip(),
                        "host": line.strip(),
                    }
                    device = DeviceModel(value)
                    parent = self.treeview._model.invisibleRootItem()
                    self.treeview.add_device(parent, device)
                    # ARCHITECTURE: Используем сервис для добавления
                    self.device_service.add_device(value)

        self.treeview.setCurrentIndex(self.treeview._model.invisibleRootItem().index())
        self.treeview._model.setHorizontalHeaderLabels(["Списки"])
        self.treeview.sort_tree()

        # ARCHITECTURE: Данные сохранятся автоматически через сигнал data_changed


    def on_treeview_double_clicked(self, item: CustomTreeItem):
        """
        Обработка двойного клика по элементу дерева
        Открывает диалог редактирования устройства
        """
        if item:
            device = item.device
            if device is not None:
                dialog = DeviceEditDialog(item.device, self)
                if dialog.exec():
                    new_device = dialog.get_updated_device()
                    item.device.update(new_device.to_dict())
                    item.setText(item.device.name)
                    
                    # ARCHITECTURE: Публикуем событие об обновлении устройства
                    self.event_bus.publish_typed(
                        EventType.DEVICE_UPDATED,
                        source='MainWindow',
                        data={'device_id': device.iid}
                    )
            else:
                self.treeview.rename_folder_action(item)

        # ARCHITECTURE: Данные сохранятся автоматически через сигнал data_changed

    def on_list_item_clicked(self, index):
        """Обработка клика по элементу списка"""
        # При клике просто выбираем элемент
        pass

    def on_list_item_double_clicked(self, item: CustomListItem):
        """Handle double click on list item"""
        if item and hasattr(item, 'device'):
            # Get current icon from list item
            device = item.device
            dialog = DeviceInfoDialog(device, self)
            dialog.exec()


    def show_tree_context_menu(self, position):
        """Показать контекстное меню для элемента дерева"""


    def on_execute_thread_finished(self):
        self.command_executed = False
        self.execute_button.setEnabled(True)
        self.execute_button.setVisible(True)
        self.command_abort_button.setEnabled(False)
        self.command_abort_button.setVisible(False)
        
        # Очищаем worker_id при завершении
        if hasattr(self, 'current_worker_id'):
            self.current_worker_id = None
        
        self.worker = None
        logger.debug("MainWindow: Worker execution finished, UI reset")


    def load_commands(self):
        # self.command_combo_box.currentIndexChanged.connect(self.on_combo_changed)
        if os.path.exists(DEFAULT_COMMANDS_FILE):
            # ARCHITECTURE: Загружаем команды через сервис
            success, message = self.command_service.import_commands_from_file(DEFAULT_COMMANDS_FILE)
            
            if success:
                # Получаем данные из сервиса и загружаем в combobox
                data = self.command_service.get_all_commands()
                self.command_combo_box.load_tasks(data)
                if config.app.expand:
                    self.command_combo_box.expand_all_items()
                self.command_combo_box.select_first_task()
                
                logger.debug(f"MainWindow: {message}")
            else:
                logger.error(f"MainWindow: {message}")


    def on_result_double_clicked(self, index):
        """Handle double click on result row"""
        row = index.row()
        hostname = self.result_table.item(row, 0).text()
        status_item = self.result_table.item(row, 2).text()
        iid = self.result_table.item(row, 3).text()

        output_item = self.result_table.item(row, 1)
        output_from_table = output_item.text() if output_item else ""

        stored_output = self.result_table.device_outputs.get(iid, "")

        output = stored_output if stored_output else output_from_table
        if not output:
            output = "Нет данных"

        dialog = CommandResultDialog(hostname, output, status_item, self, self.worker or None, device_iid=iid)
        dialog.exec_()


    def show_command_edit_dialog(self):
        editor_dialog = CommandEditorDialog(self)
        # Запускаем диалог модально
        dlg_code = QDialog.DialogCode.Rejected
        dlg_code = editor_dialog.exec()
        if dlg_code == QDialog.DialogCode.Accepted:
            # Если пользователь нажал OK, получаем новые данные
            new_data = editor_dialog.get_data()
            if new_data is not None:
                # ARCHITECTURE: Используем сервис для сохранения
                success = self.command_service.save_commands(new_data)
                
                if success:
                    # Перезагружаем данные в основном ComboBox
                    self.command_combo_box.load_tasks(new_data)
                    if config.app.expand:
                        self.command_combo_box.expand_all_items()
                    self.command_combo_box.select_first_task()

                    # ARCHITECTURE: Публикуем событие об изменении команд
                    self.event_bus.publish_typed(
                        EventType.DATA_CHANGED,
                        source='MainWindow',
                        data={'type': 'commands_updated'}
                    )
                    
                    logger.info("MainWindow: Commands saved successfully")
                else:
                    logger.error("MainWindow: Failed to save commands")
            else:
                 logger.error("Редактор вернул None при Accept.")
        else:
            pass

        editor_dialog.deleteLater()


    def get_params_dict(self, params: list) -> dict:
        from src.ui.dialogs.common.params import ParameterInputDialog
        if not params:
            return None
        dialog = ParameterInputDialog(params)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.get_data()                
        else:
            logger.debug("Пользователь отменил ввод.")
            return None


    def show_ssh_manage_dialog(self):
        from src.ui.dialogs.tools.ssh_manage import SSHManageDialog
        # ARCHITECTURE: Получаем оригинальные данные с группами через сервис
        tree_data = self.device_service.get_devices_raw()
        dlg = SSHManageDialog(tree_data, self)
        dlg.exec()


    def show_pinger_dialog(self):
        try:
            from src.ui.dialogs.tools.pinger import PingMonitorDialog
            dlg = PingMonitorDialog(self)
            dlg.exec()
        except Exception as e:
            logger.error(e)


    def on_single_host_command_executed(self, device: DeviceModel, command_data: dict):
        if hasattr(self, 'worker') and self.worker is not None:
            QMessageBox.critical(self, "Предупреждение", "Задача уже запущена. Отмените ее, чтобы запустить новую.")
            return
        self.execute_command([device], command_data)


    def execute_command(self, devices: list[DeviceModel], command_data: dict):
        timeout = command_data.get('timeout', config.app.ssh.command_timeout)        
        commands_data = command_data.get('commands', [])
        commands = None
        commands = [
            Command(
                cmd
            ) for cmd in commands_data
        ]     

        parameters = command_data.get('params', [])

        if len(parameters) > 0:
            params_dict = self.get_params_dict(parameters)
            if params_dict is None:
                return
            
            for command in commands:
                command.replace_params(params_dict)     
        
        # Clear previous results
        self.result_table.clear_results()
        
        # Initialize table with hosts
        self.result_table.add_initial_entries(devices)
        
        self.command_abort_button.setEnabled(True)
        self.command_abort_button.setVisible(True)

        self.execute_button.setEnabled(False)
        self.execute_button.setVisible(False)  

        # ARCHITECTURE: Используем WorkerBridge для управления worker
        self.worker = CommandWorker(devices, commands, timeout)
        
        # Регистрируем worker в bridge для централизованного управления
        worker_id = f"command_{id(self.worker)}"
        self.worker_bridge.register_threaded_worker(
            self.worker,
            worker_id,
            client=self,
            context={'devices': [d.host for d in devices], 'command_count': len(commands)},
            auto_start=False  # Запустим вручную после настройки сигналов
        )
        
        # Сохраняем ID worker для возможности прерывания
        self.current_worker_id = worker_id
        
        # Подключаем сигналы результата
        self.worker.progress_update.connect(self.result_table.update_progress)
        self.worker.result_ready.connect(self.result_table.set_result)
        
        # Публик��е�� событ��е о начале выполн����ния ��оманды
        self.event_bus.publish_typed(
            EventType.COMMAND_STARTED,
            source='MainWindow',
            data={'device_count': len(devices), 'command_count': len(commands)}
        )
        
        self.command_executed = True
        logger.debug("Starting command execution via WorkerBridge")
        
        # Запускаем по��ок
        if worker_id in self.worker_bridge._threads:
            self.worker_bridge._threads[worker_id].start()

    # ARCHITECTURE: Реализация интерфейса IWorkerClient
    def on_worker_started(self, worker_id: str, context: Dict[str, Any]) -> None:
        """Вызывается при запуске worker"""
        logger.debug(f"MainWindow: Worker {worker_id} started with context {context}")

    def on_worker_progress(self, worker_id: str, progress: Any) -> None:
        """Вызывается при обновлении прогресса worker"""
        # Прогресс обрабатывается чере�� сигналы напрямую для обратной совместимости
        pass

    def on_worker_finished(self, worker_id: str, result: Any) -> None:
        """Вызывается при завершении worker"""
        logger.debug(f"MainWindow: Worker {worker_id} finished")
        self.on_execute_thread_finished()
        
        # Публикуем событие о завершении команды
        self.event_bus.publish_typed(
            EventType.COMMAND_FINISHED,
            source='MainWindow',
            data={'worker_id': worker_id}
        )

    def on_worker_error(self, worker_id: str, error: str) -> None:
        """Вызывается при ошибке worker"""
        logger.error(f"MainWindow: Worker {worker_id} error: {error}")
        QMessageBox.critical(self, "Ошибка выполнения", f"Ошибка при выполнении команды:\n{error}")
