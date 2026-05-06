"""
SFTP Dialog - файловый менеджер для обмена файлами через SFTP.

Модуль предоставляет диалоговое окно для работы с удалёнными файлами
через SFTP-соединение с поддержкой:
- Двухпанельного интерфейса (локальные/удалённые файлы)
- Рекурсивного копирования файлов и папок
- Просмотра файлов с подсветкой синтаксиса
- Управления правами доступа
- Асинхронных операций с прогресс-баром
"""

import os
import stat
import pathlib
import platform
from datetime import datetime
from pathlib import PurePosixPath
from typing import Optional, Tuple
from stat import S_ISDIR, S_IMODE

import paramiko
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QMessageBox, QAbstractItemView, QInputDialog,
    QTreeView, QSplitter, QTextEdit, QMenu, QWidget, QProgressBar,
    QHBoxLayout, QLabel, QApplication, QFileDialog, QHeaderView, QPushButton,
    QStatusBar, QSizePolicy, QComboBox, QLineEdit
)
from PySide6.QtCore import Qt, QThread, Signal, QSortFilterProxyModel, Slot
from PySide6.QtGui import QStandardItemModel, QStandardItem, QIcon, QCloseEvent

from src.config import config, ICONS, DEFAULT_SSH_PRIVATE_KEY_PATH
from src.logger import logger
from src.domain.models.device import DeviceModel
from src.ui.dialogs.editors.remote_file_info import RemoteFileInfoDialog
from src.ui.dialogs.editors.remote_file_preview import RemoteFilePreviewDialog
from src.workers.command.executor_base import get_credentials


# =============================================================================
# Модели сортировки
# =============================================================================

class NameSortProxyModel(QSortFilterProxyModel):
    """Модель сортировки с поддержкой родительского каталога (..)"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sort_order = Qt.SortOrder.AscendingOrder

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):
        self._sort_order = order
        super().sort(column, order)

    def lessThan(self, left, right):
        src = self.sourceModel()
        left_name_index = src.index(left.row(), 0)
        right_name_index = src.index(right.row(), 0)

        left_is_parent = bool(src.data(left_name_index, Qt.UserRole + 1))
        right_is_parent = bool(src.data(right_name_index, Qt.UserRole + 1))

        if left_is_parent and not right_is_parent:
            return True if self._sort_order == Qt.SortOrder.AscendingOrder else False
        if right_is_parent and not left_is_parent:
            return False if self._sort_order == Qt.SortOrder.AscendingOrder else True

        left_is_dir = bool(src.data(left_name_index, Qt.UserRole + 2))
        right_is_dir = bool(src.data(right_name_index, Qt.UserRole + 2))
        if left_is_dir and not right_is_dir:
            return True
        if right_is_dir and not left_is_dir:
            return False

        col = left.column()
        left_index = src.index(left.row(), col)
        right_index = src.index(right.row(), col)

        ldata = src.data(left_index, Qt.DisplayRole) or ""
        rdata = src.data(right_index, Qt.DisplayRole) or ""

        if col == 1:
            ls = getattr(src.item(left.row(), 0), "file_size", None) if hasattr(src, "item") else None
            rs = getattr(src.item(right.row(), 0), "file_size", None) if hasattr(src, "item") else None
            try:
                if ls is not None and rs is not None:
                    return float(ls) < float(rs)
            except Exception:
                pass

        try:
            return str(ldata).lower() < str(rdata).lower()
        except Exception:
            return str(ldata) < str(rdata)


# =============================================================================
# Элементы модели
# =============================================================================

class FileItem(QStandardItem):
    """Элемент файла/папки в модели"""
    
    def __init__(self, name, size=0, date=None, permissions="", is_dir=False, is_parent_dir=False):
        super().__init__(name)
        self.setData(is_dir, Qt.UserRole + 2)
        self.setData(is_parent_dir, Qt.UserRole + 1)
        self.file_size = size
        self.file_date = date
        self.file_permissions = permissions
        self.setEditable(False)


# =============================================================================
# Утилиты
# =============================================================================

def format_size(size: int) -> str:
    """Форматирует размер файла в человеко-читаемый вид"""
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ', 'ПБ']:
        if size < 1024:
            return f"{size} {unit}" if unit == 'Б' else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} ЭБ"


# =============================================================================
# Воркеры для асинхронных операций
# =============================================================================

class FileTransferWorker(QThread):
    """Воркер для асинхронной передачи файлов"""
    
    progress = Signal(int, str)  # progress_percent, status_message
    finished = Signal(bool, str)  # success, message
    log_message = Signal(str)
    
    def __init__(self, sftp, operation: str, source: str, dest: str, 
                 is_recursive: bool = False, parent=None):
        super().__init__(parent)
        self.sftp = sftp
        self.operation = operation  # 'get', 'put', 'mkdir'
        self.source = source
        self.dest = dest
        self.is_recursive = is_recursive
        self._cancelled = False
    
    def run(self):
        try:
            if self.operation == 'get':
                self._download_file()
            elif self.operation == 'put':
                self._upload_file()
            elif self.operation == 'mkdir':
                self._create_directory()
            self.finished.emit(True, "Операция завершена успешно")
        except Exception as e:
            logger.error(f"Ошибка в FileTransferWorker: {e}")
            self.finished.emit(False, str(e))
    
    def _download_file(self):
        """Скачивание файла с прогрессом"""
        self._last_progress = 0
        
        def callback(current, total):
            if not self._cancelled:
                # Ограничиваем частоту обновлений (каждые 5%)
                percent = int((current / total) * 100) if total > 0 else 0
                if percent - self._last_progress >= 5 or percent == 100:
                    self._last_progress = percent
                    self.progress.emit(percent, f"Загружено {current}/{total} байт")

        self.log_message.emit(f"Начато скачивание: {self.source} → {self.dest}")
        self.sftp.get(self.source, self.dest, callback=callback)
        self.log_message.emit(f"Файл скачан: {self.dest}")

    def _upload_file(self):
        """Загрузка файла с прогрессом"""
        self._last_progress = 0
        
        def callback(current, total):
            if not self._cancelled:
                # Ограничиваем частоту обновлений (каждые 5%)
                percent = int((current / total) * 100) if total > 0 else 0
                if percent - self._last_progress >= 5 or percent == 100:
                    self._last_progress = percent
                    self.progress.emit(percent, f"Загружено {current}/{total} байт")

        self.log_message.emit(f"Начата загрузка: {self.source} → {self.dest}")
        self.sftp.put(self.source, self.dest, callback=callback)
        self.log_message.emit(f"Файл загружен: {self.dest}")
    
    def _create_directory(self):
        """Создание директории"""
        self.log_message.emit(f"Создание директории: {self.dest}")
        try:
            self.sftp.mkdir(self.dest)
        except FileExistsError:
            pass  # Директория уже существует
    
    def cancel(self):
        self._cancelled = True


class RecursiveTransferWorker(QThread):
    """Воркер для рекурсивной передачи папок"""
    
    progress = Signal(int, str)
    finished = Signal(bool, str)
    log_message = Signal(str)
    
    def __init__(self, sftp, operation: str, source: str, dest: str, parent=None):
        super().__init__(parent)
        self.sftp = sftp
        self.operation = operation  # 'get' или 'put'
        self.source = source
        self.dest = dest
        self._cancelled = False
        self._processed = 0
        self._total = 0
    
    def run(self):
        try:
            if self.operation == 'get':
                self._download_recursive(self.source, self.dest)
            else:
                self._upload_recursive(self.source, self.dest)
            self.finished.emit(True, "Операция завершена успешно")
        except Exception as e:
            logger.error(f"Ошибка в RecursiveTransferWorker: {e}")
            self.finished.emit(False, str(e))
    
    def _count_items(self, path: str, is_remote: bool) -> int:
        """Подсчёт количества элементов для прогресс-бара"""
        count = 1
        try:
            if is_remote:
                for entry in self.sftp.listdir_attr(path):
                    if S_ISDIR(entry.st_mode):
                        count += self._count_items(
                            str(PurePosixPath(path).joinpath(entry.filename)), True
                        )
                    else:
                        count += 1
            else:
                for entry in os.scandir(path):
                    if entry.is_dir():
                        count += self._count_items(entry.path, False)
                    else:
                        count += 1
        except Exception:
            pass
        return count
    
    def _download_recursive(self, remote_path: str, local_path: str):
        """Рекурсивное скачивание папки"""
        if not os.path.exists(local_path):
            os.makedirs(local_path)
            self.log_message.emit(f"Создана локальная папка: {local_path}")
        
        for entry in self.sftp.listdir_attr(remote_path):
            if self._cancelled:
                break
            
            entry_name = entry.filename
            remote_entry_path = str(PurePosixPath(remote_path).joinpath(entry_name))
            local_entry_path = os.path.join(local_path, entry_name)
            
            if S_ISDIR(entry.st_mode):
                self._download_recursive(remote_entry_path, local_entry_path)
            else:
                try:
                    self.sftp.get(remote_entry_path, local_entry_path)
                    self._processed += 1
                    self.progress.emit(
                        int((self._processed / max(self._total, 1)) * 100),
                        f"Обработано {self._processed}/{self._total}"
                    )
                    self.log_message.emit(f"Скачан файл: {remote_entry_path}")
                except Exception as e:
                    self.log_message.emit(f"Ошибка скачивания {remote_entry_path}: {e}")
    
    def _upload_recursive(self, local_path: str, remote_path: str):
        """Рекурсивная загрузка папки"""
        try:
            self.sftp.mkdir(remote_path)
        except FileExistsError:
            pass
        
        self.log_message.emit(f"Создана удалённая папка: {remote_path}")
        
        for entry in os.scandir(local_path):
            if self._cancelled:
                break
            
            entry_name = entry.name
            local_entry_path = entry.path
            remote_entry_path = str(PurePosixPath(remote_path).joinpath(entry_name))
            
            if entry.is_dir():
                self._upload_recursive(local_entry_path, remote_entry_path)
            else:
                try:
                    self.sftp.put(local_entry_path, remote_entry_path)
                    self._processed += 1
                    self.progress.emit(
                        int((self._processed / max(self._total, 1)) * 100),
                        f"Обработано {self._processed}/{self._total}"
                    )
                    self.log_message.emit(f"Загружен файл: {remote_entry_path}")
                except Exception as e:
                    self.log_message.emit(f"Ошибка загрузки {remote_entry_path}: {e}")
    
    def cancel(self):
        self._cancelled = True


# =============================================================================
# Основной диалог
# =============================================================================

class SFTPDialog(QDialog):
    """Диалоговое окно SFTP для обмена файлами"""
    
    LARGE_FILE_THRESHOLD = 100 * 1024 * 1024  # 100 МБ
    PREVIEW_MAX_SIZE = 1024 * 1024  # 1 МБ
    
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.ssh: Optional[paramiko.SSHClient] = None
        self.current_local_path = os.path.expanduser("~")

        # Получаем учётные данные с SSH ключом (для диалога)
        creds = get_credentials(device, use_key=True)
        self.current_remote_path = (
            '/root' if creds.username == 'root'
            else str(PurePosixPath("/home").joinpath(creds.username))
        )
        
        self.folder_icon = QIcon(ICONS['folder'])
        self._transfer_worker: Optional[QThread] = None
        
        self.setWindowTitle("SFTP Передача файлов")
        self.resize(880, 690)
        self.setup_ui()
        self.connect_to_remote()

    def setup_ui(self):
        """Создание пользовательского интерфейса"""
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.panes_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Устанавливаем политику растягивания для splitter
        self.splitter.setChildrenCollapsible(False)
        self.panes_splitter.setChildrenCollapsible(False)

        # Левая панель (локальная)
        left_widget = self._create_local_panel()
        self.panes_splitter.addWidget(left_widget)

        # Правая панель (удалённая)
        right_widget = self._create_remote_panel()
        self.panes_splitter.addWidget(right_widget)
        self.splitter.addWidget(self.panes_splitter)

        # Лог операций
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(200)
        self.splitter.addWidget(self.log_edit)
        self.splitter.setStretchFactor(0, 8)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self.splitter)

        # Статус-бар с прогрессом
        self._setup_status_bar(layout)

        self.setLayout(layout)
        
        # Устанавливаем размеры splitter по умолчанию (50/50)
        self.splitter.setSizes([self.height() // 2])
        # Растягиваем панели поровну
        total_width = self.width() // 2
        self.panes_splitter.setSizes([total_width, total_width])

        self.refresh_local_files()
        # Применяем настройки заголовков после инициализации
        self._setup_local_header()

    def _create_local_panel(self) -> QWidget:
        """Создание левой панели с локальными файлами"""
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_widget = QWidget()

        # Устанавливаем политику размера для растягивания
        left_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        # Панель выбора диска + адрес (только Windows)
        self.drive_combo = None
        if platform.system().lower() == 'windows':
            path_toolbar = QHBoxLayout()
            path_toolbar.setContentsMargins(4, 2, 4, 2)
            path_toolbar.setSpacing(4)

            drive_label = QLabel("Диск:")
            path_toolbar.addWidget(drive_label)

            self.drive_combo = QComboBox()
            self.drive_combo.setMinimumWidth(80)
            self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
            path_toolbar.addWidget(self.drive_combo)

            self.local_path_edit = QLineEdit()
            self.local_path_edit.setReadOnly(True)
            path_toolbar.addWidget(self.local_path_edit, 1)

            left_panel.addLayout(path_toolbar)
            self._populate_drives()
        else:
            self.local_path_edit = QLineEdit()
            self.local_path_edit.setReadOnly(True)
            left_panel.addWidget(self.local_path_edit)

        self.local_path_edit.setText(self.current_local_path)
        
        self.local_model = QStandardItemModel()
        self.local_model.setHorizontalHeaderLabels(["Имя", "Размер", "Дата", "Права"])
        self.local_proxy_model = NameSortProxyModel()
        self.local_proxy_model.setSourceModel(self.local_model)
        
        self.local_view = QTreeView()
        self.local_view.setModel(self.local_proxy_model)
        self.local_view.setSortingEnabled(True)
        self.local_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.local_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.local_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.local_view.setUniformRowHeights(True)
        self.local_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        header = self.local_view.header()
        header.setSortIndicatorShown(True)

        self.local_view.doubleClicked.connect(self.on_local_double_clicked)
        self.local_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_view.customContextMenuRequested.connect(self.on_local_context_menu)
        left_panel.addWidget(self.local_view)

        left_widget.setLayout(left_panel)
        return left_widget

    def _create_remote_panel(self) -> QWidget:
        """Создание правой панели с удалёнными файлами"""
        right_panel = QVBoxLayout()
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_widget = QWidget()
        
        # Устанавливаем политику размера для растягивания
        right_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, 
            QSizePolicy.Policy.Expanding
        )
        
        self.remote_path_edit = QTextEdit()
        self.remote_path_edit.setReadOnly(True)
        self.remote_path_edit.setMaximumHeight(30)
        self.remote_path_edit.setText(self.current_remote_path)
        right_panel.addWidget(self.remote_path_edit)
        
        self.remote_model = QStandardItemModel()
        self.remote_model.setHorizontalHeaderLabels(["Имя", "Размер", "Дата", "Права"])
        self.remote_proxy_model = NameSortProxyModel()
        self.remote_proxy_model.setSourceModel(self.remote_model)
        
        self.remote_view = QTreeView()
        self.remote_view.setModel(self.remote_proxy_model)
        self.remote_view.setSortingEnabled(True)
        self.remote_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.remote_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.remote_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.remote_view.setUniformRowHeights(True)
        self.remote_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        header2 = self.remote_view.header()
        header2.setSortIndicatorShown(True)

        self.remote_view.doubleClicked.connect(self.on_remote_double_clicked)
        self.remote_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_view.customContextMenuRequested.connect(self.on_remote_context_menu)
        right_panel.addWidget(self.remote_view)

        right_widget.setLayout(right_panel)
        return right_widget

    def _setup_status_bar(self, layout: QVBoxLayout):
        """Создание статус-бара с прогрессом"""
        self.statusbar = QStatusBar()
        self.statusbar.setSizeGripEnabled(False)
        
        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(200)
        
        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 100)
        self.status_progress.setMinimumWidth(300)
        self.status_progress.setMaximumWidth(500)
        self.status_progress.setTextVisible(True)
        self.status_progress.setVisible(False)
        
        self.status_cancel_btn = QPushButton("Отмена")
        self.status_cancel_btn.setVisible(False)
        self.status_cancel_btn.clicked.connect(self._cancel_transfer)
        
        self.statusbar.addWidget(self.status_label, 1)
        self.statusbar.addPermanentWidget(self.status_progress, 0)
        self.statusbar.addPermanentWidget(self.status_cancel_btn, 0)
        
        layout.addWidget(self.statusbar)

    def _populate_drives(self):
        """Заполнить ComboBox доступными дисками (Windows)"""
        if self.drive_combo is None:
            return

        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()

        # Получаем список доступных дисков Windows
        import string
        import ctypes
        drives = []
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_letter = f"{letter}:\\"
                if os.path.exists(drive_letter):
                    drives.append(drive_letter)
            bitmask >>= 1

        for drive in drives:
            self.drive_combo.addItem(drive)

        # Выбираем текущий диск если он есть в списке
        current_drive = os.path.splitdrive(self.current_local_path)[0] + "\\"
        if current_drive:
            idx = self.drive_combo.findText(current_drive)
            if idx >= 0:
                self.drive_combo.setCurrentIndex(idx)

        self.drive_combo.blockSignals(False)

    def _on_drive_changed(self, index: int):
        """Обработка изменения выбранного диска"""
        if self.drive_combo is None or index < 0:
            return

        drive = self.drive_combo.currentText()
        self.current_local_path = drive
        self.local_path_edit.setText(drive)
        self.refresh_local_files()

    def _setup_local_header(self):
        """Настройка заголовков локальной таблицы"""
        header = self.local_view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 100)
        header.resizeSection(2, 140)
        header.resizeSection(3, 60)

    def _setup_remote_header(self):
        """Настройка заголовков удалённой таблицы"""
        header2 = self.remote_view.header()
        header2.setStretchLastSection(False)
        header2.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header2.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header2.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header2.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header2.resizeSection(1, 100)
        header2.resizeSection(2, 140)
        header2.resizeSection(3, 60)

    def _get_remote_path(self, filename: str) -> str:
        """Получение полного пути к удалённому файлу"""
        return str(PurePosixPath(self.current_remote_path).joinpath(filename))

    def _get_local_path(self, filename: str) -> str:
        """Получение полного пути к локальному файлу"""
        return os.path.join(self.current_local_path, filename)

    def _scroll_log_to_end(self):
        """Автопрокрутка лога к последнему сообщению"""
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def log_operation(self, message: str):
        """Логирование операции"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_edit.append(f"[{ts}] {message}")
        logger.info(message)
        self._scroll_log_to_end()

    def connect_to_remote(self):
        """Подключение к удалённому серверу"""
        try:
            self.ssh = paramiko.SSHClient()

            # Настройка проверки хост-ключей в зависимости от конфига
            if config.app.ssh.strict_host_checking:
                self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Получаем учётные данные с SSH ключом (для диалога)
            creds = get_credentials(self.device, use_key=True)

            port = self.device.port or config.app.ssh.port

            self.ssh.connect(
                self.device.host,
                port=port,
                username=creds.username,
                password=creds.password,
                timeout=config.app.ssh.connect_timeout,
                banner_timeout=config.app.ssh.connect_timeout,
                auth_timeout=config.app.ssh.ssh_connect_timeout
            )
            
            self.sftp = self.ssh.open_sftp()
            self.log_operation(f"Подключено к {self.device.host}:{port}")
            self.refresh_remote_files()
            
        except FileNotFoundError as e:
            self._show_error("Ошибка подключения", f"Файл не найден: {e}")
            self.log_operation(f"Ошибка подключения: {e}")
        except paramiko.AuthenticationException:
            self._show_error("Ошибка аутентификации", "Неверное имя пользователя, пароль или ключ")
            self.log_operation("Ошибка аутентификации")
        except paramiko.SSHException as e:
            self._show_error("SSH ошибка", str(e))
            self.log_operation(f"SSH ошибка: {e}")
        except Exception as e:
            self._show_error("Ошибка подключения", str(e))
            self.log_operation(f"Ошибка подключения: {e}")

    def _show_error(self, title: str, message: str):
        """Показ сообщения об ошибке"""
        QMessageBox.critical(self, title, message)

    def _show_warning(self, title: str, message: str):
        """Показ предупреждения"""
        QMessageBox.warning(self, title, message)

    def _show_info(self, title: str, message: str):
        """Показ информационного сообщения"""
        QMessageBox.information(self, title, message)

    def _confirm_overwrite(self, filename: str) -> bool:
        """Подтверждение перезаписи файла"""
        reply = QMessageBox.question(
            self, "Файл существует",
            f"Файл '{filename}' уже существует. Перезаписать?",
            QMessageBox.Yes | QMessageBox.No
        )
        return reply == QMessageBox.Yes

    def _confirm_delete(self, name: str, is_dir: bool) -> bool:
        """Подтверждение удаления"""
        obj_type = "папку" if is_dir else "файл"
        text = " и всё её содержимое" if is_dir else ""
        reply = QMessageBox.question(
            self, "Подтверждение удаления",
            f"Удалить {obj_type} '{name}'{text}?",
            QMessageBox.Yes | QMessageBox.No
        )
        return reply == QMessageBox.Yes

    def refresh_local_files(self):
        """Обновление списка локальных файлов"""
        self.local_model.clear()
        self.local_model.setHorizontalHeaderLabels(["Имя", "Размер", "Дата", "Права"])

        parent_item = FileItem("..", is_dir=True, is_parent_dir=True)
        parent_item.setIcon(self.folder_icon)
        self.local_model.appendRow([parent_item, QStandardItem(), QStandardItem(), QStandardItem()])

        try:
            for entry in os.scandir(self.current_local_path):
                name_item = FileItem(
                    entry.name,
                    size=entry.stat().st_size if entry.is_file() else 0,
                    date=datetime.fromtimestamp(entry.stat().st_mtime),
                    permissions=oct(entry.stat().st_mode)[-3:],
                    is_dir=entry.is_dir()
                )
                if entry.is_dir():
                    name_item.setIcon(self.folder_icon)

                size_str = format_size(entry.stat().st_size) if entry.is_file() else ""
                size_item = QStandardItem(size_str)
                size_item.setTextAlignment(Qt.AlignRight)
                date_item = QStandardItem(datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
                date_item.setTextAlignment(Qt.AlignRight)
                perm_item = QStandardItem(oct(entry.stat().st_mode)[-3:])
                perm_item.setTextAlignment(Qt.AlignRight)
                self.local_model.appendRow([name_item, size_item, date_item, perm_item])

        except Exception as e:
            self.log_operation(f"Ошибка чтения локальной директории: {e}")

        self.local_path_edit.setText(self.current_local_path)
        self.log_operation(f"Открыта локальная папка: {self.current_local_path}")
        
        # Применяем настройки заголовка после заполнения модели
        self._setup_local_header()

    def refresh_remote_files(self):
        """Обновление списка удалённых файлов"""
        self.remote_model.clear()
        self.remote_model.setHorizontalHeaderLabels(["Имя", "Размер", "Дата", "Права"])
        
        parent_item = FileItem("..", is_dir=True, is_parent_dir=True)
        parent_item.setIcon(self.folder_icon)
        self.remote_model.appendRow([parent_item, QStandardItem(), QStandardItem(), QStandardItem()])
        
        try:
            for entry in self.sftp.listdir_attr(self.current_remote_path):
                is_dir = S_ISDIR(entry.st_mode)
                name_item = FileItem(
                    entry.filename,
                    size=entry.st_size if not is_dir else 0,
                    date=datetime.fromtimestamp(entry.st_mtime),
                    permissions=oct(S_IMODE(entry.st_mode))[-3:],
                    is_dir=is_dir
                )
                if is_dir:
                    name_item.setIcon(self.folder_icon)
                
                size_str = format_size(entry.st_size) if not is_dir else ""
                size_item = QStandardItem(size_str)
                size_item.setTextAlignment(Qt.AlignRight)
                date_item = QStandardItem(datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
                date_item.setTextAlignment(Qt.AlignRight)
                perm_item = QStandardItem(oct(S_IMODE(entry.st_mode))[-3:])
                perm_item.setTextAlignment(Qt.AlignRight)
                self.remote_model.appendRow([name_item, size_item, date_item, perm_item])
                
        except Exception as e:
            self.log_operation(f"Ошибка чтения удалённой директории: {e}")

        self.remote_path_edit.setText(self.current_remote_path)
        self.log_operation(f"Открыта удалённая папка: {self.current_remote_path}")
        
        # Применяем настройки заголовка после заполнения модели
        self._setup_remote_header()

    def on_local_double_clicked(self, index):
        """Обработка двойного клика по локальному файлу"""
        if not index.isValid():
            return
        source_index = self.local_proxy_model.mapToSource(index)
        item = self.local_model.itemFromIndex(source_index)
        if not item or not item.data(Qt.UserRole + 2):
            return
        
        if item.text() == "..":
            self.current_local_path = os.path.dirname(self.current_local_path)
        else:
            self.current_local_path = os.path.join(self.current_local_path, item.text())
        self.refresh_local_files()

    def on_remote_double_clicked(self, index):
        """Обработка двойного клика по удалённому файлу"""
        if not index.isValid():
            return
        source_index = self.remote_proxy_model.mapToSource(index)
        item = self.remote_model.itemFromIndex(source_index)
        if not item or not item.data(Qt.UserRole + 2):
            return
        
        try:
            current_path = self.current_remote_path
            if not current_path or current_path == ".":
                current_path = self.sftp.normalize(".")
            else:
                current_path = self.sftp.normalize(current_path)
            
            if item.text() == "..":
                parent = os.path.dirname(current_path.rstrip("/"))
                self.current_remote_path = parent if parent else "/"
            else:
                next_path = self.sftp.normalize(
                    str(PurePosixPath(current_path).joinpath(item.text()))
                )
                try:
                    attr = self.sftp.stat(next_path)
                    if S_ISDIR(attr.st_mode):
                        self.current_remote_path = next_path
                except Exception as e:
                    self.log_operation(f"Ошибка доступа к директории: {e}")
                    return
            self.refresh_remote_files()
        except Exception as e:
            self.log_operation(f"Ошибка навигации: {e}")

    # =============================================================================
    # Контекстные меню
    # =============================================================================

    def on_remote_context_menu(self, pos):
        """Контекстное меню для удалённых файлов"""
        def build_menu(index) -> QMenu:
            menu = QMenu(self)
            menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Создать папку", lambda: self.create_remote_folder())
            menu.addAction(QIcon(ICONS.get('menu_tools_grid', '')), "Обновить", lambda: self.refresh_remote_files())

            if not index.isValid():
                return menu

            source_index = self.remote_proxy_model.mapToSource(index)
            name_item = self.remote_model.itemFromIndex(
                self.remote_model.index(source_index.row(), 0)
            )
            if not name_item or name_item.text() == "..":
                return menu

            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('menu_info', '')), "Просмотр файла", lambda: self.preview_remote_file(name_item))
            menu.addAction(QIcon(ICONS.get('menu_export_host', '')), "Копировать на локальный компьютер", lambda: self.copy_remote_to_local(name_item))
            menu.addAction(QIcon(ICONS.get('menu_delete', '')), "Удалить", lambda: self.delete_remote_file(name_item))
            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('shield', '')), "Изменить права доступа", lambda: self.show_remote_info(name_item))

            return menu

        index = self.remote_view.indexAt(pos)
        menu = build_menu(index)
        menu.exec(self.remote_view.viewport().mapToGlobal(pos))

    def on_local_context_menu(self, pos):
        """Контекстное меню для локальных файлов"""
        def build_menu(index) -> QMenu:
            menu = QMenu(self)
            menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Создать папку", lambda: self.create_local_folder())
            menu.addAction(QIcon(ICONS.get('menu_tools_grid', '')), "Обновить", lambda: self.refresh_local_files())

            if not index.isValid():
                return menu

            source_index = self.local_proxy_model.mapToSource(index)
            name_item = self.local_model.itemFromIndex(
                self.local_model.index(source_index.row(), 0)
            )
            if not name_item or name_item.text() == "..":
                return menu

            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('menu_import_host', '')), "Загрузить на сервер", lambda: self.upload_local_to_remote(name_item))
            menu.addAction(QIcon(ICONS.get('menu_delete', '')), "Удалить", lambda: self.delete_local_file(name_item))
            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('menu_info', '')), "Свойства", lambda: self.show_local_info(name_item))

            return menu

        index = self.local_view.indexAt(pos)
        menu = build_menu(index)
        menu.exec(self.local_view.viewport().mapToGlobal(pos))

    # =============================================================================
    # Операции с локальными файлами
    # =============================================================================

    def create_local_folder(self):
        """Создание локальной папки"""
        folder_name, ok = QInputDialog.getText(self, "Создание папки", "Имя новой папки:")
        if ok and folder_name:
            new_path = os.path.join(self.current_local_path, folder_name)
            try:
                os.makedirs(new_path, exist_ok=True)
                self.refresh_local_files()
                self.log_operation(f"Создана локальная папка: {new_path}")
            except Exception as e:
                self._show_error("Ошибка создания", str(e))
                self.log_operation(f"Ошибка создания локальной папки: {e}")

    def delete_local_dir(self, dir_path: str):
        """Рекурсивное удаление локальной папки"""
        for entry in os.scandir(dir_path):
            entry_path = os.path.join(dir_path, entry.name)
            if entry.is_dir():
                self.delete_local_dir(entry_path)
            else:
                os.remove(entry_path)
                self.log_operation(f"Удалён файл: {entry_path}")
        os.rmdir(dir_path)
        self.log_operation(f"Удалена папка: {dir_path}")

    def delete_local_file(self, item: FileItem):
        """Удаление локального файла или папки"""
        item_text = item.text()
        local_path = self._get_local_path(item_text)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not self._confirm_delete(item_text, is_dir):
            return
        
        try:
            if is_dir:
                self.delete_local_dir(local_path)
            else:
                os.remove(local_path)
                self.log_operation(f"Удалён файл: {local_path}")
            self.refresh_local_files()
            self.log_operation(f"Удалено: {item_text}")
        except Exception as e:
            self._show_error("Ошибка удаления", str(e))
            self.log_operation(f"Ошибка удаления: {e}")

    def show_local_info(self, item: FileItem):
        """Показ свойств локального файла"""
        local_path = self._get_local_path(item.text())
        try:
            stat_info = os.stat(local_path)
            info = (
                f"Путь: {local_path}\n"
                f"Размер: {stat_info.st_size} байт\n"
                f"Права: {oct(stat_info.st_mode)[-3:]}\n"
                f"Изменён: {datetime.fromtimestamp(stat_info.st_mtime)}"
            )
            self._show_info("Свойства файла", info)
            self.log_operation(f"Просмотрены свойства: {local_path}")
        except Exception as e:
            self._show_error("Ошибка", str(e))
            self.log_operation(f"Ошибка получения свойств: {e}")

    # =============================================================================
    # Операции с удалёнными файлами
    # =============================================================================

    def create_remote_folder(self):
        """Создание удалённой папки"""
        folder_name, ok = QInputDialog.getText(self, "Создание папки", "Имя новой папки:")
        if ok and folder_name:
            try:
                self.sftp.mkdir(self._get_remote_path(folder_name))
                self.refresh_remote_files()
                self.log_operation(f"Создана папка на сервере: {folder_name}")
            except Exception as e:
                self._show_error("Ошибка создания", str(e))
                self.log_operation(f"Ошибка создания папки: {e}")

    def delete_remote_dir(self, remote_dir_path: str):
        """Рекурсивное удаление удалённой папки"""
        for entry in self.sftp.listdir_attr(remote_dir_path):
            entry_path = str(PurePosixPath(remote_dir_path).joinpath(entry.filename))
            if S_ISDIR(entry.st_mode):
                self.delete_remote_dir(entry_path)
            else:
                self.sftp.remove(entry_path)
                self.log_operation(f"Удалён файл: {entry_path}")
        self.sftp.rmdir(remote_dir_path)
        self.log_operation(f"Удалена папка: {remote_dir_path}")

    def delete_remote_file(self, item: FileItem):
        """Удаление удалённого файла или папки"""
        item_text = item.text()
        remote_path = self._get_remote_path(item_text)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not self._confirm_delete(item_text, is_dir):
            return
        
        try:
            if is_dir:
                self.delete_remote_dir(remote_path)
            else:
                self.sftp.remove(remote_path)
                self.log_operation(f"Удалён файл: {remote_path}")
            self.refresh_remote_files()
            self.log_operation(f"Удалено: {item_text}")
        except Exception as e:
            self._show_error("Ошибка удаления", str(e))
            self.log_operation(f"Ошибка удаления: {e}")

    def preview_remote_file(self, item: FileItem):
        """Предпросмотр и редактирование удалённого файла"""
        if item.data(Qt.UserRole + 2):
            self._show_info("Просмотр", "Просмотр доступен только для файлов.")
            return

        remote_path = self._get_remote_path(item.text())
        try:
            attr = self.sftp.stat(remote_path)
            if attr.st_size > self.PREVIEW_MAX_SIZE:
                self._show_warning(
                    "Файл слишком большой",
                    f"Файл превышает {self.PREVIEW_MAX_SIZE // (1024*1024)} МБ"
                )
                return

            # Читаем файл через SFTP
            with self.sftp.open(remote_path, 'r') as f:
                content = f.read().decode('utf-8', errors='replace')

            # Открываем диалог с возможностью редактирования и сохранения
            dlg = RemoteFilePreviewDialog(
                item.text(), content,
                remote_path=remote_path,
                sftp=self.sftp,
                parent=self
            )
            dlg.exec()
            # Обновляем список файлов после закрытия диалога (размер/дата могли измениться)
            self.refresh_remote_files()
        except Exception as e:
            self._show_error("Ошибка просмотра", str(e))
            self.log_operation(f"Ошибка предпросмотра {remote_path}: {e}")

    def show_remote_info(self, item: FileItem):
        """Показ и изменение прав удалённого файла"""
        full_path = self._get_remote_path(item.text())
        try:
            attr = self.sftp.stat(full_path)
            mode = attr.st_mode
        except Exception:
            mode = 0o644
        
        dlg = RemoteFileInfoDialog(full_path, mode, self)
        if dlg.exec() == QDialog.Accepted:
            new_mode = dlg.get_mode()
            try:
                self.sftp.chmod(full_path, new_mode)
                self.log_operation(f"Права у {full_path} изменены на {oct(new_mode)}")
                self.refresh_remote_files()
            except Exception as e:
                self._show_error("Ошибка", str(e))

    # =============================================================================
    # Передача файлов (асинхронная)
    # =============================================================================

    def _start_transfer(self, worker: QThread):
        """Запуск асинхронной передачи"""
        self._transfer_worker = worker
        worker.progress.connect(self._on_transfer_progress)
        worker.finished.connect(self._on_transfer_finished)
        worker.log_message.connect(self.log_operation)
        worker.finished.connect(worker.deleteLater)

        self.status_progress.setValue(0)
        self.status_progress.setVisible(True)
        self.status_cancel_btn.setVisible(True)
        self.status_label.setText("Начало операции...")
        worker.start()

    @Slot(int, str)
    def _on_transfer_progress(self, percent: int, message: str):
        """Обновление прогресса"""
        self.status_progress.setValue(percent)
        self.status_label.setText(message)
        QApplication.processEvents()

    @Slot(bool, str)
    def _on_transfer_finished(self, success: bool, message: str):
        """Завершение передачи"""
        self.status_progress.setVisible(False)
        self.status_cancel_btn.setVisible(False)
        self.status_label.setText("")
        self._transfer_worker = None

        if success:
            self.log_operation(message)
            self.refresh_local_files()
            self.refresh_remote_files()
        else:
            self._show_error("Ошибка передачи", message)
            self.log_operation(f"Ошибка: {message}")

    def _cancel_transfer(self):
        """Отмена текущей передачи"""
        if self._transfer_worker and hasattr(self._transfer_worker, 'cancel'):
            try:
                self._transfer_worker.cancel()
            except Exception:
                pass
        self.status_progress.setVisible(False)
        self.status_cancel_btn.setVisible(False)
        self.status_label.setText("Отменено")

    def _check_overwrite(self, remote_path: str, local_path: str, is_download: bool) -> bool:
        """Проверка необходимости подтверждения перезаписи"""
        target_path = local_path if is_download else remote_path
        try:
            if is_download:
                exists = os.path.exists(local_path)
            else:
                self.sftp.stat(remote_path)
                exists = True
        except (FileNotFoundError, IOError):
            exists = False
        
        if exists:
            filename = os.path.basename(target_path)
            return self._confirm_overwrite(filename)
        return True

    def copy_remote_to_local(self, item: FileItem):
        """Копирование файла/папки с сервера на локальный компьютер"""
        filename = item.text()
        remote_path = self._get_remote_path(filename)
        local_path = self._get_local_path(filename)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not is_dir and not self._check_overwrite(remote_path, local_path, is_download=True):
            return
        
        if is_dir:
            # Рекурсивное скачивание папки
            worker = RecursiveTransferWorker(self.sftp, 'get', remote_path, local_path, self)
            self._start_transfer(worker)
        else:
            # Скачивание файла
            worker = FileTransferWorker(self.sftp, 'get', remote_path, local_path, parent=self)
            self._start_transfer(worker)

    def upload_local_to_remote(self, item: FileItem):
        """Загрузка файла/папки на сервер"""
        filename = item.text()
        local_path = self._get_local_path(filename)
        remote_path = self._get_remote_path(filename)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not is_dir and not self._check_overwrite(remote_path, local_path, is_download=False):
            return
        
        if is_dir:
            # Рекурсивная загрузка папки
            worker = RecursiveTransferWorker(self.sftp, 'put', local_path, remote_path, self)
            self._start_transfer(worker)
        else:
            # Загрузка файла
            worker = FileTransferWorker(self.sftp, 'put', local_path, remote_path, parent=self)
            self._start_transfer(worker)

    # =============================================================================
    # Завершение работы
    # =============================================================================

    def closeEvent(self, event: QCloseEvent):
        """Корректное закрытие диалога"""
        if self._transfer_worker and self._transfer_worker.isRunning():
            reply = QMessageBox.question(
                self, "Операция выполняется",
                "Выполняется передача файлов. Прервать и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self._cancel_transfer()

        self._cleanup_connections()
        super().closeEvent(event)

    def reject(self):
        """Обработка нажатия Escape"""
        self._cancel_transfer()
        self._cleanup_connections()
        super().reject()

    def _cleanup_connections(self):
        """Закрытие SSH/SFTP соединений"""
        try:
            if self.sftp:
                self.sftp.close()
                self.sftp = None
            if self.ssh:
                self.ssh.close()
                self.ssh = None
            self.log_operation("Соединения закрыты")
        except Exception as e:
            logger.error(f"Ошибка при закрытии соединений: {e}")

    def __del__(self):
        """Деструктор"""
        self._cleanup_connections()
