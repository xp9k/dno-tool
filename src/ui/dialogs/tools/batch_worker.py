"""Воркер последовательной передачи нескольких файлов через SFTP (пакетная загрузка/скачивание)."""

import os
from stat import S_ISDIR
from pathlib import PurePosixPath

from PySide6.QtCore import QThread, Signal, Qt

from src.logger import logger


class BatchTransferWorker(QThread):
    """Воркер для последовательной передачи нескольких файлов"""

    progress = Signal(int, str)
    finished = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self, sftp, items, local_base, remote_base,
                 is_upload: bool, parent=None):
        super().__init__(parent)
        self.sftp = sftp
        self.items = items
        self.local_base = local_base
        self.remote_base = remote_base
        self.is_upload = is_upload
        self._cancelled = False
        self._processed = 0
        self._total = len(items)

    def run(self):
        try:
            for item in self.items:
                if self._cancelled:
                    break
                filename = item.text()
                is_dir = bool(item.data(Qt.ItemDataRole.UserRole + 2))

                if self.is_upload:
                    local_path = os.path.join(self.local_base, filename)
                    remote_path = str(PurePosixPath(self.remote_base).joinpath(filename))
                    if is_dir:
                        self._upload_recursive(local_path, remote_path)
                    else:
                        self.sftp.put(local_path, remote_path)
                        self.log_message.emit(f"Загружен файл: {remote_path}")
                else:
                    remote_path = str(PurePosixPath(self.remote_base).joinpath(filename))
                    local_path = os.path.join(self.local_base, filename)
                    if is_dir:
                        self._download_recursive(remote_path, local_path)
                    else:
                        self.sftp.get(remote_path, local_path)
                        self.log_message.emit(f"Скачан файл: {local_path}")

                self._processed += 1
                self.progress.emit(
                    int((self._processed / max(self._total, 1)) * 100),
                    f"Обработано {self._processed}/{self._total}"
                )

            self.finished.emit(True, "Операция завершена успешно")
        except Exception as e:
            logger.error(f"Ошибка в BatchTransferWorker: {e}")
            self.finished.emit(False, str(e))

    def _download_recursive(self, remote_path, local_path):
        if self._cancelled:
            return
        if not os.path.exists(local_path):
            os.makedirs(local_path)
            self.log_message.emit(f"Создана локальная папка: {local_path}")
        for entry in self.sftp.listdir_attr(remote_path):
            if self._cancelled:
                break
            name = entry.filename
            rpath = str(PurePosixPath(remote_path).joinpath(name))
            lpath = os.path.join(local_path, name)
            if S_ISDIR(entry.st_mode):
                self._download_recursive(rpath, lpath)
            else:
                self.sftp.get(rpath, lpath)
                self.log_message.emit(f"Скачан файл: {rpath}")

    def _upload_recursive(self, local_path, remote_path):
        if self._cancelled:
            return
        try:
            self.sftp.mkdir(remote_path)
        except FileExistsError:
            pass
        self.log_message.emit(f"Создана удалённая папка: {remote_path}")
        for entry in os.scandir(local_path):
            if self._cancelled:
                break
            name = entry.name
            lpath = entry.path
            rpath = str(PurePosixPath(remote_path).joinpath(name))
            if entry.is_dir():
                self._upload_recursive(lpath, rpath)
            else:
                self.sftp.put(lpath, rpath)
                self.log_message.emit(f"Загружен файл: {rpath}")

    def cancel(self):
        self._cancelled = True