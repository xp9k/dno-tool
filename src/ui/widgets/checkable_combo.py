from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QListView
from PySide6.QtCore import Qt, Signal, QModelIndex

class CheckableComboBox(QComboBox):
    item_check_state_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._skip_next_hide = False

        # Настройка отображения
        self.setView(QListView(self))
        self.view().pressed.connect(self._handle_item_pressed)
        self.setModel(self.model())
        self.setItemDelegate(QStyledItemDelegate())

        # Делаем редактируемым и настраиваем lineEdit
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignLeft)

        # Блокируем стандартное поведение при закрытии
        self.model().dataChanged.connect(self._update_display_text)
        self._update_display_text()

    def _handle_item_pressed(self, index: QModelIndex):
        item = self.model().itemFromIndex(index)
        new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        item.setCheckState(new_state)
        self._skip_next_hide = True
        self.item_check_state_changed.emit(item.text(), new_state == Qt.Checked)

    def _update_display_text(self):
        """Обновляет текст, игнорируя стандартное поведение QComboBox"""
        checked = self.checked_items()
        display_text = ", ".join(checked) if checked else "Выберите элементы"
        self.lineEdit().setText(display_text)
        self.setToolTip(display_text)

    def hidePopup(self):
        if not self._skip_next_hide:
            super().hidePopup()
            # После закрытия обновляем текст
            self._update_display_text()
        self._skip_next_hide = False

    def showPopup(self):
        super().showPopup()
        # Сохраняем текущий текст перед открытием
        self._saved_text = self.lineEdit().text()

    def addItem(self, text: str, checked=False, userData=None):
        super().addItem(text, userData)
        item = self.model().item(self.count() - 1)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_display_text()

    def addItems(self, texts: list, userDataList=None):
        super().addItems(texts)
        for i in range(self.count()):
            item = self.model().item(i)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
        self._update_display_text()

    def checked_items(self) -> list:
        return [
            self.itemText(i) 
            for i in range(self.count()) 
            if self.model().item(i).checkState() == Qt.Checked
        ]