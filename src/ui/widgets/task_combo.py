"""Выпадающий список задач с иерархическим деревом и параметрами команд."""

from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from src.config import ICONS
from src.logger import logger

# Роль для хранения полного словаря задачи (Task)
TASK_DATA_ROLE = Qt.UserRole + 1


class TaskTreeView(QTreeView):
    """Кастомный QTreeView для TaskComboBox, перехватывающий выбор элементов."""
    
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._task_combo = parent

    def mousePressEvent(self, event: QMouseEvent):
        index = self.indexAt(event.pos())
        if index.isValid():
            task_data = self.model().data(index, TASK_DATA_ROLE)
            if task_data:
                # Это задача - закрываем popup и обновляем текст
                self._task_combo.hidePopup()
                self._task_combo._on_task_selected(index, task_data)
                event.accept()
                return
        # Для категорий или невалидных индексов - стандартное поведение
        super().mousePressEvent(event)


class TaskTreeModel(QStandardItemModel):
    """Модель данных для QTreeView, отображающая иерархическую структуру Tasks (пакетов шагов)."""
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

    def load_data(self, data_structure: list):
        self.clear()
        root_item = self.invisibleRootItem()
        self._populate_recursive(root_item, data_structure)
        # root_item.sortChildren(0)
        self.collapse_all_items()

    def collapse_all_items(self):
        parent = self.parent()
        if isinstance(parent, TaskComboBox):
            tree_view = parent.view()
            if isinstance(tree_view, QTreeView):
                tree_view.collapseAll()

    def _populate_recursive(self, parent_item: QStandardItem, data_list: list):
        if not isinstance(data_list, list): return
        for item_data in data_list:
            if not isinstance(item_data, dict): continue
            # Это Task (имя + список commands)
            if "name" in item_data and "commands" in item_data:
                task_name = item_data.get("name", "Без имени")
                task_item = QStandardItem(task_name)
                task_item.setIcon(QIcon(ICONS.get('command', '')))
                task_item.setEditable(False); task_item.setSelectable(True)

                # Convert legacy command format if needed
                if "commands" in item_data:
                    commands = item_data["commands"]
                    if commands and isinstance(commands, list):
                        if not isinstance(commands[0], dict):
                            item_data["commands"] = [{"type": "ssh", "text": cmd} for cmd in commands]

                task_item.setData(item_data, TASK_DATA_ROLE)
                desc = item_data.get("description", "")
                if "params" in item_data and item_data["params"]:
                    desc += f"\nПараметры: {', '.join(item_data['params'])}"
                task_item.setToolTip(desc)
                parent_item.appendRow(task_item)

            # Категория (папка) содержит список дочерних элементов
            elif len(item_data) == 1:
                category_name = list(item_data.keys())[0]
                child_data_list = item_data[category_name]
                if isinstance(child_data_list, list):
                    category_item = QStandardItem(category_name)
                    category_item.setEditable(False); category_item.setSelectable(False)
                    category_item.setIcon(QIcon(ICONS['folder']))
                    parent_item.appendRow(category_item)
                    self._populate_recursive(category_item, child_data_list)

    def find_first_task_item(self, parent_index: QModelIndex = QModelIndex()) -> QModelIndex:
        '''Находит первую Task в дереве, начиная с индекса parent_index'''
        rows = self.rowCount(parent_index)
        for row in range(rows):
            index = self.index(row, 0, parent_index)
            if self.data(index, TASK_DATA_ROLE): return index
            if self.hasChildren(index):
                found_index = self.find_first_task_item(index)
                if found_index.isValid(): return found_index
        return QModelIndex()

    def export_data(self, parent_index: QModelIndex = QModelIndex()) -> list:
        data = []
        rows = self.rowCount(parent_index)
        for row in range(rows):
            index = self.index(row, 0, parent_index)
            item = self.itemFromIndex(index)
            if self.data(index, TASK_DATA_ROLE):
                # Это Task (пакет команд)
                data.append(self.data(index, TASK_DATA_ROLE))
            elif self.hasChildren(index):
                # Это категория
                category_name = item.text()
                children = self.export_data(index)
                if children:
                    data.append({category_name: children})
        return data

class TaskComboBox(QComboBox):
    taskSelected = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model = TaskTreeModel(self)
        self._tree_view = TaskTreeView(self)
        self.view().setFixedHeight(400)
        self._tree_view.setHeaderHidden(True)
        # Устанавливаем модель ДО view, чтобы view сразу знала о модели
        self.setModel(self._model)
        self.setView(self._tree_view)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.setPlaceholderText("Выберите задачу...")
        # Храним данные последней выбранной задачи (не зависит от наведения курсора)
        self._selected_task_data = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Открываем popup при клике на lineEdit
        if obj == self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            self.showPopup()
            return True
        return super().eventFilter(obj, event)

    def _on_task_selected(self, index: QModelIndex, task_data: dict):
        """Вызывается при выборе задачи из дерева."""
        path = self._get_task_path(index)
        self.lineEdit().setText(path)
        self._selected_task_data = task_data
        self.taskSelected.emit(task_data)

    def model(self) -> TaskTreeModel:
        return self._model

    def view(self) -> QTreeView:
        return self._tree_view

    def load_tasks(self, data_structure: list):
        self.model().load_data(data_structure)
        self._selected_task_data = None

    def clear(self):
        """Очищает комбобокс и сбрасывает выбранную задачу."""
        super().clear()
        self._selected_task_data = None

    def expand_all_items(self, index: QModelIndex = QModelIndex()):
        if not index.isValid():
             for row in range(self.model().rowCount()):
                 root_index = self.model().index(row, 0)
                 self.expand_all_items(root_index)
             return
        if self.model().hasChildren(index):
            self.view().expand(index)
            for row in range(self.model().rowCount(index)):
                child_index = self.model().index(row, 0, index)
                self.expand_all_items(child_index)

    def select_first_task(self):
        first_task_index = self.model().find_first_task_item()
        task_data = None
        if first_task_index.isValid():
            self.view().setCurrentIndex(first_task_index)
            task_data = self.model().data(first_task_index, TASK_DATA_ROLE)
            if task_data:
                path = self._get_task_path(first_task_index)
                self.lineEdit().setText(path)
                self._selected_task_data = task_data
        else:
             self.lineEdit().setText("")
             self._selected_task_data = None

        self.taskSelected.emit(task_data)

    def _get_task_path(self, index: QModelIndex) -> str:
        """Строит путь к задаче в виде 'Папка -> Подпапка -> Задача'"""
        names = []
        model = self.model()
        while index.isValid():
            item = model.itemFromIndex(index)
            if item is not None:
                text = item.text()
                names.append(text)
            index = index.parent()
        return " -> ".join(reversed(names))

    def get_selected_task_data(self) -> dict | None:
        """Возвращает данные последней выбранной задачи."""
        return self._selected_task_data

    def export_data(self) -> list:
        return self.model().export_data()
