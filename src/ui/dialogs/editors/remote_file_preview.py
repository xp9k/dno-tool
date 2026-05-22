"""Диалог просмотра и редактирования удалённого файла с подсветкой синтаксиса и сохранением."""

from typing import Optional

import paramiko
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit,
    QFileDialog, QMenuBar, QComboBox, QLabel, QMessageBox
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from src.ui.widgets.syntax_highlight import get_syntax_highlighter


class RemoteFilePreviewDialog(QDialog):
    """Диалог просмотра и редактирования удалённого файла."""

    def __init__(
        self,
        filename: str,
        file_content: str,
        remote_path: Optional[str] = None,
        sftp: Optional[paramiko.SFTPClient] = None,
        parent: Optional[QDialog] = None
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Редактирование файла: {filename}")
        self.resize(800, 600)
        self.filename = filename
        self.file_content = file_content
        self.remote_path = remote_path
        self.sftp = sftp
        self.current_highlighter = None
        
        self.init_ui()

        self._is_modified = False

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Главное меню с выбором подсветки
        menubar = QMenuBar(self)
        syntax_layout = QHBoxLayout()
        syntax_label = QLabel("Подсветка:")
        self.syntax_combo = QComboBox()
        self.syntax_combo.addItems([
            "Авто", "Bash", "Python", "JSON", "PHP", "HTML", "CSS", "JavaScript", "SQL", "INI", "Conf", "Env"
        ])
        self.syntax_combo.currentIndexChanged.connect(self.on_syntax_changed)
        syntax_layout.addWidget(syntax_label)
        syntax_layout.addWidget(self.syntax_combo)
        syntax_layout.addStretch()
        layout.setMenuBar(menubar)
        layout.addLayout(syntax_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text_edit)

        # Подключаем подсветку синтаксиса (авто) перед установкой текста
        # чтобы избежать лишних срабатываний textChanged
        self.set_highlighter_auto()

        # Устанавливаем текст после подключения подсветки
        self.text_edit.setPlainText(self.file_content)

        # Подключаем обработчик изменений только после инициализации
        self.text_edit.textChanged.connect(self._on_text_changed)

        btn_layout = QHBoxLayout()

        # Кнопка "Сохранить локально" слева
        self.save_local_btn = QPushButton("Сохранить локально")
        self.save_local_btn.clicked.connect(self.save_file)
        btn_layout.addWidget(self.save_local_btn)

        btn_layout.addStretch()

        # Кнопка "Сохранить" (только если есть SFTP-соединение)
        if self.sftp and self.remote_path:
            self.save_btn = QPushButton("Сохранить")
            self.save_btn.clicked.connect(self.save_to_remote)
            btn_layout.addWidget(self.save_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

    def set_highlighter_auto(self):
        if self.current_highlighter:
            self.current_highlighter.setDocument(None)
        highlighter = get_syntax_highlighter(self.text_edit.document(), self.filename)
        self.current_highlighter = highlighter

    def set_highlighter(self, mode):
        if self.current_highlighter:
            self.current_highlighter.setDocument(None)
        doc = self.text_edit.document()
        if mode == "Bash":
            from src.ui.widgets.syntax_highlight import BashSyntaxHighlighter
            self.current_highlighter = BashSyntaxHighlighter(doc)
        elif mode == "Python":
            from src.ui.widgets.syntax_highlight import PythonHighlighter
            self.current_highlighter = PythonHighlighter(doc)
        elif mode == "JSON":
            from src.ui.widgets.syntax_highlight import JsonHighlighter
            self.current_highlighter = JsonHighlighter(doc)
        elif mode == "PHP":
            from src.ui.widgets.syntax_highlight import PhpHighlighter
            self.current_highlighter = PhpHighlighter(doc)
        elif mode == "HTML":
            from src.ui.widgets.syntax_highlight import HtmlHighlighter
            self.current_highlighter = HtmlHighlighter(doc)
        elif mode == "CSS":
            from src.ui.widgets.syntax_highlight import CssHighlighter
            self.current_highlighter = CssHighlighter(doc)
        elif mode == "JavaScript":
            from src.ui.widgets.syntax_highlight import JavaScriptHighlighter
            self.current_highlighter = JavaScriptHighlighter(doc)
        elif mode == "SQL":
            from src.ui.widgets.syntax_highlight import SqlHighlighter
            self.current_highlighter = SqlHighlighter(doc)
        elif mode == "INI":
            from src.ui.widgets.syntax_highlight import IniHighlighter
            self.current_highlighter = IniHighlighter(doc)
        elif mode == "Conf":
            from src.ui.widgets.syntax_highlight import ConfHighlighter
            self.current_highlighter = ConfHighlighter(doc)
        elif mode == "Env":
            from src.ui.widgets.syntax_highlight import EnvHighlighter
            self.current_highlighter = EnvHighlighter(doc)
        else:
            self.set_highlighter_auto()

    def on_syntax_changed(self, idx):
        mode = self.syntax_combo.currentText()
        if mode == "Авто":
            self.set_highlighter_auto()
        else:
            self.set_highlighter(mode)

    def _on_text_changed(self):
        """Обработка изменения текста."""
        self._is_modified = True
        self.update_window_title()

    def update_window_title(self):
        """Обновление заголовка окна с индикатором изменений."""
        prefix = "* " if self._is_modified else ""
        self.setWindowTitle(f"{prefix}Редактирование файла: {self.filename}")

    def get_content(self) -> str:
        """Получение текущего содержимого редактора."""
        return self.text_edit.toPlainText()

    def save_file(self):
        """Сохранение файла локально."""
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить файл", self.filename)
        if path:
            try:
                content = self.get_content()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                self._is_modified = False
                self.update_window_title()
                QMessageBox.information(self, "Успех", f"Файл сохранён: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка сохранения", str(e))

    def save_to_remote(self):
        """Сохранение файла на удалённый сервер через SFTP."""
        if not self.sftp or not self.remote_path:
            QMessageBox.warning(self, "Ошибка", "Нет соединения с сервером")
            return

        try:
            content = self.get_content()
            # Записываем содержимое через SFTP
            with self.sftp.open(self.remote_path, 'w') as f:
                f.write(content.encode('utf-8'))
            self._is_modified = False
            self.update_window_title()
            self.file_content = content  # Обновляем сохранённое содержимое
            QMessageBox.information(self, "Успех", f"Файл сохранён на сервер: {self.remote_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))

    def closeEvent(self, event):
        """Обработка закрытия диалога с проверкой несохранённых изменений."""
        if self._is_modified:
            reply = QMessageBox.question(
                self, "Несохранённые изменения",
                "Файл был изменён. Сохранить изменения?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            if reply == QMessageBox.Save:
                if self.sftp and self.remote_path:
                    self.save_to_remote()
                else:
                    self.save_file()
                event.accept()
            elif reply == QMessageBox.Cancel:
                event.ignore()
            else:
                event.accept()
        else:
            event.accept()
