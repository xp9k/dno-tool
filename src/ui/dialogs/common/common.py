"""Общие диалоговые окна приложения: редактирование устройства, настройки, экспорт/импорт команд, просмотр скриптов."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QTextEdit,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QSizePolicy,
    QWidget,
    QComboBox,
    QTabWidget,
)
from PySide6.QtCore import Qt, QTimer
from src.config import config
from src.ui.icons import _icon_from_key, qicon_from_path  # zip-темы: иконки из памяти
from src.workers import CommandWorker
from src.workers.command.locale_utils import STANDARD_LOCALES
from src.domain.models.device import DeviceModel
from typing import List, Dict, Optional, Any, Tuple
from PySide6.QtGui import QIcon, QFont
from base64 import b64encode, b64decode
import json
from src.logger import logger
from src.ui.widgets.syntax_highlight import BashSyntaxHighlighter
from src.ui.widgets.thread_count_slider import ThreadCountSlider


class DeviceEditDialog(QDialog):
    """Диалог редактирования свойств устройства"""

    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(True)
        self.device = device or DeviceModel({"name": "", "host": ""})
        self.setWindowTitle("Редактирование устройства")
        self._init_ui()

    @staticmethod
    def _make_tab(tabs: QTabWidget, title: str) -> QFormLayout:
        """Вкладка с формой; строки прижаты кверху, свободное место снизу."""
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        outer.addLayout(form)
        outer.addStretch(1)
        tabs.addTab(page, title)
        return form

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.setMinimumWidth(380)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        node_iid = getattr(self.device, "node_iid", None)

        # Вкладка «Основное»: имя хоста, адрес и MAC для Wake-on-LAN.
        form = self._make_tab(self.tabs, "Основное")

        self.name_label = QLabel("Имя:")
        self.name_edit = QLineEdit(self.device.name)
        form.addRow(self.name_label, self.name_edit)

        self.host_label = QLabel("Адрес хоста:")
        self.host_edit = QLineEdit(self.device.host)
        self.host_edit.setPlaceholderText("IP-адрес или имя хоста")
        form.addRow(self.host_label, self.host_edit)


        self.mac_label = QLabel("MAC-адрес:")
        self.mac_edit = QLineEdit(self.device.mac_address or "")
        self.mac_edit.setPlaceholderText("00:11:22:33:44:55")
        self.mac_edit.setToolTip("MAC-адрес для включения хоста через Wake-on-LAN.")
        form.addRow(self.mac_label, self.mac_edit)

        # Вкладка «SSH»: данные для входа, порт, локаль команд и исключение sudo.
        ssh_form = self._make_tab(self.tabs, "SSH")
        self.ssh_tab = self.tabs.widget(self.tabs.count() - 1)

        self.user_label = QLabel("Данные для входа:")
        self.user_label.setToolTip("Связка учётных данных для входа на хост.")
        from src.ui.widgets.bundle_selector import BundleSelector

        self.bundle_selector = BundleSelector(
            allow_none=True, none_label="(наследовать глобальную)"
        )
        self.bundle_selector.set_current_iid(self.device.credential_bundle_iid)
        if node_iid and node_iid != "local":
            bundle_iid = self.device.credential_bundle_iid
            if bundle_iid and self.bundle_selector.current_iid() != bundle_iid:
                self.bundle_selector._combo.addItem("Связка ноды", bundle_iid)
                self.bundle_selector.set_current_iid(bundle_iid)
            self.bundle_selector.setEnabled(False)
            self.bundle_selector.setToolTip(
                "Связки учетных данных изменяются на ноде-владельце."
            )
        ssh_form.addRow(self.user_label, self.bundle_selector)

        self.port_label = QLabel("Порт SSH:")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(self.device.port or 22)
        ssh_form.addRow(self.port_label, self.port_edit)

        # Локаль: editable QComboBox с пунктами «По умолчанию (наследовать)»,
        # «(отключить)» и стандартными локалями. None = наследовать SSHConfig.locale;
        # строка (в т.ч. пустая "" = «(отключить)») = per-device override.
        # Пункт «По умолчанию» - непустой sentinel, в accept/get_updated_device
        # маппится обратно на None.
        self.locale_combo = QComboBox()
        self.locale_combo.setEditable(True)
        self._LOCALE_DEFAULT = "По умолчанию (наследовать)"
        self._LOCALE_DISABLED = "(отключить)"
        self.locale_combo.addItem(self._LOCALE_DEFAULT)
        self.locale_combo.addItem(self._LOCALE_DISABLED)
        for loc in STANDARD_LOCALES:
            self.locale_combo.addItem(loc)
        # device.locale: None -> «По умолчанию», "" -> «(отключить)», строка -> она
        cur_locale = self.device.locale
        if cur_locale is None:
            self.locale_combo.setCurrentIndex(0)
        elif cur_locale == "":
            self.locale_combo.setCurrentIndex(1)
        else:
            idx = self.locale_combo.findText(cur_locale)
            if idx < 0:
                self.locale_combo.insertItem(2, cur_locale)
                idx = 2
            self.locale_combo.setCurrentIndex(idx)
        self.locale_combo.setPlaceholderText("ru_RU.UTF-8")
        self.locale_combo.setToolTip(
            "Локаль, экспортируемая перед выполнением SSH-команд на этом хосте\n"
            "(LANG/LC_ALL/LC_CTYPE).\n\n"
            "«По умолчанию» - наследует глобальную настройку SSH.\n"
            "«(отключить)» - не экспортировать локаль (если на хосте\n"
            "ru_RU.UTF-8 не сгенерирована, устраняет предупреждение setlocale).\n\n"
            "Можно выбрать из списка или ввести свою."
        )
        self.locale_label = QLabel("Локаль:")
        ssh_form.addRow(self.locale_label, self.locale_combo)

        # Исключение sudo: при логине не root права повышаются всегда, галочка
        # нужна для учёток без прав sudo и команд от самого пользователя входа.
        self.no_sudo_check = QCheckBox("Не повышать права через sudo")
        self.no_sudo_check.setChecked(self.device.use_sudo is False)
        self.no_sudo_check.setToolTip(
            "При входе не под root команды выполняются через sudo, пароль берётся\n"
            "из связки. Отметьте, если у учётной записи нет прав sudo или команды\n"
            "должны работать от самого пользователя входа (его HOME, его файлы).\n"
            "Вход под root галочка не меняет."
        )
        ssh_form.addRow(self.no_sudo_check)

        # Вкладка «Экран»: VNC-порт и источник миниатюр.
        remote_form = self._make_tab(self.tabs, "Экран")

        # VNC-порт per-host override (0 = глобальный config vnc_port)
        self.vnc_port_label = QLabel("Порт VNC:")
        self.vnc_port_edit = QSpinBox()
        self.vnc_port_edit.setRange(0, 65535)
        self.vnc_port_edit.setSpecialValueText("Глобальный")
        self.vnc_port_edit.setValue(int(self.device.vnc_port or 0))
        self.vnc_port_edit.setToolTip(
            "Порт VNC-сервера на этом хосте (kmsvnc, x11vnc и KRFB).\n"
            "«Глобальный» (0) - использовать порт из настроек VNC."
        )
        remote_form.addRow(self.vnc_port_label, self.vnc_port_edit)

        self.thumbnail_backend_combo = QComboBox()
        self.thumbnail_backend_combo.addItem("По умолчанию", None)
        self.thumbnail_backend_combo.addItem("Veyon", "veyon")
        self.thumbnail_backend_combo.addItem("VNC", "vnc")
        idx = self.thumbnail_backend_combo.findData(self.device.thumbnail_backend)
        self.thumbnail_backend_combo.setCurrentIndex(max(0, idx))
        self.thumbnail_backend_combo.setToolTip(
            "По умолчанию - настройка ноды-владельца. Изменения сохраняются на ноде."
            if node_iid and node_iid != "local"
            else "По умолчанию - общая настройка миниатюр этого экземпляра."
        )
        remote_form.addRow("Источник миниатюр:", self.thumbnail_backend_combo)
        self._thumbnail_backends_supported = True

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.name_edit.setFocus()
        self.resize(420, 260)

    def accept(self):
        """Валидация перед сохранением: вход не под root через sudo без пароля - предупреждение."""
        from src.workers.command.sudo_utils import should_use_sudo

        use_sudo = False if self.no_sudo_check.isChecked() else None
        # Локаль: «По умолчанию» → None (наследовать), «(отключить)» → "" (явно
        # отключить), иначе - введённая/выбранная строка.
        loc_text = self.locale_combo.currentText().strip()
        if loc_text == self._LOCALE_DEFAULT:
            locale = None
        elif loc_text == self._LOCALE_DISABLED or not loc_text:
            locale = ""
        else:
            locale = loc_text
        tmp = DeviceModel(
            {
                "iid": getattr(self.device, "iid", None),
                "name": self.name_edit.text(),
                "host": self.host_edit.text(),
                "credential_bundle_iid": self.bundle_selector.current_iid(),
                "port": self.port_edit.value(),
                "mac_address": self.mac_edit.text() or None,
                "use_sudo": use_sudo,
                "locale": locale,
                "vnc_port": self.vnc_port_edit.value() or None,
                "thumbnail_backend": self.thumbnail_backend_combo.currentData(),
            }
        )
        node_iid = getattr(self.device, "node_iid", None)
        if not (node_iid and node_iid != "local"):
            # Логин и пароль - той связкой, под которой хост будет входить (своя
            # связка, иначе по умолчанию). Связки берутся там же, где их берёт
            # список окна: в мастере это черновик, а не рабочие настройки.
            login, password = None, None
            try:
                from src.ui.widgets.bundle_selector import credential_service_for

                svc = credential_service_for(self)
                if svc is not None:
                    login, password = svc.resolve(tmp)
            except Exception:  # noqa: BLE001 - без связок проверять нечего
                pass
            login = (login or "").strip() or "root"
            if should_use_sudo(tmp, login) and not password:
                box = QMessageBox(
                    QMessageBox.Icon.Warning,
                    "Пароль для sudo",
                    f"Хост входит под «{login}», команды выполняются через sudo, "
                    "но в связке нет пароля.\n\n"
                    "Без пароля sudo работает, только если на хосте он настроен "
                    "без пароля (NOPASSWD). Иначе выберите связку с паролем или "
                    "отметьте «Не повышать права через sudo».",
                    parent=self,
                )
                back = box.addButton("Указать связку", QMessageBox.ButtonRole.RejectRole)
                keep = box.addButton("Сохранить как есть", QMessageBox.ButtonRole.AcceptRole)
                box.setDefaultButton(back)
                box.setEscapeButton(back)
                box.exec()
                # Сохранение - только явной кнопкой, закрытие окна - возврат.
                if box.clickedButton() is not keep:
                    self.tabs.setCurrentWidget(self.ssh_tab)
                    self.bundle_selector.setFocus()
                    return
        super().accept()

    def set_thumbnail_backends_supported(self, supported: bool) -> None:
        """Disable unsupported VNC edits on a legacy owner node."""
        self._thumbnail_backends_supported = supported
        idx = self.thumbnail_backend_combo.findData("vnc")
        self.thumbnail_backend_combo.model().item(idx).setEnabled(supported)
        if not supported:
            self.thumbnail_backend_combo.setToolTip(
                "По умолчанию - настройка ноды-владельца. "
                "Для выбора VNC обновите программу на ноде."
            )

    def get_updated_device(self) -> DeviceModel:
        """Получить обновленные данные устройства"""
        node_iid = getattr(self.device, "node_iid", None)
        bundle_iid = (
            self.device.credential_bundle_iid
            if node_iid and node_iid != "local"
            else self.bundle_selector.current_iid()
        )
        device = DeviceModel(
            {
                "iid": getattr(self.device, "iid", None),
                "name": self.name_edit.text(),
                "host": self.host_edit.text(),
                "credential_bundle_iid": bundle_iid,
                "port": self.port_edit.value()
                if self.port_edit.value() != config.app.ssh.port
                else None,
                "mac_address": self.mac_edit.text() if self.mac_edit.text() else None,
                "use_sudo": False if self.no_sudo_check.isChecked() else None,
                "vnc_port": self.vnc_port_edit.value() or None,
                "thumbnail_backend": self.thumbnail_backend_combo.currentData(),
                "locale": (
                    None
                    if self.locale_combo.currentText().strip() == self._LOCALE_DEFAULT
                    else (
                        ""
                        if self.locale_combo.currentText().strip()
                        in (self._LOCALE_DISABLED, "")
                        else self.locale_combo.currentText().strip()
                    )
                ),
            }
        )
        node_iid = getattr(self.device, "node_iid", None)
        if node_iid:
            device.node_iid = node_iid
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
        self.setSizeGripEnabled(True)
        self.setWindowTitle("Настройки")
        self.setup_ui()

    @staticmethod
    def _make_tab(tabs: QTabWidget, title: str) -> QFormLayout:
        """Вкладка с формой; строки прижаты кверху, свободное место снизу."""
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        outer.addLayout(form)
        outer.addStretch(1)
        tabs.addTab(page, title)
        return form

    def setup_ui(self):
        main_layout = QVBoxLayout()
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Вкладка «SSH»: подключение по умолчанию.
        ssh_layout = self._make_tab(self.tabs, "SSH")

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

        # Связка учётных данных по умолчанию (глобальная).
        from src.ui.widgets.bundle_selector import BundleSelector

        self.bundle_selector = BundleSelector(
            allow_none=True, none_label="(не выбраны)"
        )
        try:
            from src.di import get_container
            from src.services.configuration.credential_service import CredentialService

            svc = get_container().resolve_optional(CredentialService)
            if svc is not None:
                self.bundle_selector.set_current_iid(svc.get_default_bundle_iid())
        except Exception:
            pass
        self.bundle_selector.setToolTip(
            "Связка учётных данных (логин/пароль) по умолчанию для всех хостов.\n"
            "Хосты с явным выбором связки перекрывают это значение.\n\n"
            "Кнопка справа открывает окно управления связками."
        )

        self.locale_combo = QComboBox()
        self.locale_combo.setEditable(True)
        self.locale_combo.addItems(list(STANDARD_LOCALES))
        cur_locale = (config.app.ssh.locale or "").strip()
        if cur_locale:
            idx = self.locale_combo.findText(cur_locale)
            if idx < 0:
                self.locale_combo.insertItem(0, cur_locale)
                idx = 0
            self.locale_combo.setCurrentIndex(idx)
        else:
            self.locale_combo.setEditText("")
        self.locale_combo.setPlaceholderText("ru_RU.UTF-8")
        self.locale_combo.setToolTip(
            "Локаль, экспортируемая перед выполнением SSH-команд\n"
            "(LANG/LC_ALL/LC_CTYPE) для корректного вывода кириллицы.\n\n"
            "По умолчанию «ru_RU.UTF-8» (ROSA Linux).\n\n"
            "Оставьте поле пустым, если на хостах локаль ru_RU.UTF-8\n"
            "не сгенерирована (нет в `locale -a`) - это устранит\n"
            "предупреждение:\n"
            "  bash: warning: setlocale: LC_ALL: cannot change locale (ru_RU.UTF-8)\n\n"
            "Перекрывается per-device в свойствах устройства."
        )

        # Add fields to SSH group
        ssh_layout.addRow("Данные для входа по умолчанию:", self.bundle_selector)
        ssh_layout.addRow("Порт:", self.port)
        ssh_layout.addRow("Строгая проверка ключей:", self.strict_host_checking)
        ssh_layout.addRow("Таймаут команды:", self.command_timeout)
        ssh_layout.addRow("Таймаут подключения:", self.connect_timeout)
        ssh_layout.addRow("Локаль:", self.locale_combo)

        # Вкладка «Общие»: потоки и проверка доступности.
        general_layout = self._make_tab(self.tabs, "Общие")

        self.thread_count = ThreadCountSlider(value=config.app.network.thread_count)
        self.thread_count.setToolTip(
            "Размер каждого из двух независимых общих пулов: статусы и "
            "служебные операции. Например, 8 означает до 8 потоков на пул. "
            "Пулы общие для приложения, веб-интерфейса и нод. "
            "Применяется после перезапуска программы."
        )
        self.command_thread_count = ThreadCountSlider(
            value=config.app.execution.thread_count
        )
        self.command_thread_count.setToolTip(
            "Общий лимит одновременно выполняемых команд из приложения, "
            "веб-интерфейса и нод. Применяется после перезапуска программы."
        )

        self.ping_interval = QSpinBox()
        self.ping_interval.setRange(5, 300)
        self.ping_interval.setValue(config.app.network.ping_interval)
        self.ping_interval.setSuffix(" сек")

        self.ping_timeout = QDoubleSpinBox()
        self.ping_timeout.setRange(0.5, 10.0)
        self.ping_timeout.setValue(config.app.network.ping_timeout)
        self.ping_timeout.setSingleStep(0.5)
        self.ping_timeout.setSuffix(" сек")

        # Add fields to General group
        general_layout.addRow(
            "Потоки статусов и служебных операций:", self.thread_count
        )
        general_layout.addRow("Потоки выполнения команд:", self.command_thread_count)
        restart_hint = QLabel(
            "Изменения числа потоков применяются после перезапуска программы."
        )
        restart_hint.setWordWrap(True)
        restart_hint.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        general_layout.addRow(restart_hint)
        general_layout.addRow("Интервал пинга:", self.ping_interval)
        general_layout.addRow("Таймаут пинга:", self.ping_timeout)

        # Вкладка «Отображение»: подписи хостов, деревья, каомодзи.
        self._setup_display_tab()

        # Внешний вид: тема оформления (QPalette). Выбор применяется сразу,
        # сохраняется в конфиг только по «Сохранить», «Отмена» возвращает назад.
        appearance_layout = self._make_tab(self.tabs, "Внешний вид")
        from src.ui.theme_manager import (
            FALLBACK_THEME_ID,
            SYSTEM_THEME_ID,
            apply_theme,
            list_themes,
        )

        self._initial_theme_id = getattr(config.app, "theme", FALLBACK_THEME_ID)
        self.theme_combo = QComboBox()
        for theme in list_themes():
            tid = theme.get("id", SYSTEM_THEME_ID)
            label = theme.get("name", tid)
            if theme.get("dark") is True:
                label = f"{label} (тёмная)"
            elif theme.get("dark") is False:
                label = f"{label} (светлая)"
            self.theme_combo.addItem(label, tid)
        idx = self.theme_combo.findData(self._initial_theme_id)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.setToolTip(
            "Тема оформления интерфейса.\n"
            "«Системная» - нативный вид Windows/Linux.\n"
            "Остальные - палитра поверх стиля Fusion: цвета окон,\n"
            "меню, тултипов и элементов едины на всех системах.\n\n"
            "Применяется сразу, без перезапуска."
        )
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_layout.addRow("Тема:", self.theme_combo)
        from src.ui.icon_theme_manager import list_icon_themes

        self._initial_icon_theme_id = getattr(config.app, "icon_theme", "flat")
        self.icon_theme_combo = QComboBox()
        for icon_theme in list_icon_themes():
            iid = icon_theme.get("id", "flat")
            self.icon_theme_combo.addItem(icon_theme.get("name", iid), iid)
        idx = self.icon_theme_combo.findData(self._initial_icon_theme_id)
        self.icon_theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.icon_theme_combo.setToolTip(
            "Набор иконок интерфейса.\n"
            "Flat - плоские, Classic - объёмные.\n\n"
            "Применяется сразу, без перезапуска."
        )
        self.icon_theme_combo.currentIndexChanged.connect(self._on_icon_theme_changed)
        appearance_layout.addRow("Иконки:", self.icon_theme_combo)

        # Дебаунс live-применения тем: каждое переключение комбобокса
        # (стрелки/колесо по списку) иначе синхронно делает полный refresh
        # иконок всех виджетов (при большом дереве - фриз на каждый шаг).
        # Пачка переключений схлопывается в одно применение по таймауту.
        self._theme_debounce_timer = QTimer(self)
        self._theme_debounce_timer.setSingleShot(True)
        self._theme_debounce_timer.setInterval(200)
        self._theme_debounce_timer.timeout.connect(self._apply_pending_themes)
        self._pending_theme_id = None
        self._pending_icon_theme_id = None
        # Фактически применённая visual-тема (apply_theme может откатить
        # неизвестный id на fallback): база для «изменилась ли» в reject.
        self._applied_theme_id = self._initial_theme_id

        # Add buttons
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("Отмена", QDialogButtonBox.RejectRole)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        main_layout.addWidget(buttons)
        self.setLayout(main_layout)

    def _setup_display_tab(self) -> None:
        """Вкладка «Отображение»: что подписывать под значками хостов и вид окна."""
        from src.config.settings import (
            HOST_CAPTION_TEXT_FIELDS,
            HOST_CAPTION_USER_MODES,
            normalize_caption_order,
            normalize_host_captions,
            normalize_user_mode,
        )
        from src.ui.widgets.ordered_check_list import OrderedCheckList

        page = QWidget()
        outer = QVBoxLayout(page)

        display = getattr(config.app, "display", None)

        # Главная галочка: весь сбор сведений по SSH при пинге. От неё
        # зависят «Информация о хосте» и поля подписи из сведений.
        self.query_info_on_ping = QCheckBox("Запрашивать сведения при пинге")
        self.query_info_on_ping.setChecked(
            bool(getattr(display, "query_info_on_ping", False))
        )
        self.query_info_on_ping.setToolTip(
            "Каждый пинг, на который хост ответил, запускает сбор сведений\n"
            "по SSH: вошедший пользователь, имя компьютера, время работы,\n"
            "память и т. п. Сведения обновляются с интервалом пинга.\n"
            "Без галочки выполняются только пинги."
        )
        outer.addWidget(self.query_info_on_ping)
        # Группы, зависящие от сбора, - с отступом под главной галочкой.
        dependents = QVBoxLayout()
        dependents.setContentsMargins(22, 0, 0, 0)
        outer.addLayout(dependents)

        tooltip_group = QGroupBox("Подсказка хоста")
        tooltip_layout = QVBoxLayout(tooltip_group)
        self.tooltip_info_on_ping = QCheckBox("Информация о хосте")
        self.tooltip_info_on_ping.setChecked(
            bool(getattr(display, "tooltip_info_on_ping", False))
        )
        self.tooltip_info_on_ping.setToolTip(
            "Показывать собранные сведения в подсказке при наведении.\n"
            "Что показывать, выбирается кнопкой «Информация в тултипе хоста»\n"
            "на панели вида. Нужен запрос сведений при пинге."
        )
        tooltip_layout.addWidget(self.tooltip_info_on_ping)
        dependents.addWidget(tooltip_group)

        captions_group = QGroupBox("Подписи хостов в списке")
        captions_layout = QVBoxLayout(captions_group)

        current = set(
            normalize_host_captions(getattr(display, "host_captions", ["name"]))
        )
        fields_layout = QVBoxLayout()

        labels = dict(HOST_CAPTION_TEXT_FIELDS)
        order = normalize_caption_order(getattr(display, "host_caption_order", None))
        self.caption_list = OrderedCheckList()
        self.caption_list.set_rows(
            [(key, labels[key], key in current) for key in order],
            tooltips={
                "name": "Имя хоста из списка.",
                "host": "Адрес хоста (IP или имя в сети).",
                "hostname": "Имя компьютера на самом хосте (hostname).\n"
                "Нужен запрос сведений при пинге.",
                "user": "Пользователь, вошедший в систему на компьютере\n"
                "(локальный или графический сеанс, не вход по SSH).\n"
                "Нужен запрос сведений при пинге.",
            },
        )
        fields_layout.addWidget(self.caption_list)

        self.user_mode_combo = QComboBox()
        for mode, mode_label in HOST_CAPTION_USER_MODES:
            self.user_mode_combo.addItem(mode_label, mode)
        mode_index = self.user_mode_combo.findData(
            normalize_user_mode(getattr(display, "user_mode", "login"))
        )
        self.user_mode_combo.setCurrentIndex(max(0, mode_index))
        self.user_mode_combo.setToolTip(
            "Учётная запись - логин (teacher).\n"
            "Полное имя - из сведений учётной записи на хосте (GECOS).\n"
            "Оба - «teacher (Иванова М. П.)».\n"
            "Если полное имя не задано, показывается логин."
        )
        # В строке «Вошедший пользователь», сразу после подписи.
        self.caption_list.set_row_widget("user", self.user_mode_combo)
        captions_layout.addLayout(fields_layout)

        self.caption_list.itemChanged.connect(self._sync_query_dependents)
        self.query_info_on_ping.toggled.connect(self._sync_query_dependents)
        self._sync_query_dependents()
        dependents.addWidget(captions_group)

        view_form = QFormLayout()
        self.expand_trees = QCheckBox()
        self.expand_trees.setChecked(config.app.expand)
        self.kaomoji_enabled = QCheckBox()
        self.kaomoji_enabled.setChecked(getattr(config.app, "kaomoji_enabled", True))
        self.kaomoji_enabled.setToolTip(
            "Показывать каомодзи-эмоции в статусной строке\n"
            "(меняются в зависимости от результата операции)"
        )
        view_form.addRow("Разворачивать деревья:", self.expand_trees)
        view_form.addRow("Каомодзи в статус-строке:", self.kaomoji_enabled)
        outer.addLayout(view_form)
        outer.addStretch(1)
        self.tabs.addTab(page, "Отображение")

    def _sync_query_dependents(self, *_args) -> None:
        """От запроса сведений при пинге зависят «Информация о хосте» и поля
        подписи из сведений (пользователь, имя компьютера); отметки сохраняются."""
        from src.config.settings import HOST_CAPTION_COLLECTED

        enabled = self.query_info_on_ping.isChecked()
        self.tooltip_info_on_ping.setEnabled(enabled)
        blocked = self.caption_list.blockSignals(True)
        try:
            for key in HOST_CAPTION_COLLECTED:
                self.caption_list.set_checkable(key, enabled)
        finally:
            self.caption_list.blockSignals(blocked)
        user_mode = enabled and self.caption_list.is_checked("user")
        self.user_mode_combo.setEnabled(user_mode)

    def selected_caption_fields(self) -> List[str]:
        """Отмеченные поля подписи по порядку."""
        return self.caption_list.checked()

    def _on_theme_changed(self, index: int) -> None:
        """Live-применение темы при выборе в комбобоксе (без сохранения).

        Откладывается через дебаунс-таймер: перелистывание комбобокса
        стрелками/колесом не должно фризить GUI на каждый шаг.
        """
        self._pending_theme_id = self.theme_combo.itemData(index)
        self._theme_debounce_timer.start()

    def _on_icon_theme_changed(self, index: int) -> None:
        """Live-применение темы иконок (без сохранения), через дебаунс."""
        self._pending_icon_theme_id = self.icon_theme_combo.itemData(index)
        self._theme_debounce_timer.start()

    def _apply_pending_themes(self) -> None:
        """Разово применить накопленные за дебаунс выборы тем."""
        theme_id = self._pending_theme_id
        icon_theme_id = self._pending_icon_theme_id
        self._pending_theme_id = None
        self._pending_icon_theme_id = None
        if theme_id is not None:
            from PySide6.QtWidgets import QApplication

            from src.ui.theme_manager import FALLBACK_THEME_ID, apply_theme

            app = QApplication.instance()
            if app is not None:
                self._applied_theme_id = apply_theme(app, theme_id or FALLBACK_THEME_ID)
        if icon_theme_id is not None:
            from src.ui.icon_theme_manager import apply_icon_theme

            apply_icon_theme(icon_theme_id or "flat")
        if theme_id is not None or icon_theme_id is not None:
            self._refresh_parent_icons()

    def _icon_theme_effectively_changed(self) -> bool:
        """Текущая икон-тема отличается от исходной на момент открытия."""
        try:
            from src.ui.icon_theme_manager import get_icon_theme

            return get_icon_theme() != self._initial_icon_theme_id
        except Exception:
            return False

    def _visual_theme_effectively_changed(self) -> bool:
        """Визуальная тема реально применялась и отличается от исходной.

        Трекаем фактический id применённой темы (apply_theme возвращает
        эффективный id): сравнение по палитре ненадёжно для system-темы.
        """
        return self._applied_theme_id != self._initial_theme_id

    def _refresh_parent_icons(self) -> None:
        """Попросить главное окно перечитать иконки (live-превью темы)."""
        seen = set()
        widget = self.parent()
        while widget is not None and id(widget) not in seen:
            seen.add(id(widget))
            refresh = getattr(widget, "refresh_icons", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception as e:
                    logger.debug(f"ConfigDialog: parent refresh_icons: {e}")
                return
            try:
                widget = widget.parent()
            except Exception:
                return

    def reject(self) -> None:
        """Отмена: вернуть темы, которые были до открытия настроек.

        Быстрый выход: если темы реально не менялись (окно закрыли без
        переключения комбобоксов), не трогаем apply_theme/apply_icon_theme
        и не дёргаем полный refresh - setStyle+setPalette иначе даёт
        re-polish всех окон (заметный фриз на закрытие).
        """
        self._theme_debounce_timer.stop()
        self._pending_theme_id = None
        self._pending_icon_theme_id = None
        theme_changed = self._visual_theme_effectively_changed()
        icon_changed = self._icon_theme_effectively_changed()
        if theme_changed or icon_changed:
            if theme_changed:
                from PySide6.QtWidgets import QApplication

                from src.ui.theme_manager import apply_theme

                app = QApplication.instance()
                if app is not None:
                    self._applied_theme_id = apply_theme(app, self._initial_theme_id)
            if icon_changed:
                from src.ui.icon_theme_manager import apply_icon_theme

                apply_icon_theme(self._initial_icon_theme_id)
            self._refresh_parent_icons()
        super().reject()

    def accept(self):
        """Save configuration when OK is clicked"""
        thread_count_new = self.thread_count.value()
        thread_count_changed = thread_count_new != config.app.network.thread_count
        command_thread_count_new = self.command_thread_count.value()
        thread_count_changed |= (
            command_thread_count_new != config.app.execution.thread_count
        )

        # Сохраняем связку по умолчанию (глобальная)
        try:
            from src.di import get_container
            from src.services.configuration.credential_service import CredentialService

            svc = get_container().resolve_optional(CredentialService)
            if svc is not None:
                svc.set_default_bundle_iid(self.bundle_selector.current_iid() or "")
        except Exception as e:
            logger.error(f"ConfigDialog: cannot set default bundle: {e}")

        config.app.ssh.port = self.port.value()
        config.app.ssh.strict_host_checking = self.strict_host_checking.isChecked()
        config.app.ssh.command_timeout = self.command_timeout.value()
        config.app.ssh.connect_timeout = self.connect_timeout.value()
        config.app.ssh.locale = self.locale_combo.currentText().strip()
        config.app.network.thread_count = thread_count_new
        config.app.execution.thread_count = command_thread_count_new

        # Сохраняем новые настройки пинга
        config.app.network.ping_interval = self.ping_interval.value()
        config.app.network.ping_timeout = self.ping_timeout.value()

        config.app.expand = self.expand_trees.isChecked()
        config.app.kaomoji_enabled = self.kaomoji_enabled.isChecked()
        from src.config.settings import normalize_caption_order, normalize_host_captions

        config.app.display.host_captions = normalize_host_captions(
            self.selected_caption_fields()
        )
        config.app.display.host_caption_order = normalize_caption_order(
            self.caption_list.order()
        )
        config.app.display.query_info_on_ping = self.query_info_on_ping.isChecked()
        config.app.display.tooltip_info_on_ping = (
            self.tooltip_info_on_ping.isChecked()
        )
        config.app.display.user_mode = self.user_mode_combo.currentData() or "login"
        from src.ui.theme_manager import FALLBACK_THEME_ID as _FALLBACK_THEME_ID

        selected_theme = self.theme_combo.currentData() or _FALLBACK_THEME_ID
        selected_icon_theme = self.icon_theme_combo.currentData() or "flat"
        # Если дебаунс ещё не успел применить выбор live - применяем сейчас.
        # apply_theme/apply_icon_theme возвращают эффективный id (unknown
        # откатывается на fallback) - сохраняем в конфиг именно его.
        self._theme_debounce_timer.stop()
        self._pending_theme_id = None
        self._pending_icon_theme_id = None
        from PySide6.QtWidgets import QApplication

        from src.ui.icon_theme_manager import apply_icon_theme
        from src.ui.theme_manager import apply_theme

        app = QApplication.instance()
        if app is not None:
            config.app.theme = apply_theme(app, selected_theme)
        else:
            config.app.theme = selected_theme
        config.app.icon_theme = apply_icon_theme(selected_icon_theme)
        config.save()

        # Обновляем настройки для всех активных таймеров пинга
        from src.workers.network import get_host_ping_timer_manager

        manager = get_host_ping_timer_manager()
        manager.update_all_timers_settings(
            ping_interval=config.app.network.ping_interval,
            ping_timeout=config.app.network.ping_timeout,
            ping_port=config.app.ssh.port,
        )

        if thread_count_changed:
            QMessageBox.information(
                self,
                "Перезапуск",
                "Количество потоков изменено. Для применения перезапустите программу.",
            )

        super().accept()


class _DeviceInfoDialogLegacy(QDialog):
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(True)
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
            icon = qicon_from_path(self.device.icon)
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
        self.setSizeGripEnabled(True)
        self.command_data = command_data
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Информация о команде")
        # self.setGeometry(300, 300, 450, 250)
        layout = QVBoxLayout()

        self.detailsLabel = QLabel("Выберите команду из списка", self)
        self.detailsLabel.setWordWrap(True)
        layout.addWidget(QLabel("Детали команды:"))
        layout.addWidget(self.detailsLabel)
        layout.addStretch(1)
        self.setLayout(layout)

        if self.command_data:
            name = self.command_data.get("name", "N/A")
            commands = self.command_data.get("command", [])
            description = self.command_data.get("description", "Нет описания")
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
        self.setSizeGripEnabled(True)
        # Определяем тип данных и кодируем
        encoded_text = b64encode(
            json.dumps(commands, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")
        self.encoded_text = encoded_text
        self.is_full_command = isinstance(commands, dict)
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle(
            "Экспорт полной команды" if self.is_full_command else "Экспорт команд"
        )
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        if self.is_full_command:
            self.info_label = QLabel(
                "Скопируйте текст для экспорта команды (включая имя, описание, таймаут, параметры):"
            )
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
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)

        self.info_label = QLabel(
            "Вставьте закодированный текст команды:\nПоддерживается импорт как отдельных команд, так и полных команд со всей информацией."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self.info_label)

        self.command_text_edit = QTextEdit()
        self.command_text_edit.setPlaceholderText("Вставьте данные команды...")
        self.command_text_edit.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        layout.addWidget(self.command_text_edit, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
                return {
                    "is_full_command": False,
                    "data": None,
                    "error": "Текст команды пуст",
                }

            decoded_json = b64decode(encoded_text.encode("utf-8")).decode("utf-8")
            data = json.loads(decoded_json)

            # Определяем тип данных
            if isinstance(data, dict):
                # Это полная команда - проверяем обязательные поля
                # name не требуется - при импорте используется имя текущей команды
                if "commands" not in data:
                    return {
                        "is_full_command": False,
                        "data": None,
                        "error": "Отсутствует поле 'commands'",
                    }

                # Проверяем формат команд внутри полной команды
                if not isinstance(data["commands"], list):
                    return {
                        "is_full_command": False,
                        "data": None,
                        "error": "Поле 'commands' должно быть списком",
                    }

                for cmd in data["commands"]:
                    if not isinstance(cmd, dict):
                        return {
                            "is_full_command": False,
                            "data": None,
                            "error": "Каждая команда должна быть словарём",
                        }
                    if "type" not in cmd or "text" not in cmd:
                        return {
                            "is_full_command": False,
                            "data": None,
                            "error": "Каждая команда должна иметь 'type' и 'text'",
                        }

                return {"is_full_command": True, "data": data, "error": None}

            elif isinstance(data, list):
                # Это список команд - проверяем формат
                for cmd in data:
                    if not isinstance(cmd, dict):
                        return {
                            "is_full_command": False,
                            "data": None,
                            "error": "Каждая команда должна быть словарём",
                        }
                    if "type" not in cmd or "text" not in cmd:
                        return {
                            "is_full_command": False,
                            "data": None,
                            "error": "Каждая команда должна иметь 'type' и 'text'",
                        }

                return {"is_full_command": False, "data": data, "error": None}

            else:
                return {
                    "is_full_command": False,
                    "data": None,
                    "error": "Неверный формат данных",
                }

        except json.JSONDecodeError as e:
            return {
                "is_full_command": False,
                "data": None,
                "error": f"Ошибка декодирования JSON: {e}",
            }
        except Exception as e:
            return {"is_full_command": False, "data": None, "error": f"Ошибка: {e}"}


class BashViewerDialog(QDialog):
    """Read-only plain-text script preview with search and line numbers."""

    def __init__(self, parent=None, initial_text: str = None):
        super().__init__(parent)
        from src.ui.widgets.script_editor import ScriptEditor

        self.setSizeGripEnabled(True)
        self.setWindowTitle("Просмотр скрипта")
        self.setMinimumSize(700, 440)
        self.resize(960, 640)
        layout = QVBoxLayout(self)
        self.script_editor = ScriptEditor(self, read_only=True)
        # Только меню просмотра и навигации: «Поиск» и «Вид». Сочетания клавиш
        # скрытых меню продолжают работать, их действия добавлены в виджет.
        for menu in (self.script_editor.actions_menu, self.script_editor.settings_menu):
            menu.menuAction().setVisible(False)
        self.script_editor.replace_action.setVisible(False)
        self.editor = self.script_editor.editor
        self.highlighter = self.editor.highlighter
        self.editor.setPlainText(initial_text or "")
        layout.addWidget(self.script_editor)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.accept)
        layout.addWidget(self.button_box)
