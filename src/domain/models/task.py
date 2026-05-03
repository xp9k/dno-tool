# (Импорты и константы обновлены под новую терминологию Tasks/Commands)
from PySide6.QtWidgets import QDialog, QTreeView, QLineEdit, QTextEdit, QSpinBox, \
                              QPushButton, QListWidget, QListWidgetItem, QHBoxLayout, \
                              QVBoxLayout, QGroupBox, QSplitter, QMessageBox, QInputDialog, \
                              QDialogButtonBox, QApplication, QMainWindow, QWidget, QLabel, \
                              QSizePolicy
from PySide6.QtGui import QStandardItemModel, QStandardItem, QIcon
from PySide6.QtCore import Qt, QModelIndex, Signal, QObject, Slot
from ...config import ICONS, config

# Роль для хранения полного словаря Task (задачи)
TASK_DATA_ROLE = Qt.UserRole + 1

class TaskEditModel(QStandardItemModel):
    """Модель данных для редактирования иерархической структуры Tasks."""
    structureChanged = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels(["Элемент"])
        self.default_task = {
            "name": "Новая задача",
            "description": "",
            "commands": [{"type": "ssh", "text": ""}],
            "timeout": config.app.ssh.command_timeout,
            "params": []
        }
        # Backwards-compatible attribute name used by older dialogs/code
        self.default_command = self.default_task

    def create_task(self, command_type: str = "ssh") -> dict:
        """Создает новую Task с одним шагом (Command) указанного типа"""
        t = self.default_task.copy()
        t["commands"] = [{"type": command_type, "text": ""}]
        return t

    def load_data(self, data_structure: list):
        """Загружает данные из структуры словарей в модель (Tasks и категории)."""
        self.clear()
        root_item = self.invisibleRootItem()
        self._populate_recursive(root_item, data_structure)

    def _populate_recursive(self, parent_item: QStandardItem, data_list: list):
        if not isinstance(data_list, list): return
        for item_data in data_list:
            if not isinstance(item_data, dict): continue

            # Task (имеет name и commands)
            if "name" in item_data and "commands" in item_data:
                if isinstance(item_data["commands"], list):
                    if item_data["commands"] and not isinstance(item_data["commands"][0], dict):
                        item_data["commands"] = [{"type": "ssh", "text": cmd} for cmd in item_data["commands"]]

                task_name = item_data.get("name", "Без имени")
                task_item = QStandardItem(task_name)
                task_item.setEditable(False)
                task_item.setSelectable(True)
                task_item.setIcon(QIcon(ICONS['command']))
                task_item.setData(item_data, TASK_DATA_ROLE)
                parent_item.appendRow(task_item)

            # Категория (папка)
            elif len(item_data) == 1:
                category_name = list(item_data.keys())[0]
                child_data_list = item_data[category_name]
                if isinstance(child_data_list, list):
                    category_item = QStandardItem(category_name)
                    category_item.setEditable(False)
                    category_item.setSelectable(True)
                    category_item.setIcon(QIcon(ICONS['folder']))
                    parent_item.appendRow(category_item)
                    self._populate_recursive(category_item, child_data_list)

    def get_task_data(self, index: QModelIndex) -> dict | None:
        """Возвращает данные Task (словарь) или None для категории."""
        if not index.isValid():
            return None
        return self.data(index, TASK_DATA_ROLE)
    
    # Backwards-compatible method name expected by dialogs (get_item_data)
    def get_item_data(self, index: QModelIndex) -> dict | None:
        return self.get_task_data(index)

    def is_category(self, index: QModelIndex) -> bool:
        """Проверяет, является ли элемент категорией (т.е. не имеет данных Task)."""
        return self.get_task_data(index) is None and index.isValid()

    def add_item(self, parent_index: QModelIndex, item_data: dict, is_category: bool):
        """Добавляет новый элемент (Task или категория) к родителю."""
        parent_item = self.itemFromIndex(parent_index) if parent_index.isValid() else self.invisibleRootItem()
        if not parent_item:
            return False

        if is_category:
            category_name = list(item_data.keys())[0]
            new_item = QStandardItem(category_name)
            new_item.setEditable(False)
            new_item.setSelectable(True)
            new_item.setIcon(QIcon(ICONS['folder']))
        else:
            task_name = item_data.get("name", "Без имени")
            new_item = QStandardItem(task_name)
            new_item.setEditable(False)
            new_item.setSelectable(True)
            new_item.setIcon(QIcon(ICONS['command']))
            new_item.setData(item_data, TASK_DATA_ROLE)

        parent_item.appendRow(new_item)
        self.structureChanged.emit()
        return True

    def remove_item(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        parent_index = index.parent()
        row_to_remove = index.row()
        removed = self.removeRow(row_to_remove, parent_index)
        if removed:
            self.structureChanged.emit()
        return removed

    def update_item(self, index: QModelIndex, new_data: dict):
        """Обновляет Task или имя категории."""
        if not index.isValid():
            return False

        item = self.itemFromIndex(index)
        if not item:
            return False

        is_cat = self.is_category(index)

        if is_cat:
            new_name = list(new_data.keys())[0]
            item.setText(new_name)
        else:
            updated_data = new_data.copy()

            # Убедимся что все шаги (commands) имеют тип
            if "commands" in updated_data:
                new_commands = []
                for cmd in updated_data["commands"]:
                    if isinstance(cmd, str):
                        new_commands.append({"type": "ssh", "text": cmd})
                    else:
                        new_commands.append(cmd.copy())
                updated_data["commands"] = new_commands

            new_name = updated_data.get("name", "Без имени")
            item.setText(new_name)
            item.setData(updated_data, TASK_DATA_ROLE)

        self.structureChanged.emit()
        return True

    def get_data_structure(self) -> list:
        """Реконструирует структуру Tasks из модели."""
        root_index = self.invisibleRootItem().index()
        return self._build_recursive_structure(root_index)

    def _build_recursive_structure(self, parent_index: QModelIndex) -> list:
        data_list = []
        row_count = self.rowCount(parent_index)
        for row in range(row_count):
            current_index = self.index(row, 0, parent_index)
            item_data = self.get_task_data(current_index)

            if item_data:
                data_list.append(item_data.copy())
            else:
                item = self.itemFromIndex(current_index)
                if item:
                    category_name = item.text()
                    children_list = self._build_recursive_structure(current_index)
                    data_list.append({category_name: children_list})
        return data_list

# Backwards-compatible alias для совместимости с кодом, ожидающим старое имя
CommandEditModel = TaskEditModel