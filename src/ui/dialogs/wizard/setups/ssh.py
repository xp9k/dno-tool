"""Страница мастера настройки: SSH-ключи локальной машины (local_ssh)."""

import os

from PySide6.QtWidgets import QVBoxLayout

from src.ui.dialogs.common.ssh_manage import SSHKeyTableWidget
from src.ui.dialogs.wizard.setups.base import SetupPage, hint_label


class SshSetupPage(SetupPage):
    """SSH-ключи локальной машины."""

    sid = "local_ssh"

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(
            hint_label(
                "Используется существующий SSH-ключ по умолчанию.\n"
                "Если открытого ключа нет, он извлекается из закрытого.\n"
                "Установка ключей на удалённые хосты выполняется на шаге «Удалённые». "
                "Связки (логин и пароль) задаются на шаге «Хосты»."
            )
        )

        self._ssh_widget = SSHKeyTableWidget(self)
        layout.addWidget(self._ssh_widget, 1)

    # --- контракт SetupPage ---

    def on_enter(self, ctx) -> None:
        w = self._ssh_widget
        if w is None:
            return
        if hasattr(w, "check_key_status"):
            try:
                w.check_key_status()
            except Exception:
                pass
        if hasattr(w, "reload_keys_table"):
            try:
                w.reload_keys_table()
            except Exception:
                pass

    def collect(self, ctx) -> None:
        ctx.ssh.private_key_path = self.selected_private_key_path()
        self._key_error = ""
        try:
            ctx.ssh.pubkey = self.prepare_ssh_public_key()
        except Exception as error:
            ctx.ssh.pubkey = ""
            self._key_error = str(error)

    def validate(self, ctx) -> tuple:
        if ctx.services.get("ssh", True) and not ctx.ssh.pubkey:
            return False, getattr(self, "_key_error", "") or "Нет доступного закрытого SSH-ключа."
        return True, ""

    def summary_html(self, ctx) -> str:
        services = getattr(ctx, "services", None) or {}
        if not services.get("ssh", True):
            return "SSH: не выбран"
        if self._ssh_widget is not None:
            if self.key_pair_exists():
                return "SSH: ключи сгенерированы"
            return "SSH: ключи отсутствуют"
        return "SSH: не настроен"

    # --- хелперы (логика старого монолита) ---

    def prepare_ssh_public_key(self) -> str:
        """Use an existing private key; derive only its missing public companion."""
        from src.workers.command.executor_base import load_private_key

        private = self.selected_private_key_path()
        if not private or not os.path.isfile(private):
            raise ValueError("Закрытый SSH-ключ отсутствует. Добавьте существующий ключ перед продолжением.")
        key = load_private_key(private)
        if key is None:
            raise ValueError("Не удалось прочитать закрытый SSH-ключ.")
        public = private + ".pub"
        derived = f"{key.get_name()} {key.get_base64()}"
        if not os.path.lexists(public):
            try:
                with open(public, "x", encoding="utf-8") as stream:
                    stream.write(derived + "\n")
            except FileExistsError:
                pass
        with open(public, encoding="utf-8") as stream:
            value = stream.read().strip()
        if " ".join(value.split()[:2]) != derived:
            raise ValueError("Открытый SSH-ключ не соответствует закрытому. Исправьте пару ключей.")
        return value

    def selected_private_key_path(self):
        from src.workers.command.executor_base import get_default_key_path

        row = self._ssh_widget._keys_table.currentRow()
        if row >= 0:
            keys = self._ssh_widget.scan_ssh_keys()
            if row < len(keys):
                return keys[row]["priv_path"]
        return get_default_key_path()

    def key_pair_exists(self) -> bool:
        w = self._ssh_widget
        if w is None:
            return False
        try:
            private = self.selected_private_key_path()
            return w.check_key_pair_match(private, private + ".pub")
        except Exception:
            return False
