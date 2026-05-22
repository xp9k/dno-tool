"""Виджет иерархического дерева устройств с поддержкой drag-and-drop, чекбоксов и контекстного меню."""

from PySide6.QtGui import (QStandardItemModel, QStandardItem, QIcon, QStandardItem, QDragEnterEvent, QDrag, QMouseEvent, QDropEvent, QDragMoveEvent)
from PySide6.QtCore import Qt, Signal, QMimeData, QModelIndex
from PySide6.QtWidgets import (QTreeView, QAbstractItemView, QMessageBox,
                               QMenu, QInputDialog, QLineEdit)

from src.domain.models.device import DeviceModel
from src.config import ICONS, config
from src.data import datastore
import json
import os
import re
from typing import Optional
from collections import defaultdict
from src.logger import logger

from src.workers.network import get_host_ping_timer_manager

MIME_TYPE = "application/x-device-treeitem"

# Глобальная переменная для хранения перетаскиваемых элементов
_drag_source_items = []

FOLDER_DATAROLE = Qt.ItemDataRole.UserRole + 2
ITEM_DATAROLE = Qt.ItemDataRole.UserRole + 1

class CustomTreeItem(QStandardItem):
    """Кастомный элемент дерева с дополнительными свойствами"""

    def __init__(self, text: str = '', device: DeviceModel = None):
        """
        Инициализация элемента
        device - объект устройства
        """
        super().__init__(text)
        self.setEditable(True)
        self.setSelectable(True)
        self.setCheckable(True)
        self.setFlags(self.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.setCheckState(Qt.CheckState.Unchecked)
        self.setEditable(False)

        self.device = device
        if self.device is None:
            self.setData(True, FOLDER_DATAROLE)  # Только для папок
        self.update()

    def update(self):
        if self.device:
            self.setText(self.device.name)
            self.setIcon(QIcon(self.device.icon))
            self.setData(False, FOLDER_DATAROLE)  # Для устройств явно False
            self.setToolTip(f"Имя: {self.device.name}\nХост: {self.device.host}\nПорт: {self.device.port or config.app.ssh.port}\nЛогин: {self.device.login or config.app.ssh.username}")
        else:
            self.setText(self.text())
            self.setIcon(QIcon(ICONS['folder']))
            self.setData(True, FOLDER_DATAROLE)

    
    def appendRow(self, item: QStandardItem) -> None:
        result = super(CustomTreeItem, self).appendRow(item)


class CustomTreeItemModel(QStandardItemModel):
    """Кастомная модель для дерева с поддержкой чекбоксов"""
    itemDataChanged = Signal(object, object)  # Сигнал об изменении состояния чекбокса
    data_changed = Signal(list)  # Сигнал об изменении данных дерева

    def __init__(self, parent=None, read_only=False):
        super().__init__(parent)
        self.read_only = read_only

    def setData(self, index, value, role=Qt.ItemDataRole.CheckStateRole):
        """
        Обработка изменения состояния чекбокса
        Реализует каскадное включение/выключение чекбоксов
        """
        oldvalue = index.data(role)
        result = super(CustomTreeItemModel, self).setData(index, value, role)

        if role != Qt.ItemDataRole.CheckStateRole:
            return result

        if result and value != oldvalue:
            self.itemDataChanged.emit(self.itemFromIndex(index), role)
            item = self.itemFromIndex(index)
            if item.checkState() == Qt.CheckState.Unchecked:
                item.setIcon(QIcon(ICONS['default']))
            for i in range(item.rowCount()):
                child = item.child(i)
                self.setData(child.index(), value, role)
            self.propagate_checkstate_up(item)
        return result
    
    def propagate_checkstate_up(self, item: CustomTreeItem):
        parent = item.parent()
        while parent is not None:
            checked = self.collectChecked(parent)
            if len(checked) == 0:
                parent.setCheckState(Qt.CheckState.Unchecked)
            elif len(checked) == parent.rowCount():
                parent.setCheckState(Qt.CheckState.Checked)
            else:
                parent.setCheckState(Qt.CheckState.PartiallyChecked)
            item = parent
            parent = item.parent()
    
    def collectChecked(self, node):
        checked = []
        
        if node is None:
            return checked
        
        for i in range(node.rowCount()):
            child = node.child(i)
            if child.checkState() == Qt.CheckState.Checked or child.checkState() == Qt.CheckState.PartiallyChecked:
                checked.append(child)
        return checked

    def supportedDropActions(self):
        """Поддерживаемые действия при перетаскивании"""
        return Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def export_tree_data(self):
        """Сохранение всего дерева с учетом иерархии"""
        root = self.invisibleRootItem()
        data = []
        
        for i in range(root.rowCount()):
            group_item: CustomTreeItem = root.child(i)
            if group_item.hasChildren():
                data.append({group_item.text(): self._save_group_items(group_item)})
            else:
                device: DeviceModel = group_item.device
                if device is None:
                    data.append({group_item.text(): {}})
                else:
                    data.append(device.export())
                
        return data

    def _save_group_items(self, group_item):
        """Сохранение элементов группы"""
        items = []
        for i in range(group_item.rowCount()):
            child = group_item.child(i)
            if child.hasChildren():
                # Если это подгруппа
                items.append({child.text(): self._save_group_items(child)})
            else:
                # Если это устройство
                device: DeviceModel = child.device
                if device is None:
                    items.append({child.text(): {}})
                else:
                    items.append(device.export())
        return items
    
    def mimeTypes(self):
        return [
            MIME_TYPE,
            'text/plain',
        ]
    
    
    def mimeData(self, indexes):
        """Сохраняет дополнительные данные элемента при drag"""
        mime_data = QMimeData()
        if indexes and mime_data:
            items_data = bytearray()
            for index in indexes:
                item = self.itemFromIndex(index)
                if isinstance(item, CustomTreeItem):
                    is_folder = bool(item.data(FOLDER_DATAROLE))
                    # Сохраняем все свойства элемента
                    item_data = {
                        'text': item.text(),
                        'is_folder': str(is_folder),
                        'checkState': int(item.checkState().value),  # Convert enum to int
                        'row': index.row(),
                        'parent_row': index.parent().row() if index.parent().isValid() else -1,
                    }
                    if hasattr(item, 'device') and item.device is not None:
                        item_data['device'] = item.device.export()
                    items_data.extend(f"{item_data}".encode('utf-8'))
                    items_data.extend(b'\n')

            # Сохраняем данные в MIME
            mime_data.setData(
                MIME_TYPE,
                bytes(items_data)
            )
        return mime_data
    

    def dropMimeData(self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: QModelIndex):
        global _drag_source_items
        
        if self.read_only:
            return False

        if action == Qt.DropAction.IgnoreAction:
            return True

        if not data.hasFormat(MIME_TYPE):
            return False

        encoded_data = data.data(MIME_TYPE)
        rows = encoded_data.split(ord('\n'))
        parent_item = self.itemFromIndex(parent) if parent.isValid() else self.invisibleRootItem()

        insert_row = row
        if insert_row is None:
            insert_row = parent_item.rowCount()

        # Сохраняем оригинальные элементы для удаления
        # Удаляем сразу, ДО вставки новых, чтобы индексы не сбились
        items_to_delete = [(item.parent(), item.row()) for item in _drag_source_items]

        # Группируем по родителю (используем id() т.к. CustomTreeItem unhashable)
        # и удаляем с конца, чтобы не сбить индексы
        items_by_parent_id = defaultdict(list)
        parent_id_to_obj = {}
        for item_parent, item_row in items_to_delete:
            parent_id = id(item_parent)
            items_by_parent_id[parent_id].append(item_row)
            parent_id_to_obj[parent_id] = item_parent

        for parent_id, rows_list in items_by_parent_id.items():
            item_parent = parent_id_to_obj[parent_id]
            for row_idx in sorted(rows_list, reverse=True):
                # Получаем элемент и останавливаем пинг если нужно
                if item_parent is not None:
                    item_to_remove = item_parent.child(row_idx)
                else:
                    item_to_remove = self.item(row_idx)
                if item_to_remove and hasattr(item_to_remove, 'device') and item_to_remove.device:
                    if item_to_remove.checkState() == Qt.CheckState.Checked:
                        get_host_ping_timer_manager().stop_ping(item_to_remove.device.host)
                # Удаляем элемент
                if item_parent is not None:
                    item_parent.removeRow(row_idx)
                else:
                    self.removeRow(row_idx)

        # Корректируем insert_row если вставляем в того же родителя
        # (если удалённые элементы были до позиции вставки)
        deleted_before_insert = 0
        for item_parent, item_row in items_to_delete:
            if item_parent == parent_item and item_row < insert_row:
                deleted_before_insert += 1
        insert_row -= deleted_before_insert
        if insert_row < 0:
            insert_row = 0

        for row_data in rows:
            if not row_data:
                continue
            try:
                decoded = row_data.data().decode('utf-8')
                json_acceptable_string = decoded.replace("'", "\"")
                item_data = json.loads(json_acceptable_string)

                # Используем сохраненные ссылки на исходные элементы
                source_item = None
                if len(_drag_source_items) > 0:
                    # Берем первый элемент для копирования дочерних элементов (для папок)
                    source_item = _drag_source_items[0]

                # Создаем новый элемент
                if 'device' in item_data:
                    device = DeviceModel(item_data['device'])
                    new_item = CustomTreeItem(device.name, device)
                    new_item.setData(False, FOLDER_DATAROLE)  # Для устройств
                else:
                    # Это папка - создаем и копируем дочерние элементы
                    new_item = CustomTreeItem(item_data['text'])
                    new_item.setData(True, FOLDER_DATAROLE)  # Для папок
                    # Копируем чекбокс состояние
                    new_item.setCheckState(Qt.CheckState(item_data.get('checkState', 0)))

                    # Копируем дочерние элементы рекурсивно из исходного элемента
                    if source_item and source_item.hasChildren():
                        self._copy_child_items(new_item, source_item)

                new_item.setDropEnabled(True)
                new_item.setDragEnabled(True)

                # Проверяем можно ли добавить в родителя
                if parent_item:
                    parent_is_folder = bool(parent_item.data(FOLDER_DATAROLE))
                    if parent.isValid() and not parent_is_folder:
                        # Если родитель не папка - превращаем его в папку
                        parent_item.setData(True, FOLDER_DATAROLE)
                        parent_item.setCheckState(Qt.CheckState.Unchecked)
                        parent_item.setIcon(QIcon(ICONS['folder']))
                        parent_item.device = None

                    # Вставляем элемент на позицию insert_row (если задано), иначе в конец
                    if insert_row is None or insert_row < 0 or insert_row > parent_item.rowCount():
                        parent_item.appendRow(new_item)
                    else:
                        parent_item.insertRow(insert_row, [new_item])
                        insert_row += 1
                else:
                    # Корень
                    if insert_row is None or insert_row < 0 or insert_row > self.rowCount():
                        self.appendRow(new_item)
                    else:
                        self.insertRow(insert_row, [new_item])
                        insert_row += 1

                # Удаляем обработанный элемент из списка (после создания копии)
                if len(_drag_source_items) > 0:
                    _drag_source_items.pop(0)

            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON: {e}")
                return False
            except Exception as e:
                logger.error(f"Error: {e}")
                return False

        return True

    def _copy_child_items(self, target_item: CustomTreeItem, source_item: CustomTreeItem):
        """Рекурсивное копирование дочерних элементов"""
        for i in range(source_item.rowCount()):
            child = source_item.child(i)
            if child.data(FOLDER_DATAROLE):  # Это папка
                new_child = CustomTreeItem(child.text())
                new_child.setData(True, FOLDER_DATAROLE)
                new_child.setCheckState(child.checkState())
                new_child.setDropEnabled(True)
                new_child.setDragEnabled(True)
                # Рекурсивно копируем детей этой папки
                if child.hasChildren():
                    self._copy_child_items(new_child, child)
                target_item.appendRow(new_child)
            else:  # Это устройство
                if hasattr(child, 'device') and child.device:
                    if isinstance(child.device, dict):
                        device = DeviceModel(child.device)
                    else:
                        device = DeviceModel(child.device.export())
                    new_child = CustomTreeItem(device.name, device)
                    new_child.setData(False, FOLDER_DATAROLE)
                    new_child.setCheckState(child.checkState())
                    new_child.setDropEnabled(True)
                    new_child.setDragEnabled(True)
                    target_item.appendRow(new_child)

    def load_tree_data(self, data):
        """Загрузка данных в дерево"""
        self.clear()
        root = self.invisibleRootItem()
        self._load_group_items(root, data)
        self.setHorizontalHeaderLabels(["Списки"])
        

    def sort_group_data(self, data):
        """Сортировка: сначала узлы с дочерними элементами, затем простые, по алфавиту"""
        def sort_key(item):
            if isinstance(item, dict):
                if "name" in item and "host" in item:
                    return (1, item["name"].lower())  # leaf
                else:
                    key = next(iter(item.keys()))
                    return (0, key.lower())  # group
            return (1, str(item).lower())
        return sorted(data, key=sort_key)
    

    def _load_group_items(self, parent_item, data):
        """Рекурсивная загрузка элементов группы"""
        for item in data:
            if isinstance(item, dict):
                if "name" in item and "host" in item:
                    device = DeviceModel(item)
                    device_item = CustomTreeItem(device.name, device)
                    device_item.setData(False, FOLDER_DATAROLE)  # Для устройств
                    parent_item.appendRow(device_item)
                else:
                    for key, value in item.items():
                        group_item = CustomTreeItem(key)
                        group_item.setData(True, FOLDER_DATAROLE)  # Для папок
                        parent_item.appendRow(group_item)
                        self._load_group_items(group_item, value)


    def sort_tree(self, parent_item=None):
        pass


    def clear_tree(self):
        """Очистка дерева"""
        self.clear()


class DeviceTreeView(QTreeView):
    itemDoubleClicked = Signal(object)
    itemDataChanged = Signal(object, object)  # Сигнал об изменении состояния чекбокса
    fileDropped = Signal(object)
    data_changed = Signal(list)  # Сигнал для уведомления об изменении данных дерева
    device_removed = Signal(object)  # Сигнал об удалении устройства (DeviceModel)

    def __init__(self, parent=None, read_only=False, enable_ping=True):
        super().__init__(parent)
        self._model = CustomTreeItemModel(read_only=read_only)
        self.read_only = read_only
        self.enable_ping = enable_ping
        self.setModel(self._model)

        self.setup_ui()

        self.doubleClicked.connect(self.on_item_double_clicked)
        self.customContextMenuRequested.connect(self.show_tree_context_menu)

        # Подключаем сигнал изменения чекбокса
        self._model.itemDataChanged.connect(self._on_item_checkstate_changed)

        # Подключаем сигнал изменения данных модели к сигналу дерева
        self._model.data_changed.connect(self.data_changed.emit)

        if self.enable_ping:
            get_host_ping_timer_manager().online_updated.connect(self._on_ping_online_updated)

    def _on_item_checkstate_changed(self, item, role):
        """Обработчик изменения чекбокса"""
        if role == Qt.ItemDataRole.CheckStateRole:
            self.on_item_checkstate_changed(item, item.checkState())

    def _on_ping_online_updated(self, device: DeviceModel, is_online: bool):
        """Обновление иконки элемента дерева при изменении статуса пинга"""
        item = self._find_tree_item_by_host(device.host)
        if item:
            try:
                item.setIcon(QIcon(device.icon))
            except (RuntimeError, AttributeError):
                pass

    def _find_tree_item_by_host(self, host: str, parent=None):
        """Рекурсивный поиск элемента дерева по хосту"""
        if parent is None:
            parent = self._model.invisibleRootItem()
        for i in range(parent.rowCount()):
            child = parent.child(i)
            if hasattr(child, 'device') and child.device and child.device.host == host:
                return child
            if child.hasChildren():
                result = self._find_tree_item_by_host(host, child)
                if result:
                    return result
        return None


    def setup_ui(self):
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        

    def add_device(self, parent: CustomTreeItem, device: DeviceModel):
        if not parent.hasChildren():
            parent.setData(True, FOLDER_DATAROLE)
            parent.device = None
            parent.setIcon(QIcon(ICONS['folder']))

        device_item = CustomTreeItem(device.name, device)
        device_item.setFlags(device_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        device_item.setCheckState(Qt.CheckState.Unchecked)
        device_item.setIcon(QIcon(ICONS['default']))
        parent.appendRow(device_item)

        return device_item
    

    def get_all_checked_devices(self):
        """Рекурсивно возвращает все отмеченные устройства (DeviceModel) в дереве"""
        checked_devices = []
        def collect_checked(item):
            for i in range(item.rowCount()):
                child = item.child(i)
                if hasattr(child, 'device') and child.device and child.checkState() == Qt.CheckState.Checked:
                    checked_devices.append(child.device)
                if child.hasChildren():
                    collect_checked(child)
        root = self._model.invisibleRootItem()
        collect_checked(root)
        return checked_devices

    def get_selected_devices(self):
        """Возвращает список всех выбранных устройств (не папок) в текущем выделении"""
        selected_items = []
        for index in self.selectedIndexes():
            item = self._model.itemFromIndex(index)
            if item and hasattr(item, 'device') and item.device is not None:
                selected_items.append(item)
        return selected_items

    def get_selected_items(self):
        """Возвращает список всех выбранных элементов (устройства и папки) в текущем выделении"""
        selected_items = []
        seen = set()
        for index in self.selectedIndexes():
            item = self._model.itemFromIndex(index)
            if item and id(item) not in seen:
                selected_items.append(item)
                seen.add(id(item))
        return selected_items
    

    def collectChecked(self, node):
        checked = []
        
        if node is None:
            return checked
        
        for i in range(node.rowCount()):
            child = node.child(i)
            if child.checkState() == Qt.CheckState.Checked:
                checked.append(child)
        return checked
    
    def clear_devices(self):
        """Очистка всех устройств с остановкой таймеров"""
        get_host_ping_timer_manager().stop_all()
        self._model.clear()
    

    def on_item_double_clicked(self, index):
        item = self._model.itemFromIndex(index)
        if item and hasattr(item, 'device'):
            self.itemDoubleClicked.emit(item)


    def add_device_to_group(self, parent_item):
        """Добавление нового устройства в группу"""
        from src.ui.dialogs.common.common import DeviceEditDialog
            
        success, device = DeviceEditDialog.add_device(self)
        if success:
            device_item = CustomTreeItem(device.name, device)
            device_item.setFlags(device_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            device_item.setCheckState(Qt.CheckState.Unchecked)
            device_item.setIcon(QIcon(device.icon))
            if parent_item is None:
                parent_item = self._model.invisibleRootItem()
            parent_item.appendRow(device_item)
            
            new_data = self.get_tree_data()
            self.data_changed.emit(new_data)  # ← Эмитим сигнал вместо вызова datastore


    def add_folder_to_group(self, parent_item):
        """Добавление новой папки в группу"""
        if parent_item is None:
            parent_item = self._model.invisibleRootItem()
            
        # Создаем диалог для ввода имени папки
        folder_name, ok = QInputDialog.getText(
            self,
            "Новая папка",
            "Введите имя папки:",
            QLineEdit.Normal,
            "Новая папка"
        )
        
        if ok  and folder_name:
            folder_item = CustomTreeItem(folder_name)
            folder_item.setIcon(QIcon(ICONS['folder']))
            parent_item.appendRow(folder_item)
            return folder_item
        
        new_data = self.get_tree_data()
        datastore.set_hosts_data(new_data)
    

    def edit_device(self, item):
        """Редактирование существующего устройства"""
        from src.ui.dialogs.common.common import DeviceEditDialog
        
        if hasattr(item, 'device'):
            device: DeviceModel = item.device
            success, updated_data = DeviceEditDialog.edit_device(device, self)
            if success:
                device.update(updated_data)
                item.setText(device.name)

    
    def remove_from_treeview(self, item: CustomTreeItem):
        """Удаление элемента из дерева (для обратной совместимости)"""
        self.remove_selected_items([item])

    def remove_selected_items(self, items: list):
        """Удаление списка выбранных элементов из дерева"""
        if not items:
            return

        if QMessageBox.question(
            self,
            "Удаление элементов",
            f"Вы уверены, что хотите удалить {len(items)} элемент(ов)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.No:
            return

        # Собираем устройства для эмита сигнала (включая вложенные в папки)
        devices_to_remove = []
        
        def collect_devices(item):
            """Рекурсивно собирает все устройства из элемента"""
            if hasattr(item, 'device') and item.device:
                devices_to_remove.append(item.device)
            for i in range(item.rowCount()):
                child = item.child(i)
                collect_devices(child)
        
        for item in items:
            collect_devices(item)
        
        # Эмитим сигналы для всех устройств
        for device in devices_to_remove:
            self.device_removed.emit(device)

        # Группируем элементы по родителю для корректного удаления
        items_by_parent = defaultdict(list)
        for item in items:
            parent = item.parent()
            items_by_parent[id(parent)].append((parent, item.row()))

        # Удаляем элементы (с конца, чтобы не сбить индексы)
        for parent_id, parent_rows in items_by_parent.items():
            for parent, row in sorted(parent_rows, key=lambda x: x[1], reverse=True):
                if parent is not None:
                    parent.removeRow(row)
                else:
                    self._model.removeRow(row)

        self.clearSelection()
        new_data = self.get_tree_data()
        self.data_changed.emit(new_data)

    def move_selected_to_folder(self, items: list, target_folder: CustomTreeItem):
        """Перемещение списка выбранных устройств в целевую папку"""
        if not items or not target_folder:
            return

        # Убедимся, что целевой элемент - папка
        if not target_folder.data(FOLDER_DATAROLE):
            return

        # Перемещаем элементы (с конца, чтобы не сбить индексы)
        for item in sorted(items, key=lambda x: x.row(), reverse=True):
            # Удаляем из текущего родителя
            parent = item.parent()
            if parent:
                parent.removeRow(item.row())
            else:
                self._model.removeRow(item.row())

            # Добавляем в целевую папку
            target_folder.appendRow(item)

        self.clearSelection()
        new_data = self.get_tree_data()
        self.data_changed.emit(new_data)


    def show_tree_context_menu(self, position):
        """Показать контекстное меню для элемента дерева"""

        if self.read_only:
            return

        menu = QMenu()

        item: CustomTreeItem = None
        index = self.indexAt(position)

        if index.isValid():
            item: CustomTreeItem = self._model.itemFromIndex(index)

        if item is None:
            item = self._model.invisibleRootItem()

        # Определяем целевую папку для добавления
        if item.data(FOLDER_DATAROLE):
            target_item = item
        else:
            target_item = item.parent()

        # Получаем выбранные устройства
        selected_devices = self.get_selected_devices()
        has_selection = len(selected_devices) > 0

        # Если нет выделения, но клик по устройству - используем это устройство
        if not has_selection and item and hasattr(item, 'device') and item.device is not None:
            selected_devices = [item]
            has_selection = True

        # Add device action
        add_device_action = menu.addAction(QIcon(ICONS.get('default', '')), "Добавить устройство")
        add_device_action.triggered.connect(lambda: self.add_device_to_group(target_item))

        add_folder_action = menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Добавить папку")
        add_folder_action.triggered.connect(lambda: self.add_folder_to_group(target_item))

        menu.addSeparator()

        sort_action = menu.addAction(QIcon(ICONS.get('menu_tools_grid', '')), "Сортировать")
        sort_action.triggered.connect(lambda: self.sort_current_group(target_item))

        menu.addSeparator()

        if item.data(FOLDER_DATAROLE):
            rename_folder_action = menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Переименовать папку")
            rename_folder_action.triggered.connect(lambda: self.rename_folder_action(item))

        selected_items = self.get_selected_items()
        has_selection = len(selected_items) > 0

        if not has_selection and index.isValid():
            selected_items = [item]
            has_selection = True

        if has_selection:
            selected_devices = [it for it in selected_items if hasattr(it, 'device') and it.device is not None]

            if len(selected_devices) == 1 and len(selected_items) == 1:
                if selected_devices[0].device:
                    edit_action = menu.addAction(QIcon(ICONS.get('menu_settings', '')), "Редактировать")
                    edit_action.triggered.connect(lambda: self.edit_device(selected_devices[0]))

            remove_text = f"Удалить ({len(selected_items)})" if len(selected_items) > 1 else "Удалить"
            remove_action = menu.addAction(QIcon(ICONS.get('menu_delete', '')), remove_text)
            remove_action.triggered.connect(lambda: self.remove_selected_items(selected_items))

        menu.exec_(self.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _show_move_dialog(self, items: list):
        """Показать диалог для перемещения устройств в выбранную папку"""
        from PySide6.QtWidgets import QInputDialog

        # Собираем все папки из дерева
        folders = self._get_all_folders()
        if not folders:
            QMessageBox.information(self, "Перемещение", "Нет доступных папок для перемещения.")
            return

        # Показываем диалог выбора папки
        folder_names = [f[0] for f in folders]
        folder_name, ok = QInputDialog.getItem(
            self,
            "Переместить устройства",
            "Выберите папку:",
            folder_names,
            0,
            False
        )

        if ok and folder_name:
            # Находим папку по имени
            target_folder = None
            for folder_path, folder_item in folders:
                if folder_path == folder_name:
                    target_folder = folder_item
                    break

            if target_folder:
                self.move_selected_to_folder(items, target_folder)

    def _get_all_folders(self):
        """Рекурсивно собирает все папки из дерева"""
        folders = []

        def collect_folders(item, path=""):
            for i in range(item.rowCount()):
                child = item.child(i)
                if child.data(FOLDER_DATAROLE):
                    folder_path = f"{path}/{child.text()}" if path else child.text()
                    folders.append((folder_path, child))
                    if child.hasChildren():
                        collect_folders(child, folder_path)

        root = self._model.invisibleRootItem()
        collect_folders(root)
        return folders


    def startDrag(self, supportedActions):
        # Получаем все выбранные индексы
        selected_indexes = self.selectedIndexes()
        if not selected_indexes:
            return

        # Сохраняем ссылки на перетаскиваемые элементы
        global _drag_source_items
        _drag_source_items = [self.model().itemFromIndex(idx) for idx in selected_indexes]

        # Разрешаем перетаскивание всех выбранных элементов
        mimeData = self.model().mimeData(selected_indexes)
        drag = QDrag(self)
        drag.setMimeData(mimeData)

        # Визуализация перетаскиваемого элемента (первый элемент)
        if _drag_source_items:
            pixmap = _drag_source_items[0].icon().pixmap(32, 32)
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())

        # Выполняем drag - удаление произойдет в dropMimeData
        drag.exec(Qt.DropAction.MoveAction)


    def dropEvent(self, event: QDropEvent):
        mime_data = event.mimeData()

        # Обработка перетаскивания элементов дерева (папки/устройства)
        if mime_data.hasFormat(MIME_TYPE):
            # Определяем целевой индекс и позицию вставки с учётом индикатора (OnItem / Above / Below)
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            drop_pos = self.dropIndicatorPosition()

            if not index.isValid():
                # Пустая область — вставляем в корень в конец
                target_parent_index = QModelIndex()
                insert_row = self._model.rowCount()
            else:
                if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                    # Поместить внутрь элемента — в конец его детей
                    target_parent_index = index
                    parent_item = self._model.itemFromIndex(index)
                    insert_row = parent_item.rowCount() if parent_item is not None else 0
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
                    target_parent_index = index.parent()
                    insert_row = index.row()
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    target_parent_index = index.parent()
                    insert_row = index.row() + 1
                else:
                    target_parent_index = index.parent()
                    insert_row = index.row() if index.isValid() else self._model.rowCount()

            # Получаем parent_item (корень если невалиден)
            parent_item = self._model.itemFromIndex(target_parent_index) if target_parent_index.isValid() else self._model.invisibleRootItem()

            # Разрешаем дроп только в папку или в корень
            parent_is_folder = True
            if parent_item is not None:
                parent_is_folder = bool(parent_item.data(FOLDER_DATAROLE))

            if target_parent_index.isValid() and not parent_is_folder:
                event.ignore()
                return

            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

            # Вызываем модельную обработку дропа с корректной позицией вставки
            success = self._model.dropMimeData(
                mime_data,
                event.dropAction(),
                insert_row,
                0,
                target_parent_index
            )

            # Эмитим обновлённые данные дерева при успехе
            if success:
                self.data_changed.emit(self.get_tree_data())
            return

        # Обработка перетаскивания файлов (URLs)
        if mime_data.hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

            # Определяем целевую папку
            index = self.indexAt(event.position().toPoint())
            target_item = None
            if index.isValid():
                item = self._model.itemFromIndex(index)
                if item and item.data(FOLDER_DATAROLE):
                    target_item = item
                elif item and item.parent():
                    target_item = item.parent()

            if target_item is None:
                target_item = self._model.invisibleRootItem()

            # Обрабатываем каждый файл
            files_processed = 0
            for url in mime_data.urls():
                file_path = url.toLocalFile()
                if file_path.lower().endswith('.txt'):
                    self._parse_and_import_txt_file(file_path, target_item)
                    files_processed += 1

            if files_processed > 0:
                self.sort_tree()
                logger.info(f"Imported devices from {files_processed} txt file(s)")
            return

        # Обработка текстовых данных (plain text)
        if mime_data.hasText():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

            # Определяем целевую папку
            index = self.indexAt(event.position().toPoint())
            target_item = None
            if index.isValid():
                item = self._model.itemFromIndex(index)
                if item and item.data(FOLDER_DATAROLE):
                    target_item = item
                elif item and item.parent():
                    target_item = item.parent()

            if target_item is None:
                target_item = self._model.invisibleRootItem()

            # Пытаемся распарсить текст как список хостов
            text = mime_data.text()
            self._parse_text_and_import(text, target_item)
            return

        event.ignore()


    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mouseButtons() != Qt.MouseButton.LeftButton:
            return

        if self.read_only:
            return

        # Принимаем перетаскивание элементов дерева
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
            return

        # Принимаем перетаскивание файлов
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return

        # Принимаем текстовые данные
        if event.mimeData().hasText():
            event.acceptProposedAction()
            return

        event.ignore()
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        """Обработка выхода за пределы виджета - сбрасываем источники если drag отменен"""
        global _drag_source_items
        # Сбрасываем ссылку при выходе из виджета (drag отменен)
        _drag_source_items = []
        super().dragLeaveEvent(event)


    def rename_folder_action(self, item: CustomTreeItem):
        if item.data(FOLDER_DATAROLE):
            new_name, ok = QInputDialog.getText(
                self,
                "Переименовать папку",
                "Введите новое имя папки:",
                QLineEdit.Normal,
                item.text()
            )

            if ok and new_name:
                item.setText(new_name)

                new_data = self.get_tree_data()
                self.data_changed.emit(new_data)  # ← Эмитим сигнал вместо вызова datastore

    def sort_current_group(self, group_item: CustomTreeItem):
        """Сортировка элементов в текущей группе"""
        if group_item is None:
            group_item = self._model.invisibleRootItem()
        
        # Получаем данные дочерних элементов
        children_data = []
        for i in range(group_item.rowCount()):
            child = group_item.child(i)
            if child.hasChildren():
                # Это подгруппа - сохраняем как словарь
                children_data.append({child.text(): self._get_child_items_data(child)})
            else:
                # Это устройство или пустая папка
                if hasattr(child, 'device') and child.device:
                    children_data.append(child.device.export())
                else:
                    children_data.append({child.text(): {}})
        
        # Сортируем данные
        sorted_data = self._model.sort_group_data(children_data)
        
        # Очищаем группу
        group_item.removeRows(0, group_item.rowCount())
        
        # Перезагружаем отсортированные элементы
        self._load_group_items(group_item, sorted_data)
        
        # Уведомляем об изменении данных
        new_data = self.get_tree_data()
        self.data_changed.emit(new_data)
        logger.info(f"Sorted group: {group_item.text() if group_item.text() else 'root'}")

    def _get_child_items_data(self, parent_item: CustomTreeItem) -> list:
        """Рекурсивное получение данных дочерних элементов"""
        items = []
        for i in range(parent_item.rowCount()):
            child = parent_item.child(i)
            if child.hasChildren():
                items.append({child.text(): self._get_child_items_data(child)})
            else:
                if hasattr(child, 'device') and child.device:
                    items.append(child.device.export())
                else:
                    items.append({child.text(): {}})
        return items


    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)

        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.pos())
            if not index.isValid():
                self.selectionModel().clearSelection() 
                self.setCurrentIndex(self._model.invisibleRootItem().index())
            else:
                model = self.model()
                if isinstance(model, QStandardItemModel):
                    self.current_selected_item = model.itemFromIndex(index)
                else:
                    self.setCurrentIndex(self._model.invisibleRootItem().index())


    def sort_tree(self):
        self._model.sort_tree()

    def load_tree_data(self, data: dict):
        """Загрузка данных в дерево с запуском таймеров для отмеченных устройств"""
        self._model.clear()
        root = self._model.invisibleRootItem()
        self._load_group_items(root, data)

        # Запускаем таймеры для уже отмеченных устройств после загрузки
        if self.enable_ping:
            self._sync_checked_devices_to_ping_manager()

    def _sync_checked_devices_to_ping_manager(self):
        """Синхронизация отмеченных устройств - запуск таймеров с разнесением по времени"""
        from PySide6.QtCore import QTimer

        checked_devices = self.get_all_checked_devices()
        if not checked_devices:
            return

        batch_size = config.app.network.thread_count
        delay_per_batch_ms = 500
        manager = get_host_ping_timer_manager()

        def start_batch(devices, delay_ms=0):
            if not devices:
                return
            def do_start():
                for device in devices:
                    manager.start_ping(device)
            if delay_ms > 0:
                QTimer.singleShot(delay_ms, do_start)
            else:
                do_start()

        for i in range(0, len(checked_devices), batch_size):
            batch = checked_devices[i:i + batch_size]
            batch_index = i // batch_size
            start_batch(batch, delay_ms=batch_index * delay_per_batch_ms)

        logger.info(f"Started ping timers for {len(checked_devices)} checked devices (batched)")

    def on_item_checkstate_changed(self, item, check_state):
        """Обработчик изменения чекбокса устройства"""
        if not hasattr(item, 'device') or item.device is None:
            return

        if not self.enable_ping:
            return

        manager = get_host_ping_timer_manager()
        if check_state == Qt.CheckState.Checked:
            manager.start_ping(item.device)
            logger.debug(f"DeviceTreeView: Started ping for {item.device.host}")
        else:
            manager.stop_ping(item.device.host)
            logger.debug(f"DeviceTreeView: Stopped ping for {item.device.host}")

    def _load_group_items(self, parent_item, data):
        """Рекурсивная загрузка элементов группы"""
        for item in data:
            if isinstance(item, dict):
                if "name" in item and "host" in item:
                    device = DeviceModel(item)
                    device_item = CustomTreeItem(device.name, device)
                    device_item.setData(False, FOLDER_DATAROLE)  # Для устройств
                    device_item.setCheckState(Qt.CheckState.Unchecked)  # Сбрасываем чекбокс
                    parent_item.appendRow(device_item)
                else:
                    for key, value in item.items():
                        group_item = CustomTreeItem(key)
                        group_item.setData(True, FOLDER_DATAROLE)  # Для папок
                        parent_item.appendRow(group_item)
                        self._load_group_items(group_item, value)

    def export_tree_data(self):
        return self._model.export_tree_data()

    def get_tree_data(self):
        return self._model.export_tree_data()

    def _parse_and_import_txt_file(self, file_path: str, parent_item: CustomTreeItem):
        """
        Парсинг txt файла и импорт устройств в дерево.
        
        Поддерживаемые форматы:
        - Один хост на строку (только IP/hostname)
        - Формат: name,host,port,login,password (CSV-подобный)
        - Формат: name:host:port:login:password (через двоеточие)
        - Комментарии начинаются с #
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            devices_added = 0
            filename = os.path.basename(file_path)
            
            for line in lines:
                line = line.strip()
                
                # Пропускаем пустые строки и комментарии
                if not line or line.startswith('#'):
                    continue
                
                device = self._parse_device_line(line, filename)
                if device:
                    self.add_device(parent_item, device)
                    devices_added += 1
            
            if devices_added > 0:
                logger.info(f"Added {devices_added} devices from file: {filename}")
            
        except Exception as e:
            logger.error(f"Error parsing txt file {file_path}: {e}")

    def _parse_text_and_import(self, text: str, parent_item: CustomTreeItem):
        """
        Парсинг текста и импорт устройств в дерево.
        
        Поддерживаемые форматы:
        - Один хост на строку
        - Разделители: запятая, точка с запятой, пробел, двоеточие
        """
        lines = text.strip().split('\n')
        devices_added = 0
        
        for line in lines:
            line = line.strip()
            
            # Пропускаем пустые строки и комментарии
            if not line or line.startswith('#'):
                continue
            
            device = self._parse_device_line(line, "clipboard")
            if device:
                self.add_device(parent_item, device)
                devices_added += 1
        
        if devices_added > 0:
            logger.info(f"Added {devices_added} devices from clipboard/text")

    def _parse_device_line(self, line: str, source: str = "") -> Optional[DeviceModel]:
        """
        Парсинг одной строки в объект DeviceModel.
        
        Поддерживаемые форматы:
        1. Только хост: "192.168.1.1" -> имя = хост
        2. CSV: "name,host,port,login,password"
        3. Colon: "name:host:port:login:password"
        4. С пробелами: "name host port login password"
        """
        line = line.strip()
        if not line:
            return None
        
        # Пробуем CSV формат (запятая)
        if ',' in line:
            parts = [p.strip() for p in line.split(',')]
            return self._create_device_from_parts(parts, source)
        
        # Пробуем формат с двоеточием (но не IPv6 без скобок)
        # IPv6 адреса в скобках: [::1]:22
        if ':' in line and not line.startswith('['):
            # Проверяем, не похожа ли строка на IPv6
            colon_count = line.count(':')
            if colon_count <= 4:  # Скорее всего не IPv6
                parts = [p.strip() for p in line.split(':')]
                return self._create_device_from_parts(parts, source)
        
        # Пробуем формат с точкой с запятой
        if ';' in line:
            parts = [p.strip() for p in line.split(';')]
            return self._create_device_from_parts(parts, source)
        
        # Пробуем формат с пробелом/табуляцией
        if '\t' in line:
            parts = [p.strip() for p in line.split('\t')]
            return self._create_device_from_parts(parts, source)
        
        if '  ' in line:
            parts = [p.strip() for p in line.split()]
            return self._create_device_from_parts(parts, source)
        
        # Просто хост (IP или hostname)
        if self._is_valid_host(line):
            return DeviceModel({
                "name": line,
                "host": line,
                "port": config.app.ssh.port,
                "login": config.app.ssh.username,
            })
        
        return None

    def _create_device_from_parts(self, parts: list, source: str = "") -> Optional[DeviceModel]:
        """Создание DeviceModel из списка частей"""
        if not parts or not parts[0]:
            return None
        
        device_data = {
            "name": parts[0] if len(parts) > 0 and parts[0] else "",
            "host": parts[1] if len(parts) > 1 and parts[1] else parts[0],
            "port": config.app.ssh.port,
            "login": config.app.ssh.username,
            "password": "",
        }
        
        # Парсим порт если есть
        if len(parts) > 2 and parts[2]:
            try:
                device_data["port"] = int(parts[2])
            except ValueError:
                # Если порт не число, возможно это логин
                device_data["login"] = parts[2]
        
        # Парсим логин если есть
        if len(parts) > 3 and parts[3]:
            device_data["login"] = parts[3]
        
        # Парсим пароль если есть
        if len(parts) > 4 and parts[4]:
            device_data["password"] = parts[4]
        
        # Если имя пустое, используем хост
        if not device_data["name"]:
            device_data["name"] = device_data["host"]
        
        # Проверяем валидность хоста
        if not self._is_valid_host(device_data["host"]):
            return None
        
        return DeviceModel(device_data)

    def _is_valid_host(self, host: str) -> bool:
        """
        Проверка валидности хоста (IP, hostname, domain).
        """
        if not host:
            return False
        
        # IPv4 адрес
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(ipv4_pattern, host):
            # Проверяем что каждая часть <= 255
            parts = host.split('.')
            return all(0 <= int(p) <= 255 for p in parts)
        
        # IPv6 адрес (упрощенно)
        if host.startswith('[') and host.endswith(']'):
            return True
        
        # Hostname / domain name
        hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
        if re.match(hostname_pattern, host):
            return True
        
        # localhost
        if host.lower() in ['localhost', 'localhost.localdomain']:
            return True

        return False
