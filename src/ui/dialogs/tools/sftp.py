"""Диалог SFTP-файлового менеджера: двухпанельный интерфейс для обмена файлами с удалённым хостом."""

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

from src.config import config, ICONS
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
            if unit == 'Б':
                return f"{size} {unit}"
            return f"{size:.1f} {unit}" if size != int(size) else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} ЭБ"


# =============================================================================
# Воркеры для асинхронных операций
# =============================================================================

class TransferCancelled(Exception):
    pass


class FileTransferWorker(QThread):
    """Воркер для асинхронной передачи файлов"""
    
    progress = Signal(int, str)
    finished = Signal(bool, str)
    log_message = Signal(str)
    
    def __init__(self, sftp, operation: str, source: str, dest: str, 
                 is_recursive: bool = False, parent=None):
        super().__init__(parent)
        self.sftp = sftp
        self.operation = operation
        self.source = source
        self.dest = dest
        self.is_recursive = is_recursive
        self._cancelled = False
    
    def run(self):
        try:
            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return
            if self.operation == 'get':
                self._download_file()
            elif self.operation == 'put':
                self._upload_file()
            elif self.operation == 'mkdir':
                self._create_directory()
            if self._cancelled:
                self.finished.emit(False, "Cancelled")
            else:
                self.finished.emit(True, "Операция завершена успешно")
        except TransferCancelled:
            self.finished.emit(False, "Cancelled")
        except Exception as e:
            logger.error(f"Ошибка в FileTransferWorker: {e}")
            self.finished.emit(False, str(e))
    
    def _download_file(self):
        self._last_progress = 0
        
        def callback(current, total):
            if self._cancelled:
                raise TransferCancelled()
            percent = int((current / total) * 100) if total > 0 else 0
            if percent - self._last_progress >= 5 or percent == 100:
                self._last_progress = percent
                self.progress.emit(percent, f"Загружено {current}/{total} байт")

        self.log_message.emit(f"Начато скачивание: {self.source} → {self.dest}")
        self.sftp.get(self.source, self.dest, callback=callback)
        self.log_message.emit(f"Файл скачан: {self.dest}")

    def _upload_file(self):
        self._last_progress = 0
        
        def callback(current, total):
            if self._cancelled:
                raise TransferCancelled()
            percent = int((current / total) * 100) if total > 0 else 0
            if percent - self._last_progress >= 5 or percent == 100:
                self._last_progress = percent
                self.progress.emit(percent, f"Загружено {current}/{total} байт")

        self.log_message.emit(f"Начата загрузка: {self.source} → {self.dest}")
        self.sftp.put(self.source, self.dest, callback=callback)
        self.log_message.emit(f"Файл загружен: {self.dest}")
    
    def _create_directory(self):
        self.log_message.emit(f"Создание директории: {self.dest}")
        try:
            self.sftp.mkdir(self.dest)
        except FileExistsError:
            pass
    
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
        self.operation = operation
        self.source = source
        self.dest = dest
        self._cancelled = False
        self._processed = 0
        self._total = 0
    
    def run(self):
        try:
            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return
            is_remote = self.operation == 'get'
            self._total = self._count_items(self.source, is_remote)
            self._processed = 0
            if self.operation == 'get':
                self._download_recursive(self.source, self.dest)
            else:
                self._upload_recursive(self.source, self.dest)
            if self._cancelled:
                self.finished.emit(False, "Cancelled")
            else:
                self.finished.emit(True, "Операция завершена успешно")
        except TransferCancelled:
            self.finished.emit(False, "Cancelled")
        except Exception as e:
            logger.error(f"Ошибка в RecursiveTransferWorker: {e}")
            self.finished.emit(False, str(e))
    
    def _count_items(self, path: str, is_remote: bool) -> int:
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
                    def callback(current, total):
                        if self._cancelled:
                            raise TransferCancelled()

                    self.sftp.get(remote_entry_path, local_entry_path, callback=callback)
                    self._processed += 1
                    self.progress.emit(
                        int((self._processed / max(self._total, 1)) * 100),
                        f"Обработано {self._processed}/{self._total}"
                    )
                    self.log_message.emit(f"Скачан файл: {remote_entry_path}")
                except TransferCancelled:
                    raise
                except Exception as e:
                    self.log_message.emit(f"Ошибка скачивания {remote_entry_path}: {e}")
    
    def _upload_recursive(self, local_path: str, remote_path: str):
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
                    def callback(current, total):
                        if self._cancelled:
                            raise TransferCancelled()

                    self.sftp.put(local_entry_path, remote_entry_path, callback=callback)
                    self._processed += 1
                    self.progress.emit(
                        int((self._processed / max(self._total, 1)) * 100),
                        f"Обработано {self._processed}/{self._total}"
                    )
                    self.log_message.emit(f"Загружен файл: {remote_entry_path}")
                except TransferCancelled:
                    raise
                except Exception as e:
                    self.log_message.emit(f"Ошибка загрузки {remote_entry_path}: {e}")
    
    def cancel(self):
        self._cancelled = True


# =============================================================================
# Основной диалог
# =============================================================================

class DeleteWorker(QThread):
    """Воркер для асинхронного удаления файлов/папок"""

    progress = Signal(int, str)
    finished = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self, sftp, path: str, is_remote: bool, is_dir: bool, parent=None):
        super().__init__(parent)
        self.sftp = sftp
        self.path = path
        self.is_remote = is_remote
        self.is_dir = is_dir
        self._cancelled = False
        self._processed = 0
        self._total = 0

    def run(self):
        try:
            if self.is_dir:
                if self.is_remote:
                    self._total = 1
                    self._delete_remote_dir(self.path)
                else:
                    self._total = 1
                    self._delete_local_dir(self.path)
            else:
                self._total = 1
                if self.is_remote:
                    self.sftp.remove(self.path)
                    self.log_message.emit(f"Удалён файл: {self.path}")
                else:
                    os.remove(self.path)
                    self.log_message.emit(f"Удалён файл: {self.path}")
                self._processed = 1
                self.progress.emit(100, f"Удалено {self._processed}/{self._total}")
            self.finished.emit(True, "Удаление завершено")
        except Exception as e:
            logger.error(f"Ошибка в DeleteWorker: {e}")
            self.finished.emit(False, str(e))

    def _delete_local_dir(self, dir_path: str):
        for entry in os.scandir(dir_path):
            if self._cancelled:
                break
            entry_path = os.path.join(dir_path, entry.name)
            if entry.is_dir():
                self._delete_local_dir(entry_path)
            else:
                os.remove(entry_path)
                self.log_message.emit(f"Удалён файл: {entry_path}")
                self._processed += 1
                self.progress.emit(
                    int((self._processed / max(self._total, 1)) * 100),
                    f"Удалено {self._processed}/{self._total}"
                )
        os.rmdir(dir_path)
        self.log_message.emit(f"Удалена папка: {dir_path}")

    def _delete_remote_dir(self, remote_dir_path: str):
        for entry in self.sftp.listdir_attr(remote_dir_path):
            if self._cancelled:
                break
            entry_path = str(PurePosixPath(remote_dir_path).joinpath(entry.filename))
            if S_ISDIR(entry.st_mode):
                self._delete_remote_dir(entry_path)
            else:
                self.sftp.remove(entry_path)
                self.log_message.emit(f"Удалён файл: {entry_path}")
                self._processed += 1
                self.progress.emit(
                    int((self._processed / max(self._total, 1)) * 100),
                    f"Удалено {self._processed}/{self._total}"
                )
        self.sftp.rmdir(remote_dir_path)
        self.log_message.emit(f"Удалена папка: {remote_dir_path}")

    def _count_items(self, path: str, is_remote: bool) -> int:
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

    def cancel(self):
        self._cancelled = True


class ConnectionWorker(QThread):
    """Воркер для асинхронного подключения по SSH/SFTP"""

    connected = Signal(object, object)
    error = Signal(str, str)
    finished = Signal()

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device

    def run(self):
        try:
            ssh = paramiko.SSHClient()

            if config.app.ssh.strict_host_checking:
                ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            creds = get_credentials(self.device, use_key=True)
            port = self.device.port or config.app.ssh.port

            ssh.connect(
                self.device.host,
                port=port,
                username=creds.username,
                password=creds.password,
                pkey=creds.private_key,
                timeout=config.app.ssh.connect_timeout,
                banner_timeout=config.app.ssh.connect_timeout,
                auth_timeout=config.app.ssh.ssh_connect_timeout
            )

            sftp = ssh.open_sftp()
            self.connected.emit(ssh, sftp)
        except FileNotFoundError as e:
            self.error.emit("Ошибка подключения", f"Файл не найден: {e}")
        except paramiko.AuthenticationException:
            self.error.emit("Ошибка аутентификации", "Неверное имя пользователя, пароль или ключ")
        except paramiko.SSHException as e:
            self.error.emit("SSH ошибка", str(e))
        except Exception as e:
            self.error.emit("Ошибка подключения", str(e))
        finally:
            self.finished.emit()


class SFTPDialog(QDialog):
    """Диалоговое окно SFTP для обмена файлами"""
    
    PREVIEW_MAX_SIZE = 1024 * 1024  # 1 МБ
    
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.ssh: Optional[paramiko.SSHClient] = None
        self.current_local_path = os.path.expanduser("~")

        creds = get_credentials(device, use_key=True)
        self.current_remote_path = (
            '/root' if creds.username == 'root'
            else str(PurePosixPath("/home").joinpath(creds.username))
        )
        
        self.folder_icon = QIcon(ICONS['folder'])
        self._transfer_worker: Optional[QThread] = None
        self._connection_worker: Optional[ConnectionWorker] = None
        
        self.setWindowTitle("SFTP Передача файлов")
        self.resize(880, 690)
        self.setup_ui()
        self._connect_async()

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
        left_panel.setContentsMargins(4, 2, 4, 2)
        left_panel.setSpacing(4)
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
            path_toolbar.setSpacing(4)

            drive_label = QLabel("Диск:")
            path_toolbar.addWidget(drive_label)

            self.drive_combo = QComboBox()
            self.drive_combo.setMinimumWidth(80)
            self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
            path_toolbar.addWidget(self.drive_combo)

            self.local_path_edit = QLineEdit()
            self.local_path_edit.returnPressed.connect(self._on_local_path_enter)
            path_toolbar.addWidget(self.local_path_edit, 1)

            left_panel.addLayout(path_toolbar)
            self._populate_drives()
        else:
            self.local_path_edit = QLineEdit()
            self.local_path_edit.returnPressed.connect(self._on_local_path_enter)
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
        self.local_view.activated.connect(self.on_local_double_clicked)
        self.local_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_view.customContextMenuRequested.connect(self.on_local_context_menu)
        left_panel.addWidget(self.local_view)

        left_widget.setLayout(left_panel)
        return left_widget

    def _create_remote_panel(self) -> QWidget:
        """Создание правой панели с удалёнными файлами"""
        right_panel = QVBoxLayout()
        right_panel.setContentsMargins(4, 2, 4, 2)
        right_panel.setSpacing(4)
        right_widget = QWidget()
        
        # Устанавливаем политику размера для растягивания
        right_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, 
            QSizePolicy.Policy.Expanding
        )
        
        self.remote_path_edit = QLineEdit()
        self.remote_path_edit.returnPressed.connect(self._on_remote_path_enter)
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
        self.remote_view.activated.connect(self.on_remote_double_clicked)
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
        
        self.reconnect_btn = QPushButton("Переподключить")
        self.reconnect_btn.setVisible(False)
        self.reconnect_btn.clicked.connect(self.reconnect_to_remote)
        
        self.statusbar.addWidget(self.status_label, 1)
        self.statusbar.addPermanentWidget(self.status_progress, 0)
        self.statusbar.addPermanentWidget(self.status_cancel_btn, 0)
        self.statusbar.addPermanentWidget(self.reconnect_btn, 0)
        
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

    def _on_local_path_enter(self):
        """Переход по введённому локальному пути"""
        path = self.local_path_edit.text().strip()
        if os.path.isdir(path):
            self.current_local_path = path
            self.refresh_local_files()
        else:
            self._show_warning("Путь не найден", f"Папка не существует: {path}")
            self.local_path_edit.setText(self.current_local_path)

    def _on_remote_path_enter(self):
        """Переход по введённому удалённому пути"""
        if self.sftp is None:
            return
        path = self.remote_path_edit.text().strip()
        try:
            attr = self.sftp.stat(path)
            if S_ISDIR(attr.st_mode):
                self.current_remote_path = path
                self.refresh_remote_files()
            else:
                self._show_warning("Не папка", f"Путь не является папкой: {path}")
                self.remote_path_edit.setText(self.current_remote_path)
        except Exception as e:
            self._show_warning("Путь не найден", f"Ошибка доступа: {e}")
            self.remote_path_edit.setText(self.current_remote_path)

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

    def _connect_async(self):
        """Асинхронное подключение к удалённому серверу"""
        port = self.device.port or config.app.ssh.port
        self.status_label.setText(f"Подключение к {self.device.host}:{port}...")
        self.status_progress.setVisible(True)
        self.status_progress.setRange(0, 0)

        self._connection_worker = ConnectionWorker(self.device, self)
        self._connection_worker.connected.connect(self._on_connected)
        self._connection_worker.error.connect(self._on_connection_error)
        self._connection_worker.finished.connect(self._on_connection_finished)
        self._connection_worker.start()

    @Slot(object, object)
    def _on_connected(self, ssh, sftp):
        """Обработка успешного подключения"""
        self.ssh = ssh
        self.sftp = sftp
        port = self.device.port or config.app.ssh.port
        self.log_operation(f"Подключено к {self.device.host}:{port}")
        self.reconnect_btn.setVisible(False)
        self.refresh_remote_files()

    @Slot(str, str)
    def _on_connection_error(self, title, message):
        """Обработка ошибки подключения"""
        self._show_error(title, message)
        self.log_operation(f"{title}: {message}")
        self.reconnect_btn.setVisible(True)

    @Slot()
    def _on_connection_finished(self):
        """Завершение подключения (любое)"""
        self.status_progress.setVisible(False)
        self.status_label.setText("")
        if self._connection_worker:
            self._connection_worker.deleteLater()
            self._connection_worker = None

    def reconnect_to_remote(self):
        """Переподключение к удалённому серверу"""
        self._cleanup_connections()
        self._connect_async()

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
        if self.sftp is None:
            return
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
                parent = str(PurePosixPath(current_path).parent)
                self.current_remote_path = parent
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
        menu = QMenu(self)
        menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Создать папку", self.create_remote_folder)
        menu.addAction(QIcon(ICONS.get('menu_tools_grid', '')), "Обновить", self.refresh_remote_files)

        indexes = self.remote_view.selectionModel().selectedIndexes()
        items = []
        for idx in indexes:
            src = self.remote_proxy_model.mapToSource(idx)
            if src.column() == 0:
                name_item = self.remote_model.itemFromIndex(src)
                if name_item and name_item.text() != "..":
                    items.append(name_item)

        if items:
            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('menu_export_host', '')), "Копировать на локальный компьютер", lambda: self._transfer_selected_remote(items))
            menu.addAction(QIcon(ICONS.get('menu_delete', '')), "Удалить", lambda: self._delete_selected_remote(items))
            if len(items) == 1:
                menu.addSeparator()
                menu.addAction(QIcon(ICONS.get('menu_info', '')), "Просмотр файла", lambda: self.preview_remote_file(items[0]))
                menu.addAction(QIcon(ICONS.get('shield', '')), "Изменить права доступа", lambda: self.show_remote_info(items[0]))

        menu.exec(self.remote_view.viewport().mapToGlobal(pos))

    def on_local_context_menu(self, pos):
        """Контекстное меню для локальных файлов"""
        menu = QMenu(self)
        menu.addAction(QIcon(ICONS.get('menu_folder', '')), "Создать папку", self.create_local_folder)
        menu.addAction(QIcon(ICONS.get('menu_tools_grid', '')), "Обновить", self.refresh_local_files)

        indexes = self.local_view.selectionModel().selectedIndexes()
        items = []
        for idx in indexes:
            src = self.local_proxy_model.mapToSource(idx)
            if src.column() == 0:
                name_item = self.local_model.itemFromIndex(src)
                if name_item and name_item.text() != "..":
                    items.append(name_item)

        if items:
            menu.addSeparator()
            menu.addAction(QIcon(ICONS.get('menu_import_host', '')), "Загрузить на сервер", lambda: self._transfer_selected_local(items))
            menu.addAction(QIcon(ICONS.get('menu_delete', '')), "Удалить", lambda: self._delete_selected_local(items))
            if len(items) == 1:
                menu.addSeparator()
                menu.addAction(QIcon(ICONS.get('menu_info', '')), "Свойства", lambda: self.show_local_info(items[0]))

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

    def delete_local_file(self, item: FileItem):
        """Удаление локального файла или папки"""
        item_text = item.text()
        local_path = self._get_local_path(item_text)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not self._confirm_delete(item_text, is_dir):
            return
        
        worker = DeleteWorker(None, local_path, is_remote=False, is_dir=is_dir, parent=self)
        self._start_delete_worker(worker)

    def delete_remote_file(self, item: FileItem):
        """Удаление удалённого файла или папки"""
        if self.sftp is None:
            return
        item_text = item.text()
        remote_path = self._get_remote_path(item_text)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not self._confirm_delete(item_text, is_dir):
            return
        
        worker = DeleteWorker(self.sftp, remote_path, is_remote=True, is_dir=is_dir, parent=self)
        self._start_delete_worker(worker)

    def _start_delete_worker(self, worker: DeleteWorker):
        """Запуск воркера удаления"""
        if self._transfer_worker is not None:
            self._show_warning("Операция выполняется", "Дождитесь завершения текущей операции или отмените её")
            return
        self._transfer_worker = worker
        worker.progress.connect(self._on_transfer_progress)
        worker.finished.connect(self._on_delete_finished)
        worker.log_message.connect(self.log_operation)
        worker.finished.connect(worker.deleteLater)

        self.status_progress.setValue(0)
        self.status_progress.setRange(0, 100)
        self.status_progress.setVisible(True)
        self.status_cancel_btn.setVisible(True)
        self.status_label.setText("Удаление...")
        worker.start()

    @Slot(bool, str)
    def _on_delete_finished(self, success: bool, message: str):
        """Завершение удаления"""
        self.status_progress.setVisible(False)
        self.status_cancel_btn.setVisible(False)
        self.status_cancel_btn.setEnabled(True)
        self.status_label.setText("")
        self._transfer_worker = None

        if success:
            self.log_operation(message)
            self.refresh_local_files()
            self.refresh_remote_files()
        else:
            was_cancelled = "cancel" in message.lower()
            if was_cancelled:
                self.log_operation("Удаление отменено")
            else:
                self._show_error("Ошибка удаления", message)
                self.log_operation(f"Ошибка: {message}")

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

    def preview_remote_file(self, item: FileItem):
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
        if self._transfer_worker is not None:
            self._show_warning("Операция выполняется", "Дождитесь завершения текущей операции или отмените её")
            return
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

    @Slot(bool, str)
    def _on_transfer_finished(self, success: bool, message: str):
        """Завершение передачи"""
        self.status_progress.setVisible(False)
        self.status_cancel_btn.setVisible(False)
        self.status_cancel_btn.setEnabled(True)
        self.status_label.setText("")
        self._transfer_worker = None

        if success:
            self.log_operation(message)
            self.refresh_local_files()
            self.refresh_remote_files()
        else:
            was_cancelled = message == "Cancelled" or "cancel" in message.lower()
            if was_cancelled:
                self.log_operation("Операция отменена")
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
        self.status_label.setText("Отмена операции...")
        self.status_cancel_btn.setEnabled(False)

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
        
        if not self._check_overwrite(remote_path, local_path, is_download=True):
            return
        
        if is_dir:
            worker = RecursiveTransferWorker(self.sftp, 'get', remote_path, local_path, self)
            self._start_transfer(worker)
        else:
            worker = FileTransferWorker(self.sftp, 'get', remote_path, local_path, parent=self)
            self._start_transfer(worker)

    def upload_local_to_remote(self, item: FileItem):
        """Загрузка файла/папки на сервер"""
        filename = item.text()
        local_path = self._get_local_path(filename)
        remote_path = self._get_remote_path(filename)
        is_dir = bool(item.data(Qt.UserRole + 2))
        
        if not self._check_overwrite(remote_path, local_path, is_download=False):
            return
        
        if is_dir:
            worker = RecursiveTransferWorker(self.sftp, 'put', local_path, remote_path, self)
            self._start_transfer(worker)
        else:
            worker = FileTransferWorker(self.sftp, 'put', local_path, remote_path, parent=self)
            self._start_transfer(worker)

    def _transfer_selected_remote(self, items: list):
        """Передача нескольких выбранных удалённых файлов на локальный компьютер"""
        if not items:
            return
        if len(items) == 1:
            self.copy_remote_to_local(items[0])
            return
        from src.ui.dialogs.tools.batch_worker import BatchTransferWorker
        worker = BatchTransferWorker(
            self.sftp, items, self.current_local_path, self.current_remote_path,
            is_upload=False, parent=self
        )
        self._start_transfer(worker)

    def _transfer_selected_local(self, items: list):
        """Передача нескольких выбранных локальных файлов на сервер"""
        if not items:
            return
        if len(items) == 1:
            self.upload_local_to_remote(items[0])
            return
        from src.ui.dialogs.tools.batch_worker import BatchTransferWorker
        worker = BatchTransferWorker(
            self.sftp, items, self.current_local_path, self.current_remote_path,
            is_upload=True, parent=self
        )
        self._start_transfer(worker)

    def _delete_selected_remote(self, items: list):
        """Удаление нескольких выбранных удалённых файлов"""
        if not items:
            return
        for item in items:
            self.delete_remote_file(item)
            if self._transfer_worker is not None:
                break

    def _delete_selected_local(self, items: list):
        """Удаление нескольких выбранных локальных файлов"""
        if not items:
            return
        for item in items:
            self.delete_local_file(item)
            if self._transfer_worker is not None:
                break

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

        if self._connection_worker and self._connection_worker.isRunning():
            self._connection_worker.quit()
            self._connection_worker.wait(3000)

        self._cleanup_connections()
        super().closeEvent(event)

    def reject(self):
        """Обработка нажатия Escape"""
        self._cancel_transfer()
        if self._connection_worker and self._connection_worker.isRunning():
            self._connection_worker.quit()
            self._connection_worker.wait(3000)
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


