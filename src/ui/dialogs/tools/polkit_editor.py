"""
Polkit Editor Dialog — Диалог для просмотра и редактирования политик Polkit на удалённом хосте.

Поддерживает:
- Загрузку политик из /usr/share/polkit-1/actions/ (.policy XML)
- 2 столбца: Список политик, Детали/редактирование
- Редактирование значений allow_any, allow_inactive, allow_active непосредственно в правой панели
- Прогресс загрузки
"""

import xml.etree.ElementTree as ET
from typing import Optional, Dict, Tuple

from PySide6.QtCore import Qt, QThread, Signal, QSortFilterProxyModel
from PySide6.QtGui import QStandardItemModel, QStandardItem, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QTreeView,
    QLabel, QPushButton, QMessageBox, QComboBox, QLineEdit,
    QHeaderView, QStatusBar, QWidget, QFormLayout, QGroupBox,
    QProgressBar, QScrollArea
)

from src.config import ICONS
from src.logger import logger
from src.domain.models.device import DeviceModel


class PolkitLoadThread(QThread):
    """Поток для загрузки политик Polkit с удалённого хоста"""
    result = Signal(dict, str)
    progress = Signal(int, str)

    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self._aborted = False

    def abort(self):
        self._aborted = True

    def run(self):
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()

            if self._aborted:
                return
            self.progress.emit(5, "Подключение к хосту...")
            client = worker.get_client(self.device)

            if self._aborted:
                client.close()
                return
            self.progress.emit(15, "Получение списка .policy файлов...")
            list_output, list_ok = self._exec_command(
                client,
                "find /usr/share/polkit-1/actions/ -name '*.policy' 2>/dev/null"
            )

            if self._aborted:
                client.close()
                return

            if not list_ok or not list_output.strip():
                client.close()
                if not self._aborted:
                    self.progress.emit(100, "Готово")
                    self.result.emit({}, "Нет .policy файлов на хосте")
                return

            file_list = [f.strip() for f in list_output.strip().splitlines() if f.strip()]
            total_files = len(file_list)

            if total_files == 0:
                client.close()
                if not self._aborted:
                    self.progress.emit(100, "Готово")
                    self.result.emit({}, "")
                return

            self.progress.emit(20, f"Найдено {total_files} файлов. Загрузка...")

            policies_data = {}

            for i, file_path in enumerate(file_list):
                if self._aborted:
                    client.close()
                    return
                pct = 20 + int((i / total_files) * 75)
                fname = file_path.rsplit('/', 1)[-1] if '/' in file_path else file_path
                self.progress.emit(pct, f"Чтение {fname} ({i + 1}/{total_files})...")

                xml_output, xml_ok = self._exec_command(client, f"cat '{file_path}'")
                if xml_ok and xml_output.strip():
                    try:
                        root = ET.fromstring(xml_output)
                        parsed = self._parse_policy_xml_root(root)
                        for action_id in parsed:
                            parsed[action_id]['_source_file'] = file_path
                        policies_data.update(parsed)
                    except ET.ParseError:
                        pass

            client.close()
            if self._aborted:
                return
            self.progress.emit(100, "Готово")
            self.result.emit(policies_data, "")
        except Exception as e:
            if self._aborted:
                return
            logger.error(f"PolkitLoadThread error: {e}")
            self.progress.emit(100, "Ошибка")
            self.result.emit({}, str(e))

    def _exec_command(self, client, command: str) -> Tuple[str, bool]:
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_status != 0 and not output.strip():
            return err, False
        return output, exit_status == 0

    def _parse_policy_xml_root(self, root: ET.Element) -> dict:
        policies = {}

        for action in root.iter('action'):
            action_id = action.get('id', '')
            if not action_id:
                continue

            description = ""
            message = ""

            for desc in action.findall('description'):
                lang = desc.get('{http://www.w3.org/XML/1998/namespace}lang', '')
                if lang in ('ru', 'en', ''):
                    description = desc.text or ""
                    if lang in ('ru', 'en'):
                        break

            for msg in action.findall('message'):
                lang = msg.get('{http://www.w3.org/XML/1998/namespace}lang', '')
                if lang in ('ru', 'en', ''):
                    message = msg.text or ""
                    if lang in ('ru', 'en'):
                        break

            defaults = {}
            for defaults_elem in action.findall('defaults'):
                for perm in ['allow_any', 'allow_inactive', 'allow_active']:
                    elem = defaults_elem.find(perm)
                    if elem is not None:
                        defaults[perm] = elem.text or ""

            annotate = {}
            for ann in action.findall('annotate'):
                key = ann.get('key', '')
                if key:
                    annotate[key] = ann.text or ""

            policies[action_id] = {
                'description': description,
                'message': message,
                'defaults': defaults,
                'annotations': annotate,
                'vendor': action.get('vendor', ''),
                'vendor_url': action.get('vendor_url', ''),
                'icon': action.get('icon_name', ''),
            }

        return policies


class PolkitSaveThread(QThread):
    """Поток для сохранения изменений политик на удалённом хосте"""
    result = Signal(bool, str)

    def __init__(self, device: DeviceModel, action_id: str, data: dict, parent=None):
        super().__init__(parent)
        self.device = device
        self.action_id = action_id
        self.data = data

    def run(self):
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device)

            success, msg = self._save_policy_override(client)

            client.close()
            self.result.emit(success, msg)
        except Exception as e:
            logger.error(f"PolkitSaveThread error: {e}")
            self.result.emit(False, str(e))

    def _save_policy_override(self, client) -> Tuple[bool, str]:
        source_file = self.data.get('_source_file')
        if not source_file:
            return False, "Не найден исходный .policy файл"

        defaults = self.data.get('defaults', {})
        new_values = {
            'allow_any': defaults.get('allow_any', 'auth_admin'),
            'allow_inactive': defaults.get('allow_inactive', 'auth_admin'),
            'allow_active': defaults.get('allow_active', 'auth_admin'),
        }

        xml_output, xml_ok = self._exec_command(client, f"sudo cat '{source_file}'")
        if not xml_ok or not xml_output.strip():
            return False, f"Не удалось прочитать {source_file}"

        try:
            root = ET.fromstring(xml_output)
        except ET.ParseError as e:
            return False, f"Ошибка разбора XML: {e}"

        found = False
        for action in root.iter('action'):
            if action.get('id') == self.action_id:
                found = True
                for defaults_elem in action.findall('defaults'):
                    for perm, new_val in new_values.items():
                        elem = defaults_elem.find(perm)
                        if elem is not None:
                            elem.text = new_val

        if not found:
            return False, f"Action {self.action_id} не найден в {source_file}"

        modified_xml = ET.tostring(root, encoding='unicode', xml_declaration=True)

        cmd = f"sudo tee '{source_file}' > /dev/null"
        stdin, stdout, stderr = client.exec_command(cmd)
        stdin.write(modified_xml)
        stdin.flush()
        stdin.channel.shutdown_write()
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            err = stderr.read().decode("utf-8", errors="replace")
            return False, f"Ошибка записи: {err}"

        return True, f"Политика {self.action_id} сохранена в {source_file}"

    def _exec_command(self, client, command: str) -> Tuple[str, bool]:
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_status != 0 and not output.strip():
            return err, False
        return output, exit_status == 0


PERMISSION_VALUES = {
    'yes': 'yes — Разрешить',
    'no': 'no — Запретить',
    'auth_admin': 'auth_admin — Аутентификация администратора',
    'auth_admin_keep': 'auth_admin_keep — Аутентификация админа (сохранённая)',
    'auth_self': 'auth_self — Аутентификация пользователя',
    'auth_self_keep': 'auth_self_keep — Аутентификация пользователя (сохранённая)',
}

PERM_KEYS = [
    ('allow_any', 'allow_any (любой пользователь)'),
    ('allow_inactive', 'allow_inactive (неактивная сессия)'),
    ('allow_active', 'allow_active (активная сессия)'),
]


class PolkitEditorDialog(QDialog):
    """Диалог для просмотра и редактирования политик Polkit на удалённом хосте"""

    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.policies_data: Dict[str, dict] = {}
        self._load_thread: Optional[PolkitLoadThread] = None
        self._save_thread: Optional[PolkitSaveThread] = None
        self._closing = False
        self._current_action_id: Optional[str] = None
        self.combo_boxes = {}

        self.setWindowTitle(f"Редактор политик Polkit — {device.name}")
        self.resize(900, 550)

        self._init_ui()
        self._load_policies()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        top_layout = QHBoxLayout()

        search_label = QLabel("🔍 Поиск:")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Фильтр по имени политики...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_policies)

        self.refresh_btn = QPushButton("🔄 Обновить")
        self.refresh_btn.clicked.connect(self._load_policies)

        top_layout.addWidget(search_label)
        top_layout.addWidget(self.search_edit, 1)
        top_layout.addWidget(self.refresh_btn)
        layout.addLayout(top_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Левый столбец: список политик
        policy_widget = QWidget()
        policy_layout = QVBoxLayout(policy_widget)
        policy_layout.setContentsMargins(0, 0, 0, 0)
        policy_layout.addWidget(QLabel("<b>Политики</b>"))

        self.policy_model = QStandardItemModel()
        self.policy_model.setHorizontalHeaderLabels(["Политика"])

        self.policy_proxy = QSortFilterProxyModel()
        self.policy_proxy.setSourceModel(self.policy_model)
        self.policy_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.policy_proxy.setFilterKeyColumn(0)

        self.policy_view = QTreeView()
        self.policy_view.setModel(self.policy_proxy)
        self.policy_view.setSortingEnabled(True)
        self.policy_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.policy_view.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.policy_view.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.policy_view.setUniformRowHeights(True)
        self.policy_view.header().setStretchLastSection(True)
        self.policy_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.policy_view.doubleClicked.connect(self._on_policy_double_clicked)
        self.policy_view.selectionModel().selectionChanged.connect(self._on_policy_selected)

        policy_layout.addWidget(self.policy_view)
        self.splitter.addWidget(policy_widget)

        # Правый столбец: детали и редактирование
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(4, 4, 4, 4)
        self.detail_layout.setSpacing(8)
        self.detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.placeholder_label = QLabel("Выберите политику из списка слева")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_layout.addWidget(self.placeholder_label)

        self.detail_scroll.setWidget(self.detail_container)
        self.splitter.addWidget(self.detail_scroll)

        self.splitter.setSizes([380, 720])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)

        layout.addWidget(self.splitter, 1)

        self.save_btn = QPushButton("💾 Сохранить")
        self.save_btn.clicked.connect(self._save_current_policy)
        self.save_btn.setMinimumHeight(36)
        self.save_btn.setVisible(False)
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        save_layout.addWidget(self.save_btn)
        layout.addLayout(save_layout)

        self.statusbar = QStatusBar()
        layout.addWidget(self.statusbar)

    def _clear_detail_widgets(self):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            w = item.widget()
            if w and w is not self.placeholder_label:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        self.detail_layout.setSpacing(8)

    def _clear_layout(self, layout):
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _show_policy_details(self, action_id: str):
        data = self.policies_data.get(action_id, {})
        self._current_action_id = action_id
        self.combo_boxes = {}

        self._clear_detail_widgets()
        self.placeholder_label.setVisible(False)

        title_label = QLabel(f"<h3>{action_id}</h3>")
        title_label.setWordWrap(True)
        self.detail_layout.addWidget(title_label)

        description = data.get('description', '')
        message = data.get('message', '')

        if description:
            desc_label = QLabel(f"<b>Описание:</b> {description}")
            desc_label.setWordWrap(True)
            desc_label.setMaximumHeight(60)
            self.detail_layout.addWidget(desc_label)

        if message:
            msg_label = QLabel(f"<b>Сообщение:</b> {message}")
            msg_label.setWordWrap(True)
            msg_label.setMaximumHeight(60)
            self.detail_layout.addWidget(msg_label)

        # Идентификатор
        id_label = QLabel(f"<b>Идентификатор:</b> <code>{action_id}</code>")
        id_label.setWordWrap(True)
        self.detail_layout.addWidget(id_label)

        # Поставщик
        vendor = data.get('vendor', '')
        vendor_url = data.get('vendor_url', '')
        if vendor or vendor_url:
            info_group = QGroupBox("Информация")
            info_layout = QFormLayout(info_group)
            if vendor:
                info_layout.addRow("Поставщик:", QLabel(vendor))
            if vendor_url:
                url_label = QLabel(f"<a href='{vendor_url}'>{vendor_url}</a>")
                url_label.setOpenExternalLinks(True)
                info_layout.addRow("URL:", url_label)
            self.detail_layout.addWidget(info_group)

        # Права доступа — редактируемые комбобоксы
        defaults = data.get('defaults', {})
        perm_group = QGroupBox("Права доступа")
        perm_layout = QFormLayout(perm_group)
        perm_layout.setSpacing(8)

        for key, label in PERM_KEYS:
            current_value = defaults.get(key, 'auth_admin')
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(20)

            for val, display in PERMISSION_VALUES.items():
                combo.addItem(display, val)

            idx = combo.findData(current_value)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(current_value, current_value)
                combo.setCurrentIndex(combo.count() - 1)

            self.combo_boxes[key] = combo
            perm_layout.addRow(label, combo)

        self.detail_layout.addWidget(perm_group)

        # Аннотации
        annotations = data.get('annotations', {})
        if annotations:
            ann_group = QGroupBox("Аннотации")
            ann_layout = QFormLayout(ann_group)
            for key, value in annotations.items():
                ann_layout.addRow(key, QLabel(value))
            self.detail_layout.addWidget(ann_group)

        self.save_btn.setVisible(True)

    def _save_current_policy(self):
        if not self._current_action_id or self._current_action_id not in self.policies_data:
            return

        data = dict(self.policies_data[self._current_action_id])
        new_defaults = dict(data.get('defaults', {}))

        for key, combo in self.combo_boxes.items():
            new_defaults[key] = combo.currentData()

        data['defaults'] = new_defaults
        self._save_policy(self._current_action_id, data)

    def _load_policies(self):
        if self._load_thread is not None:
            self._load_thread.abort()
            self._load_thread.wait(5000)
            self._load_thread = None

        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.progress_bar.setFormat("%p%")
        self.status_label.setText("Подключение...")
        self.refresh_btn.setEnabled(False)
        self.policy_model.clear()
        self._clear_detail_widgets()
        self.placeholder_label.setVisible(True)
        self._current_action_id = None
        self.combo_boxes = {}
        self.save_btn.setVisible(False)

        self._load_thread = PolkitLoadThread(self.device)
        self._load_thread.progress.connect(self._on_load_progress)
        self._load_thread.result.connect(self._on_policies_loaded)
        self._load_thread.start()

    def _on_load_progress(self, percent: int, message: str):
        if self._closing:
            return
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{percent}% — {message}")
        self.status_label.setText(message)

    def _on_policies_loaded(self, policies: dict, error: str):
        if self._closing:
            return
        self.progress_bar.setVisible(False)
        self.refresh_btn.setEnabled(True)

        if error:
            self.status_label.setText(f"Ошибка: {error}")
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось загрузить политики:\n{error}")
            return

        self.policies_data = policies
        self._populate_policy_list()
        count = len(policies)
        self.status_label.setText(f"Загружено политик: {count}")
        self.statusbar.showMessage(f"Политик: {count}", 5000)

    def _populate_policy_list(self):
        self.policy_model.clear()
        self.policy_model.setHorizontalHeaderLabels(["Политика"])

        for action_id in sorted(self.policies_data.keys()):
            name_item = QStandardItem(action_id)
            name_item.setData(action_id, Qt.ItemDataRole.UserRole)
            name_item.setEditable(False)
            name_item.setIcon(QIcon(ICONS.get('shield', ICONS.get('default', ''))))
            self.policy_model.appendRow([name_item])

    def _filter_policies(self, text: str):
        self.policy_proxy.setFilterFixedString(text)

    def _on_policy_selected(self, selected, deselected):
        indexes = selected.indexes()
        if not indexes:
            return

        proxy_index = indexes[0]
        source_index = self.policy_proxy.mapToSource(proxy_index)
        item = self.policy_model.itemFromIndex(source_index)
        if not item:
            return

        action_id = item.data(Qt.ItemDataRole.UserRole)
        if action_id in self.policies_data:
            self._show_policy_details(action_id)

    def _on_policy_double_clicked(self, index):
        source_index = self.policy_proxy.mapToSource(index)
        item = self.policy_model.itemFromIndex(source_index)
        if not item:
            return

        action_id = item.data(Qt.ItemDataRole.UserRole)
        if action_id in self.policies_data:
            self._show_policy_details(action_id)

    def _save_policy(self, action_id: str, new_data: dict):
        self.statusbar.showMessage(f"Сохранение политики {action_id}...")
        self.refresh_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.save_btn.setText("⏳ Сохранение...")

        self._save_thread = PolkitSaveThread(self.device, action_id, new_data)
        self._save_thread.result.connect(self._on_policy_saved)
        self._save_thread.start()

    def _on_policy_saved(self, success: bool, message: str):
        if self._closing:
            return
        self.refresh_btn.setEnabled(True)

        if self.save_btn:
            self.save_btn.setEnabled(True)
            self.save_btn.setText("💾 Сохранить")

        if success:
            self.statusbar.showMessage(message, 5000)
            action_id = self._save_thread.action_id if self._save_thread else None
            new_data = self._save_thread.data if self._save_thread else None
            if action_id and new_data:
                self.policies_data[action_id] = new_data
                if action_id == self._current_action_id:
                    self._show_policy_details(action_id)
            QMessageBox.information(self, "Успешно", message)
        else:
            self.statusbar.showMessage(f"Ошибка: {message}", 10000)
            QMessageBox.critical(self, "Ошибка сохранения", message)

    def closeEvent(self, event):
        self._closing = True

        if self._load_thread is not None:
            self._load_thread.abort()

        for thread in (self._load_thread, self._save_thread):
            if thread is not None and thread.isRunning():
                thread.wait(5000)

        event.accept()