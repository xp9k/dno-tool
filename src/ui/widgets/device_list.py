from PySide6.QtGui import QStandardItem, QStandardItemModel, QIcon, QAction
from PySide6.QtCore import QTimer, Qt, Signal, QSize, QModelIndex, QPoint
from PySide6.QtWidgets import QListView, QMenu, QScrollArea, QVBoxLayout, QLabel, QWidget, QFrame, QHBoxLayout, QSizePolicy, QAbstractScrollArea
from typing import Optional

QWIDGETSIZE_MAX = 16777215
from src.config import ICONS, config
from src.domain.models.device import DeviceModel

import subprocess
from src.logger import logger
from src.data import datastore
from src.workers.network import get_host_ping_timer_manager


class CustomListItem(QStandardItem):
    """
    Кастомный элемент списка с поддержкой пинга устройства.

    Пинг управляется централизованно через HostPingTimerManager.
    """

    def __init__(self, text: str = '', device: DeviceModel = None, tree_item=None):
        """
        Инициализация элемента списка

        Args:
            text: Отображаемый текст
            device: Объект устройства
            tree_item: Связанный элемент дерева
        """
        icon = QIcon(device.icon)
        super().__init__(QIcon(device.icon), text)
        self.device = device
        self.tree_item = tree_item

        if isinstance(icon, QIcon):
            self.setIcon(icon)
        else:
            self.setIcon(QIcon(self.device.icon))

        self.setFlags(Qt.ItemFlag.ItemIsEnabled |
                     Qt.ItemFlag.ItemIsSelectable)

        self.setToolTip(
            f"Имя: {self.device.name}\nХост: {self.device.host}\nПорт: {self.device.port or config.app.ssh.port}\nЛогин: {self.device.login or config.app.ssh.username}"
        )

    def update_online_icon(self, device: DeviceModel, is_online: bool):
        """Обновление иконки при изменении статуса онлайн (вызывается из DeviceListView)"""
        if self.device and self.device.host == device.host:
            try:
                self.setIcon(QIcon(device.icon))
            except (RuntimeError, AttributeError):
                pass

    def cleanup(self):
        """Очистка ресурсов перед удалением"""
        if self.device and self.device.host:
            get_host_ping_timer_manager().stop_ping(self.device.host)


    def open_terminal(self):
        """Открывает новое окно терминала с SSH подключением (Windows и Linux)"""
        import platform
        import shutil
        from src.workers.command.executor_base import get_credentials

        try:
            hostname = self.device.host
            creds = get_credentials(self.device)
            port = self.device.port or config.app.ssh.port
            strict = 'yes' if config.app.ssh.strict_host_checking else 'no'
            ssh_cmd = f"ssh -o StrictHostKeyChecking={strict} -p {port} {creds.username}@{hostname}"

            system = platform.system().lower()
            if system == 'windows':
                # Try Windows Terminal (wt.exe), fallback to cmd
                wt_path = shutil.which('wt')
                if wt_path:
                    subprocess.Popen([
                        'wt', 'cmd', '/k', ssh_cmd
                    ])
                else:
                    subprocess.Popen([
                        'cmd', '/c', f'start cmd /k {ssh_cmd}'
                    ])
            else:
                # Try common Linux terminals
                for term in ['gnome-terminal', 'x-terminal-emulator', 'konsole', 'xfce4-terminal', 'xterm']:
                    term_path = shutil.which(term)
                    if term_path:
                        subprocess.Popen([
                            term, '-e', ssh_cmd
                        ])
                        break
                else:
                    raise RuntimeError('No supported terminal emulator found.')
        except Exception as e:
            logger.error(f"Ошибка открытия терминала: {e}")

    def open_sftp(self):
        """Opens SFTP dialog for file transfer"""
        from src.ui.dialogs.tools.sftp import SFTPDialog
        dialog = SFTPDialog(self.device, None)
        dialog.exec()


class DynamicHeightListView(QListView):
    """QListView с динамической высотой на основе содержимого"""

    def __init__(self, parent=None, icon_size: QSize = QSize(64, 64), spacing: int = 4):
        super().__init__(parent)
        self._icon_size = icon_size
        self._spacing = spacing
        self._grid_size = None
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
    def setGridSize(self, size: QSize):
        """Переопределяем для отслеживания размера сетки"""
        super().setGridSize(size)
        self._grid_size = size
        self.updateGeometry()
        
    def sizeHint(self) -> QSize:
        """Возвращает оптимальный размер на основе содержимого"""
        if self.model() is None or self._grid_size is None:
            return QSize(100, 100)

        count = self.model().rowCount()
        if count == 0:
            return QSize(100, self._grid_size.height())

        # Получаем доступную ширину от родителя
        viewport_width = self.parent().width() if self.parent() else 400
        if viewport_width <= 0:
            viewport_width = 400

        # Вычисляем количество колонок
        item_width = self._grid_size.width()
        columns = max(1, viewport_width // item_width)

        # Вычисляем количество строк
        rows = (count + columns - 1) // columns

        # Вычисляем высоту
        item_height = self._grid_size.height()
        total_height = rows * item_height + self._spacing * 2

        return QSize(viewport_width, total_height)
    
    def minimumSizeHint(self) -> QSize:
        """Минимальный размер - одна строка"""
        if self._grid_size is None:
            return QSize(100, 100)
        return QSize(self._grid_size.width(), self._grid_size.height() + self._spacing * 2)
    
    def resizeEvent(self, event):
        """При изменении размера пересчитываем высоту"""
        super().resizeEvent(event)
        # Обновляем геометрию для пересчета sizeHint
        self.updateGeometry()


class DeviceListView(QWidget):
    itemDoubleClicked = Signal(object)  # Will emit the clicked CustomListItem
    commandsExecuted = Signal(DeviceModel, dict)
    clicked = Signal(QModelIndex)  # For compatibility with previous QListView API
    remoteRecordingRequested = Signal(DeviceModel)  # ПКМ -> Удалённая запись
    polkitEditorRequested = Signal(DeviceModel)  # ПКМ -> Редактор политик

    """Widget: sectioned icon view adapted for devices"""
    def __init__(
        self,
        parent=None,
        icon_size: QSize = QSize(64, 64),
        spacing: int = 4
    ):
        super().__init__(parent)
        self._icon_size = icon_size
        self._spacing = spacing
        self._categories = {}  # name -> {widget, label, view, model}
        self._default_category = "Все устройства"
        self._category_order = []  # Порядок добавления категорий

        self._setup_ui()
        get_host_ping_timer_manager().online_updated.connect(self._on_ping_online_updated)

    def _on_ping_online_updated(self, device: DeviceModel, is_online: bool):
        """Обновление иконок элементов списка при изменении статуса пинга"""
        for name, info in self._categories.items():
            model = info["model"]
            for i in range(model.rowCount()):
                item = model.item(i)
                if hasattr(item, 'update_online_icon'):
                    item.update_online_icon(device, is_online)

    def _setup_ui(self):
        # Main layout
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # Scroll area + container
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        # self._scroll.setFrameShape(QScrollArea.Shape.Box)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(4, 4, 4, 4)
        self._vbox.setSpacing(2)

        self._scroll.setWidget(self._container)
        main.addWidget(self._scroll)

    def _calculate_section_height(self, view: DynamicHeightListView, item_count: int) -> int:
        """Вычисляет высоту секции на основе количества элементов"""
        if item_count == 0:
            return view.minimumSizeHint().height()

        grid_size = view.gridSize()
        if not grid_size.isValid():
            grid_size = QSize(self._icon_size.width() + self._spacing * 2 + 12,
                            self._icon_size.height() + 20 + self._spacing * 2 + 12)

        # Получаем доступную ширину от контейнера
        viewport_width = self._container.width() - 8  # Учитываем отступы
        if viewport_width <= 0:
            viewport_width = self._scroll.viewport().width() - 8
        if viewport_width <= 0:
            viewport_width = 400

        item_width = grid_size.width()
        item_height = grid_size.height()

        # Вычисляем количество колонок
        columns = max(1, viewport_width // item_width)

        # Вычисляем количество строк
        rows = (item_count + columns - 1) // columns

        # Общая высота = строки * высота_элемента + отступы
        total_height = rows * item_height + self._spacing * 2

        return max(total_height, item_height + self._spacing * 2)

    def _update_section_height(self, category_name: str):
        """Обновляет высоту конкретной секции"""
        if category_name not in self._categories:
            return
            
        info = self._categories[category_name]
        view = info["view"]
        model = info["model"]
        
        item_count = model.rowCount()
        new_height = self._calculate_section_height(view, item_count)
        
        # Обновляем политику размера
        is_last = (self._category_order and self._category_order[-1] == category_name)
        
        if is_last and len(self._category_order) > 0:
            # Последняя секция растягивается на оставшуюся высоту
            view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            view.setMinimumHeight(new_height)
            view.setMaximumHeight(QWIDGETSIZE_MAX)
        else:
            # Остальные секции имеют фиксированную высоту
            view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.setFixedHeight(new_height)

    def _update_all_sections_height(self):
        """Обновляет высоту всех секций"""
        for name in self._category_order:
            self._update_section_height(name)

    # --------- API ----------
    def add_category(self, name: str):
        """Добавляет категорию с заголовком `name`."""
        if name in self._categories:
            return  # already exists

        # Добавляем в порядок категорий
        if name not in self._category_order:
            self._category_order.append(name)

        # header
        header = QLabel(name)
        # keep label width to its content so the separating line starts right after the title
        header.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setContentsMargins(2, 2, 2, 2)

        # separating line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setLineWidth(1)
        line.setMidLineWidth(0)

        # header frame
        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        # make spacing small so the line is pressed up against the label
        header_layout.setSpacing(4)
        header_layout.addWidget(header)
        header_layout.addWidget(line)

        # list view с динамической высотой
        view = DynamicHeightListView(icon_size=self._icon_size, spacing=self._spacing)
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setFlow(QListView.LeftToRight)
        view.setWrapping(True)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setMovement(QListView.Movement.Snap)
        view.setSpacing(self._spacing)
        view.setIconSize(self._icon_size)
        view.setSelectionMode(QListView.SelectionMode.SingleSelection)

        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)

        view.setUniformItemSizes(False)
        view.setWordWrap(True)

        # model
        model = QStandardItemModel()
        view.setModel(model)

        # forward signals for compatibility
        view.clicked.connect(self._make_forward_clicked(model))
        view.doubleClicked.connect(self._make_forward_doubleclicked(model))
        view.customContextMenuRequested.connect(lambda pos, v=view: self.show_list_context_menu(v, pos))

        # container widget for this category
        cat_widget = QWidget()
        cat_layout = QVBoxLayout(cat_widget)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(2)
        cat_layout.addWidget(header_frame)
        cat_layout.addWidget(view)

        # store
        self._categories[name] = {
            "widget": cat_widget,
            "label": header,
            "view": view,
            "model": model,
        }

        # append to layout
        self._vbox.addWidget(cat_widget)

        # Устанавливаем gridSize для view
        self._update_view_grid(view)

        # Устанавливаем начальную высоту
        self._update_section_height(name)

        # If this is the default category, expose compatibility attributes
        if name == self._default_category:
            self._model = model
            self._view = view

    def _make_forward_clicked(self, model):
        def _slot(index: QModelIndex):
            # forward the raw QModelIndex coming from the specific view
            self.clicked.emit(index)
        return _slot

    def _make_forward_doubleclicked(self, model):
        def _slot(index: QModelIndex):
            item = model.itemFromIndex(index)
            if item and hasattr(item, 'device'):
                self.itemDoubleClicked.emit(item)
        return _slot

    def remove_category(self, name: str):
        """Удаляет категорию и её виджеты."""
        info = self._categories.pop(name, None)
        if not info:
            return
        
        # Удаляем из порядка категорий
        if name in self._category_order:
            self._category_order.remove(name)
        
        widget = info["widget"]
        self._vbox.removeWidget(widget)
        widget.deleteLater()
        
        # Обновляем высоту новой последней секции
        if self._category_order:
            self._update_section_height(self._category_order[-1])

    def add_device(self, device: DeviceModel, category: str | None = None):
        """Add device to appropriate category (or default).
        If `category` provided, use it. If category is None, fallback to device.group/device.folder or default.
        """
        # Determine category name
        if category is None:
            category = getattr(device, 'group', None) or getattr(device, 'folder', None) or self._default_category
        else:
            # treat empty/None-like parent as explicit "Нет категории"
            if category == "" or category is None:
                category = "Нет категории"

        if category not in self._categories:
            self.add_category(category)

        model = self._categories[category]["model"]

        # Проверяем, есть ли уже такое устройство в этой категории (по host)
        device_host = getattr(device, 'host', None)
        for i in range(model.rowCount()):
            existing_item = model.item(i)
            if existing_item and hasattr(existing_item, 'device') and existing_item.device:
                existing_host = getattr(existing_item.device, 'host', None)
                if existing_host == device_host:
                    logger.debug(f"Device {device.name} ({device_host}) already exists in category {category}, skipping")
                    return existing_item

        item = CustomListItem(device.name, device)
        item.setEditable(False)
        model.appendRow(item)

        get_host_ping_timer_manager().start_ping(device)

        # Обновляем высоту секции после добавления
        self._update_section_height(category)

        # layout updates
        self._categories[category]["view"].viewport().update()

        # return total across all categories
        total = sum(self._categories[c]["model"].rowCount() for c in self._categories)
        logger.debug(f"Total items in DeviceListView model: {total}")
        # return total
        return item

    def remove_device(self, device_name: str):
        """Remove device by name across all categories"""
        removed = False
        # iterate over a snapshot to allow modifying _categories inside loop
        for name, info in list(self._categories.items()):
            model = info["model"]
            # iterate backwards to safely remove rows
            for i in range(model.rowCount() - 1, -1, -1):
                item: CustomListItem = model.item(i)
                if item and item.text() == device_name:
                    if hasattr(item, 'cleanup'):
                        item.cleanup()
                    model.removeRow(i)
                    removed = True
            # Обновляем высоту секции после удаления
            if removed:
                self._update_section_height(name)
            # if category became empty, remove it (but keep default category)
            if model.rowCount() == 0 and name != self._default_category:
                self.remove_category(name)
        return removed

    def remove_device_by_host(self, device_host: str):
        """Remove device by host across all categories"""
        removed = False
        # iterate over a snapshot to allow modifying _categories inside loop
        for name, info in list(self._categories.items()):
            model = info["model"]
            # iterate backwards to safely remove rows
            for i in range(model.rowCount() - 1, -1, -1):
                item: CustomListItem = model.item(i)
                if item and hasattr(item, 'device') and item.device:
                    item_host = getattr(item.device, 'host', None)
                    if item_host == device_host:
                        if hasattr(item, 'cleanup'):
                            item.cleanup()
                        model.removeRow(i)
                        removed = True
            # Обновляем высоту секции после удаления
            if removed:
                self._update_section_height(name)
            # if category became empty, remove it (but keep default category)
            if model.rowCount() == 0 and name != self._default_category:
                self.remove_category(name)
        return removed

    def clear_devices(self):
        """Clear all devices from list and cleanup resources"""
        # Iterate over snapshot because remove_category may modify _categories
        for name, info in list(self._categories.items()):
            model = info["model"]
            # cleanup each item
            for i in range(model.rowCount() - 1, -1, -1):
                item: CustomListItem = model.item(i)
                if item and hasattr(item, 'cleanup'):
                    item.cleanup()
                model.removeRow(i)
            # Обновляем высоту секции
            self._update_section_height(name)
            # remove non-default empty categories
            if model.rowCount() == 0 and name != self._default_category:
                self.remove_category(name)
        # ensure default view updated
        default_info = self._categories.get(self._default_category)
        if default_info:
            default_info["view"].viewport().update()

    def get_all_devices(self):
        """Get all devices from list"""
        devices: list[DeviceModel] = []
        for info in self._categories.values():
            model = info["model"]
            for i in range(model.rowCount()):
                item: CustomListItem = model.item(i)
                if item and hasattr(item, 'device'):
                    devices.append(item.device)
        return devices

    def get_current_device(self):
        """Get currently selected device from active focused view"""
        # Try to find focused view first
        fw = self.focusWidget()
        if isinstance(fw, QListView):
            index = fw.currentIndex()
            model = fw.model()
            item = model.itemFromIndex(index)
            if item and hasattr(item, 'device'):
                return item.device
        # Fallback: first selected item across categories
        for info in self._categories.values():
            view = info["view"]
            sel = view.selectedIndexes()
            if sel:
                item = info["model"].itemFromIndex(sel[0])
                if item and hasattr(item, 'device'):
                    return item.device
        return None

    def _update_view_grid(self, view: QListView):
        """Настраивает gridSize у QListView так, чтобы плитки имели адекватную ширину/высоту."""
        fm = view.fontMetrics()
        text_h = fm.height()
        tile_w = self._icon_size.width() + self._spacing * 2 + 12
        tile_h = self._icon_size.height() + text_h + self._spacing * 2 + 12

        total_items = view.model().rowCount()
        view.setGridSize(QSize(tile_w, tile_h))

    def resizeEvent(self, event):
        """Обработка изменения размера виджета"""
        super().resizeEvent(event)
        # Пересчитываем высоты всех секций при изменении размера
        # Используем QTimer для отложенного вызова после завершения resize
        QTimer.singleShot(0, self._update_all_sections_height)

    # Compatibility helper so callers that expect a QListView-like .viewport() work
    def viewport(self):
        """Return a QWidget representing the viewport (compatibility with old QListView usage)."""
        if getattr(self, "_view", None) is not None:
            return self._view.viewport()
        # Fallback: return scroll area's viewport
        return self._scroll.viewport()

    # Menu / commands handling: adapted to per-view calls
    def create_commands_menu(self, parent_item: QMenu, data_structure) -> QMenu:
        if not isinstance(data_structure, list): 
            return parent_item
        device = self.get_current_device()
        for item_data in data_structure:
            if not isinstance(item_data, dict): continue
            if "name" in item_data and "commands" in item_data:                
                command_name = item_data.get("name", "Без имени")
                command_action = QAction(QIcon(ICONS.get('command', '')), command_name, parent_item)
                parent_item.addAction(command_action)
                commands = item_data["commands"]
                if commands and isinstance(commands, list): 
                    command_data = {
                        "name": command_name,
                        "commands": commands,
                        "description": item_data.get("description", ""),
                        "params": item_data.get("params", []),
                        "timeout": item_data.get("timeout", config.app.ssh.command_timeout)
                    }
                    command_action.triggered.connect(
                        lambda _, command_data=command_data: self.execute_command_on_device(device, command_data)
                        )
            elif len(item_data) == 1:
                category_name = list(item_data.keys())[0]
                child_data_list = item_data[category_name]
                if isinstance(child_data_list, list):
                    category_item = parent_item.addMenu(category_name)
                    category_item.setIcon(QIcon(ICONS.get('folder', "")))
                    self.create_commands_menu(category_item, child_data_list)
        return parent_item

    def show_list_context_menu(self, view: QListView, position: QPoint):
        """Show context menu for item in given view at position (local to that view)"""
        index = view.indexAt(position)
        if index.isValid():
            model = view.model()
            item: CustomListItem = model.itemFromIndex(index)
            if isinstance(item, CustomListItem) and item.device:
                menu = QMenu()

                terminal_action = menu.addAction(QIcon(ICONS.get('menu_terminal', '')), "Открыть в терминале")
                terminal_action.triggered.connect(item.open_terminal)

                sftp_action = menu.addAction(QIcon(ICONS.get('menu_sftp', '')), "Обмен файлами")
                sftp_action.triggered.connect(item.open_sftp)

                menu.addSeparator()

                commands_submenu = QMenu("Выполнить команду", self)
                commands_submenu.setIcon(QIcon(ICONS.get('command', '')))
                menu.addMenu(commands_submenu)
                commands_submenu = self.create_commands_menu(commands_submenu, datastore.get_commands_data())

                menu.addSeparator()

                kde_action = menu.addAction(QIcon(ICONS.get('menu_kde', '')), "Управление KDE")
                kde_action.setStatusTip("Управление настройками KDE на удалённой машине")
                kde_action.triggered.connect(lambda: self._open_kde_config_dialog(item.device))

                menu.addSeparator()

                info_action = menu.addAction(QIcon(ICONS.get('menu_info', '')), "Информация о устройстве")
                info_action.triggered.connect(lambda _, idx=index, v=view: self._emit_item_doubleclicked_from_view(v, idx))

                menu.addSeparator()

                recording_action = menu.addAction(QIcon(ICONS.get('menu_recording', '')), "Удалённая запись")
                recording_action.triggered.connect(lambda: self.remoteRecordingRequested.emit(item.device))

                polkit_action = menu.addAction(QIcon(ICONS.get('menu_polkit', '')), "Редактор политик")
                polkit_action.triggered.connect(lambda: self.polkitEditorRequested.emit(item.device))

                global_pos = view.mapToGlobal(position)
                menu.exec_(global_pos)

    def _emit_item_doubleclicked_from_view(self, view: QListView, index: QModelIndex):
        model = view.model()
        item = model.itemFromIndex(index)
        if item and hasattr(item, 'device'):
            self.itemDoubleClicked.emit(item)

    def execute_command_on_device(self, device, command_data):
        if not device:
            device = self.get_current_device()

        if device and command_data:
            self.commandsExecuted.emit(device, command_data)
            
    # ARCHITECTURE: Статический метод для получения Command с отложенным импортом
    @staticmethod
    def _get_command_class():
        """Ленивый импорт Command для избежания циклической зависимости"""
        from src.workers import Command
        return Command

    def _open_kde_config_dialog(self, device):
        """Открыть диалог управления настройками KDE"""
        try:
            from src.ui.dialogs.kde import KDEConfigDialog
            
            dialog = KDEConfigDialog(device, self)
            dialog.exec_()
            
        except ImportError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось загрузить модуль управления KDE:\n{e}"
            )
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Ошибка при открытии диалога KDE:\n{e}"
            )
