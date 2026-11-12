"""Шаг мастера «Подготовка устройств» (sid="remote_prepare").

Параметры сервиса «prepare»: вход для подготовки (заводская учётка с sudo),
учётные записи, пароль root (по галочке), открытые ключи из ~/.ssh для
выбранных учётных записей (по галочке: root, из связок, из таблицы), вход root
по SSH и связка, которую получат записи подготовленных хостов. Выполняется на шаге «Установка» первым сервисом каждого хоста.
"""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
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


class _CenteredCheckDelegate(QStyledItemDelegate):
    """Флажок по центру ячейки (у стандартного делегата он прижат влево).

    Щелчок в любом месте ячейки или пробел переключает флажок через модель:
    itemChanged таблицы срабатывает как обычно.
    """

    @staticmethod
    def _checked(index) -> bool:
        value = index.data(Qt.ItemDataRole.CheckStateRole)
        try:
            return Qt.CheckState(value) == Qt.CheckState.Checked
        except (TypeError, ValueError):
            return False

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        # Фон и выделение - без своего флажка и текста.
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        check = QStyleOptionButton()
        check.rect = self._indicator_rect(style, option, widget)
        check.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if self._checked(index) else QStyle.StateFlag.State_Off
        )
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, check, painter, widget)

    @staticmethod
    def _indicator_rect(style, option, widget):
        size = style.subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, QStyleOptionButton(), widget
        ).size()
        rect = QRect(QPoint(0, 0), size)
        rect.moveCenter(option.rect.center())
        return rect

    def editorEvent(self, event, model, option, index):
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonDblClick:
            return True
        toggle = (
            kind == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        ) or (
            kind == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select)
        )
        if not toggle:
            return kind == QEvent.Type.MouseButtonPress
        model.setData(
            index,
            Qt.CheckState.Unchecked if self._checked(index) else Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
        return True


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
                "(например, teacher), создание учётных записей, пароль root, "
                "открытые ключи для входа, разрешение входа root по SSH.\n"
                "Подготовка выполняется на шаге «Установка» первой, под учётной "
                "записью не root - всегда через sudo: ей нужны права root. Остальные "
                "сервисы хоста ставятся уже с данными для входа после подготовки; "
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
        bundles_box = QGroupBox("Подключение к хостам")
        bundles_form = QFormLayout(bundles_box)
        self._connect_selector = BundleSelector(
            bundles_box, allow_none=True, none_label=INHERIT_LABEL
        )
        self._connect_selector.setToolTip(
            "Заводская учётная запись с правами sudo (например, teacher): под ней "
            "выполняется подготовка, не под root - всегда через sudo. Пароль связки "
            "используется для входа и для sudo.\n"
            "«Как у хоста» - связка из колонки «Данные для входа» шага «Хосты», у "
            "хоста без своей связки - связка по умолчанию."
        )
        bundles_form.addRow("Данные для входа при подготовке:", self._connect_selector)
        # Связка «после»: обычно это связка по умолчанию шага «Хосты» (видна
        # строкой); своя выбирается только с галочкой «Другая».
        after_row = QWidget(bundles_box)
        after_layout = QHBoxLayout(after_row)
        after_layout.setContentsMargins(0, 0, 0, 0)
        self._after_default_label = QLabel(after_row)
        self._after_default_label.setToolTip(
            "Под ней проверяется вход и ставятся остальные сервисы. Подготовленные "
            "хосты остаются без своей связки и берут связку по умолчанию шага «Хосты»."
        )
        after_layout.addWidget(self._after_default_label, 1)
        self._after_selector = BundleSelector(after_row, allow_none=False)
        self._after_selector.setToolTip(
            "Под ней проверяется вход и ставятся остальные сервисы; при «Готово» она "
            "записывается подготовленным хостам в колонку «Данные для входа» - только "
            "тем, где "
            "подготовка и проверочный вход прошли.\n"
            "Если такой учётной записи нет на хосте, нажмите «Добавить» под таблицей "
            "учётных записей: строка добавится с логином и паролем этой связки."
        )
        self._after_selector.setVisible(False)
        after_layout.addWidget(self._after_selector, 1)
        self._after_other = QCheckBox("Другая", after_row)
        self._after_other.setToolTip(
            "Выбрать для входа после подготовки связку, отличную от связки по умолчанию"
        )
        after_layout.addWidget(self._after_other)
        bundles_form.addRow("Данные для входа после подготовки:", after_row)
        # Под выбранной связкой «после»; видна только с галочкой «Другая».
        self._after_default = QCheckBox(
            "Использовать эти данные в дальнейшем по умолчанию", bundles_box
        )
        self._after_default.setToolTip(
            "При «Готово» эта связка станет связкой по умолчанию программы, "
            "подготовленные хосты будут её наследовать (своя связка хоста снимается).\n"
            "Подключение на шаге «Установка» это не меняет: до сохранения связка по "
            "умолчанию остаётся той, что выбрана на шаге «Хосты»."
        )
        bundles_form.addRow("", self._after_default)
        bundles_form.setRowVisible(self._after_default, False)
        # Выбор пользователя: галочка неактивна, если связка уже по умолчанию.
        self._after_default_wanted = False
        self._bundles_form = bundles_form
        layout.addWidget(bundles_box)

        # --- учётные записи ---
        accounts_box = QGroupBox("Учётные записи")
        accounts_layout = QVBoxLayout(accounts_box)
        accounts_layout.addWidget(
            hint_label(
                "Новые создаются, у существующих меняется пароль. Флажок sudo "
                "добавляет в группу wheel (или sudo), «без пароля» - вдобавок sudo "
                "без запроса пароля (/etc/sudoers.d). Снятые флажки права не "
                "отбирают."
            )
        )
        self._accounts = QTableWidget(0, 4, accounts_box)
        self._accounts.setHorizontalHeaderLabels(
            ["Логин", "Пароль", "sudo", "без пароля"]
        )
        self._accounts.horizontalHeaderItem(3).setToolTip(
            "sudo не спрашивает пароль: связке этой учётной записи пароль не нужен, "
            "если вход по ключу"
        )
        header = self._accounts.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._check_delegate = _CenteredCheckDelegate(self._accounts)
        for column in (2, 3):
            self._accounts.setItemDelegateForColumn(column, self._check_delegate)
        self._accounts.verticalHeader().setVisible(False)
        self._accounts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._accounts.setMinimumHeight(80)
        self._accounts.setMaximumHeight(107)
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

        # --- открытые ключи: какие ключи и каким учётным записям ---
        self._key_check = QCheckBox("Добавить открытые ключи", body)
        self._key_check.setToolTip(
            "Отмеченные открытые ключи добавляются в authorized_keys отмеченных "
            "учётных записей: после этого под ними можно входить по ключу."
        )
        layout.addWidget(self._key_check)
        self._keys_fields = QWidget(body)
        keys_layout = QVBoxLayout(self._keys_fields)
        # Правый отступ выравнивается по таблице учётных записей в
        # _align_keys_margin: у неё зазор до края задаёт рамка группы.
        keys_layout.setContentsMargins(20, 0, 0, 0)
        self._keys_layout = keys_layout
        keys_layout.addWidget(
            hint_label(
                "Ключи из ~/.ssh этой машины; программа входит на хосты ключом, "
                "выбранным на шаге «SSH-ключи». Учётные записи - root, из связок "
                "и из таблицы выше; новые получают ключи сразу после создания."
            )
        )
        lists_row = QHBoxLayout()
        keys_column = QVBoxLayout()
        keys_column.addWidget(QLabel("Ключи:", self._keys_fields))
        self._keys = QListWidget(self._keys_fields)
        self._keys.setMinimumHeight(60)
        self._keys.setMaximumHeight(90)
        keys_column.addWidget(self._keys)
        lists_row.addLayout(keys_column, 1)
        users_column = QVBoxLayout()
        users_column.addWidget(QLabel("Учётные записи:", self._keys_fields))
        self._key_users = QListWidget(self._keys_fields)
        self._key_users.setMinimumHeight(60)
        self._key_users.setMaximumHeight(90)
        users_column.addWidget(self._key_users)
        lists_row.addLayout(users_column, 1)
        keys_layout.addLayout(lists_row)
        refresh_row = QHBoxLayout()
        refresh = QPushButton("Обновить список ключей", self._keys_fields)
        refresh.clicked.connect(self._on_refresh_keys)
        refresh_row.addWidget(refresh)
        refresh_row.addStretch()
        keys_layout.addLayout(refresh_row)
        self._keys_fields.setVisible(False)
        self._key_check.toggled.connect(self._keys_fields.setVisible)
        layout.addWidget(self._keys_fields)

        # Отзыв прав у заводской учётки (например, teacher): исключение из
        # групп wheel и sudo последним шагом подготовки.
        self._revoke_check = QCheckBox(body)
        self._revoke_check.setToolTip(
            "Учётная запись входа для подготовки исключается из групп wheel и sudo "
            "последним шагом подготовки. Если права выданы записью в /etc/sudoers "
            "или /etc/sudoers.d, отчёт хоста предупредит об этом."
        )
        layout.addWidget(self._revoke_check)

        # --- root: отдельные галочки, без своей группы (экономия места) ---
        self._root_check = QCheckBox("Изменить пароль root", body)
        layout.addWidget(self._root_check)
        self._root_fields = QWidget(body)
        root_form = QFormLayout(self._root_fields)
        root_form.setContentsMargins(20, 0, 0, 0)
        self._root_password = self._password_edit(self._root_fields)
        self._root_confirm = self._password_edit(self._root_fields)
        root_form.addRow("Новый пароль:", self._root_password)
        root_form.addRow("Повтор пароля:", self._root_confirm)
        self._root_fields.setVisible(False)
        self._root_check.toggled.connect(self._root_fields.setVisible)
        layout.addWidget(self._root_fields)

        permit_row = QHBoxLayout()
        self._permit_check = QCheckBox("Разрешить вход root по SSH:", body)
        self._permit_check.setToolTip(
            "PermitRootLogin в /etc/ssh/sshd_config, root убирается из DenyUsers и "
            "из /etc/ssh/denyusers. Конфигурация проверяется sshd -T, при ошибке "
            "возвращается прежняя, denyusers тогда не меняется."
        )
        self._permit_combo = QComboBox(body)
        for value, label in PERMIT_CHOICES:
            self._permit_combo.addItem(label, value)
        self._permit_combo.setEnabled(False)
        self._permit_check.toggled.connect(self._permit_combo.setEnabled)
        permit_row.addWidget(self._permit_check)
        permit_row.addWidget(self._permit_combo)
        permit_row.addStretch()
        layout.addLayout(permit_row)

        layout.addStretch()

        self._ctx = None
        # Отметки учётных записей для ключей - по логину, переживают пересборку.
        self._key_user_marks = {"root": True}
        self._default_key_marked = False
        self._connect_selector.changed.connect(self._on_connect_changed)
        self._after_selector.changed.connect(self._on_after_changed)
        self._after_other.toggled.connect(self._on_after_other_toggled)
        self._after_default.toggled.connect(self._on_after_default_toggled)
        self._accounts.itemChanged.connect(self._on_account_item_changed)
        self._key_users.itemChanged.connect(self._on_key_user_changed)
        self._update_revoke_label("")
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
        """Добавить строку; учётная запись связки «после» - готовой строкой.

        Пока её логина (не root) нет в таблице, «Добавить» вставляет его с
        паролем связки и флажками: sudo - всегда (остальные сервисы идут под
        ней через sudo), «без пароля» - если в связке нет пароля (иначе sudo
        спросил бы пароль, которого нет). Дальше - пустые строки.
        """
        after = self._after_bundle()
        if after is not None:
            login = after.login or "root"
            present = {account.login for account in self._account_rows()}
            if login != "root" and login not in present:
                self._add_account(login, after.password or "", True, not after.password)
                return
        self._add_account()

    def _after_bundle(self):
        """Связка «после» (пустой выбор - глобальная); None - связки нет."""
        ctx = self._ctx
        if ctx is None or ctx.credentials is None:
            return None
        iid = self._after_iid() or self._global_bundle_iid(ctx)
        return next((b for b in ctx.credentials.bundles if b.iid == iid), None)

    def _after_iid(self) -> str:
        """Связка «после»; "" - связка по умолчанию (галочка «Другая» снята)."""
        if not self._after_other.isChecked():
            return ""
        return self._after_selector.current_iid() or ""

    def _add_account(self, login="", password="", admin=False, nopasswd=False) -> None:
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
        free = QTableWidgetItem()
        free.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        free.setCheckState(
            Qt.CheckState.Checked if nopasswd and admin else Qt.CheckState.Unchecked
        )
        self._accounts.setItem(row, 3, free)
        self._rebuild_key_users()

    def _on_account_item_changed(self, item) -> None:
        # «Без пароля» имеет смысл только с sudo: флажки держатся согласованно.
        column, row = item.column(), item.row()
        checked = item.checkState() == Qt.CheckState.Checked
        other = self._accounts.item(row, 2 if column == 3 else 3)
        if column == 3 and checked and other is not None:
            other.setCheckState(Qt.CheckState.Checked)
        elif column == 2 and not checked and other is not None:
            other.setCheckState(Qt.CheckState.Unchecked)
        if column == 0:
            self._rebuild_key_users()

    def _remove_account(self) -> None:
        row = self._accounts.currentRow()
        if row >= 0:
            self._accounts.removeRow(row)
            self._rebuild_key_users()

    def _account_rows(self) -> list:
        rows = []
        for row in range(self._accounts.rowCount()):
            login_item = self._accounts.item(row, 0)
            edit = self._accounts.cellWidget(row, 1)
            check = self._accounts.item(row, 2)
            free = self._accounts.item(row, 3)
            login = (login_item.text() if login_item else "").strip()
            password = edit.text() if edit is not None else ""
            if not login and not password:
                continue
            rows.append(
                PrepareAccount(
                    login,
                    password,
                    check is not None and check.checkState() == Qt.CheckState.Checked,
                    free is not None and free.checkState() == Qt.CheckState.Checked,
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
        """Перечитать ~/.ssh, сохранив отметки по пути к файлу.

        При первом заполнении отмечен ключ шага «SSH-ключи»: им программа
        входит на хосты.
        """
        import os

        from src.ui.dialogs.common.ssh_manage import scan_ssh_keys

        checked = {os.path.normcase(os.path.abspath(p)) for p in self._checked_key_paths()}
        if not self._default_key_marked and self._ctx is not None:
            private = getattr(self._ctx.ssh, "private_key_path", "") or ""
            if private:
                checked.add(os.path.normcase(os.path.abspath(private + ".pub")))
                self._default_key_marked = True
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
                if os.path.normcase(os.path.abspath(key["pub_path"])) in checked
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

    def _bundle_login(self, ctx, iid) -> str:
        """Логин связки (пустой iid - связка по умолчанию); "" - связки нет."""
        iid = iid or self._global_bundle_iid(ctx)
        bundle = next((b for b in ctx.credentials.bundles if b.iid == iid), None)
        return (bundle.login or "root") if bundle else ""

    def _key_user_entries(self) -> list:
        """[(логин, роли)] для списка учётных записей под ключи, без повторов."""
        roles = {"root": []}
        ctx = self._ctx
        if ctx is not None and ctx.credentials is not None:
            for login, role in (
                (self._bundle_login(ctx, self._connect_selector.current_iid()),
                 "вход при подготовке"),
                (self._bundle_login(ctx, self._after_iid()),
                 "вход после подготовки"),
            ):
                if login:
                    roles.setdefault(login, []).append(role)
        for row in range(self._accounts.rowCount()):
            item = self._accounts.item(row, 0)
            login = (item.text() if item else "").strip()
            if login:
                roles.setdefault(login, []).append("из таблицы")
        return list(roles.items())

    def _rebuild_key_users(self) -> None:
        """Пересобрать список учётных записей для ключей, сохранив отметки."""
        self._key_users.blockSignals(True)
        try:
            self._key_users.clear()
            for login, roles in self._key_user_entries():
                text = login + (f" ({', '.join(dict.fromkeys(roles))})" if roles else "")
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, login)
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if self._key_user_marks.get(login)
                    else Qt.CheckState.Unchecked
                )
                self._key_users.addItem(item)
        finally:
            self._key_users.blockSignals(False)

    def _on_key_user_changed(self, item) -> None:
        login = item.data(Qt.ItemDataRole.UserRole)
        self._key_user_marks[login] = item.checkState() == Qt.CheckState.Checked

    def _checked_key_users(self) -> list:
        return [
            self._key_users.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._key_users.count())
            if self._key_users.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _on_after_changed(self, _iid) -> None:
        self._update_after_default()
        self._rebuild_key_users()

    def _on_after_other_toggled(self, checked: bool) -> None:
        self._after_default_label.setVisible(not checked)
        self._after_selector.setVisible(checked)
        self._on_after_changed(self._after_iid())

    def _on_after_default_toggled(self, checked: bool) -> None:
        if self._after_default.isEnabled():
            self._after_default_wanted = checked

    def _update_after_default(self) -> None:
        """«Использовать эту связку в дальнейшем по умолчанию».

        Видна только с выбранной своей связкой «после»; если она и так
        связка по умолчанию, галочка отмечена и неактивна.
        """
        iid = self._after_iid()
        self._bundles_form.setRowVisible(self._after_default, bool(iid))
        already = bool(iid) and self._ctx is not None and iid == self._global_bundle_iid(
            self._ctx
        )
        self._after_default.blockSignals(True)
        try:
            self._after_default.setEnabled(not already)
            self._after_default.setChecked(already or self._after_default_wanted)
        finally:
            self._after_default.blockSignals(False)

    def _on_connect_changed(self, _iid) -> None:
        if self._ctx is not None:
            self._update_revoke_label(self._connect_login(self._ctx))
        self._rebuild_key_users()

    def _connect_login(self, ctx) -> str:
        """Логин явно выбранной связки для подключения; "" - у каждого хоста своя."""
        iid = self._connect_selector.current_iid()
        bundle = next((b for b in ctx.credentials.bundles if b.iid == iid), None)
        return (bundle.login or "root") if bundle else ""

    def _update_revoke_label(self, login: str) -> None:
        self._revoke_check.setText(
            "Отозвать права администратора у учётной записи подключения"
            + (f" ({login})" if login else " (из данных для входа каждого хоста)")
        )

    @staticmethod
    def _global_bundle_iid(ctx) -> str:
        """Связка по умолчанию мастера (задаётся на шаге «Хосты»)."""
        return ctx.default_bundle_iid or ""

    @staticmethod
    def _global_name(ctx) -> str:
        default_iid = ctx.default_bundle_iid or ""
        bundle = next((b for b in ctx.credentials.bundles if b.iid == default_iid), None)
        return (bundle.description or bundle.login) if bundle else "не задана"

    def _update_inherit_labels(self, ctx) -> None:
        """В пунктах наследования видно, какая связка глобальная."""
        name = self._global_name(ctx)
        self._connect_selector.set_none_label(f"(как у хоста, иначе по умолчанию: {name})")
        self._after_default_label.setText(
            f"по умолчанию: «{name}»"
            if self._global_bundle_iid(ctx)
            else "по умолчанию не заданы"
        )

    @staticmethod
    def _hosts_without_bundle(ctx) -> int:
        """Хосты шага «Хосты» без своей (существующей) связки."""
        known = {b.iid for b in ctx.credentials.bundles}
        count = 0

        def walk(nodes):
            nonlocal count
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if "host" in node:
                    if node.get("credential_bundle_iid") not in known:
                        count += 1
                elif len(node) == 1 and isinstance(next(iter(node.values())), list):
                    walk(next(iter(node.values())))

        walk(ctx.hosts_data or [])
        return count

    # --- контракт SetupPage ---

    def _align_keys_margin(self) -> None:
        """Правый край списков ключей - как у таблицы учётных записей."""
        box = self._accounts.parentWidget()
        if box is None or not box.isVisible():
            return
        gap = box.width() - (self._accounts.geometry().right() + 1)
        margins = self._keys_layout.contentsMargins()
        if gap >= 0 and margins.right() != gap:
            margins.setRight(gap)
            self._keys_layout.setContentsMargins(margins)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._align_keys_margin)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._align_keys_margin()

    def on_enter(self, ctx) -> None:
        self._ctx = ctx
        for selector in (self._connect_selector, self._after_selector):
            selector.refresh()
        self._update_inherit_labels(ctx)
        self._update_revoke_label(self._connect_login(ctx))
        self._update_after_default()
        self._rebuild_key_users()
        self._reload_keys()

    def collect(self, ctx) -> None:
        self._key_error = ""
        keys, key_users = (), ()
        if self._key_check.isChecked():
            key_users = tuple(self._checked_key_users())
            try:
                keys = self._read_keys()
            except ValueError as error:
                self._key_error = str(error)
        after_iid = self._after_iid()
        ctx.prepare = PreparePlan(
            connect_bundle_iid=self._connect_selector.current_iid() or "",
            accounts=tuple(self._account_rows()),
            root_password=(
                self._root_password.text() if self._root_check.isChecked() else ""
            ),
            keys=keys,
            key_users=key_users,
            permit_root=(
                self._permit_combo.currentData() if self._permit_check.isChecked() else ""
            ),
            after_bundle_iid=after_iid,
            revoke_admin=self._revoke_check.isChecked(),
            # Уже связка по умолчанию - менять нечего.
            after_as_default=(
                bool(after_iid)
                and after_iid != self._global_bundle_iid(ctx)
                and self._after_default.isChecked()
            ),
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
            if not ctx.prepare.keys:
                return False, "Отметьте ключи или снимите галочку «Добавить открытые ключи»"
            if not ctx.prepare.key_users:
                return False, (
                    "Отметьте учётные записи для ключей или снимите галочку "
                    "«Добавить открытые ключи»"
                )
        bundles = {b.iid: (b.login, b.password) for b in ctx.credentials.bundles}
        default_iid = self._global_bundle_iid(ctx)
        try:
            validate_prepare(ctx.prepare, bundles, default_iid)
        except ValueError as error:
            return False, str(error)
        warnings = []
        if not ctx.prepare.connect_bundle_iid and default_iid not in bundles:
            missing = self._hosts_without_bundle(ctx)
            if missing:
                warnings.append(
                    "Данные для входа при подготовке берутся у хоста, а данные для входа по "
                    f"умолчанию не заданы: {missing} хост(ов) без своей связки "
                    "будут пропущены."
                )
        after = bundles[ctx.prepare.after_bundle_iid or default_iid]
        after_login = after[0] or "root"
        if ctx.prepare.revoke_admin and after_login != "root" and not any(
            a.login == after_login and a.admin for a in ctx.prepare.accounts
        ):
            who = self._connect_login(ctx) or "учётной записи входа"
            warnings.append(
                f"Права администратора у «{who}» отзываются, а "
                f"после подготовки вход идёт под «{after_login}»: её нет среди "
                "учётных записей с флажком sudo. Если у неё на хостах нет прав sudo, "
                "остальные сервисы не установятся."
            )
        if after_login != "root" and not after[1]:
            # Связка «после» без пароля: вход только по ключу, sudo - только
            # если на хостах он без пароля (NOPASSWD).
            if not (ctx.prepare.keys and after_login in ctx.prepare.key_users):
                warnings.append(
                    f"В данных для входа после подготовки («{after_login}») нет пароля: вход "
                    f"возможен только по ключу. Отметьте «{after_login}» в списке "
                    "учётных записей для ключей, если ключа на хостах ещё нет."
                )
            if not any(
                a.login == after_login and a.nopasswd for a in ctx.prepare.accounts
            ):
                warnings.append(
                    f"sudo под «{after_login}» спросит пароль, а в связке его нет: "
                    "отметьте у неё в таблице «без пароля», если на хостах sudo ещё "
                    "не настроен без пароля - иначе остальные сервисы не установятся."
                )
        if after_login == "root" and not ctx.prepare.permit_root:
            warnings.append(
                "После подготовки вход идёт под root, а вход root по SSH на "
                "этом шаге не разрешается: если на хостах root указан в "
                "/etc/ssh/denyusers или PermitRootLogin запрещает его, проверочный "
                "вход не пройдёт и хосты будут пропущены."
            )
        return True, "\n\n".join(warnings)

    def summary_html(self, ctx) -> str:
        from html import escape

        plan = ctx.prepare
        names = {b.iid: b.description or b.login for b in ctx.credentials.bundles}
        global_name = self._global_name(ctx)
        accounts = ", ".join(
            escape(a.login)
            + (" (sudo без пароля)" if a.nopasswd else " (sudo)" if a.admin else "")
            for a in plan.accounts
        ) or "нет"
        permit = dict(PERMIT_CHOICES).get(plan.permit_root, "не меняется")
        connect = (
            names.get(plan.connect_bundle_iid, "-")
            if plan.connect_bundle_iid
            else "как у хоста, иначе по умолчанию: " + global_name
        )
        after = (
            names.get(plan.after_bundle_iid, "-")
            if plan.after_bundle_iid
            else "по умолчанию: " + global_name
        )
        who = self._connect_login(ctx) or "учётной записи входа"
        text = (
            f"данные для входа при подготовке: «{escape(connect)}»; "
            f"учётные записи: {accounts}; "
            f"пароль root: {'меняется' if plan.root_password else 'не меняется'}; "
            + (
                f"ключи: {len(plan.keys)} для {escape(', '.join(plan.key_users))}; "
                if plan.keys
                else "ключи не добавляются; "
            )
            + f"вход root по SSH: {permit}; "
            + (f"права администратора у «{escape(who)}» отзываются; "
               if plan.revoke_admin else "")
            + f"данные для входа после подготовки: «{escape(after)}»"
            + (", при «Готово» станут данными для входа по умолчанию"
               if plan.after_as_default else "")
        )
        prepared = len(ctx.install_result.bundle_assignments)
        if prepared and plan.after_as_default:
            text += (
                f". Записи {prepared} хост(ов) при сохранении будут наследовать "
                "связку по умолчанию (своя связка хоста снимается)"
            )
        elif prepared:
            text += (
                f". Записям {prepared} хост(ов) при сохранении будет назначена связка "
                "после подготовки (своя связка хоста заменяется)"
            )
        return text
