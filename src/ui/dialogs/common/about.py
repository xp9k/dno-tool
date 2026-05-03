import os
import json
import shutil
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton,
    QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QCursor, QDesktopServices
from PySide6.QtCore import QUrl

from src import __version__
from src.services.updater import check_for_update, _get_token
from src.services.updater import _get_headers, _is_version_tag, _get_downloads_dir

import urllib.request
import urllib.error

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
        import platform
        from src.services.updater import get_latest_release, GITHUB_API, REPO, CURRENT_VERSION

        system = platform.system()
        if system == "Linux":
            os_name = "mos"
        elif system == "Windows":
            os_name = "windows"
        else:
            self.finished.emit({"success": False, "message": f"Unsupported OS: {system}", "download_dir": "", "new_version": CURRENT_VERSION, "error": f"Unsupported OS: {system}"})
            return

        try:
            token = _get_token()
            self.progress.emit("Получение информации о релизе...")
            release = get_latest_release(token)
            tag = release.get("tag_name", "")
            latest_version = tag.lstrip("v")
            archive_name = f"dnotool-{latest_version}-{os_name}.zip"

            asset = None
            for a in release.get("assets", []):
                if a.get("name") == archive_name:
                    asset = a
                    break

            if not asset:
                self.finished.emit({"success": False, "message": f"Archive {archive_name} not found.", "download_dir": "", "new_version": latest_version, "error": f"Archive {archive_name} not found."})
                return

            download_url = asset.get("url") or asset.get("browser_download_url")
            dest_dir = _get_downloads_dir()
            dest_path = dest_dir / archive_name

            headers = _get_headers(token)
            headers["Accept"] = "application/octet-stream"
            req = urllib.request.Request(download_url, headers=headers)

            self.progress.emit(f"Загрузка {archive_name}...")
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = resp.headers.get("Content-Length")
                total_bytes = int(total) if total else 0
                downloaded = 0
                chunk_size = 8192
                with open(str(dest_path), "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0:
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total_bytes / (1024 * 1024)
                            self.progress.emit(f"Загрузка: {mb_done:.1f} / {mb_total:.1f} МБ")
                        else:
                            mb_done = downloaded / (1024 * 1024)
                            self.progress.emit(f"Загрузка: {mb_done:.1f} МБ")

            self.finished.emit({
                "success": True,
                "message": f"Архив сохранён: {dest_path}",
                "download_dir": str(dest_dir),
                "new_version": latest_version,
                "error": None,
            })

        except PermissionError as e:
            self.finished.emit({"success": False, "message": f"Ошибка авторизации: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)})
        except ConnectionError as e:
            self.finished.emit({"success": False, "message": f"Сетевая ошибка: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)})
        except Exception as e:
            self.finished.emit({"success": False, "message": f"Ошибка: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)})


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setFixedSize(400, 280)

        layout = QVBoxLayout(self)

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
        layout.addWidget(author_label)
        layout.addWidget(link_label)

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
        self.version_label.setText("Загрузка обновления...")

        self._download_thread = UpdateDownloadThread(self)
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.finished.connect(self._on_download_finished)
        self._download_thread.start()

    def _on_download_progress(self, msg: str):
        self.version_label.setText(msg)

    def _on_download_finished(self, result: dict):
        if result.get("success"):
            download_dir = result.get("download_dir", "")
            QMessageBox.information(
                self,
                "Обновление загружено",
                f"Архив обновления сохранён в:\n{download_dir}\n\n"
                "Распакуйте его и замените бинарный файл."
            )
            self.version_label.setText(
                f"<font color='green'>Загружено v{result['new_version']}</font>"
            )
            self.update_btn.hide()
            try:
                webbrowser.open(download_dir)
            except Exception:
                pass
        else:
            QMessageBox.critical(
                self,
                "Ошибка обновления",
                f"Не удалось загрузить обновление:\n{result.get('message', 'Unknown error')}"
            )
            self.version_label.setText(
                "<font color='red'>Ошибка обновления</font>"
            )
            self.update_btn.show()
            self.update_btn.setText("Повторить")

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