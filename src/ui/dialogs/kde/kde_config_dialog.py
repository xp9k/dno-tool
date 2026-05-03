"""
KDE Config Dialog - Диалог управления настройками KDE на удалённой машине.

Интегрируется с основным приложением через:
- Контекстное меню дерева хостов
- SSH подключение через workers
- Получение списка пользователей из /home
- Чтение/запись настроек KDE через kdeglobals
"""

import os
import sys
import re
import configparser
from typing import Dict, List, Optional, Tuple, Any

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPushButton,
    QTabWidget, QScrollArea, QMessageBox, QGroupBox, QToolBar,
    QStatusBar, QFrame, QColorDialog, QProgressDialog, QApplication, QWidget
)
from PySide6.QtGui import QAction, QColor, QPalette, QIcon
from PySide6.QtCore import Qt, QSize, Signal, QObject, QThread

from src.logger import logger
from src.domain.models.device import DeviceModel
from src.config import get_asset_path
from src.workers.command.executor_base import get_credentials
from src.architecture import WorkerBridge, EventBus, EventType
from src.config.kde_settings import (
    SETTING_DESCRIPTIONS, KEY_TO_SECTION, SECTION_NAMES,
    ALL_DEFAULTS, DEFAULT_VALUES
)


class ValueWidget:
    """Класс для хранения информации о виджете и его типе"""
    def __init__(self, widget, value_type, section, key, description=None):
        self.widget = widget
        self.value_type = value_type
        self.section = section
        self.key = key
        self.description = description

    def get_value(self):
        if self.value_type == 'bool':
            return 'true' if self.widget.isChecked() else 'false'
        elif self.value_type == 'int':
            return str(self.widget.value())
        elif self.value_type == 'float':
            return str(self.widget.value())
        elif self.value_type == 'color':
            if hasattr(self.widget, 'lineEdit'):
                return self.widget.lineEdit().text()
            else:
                return self.widget.text()
        elif self.value_type == 'enum':
            return self.widget.currentData()
        else:
            return self.widget.text()

    def set_value(self, value):
        if self.value_type == 'bool':
            self.widget.setChecked(str(value).lower() in ['true', '1', 'on', 'yes'])
        elif self.value_type == 'int':
            try:
                self.widget.setValue(int(value))
            except:
                pass
        elif self.value_type == 'float':
            try:
                self.widget.setValue(float(value))
            except:
                pass
        elif self.value_type == 'color':
            if hasattr(self.widget, 'lineEdit'):
                self.widget.lineEdit().setText(value)
            else:
                self.widget.setText(value)
            color = QColor(value)
            if color.isValid():
                palette = self.widget.palette()
                palette.setColor(QPalette.ColorRole.Base, color)
                if color.lightness() > 150:
                    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
                else:
                    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
                self.widget.setPalette(palette)
        elif self.value_type == 'enum':
            index = self.widget.findData(value)
            if index >= 0:
                self.widget.setCurrentIndex(index)
        else:
            self.widget.setText(value)


class KDEConfigWorker(QThread):
    """
    Worker для выполнения SSH команд при работе с KDE настройками.
    
    Используется для:
    - Получения списка пользователей
    - Чтения файла kdeglobals
    - Записи изменённого kdeglobals
    """
    
    started_signal = Signal()
    finished_signal = Signal(object)  # результат: (success, data, message)
    error_signal = Signal(str)
    progress_signal = Signal(str)
    
    def __init__(self, device: DeviceModel, command: str, timeout: int = 30):
        super().__init__()
        self.device = device
        self.command = command
        self.timeout = timeout
        self._aborting = False
        
    def run(self):
        """Выполнение SSH команды"""
        self.started_signal.emit()
        
        try:
            import paramiko
            from src.config import config, SSH_RECV_BUFFER_SIZE
            
            # Создание SSH подключения
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Подключение
            port = self.device.port or config.app.ssh.port
            creds = get_credentials(self.device, use_key=False)

            logger.info(f"KDE Worker: Подключение к {self.device.host}:{port} как {creds.username}")
            self.progress_signal.emit(f"Подключение к {self.device.host}...")

            client.connect(
                hostname=self.device.host,
                port=port,
                username=creds.username,
                password=creds.password,
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False
            )
            
            # Выполнение команды
            self.progress_signal.emit(f"Выполнение: {self.command[:50]}...")
            stdin, stdout, stderr = client.exec_command(self.command, timeout=self.timeout)
            
            # Чтение вывода
            output = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            
            client.close()
            
            if error and not output:
                self.error_signal.emit(f"Ошибка: {error}")
                self.finished_signal.emit((False, None, error))
            else:
                self.finished_signal.emit((True, output, "Успешно"))
                
        except Exception as e:
            error_msg = f"Ошибка выполнения: {str(e)}"
            logger.error(f"KDE Worker: {error_msg}")
            self.error_signal.emit(error_msg)
            self.finished_signal.emit((False, None, error_msg))
    
    def abort(self):
        """Прерывание выполнения"""
        self._aborting = True


class RemoteKDEConfigManager:
    """
    Менеджер для управления настройками KDE на удалённой машине.
    
    Предоставляет методы для:
    - Получения списка пользователей
    - Чтения конфигурации kdeglobals
    - Записи изменённой конфигурации
    """
    
    def __init__(self, device: DeviceModel, parent: QObject = None):
        self.device = device
        self.parent = parent
        self.logger = logger
        
    def get_home_users(self, callback=None) -> List[str]:
        """
        Получить список пользователей из /home.
        
        Returns:
            Список имён пользователей
        """
        users = []
        try:
            import paramiko
            from src.config import config
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            port = self.device.port or config.app.ssh.port
            creds = get_credentials(self.device, use_key=False)

            client.connect(
                hostname=self.device.host,
                port=port,
                username=creds.username,
                password=creds.password,
                timeout=10
            )

            # Получаем список пользователей из /home
            stdin, stdout, stderr = client.exec_command(
                "ls -1 /home 2>/dev/null | grep -v '^lost+found$'",
                timeout=10
            )
            
            output = stdout.read().decode('utf-8', errors='replace')
            users = [u.strip() for u in output.split('\n') if u.strip()]
            
            # Добавляем текущего пользователя если его нет в /home
            stdin, stdout, stderr = client.exec_command(
                "whoami",
                timeout=10
            )
            current_user = stdout.read().decode('utf-8', errors='replace').strip()
            if current_user and current_user not in users:
                users.insert(0, current_user)
            
            client.close()
            
            self.logger.info(f"RemoteKDEConfig: Найдено пользователей: {users}")
            
        except Exception as e:
            self.logger.error(f"RemoteKDEConfig: Ошибка получения пользователей: {e}")
            if callback:
                callback(False, [], str(e))
            return []
        
        if callback:
            callback(True, users, "Успешно")
        
        return users
    
    def read_kde_config(self, username: str, callback=None) -> Tuple[bool, configparser.ConfigParser, str]:
        """
        Прочитать конфигурацию KDE для пользователя.
        
        Args:
            username: Имя пользователя
            
        Returns:
            (success, config, message)
        """
        try:
            import paramiko
            from src.config import config
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            port = self.device.port or config.app.ssh.port
            creds = get_credentials(self.device, use_key=False)
            ssh_username = creds.username
            password = creds.password

            client.connect(
                hostname=self.device.host,
                port=port,
                username=ssh_username,
                password=password,
                timeout=10
            )
            
            # Определяем путь к kdeglobals
            if username == ssh_username:
                config_path = "~/.config/kdeglobals"
            else:
                config_path = f"/home/{username}/.config/kdeglobals"
            
            # Проверяем существование файла
            stdin, stdout, stderr = client.exec_command(
                f"test -f {config_path} && echo 'exists' || echo 'not_exists'",
                timeout=10
            )
            
            exists = stdout.read().decode('utf-8').strip()
            
            if exists != 'exists':
                client.close()
                msg = f"Файл конфигурации не найден для пользователя {username}"
                self.logger.warning(f"RemoteKDEConfig: {msg}")
                if callback:
                    callback(False, None, msg)
                return (False, None, msg)
            
            # Читаем файл
            stdin, stdout, stderr = client.exec_command(
                f"cat {config_path}",
                timeout=10
            )
            
            config_content = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            
            client.close()
            
            if error and not config_content:
                if callback:
                    callback(False, None, error)
                return (False, None, error)
            
            # Парсим конфигурацию
            kde_config = configparser.ConfigParser()
            kde_config.optionxform = str  # Сохраняем регистр ключей
            
            try:
                kde_config.read_string(config_content)
            except Exception as e:
                msg = f"Ошибка парсинга конфигурации: {e}"
                self.logger.error(f"RemoteKDEConfig: {msg}")
                if callback:
                    callback(False, None, msg)
                return (False, None, msg)
            
            self.logger.info(f"RemoteKDEConfig: Конфигурация загружена для {username}")
            
            if callback:
                callback(True, kde_config, "Успешно")
            
            return (True, kde_config, "Успешно")
            
        except Exception as e:
            msg = f"Ошибка чтения конфигурации: {str(e)}"
            self.logger.error(f"RemoteKDEConfig: {msg}")
            if callback:
                callback(False, None, msg)
            return (False, None, msg)
    
    def write_kde_config(self, username: str, kde_config: configparser.ConfigParser, callback=None) -> Tuple[bool, str]:
        """
        Записать конфигурацию KDE для пользователя.

        Args:
            username: Имя пользователя
            kde_config: Конфигурация для записи

        Returns:
            (success, message)
        """
        try:
            import paramiko
            from src.config import config as app_config

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            port = self.device.port or app_config.app.ssh.port
            creds = get_credentials(self.device, use_key=False)
            ssh_username = creds.username
            password = creds.password

            client.connect(
                hostname=self.device.host,
                port=port,
                username=ssh_username,
                password=password,
                timeout=10
            )

            # Определяем путь к kdeglobals
            if username == ssh_username:
                config_path = "~/.config/kdeglobals"
            else:
                config_path = f"/home/{username}/.config/kdeglobals"

            # Создаём директорию если не существует
            stdin, stdout, stderr = client.exec_command(
                f"mkdir -p $(dirname {config_path})",
                timeout=10
            )
            stdout.read()
            stderr.read()

            # Создаём бэкап если файл существует
            stdin, stdout, stderr = client.exec_command(
                f"test -f {config_path} && cp {config_path} {config_path}.backup",
                timeout=10
            )
            stdout.read()
            stderr.read()

            # Записываем конфигурацию
            config_content = self._config_to_string(kde_config)

            # Используем exec_command для записи файла
            stdin, stdout, stderr = client.exec_command(
                f"cat > {config_path}",
                timeout=10
            )
            stdin.write(config_content.encode('utf-8'))
            stdin.flush()
            stdin.channel.shutdown_write()
            
            # Ждём завершения и читаем вывод
            stdout.channel.recv_exit_status()
            error = stderr.read().decode('utf-8', errors='replace')

            client.close()

            if error:
                if callback:
                    callback(False, error)
                return (False, error)

            self.logger.info(f"RemoteKDEConfig: Конфигурация сохранена для {username}")

            if callback:
                callback(True, "Конфигурация сохранена")

            return (True, "Конфигурация сохранена")

        except Exception as e:
            msg = f"Ошибка записи конфигурации: {str(e)}"
            self.logger.error(f"RemoteKDEConfig: {msg}")
            if callback:
                callback(False, msg)
            return (False, msg)
    
    def _config_to_string(self, config: configparser.ConfigParser) -> str:
        """Конвертировать ConfigParser в строку"""
        output = []
        for section in config.sections():
            output.append(f"[{section}]")
            for key, value in config.items(section):
                output.append(f"{key}={value}")
            output.append("")
        return "\n".join(output)


class KDEConfigDialog(QDialog):
    """
    Диалог управления настройками KDE на удалённой машине.
    
    Функционал:
    - Подключение к удалённой машине через SSH
    - Получение списка пользователей из /home
    - Выбор пользователя для работы
    - Чтение настроек KDE из kdeglobals
    - Отображение настроек с русскими описаниями
    - Сохранение изменений в файл конфигурации
    """
    
    # Сигналы для интеграции с архитектурой
    config_loaded = Signal(bool, object, str)  # success, config, message
    config_saved = Signal(bool, str)   # success, message
    users_loaded = Signal(list)        # список пользователей
    
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.parent = parent
        self.username = None
        self.config_manager = None
        self.config = None
        self.value_widgets = []
        self.modified = False
        self.current_worker = None
        self._user_signal_connected = False  # Флаг подключения сигнала
        self.display_mode = 'basic'  # 'basic' или 'pro'

        self.setWindowTitle(f"⚙️ Управление KDE - {device.host}")
        self.setMinimumSize(1200, 800)

        self._init_ui()
        self._connect_signals()

    def showEvent(self, event):
        """Обработчик показа окна - загружаем пользователей"""
        super().showEvent(event)
        # Загружаем пользователей при первом показе окна
        if not self._user_signal_connected:
            self._load_users()
        
    def _init_ui(self):
        """Инициализация интерфейса"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Верхняя панель с выбором пользователя
        self._init_top_panel(layout)

        # Вкладки с настройками
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        layout.addWidget(self.tabs)

        # Статус бар
        self.statusBar = QStatusBar()
        layout.addWidget(self.statusBar)
        
    def _init_top_panel(self, layout):
        """Верхняя панель с элементами управления"""
        top_frame = QFrame()
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # Выбор пользователя
        top_layout.addWidget(QLabel("👤 Пользователь:"))

        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(200)
        self.user_combo.addItem("Выберите пользователя...", "")
        top_layout.addWidget(self.user_combo)

        # Кнопка загрузки пользователей
        self.load_users_btn = QPushButton("🔄 Загрузить")
        self.load_users_btn.clicked.connect(self._load_users)
        top_layout.addWidget(self.load_users_btn)

        top_layout.addStretch()

        # Режим отображения
        top_layout.addWidget(QLabel("📊 Режим:"))
        
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(150)
        self.mode_combo.addItem("🔰 Базовый", "basic")
        self.mode_combo.addItem("🔧 Расширенный", "pro")
        self.mode_combo.setCurrentIndex(0)  # По умолчанию Базовый
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_layout.addWidget(self.mode_combo)

        top_layout.addStretch()

        # Кнопки управления
        self.load_config_btn = QPushButton("📖 Загрузить настройки")
        self.load_config_btn.clicked.connect(self._load_config)
        self.load_config_btn.setEnabled(False)
        top_layout.addWidget(self.load_config_btn)

        self.save_config_btn = QPushButton("💾 Сохранить")
        self.save_config_btn.clicked.connect(self._save_config)
        self.save_config_btn.setEnabled(False)
        top_layout.addWidget(self.save_config_btn)

        self.reload_config_btn = QPushButton("🔄 Перезагрузить")
        self.reload_config_btn.clicked.connect(self._reload_config)
        self.reload_config_btn.setEnabled(False)
        top_layout.addWidget(self.reload_config_btn)

        # Кнопка экспорта
        self.export_config_btn = QPushButton("📤 Экспорт")
        self.export_config_btn.clicked.connect(self._export_config)
        self.export_config_btn.setEnabled(False)
        top_layout.addWidget(self.export_config_btn)

        layout.addWidget(top_frame)
        
    def _connect_signals(self):
        """Подключение сигналов"""
        self.config_loaded.connect(self._on_config_loaded)
        self.config_saved.connect(self._on_config_saved)
        self.users_loaded.connect(self._on_users_loaded)

    def _on_mode_changed(self, index: int):
        """Обработчик изменения режима отображения"""
        mode = self.mode_combo.itemData(index)
        self.display_mode = mode
        logger.debug(f"Режим изменён на: {mode}")
        
        # Пересоздаём вкладки с учётом нового режима
        if self.config:
            self._populate_tabs()
        
    def _load_users(self):
        """Загрузка списка пользователей"""
        self.statusBar.showMessage("🔄 Загрузка списка пользователей...")
        self.load_users_btn.setEnabled(False)
        
        try:
            self.config_manager = RemoteKDEConfigManager(self.device, self)
            users = self.config_manager.get_home_users()
            
            if users:
                self.users_loaded.emit(users)
            else:
                QMessageBox.warning(self, "Предупреждение", "Не удалось получить список пользователей")
                self.statusBar.showMessage("⚠️ Ошибка загрузки пользователей")
                
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка загрузки пользователей: {e}")
            self.statusBar.showMessage("❌ Ошибка")
            
        finally:
            self.load_users_btn.setEnabled(True)
            
    def _on_users_loaded(self, users: List[str]):
        """Обработчик загрузки пользователей"""
        # Блокируем сигнал на время модификации
        self.user_combo.blockSignals(True)
        
        # Отключаем сигнал если был подключен
        if self._user_signal_connected:
            self.user_combo.currentIndexChanged.disconnect(self._on_user_selected)
            self._user_signal_connected = False

        self.user_combo.clear()
        self.user_combo.addItem("Выберите пользователя...", "")

        for user in sorted(users):
            self.user_combo.addItem(f"👤 {user}", user)

        self.user_combo.currentIndexChanged.connect(self._on_user_selected)
        self._user_signal_connected = True
        
        # Разблокируем сигнал
        self.user_combo.blockSignals(False)
        
        self.statusBar.showMessage(f"✅ Загружено {len(users)} пользователей")
        
        # Если есть пользователи, выбираем первого и загружаем конфиг
        if users:
            self.user_combo.setCurrentIndex(1)  # Индекс 0 - это "Выберите пользователя..."

    def _on_user_selected(self, index: int):
        """Выбор пользователя"""
        user = self.user_combo.itemData(index)
        logger.debug(f"_on_user_selected: index={index}, user={user}")
        
        if user:
            self.username = user
            self.statusBar.showMessage(f"👤 Выбран пользователь: {self.username}")
            # Автоматическая загрузка конфигурации
            logger.debug(f"Вызов _load_config для {self.username}")
            self._load_config()
        else:
            logger.debug(f"Пользователь не выбран (index={index})")

    def _load_config(self):
        """Загрузка конфигурации KDE"""
        logger.debug(f"_load_config: username={self.username}, config_manager={self.config_manager}")
        
        if not self.username:
            QMessageBox.warning(self, "Предупреждение", "Выберите пользователя")
            return
        
        if not self.config_manager:
            logger.debug("Создание config_manager")
            self.config_manager = RemoteKDEConfigManager(self.device, self)

        self.statusBar.showMessage(f"📖 Загрузка настроек для {self.username}...")
        self.load_config_btn.setEnabled(False)
        self.save_config_btn.setEnabled(False)
        self.reload_config_btn.setEnabled(False)
        self.user_combo.setEnabled(False)

        def callback(success: bool, config: Optional[configparser.ConfigParser], message: str):
            logger.debug(f"callback: success={success}, message={message}")
            self.config_loaded.emit(success, config, message)

        logger.debug(f"Вызов read_kde_config для {self.username}")
        self.config_manager.read_kde_config(self.username, callback)

    def _on_config_loaded(self, success: bool, config: Optional[configparser.ConfigParser], message: str):
        """Обработчик загрузки конфигурации"""
        logger.debug(f"_on_config_loaded: success={success}, message={message}")
        
        self.user_combo.setEnabled(True)
        self.load_config_btn.setEnabled(True)

        if success:
            self.config = config
            logger.debug("Вызов _populate_tabs")
            self._populate_tabs()
            self.save_config_btn.setEnabled(True)
            self.reload_config_btn.setEnabled(True)
            self.export_config_btn.setEnabled(True)
            self.statusBar.showMessage(f"✅ Настройки загружены: {message}")
        else:
            QMessageBox.warning(self, "Предупреждение", message)
            self.statusBar.showMessage(f"⚠️ {message}")
        
    def _reload_config(self):
        """Перезагрузка конфигурации"""
        if self.modified:
            reply = QMessageBox.question(
                self, "Несохраненные изменения",
                "Есть несохраненные изменения!\n\nПерезагрузить без сохранения?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
                
        self._load_config()
        
    def _save_config(self):
        """Сохранение конфигурации"""
        if not self.username or not self.config:
            return
            
        # Собираем данные из виджетов
        for vw in self.value_widgets:
            value = vw.get_value()
            if not self.config.has_section(vw.section):
                self.config.add_section(vw.section)
            self.config.set(vw.section, vw.key, value)
        
        self.statusBar.showMessage(f"💾 Сохранение настроек для {self.username}...")
        self.save_config_btn.setEnabled(False)
        
        def callback(success: bool, message: str):
            self.config_saved.emit(success, message)
            
        self.config_manager.write_kde_config(self.username, self.config, callback)
        
    def _on_config_saved(self, success: bool, message: str):
        """Обработчик сохранения конфигурации"""
        if success:
            self.modified = False
            
            # Предлагаем перезагрузить KDE
            reply = QMessageBox.question(
                self,
                "✅ Настройки сохранены",
                f"✅ {message}\n\n"
                "⚠️ Для применения изменений необходимо перезапустить KDE.\n\n"
                "🔄 Перезагрузить KDE сейчас?\n"
                "Это выполнит команду:\n"
                "kquitapp5 plasmashell && kstart5 plasmashell",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self._restart_kde()
            
            self.statusBar.showMessage(f"✅ {message}")
        else:
            QMessageBox.critical(self, "Ошибка", message)
            self.statusBar.showMessage(f"❌ {message}")

        self.save_config_btn.setEnabled(True)

    def _restart_kde(self):
        """Перезагрузка KDE у пользователя"""
        try:
            import paramiko
            from src.config import config as app_config

            self.statusBar.showMessage("🔄 Перезагрузка KDE...")
            logger.debug(f"Перезагрузка KDE на {self.device.host} для {self.username}")

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            port = self.device.port or app_config.app.ssh.port
            creds = get_credentials(self.device, use_key=False)
            ssh_username = creds.username
            password = creds.password

            client.connect(
                hostname=self.device.host,
                port=port,
                username=ssh_username,
                password=password,
                timeout=10
            )

            # Команда для перезагрузки KDE с загрузкой профиля
            # Используем bash -l для загрузки окружения
            restart_cmd = (
                "export DISPLAY=:0; "
                "bash -l -c 'kquitapp5 plasmashell && sleep 2 && kstart5 plasmashell' 2>&1"
            )
            
            logger.debug(f"Выполнение команды: {restart_cmd}")

            stdin, stdout, stderr = client.exec_command(restart_cmd, timeout=30)

            # Читаем вывод
            output = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            
            # Ждём завершения
            exit_status = stdout.channel.recv_exit_status()
            
            client.close()
            
            logger.debug(f"Перезагрузка KDE: exit_status={exit_status}, output={output}, error={error}")

            if exit_status == 0 or "kquitapp5" in output.lower() or not error:
                QMessageBox.information(
                    self,
                    "KDE перезапущен",
                    "✅ KDE успешно перезапущен!\n\n"
                    "Изменения должны примениться в течение нескольких секунд."
                )
                self.statusBar.showMessage("🔄 KDE перезапущен")
            else:
                QMessageBox.warning(
                    self,
                    "Ошибка перезагрузки KDE",
                    f"⚠️ Не удалось перезагрузить KDE.\n\n"
                    f"Выход: {output}\n"
                    f"Ошибка: {error}\n\n"
                    "Вы можете выйти из системы и войти снова."
                )
                self.statusBar.showMessage("⚠️ Ошибка перезагрузки KDE")

        except Exception as e:
            logger.error(f"Перезагрузка KDE: исключение {e}")
            QMessageBox.warning(
                self,
                "Ошибка перезагрузки KDE",
                f"⚠️ Не удалось перезагрузить KDE.\n\n"
                f"Ошибка: {str(e)}\n\n"
                "Вы можете выйти из системы и войти снова."
            )
            self.statusBar.showMessage(f"⚠️ Ошибка: {e}")

    def _export_config(self):
        """Экспорт конфигурации в локальный файл"""
        if not self.config:
            QMessageBox.warning(self, "Предупреждение", "Сначала загрузите настройки")
            return
        
        from PySide6.QtWidgets import QFileDialog
        
        # Предлагаем имя файла
        default_filename = f"kdeglobals_{self.username}_{self.device.host}.cfg"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт настроек KDE",
            default_filename,
            "Config Files (*.cfg *.conf);;All Files (*)"
        )
        
        if file_path:
            try:
                # Сохраняем конфигурацию в файл
                with open(file_path, 'w', encoding='utf-8') as f:
                    self.config.write(f)
                
                QMessageBox.information(
                    self,
                    "Экспорт завершён",
                    f"✅ Настройки экспортированы в:\n{file_path}"
                )
                self.statusBar.showMessage(f"📤 Экспорт: {os.path.basename(file_path)}")
                logger.info(f"Экспорт настроек: {file_path}")
                
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Ошибка экспорта",
                    f"❌ Не удалось экспортировать настройки:\n{str(e)}"
                )
                logger.error(f"Ошибка экспорта: {e}")

    def _populate_tabs(self):
        """Заполнение вкладок настройками"""
        logger.debug(f"_populate_tabs: config={self.config}, username={self.username}, mode={self.display_mode}")

        self.tabs.clear()
        self.value_widgets.clear()

        if not self.config:
            logger.warning("_populate_tabs: self.config is None")
            return

        logger.debug(f"_populate_tabs: config sections={self.config.sections()}")

        # Собираем все секции
        all_sections = set()
        for key in SETTING_DESCRIPTIONS.keys():
            section = KEY_TO_SECTION.get(key, 'General')
            all_sections.add(section)

        # Добавляем секции из конфигурации
        for section in self.config.sections():
            all_sections.add(section)

        # Фильтрация по режиму
        if self.display_mode == 'basic':
            # В базовом режиме показываем только вкладки с ограничениями
            restriction_sections = {
                'KDE Resource Restrictions][$i',
                'KDE Action Restrictions][$i',
                'KDE Control Module Restrictions][$i'
            }
            all_sections = all_sections & restriction_sections
            logger.debug(f"_populate_tabs: basic mode, sections={all_sections}")

        # Сортируем секции
        known_sections = [s for s in all_sections if s in SECTION_NAMES]
        unknown_sections = [s for s in all_sections if s not in SECTION_NAMES]
        sorted_sections = sorted(known_sections, key=lambda s: list(SECTION_NAMES.keys()).index(s) if s in SECTION_NAMES else 999)
        sorted_sections.extend(sorted(unknown_sections))

        logger.debug(f"_populate_tabs: creating {len(sorted_sections)} tabs")

        # Создаём вкладки
        for section in sorted_sections:
            tab_widget = self._create_section_tab(section)
            human_section = SECTION_NAMES.get(section, section)
            self.tabs.addTab(tab_widget, human_section)

        logger.debug(f"_populate_tabs: finished, tabs count={self.tabs.count()}")
            
    def _create_section_tab(self, section_name: str) -> QScrollArea:
        """Создание вкладки с настройками секции"""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 15, 15, 15)

        # Получаем настройки для этой секции
        section_settings = {
            key: desc for key, desc in SETTING_DESCRIPTIONS.items()
            if KEY_TO_SECTION.get(key, 'General') == section_name
        }

        # Получаем значения из конфигурации
        config_values = {}
        if self.config.has_section(section_name):
            for key, value in self.config.items(section_name):
                if not key.startswith('#') and not key.endswith('[$i]'):
                    config_values[key] = value

        # Объединяем
        all_keys = set(section_settings.keys()) | set(config_values.keys())

        if not all_keys:
            label = QLabel("⚠️ Эта секция пуста")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        else:
            # Группируем по типам
            bool_items = []
            int_items = []
            color_items = []
            string_items = []

            for key in all_keys:
                value = config_values.get(key, ALL_DEFAULTS.get(key, ''))
                value_type = self._detect_value_type(key, value)

                if value_type == 'bool':
                    bool_items.append((key, value, value_type))
                elif value_type in ['int', 'float']:
                    int_items.append((key, value, value_type))
                elif value_type == 'color':
                    color_items.append((key, value, value_type))
                else:
                    string_items.append((key, value, value_type))

            # Создаём группы
            if bool_items:
                group = self._create_group_box("✅ Логические настройки", bool_items, section_name)
                layout.addWidget(group)

            if int_items:
                group = self._create_group_box("🔢 Числовые настройки", int_items, section_name)
                layout.addWidget(group)

            if color_items:
                group = self._create_group_box("🎨 Цветовые настройки", color_items, section_name)
                layout.addWidget(group)

            if string_items:
                group = self._create_group_box("📝 Текстовые настройки", string_items, section_name)
                layout.addWidget(group)

        layout.addStretch()
        scroll_area.setWidget(container)
        return scroll_area
        
    def _create_group_box(self, title: str, items: List[Tuple], section_name: str) -> QGroupBox:
        """Создание группы настроек"""
        group = QGroupBox(title)

        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(10)

        # Находим максимальную ширину названий
        max_name_width = 0
        widgets_data = []

        for key, value, value_type in items:
            human_name = SETTING_DESCRIPTIONS.get(key, (key, ""))[0]
            description = SETTING_DESCRIPTIONS.get(key, ("", ""))[1]

            # Для bool типа передаем label сразу в виджет
            label = human_name if value_type == 'bool' else None
            widget = self._create_widget_for_type(value_type, value, section_name, key, label)
            if widget:
                vw = ValueWidget(widget, value_type, section_name, key)
                self.value_widgets.append(vw)

                widgets_data.append((widget, human_name, description, value_type))

                font_metrics = widget.fontMetrics()
                text_width = font_metrics.horizontalAdvance(human_name)
                max_name_width = max(max_name_width, text_width)

        max_name_width += 20

        for widget, human_name, description, value_type in widgets_data:
            row = QHBoxLayout()
            row.setSpacing(10)

            # Для bool типа label уже встроен в QCheckBox, отдельный QLabel не нужен
            if value_type != 'bool':
                name_label = QLabel(f"<b>{human_name}</b>")
                name_label.setMinimumWidth(max_name_width)
                row.addWidget(name_label)

            row.addWidget(widget)

            if description:
                desc_label = QLabel(f"<span style='color: #666; font-size: 12px;'>{description}</span>")
                desc_label.setWordWrap(False)
                row.addWidget(desc_label)

            row.addStretch()
            group_layout.addLayout(row)

        return group
        
    def _create_widget_for_type(self, value_type: str, value: str, section: str, key: str, label: str = None):
        """Создание виджета для типа значения"""
        from PySide6.QtWidgets import QWidget

        if value_type == 'bool':
            widget = QCheckBox()
            widget.setChecked(str(value).lower() in ['true', '1', 'on', 'yes'])
            if label:
                widget.setText(label)
            return widget

        elif value_type == 'int':
            widget = QSpinBox()
            widget.setRange(-1000000, 1000000)
            widget.setSingleStep(1)
            widget.setFixedWidth(200)
            try:
                widget.setValue(int(value))
            except:
                widget.setValue(0)
            return widget

        elif value_type == 'float':
            widget = QDoubleSpinBox()
            widget.setRange(-1000000.0, 1000000.0)
            widget.setDecimals(3)
            widget.setSingleStep(0.1)
            widget.setFixedWidth(200)
            try:
                widget.setValue(float(value))
            except:
                widget.setValue(0.0)
            return widget

        elif value_type == 'color':
            line_edit = QLineEdit()
            line_edit.setReadOnly(True)
            line_edit.setText(value)
            line_edit.setFixedWidth(150)

            color = QColor(value)
            if color.isValid():
                palette = line_edit.palette()
                palette.setColor(QPalette.ColorRole.Base, color)
                if color.lightness() > 150:
                    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
                else:
                    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
                line_edit.setPalette(palette)

            color_btn = QPushButton("🎨")
            color_btn.setFixedWidth(45)

            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(5)
            container_layout.addWidget(line_edit)
            container_layout.addWidget(color_btn)

            color_btn.clicked.connect(lambda: self._select_color(line_edit))

            return container

        else:
            widget = QLineEdit()
            widget.setText(value)
            widget.setFixedWidth(200)
            return widget
            
    def _select_color(self, line_edit):
        """Выбор цвета"""
        current_color = QColor(line_edit.text())
        if not current_color.isValid():
            current_color = QColor("#ffffff")
            
        color = QColorDialog.getColor(current_color, self, "Выберите цвет")
        if color.isValid():
            line_edit.setText(color.name())
            palette = line_edit.palette()
            palette.setColor(QPalette.ColorRole.Base, color)
            if color.lightness() > 150:
                palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
            else:
                palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
            line_edit.setPalette(palette)
            self.modified = True
            
    def _detect_value_type(self, key: str, value: str) -> str:
        """Определение типа значения"""
        # Проверка на boolean
        if str(value).lower() in ['true', 'false', '1', '0', 'on', 'off', 'yes', 'no']:
            return 'bool'
            
        # Проверка на цвет
        if str(value).startswith('#') and len(value) == 7:
            try:
                QColor(value)
                return 'color'
            except:
                pass
                
        # Проверка на integer
        try:
            int(value)
            return 'int'
        except:
            pass
            
        # Проверка на float
        try:
            float(value)
            if '.' in str(value):
                return 'float'
        except:
            pass
            
        return 'string'


# Импорты для виджетов
from PySide6.QtWidgets import QWidget
