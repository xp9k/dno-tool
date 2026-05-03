from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import copy

from src.domain.models.task import CommandEditModel
from src.ui.dialogs.editors.bash_edit import BashEditorDialog
from src.ui.dialogs.common.common import ExportCommandDlg, ImportCommandDlg
from .sftp_edit import SFTPCommandEditorDialog
from ..common.params import ParamsInputDialog
import json
from src.workers import Command
from src.logger import logger
from src.config import config, DEFAULT_COMMANDS_FILE, ICONS

# ARCHITECTURE: Импорт сервисов и DI контейнера
from src.di import get_container
from src.services import CommandService


class SingleLineDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideRight  # Добавляем "..." в конце
        option.features &= ~QStyleOptionViewItem.WrapText  # Отключаем перенос


class CustomLineEdit(QLineEdit):
    KeyPressed = Signal(Qt.Key, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Введите значение...")

    # Override the keyPressEvent method
    def keyPressEvent(self, event: QKeyEvent):
        # Get the key code and text
        key = event.key()
        text = event.text()

        # Emit the KeyPressed signal
        self.KeyPressed.emit(key, text)

        # print(f"Key Pressed: {key}")
        # print(f"Key Text: '{text}'")

        # You can add custom logic here based on the key pressed
        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            pass
            # print("Enter key pressed!")
            # Prevent the default behavior (e.g., submitting a form) if needed
            # event.ignore()
        elif key == Qt.Key.Key_Escape:
            pass
            # print("Escape key pressed!")
            # Clear the line edit text
            self.clear()
            # Prevent the default behavior
            event.ignore()
        else:
            # For other keys, call the base class implementation
            # This ensures the default behavior (like typing characters) still works
            super().keyPressEvent(event)


class CustomSpinBox(QSpinBox):
    # Определяем пользовательский сигнал, который будет испускаться при пользовательском изменении значения
    userValueChanged = Signal(int) # Сигнал будет передавать новое значение

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100) # Устанавливаем диапазон значений
        self.setValue(0) # Устанавливаем начальное значение

    # Переопределяем stepBy для перехвата пользовательского шага (клавиши и кнопки)
    def stepBy(self, steps):
        # Вызываем реализацию базового класса для выполнения фактического шага
        super().stepBy(steps)
        # Испускаем наш пользовательский сигнал после того, как значение было изменено действием пользователя
        self.userValueChanged.emit(self.value())

    # Также переопределяем keyPressEvent для перехвата Enter после ввода
    def keyPressEvent(self, event: QKeyEvent):
        # Проверяем, является ли нажатая клавиша Enter или Return
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            # Когда Enter нажат после ввода, значение обновляется внутренне.
            # Мы можем испустить сигнал здесь, так как это тоже пользовательский ввод.
            # Необходимо убедиться, что значение зафиксировано перед испусканием.
            # Вызов interpretText() заставляет спинбокс обновить свое значение
            # из части редактирования строки, если текст действителен.
            self.interpretText()
            self.userValueChanged.emit(self.value())
            # Опционально, предотвращаем стандартное поведение
            # event.ignore()
        else:
            # Для других клавиш вызываем реализацию базового класса
            super().keyPressEvent(event)


class ClearableTreeView(QTreeView):
    """QTreeView: используем стандартные визуальные эффекты drag'n'drop,
    но запрещаем помещать элементы внутрь не-папок (т.е. разрешаем drop
    только в категорию или в корень).
    """
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Используем стандартное поведение drag'n'drop и стандартные визуальные эффекты
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event: QDropEvent):
        """Разрешаем drop только в папку (категорию) или в корень; в остальных
        случаях игнорируем drop, сохраняя стандартные визуальные эффекты во время
        перемещения.
        """
        drop_index = self.indexAt(event.pos())
        model = self.model()

        # Определяем целевой parent для дропа: если индикатор указывает "на элемент",
        # то parent == этот элемент (т.е. помещаем внутрь), иначе parent == родитель этого индекса
        drop_pos = self.dropIndicatorPosition()
        if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            target_parent = drop_index
        else:
            target_parent = drop_index.parent()

        # Разрешаем дроп, если target_parent невалиден (корень) или является категорией
        if target_parent.isValid() and hasattr(model, 'is_category') and not model.is_category(target_parent):
            event.ignore()
            return

        # В остальных случаях передаём событие стандартной обработке и эмитим сигнал изменений
        super().dropEvent(event)
        self.data_changed.emit()

class CenterAlignDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter



class CommandEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Редактор команд")
        self.setMinimumSize(480, 480)
        self.resize(800, 600)

        # ARCHITECTURE: Получаем сервис команд через DI контейнер
        self.command_service = get_container().resolve(CommandService)

        self.modified = False
        # Original data for cancel functionality
        self._original_data = None

        self.model = CommandEditModel(self)
        try:
            # ARCHITECTURE: Используем сервис вместо datastore
            self.initial_data_structure = self.command_service.get_all_commands()
            self.model.load_data(self.initial_data_structure)
            logger.debug(f"CommandEditorDialog: Model loaded. Root row count: {self.model.rowCount()}")
            if self.model.rowCount() == 0 and len(self.initial_data_structure) > 0:
                 logger.debug("CommandEditorDialog: WARNING - Data provided but model is empty after load!")
            
            # ARCHITECTURE: Сохраняем оригинальные данные для отмены изменений
            self._original_data = copy.deepcopy(self.initial_data_structure)
            
        except Exception as e:
            logger.debug(f"CommandEditorDialog: Error loading data into model: {e}")
            QMessageBox.critical(self, "Ошибка загрузки данных", f"Не удалось загрузить данные в модель:\n{e}")
            self.model.load_data([])
            self._original_data = []

        # --- Правая панель: Дерево ---
        self.treeView = ClearableTreeView()
        self.treeView.setModel(self.model)
        self.treeView.setHeaderHidden(True)
        self.treeView.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.treeView.setContentsMargins(4, 4, 4, 4)
        logger.debug("CommandEditorDialog: TreeView created and model set.")

        # ARCHITECTURE: Подключаем сигнал drag'n'drop для сохранения изменений
        self.treeView.data_changed.connect(self._on_tree_data_changed)

        # Создаем контейнер для дерева
        treeContainer = QVBoxLayout()
        treeContainer.addWidget(self.treeView)
        treeContainer.setContentsMargins(0, 0, 4, 0)
        treeContainer.setSpacing(4)

        # Контекстное меню для дерева
        self.treeView.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeView.customContextMenuRequested.connect(self._show_tree_context_menu)

        pos = QPoint(0, 0)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,  # Тип события (нажатие кнопки мыши)
            pos,                          # Позиция клика (относительно treeView)
            Qt.MouseButton.LeftButton,         # Какая кнопка нажата
            Qt.MouseButton.LeftButton,         # Состояние кнопки
            Qt.KeyboardModifier.NoModifier     # Модификаторы (Ctrl, Shift и т. д.)
        )
        self.treeView.mousePressEvent(event)

        # --- Левая панель: Форма редактирования и кнопки ---
        # Виджет-контейнер для левой панели
        leftPanelWidget = QWidget()
        # Layout для этого виджета-контейнера
        leftPanelLayout = QVBoxLayout(leftPanelWidget) # Устанавливаем layout для leftPanelWidget
        leftPanelLayout.setContentsMargins(4, 0, 4, 4)
        leftPanelLayout.setSpacing(8)

        # Группа для полей редактирования
        self.editorFrame = QGroupBox("Редактирование элемента")
        editorFrameLayout = QVBoxLayout() # Layout для содержимого GroupBox
        editorFrameLayout.setContentsMargins(8, 8, 8, 8)
        editorFrameLayout.setSpacing(8)
        self.editorFrame.setLayout(editorFrameLayout)

        # Поля (как раньше)
        self.nameLabel = QLabel("Имя:")
        self.nameEdit = CustomLineEdit()
        self.descLabel = QLabel("Описание:")
        self.descEdit = CustomLineEdit()
        # self.descEdit.setAcceptRichText(False)
        # self.descEdit.setMaximumHeight(100)
        self.timeoutLabel = QLabel("Таймаут (сек):")
        self.timeoutSpinBox = CustomSpinBox()
        self.timeoutSpinBox.setRange(0, 7200)
        self.timeoutSpinBox.setValue(config.app.ssh.command_timeout)
        self.timeoutSpinBox.setSuffix(" сек")
        
        # Параметры
        paramsLayout = QHBoxLayout()
        paramsLayout.setContentsMargins(4, 4, 4, 4)
        self.paramsLabel = QLabel("Параметры:")
        self.paramsButton = QPushButton("Редактировать...")
        self.paramsButton.clicked.connect(self._edit_params)
        self.paramsButton.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        paramsLayout.addWidget(self.paramsLabel)
        paramsLayout.addStretch(1)  # Добавляем растягивающийся промежуток
        paramsLayout.addWidget(self.paramsButton)
        # Команды
        self.commandsLabel = QLabel("Команды для выполнения:")
        
        self.commandsTable = QTableWidget()
        self.commandsTable.setColumnCount(2)
        self.commandsTable.setHorizontalHeaderLabels(["Тип", "Команда"])
        # Центрируем заголовок и содержимое первой колонки
        self.commandsTable.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.commandsTable.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        # center_delegate = CenterAlignDelegate()
        # self.commandsTable.setItemDelegateForColumn(0, center_delegate)
        self.commandsTable.setToolTip("Двойной клик для редактирования, Del для удаления")
        self.commandsTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.commandsTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.commandsTable.setColumnWidth(0, 50)  # Фиксированная ширина для столбца типа
        self.commandsTable.verticalHeader().setVisible(False)
        self.commandsTable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.commandsTable.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        commandButtonsLayout = QHBoxLayout()
        self.addCmdButton = QPushButton("Добавить SSH-команду")
        self.addSftpButton = QPushButton("Добавить SFTP-команду")
        self.addLocalButton = QPushButton("Добавить локальную команду")
        self.removeCmdButton = QPushButton("Удалить команду")
        commandButtonsLayout.addWidget(self.addCmdButton, stretch=1)
        commandButtonsLayout.addWidget(self.addSftpButton, stretch=1, alignment=Qt.AlignmentFlag.AlignCenter)
        commandButtonsLayout.addWidget(self.addLocalButton, stretch=1, alignment=Qt.AlignmentFlag.AlignRight)
        commandButtonsLayout.addStretch()

        ImportExportButtonsLayout = QHBoxLayout()
        self.ImportButton = QPushButton("Импорт команд")
        self.ExportButton = QPushButton("Экспорт команд")
        ImportExportButtonsLayout.addWidget(self.ImportButton)
        ImportExportButtonsLayout.addWidget(self.ExportButton)
        ImportExportButtonsLayout.addStretch()
        ImportExportButtonsLayout.setStretch(0, 1)
        ImportExportButtonsLayout.setStretch(1, 1)
        
         # Сборка формы редактирования внутри GroupBox
        editorFrameLayout.addWidget(self.nameLabel)
        editorFrameLayout.addWidget(self.nameEdit)
        editorFrameLayout.addWidget(self.descLabel)
        editorFrameLayout.addWidget(self.descEdit)
        editorFrameLayout.addWidget(self.timeoutLabel)
        editorFrameLayout.addWidget(self.timeoutSpinBox)
        editorFrameLayout.addLayout(paramsLayout)
        editorFrameLayout.addWidget(self.commandsLabel)
        editorFrameLayout.addWidget(self.commandsTable, stretch=1)
        editorFrameLayout.addLayout(commandButtonsLayout, stretch=0)
        editorFrameLayout.addWidget(self.removeCmdButton, stretch=0, alignment=Qt.AlignmentFlag.AlignCenter)
        editorFrameLayout.addLayout(ImportExportButtonsLayout)
        editorFrameLayout.addStretch()

        # Кнопки действий для дерева
        treeButtonsLayout = QVBoxLayout() # Отдельный layout для кнопок дерева
        self.saveChangesButton = QPushButton("Сохранить изменения элемента")
        self.saveChangesButton.setVisible(False)    # Скрываем кнопку
        treeButtonsLayout.addWidget(self.saveChangesButton)        
        treeButtonsLayout.addStretch()

        # Добавляем GroupBox и кнопки дерева в layout левой панели
        leftPanelLayout.addWidget(self.editorFrame)
        leftPanelLayout.addLayout(treeButtonsLayout)
        leftPanelLayout.setStretchFactor(self.editorFrame, 1) # editorFrame растягивается

        # --- Сплиттер ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)

        # Создаем виджет для размещения дерева и его кнопок
        treeWidget = QWidget()
        treeWidget.setLayout(treeContainer)

        self.splitter.addWidget(treeWidget)    # Добавляем левую панель (дерево с кнопками)
        self.splitter.addWidget(leftPanelWidget) # Добавляем правую панель (уже с layout'ом)
        self.splitter.setSizes([300, 500]) # Устанавливаем размеры

        # --- Кнопки OK/Cancel ---
        # self.dialogButtonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # self.dialogButtonBox.accepted.connect(self.accept)
        # self.dialogButtonBox.rejected.connect(self.reject)
        self.dialogButtonBox = QHBoxLayout()
        self.dialogButtonBox.setContentsMargins(0, 8, 0, 0)
        self.dialogButtonBox.setSpacing(8)
        self.dialogButtonBox.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.dialogButtonOk = QPushButton("Ok")
        self.dialogButtonCancel = QPushButton("Отмена", default=True)
        self.dialogButtonApply = QPushButton("Применить")


        self.dialogButtonBox.addWidget(self.dialogButtonOk)
        self.dialogButtonBox.addWidget(self.dialogButtonCancel)
        self.dialogButtonBox.addWidget(self.dialogButtonApply)

        self.dialogButtonOk.clicked.connect(self._apply_changes_and_close)
        self.dialogButtonCancel.clicked.connect(self.close)
        self.dialogButtonApply.clicked.connect(self._apply_changes)

        # --- Основной Layout диалога ---
        mainDialogLayout = QVBoxLayout(self) # Устанавливаем QVBoxLayout как основной layout ДИАЛОГА
        mainDialogLayout.setContentsMargins(8, 8, 8, 8)
        mainDialogLayout.setSpacing(8)
        mainDialogLayout.addWidget(self.splitter)        # Добавляем сплиттер
        mainDialogLayout.addLayout(self.dialogButtonBox) # Добавляем кнопки OK/Cancel
        # self.setLayout(mainDialogLayout) # Этот вызов уже сделан при создании QVBoxLayout(self)

        # Connect signals
        self.nameEdit.KeyPressed.connect(self.set_modified)
        self.descEdit.KeyPressed.connect(self.set_modified)
        self.timeoutSpinBox.userValueChanged.connect(self.set_modified)
        self.treeView.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.addCmdButton.clicked.connect(lambda: self._add_command_to_list_of_type("ssh"))
        self.addSftpButton.clicked.connect(lambda: self._add_command_to_list_of_type("sftp"))
        self.addLocalButton.clicked.connect(lambda: self._add_command_to_list_of_type("local"))
        self.removeCmdButton.clicked.connect(self._delete_selected_command)
        self.ImportButton.clicked.connect(self._import_commands)
        self.ExportButton.clicked.connect(self._export_commands)

        # Connect signals
        self.saveChangesButton.clicked.connect(self._save_current_item)

        # Connect table signals
        self.commandsTable.itemDoubleClicked.connect(
            lambda item: self._edit_command(item.row())
        )
        self.commandsTable.keyPressEvent = self.commandsTable.keyPressEvent
        def handle_key(event):
            if event.key() == Qt.Key.Key_Delete:
                self._delete_selected_command()
            else:
                self.commandsTable.keyPressEvent(event)
        self.commandsTable.keyPressEvent = handle_key

        # --- Инициализация состояния ---
        if config.app.expand:
            self.treeView.expandAll() # Раскрываем узлы

        # ARCHITECTURE: Сбрасываем выделение чтобы ничего не было выбрано при открытии
        self.treeView.clearSelection()
        self.treeView.setCurrentIndex(QModelIndex())
        
        # Вызываем обработчик выбора для инициализации полей (с пустым выделением)
        self.blockSignals(True)
        self._on_selection_changed(QModelIndex(), QModelIndex())
        self.blockSignals(False)
        
        # ARCHITECTURE: Сбрасываем modified после инициализации чтобы не показывать диалог без причин
        self.modified = False
        logger.debug("CommandEditorDialog: Initialization complete, no selection, modified flag reset")


    # --- Слоты и методы (без изменений) ---
    @Slot(QModelIndex, QModelIndex)
    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex):
        # Если попытка перейти на тот же элемент — ничего не делать
        if current == previous:
            return

        # Устанавливаем текущее как активное
        self.treeView._current_index = current

        # Получаем данные и флаги
        is_category = self.model.is_category(current)
        item_data = self.model.get_item_data(current)
        item = self.model.itemFromIndex(current)
        can_edit = current.isValid()
        can_edit_command_details = can_edit and not is_category

        # Управление доступностью элементов интерфейса
        self.saveChangesButton.setEnabled(can_edit)
        self.editorFrame.setEnabled(can_edit)
        self.nameEdit.setEnabled(can_edit)
        self.descEdit.setEnabled(can_edit_command_details)
        self.timeoutSpinBox.setEnabled(can_edit_command_details)
        self.commandsTable.setEnabled(can_edit_command_details)
        self.addCmdButton.setEnabled(can_edit_command_details)
        self.addSftpButton.setEnabled(can_edit_command_details)
        self.addLocalButton.setEnabled(can_edit_command_details)
        self.removeCmdButton.setEnabled(can_edit_command_details)
        self.ImportButton.setEnabled(can_edit_command_details)
        self.ExportButton.setEnabled(can_edit_command_details)
        self.paramsButton.setEnabled(can_edit_command_details)

        # Обновляем поля редактора на основе выбранного элемента
        if can_edit:
            self.nameEdit.setText(item.text() if item else "")
            if item_data:
                self.descEdit.setText(item_data.get("description", ""))
                self.timeoutSpinBox.setValue(item_data.get("timeout", config.app.ssh.command_timeout))

                params = item_data.get("params", [])
                self.paramsLabel.setText(f"Параметры: {', '.join(params) if params else ''}")

                # Обновляем таблицу команд
                self.commandsTable.setRowCount(0)
                commands = item_data.get("commands", [])
                for cmd in commands:
                    if isinstance(cmd, dict):
                        cmd_text = cmd.get("text", "")
                        cmd_type = cmd.get("type", "ssh")
                        row_position = self.commandsTable.rowCount()
                        self.commandsTable.insertRow(row_position)
                        type_item = QTableWidgetItem(cmd_type)
                        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        self.commandsTable.setItem(row_position, 0, type_item)
                        self.commandsTable.setItem(row_position, 1, QTableWidgetItem(cmd_text))
                    else:
                        # Legacy format support
                        row_position = self.commandsTable.rowCount()
                        self.commandsTable.insertRow(row_position)
                        type_item = QTableWidgetItem("ssh")
                        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        self.commandsTable.setItem(row_position, 0, type_item)
                        self.commandsTable.setItem(row_position, 1, QTableWidgetItem(str(cmd)))
            else:
                self.descEdit.clear()
                self.timeoutSpinBox.setValue(config.app.ssh.command_timeout)
                self.commandsTable.setRowCount(0)
        else:
            self.nameEdit.clear()
            self.descEdit.clear()
            self.timeoutSpinBox.setValue(config.app.ssh.command_timeout)
            self.commandsTable.setRowCount(0)

        # Создаём бэкап для текущего выбранного элемента (для возможного отката)
        if current.isValid():
            if item_data is not None:
                self._backup_data = copy.deepcopy(item_data)
            else:
                self._backup_data = None
            self._backup_index = current
        else:
            self._backup_data = None
            self._backup_index = QModelIndex()

    def _get_current_or_root_index(self) -> QModelIndex:
        current_index = self.treeView.currentIndex()
        # logger.debug(f"_get_current_or_root_index: current_index.isValid() = {current_index.isValid()}") # Debug
        if current_index.isValid():
            # Если выбран элемент, определяем, куда добавлять:
            # - В родителя, если выбрана команда
            # - В саму категорию, если выбрана категория
            if not self.model.is_category(current_index):
                # logger.debug("  -> Adding to parent of selected command") # Debug
                return current_index.parent()
            else:
                # logger.debug("  -> Adding to selected category") # Debug
                return current_index
        else:
            # Если НИЧЕГО не выбрано (current_index невалиден), добавляем в корень
            # logger.debug("  -> Adding to root") # Debug
            return QModelIndex() # Возвращаем невалидный индекс для корня

    # --- Контекстное меню дерева ---
    def _show_tree_context_menu(self, position: QPoint):
        """Показать контекстное меню для элемента дерева."""
        index = self.treeView.indexAt(position)
        menu = QMenu(self)

        add_category_action = menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Добавить категорию")
        add_command_action = menu.addAction(QIcon(ICONS.get('command', '')), "Добавить команду")
        menu.addSeparator()
        delete_action = menu.addAction(QIcon(ICONS.get('menu_delete', '')), "Удалить элемент")
        delete_action.setEnabled(index.isValid())

        action = menu.exec(self.treeView.viewport().mapToGlobal(position))
        if action == add_category_action:
            self._add_category()
        elif action == add_command_action:
            self._add_command()
        elif action == delete_action:
            self._delete_item()

    def _add_category(self):
        parent_index = self._get_current_or_root_index()
        name, ok = QInputDialog.getText(self, "Добавить категорию", "Введите имя новой категории:")
        if ok and name:
            category_data = {name: []}
            if self.model.add_item(parent_index, category_data, is_category=True):
                self.treeView.expand(parent_index)
                self.set_modified()
        elif ok and not name: QMessageBox.warning(self, "Ошибка", "Имя категории не может быть пустым.")

    def _add_command(self):
        """Add new command to tree"""
        parent_index = self._get_current_or_root_index()
        # Проверка: нельзя добавлять команду в команду
        if parent_index.isValid() and not self.model.is_category(parent_index):
             QMessageBox.warning(self, "Ошибка", "Команду можно добавить только в категорию или в корень дерева.")
             return
        name, ok = QInputDialog.getText(self, "Добавить команду", "Введите имя новой команды:")
        if ok and name:
            command_data = self.model.default_command.copy()
            command_data["name"] = name
            command_data["commands"] = []
            if self.model.add_item(parent_index, command_data, is_category=False):
                self.treeView.expand(parent_index)
                self._update_editor_from_selection()
                self.set_modified()
        elif ok and not name:
            QMessageBox.warning(self, "Ошибка", "Имя команды не может быть пустым.")

    def _delete_item(self):
        current_index = self.treeView.currentIndex()
        if not current_index.isValid(): 
            QMessageBox.warning(self, "Ошибка", "Сначала выберите элемент для удаления.")
            return
        
        item_name = self.model.data(current_index, Qt.DisplayRole)
        reply = QMessageBox.question(
            self, 
            "Подтверждение удаления", 
            f"Вы уверены, что хотите удалить '{item_name}'?", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.model.remove_item(current_index):
                # ARCHITECTURE: Не вызываем _on_selection_changed здесь, 
                # чтобы не триггерить диалог сохранения
                # modified будет установлен при следующем изменении
                self.modified = True
                logger.debug("CommandEditorDialog: Item deleted, marked as modified")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось удалить элемент.")


    def _apply_changes(self, current_index: QModelIndex = QModelIndex()):
        """Применить изменения и сохранить через сервис"""
        if self.modified:
            self._save_current_item(current_index)
            new_data = self.get_data()
            
            # ARCHITECTURE: Используем сервис для сохранения
            success = self.command_service.save_commands(new_data)
            
            if success:
                # Сохраняем в файл через сервис
                export_success, message = self.command_service.export_commands_to_file(DEFAULT_COMMANDS_FILE)
                
                if export_success:
                    self.modified = False
                    logger.info(f"CommandEditorDialog: Commands saved successfully")
                else:
                    QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить в файл: {message}")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось сохранить команды. Проверьте валидность данных.")

    def _on_tree_data_changed(self):
        """Обработчик изменения данных дерева (drag'n'drop)"""
        logger.debug("CommandEditorDialog: Tree data changed via drag'n'drop")
        self.modified = True


    def _apply_changes_and_close(self):
        self._apply_changes()
        self.accept()


    def _save_item_changes(self):
        current_index = self.treeView.currentIndex()
        if not current_index.isValid():
            QMessageBox.warning(self, "Ошибка", "Сначала выберите элемент для сохранения изменений.")
            return
            
        new_name = self.nameEdit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Ошибка", "Имя элемента не может быть пустым.")
            return
            
        is_category = self.model.is_category(current_index)
        if is_category:
            # Для категории: получаем текущих детей ПЕРЕД обновлением
            children_data = self.model._build_recursive_structure(current_index)
            updated_data = {new_name: children_data}
            if self.model.update_item(current_index, updated_data):
                QMessageBox.information(self, "Сохранено", f"Имя категории '{new_name}' обновлено.")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось сохранить имя категории.")
        else: # Команда
            item_data = self.model.get_item_data(current_index).copy()  # Create a copy
            if item_data:
                item_data["name"] = new_name
                item_data["description"] = self.descEdit.text()
                item_data["timeout"] = self.timeoutSpinBox.value()
                
                # Get commands from list widget
                commands = []
                for row in range(self.commandsTable.rowCount()):
                    cmd_type = self.commandsTable.item(row, 0).text()
                    cmd_text = self.commandsTable.item(row, 1).text()
                    commands.append({"type": cmd_type, "text": cmd_text})
                    
                item_data["commands"] = commands
                
                self.model.update_item(current_index, item_data)


    def _get_current_params(self) -> list:
        """Получить параметры текущей команды"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return []
        item_data = self.model.get_item_data(current_item)
        if not item_data:
            return []
        return item_data.get("params", [])

    def _export_commands(self):
        """Экспорт полной команды со всей информацией (кроме имени)"""
        current_item = self._get_selected_command_item()
        if not current_item:
            QMessageBox.warning(self, "Предупреждение", "Сначала выберите команду для экспорта")
            return

        item_data = self.model.get_item_data(current_item)
        if not item_data:
            QMessageBox.warning(self, "Предупреждение", "Не удалось получить данные команды")
            return

        # Копируем данные без имени
        export_data = copy.deepcopy(item_data)
        export_data.pop("name", None)

        dlg = ExportCommandDlg(self, export_data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pass

    def _import_commands(self):
        """
        Импорт команд.
        Автоматически определяет формат:
        - Полная команда: обновляет текущую выбранную команду (кроме имени)
        - Список команд: добавляет команды в текущую задачу
        """
        dlg = ImportCommandDlg(self)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                result = dlg.get_decoded_command()

                if result.get("error"):
                    QMessageBox.critical(self, "Ошибка", result["error"])
                    return

                data = result["data"]
                is_full_command = result["is_full_command"]

                if is_full_command:
                    # Импорт полной команды - обновляем текущую выбранную команду
                    current_item = self._get_selected_command_item()
                    if not current_item:
                        QMessageBox.warning(self, "Предупреждение", "Сначала выберите команду для обновления")
                        return

                    # Получаем текущие данные и сохраняем имя
                    current_data = self.model.get_item_data(current_item)
                    if not current_data:
                        return

                    # Обновляем все поля кроме имени
                    data["name"] = current_data["name"]

                    # Обновляем модель
                    self.model.update_item(current_item, data)
                    self._update_editor_from_selection()
                    self.set_modified()
                    QMessageBox.information(
                        self, "Успех",
                        f"Команда успешно обновлена"
                    )
                else:
                    # Импорт списка команд - добавляем в текущую задачу
                    current_item = self._get_selected_command_item()
                    if not current_item:
                        QMessageBox.warning(self, "Предупреждение", "Сначала выберите команду для импорта")
                        return

                    item_data = self.model.get_item_data(current_item).copy()
                    if not item_data:
                        return

                    if "commands" not in item_data:
                        item_data["commands"] = []

                    # Добавляем импортированные команды
                    if isinstance(data, list):
                        for cmd in data:
                            if isinstance(cmd, dict) and "type" in cmd and "text" in cmd:
                                item_data["commands"].append({
                                    "type": cmd["type"].lower(),
                                    "text": cmd["text"]
                                })

                    self.model.update_item(current_item, item_data)
                    self._update_editor_from_selection()
                    self.set_modified()
                    QMessageBox.information(self, "Успех", "Команды успешно импортированы")

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать команды: {e}")
            logger.error(e)

    def _remove_command_from_list(self):
        """Remove selected commands"""
        selected_items = self.commandsTable.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Ошибка", "Сначала выберите команду в списке для удаления.")
            return

        current = self._get_selected_command_item()
        if not current:
            return

        item_data = self.model.get_item_data(current).copy()  # Create a copy
        if not item_data or "commands" not in item_data:
            return

        # Remove commands in reverse order
        rows = sorted(set(item.row() for item in selected_items), reverse=True)
        commands = item_data["commands"].copy()  # Create a copy of commands list
        for row in rows:
            if 0 <= row < len(commands):
                commands.pop(row)
        
        item_data["commands"] = commands
        self.model.update_item(current, item_data)
        self._update_editor_from_selection()

        self.modified = True

    def _edit_command_in_list(self, item: QTableWidgetItem):
        """Edit selected command"""
        current = self._get_selected_command_item()
        if not current:
            return

        item_data = self.model.get_item_data(current)
        if not item_data or "commands" not in item_data:
            return

        row = self.commandsTable.row(item)
        if not 0 <= row < len(item_data["commands"]):
            return

        command = item_data["commands"][row].copy()  # Create a copy
        cmd_type = command.get("type", "ssh")
        cmd_text = command.get("text", "")

        dlg = None
        try:
            if cmd_type == "sftp":
                dlg = SFTPCommandEditorDialog(self, cmd_text)
            else:
                dlg = BashEditorDialog(self, cmd_text, self._get_current_params())
                if cmd_type == "local":
                    dlg.setWindowTitle("Команды на локальном компьютере")

            if dlg.exec() == QDialog.DialogCode.Accepted:
                command["text"] = dlg.getText()
                item_data = item_data.copy()  # Create a copy of item_data
                item_data["commands"] = item_data["commands"].copy()  # Create a copy of commands list
                item_data["commands"][row] = command  # Replace with updated command
                self.model.update_item(current, item_data)
                self._update_editor_from_selection()
        finally:
            if dlg:
                dlg.deleteLater()

        self.modified = True

    def _commands_list_key_press(self, event):
        if event.key() == Qt.Key.Key_Delete: self._remove_command_from_list()
        else: QTableWidget.keyPressEvent(self.commandsTable, event)

    def get_data(self) -> list | None:
        return self.model.get_data_structure()

    def _edit_params(self):
        """Show dialog for editing command parameters"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        item_data = self.model.get_item_data(current_item)
        if not item_data:
            return
            
        params = item_data.get("params", [])
        dlg = ParamsInputDialog(params, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item_data["params"] = dlg.get_params()
            self.model.update_item(current_item, item_data)


    def _add_command_to_list_of_type(self, cmd_type: str):
        """Add a new command of specified type"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        item_data = self.model.get_item_data(current_item)
        if not item_data:
            return        # Create empty command of specified type
        new_cmd = {"type": cmd_type, "text": ""}
        
        if cmd_type == "ssh":
            dlg = BashEditorDialog(self, "", self._get_current_params())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_cmd["text"] = dlg.getText()
            else:
                return
        elif cmd_type == "sftp":
            dlg = SFTPCommandEditorDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_cmd["text"] = dlg.getText()
            else:
                return
        elif cmd_type == "local":
            dlg = BashEditorDialog(self, "", self._get_current_params())
            dlg.setWindowTitle("Local Command Editor")
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_cmd["text"] = dlg.getText()
            else:
                return
        
        # Add new command to item's commands list
        if "commands" not in item_data:
            item_data["commands"] = []
        item_data["commands"].append(new_cmd)
        
        # Update model and view
        self.model.update_item(current_item, item_data)
        self._update_editor_from_selection()

        self.set_modified()

    def _edit_command(self, row: int):
        """Edit command at specified row"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        item_data = self.model.get_item_data(current_item)
        if not item_data or "commands" not in item_data:
            return
            
        commands = item_data["commands"]
        if not 0 <= row < len(commands):
            return
            
        cmd = commands[row]
        cmd_type = cmd.get("type", "ssh")
        cmd_text = cmd.get("text", "")
        
        if cmd_type == "ssh":
            dlg = BashEditorDialog(self, cmd_text, self._get_current_params())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                commands[row]["text"] = dlg.getText()
        elif cmd_type == "sftp":
            dlg = SFTPCommandEditorDialog(self, cmd_text)  # Pass initial_text
            if dlg.exec() == QDialog.DialogCode.Accepted:
                commands[row]["text"] = dlg.getText()  # Use getText consistently
        elif cmd_type == "local":
            dlg = BashEditorDialog(self, cmd_text, self._get_current_params())
            dlg.setWindowTitle("Local Command Editor")
            if dlg.exec() == QDialog.DialogCode.Accepted:
                commands[row]["text"] = dlg.getText()
        
        # Update model and view
        self.model.update_item(current_item, item_data)
        self._update_editor_from_selection()

        self.set_modified()

    def _update_editor_from_selection(self):
        """Update editor fields and command list from selected item"""
        self.commandsTable.setRowCount(0)
        
        current = self.treeView.currentIndex()
        is_category = self.model.is_category(current)
        item = self.model.itemFromIndex(current)
        item_data = self.model.get_item_data(current)
        
        can_edit = current.isValid()
        can_edit_command_details = can_edit and not is_category
        
        # Update field states
        self.nameEdit.setEnabled(can_edit)
        self.descEdit.setEnabled(can_edit_command_details)
        self.timeoutSpinBox.setEnabled(can_edit_command_details)
        self.paramsButton.setEnabled(can_edit_command_details)
        self.addCmdButton.setEnabled(can_edit_command_details)
        self.addSftpButton.setEnabled(can_edit_command_details)
        self.addLocalButton.setEnabled(can_edit_command_details)
        self.removeCmdButton.setEnabled(can_edit_command_details)
        self.commandsTable.setEnabled(can_edit_command_details)
        
        if can_edit:
            self.nameEdit.setText(item.text() if item else "")

            if item_data and can_edit_command_details:
                self.descEdit.setText(item_data.get("description", ""))
                self.timeoutSpinBox.setValue(item_data.get("timeout", config.app.ssh.command_timeout))

                # Обновляем лейбл с параметрами
                params = item_data.get("params", [])
                self.paramsLabel.setText(f"Параметры: {', '.join(params) if params else ''}")

                # Обновляем таблицу команд
                commands = item_data.get("commands", [])
                self.commandsTable.setRowCount(len(commands))
                for row, cmd in enumerate(commands):
                    if isinstance(cmd, dict):
                        cmd_type = cmd.get("type", "ssh")
                        cmd_text = cmd.get("text", "")
                        type_item = QTableWidgetItem(cmd_type)
                        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        text_item = QTableWidgetItem(cmd_text)

                        self.commandsTable.setItem(row, 0, type_item)
                        self.commandsTable.setItem(row, 1, text_item)
            else:
                self.descEdit.clear()
                self.timeoutSpinBox.setValue(config.app.ssh.command_timeout)
                self.paramsLabel.setText("Параметры:")
        else:
            self.nameEdit.clear()
            self.descEdit.clear()
            self.timeoutSpinBox.setValue(config.app.ssh.command_timeout)
            self.paramsLabel.setText("Параметры:")

    def _edit_params(self):
        """Show dialog for editing command parameters"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        # Get current parameters from item data
        item_data = self.model.get_item_data(current_item)
        if not item_data:
            return
            
        params = item_data.get("params", [])
        dlg = ParamsInputDialog(params, self)
        dlg.result_ready.connect(self._update_params)
        dlg.exec()

    def _update_params(self, new_params):
        """Update parameters after editing"""
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        item_data = self.model.get_item_data(current_item)
        if not item_data:
            return
            
        item_data["params"] = new_params
        self.model.update_item(current_item, item_data)
        self._update_editor_from_selection()

    def _delete_selected_command(self):
        """Delete currently selected command from the list"""
        if QMessageBox.question(self, "Удаление команды", "Действительно удалить команду?", 
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        
        current_row = self.commandsTable.currentRow()
        if current_row < 0:
            return
            
        current_item = self._get_selected_command_item()
        if not current_item:
            return
            
        item_data = self.model.get_item_data(current_item).copy()  # Create a copy
        if not item_data or "commands" not in item_data:
            return
            
        # Remove command and update model
        if 0 <= current_row < len(item_data["commands"]):
            commands = item_data["commands"].copy()  # Create a copy of commands list
            commands.pop(current_row)
            item_data["commands"] = commands
            self.model.update_item(current_item, item_data)
            self._update_editor_from_selection()

        self.set_modified()

    def _get_selected_command_item(self) -> QModelIndex:
        """Get currently selected command item"""
        current = self.treeView.currentIndex()
        if not current.isValid(): #or self.model.is_category(current):
            return None
        return current

    def _save_current_item(self, current: QModelIndex = None):
        """Save changes to currently selected item"""
        if not current:
            current = self.treeView.currentIndex()

        item = self.model.itemFromIndex(current)
        if not item:
            return

        if not current.isValid():
            return
            
        if self.model.is_category(current):
            # For category, only name can be changed
            new_name = self.nameEdit.text().strip()
            if not new_name:
                return
                
            # Find children and create new category data
            children = []
            for row in range(self.model.rowCount(current)):
                child_index = self.model.index(row, 0, current)
                child_data = self.model.get_item_data(child_index)
                if child_data:
                    children.append(child_data)
            
            new_data = {new_name: children}
            self.model.update_item(current, new_data)
        else:
            # For command, update all fields
            item_data = self.model.get_item_data(current)
            if not item_data:
                return
                
            item_data["name"] = self.nameEdit.text().strip()
            item_data["description"] = self.descEdit.text().strip()
            item_data["timeout"] = self.timeoutSpinBox.value()
            
            self.model.update_item(current, item_data)

        self.modified = False

        QMessageBox.information(self, "Сохранено", "Изменения успешно сохранены.")

    def _get_selected_item_data(self):
        """Get data from currently selected item"""
        current = self.treeView.currentIndex()
        if not current.isValid():
            return None
        return self.model.get_item_data(current)
    
    def set_modified(self, force: bool = False):
        """Пометить данные как измененные."""
        if self.modified and not force:
            return
        self.modified = True


    def CloseQuery(self) -> bool:
        """
        Запрос на закрытие диалога с сохранением/отменой изменений.

        Returns:
            True если можно закрыть, False если отменено пользователем
        """
        if self.modified:
            result = QMessageBox.question(
                self,
                "Предупреждение",
                "Сохранить изменения?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            result = QMessageBox.StandardButton(result)

            if result == QMessageBox.StandardButton.Yes:
                # ✅ Сохранить изменения
                self._apply_changes()
                if self.modified:
                    # Если все еще modified - ошибка сохранения
                    return False
                self.accept()
                return True

            elif result == QMessageBox.StandardButton.No:
                # ❌ Не сохранять - закрываем без сохранения
                logger.info("CommandEditorDialog: Discarding changes")
                self.reject()
                return True

            elif result == QMessageBox.StandardButton.Cancel:
                # ⛔ Отмена закрытия - остаемся в диалоге
                logger.debug("CommandEditorDialog: Close cancelled by user")
                return False

        # Если нет изменений - закрываем
        return True



    def closeEvent(self, event):
        if not self.CloseQuery():
            event.ignore()
            return
    
        super().closeEvent(event)