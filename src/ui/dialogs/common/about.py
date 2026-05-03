import os
import tempfile
import zipfile
import shutil
import platform
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QProgressBar, QWidget
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QCursor, QDesktopServices
from PySide6.QtCore import QUrl

from src import __version__
from src.services.updater import check_for_update, update

try:
    from src.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class UpdateCheckThread(QThread):
    result_ready = Signal(dict)

    def run(self):
        result = check_for_update()
        self.result_ready.emit(result)


class UpdateDownloadThread(QThread):
    progress = Signal(str)
    finished = Signal(dict)

    def run(self):
        self.progress.emit("Загрузка обновления...")
        result = update()
        self.finished.emit(result)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setFixedSize(400, 320)

        layout = QVBoxLayout()

        title_label = QLabel(f"DNOTool v{__version__}")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description_label = QLabel(
            "Программа для управления парком машин "
            "под управлением семейства ОС Linux."
        )
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setWordWrap(True)

        self.version_label = QLabel("Проверка обновлений...")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setWordWrap(True)
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.version_label.setOpenExternalLinks(False)
        self.version_label.linkActivated.connect(self._open_link)

        self.update_btn = QPushButton("")
        self.update_btn.setFixedHeight(32)
        self.update_btn.clicked.connect(self._on_update_click)
        self.update_btn.hide()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.hide()

        author_label = QLabel(
            "Разработчик: <a href='https://t.me/x_p_9_k'>Yar</a>"
        )
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_label.setOpenExternalLinks(False)
        author_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        author_label.linkActivated.connect(self._open_link)

        link_label = QLabel(
            '<a href="https://wiki.dno-it.ru/">wiki.dno-it.ru</a>'
        )
        link_label.setOpenExternalLinks(False)
        link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        link_label.linkActivated.connect(self._open_link)

        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addWidget(self.version_label)
        layout.addWidget(self.update_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(author_label)
        layout.addWidget(link_label)

        self.setLayout(layout)

        self._check_thread = UpdateCheckThread()
        self._check_thread.result_ready.connect(self._on_check_result)
        self._check_thread.start()

    def _on_check_result(self, result: dict):
        if result.get("error"):
            self.version_label.setText(
                f"<font color='orange'>Ошибка проверки: {result['error']}</font>"
            )
            return

        current = result.get("current_version", "?")
        latest = result.get("latest_version", "?")

        if result.get("update_available"):
            self.version_label.setText(
                f"<font color='red'>Доступна новая версия: {latest} "
                f"(текущая {current})</font>"
            )
            self.update_btn.setText(f"Обновить до v{latest}")
            self.update_btn.show()
        else:
            self.version_label.setText(
                f"<font color='green'>Установлена последняя версия ({current})</font>"
            )

    def _on_update_click(self):
        self.update_btn.hide()
        self.progress_bar.show()
        self.version_label.setText("Загрузка обновления...")

        self._download_thread = UpdateDownloadThread()
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.finished.connect(self._on_download_finished)
        self._download_thread.start()

    def _on_download_progress(self, msg: str):
        self.version_label.setText(msg)

    def _on_download_finished(self, result: dict):
        self.progress_bar.hide()

        if result.get("success"):
            QMessageBox.information(
                self,
                "Обновление",
                f"Обновление успешно!\n\n{result['message']}\n\n"
                "Перезапустите приложение для применения изменений."
            )
            self.version_label.setText(
                f"<font color='green'>Обновлено до v{result['new_version']}</font>"
            )
            self.update_btn.hide()
        else:
            QMessageBox.critical(
                self,
                "Ошибка обновления",
                f"Не удалось обновить:\n{result.get('message', 'Unknown error')}"
            )
            self.version_label.setText(
                f"<font color='red'>Ошибка обновления</font>"
            )
            self.update_btn.show()
            self.update_btn.setText("Повторить обновление")

    @staticmethod
    def _open_link(link: str):
        QDesktopServices.openUrl(QUrl(link))

    def closeEvent(self, event):
        if hasattr(self, "_check_thread") and self._check_thread.isRunning():
            self._check_thread.quit()
            self._check_thread.wait(3000)
        if hasattr(self, "_download_thread") and self._download_thread.isRunning():
            self._download_thread.quit()
            self._download_thread.wait(3000)
        event.accept()