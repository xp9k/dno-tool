from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                                QPushButton, QTextEdit, QLabel, QMessageBox,
                                QSplitter, QFrame, QHeaderView, QTableWidget,
                                QTableWidgetItem, QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from src.ui.widgets.device_tree import DeviceTreeView, CustomTreeItem
import os
from src.config import *
from src.logger import logger
from src.domain.models.device import DeviceModel
from typing import List
from src.ui.dialogs.tools.known_hosts import KnownHostsDialog
from src.workers.key_installer import KeyInstallerWorker, RESULT_ERROR, RESULT_SUCCESS, RESULT_IGNORE, RESULT_ABORT


class SSHManageDialog(QDialog):
    def __init__(self, tree_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управление SSH ключами")
        self.resize(800, 500)
        self.public_key_path = DEFAULT_SSH_PUBLIC_KEY_PATH if os.path.exists(DEFAULT_SSH_PUBLIC_KEY_PATH) else os.path.expanduser('~/.ssh/id_rsa.pub')
        self._init_ui(tree_data)

        self.installation = False
        self._abort = False

    def _init_ui(self, tree_data):
        layout = QVBoxLayout(self)
        # Верхняя панель
        top_panel = QHBoxLayout()
        self.key_status_label = QLabel()
        self.gen_key_btn = QPushButton()
        self.gen_key_btn.clicked.connect(self.handle_key_btn)
        # Кнопка просмотра known_hosts
        self.known_hosts_btn = QPushButton("Просмотр known_hosts")
        self.known_hosts_btn.clicked.connect(self.show_known_hosts_dialog)
        # Кнопка генерации публичного ключа из приватного
        self.gen_pub_from_priv_btn = QPushButton("Создать публичный ключ из приватного")
        self.gen_pub_from_priv_btn.clicked.connect(self.generate_public_from_private_key)
        self.gen_pub_from_priv_btn.setVisible(False)
        self.check_key_status()
        top_panel.addWidget(self.key_status_label)
        top_panel.addStretch()
        top_panel.addWidget(self.gen_pub_from_priv_btn)
        top_panel.addWidget(self.gen_key_btn, alignment=Qt.AlignmentFlag.AlignRight)
        top_panel.addWidget(self.known_hosts_btn, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top_panel)

        # Сплиттер: дерево и лог
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # Дерево хостов
        # self.tree_frame = QFrame()        
        # self.tree_frame.setLayout(QVBoxLayout())
        # self.tree_frame.setContentsMargins(0, 0, 0, 0)

        self.treeview = DeviceTreeView(read_only=True, enable_ping=False)
        # self.tree_frame.layout().addWidget(self.treeview)
        self.treeview.setHeaderHidden(False)
        self.treeview.setSelectionMode(self.treeview.SelectionMode.ExtendedSelection)
        self.treeview._model.load_tree_data(tree_data)
        splitter.addWidget(self.treeview)

        # Окно лога
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # splitter.addWidget(self.log_text)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

        self.installTable = QTableWidget()
        self.installTable.setColumnCount(3)
        self.installTable.setHorizontalHeaderLabels(["Результат", "Хост", "Подробности"])
        # Центрируем заголовок и содержимое первой колонки
        self.installTable.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.horizontalHeaderItem(1).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.horizontalHeaderItem(2).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.installTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.installTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.installTable.setColumnWidth(0, 95)
        self.installTable.setColumnWidth(1, 125)
        self.installTable.verticalHeader().setVisible(False)
        self.installTable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.installTable.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.installTable.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.installTable)
        
        self.treeview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.installTable.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        splitter.setSizes([200, 600])
        splitter.setStretchFactor(1, 2)

        # Кнопка установки ключа
        btn_panel = QHBoxLayout()
        self.install_btn = QPushButton("Установить ключ на отмеченные хосты")
        self.install_btn.clicked.connect(self.install_key_on_selected)
        self.install_btn.setDefault(True)
        self.abort_btn = QPushButton("Отменить установку")
        self.abort_btn.clicked.connect(self.abort_installation)
        self.abort_btn.setVisible(False)
        btn_panel.addStretch()
        btn_panel.addWidget(self.abort_btn)
        btn_panel.addWidget(self.install_btn)
        layout.addLayout(btn_panel)


    def check_key_pair_match(self, privkey_path, pubkey_path):
        """
        Проверяет, соответствует ли публичный ключ приватному (RSA).
        Возвращает True, если соответствует, иначе False.
        """
        import paramiko
        if not os.path.exists(privkey_path) or not os.path.exists(pubkey_path):
            return False
        try:
            key = paramiko.RSAKey.from_private_key_file(privkey_path, password="")
            derived_pub = f"ssh-rsa {key.get_base64()} {key.get_name()}"
            with open(pubkey_path, 'r', encoding='utf-8') as f:
                pubkey = f.read().strip()
            derived_key = ' '.join(derived_pub.split()[:2])
            pubkey_key = ' '.join(pubkey.split()[:2])
            return derived_key == pubkey_key
        except Exception as e:
            print(e)
            return False

    def check_key_status(self):
        self.pubkey_path = os.path.join(os.path.dirname(self.public_key_path), 'id_rsa.pub')
        self.privkey_path = os.path.join(os.path.dirname(self.public_key_path), 'id_rsa')
        pubkey_exists = os.path.exists(self.pubkey_path)
        privkey_exists = os.path.exists(self.privkey_path)
        icon_ok = f'<img src="{ICONS["result_success"]}" width="14" height="14">'
        icon_err = f'<img src="{ICONS["result_failure"]}" width="14" height="14">'
        icon_key = f'<img src="{ICONS["key_exists"]}" width="14" height="14">'
        icon_nokey = f'<img src="{ICONS["key_missing"]}" width="14" height="14">'
        pubkey_status = f"{icon_key if pubkey_exists else icon_nokey} Публичный ключ: {'<b>есть</b>' if pubkey_exists else '<b>нет</b>'} ({self.pubkey_path})"
        privkey_status = f"{icon_key if privkey_exists else icon_nokey} Приватный ключ: {'<b>есть</b>' if privkey_exists else '<b>нет</b>'} ({self.privkey_path})"
        pair_status = ''
        if pubkey_exists and privkey_exists:
            if self.check_key_pair_match(self.privkey_path, self.pubkey_path):
                pair_status = f'{icon_ok} Пара ключей совпадает'
            else:
                pair_status = f'{icon_err} Пара ключей НЕ совпадает!'
        self.key_status = f"{pubkey_status} | {privkey_status}"
        self.pubkey_path = self.pubkey_path
        self.privkey_path = self.privkey_path
        if not pubkey_exists or not privkey_exists:
            self.key_status_label.setText(f"{pubkey_status}<br>{privkey_status}<br>{icon_nokey} Ключ не найден или не полный. Требуется генерация.")
            self.gen_key_btn.setText("Сгенерировать ключи")
        else:
            self.key_status_label.setText(f"{pubkey_status}<br>{privkey_status}<br>{pair_status}")
            self.gen_key_btn.setText("Перегенерировать ключи")
        
        # Показываем кнопку генерации публичного ключа из приватного
        self.gen_pub_from_priv_btn.setVisible(privkey_exists and not pubkey_exists)

    def handle_key_btn(self):
        pubkey_exists = os.path.exists(self.pubkey_path)
        privkey_exists = os.path.exists(self.privkey_path)
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Управление SSH ключами")
        layout = QVBoxLayout(dialog)
        # Создаём горизонтальный layout БЕЗ указания dialog в конструкторе
        btn_layout = QHBoxLayout()
        
        info_label = QLabel()
        if privkey_exists and not pubkey_exists:
            info_label.setText("Найден приватный ключ, но не найден публичный.")
            gen_pub_btn = QPushButton("Сгенерировать публичный ключ из приватного")
            gen_pub_btn.clicked.connect(lambda: self.generate_public_from_private(dialog))
            layout.addWidget(info_label)
            btn_layout.addWidget(gen_pub_btn)
        else:
            # info_label.setText("Управление SSH ключами")
            layout.addWidget(info_label)
        
        gen_new_btn = QPushButton("Сгенерировать новую пару ключей")
        gen_new_btn.clicked.connect(lambda: self.generate_new_pair(dialog))
        btn_layout.addWidget(gen_new_btn)
        
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)
        
        dialog.exec()

    def generate_key(self):
        import paramiko
        try:
            key = paramiko.RSAKey.generate(4096)
            key.write_private_key_file(self.privkey_path)
            pub_key_str = f"ssh-rsa {key.get_base64()} dnotool-generated-key\n"
            with open(self.pubkey_path, 'w', encoding='utf-8') as f:
                f.write(pub_key_str)
            QMessageBox.information(self, "SSH ключ", "Ключи успешно сгенерированы.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка генерации ключей: {e}")
        self.check_key_status()


    def _derive_public_key_from_private(self, privkey_path):
        import paramiko
        key = paramiko.RSAKey.from_private_key_file(privkey_path, password="")
        return f"ssh-rsa {key.get_base64()} {key.get_name()}\n"

    def generate_public_from_private_key(self):
        """Generate public key from existing private key"""
        try:
            pub_key_str = self._derive_public_key_from_private(self.privkey_path)
            with open(self.pubkey_path, 'w', encoding='utf-8') as f:
                f.write(pub_key_str)
            QMessageBox.information(self, "SSH ключ", "Публичный ключ успешно сгенерирован.")
            self.check_key_status()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка генерации публичного ключа: {e}")

    def generate_public_from_private(self, dialog):
        """Generate public key from existing private key"""
        try:
            pub_key_str = self._derive_public_key_from_private(self.privkey_path)
            with open(self.pubkey_path, 'w', encoding='utf-8') as f:
                f.write(pub_key_str)
            QMessageBox.information(self, "SSH ключ", "Публичный ключ успешно сгенерирован.")
            self.check_key_status()
            dialog.close()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка генерации публичного ключа: {e}")

    def generate_new_pair(self, dialog):
        """Generate new key pair (both private and public)"""
        if QMessageBox.question(
            self,
            "Подтверждение",
            "Это действие удалит существующие ключи. Продолжить?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            # Remove old keys if they exist
            if os.path.exists(self.pubkey_path):
                try:
                    os.remove(self.pubkey_path)
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка", f"Ошибка удаления публичного ключа: {e}")
                    return
            if os.path.exists(self.privkey_path):
                try:
                    os.remove(self.privkey_path)
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка", f"Ошибка удаления приватного ключа: {e}")
                    return
                    
            self.generate_key()
            dialog.close()

    
    def on_install_finished(self):
        self.thread.quit()
        self.thread.deleteLater()

        logger.info("Установка ключа завершена")
        self.install_btn.setVisible(True)
        self.abort_btn.setVisible(False)
        self.worker = None
        self.thread = None

        if self.installation and self._abort:
            QMessageBox.information(self, "Установка SSH ключа", "Установка прервана пользователем")
        else:
            QMessageBox.information(self, "Установка SSH ключа", "Установка ключа завершена")

        self.installation = False
        self._abort = False


    def show_known_hosts_dialog(self):
        known_hosts_path = os.path.expanduser('~/.ssh/known_hosts')
        dlg = KnownHostsDialog(known_hosts_path, self)
        dlg.exec()


    def update_install_status(self, device: DeviceModel, status, detailes=None):
        row_position = self.installTable.rowCount()
        self.installTable.insertRow(row_position)

        status_item = QTableWidgetItem()
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.setItem(row_position, 0, status_item)

        host_item = QTableWidgetItem(device.name)
        host_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.installTable.setItem(row_position, 1, host_item)

        info_item = QTableWidgetItem(detailes if detailes else "")
        info_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.installTable.setItem(row_position, 2, info_item)

        if status == RESULT_SUCCESS:
            status_item.setIcon(QIcon(ICONS['result_success']))
            status_item.setText("Успех")
            return

        if status == RESULT_ERROR:
            status_item.setIcon(QIcon(ICONS['result_failure']))
            status_item.setText("Ошибка")
            return

        if status == RESULT_IGNORE:
            status_item.setIcon(QIcon(ICONS['result_warning']))
            status_item.setText("Пропуск")
            return

        if status == RESULT_ABORT:
            status_item.setIcon(QIcon(ICONS['result_failure']))
            status_item.setText("Отмена")
            return

        logger.error(f"Неизвестная ошибка установки ключа на {device.name}: {detailes}")


    def install_key_on_selected(self):
        devices = self.treeview.get_all_checked_devices()

        if not devices:
            QMessageBox.warning(self, "SSH ключ", "Не выбраны хосты для установки ключа.")
            return
        
        self.log_text.clear()
        self.installTable.setRowCount(0)
        
        pubkey_path = self.public_key_path
        privkey_path = pubkey_path.replace('.pub', '')
        pubkey_exists = os.path.exists(pubkey_path)
        privkey_exists = os.path.exists(privkey_path)

        key_info = f"Публичный ключ: {'<b>есть</b>' if pubkey_exists else '<b>нет</b>'} ({pubkey_path}), "                               
        logger.info(f"Публичный ключ: {'есть' if pubkey_exists else 'нет'} ({pubkey_path}), ")
        key_info = f"Приватный ключ: {'<b>есть</b>' if privkey_exists else '<b>нет</b>'} ({privkey_path})"
        logger.info(f"Приватный ключ: {'есть' if privkey_exists else 'нет'} ({privkey_path})")

        if pubkey_exists and privkey_exists:
            if not self.check_key_pair_match(privkey_path, pubkey_path):
                logger.info("Публичный и приватный ключи не совпадают! Установите корректную пару.")
                return

        key_to_install = pubkey_path

        if not pubkey_exists:
            if privkey_exists:
                logger.info("Публичный ключ не нашелся, но есть приватный.")
                if privkey_path and os.path.exists(privkey_path):
                    if QMessageBox.question(
                            self.parent(),
                            "Приватный ключ",
                            f"Публичный ключ не найден, но есть приватный.\nУстановить приватный ключ {privkey_path}?",
                            QMessageBox.Yes | QMessageBox.No
                        ) == QMessageBox.Yes:
                        key_to_install = privkey_path
                    else:
                        logger.info("Отмена установки")
                        return
            else:
                logger.info("Не нашел ни публичного, ни приватного ключа")
                return

        self.install_btn.setVisible(False)
        self.abort_btn.setVisible(True)

        self.thread = QThread()

        self.worker = KeyInstallerWorker(devices, key_to_install)
        self.worker.moveToThread(self.thread)
        self.worker.log_signal.connect(lambda msg: logger.debug(f"[INSTALL] {msg}"))
        self.worker.error.connect(lambda msg: logger.error(msg))
        self.worker.finished.connect(self.on_install_finished)
        self.worker.result_signal.connect(self.update_install_status)

        self.thread.started.connect(self.worker.execute)

        self.installation = True

        self.thread.start()



    def closeEvent(self, event):
        if self.installation:
            msg = "Установка ключа еще не завершена. Прервать установку?"
            if QMessageBox.question(self, "SSH ключ", msg, 
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self.abort_installation()
               
            event.ignore()
            return

        event.accept()

    
    def abort_installation(self):
        if self.worker:
            self.worker.abort()
            self._abort = True



