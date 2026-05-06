from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, QMessageBox,
                              QDialogButtonBox, QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QTextEdit, QHBoxLayout, QGroupBox,
                              QPushButton)
from PySide6.QtCore import Qt
from src.config import config
from src.workers import CommandWorker
from src.domain.models.device import DeviceModel
from typing import List, Dict, Optional, Any, Tuple
from PySide6.QtGui import QIcon, QFont
from base64 import b64encode, b64decode
import json
from src.logger import logger
from src.ui.widgets.syntax_highlight import BashSyntaxHighlighter

class DeviceEditDialog(QDialog):
    """Диалог редактирования свойств устройства"""
    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self.device = device or DeviceModel({"name": "", "host": ""})
        self.setWindowTitle("Редактирование устройства")
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()

        # Name field
        self.name_label = QLabel("Имя устройства:")
        self.name_edit = QLineEdit(self.device.name)
        layout.addWidget(self.name_label)
        layout.addWidget(self.name_edit)

        # Host field
        self.host_label = QLabel("Хост устройства:")
        self.host_edit = QLineEdit(self.device.host)
        layout.addWidget(self.host_label)
        layout.addWidget(self.host_edit)

        self.user_label = QLabel("Пользователь:")
        self.user_edit = QLineEdit(self.device.login or "")
        layout.addWidget(self.user_label)
        layout.addWidget(self.user_edit)

        self.password_label = QLabel("Пароль:")
        self.password_edit = QLineEdit(self.device.password or "")
        layout.addWidget(self.password_label)
        layout.addWidget(self.password_edit)

        self.port_label = QLabel("Порт:")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(self.device.port or 22)
        layout.addWidget(self.port_label)
        layout.addWidget(self.port_edit)

        self.mac_label = QLabel("MAC-адрес:")
        self.mac_edit = QLineEdit(self.device.mac_address or "")
        self.mac_edit.setPlaceholderText("00:11:22:33:44:55")
        layout.addWidget(self.mac_label)
        layout.addWidget(self.mac_edit)

        # Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.setLayout(layout)

    def get_updated_device(self) -> DeviceModel:
        """Получить обновленные данные устройства"""
        device = DeviceModel({
            "name": self.name_edit.text(),
            "host": self.host_edit.text(),
            "login": self.user_edit.text() if self.user_edit.text() else None,
            "password": self.password_edit.text() if self.password_edit.text() else None,
            "port": self.port_edit.value() if self.port_edit.value() != config.app.ssh.port else None,
            "mac_address": self.mac_edit.text() if self.mac_edit.text() else None,
        })
        return device

    @staticmethod
    def edit_device(device, parent=None):
        """
        Статический метод для редактирования устройства
        Args:
            device: Словарь с данными устройства
            parent: Родительское окно
        Returns:
            tuple: (bool, dict) - Успешно ли выполнено редактирование и новые данные
        """
        dialog = DeviceEditDialog(device, parent)
        if dialog.exec():
            return True, dialog.get_updated_device()
        return False, None

    @staticmethod
    def add_device(parent=None):
        """
        Статический метод для добавления нового устройства
        Args:
            parent: Родительское окно
        Returns:
            tuple: (bool, dict) - Успешно ли выполнено добавление и новые данные
        """
        dialog = DeviceEditDialog(None, parent)
        if dialog.exec():
            return True, dialog.get_updated_device()
        return False, None
    


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout()
        
        # Create SSH settings group
        ssh_group = QGroupBox("Настройки SSH по умолчанию")
        ssh_layout = QFormLayout()
        
        self.username = QLineEdit(config.app.ssh.username)
        self.password = QLineEdit(config.app.ssh.password)
        self.password.setEchoMode(QLineEdit.Password)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(config.app.ssh.port)
        self.strict_host_checking = QCheckBox()
        self.strict_host_checking.setChecked(config.app.ssh.strict_host_checking)

        self.command_timeout = QSpinBox()
        self.command_timeout.setRange(5, 300)
        self.command_timeout.setValue(config.app.ssh.command_timeout)
        self.command_timeout.setSuffix(" сек")

        self.connect_timeout = QSpinBox()
        self.connect_timeout.setRange(1, 60)
        self.connect_timeout.setValue(config.app.ssh.connect_timeout)
        self.connect_timeout.setSuffix(" сек")

        # Add fields to SSH group
        ssh_layout.addRow("Пользователь:", self.username)
        ssh_layout.addRow("Пароль:", self.password)
        ssh_layout.addRow("Порт:", self.port)
        ssh_layout.addRow("Строгая проверка ключей:", self.strict_host_checking)
        ssh_layout.addRow("Таймаут команды:", self.command_timeout)
        ssh_layout.addRow("Таймаут подключения:", self.connect_timeout)
        
        ssh_group.setLayout(ssh_layout)
        main_layout.addWidget(ssh_group)

        # Create General settings group
        general_group = QGroupBox("Общие настройки")
        general_layout = QFormLayout()
        
        self.thread_count = QSpinBox()
        self.thread_count.setRange(1, 32)
        self.thread_count.setValue(config.app.network.thread_count)
        
        self.ping_interval = QSpinBox()
        self.ping_interval.setRange(5, 300)
        self.ping_interval.setValue(config.app.network.ping_interval)
        self.ping_interval.setSuffix(" сек")

        self.ping_timeout = QDoubleSpinBox()
        self.ping_timeout.setRange(0.5, 10.0)
        self.ping_timeout.setValue(config.app.network.ping_timeout)
        self.ping_timeout.setSingleStep(0.5)
        self.ping_timeout.setSuffix(" сек")

        self.expand_trees = QCheckBox()
        self.expand_trees.setChecked(config.app.expand)

        # Add fields to General group
        general_layout.addRow("Количество потоков:", self.thread_count)
        general_layout.addRow("Интервал пинга:", self.ping_interval)
        general_layout.addRow("Таймаут пинга:", self.ping_timeout)
        general_layout.addRow("Разворачивать деревья:", self.expand_trees)
        
        general_group.setLayout(general_layout)
        main_layout.addWidget(general_group)

        # Add buttons
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        
        main_layout.addWidget(buttons)
        self.setLayout(main_layout)

    def accept(self):
        """Save configuration when OK is clicked"""
        thread_count_changed = self.thread_count.value() != config.app.network.thread_count

        config.app.ssh.username = self.username.text()
        config.app.ssh.password = self.password.text()
        config.app.ssh.port = self.port.value()
        config.app.ssh.strict_host_checking = self.strict_host_checking.isChecked()
        config.app.ssh.command_timeout = self.command_timeout.value()
        config.app.ssh.connect_timeout = self.connect_timeout.value()
        config.app.network.thread_count = self.thread_count.value()
        
        # Сохраняем новые настройки пинга
        config.app.network.ping_interval = self.ping_interval.value()
        config.app.network.ping_timeout = self.ping_timeout.value()
        
        config.app.expand = self.expand_trees.isChecked()
        config.save()
        
        # Обновляем настройки для всех активных таймеров пинга
        from src.workers.network import get_host_ping_timer_manager
        manager = get_host_ping_timer_manager()
        manager.update_all_timers_settings(
            ping_interval=config.app.network.ping_interval,
            ping_timeout=config.app.network.ping_timeout,
            ping_port=config.app.ssh.port
        )

        if thread_count_changed:
            QMessageBox.information(
                self,
                "Перезапуск",
                "Количество потоков изменено. Для применения перезапустите программу."
            )
        
        super().accept()


class _DeviceInfoDialogLegacy(QDialog):
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Информация об устройстве")
        self.setMinimumWidth(300)
        
        layout = QVBoxLayout(self)
        
        # Add icon label at the top
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Get current icon from list item
        if self.device.icon is not None:
            icon = QIcon(self.device.icon)
            if isinstance(icon, QIcon):
                pixmap = icon.pixmap(48, 48)
                self.icon_label.setPixmap(pixmap)
        
        layout.addWidget(self.icon_label)
        
        # Create form layout for device info with alignments
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Create and style name fields
        name_label = QLabel("Имя:")
        name_value = QLabel(self.device.name)
        name_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow(name_label, name_value)
        
        # Create and style host fields
        host_label = QLabel("Хост:")
        host_value = QLabel(self.device.host)
        host_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow(host_label, host_value)
        
        layout.addLayout(form)
        
        # Add OK button
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class CommandInfoDialog(QDialog):
    def __init__(self, command_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.command_data = command_data
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle('Информация о команде')
        # self.setGeometry(300, 300, 450, 250)
        layout = QVBoxLayout()

        self.detailsLabel = QLabel("Выберите команду из списка", self)
        self.detailsLabel.setWordWrap(True)
        layout.addWidget(QLabel("Детали команды:"))
        layout.addWidget(self.detailsLabel)
        layout.addStretch(1)
        self.setLayout(layout)


        if self.command_data:
            name = self.command_data.get('name', 'N/A')
            commands = self.command_data.get('command', [])
            description = self.command_data.get('description', 'Нет описания')
            details_text = f"<b>Имя:</b> {name} <br/>"
            details_text += f"<b>Описание:</b> {description}<br/>"
            details_text += "<b>Команды для выполнения:</b>"
            if commands:
                 details_text += "<ul>"
                 for cmd in commands:
                      details_text += f"<li><code>{cmd}</code></li>"
                 details_text += "</ul>"
            else:
                details_text += "<i>(нет команд)</i>"
            self.detailsLabel.setText(details_text)
        else:
            self.detailsLabel.setText("Нет данных для отображения.")


class ExportCommandDlg(QDialog):
    """
    Диалог экспорта команд.
    Поддерживает два режима:
    1. Экспорт списка команд (список словарей с type/text)
    2. Экспорт полной команды (словарь с name, description, timeout, params, commands)
    """
    def __init__(self, parent=None, commands=None):
        super().__init__(parent)
        # Определяем тип данных и кодируем
        encoded_text = b64encode(json.dumps(commands, ensure_ascii=False).encode('utf-8')).decode('utf-8')
        self.encoded_text = encoded_text
        self.is_full_command = isinstance(commands, dict)
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Экспорт полной команды" if self.is_full_command else "Экспорт команд")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        if self.is_full_command:
            self.info_label = QLabel("Скопируйте текст для экспорта команды (включая имя, описание, таймаут, параметры):")
        else:
            self.info_label = QLabel("Скопируйте текст для экспорта команд:")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.command_text_edit = QTextEdit()
        self.command_text_edit.setReadOnly(True)
        self.command_text_edit.setText(self.encoded_text)
        layout.addWidget(self.command_text_edit, stretch=1)

        # Кнопки копирования и закрытия
        buttons_layout = QHBoxLayout()

        self.copy_button = QPushButton("Копировать")
        self.copy_button.clicked.connect(self.command_text_edit.selectAll)
        self.copy_button.clicked.connect(self.command_text_edit.copy)
        buttons_layout.addWidget(self.copy_button)

        buttons_layout.addStretch()

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        buttons_layout.addWidget(ok_button)

        layout.addLayout(buttons_layout)
        self.setLayout(layout)

        # Автоматически выделяем весь текст
        self.command_text_edit.selectAll()
        self.command_text_edit.copy()


class ImportCommandDlg(QDialog):
    """
    Диалог импорта команд.
    Автоматически определяет формат:
    1. Список команд (список словарей с type/text)
    2. Полная команда (словарь с name, description, timeout, params, commands)
    
    Returns:
        dict с полями:
        - is_full_command: bool (True если полная команда)
        - data: сами данные (dict или list)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Импорт команды")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        self.info_label = QLabel("Вставьте закодированный текст команды:\nПоддерживается импорт как отдельных команд, так и полных команд со всей информацией.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.command_text_edit = QTextEdit()
        self.command_text_edit.setPlaceholderText("Вставьте данные команды...")
        layout.addWidget(self.command_text_edit, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_decoded_command(self):
        """
        Декодируем base64 строку и определяем тип данных.
        
        Returns:
            dict с полями:
            - is_full_command: bool
            - data: dict (полная команда) или list (список команд)
            - error: str (если ошибка)
        """
        try:
            encoded_text = self.command_text_edit.toPlainText().strip()
            if not encoded_text:
                return {"is_full_command": False, "data": None, "error": "Текст команды пуст"}

            decoded_json = b64decode(encoded_text.encode('utf-8')).decode('utf-8')
            data = json.loads(decoded_json)

            # Определяем тип данных
            if isinstance(data, dict):
                # Это полная команда - проверяем обязательные поля
                # name не требуется - при импорте используется имя текущей команды
                if "commands" not in data:
                    return {"is_full_command": False, "data": None, "error": "Отсутствует поле 'commands'"}

                # Проверяем формат команд внутри полной команды
                if not isinstance(data["commands"], list):
                    return {"is_full_command": False, "data": None, "error": "Поле 'commands' должно быть списком"}

                for cmd in data["commands"]:
                    if not isinstance(cmd, dict):
                        return {"is_full_command": False, "data": None, "error": "Каждая команда должна быть словарём"}
                    if "type" not in cmd or "text" not in cmd:
                        return {"is_full_command": False, "data": None, "error": "Каждая команда должна иметь 'type' и 'text'"}

                return {"is_full_command": True, "data": data, "error": None}

            elif isinstance(data, list):
                # Это список команд - проверяем формат
                for cmd in data:
                    if not isinstance(cmd, dict):
                        return {"is_full_command": False, "data": None, "error": "Каждая команда должна быть словарём"}
                    if "type" not in cmd or "text" not in cmd:
                        return {"is_full_command": False, "data": None, "error": "Каждая команда должна иметь 'type' и 'text'"}

                return {"is_full_command": False, "data": data, "error": None}

            else:
                return {"is_full_command": False, "data": None, "error": "Неверный формат данных"}

        except json.JSONDecodeError as e:
            return {"is_full_command": False, "data": None, "error": f"Ошибка декодирования JSON: {e}"}
        except Exception as e:
            return {"is_full_command": False, "data": None, "error": f"Ошибка: {e}"}


class BashViewerDialog(QDialog):
    TELEGRAM_CODE_BLOCK_STYLE = """
        background-color: #282c34;
        color: #abb2bf;
        font-family: monospace;
        padding: 10px 15px;
        border-radius: 5px;
        border: 1px solid #4c566a;
        white-space: pre;
    """
    def __init__(self, parent = None, initial_text: str = None):
        super().__init__(parent)
        self.setWindowTitle("Просмотр скрипта")
        self.setMinimumSize(600, 400)

        layout = QVBoxLayout(self)

        self.editor = QTextEdit(self)
        self.editor.setPlaceholderText("Введите ваш bash скрипт здесь...")
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.editor.setFont(font)
        layout.addWidget(self.editor)

        # Применяем подсветку синтаксиса
        self.highlighter = BashSyntaxHighlighter(self.editor.document())

        if initial_text:            
            initial_text = initial_text.replace("\n", "<br>").replace("%TGSTYLE%", self.TELEGRAM_CODE_BLOCK_STYLE)
            # initial_text = html.escape(initial_text)
            self.editor.setHtml(initial_text)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
        )
        self.button_box.accepted.connect(self.accept)
        layout.addWidget(self.button_box)

        self.setLayout(layout)
