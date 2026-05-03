import sys
import re
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
    QDialogButtonBox,
    QFormLayout,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMessageBox
)
from PySide6.QtCore import Qt, Signal, QSize
from src.logger import logger

class ParameterInputDialog(QDialog):
    """
    Диалоговое окно для ввода значений параметров, извлеченных из строки.

    Парсит строку вида "текст %param1:описание1% текст ... текст %param2:описание2% ..."
    и создает форму с полями ввода для каждого параметра.
    """
    def __init__(self, parameters: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Введите значения параметров")
        self.setMinimumWidth(400) # Минимальная ширина окна

        self.parameters = self._parse_parameters(parameters) # Словарь {param_name: description}
        self.input_fields = {} # Словарь для хранения QLineEdit {param_name: QLineEdit}
        self.result_data = None # Словарь для хранения результата {param_name: value}

        # Основной макет
        main_layout = QVBoxLayout(self)

        # --- Область с прокруткой для формы ---
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True) # Разрешить изменение размера виджета внутри
        scroll_widget = QWidget() # Виджет-контейнер для формы
        form_layout = QFormLayout(scroll_widget) # Макет формы для пар "метка: поле ввода"
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows) # Перенос строк при необходимости

        if not self.parameters:
            # Если параметры не найдены
            form_layout.addRow(QLabel("В строке не найдено параметров для ввода."))
        else:
            # Создание полей ввода для каждого параметра
            sorted_params = sorted(self.parameters.items()) # Сортируем для предсказуемого порядка
            for param_name, description in sorted_params:
                label = QLabel(f"{description}:")
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(f"Значение для {description}")
                form_layout.addRow(label, line_edit)
                self.input_fields[param_name] = line_edit # Сохраняем ссылку на поле ввода

        scroll_area.setWidget(scroll_widget) # Устанавливаем виджет с формой в область прокрутки
        main_layout.addWidget(scroll_area) # Добавляем область прокрутки в основной макет

        # --- Кнопки OK и Cancel ---
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept) # При нажатии OK вызываем accept
        button_box.rejected.connect(self.reject) # При нажатии Cancel вызываем reject

        main_layout.addWidget(button_box)

        self.setLayout(main_layout)

        # Устанавливаем фокус на первое поле ввода, если оно есть
        if self.input_fields:
            first_param_name = sorted(self.input_fields.keys())[0]
            self.input_fields[first_param_name].setFocus()


    def _parse_parameters(self, parameters: list):
        """
        Преобразует список параметров в словарь {param_name: description}.
        """        
        parameters = {f'param{i+1}': value for i, value in enumerate(parameters)}
        logger.debug(f"Найденные параметры: {parameters}") # Отладочный вывод
        return parameters

    def accept(self):
        """
        Вызывается при нажатии кнопки OK.
        Собирает данные из полей ввода и сохраняет их.
        """
        self.result_data = {}
        for param_name, line_edit in self.input_fields.items():
            self.result_data[param_name] = line_edit.text()
        logger.debug(f"Собранные данные: {self.result_data}") # Отладочный вывод
        super().accept() # Закрывает диалог со статусом "принято"

    def get_data(self):
        """
        Возвращает собранные данные после закрытия диалога через OK.
        """
        return self.result_data

    def format_string(self):
        """
        Возвращает исходную строку с заменой %paramN:описание% на введённые значения.
        Если значение не введено, плейсхолдер остаётся.
        """
        result = self.input_string
        if self.result_data:
            # regex = r"%(\w+):[^%]+%" # Старая версия

            # Новое регулярное выражение:
            # %          - соответствует символу '%'
            # (\w+)      - захватывает имя параметра (группа 1)
            # (:[^%]+)?  - опционально (?) соответствует ':' и любым символам, кроме '%' (группа 2)
            # %          - соответствует символу '%'
            regex = r"%(\w+)(:[^%]+)?%"            
            def repl(match):
                # Имя параметра всегда находится в первой захватывающей группе (\w+)
                param = match.group(1)
                # Ищем значение параметра в self.result_data
                # Если найдено, возвращаем значение.
                # Если не найдено, возвращаем исходное совпадение (т.е. плейсхолдер останется)
                return self.result_data.get(param, match.group(0))
            
            # Выполняем замену всех найденных совпадений
            result = re.sub(regex, repl, result)
        return result

class ParamsInputDialog(QDialog):
    """Диалог для редактирования параметров команды"""
    
    result_ready = Signal(list)  # Signal emits list of parameter names
    
    def __init__(self, params=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактирование параметров")
        self.setMinimumSize(400, 300)
        self.params = params or []
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Parameters list
        params_label = QLabel("Параметры команды:")
        layout.addWidget(params_label)
        
        self.params_list = QListWidget()
        self.params_list.addItems(self.params)
        self.params_list.itemDoubleClicked.connect(self._edit_param)
        layout.addWidget(self.params_list)
        
        # Buttons for managing parameters
        buttons_layout = QHBoxLayout()
        
        self.add_btn = QPushButton("Добавить")
        self.add_btn.clicked.connect(self._add_param)
        buttons_layout.addWidget(self.add_btn)
        
        self.edit_btn = QPushButton("Изменить")
        self.edit_btn.clicked.connect(self._edit_param)
        buttons_layout.addWidget(self.edit_btn)
        
        self.remove_btn = QPushButton("Удалить")
        self.remove_btn.clicked.connect(self._remove_param)
        buttons_layout.addWidget(self.remove_btn)
        
        layout.addLayout(buttons_layout)
        
        # OK/Cancel buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
        
    def _add_param(self):
        """Add a new parameter to the list"""
        text, ok = QInputDialog.getText(
            self,
            "Добавить параметр",
            "Введите имя параметра:"
        )
        if ok and text:
            self.params_list.addItem(text)
            
    def _edit_param(self):
        """Edit selected parameter"""
        current = self.params_list.currentItem()
        if not current:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Выберите параметр для редактирования"
            )
            return
            
        text, ok = QInputDialog.getText(
            self,
            "Изменить параметр",
            "Введите новое имя параметра:",
            text=current.text()
        )
        if ok and text:
            current.setText(text)
            
    def _remove_param(self):
        """Remove selected parameter"""
        current = self.params_list.currentItem()
        if not current:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Выберите параметр для удаления"
            )
            return
            
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Вы уверены, что хотите удалить этот параметр?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.params_list.takeItem(self.params_list.row(current))
            
    def accept(self):
        """Handle dialog acceptance"""
        params = [self.params_list.item(i).text() 
                 for i in range(self.params_list.count())]
        self.result_ready.emit(params)
        super().accept()
        
    def get_params(self) -> list:
        """Get current parameter list"""
        return [self.params_list.item(i).text() 
                for i in range(self.params_list.count())]

# --- Пример использования ---
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Пример строки с параметрами
    input_str = ("Это строка %param1: Имя пользователя%, в которой есть что-то еще, "
                 "%param2: Название города%, и даже %param3: Код страны%. "
                 "Еще один параметр %param4: Очень длинное и подробное описание параметра номер четыре%.")

    dialog = ParameterInputDialog(input_str)
    result_code = dialog.exec()

    if result_code == QDialog.DialogCode.Accepted:
        output_data = dialog.get_data()
        print("\n--- Результат ---")
        print("Полученный словарь:")
        print(output_data)
        # Пример форматирования строки с заменой параметров
        formatted = dialog.format_string()
        print("\nСтрока с подстановкой значений:")
        print(formatted)
    else:
        print("\n--- Результат ---")
        print("Пользователь отменил ввод.")

    sys.exit(0)