"""Редактор Bash-скриптов с подсветкой синтаксиса и вставкой параметров команд."""

import sys
import re
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox,
    QHBoxLayout, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QMenu
)
from PySide6.QtGui import (
    QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QTextDocument, QAction, QIcon
)
from PySide6.QtCore import Qt
from src.ui.widgets.syntax_highlight import BashSyntaxHighlighter
from src.config import ICONS


# Стандартные параметры CommandWorker
STANDARD_PARAMS = [
    ("%name%", "Имя устройства (псевдоним)"),
    ("%login%", "Логин пользователя (из устройства или конфига)"),
    ("%user%", "Логин пользователя (синоним %login%)"),
    ("%username%", "Логин пользователя (синоним %login%)"),
    ("%hostname%", "Хост устройства"),
    ("%host%", "Хост устройства (синоним %hostname%)"),
    ("%ip%", "IP-адрес устройства (синоним %host%)"),
    ("%port%", "Порт SSH устройства"),
    ("%password%", "Пароль пользователя"),
    ("%pass%", "Пароль пользователя (синоним %password%)"),
    ("%date%", "Текущая дата в формате YYYY.MM.DD"),
    ("%time%", "Текущее время в формате HHMMSS"),
    ("%timestamp%", "Временная метка в формате YYYY.MM.DD_HHMMSS"),
    ("%mac%", "MAC-адрес устройства")
]


class ParamsHelpDialog(QDialog):
    """
    Диалог справки по доступным параметрам команд.
    """
    def __init__(self, parent=None, custom_params=None):
        super().__init__(parent)
        self.setWindowTitle("Справка по параметрам")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # Описание
        desc_label = QLabel(
            "Параметры автоматически заменяются на значения при выполнении команды.\n"
            "Используйте %имя% для вставки значения."
        )
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # Таблица параметров
        self.params_table = QTableWidget()
        self.params_table.setColumnCount(2)
        self.params_table.setHorizontalHeaderLabels(["Параметр", "Описание"])
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.params_table.setColumnWidth(0, 120)
        self.params_table.verticalHeader().setVisible(False)
        self.params_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.params_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.params_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.params_table)

        # Заполняем стандартные параметры
        row = 0
        for param, description in STANDARD_PARAMS:
            self.params_table.insertRow(row)
            param_item = QTableWidgetItem(param)
            param_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.params_table.setItem(row, 0, param_item)
            self.params_table.setItem(row, 1, QTableWidgetItem(description))
            row += 1

        # Добавляем пользовательские параметры если есть
        if custom_params:
            for i, param in enumerate(custom_params, 1):
                self.params_table.insertRow(row)
                param_item = QTableWidgetItem(f"%param{i}%")
                param_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.params_table.setItem(row, 0, param_item)
                self.params_table.setItem(row, 1, QTableWidgetItem(param))
                row += 1

        # Кнопка закрытия
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)


class BashEditorDialog(QDialog):
    """
    Диалоговое окно с многострочным редактором для Bash и кнопками OK/Cancel.
    """
    def __init__(self, parent=None, initial_text=None, custom_params=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор Bash скрипта")
        self.setMinimumSize(600, 400)
        self.custom_params = custom_params or []

        layout = QVBoxLayout(self)

        self.editor = QTextEdit(self)
        self.editor.setPlaceholderText("Введите ваш bash скрипт здесь...")
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.editor.setFont(font)
        layout.addWidget(self.editor)

        # Применяем подсветку синтаксиса
        self.highlighter = BashSyntaxHighlighter(self.editor.document())

        if initial_text:
            self.editor.setText(initial_text)

        # Панель с кнопками: "?" и "+" слева, OK/Cancel справа
        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 8, 0, 0)
        buttons_layout.setSpacing(8)
        
        # Кнопка справки слева
        self.help_button = QPushButton("?")
        self.help_button.setFixedSize(30, 30)
        self.help_button.setToolTip("Справка по параметрам")
        self.help_button.clicked.connect(self._show_params_help)
        buttons_layout.addWidget(self.help_button)
        
        # Кнопка добавления параметров (показывается только если есть параметры)
        self.add_param_button = QPushButton("+")
        self.add_param_button.setFixedSize(30, 30)
        self.add_param_button.setToolTip("Вставить параметр")
        self.add_param_button.clicked.connect(self._show_params_menu)
        # self.add_param_button.setVisible(bool(self.custom_params))
        buttons_layout.addWidget(self.add_param_button)
        
        buttons_layout.addStretch()
        
        # Кнопки OK/Cancel справа
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        buttons_layout.addWidget(self.button_box)
        
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def _show_params_help(self):
        """Показать справку по параметрам"""
        dlg = ParamsHelpDialog(self, self.custom_params)
        dlg.exec()

    def _show_params_menu(self):
        """Показать меню для вставки параметров"""
        # if not self.custom_params:
        #     return
        
        # Создаем контекстное меню
        menu = QMenu(self)
        
        # Добавляем стандартные параметры
        standard_section = QMenu("Стандартные параметры", self)
        standard_section.setIcon(QIcon(ICONS.get('menu_file', '')))
        menu.addMenu(standard_section)
        for param, description in STANDARD_PARAMS:
            action = QAction(f"{param} ({description})", self)
            action.triggered.connect(lambda checked, p=param: self._insert_param(p))
            standard_section.addAction(action)

        if self.custom_params:
            custom_section = QMenu("Параметры команды", self)
            custom_section.setIcon(QIcon(ICONS.get('command', '')))
            menu.addMenu(custom_section)
            for i, param in enumerate(self.custom_params, 1):
                param_name = f"%param{i}%"
                action = QAction(f"{param_name} — {param}", self)
                action.triggered.connect(lambda checked, p=param_name: self._insert_param(p))
                custom_section.addAction(action)
        
        # Показываем меню под кнопкой
        menu.exec(self.add_param_button.mapToGlobal(self.add_param_button.rect().bottomLeft()))

    def _insert_param(self, param: str):
        """Вставить параметр в позицию курсора"""
        cursor = self.editor.textCursor()
        cursor.insertText(param)
        self.editor.setFocus()

    def getText(self):
        return self.editor.toPlainText()

    def setText(self, text):
        self.editor.setPlainText(text)

# Пример использования
if __name__ == "__main__":
    app = QApplication(sys.argv)

    initial_script = """#!/bin/bash

# Это простой пример скрипта
NAME="Мир"
echo "Привет, $NAME!" # Вывод приветствия

# Цикл for
for i in {1..3}; do
  echo "Итерация $i"
  sleep 1 # Пауза на 1 секунду
done

# Условный оператор
if [ "$USER" = "root" ]; then
  echo "Выполняется от имени root."
else
  echo "Выполняется от имени $USER."
fi

# Проверка существования файла
FILE="/etc/passwd"
if [ -f "$FILE" ]; then
    echo "Файл $FILE существует."
else
    echo "Файл $FILE не найден."
fi

echo 'Это строка в одинарных кавычках, $NAME не заменится'
echo "Это строка в двойных кавычках, \\$NAME заменится: $NAME"

exit 0
"""

    dialog = BashEditorDialog(initial_text=initial_script)

    if dialog.exec():
        script_text = dialog.getText()
        print("--- Полученный скрипт: ---")
        print(script_text)
        print("--------------------------")
    else:
        print("Редактирование отменено.")

    sys.exit()