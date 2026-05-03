from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QDialogButtonBox, QHBoxLayout, QCheckBox, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from src.workers import CommandWorker
from src.domain.models.device import DeviceModel

class BaseCommandResultDialog(QDialog):
    """Base dialog for displaying command results"""
    def __init__(self, hostname: str, output: str, status: str, parent: None, progress_source: CommandWorker = None, device_iid: str = None):
        super().__init__()
        self.hostname = hostname
        self.device_iid = device_iid
        self.progress_source = progress_source
        self.setWindowTitle(f"Результат выполнения команды на {hostname}")
        self.setup_ui(hostname, output, status)
        self.resize(600, 400)
        
        # Connect to progress updates if source provided
        # if progress_source:
        #     progress_source.progress_update.connect(self.update_output)

    def setup_ui(self, hostname: str, output: str, status: str):
        layout = QVBoxLayout()

        # Host and status info
        info_layout = QHBoxLayout()
        host_label = QLabel(f"Хост: {hostname}")
        self.status_label = QLabel(f"Статус: {status}")
        info_layout.addWidget(host_label)
        info_layout.addStretch()
        info_layout.addWidget(self.status_label)
        layout.addLayout(info_layout)

        # Result text field
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(output)
        layout.addWidget(self.text_edit)

        self.scroll_checkbox = QCheckBox("Авто прокрутка")
        self.scroll_checkbox.setChecked(True)
        self.scroll_checkbox.setVisible(False)

        self.btnClose = QPushButton("Закрыть")
        self.btnClose.clicked.connect(self.reject)

        button_box = QHBoxLayout()
        button_box.addWidget(self.scroll_checkbox, 1, Qt.AlignmentFlag.AlignLeft)
        button_box.addStretch()
        button_box.addWidget(self.btnClose)

        layout.addLayout(button_box)

        self.setLayout(layout)


    def update_output(self, device: DeviceModel, current_output: str):
        """Update output in real-time"""
        if self.device_iid and device.iid != self.device_iid:
            return
        if not current_output:
            return
        # Append current fragment instead of replacing full text
        try:
            # Move cursor to end and insert fragment (preserves existing text)
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)
            # insertPlainText keeps text as plain text and does not add extra formatting
            self.text_edit.insertPlainText(current_output)
            self.move_cursor_to_end()
        except Exception:
            # Fallback: appendPlainText if insertPlainText fails
            try:
                self.text_edit.appendPlainText(current_output)
                self.move_cursor_to_end()
            except Exception:
                pass
    
    def result_ready(self, device: DeviceModel, success: bool, output: str = ""):
        """Update dialog status when final signal arrives"""
        if self.device_iid and device.iid != self.device_iid:
            return

        try:
            if output and output.strip() and not success:
                cursor = self.text_edit.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.text_edit.setTextCursor(cursor)
                self.text_edit.insertPlainText(output)
                self.move_cursor_to_end()
            self.status_label.setText(f"Статус: {('Успех' if success else 'Неудача')}")
            self.move_cursor_to_end()
        except Exception:
            pass

    
    def move_cursor_to_end(self):
        """Move cursor to the end of text in QTextEdit widget"""

        if not self.scroll_checkbox.isChecked():
            return
        
        # 1. Получаем копию текущего курсора
        cursor = self.text_edit.textCursor()

        # 2. Перемещаем курсор в конец документа
        # Используем QTextCursor.MoveOperation.End
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # 3. Устанавливаем измененный курсор обратно в QTextEdit
        self.text_edit.setTextCursor(cursor)

        # Убедимся, что виджет имеет фокус, чтобы курсор был виден
        self.text_edit.setFocus()


    def closeEvent(self, event):
        """Handle dialog close"""
        # Disconnect from progress source if connected
        if hasattr(self, 'progress_source') and self.progress_source:
            try:
                self.progress_source.progress_update.disconnect(self.update_output)
                self.progress_source.result_ready.disconnect(self.result_ready)
            except:
                pass
        super().closeEvent(event)



class CommandResultDialog(BaseCommandResultDialog):
    """Dialog for displaying command results with real-time updates"""
    def __init__(self, hostname: str, output: str, status: str, parent=None, worker: CommandWorker = None, device_iid: str = None):
        super().__init__(hostname, output, status, parent, worker, device_iid=device_iid)
        self.worker: CommandWorker = worker
        
        # Setup real-time updates from main window
        if isinstance(worker, CommandWorker):
            self.main_window = parent
            try:
                self.worker.progress_update.connect(self.update_output)
                self.worker.result_ready.connect(self.result_ready)
            except:
                pass

