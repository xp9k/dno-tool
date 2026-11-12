"""Диалог «О программе»: информация о версии, проверка и загрузка обновлений."""

import os
import json
import shutil
import subprocess
import platform
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QFont, QCursor, QDesktopServices, QPalette
from PySide6.QtCore import QUrl

from src import __version__
from src.config.settings import get_asset_path
from src.services.updater import check_for_update, is_installed_as_package
from src.services.updater import _get_headers, _is_version_tag, _get_downloads_dir
from src.ui.icons import set_themed_icon
from src.ui.widgets.module_player import ModuleMusicPlayer, load_module
from src.ui.widgets.wave_backdrop import WaveBackdrop
from src.utils.network.https import create_https_context
from src.workers.qthread_registry import track_qthread

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
    download_finished = Signal(dict)

    def run(self):
        import platform
        from src.services.updater import (
            get_latest_release,
            GITHUB_API,
            REPO,
            CURRENT_VERSION,
        )

        system = platform.system()
        if system == "Linux":
            os_name = "mos"
        elif system == "Windows":
            os_name = "windows"
        else:
            self.download_finished.emit(
                {
                    "success": False,
                    "message": f"Unsupported OS: {system}",
                    "download_dir": "",
                    "new_version": CURRENT_VERSION,
                    "error": f"Unsupported OS: {system}",
                }
            )
            return

        try:
            self.progress.emit("Получение информации о релизе...")
            release = get_latest_release()
            tag = release.get("tag_name", "")
            latest_version = tag.lstrip("v")
            archive_name = f"dno-tool-{latest_version}-{os_name}.zip"

            asset = None
            for a in release.get("assets", []):
                if a.get("name") == archive_name:
                    asset = a
                    break

            if not asset:
                self.download_finished.emit(
                    {
                        "success": False,
                        "message": f"Archive {archive_name} not found.",
                        "download_dir": "",
                        "new_version": latest_version,
                        "error": f"Archive {archive_name} not found.",
                    }
                )
                return

            download_url = asset.get("url") or asset.get("browser_download_url")
            dest_dir = _get_downloads_dir()
            dest_path = dest_dir / archive_name

            headers = _get_headers()
            headers["Accept"] = "application/octet-stream"
            req = urllib.request.Request(download_url, headers=headers)

            self.progress.emit(f"Загрузка {archive_name}...")
            with urllib.request.urlopen(
                req, timeout=120, context=create_https_context()
            ) as resp:
                total = resp.headers.get("Content-Length")
                total_bytes = int(total) if total else 0
                downloaded = 0
                chunk_size = 8192
                interrupted = False
                with open(str(dest_path), "wb") as f:
                    while True:
                        if self.isInterruptionRequested():
                            interrupted = True
                            break
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0:
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total_bytes / (1024 * 1024)
                            self.progress.emit(
                                f"Загрузка: {mb_done:.1f} / {mb_total:.1f} МБ"
                            )
                        else:
                            mb_done = downloaded / (1024 * 1024)
                            self.progress.emit(f"Загрузка: {mb_done:.1f} МБ")

            if interrupted:
                # Окно закрыто: неполный архив не должен выглядеть загруженным.
                dest_path.unlink(missing_ok=True)
                return

            from src.utils.filesystem.fs_utils import ensure_user_owned

            ensure_user_owned(str(dest_path))

            self.download_finished.emit(
                {
                    "success": True,
                    "message": f"Архив сохранён: {dest_path}",
                    "download_dir": str(dest_dir),
                    "new_version": latest_version,
                    "error": None,
                }
            )

        except PermissionError as e:
            self.download_finished.emit(
                {
                    "success": False,
                    "message": f"Ошибка авторизации: {e}",
                    "download_dir": "",
                    "new_version": CURRENT_VERSION,
                    "error": str(e),
                }
            )
        except ConnectionError as e:
            self.download_finished.emit(
                {
                    "success": False,
                    "message": f"Сетевая ошибка: {e}",
                    "download_dir": "",
                    "new_version": CURRENT_VERSION,
                    "error": str(e),
                }
            )
        except Exception as e:
            self.download_finished.emit(
                {
                    "success": False,
                    "message": f"Ошибка: {e}",
                    "download_dir": "",
                    "new_version": CURRENT_VERSION,
                    "error": str(e),
                }
            )


MUSIC_FILE = "music/about.xm"
_VIEW_STATE_ID = "about_dialog"
# Выключение звука без ViewState (окно открыто без главного окна) живёт до выхода.
_session_muted = False


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setFixedSize(440, 330)

        self._view_state = getattr(parent, "view_state", None)
        if self._view_state is not None:
            self._view_state.register(_VIEW_STATE_ID, persist=True)
        self._music: ModuleMusicPlayer | None = None
        module = load_module(get_asset_path(MUSIC_FILE))
        if module is not None:
            self._music = ModuleMusicPlayer(module, self)
        self._backdrop = WaveBackdrop(
            self, self._music.level if self._music is not None else None
        )
        self._backdrop.setGeometry(self.rect())
        self._backdrop.lower()

        layout = QVBoxLayout(self)

        title_label = QLabel(f"DNO Tool v{__version__}")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description_label = QLabel(
            "Программа для управления парком машин под управлением семейства ОС Linux."
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

        author_label = QLabel("Разработчик: <a href='https://t.me/x_p_9_k'>Yar</a>")
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_label.setOpenExternalLinks(False)
        author_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        author_label.linkActivated.connect(self._open_link)

        link_label = QLabel('<a href="https://wiki.dno-it.ru/">wiki.dno-it.ru</a>')
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
        layout.addStretch(1)
        layout.addLayout(self._build_music_row(module))

        self._closed = False
        self._download_thread: UpdateDownloadThread | None = None
        self._check_thread = track_qthread(UpdateCheckThread(), "Проверка обновлений")
        self._check_thread.result_ready.connect(self._on_check_result)
        self._check_thread.start()

    def _build_music_row(self, module) -> QHBoxLayout:
        """Строка внизу: авторы музыки из файла модуля слева, кнопка звука справа."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.music_credits_label = QLabel()
        self.mute_button = QToolButton()
        self.mute_button.setAutoRaise(True)
        self.mute_button.setCheckable(True)
        self.mute_button.setIconSize(QSize(18, 18))
        self.mute_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.mute_button.toggled.connect(self._on_mute_toggled)
        row.addWidget(self.music_credits_label, 1, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.mute_button, 0, Qt.AlignmentFlag.AlignBottom)
        if module is None:
            self.music_credits_label.hide()
            self.mute_button.hide()
            return row

        credits = module.credits()
        font = self.music_credits_label.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        self.music_credits_label.setFont(font)
        palette = self.music_credits_label.palette()
        palette.setColor(
            QPalette.ColorRole.WindowText,
            palette.color(QPalette.ColorRole.PlaceholderText),
        )
        self.music_credits_label.setPalette(palette)
        self.music_credits_label.setText("♪ " + " · ".join(credits))
        self.music_credits_label.setToolTip(
            "Музыка в окне (модуль XM):\n" + "\n".join(credits)
        )
        self.music_credits_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        muted = self._load_muted()
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(muted)
        self.mute_button.blockSignals(False)
        self._apply_mute(muted)
        return row

    def _load_muted(self) -> bool:
        if self._view_state is not None:
            return bool(self._view_state.get(_VIEW_STATE_ID, "music_muted", False))
        return _session_muted

    def _on_mute_toggled(self, muted: bool) -> None:
        global _session_muted
        _session_muted = muted
        if self._view_state is not None:
            self._view_state.set(_VIEW_STATE_ID, "music_muted", muted, track_change=False)
        self._apply_mute(muted)

    def _apply_mute(self, muted: bool) -> None:
        set_themed_icon(self.mute_button, "ve_mute" if muted else "ve_volume")
        self.mute_button.setToolTip("Включить музыку" if muted else "Выключить музыку")
        if self._music is None:
            return
        if muted:
            self._music.stop()
        elif self.isVisible():
            self._music.start()

    def showEvent(self, event) -> None:  # noqa: N802 - имя Qt
        super().showEvent(event)
        if self._music is not None and not self.mute_button.isChecked():
            self._music.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 - имя Qt
        super().resizeEvent(event)
        backdrop = getattr(self, "_backdrop", None)
        if backdrop is not None:
            backdrop.setGeometry(self.rect())

    def done(self, result: int) -> None:
        """Закрыть окно без ожидания сети: потоки учтены в реестре и завершатся сами."""
        self._closed = True
        if self._download_thread is not None:
            self._download_thread.requestInterruption()
        if self._music is not None:
            self._music.stop()
        self._backdrop.stop()
        super().done(result)

    def _on_check_result(self, result: dict):
        if self._closed:
            return
        # Резервный источник обновлений (GitHub недоступен, но есть fallback).
        fallback_url = result.get("fallback_url")
        if fallback_url and not result.get("update_available"):
            self.version_label.setText(
                "<font color='#1976D2'>Проверка обновлений на GitHub недоступна.<br>"
                "Проверьте обновления вручную: "
                f"<a href='{fallback_url}'>открыть страницу загрузки</a></font>"
            )
            return

        if result.get("error"):
            # Тихий режим: если fallback тоже недоступен, ничего не говорим.
            self.version_label.setText(
                f"<font color='green'>Установлена последняя версия "
                f"({result.get('current_version', '?')})</font>"
            )
            return

        current = result.get("current_version", "?")
        latest = result.get("latest_version", "?")

        if result.get("update_available"):
            if is_installed_as_package():
                self.version_label.setText(
                    f"<font color='#1976D2'>Доступна новая версия: {latest} "
                    f"(текущая {current})<br>"
                    "Обновите пакет через менеджер пакетов: "
                    "<b>dnf update dno-tool</b></font>"
                )
            else:
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
        if is_installed_as_package():
            self.update_btn.hide()
            return
        self.update_btn.hide()
        self.version_label.setText("Загрузка обновления...")

        self._download_thread = track_qthread(
            UpdateDownloadThread(), "Загрузка обновления"
        )
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.download_finished.connect(self._on_download_finished)
        self._download_thread.start()

    def _on_download_progress(self, msg: str):
        if self._closed:
            return
        self.version_label.setText(msg)

    def _on_download_finished(self, result: dict):
        self._download_thread = None
        if self._closed:
            return
        if result.get("success"):
            download_dir = result.get("download_dir", "")
            QMessageBox.information(
                self,
                "Обновление загружено",
                f"Архив обновления сохранён в:\n{download_dir}\n\n"
                "Распакуйте его и замените бинарный файл.",
            )
            self.version_label.setText(
                f"<font color='green'>Загружено v{result['new_version']}</font>"
            )
            self.update_btn.hide()
            try:
                if platform.system() == "Windows":
                    os.startfile(download_dir)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", download_dir])
                else:
                    from src.utils.system.subprocess_env import (
                        build_clean_env,
                        safe_cwd_for_user,
                        wrap_as_user,
                    )

                    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    clean_env = build_clean_env()
                    if clean_env is not None:
                        kwargs["env"] = clean_env
                    kwargs["cwd"] = safe_cwd_for_user()
                    subprocess.Popen(wrap_as_user(["xdg-open", download_dir]), **kwargs)
            except Exception:
                pass
        else:
            QMessageBox.critical(
                self,
                "Ошибка обновления",
                f"Не удалось загрузить обновление:\n{result.get('message', 'Unknown error')}",
            )
            self.version_label.setText("<font color='red'>Ошибка обновления</font>")
            self.update_btn.show()
            self.update_btn.setText("Повторить")

    @staticmethod
    def _open_link(link: str):
        QDesktopServices.openUrl(QUrl(link))
