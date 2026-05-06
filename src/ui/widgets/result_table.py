from datetime import datetime

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMenu, QFileDialog, QMessageBox
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt
from src.domain.models.device import DeviceModel
from src.config import ICONS
import csv


class CommandResultTable(QTableWidget):
    """Model for command execution results table"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.device_rows = {}
        self.device_outputs = {}
        self._icon_pending = QIcon(ICONS['result_pending'])
        self._icon_executing = QIcon(ICONS['result_executing'])
        self._icon_success = QIcon(ICONS['result_success'])
        self._icon_failure = QIcon(ICONS['result_failure'])
        self._icon_cancelled = QIcon(ICONS['result_cancelled'])
        self._icon_connection_lost = QIcon(ICONS['result_connection_lost'])
        self.setup_ui()
        self.setup_context_menu()

    def setup_ui(self):
        """Setup table UI and behavior"""
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["Имя хоста", "Текст выполнения", "Результат", "iid"])

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionHidden(3, True)

        self.setColumnWidth(0, 100)
        self.setColumnWidth(2, 100)
        self.setColumnWidth(3, 0)

        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def setup_context_menu(self):
        """Setup context menu for the table"""
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, position):
        """Show context menu at the given position"""
        menu = QMenu()

        save_current_action = QAction(QIcon(ICONS.get('menu_save', '')), "Сохранить текущий результат", self)
        save_current_action.triggered.connect(self.save_current_result)
        menu.addAction(save_current_action)

        save_all_action = QAction(QIcon(ICONS.get('menu_export', '')), "Сохранить все результаты", self)
        save_all_action.triggered.connect(self.save_all_results)
        menu.addAction(save_all_action)

        clear_all_action = QAction(QIcon(ICONS.get('menu_delete', '')), "Очистить все", self)
        clear_all_action.triggered.connect(self.clear_results)
        menu.addAction(clear_all_action)

        menu.exec_(self.viewport().mapToGlobal(position))

    def save_current_result(self):
        """Save the currently selected result to a text file"""
        selected_items = self.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Предупреждение", "Пожалуйста, выберите строку для сохранения.")
            return

        selected_row = selected_items[0].row()

        hostname_item = self.item(selected_row, 0)
        output_item = self.item(selected_row, 1)
        result_item = self.item(selected_row, 2)

        hostname = hostname_item.text() if hostname_item else ""
        output = output_item.text() if output_item else ""
        result = result_item.text() if result_item else ""

        device_iid = None
        for iid_key, row_num in self.device_rows.items():
            if row_num == selected_row:
                device_iid = iid_key
                break

        if device_iid and device_iid in self.device_outputs:
            full_output = self.device_outputs[device_iid]
        else:
            full_output = output

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить результат",
            f"Результат_{hostname}.txt",
            "Текстовые файлы (*.txt);;Все файлы (*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Hostname: {hostname}\n")
                    f.write(f"Result: {result}\n")
                    f.write("-" * 50 + "\n")
                    f.write(full_output)
                QMessageBox.information(self, "Успех", f"Результат сохранен в {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{str(e)}")

    def save_all_results(self):
        """Save all results to a CSV file"""
        if self.rowCount() == 0:
            QMessageBox.warning(self, "Предупреждение", "Нет результатов для сохранения.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить все результаты",
            "Все_результаты_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt",
            "Текстовый файл с разделителем Tab (*.txt);;All Files (*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', newline='', encoding='utf-8') as f:
                    headers = []
                    for col in range(self.columnCount() - 1):
                        header_item = self.horizontalHeaderItem(col)
                        if header_item:
                            headers.append(header_item.text())
                        else:
                            headers.append(f"Column {col}")
                    f.write('\t'.join(headers) + '\n')

                    for row in range(self.rowCount()):
                        row_data = []
                        for col in range(self.columnCount() - 1):
                            item = self.item(row, col)
                            if item:
                                row_data.append(item.text())
                            else:
                                row_data.append("")
                        f.write('\t'.join(row_data) + '\n')

                QMessageBox.information(self, "Успех", f"Все результаты сохранены в {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{str(e)}")

    def clear_results(self):
        """Clear all results"""
        self.setRowCount(0)
        self.device_rows.clear()
        self.device_outputs.clear()

    def add_initial_entries(self, devices: list[DeviceModel]):
        """Add initial entries for hosts"""
        for device in devices:
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, 0, QTableWidgetItem(device.name))
            self.setItem(row, 1, QTableWidgetItem(""))
            self._set_status_item(row, "Ожидание", self._icon_pending)
            self.setItem(row, 3, QTableWidgetItem(str(device.iid)))
            self.device_rows[device.iid] = row
            self.device_outputs[device.iid] = ""

    def set_executing(self, device: DeviceModel):
        """Set status to executing (connection/command started) for a device"""
        if device.iid in self.device_rows:
            row = self.device_rows[device.iid]

            status_item = self.item(row, 2)
            if status_item and status_item.text() in ("Отменено", "Потеря связи", "Успех", "Неудача"):
                return

            self._set_status_item(row, "Выполнение", self._icon_executing)

    def update_progress(self, device: DeviceModel, current_output: str):
        """Update progress for host by appending current fragment to accumulated output"""
        try:
            if device.iid in self.device_rows:
                row = self.device_rows[device.iid]

                status_item = self.item(row, 2)
                if status_item and status_item.text() in ("Отменено", "Потеря связи"):
                    return

                chunk = current_output or ""
                if chunk:
                    prev = self.device_outputs.get(device.iid, "")
                    self.device_outputs[device.iid] = prev + chunk
                full = self.device_outputs[device.iid]
                preview = full[:50] + "..." if len(full) > 50 else full
                self.setItem(row, 1, QTableWidgetItem(preview))
                self._set_status_item(row, 'Выполнение', self._icon_executing)
        except Exception as e:
            print(e)

    def _set_status_item(self, row: int, text: str, icon: QIcon):
        item = QTableWidgetItem(icon, text)
        self.setItem(row, 2, item)

    def set_result(self, device: DeviceModel, success: bool, error_message: str = ""):
        """Set final result for host"""
        if success is None:
            success = False

        if device.iid in self.device_rows:
            row = self.device_rows[device.iid]

            status_item = self.item(row, 2)
            if status_item and status_item.text() in ("Отменено", "Потеря связи"):
                return

            if success:
                if error_message:
                    self.device_outputs[device.iid] = error_message
                preview = self.device_outputs.get(device.iid, "")[:50]
                if len(self.device_outputs.get(device.iid, "")) > 50:
                    preview += "..."
                self.setItem(row, 1, QTableWidgetItem(preview))
                self._set_status_item(row, "Успех", self._icon_success)
            else:
                error_text = error_message if error_message else "Неизвестная ошибка"
                self.device_outputs[device.iid] = error_text
                if "aborted" in error_text.lower():
                    self.setItem(row, 1, QTableWidgetItem(error_text))
                    self._set_status_item(row, "Отменено", self._icon_cancelled)
                elif "connection lost" in error_text.lower() or "потеря связи" in error_text.lower():
                    self.setItem(row, 1, QTableWidgetItem(error_text))
                    self._set_status_item(row, "Потеря связи", self._icon_connection_lost)
                else:
                    self.setItem(row, 1, QTableWidgetItem(error_text))
                    self._set_status_item(row, "Неудача", self._icon_failure)

    def mark_pending_as_cancelled(self):
        """Mark all pending/waiting results as cancelled by user"""
        for device_iid, row in self.device_rows.items():
            status_item = self.item(row, 2)
            if status_item:
                current_status = status_item.text()
                if current_status in ("Ожидание", "Выполнение"):
                    self._set_status_item(row, "Отменено", self._icon_cancelled)