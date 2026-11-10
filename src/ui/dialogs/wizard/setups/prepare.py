"""Шаг мастера «Подготовка устройств» (sid="remote_prepare").

Параметры сервиса «prepare»: вход для подготовки (заводская учётка с sudo),
учётные записи, пароль root (по галочке), открытые ключи root из ~/.ssh
(по галочке, выбираются на этом шаге), вход root по SSH и связка, которую
получат записи подготовленных хостов. Выполняется на шаге «Установка» первым сервисом каждого хоста.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.dialogs.wizard.setups.base import SetupPage, hint_label
from src.ui.widgets.bundle_selector import BundleSelector
from src.workers.setup.types import PrepareAccount, PreparePlan, validate_prepare

INHERIT_LABEL = "(наследовать глобальную)"

PERMIT_CHOICES = (
    ("prohibit-password", "только по ключу"),
    ("yes", "по ключу и паролю"),
)


class PrepareDevicesPage(SetupPage):
    """Параметры подготовки заводских устройств."""

    sid = "remote_prepare"

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                "Для устройств с заводским образом: вход под учётной записью с sudo "
                "(например, teacher), создание учётных записей, пароль и ключ root, "
                "снятие запрета входа root в /etc/ssh/denyusers.\n"
                "Подготовка выполняется на шаге «Установка» первой. Остальные "
                "сервисы хоста ставятся уже под связкой для подготовленных хостов; "
                "если подготовка не удалась, хост пропускается."
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # --- связки ---
        bundles_box = QGroupBox("Связки")
        bundles_form = QFormLayout(bundles_box)
        # То же значение, что «Связка по умолчанию» шага «SSH-ключи» (общее
        # ctx.ssh.bundle_iid): при первой настройке задать его больше негде.
        self._global_selector = BundleSelector(
            bundles_box, allow_none=True, none_label="(нет связки)"
        )
        self._global_selector.setToolTip(
            "Связка по умолчанию для всех хостов без своей связки. То же поле, что "
            "на шаге «SSH-ключи»; сохраняется при «Готово».\n"
            "Кнопка справа открывает окно связок."
        )
        bundles_form.addRow("Связка по умолчанию (глобальная):", self._global_selector)
        self._connect_selector = BundleSelector(
            bundles_box, allow_none=True, none_label=INHERIT_LABEL
        )
        self._connect_selector.setToolTip(
            "Заводская учётная запись с правами sudo. Пароль связки используется "
            "для входа и для sudo."
        )
        bundles_form.addRow("Вход для подготовки:", self._connect_selector)
        self._after_selector = BundleSelector(
            bundles_box, allow_none=True, none_label=INHERIT_LABEL
        )
        self._after_selector.setToolTip(
            "Связка, которую получат записи хостов. Назначается только хостам, "
            "где подготовка и проверочный вход с ней прошли.\n"
            "«Наследовать глобальную» - записи хостов остаются без своей связки "
            "и берут связку по умолчанию."
        )
        bundles_form.addRow("Связка для записей хостов:", self._after_selector)
        layout.addWidget(bundles_box)

        # --- учётные записи ---
        accounts_box = QGroupBox("Учётные записи")
        accounts_layout = QVBoxLayout(accounts_box)
        accounts_layout.addWidget(
            hint_label(
                "Новые создаются, у существующих меняется пароль. Флажок sudo "
                "добавляет в группу wheel (или sudo); снятый флажок из группы "
                "не исключает."
            )
        )
        self._accounts = QTableWidget(0, 3, accounts_box)
        self._accounts.setHorizontalHeaderLabels(["Логин", "Пароль", "sudo"])
        header = self._accounts.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._accounts.verticalHeader().setVisible(False)
        self._accounts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._accounts.setMinimumHeight(90)
        self._accounts.setMaximumHeight(160)
        accounts_layout.addWidget(self._accounts)
        buttons = QHBoxLayout()
        add_button = QPushButton("Добавить", accounts_box)
        add_button.clicked.connect(self._on_add_clicked)
        remove_button = QPushButton("Удалить", accounts_box)
        remove_button.clicked.connect(self._remove_account)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        self._show_passwords = QCheckBox("Показать пароли", accounts_box)
        self._show_passwords.toggled.connect(self._apply_echo)
        buttons.addWidget(self._show_passwords)
        accounts_layout.addLayout(buttons)
        layout.addWidget(accounts_box)

        # --- root ---
        root_box = QGroupBox("root")
        root_layout = QVBoxLayout(root_box)
        self._root_check = QCheckBox("Изменить пароль root", root_box)
        root_layout.addWidget(self._root_check)
        self._root_fields = QWidget(root_box)
        root_form = QFormLayout(self._root_fields)
        root_form.setContentsMargins(20, 0, 0, 0)
        self._root_password = self._password_edit(self._root_fields)
        self._root_confirm = self._password_edit(self._root_fields)
        root_form.addRow("Новый пароль:", self._root_password)
        root_form.addRow("Повтор пароля:", self._root_confirm)
        self._root_fields.setVisible(False)
        self._root_check.toggled.connect(self._root_fields.setVisible)
        root_layout.addWidget(self._root_fields)

        self._key_check = QCheckBox("Добавить ключи root", root_box)
        self._key_check.setToolTip(
            "Отмеченные открытые ключи добавляются в authorized_keys root."
        )
        root_layout.addWidget(self._key_check)
        self._keys_fields = QWidget(root_box)
        keys_layout = QVBoxLayout(self._keys_fields)
        keys_layout.setContentsMargins(20, 0, 0, 0)
        keys_layout.addWidget(
            hint_label(
                "Ключи из ~/.ssh этой машины. Создать или импортировать ключ "
                "можно на шаге «SSH-ключи»."
            )
        )
        self._keys = QListWidget(self._keys_fields)
        self._keys.setMinimumHeight(70)
        self._keys.setMaximumHeight(140)
        keys_layout.addWidget(self._keys)
        refresh_row = QHBoxLayout()
        refresh = QPushButton("Обновить список", self._keys_fields)
        refresh.clicked.connect(self._on_refresh_keys)
        refresh_row.addWidget(refresh)
        refresh_row.addStretch()
        keys_layout.addLayout(refresh_row)
        self._keys_fields.setVisible(False)
        self._key_check.toggled.connect(self._keys_fields.setVisible)
        root_layout.addWidget(self._keys_fields)

        permit_row = QHBoxLayout()
        self._permit_check = QCheckBox("Разрешить вход root по SSH:", root_box)
        self._permit_check.setToolTip(
            "PermitRootLogin в /etc/ssh/sshd_config и root из DenyUsers. "
            "Конфигурация проверяется sshd -T, при ошибке возвращается прежняя."
        )
        self._permit_combo = QComboBox(root_box)
        for value, label in PERMIT_CHOICES:
            self._permit_combo.addItem(label, value)
        self._permit_combo.setEnabled(False)
        self._permit_check.toggled.connect(self._permit_combo.setEnabled)
        permit_row.addWidget(self._permit_check)
        permit_row.addWidget(self._permit_combo)
        permit_row.addStretch()
        root_layout.addLayout(permit_row)
        layout.addWidget(root_box)
        layout.addStretch()

        self._ctx = None
        self._global_selector.changed.connect(self._on_global_changed)
        self._key_error = ""

    # --- учётные записи ---

    def _password_edit(self, parent) -> QLineEdit:
        edit = QLineEdit(parent)
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal
            if getattr(self, "_show_passwords", None) is not None
            and self._show_passwords.isChecked()
            else QLineEdit.EchoMode.Password
        )
        return edit

    def _on_add_clicked(self) -> None:
        self._add_account()

    def _add_account(self, login="", password="", admin=False) -> None:
        row = self._accounts.rowCount()
        self._accounts.insertRow(row)
        self._accounts.setItem(row, 0, QTableWidgetItem(login))
        edit = self._password_edit(self._accounts)
        edit.setText(password)
        self._accounts.setCellWidget(row, 1, edit)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        check.setCheckState(Qt.CheckState.Checked if admin else Qt.CheckState.Unchecked)
        self._accounts.setItem(row, 2, check)

    def _remove_account(self) -> None:
        row = self._accounts.currentRow()
        if row >= 0:
            self._accounts.removeRow(row)

    def _account_rows(self) -> list:
        rows = []
        for row in range(self._accounts.rowCount()):
            login_item = self._accounts.item(row, 0)
            edit = self._accounts.cellWidget(row, 1)
            check = self._accounts.item(row, 2)
            login = (login_item.text() if login_item else "").strip()
            password = edit.text() if edit is not None else ""
            if not login and not password:
                continue
            rows.append(
                PrepareAccount(
                    login,
                    password,
                    check is not None and check.checkState() == Qt.CheckState.Checked,
                )
            )
        return rows

    def _password_edits(self) -> list:
        edits = [self._root_password, self._root_confirm]
        for row in range(self._accounts.rowCount()):
            edit = self._accounts.cellWidget(row, 1)
            if edit is not None:
                edits.append(edit)
        return edits

    def _apply_echo(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        for edit in self._password_edits():
            edit.setEchoMode(mode)

    # --- ключ и связки ---

    def _reload_keys(self) -> None:
        """Перечитать ~/.ssh, сохранив отметки по пути к файлу."""
        from src.ui.dialogs.common.ssh_manage import scan_ssh_keys

        checked = set(self._checked_key_paths())
        self._keys.clear()
        for key in scan_ssh_keys():
            if not key["has_pub"]:
                continue
            item = QListWidgetItem(f"{key['name']} ({key['type']})")
            item.setData(Qt.ItemDataRole.UserRole, key["pub_path"])
            item.setToolTip(key["pub_path"])
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(
                Qt.CheckState.Checked
                if key["pub_path"] in checked
                else Qt.CheckState.Unchecked
            )
            self._keys.addItem(item)

    def _on_refresh_keys(self) -> None:
        self._reload_keys()

    def _checked_key_paths(self) -> list:
        return [
            self._keys.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._keys.count())
            if self._keys.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _read_keys(self) -> tuple:
        keys = []
        for path in self._checked_key_paths():
            try:
                with open(path, encoding="utf-8") as stream:
                    lines = [line.strip() for line in stream if line.strip()]
            except OSError as error:
                raise ValueError(f"Не удалось прочитать {path}: {error}") from error
            if len(lines) != 1:
                raise ValueError(f"В файле {path} должен быть один открытый ключ")
            keys.append(lines[0])
        return tuple(dict.fromkeys(keys))

    def _on_global_changed(self, iid) -> None:
        # Сразу в общее состояние: шаг «SSH-ключи» покажет то же значение.
        if self._ctx is not None:
            self._ctx.ssh.bundle_iid = iid or ""
            self._update_inherit_labels(self._ctx)

    @staticmethod
    def _global_bundle_iid(ctx) -> str:
        """Связка по умолчанию мастера (общая с шагом «SSH-ключи»)."""
        return ctx.ssh.bundle_iid or ""

    def _update_inherit_labels(self, ctx) -> None:
        """В пункте наследования видно, какая связка глобальная."""
        default_iid = self._global_bundle_iid(ctx)
        bundle = next((b for b in ctx.credentials.bundles if b.iid == default_iid), None)
        name = (bundle.description or bundle.login) if bundle else "не задана"
        for selector in (self._connect_selector, self._after_selector):
            selector.set_none_label(f"(наследовать глобальную: {name})")

    # --- контракт SetupPage ---

    def on_enter(self, ctx) -> None:
        self._ctx = ctx
        for selector in (self._global_selector, self._connect_selector, self._after_selector):
            selector.refresh()
        self._global_selector.set_current_iid(ctx.ssh.bundle_iid or None)
        self._update_inherit_labels(ctx)
        self._reload_keys()

    def collect(self, ctx) -> None:
        self._key_error = ""
        keys = ()
        if self._key_check.isChecked():
            try:
                keys = self._read_keys()
            except ValueError as error:
                self._key_error = str(error)
        ctx.prepare = PreparePlan(
            connect_bundle_iid=self._connect_selector.current_iid() or "",
            accounts=tuple(self._account_rows()),
            root_password=(
                self._root_password.text() if self._root_check.isChecked() else ""
            ),
            root_keys=keys,
            permit_root=(
                self._permit_combo.currentData() if self._permit_check.isChecked() else ""
            ),
            after_bundle_iid=self._after_selector.current_iid() or "",
        )

    def validate(self, ctx) -> tuple:
        if not ctx.services.get("prepare"):
            return True, ""
        if self._root_check.isChecked():
            if not self._root_password.text():
                return False, "Введите новый пароль root или снимите галочку"
            if self._root_password.text() != self._root_confirm.text():
                return False, "Пароль root и его повтор не совпадают"
        if self._key_check.isChecked():
            if self._key_error:
                return False, self._key_error
            if not ctx.prepare.root_keys:
                return False, "Отметьте ключи root или снимите галочку «Добавить ключи root»"
        bundles = {b.iid: (b.login, b.password) for b in ctx.credentials.bundles}
        default_iid = self._global_bundle_iid(ctx)
        try:
            validate_prepare(ctx.prepare, bundles, default_iid)
        except ValueError as error:
            return False, str(error)
        after = bundles[ctx.prepare.after_bundle_iid or default_iid]
        if (after[0] or "root") == "root" and not ctx.prepare.permit_root:
            return True, (
                "Связка после подготовки входит под root, а вход root по SSH на "
                "этом шаге не разрешается: если PermitRootLogin на хостах "
                "запрещает его, проверочный вход не пройдёт и хосты будут пропущены."
            )
        return True, ""

    def summary_html(self, ctx) -> str:
        from html import escape

        plan = ctx.prepare
        names = {b.iid: b.description or b.login for b in ctx.credentials.bundles}
        names[""] = "наследовать глобальную: " + names.get(ctx.ssh.bundle_iid, "не задана")
        accounts = ", ".join(
            escape(a.login) + (" (sudo)" if a.admin else "") for a in plan.accounts
        ) or "нет"
        permit = dict(PERMIT_CHOICES).get(plan.permit_root, "не меняется")
        text = (
            f"вход «{escape(names.get(plan.connect_bundle_iid, '—'))}»; "
            f"учётные записи: {accounts}; "
            f"пароль root: {'меняется' if plan.root_password else 'не меняется'}; "
            f"ключей root: {len(plan.root_keys)}; "
            f"вход root по SSH: {permit}; "
            f"связка после: «{escape(names.get(plan.after_bundle_iid, '—'))}»"
        )
        prepared = len(ctx.install_result.bundle_assignments)
        if prepared:
            text += f". Записям {prepared} хост(ов) будет назначена связка при сохранении"
        return text


