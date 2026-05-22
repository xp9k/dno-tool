"""Диалог сканирования IP-адресов сети: ICMP и порт-сканирование, определение MAC и импорт устройств."""

import platform
import re
from PySide6.QtWidgets import (QDialog, QHeaderView, QLabel, QLineEdit, QWidget,
                             QPushButton, QTableWidget, QVBoxLayout,
                             QProgressDialog, QTableWidgetItem, QCheckBox, QHBoxLayout, QFrame,
                             QRadioButton, QButtonGroup, QInputDialog, QMessageBox, QSizePolicy,
                             QGridLayout, QTreeView)
from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QStandardItemModel, QStandardItem, QIcon
import subprocess
import ipaddress
import threading
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
from src.domain.models.device import DeviceModel
from src.ui.widgets.checkable_combo import CheckableComboBox
from src.config import ICONS, config, PORTS
from src.logger import logger


def get_mac_address(ip_address: str) -> Optional[str]:
    """
    Получить MAC-адрес по IP-адресу из ARP-таблицы.
    
    Args:
        ip_address: IP-адрес устройства
        
    Returns:
        MAC-адрес в формате "XX:XX:XX:XX:XX:XX" или None если не найден
    """
    try:
        system = platform.system().lower()
        mac_address = None
        
        # Для Windows - скрываем консольное окно
        startupinfo = None
        if system == "windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        
        if system == "windows":
            # Windows: используем getmac или arp -a
            try:
                # Пробуем getmac (встроенная утилита Windows)
                result = subprocess.run(
                    ["getmac", "/s", ip_address, "/fo", "csv", "/nh"],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=5,
                    startupinfo=startupinfo
                )
                if result.returncode == 0 and result.stdout:
                    # Парсим вывод: "xx-xx-xx-xx-xx-xx","DeviceName"
                    lines = result.stdout.strip().split("\n")
                    for line in lines:
                        parts = line.split(",")
                        if parts and len(parts) >= 1:
                            mac = parts[0].strip().strip('"')
                            if mac and re.match(r'^([0-9A-Fa-f]{2}[-]){5}[0-9A-Fa-f]{2}$', mac):
                                mac_address = mac.replace("-", ":").upper()
                                break
            except Exception:
                pass
            
            # Fallback: используем arp -a
            if not mac_address:
                try:
                    result = subprocess.run(
                        ["arp", "-a", ip_address],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore',
                        timeout=5,
                        startupinfo=startupinfo
                    )
                    if result.returncode == 0 and result.stdout:
                        # Парсим вывод: "Interface: 192.168.1.1 --- 0x2"
                        # "  Internet Address      Physical Address      Type"
                        # "  192.168.1.1           xx-xx-xx-xx-xx-xx     dynamic"
                        for line in result.stdout.split("\n"):
                            if ip_address in line:
                                parts = line.split()
                                if len(parts) >= 2:
                                    mac = parts[1]
                                    if re.match(r'^([0-9A-Fa-f]{2}[-]){5}[0-9A-Fa-f]{2}$', mac):
                                        mac_address = mac.replace("-", ":").upper()
                                        break
                except Exception:
                    pass
        
        else:
            # Linux/Mac: используем /proc/net/arp или ip neigh
            try:
                # Пробуем читать /proc/net/arp
                with open("/proc/net/arp", "r") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 4 and parts[0] == ip_address:
                            mac = parts[3]
                            if re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', mac):
                                mac_address = mac.upper()
                                break
            except Exception:
                pass
            
            # Fallback: используем ip neigh
            if not mac_address:
                try:
                    result = subprocess.run(
                        ["ip", "neigh", "show", ip_address],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore',
                        timeout=5,
                        startupinfo=startupinfo
                    )
                    if result.returncode == 0 and result.stdout:
                        # Парсим: "192.168.1.1 dev eth0 lladdr xx:xx:xx:xx:xx:xx REACHABLE"
                        match = re.search(r'lladdr\s+([0-9a-fA-F:]{17})', result.stdout)
                        if match:
                            mac_address = match.group(1).upper()
                except Exception:
                    pass
            
            # Fallback: используем arp
            if not mac_address:
                try:
                    result = subprocess.run(
                        ["arp", "-n", ip_address],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore',
                        timeout=5,
                        startupinfo=startupinfo
                    )
                    if result.returncode == 0 and result.stdout:
                        # Парсим: "? (192.168.1.1) at xx:xx:xx:xx:xx:xx [ether] on eth0"
                        match = re.search(r'at\s+([0-9a-fA-F:]{17})', result.stdout)
                        if match:
                            mac_address = match.group(1).upper()
                except Exception:
                    pass
        
        return mac_address
        
    except Exception as e:
        logger.debug(f"Error getting MAC address for {ip_address}: {e}")
        return None


class ScannerTreeItem(QStandardItem):
    """Элемент дерева сканера с устройством"""
    def __init__(self, text: str, device: DeviceModel = None, is_folder: bool = False):
        super().__init__(text)
        self.device = device
        self.is_folder = is_folder
        self.setCheckable(True)
        self.setCheckState(Qt.CheckState.Unchecked)
        if is_folder:
            self.setIcon(QIcon(ICONS['folder']))
        else:
            self.setIcon(QIcon(ICONS['offline']))
            self.setEditable(False)

    def update_online_status(self, is_online: bool):
        """Обновить статус онлайн"""
        if is_online:
            self.setIcon(QIcon(ICONS['online']))
            # Устанавливаем статус в 5-ю колонку (индекс 4)
            if self.index().model() and self.index().siblingAtColumn(4):
                status_item = self.index().siblingAtColumn(4).data(Qt.ItemDataRole.UserRole)
                if status_item is not None:
                    self.index().model().setData(self.index().siblingAtColumn(4), "Онлайн", Qt.ItemDataRole.DisplayRole)
                else:
                    status_item = QStandardItem("Онлайн")
                    self.setChild(self.row(), 4, status_item)
        else:
            self.setIcon(QIcon(ICONS['offline']))


class IPScannerTree(QTreeView):
    """Дерево результатов сканирования с колонками для хостов"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QStandardItemModel()
        # 5 колонок: Хосты | IP адрес | Порты | MAC адрес | Статус
        self._model.setHorizontalHeaderLabels(["Хосты", "IP адрес", "Порты", "MAC адрес", "Статус"])
        self.setModel(self._model)
        self.setHeaderHidden(False)
        self.setAnimated(True)
        self.setExpandsOnDoubleClick(False)
        self.setAllColumnsShowFocus(True)

        # Настройка колонок
        header = self.header()
        header.setStretchLastSection(True)
        self.setColumnWidth(0, 200)  # Хосты
        self.setColumnWidth(1, 140)  # IP адрес
        self.setColumnWidth(2, 100)  # Порты
        self.setColumnWidth(3, 140)  # MAC адрес
        self.setColumnWidth(4, 80)   # Статус

        # Подключаем сигнал изменения чекбокса
        self._model.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item):
        """Обработка изменения элемента (чекбокса)"""
        if item.isCheckable() and item.checkState() != Qt.CheckState.PartiallyChecked:
            if hasattr(item, 'is_folder') and getattr(item, 'is_folder', False):
                self._set_children_checkstate(item, item.checkState())
            elif hasattr(item, 'device') and getattr(item, 'device', None) is not None:
                self._update_parent_checkstate(item)

    def _set_children_checkstate(self, parent_item, check_state):
        """Установить состояние чекбокса всем детям"""
        for i in range(parent_item.rowCount()):
            child = parent_item.child(i)
            if child is None:
                continue
            child.setCheckState(check_state)
            if hasattr(child, 'is_folder') and getattr(child, 'is_folder', False):
                self._set_children_checkstate(child, check_state)

    def _update_parent_checkstate(self, child_item):
        """Обновить состояние чекбокса родителя на основе детей"""
        parent = child_item.parent()
        if not parent:
            return
        checked_count = 0
        total_count = parent.rowCount()
        for i in range(total_count):
            child = parent.child(i)
            if child is None:
                continue
            if child.checkState() == Qt.CheckState.Checked:
                checked_count += 1
        if checked_count == 0:
            parent.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == total_count:
            parent.setCheckState(Qt.CheckState.Checked)
        else:
            parent.setCheckState(Qt.CheckState.PartiallyChecked)
        self._update_parent_checkstate(parent)

    def clear_tree(self):
        """Очистить дерево"""
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Хосты", "IP адрес", "Порты", "MAC адрес", "Статус"])

    def add_folder(self, folder_name: str) -> ScannerTreeItem:
        """Добавить папку (подсеть)"""
        folder_item = ScannerTreeItem(folder_name, is_folder=True)
        self._model.appendRow(folder_item)
        return folder_item

    def add_device_to_folder(self, folder: ScannerTreeItem, device: DeviceModel,
                             ports: List = None, mac_address: str = None,
                             is_online: bool = False) -> ScannerTreeItem:
        """Добавить устройство в папку с информацией в колонках"""
        device_item = ScannerTreeItem(device.name, device)

        # Колонка 1: IP адрес
        ip_item = QStandardItem(device.host)

        # Колонка 2: Порты
        ports_text = ", ".join(map(str, ports)) if ports else ""
        if ports == ['icmp']:
            ports_text = "ICMP"
        ports_item = QStandardItem(ports_text)

        # Колонка 3: MAC адрес
        mac_item = QStandardItem(mac_address if mac_address else "")

        # Колонка 4: Статус
        status_item = QStandardItem("Онлайн" if is_online else "Офлайн")

        folder.appendRow([device_item, ip_item, ports_item, mac_item, status_item])
        self.expand(folder.index())

        # Обновляем иконку
        if is_online:
            device_item.setIcon(QIcon(ICONS['online']))
        else:
            device_item.setIcon(QIcon(ICONS['offline']))

        return device_item

    def update_device_in_folder(self, folder: ScannerTreeItem, device: DeviceModel,
                                 is_online: bool, ports: List = None, mac_address: str = None):
        """Обновить устройство в папке"""
        for i in range(folder.rowCount()):
            child = folder.child(i)
            if hasattr(child, 'device') and child.device and child.device.host == device.host:
                # Обновляем имя
                child.setText(device.name)
                # Обновляем IP (колонка 1)
                ip_item = folder.child(i, 1)
                if ip_item:
                    ip_item.setText(device.host)
                # Обновляем порты (колонка 2)
                if ports is not None:
                    ports_text = ", ".join(map(str, ports)) if ports else ""
                    if ports == ['icmp']:
                        ports_text = "ICMP"
                    ports_item = folder.child(i, 2)
                    if ports_item:
                        ports_item.setText(ports_text)
                # Обновляем MAC (колонка 3)
                if mac_address:
                    mac_item = folder.child(i, 3)
                    if mac_item:
                        mac_item.setText(mac_address)
                # Обновляем статус (колонка 4)
                status_item = folder.child(i, 4)
                if status_item:
                    status_item.setText("Онлайн" if is_online else "Офлайн")
                # Обновляем иконку
                child.update_online_status(is_online)
                return
        # Если не найдено, добавляем новое
        self.add_device_to_folder(folder, device, ports, mac_address, is_online)

    def get_checked_devices(self) -> List[DeviceModel]:
        """Получить все отмеченные устройства (исключая папки)"""
        devices = []

        def collect_checked(item):
            # Пропускаем папки
            if hasattr(item, 'is_folder') and item.is_folder:
                for i in range(item.rowCount()):
                    collect_checked(item.child(i))
            # Добавляем только устройства с checked
            elif hasattr(item, 'device') and item.device and item.checkState() == Qt.CheckState.Checked:
                devices.append(item.device)
            else:
                for i in range(item.rowCount()):
                    collect_checked(item.child(i))

        for i in range(self._model.rowCount()):
            collect_checked(self._model.item(i))

        return devices


class ScannerRunnable(QObject):
    finished = Signal()
    error = Signal(str)
    scan_finished = Signal(str, str, bool, list, str)  # ip, hostname, is_online, ports, mac_address

    def __init__(self, ips: List[str], icmp: bool = True, resolve_mac: bool = False):
        super().__init__()
        self.ips = ips
        self._abort_event = threading.Event()
        self.icmp = icmp
        self.resolve_mac = resolve_mac

    def abort(self):
        self._abort_event.set()
        logger.info("ScannerRunnable: scan abort requested")

    @property
    def is_aborting(self) -> bool:
        return self._abort_event.is_set()

    def _scan_host_port(self, ip_address: str, port: int, timeout: float = 1.5) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((ip_address, port)) == 0
        except Exception:
            return False

    def scan_host(self, ip_address: str):
        ok_ports = []
        result = False
        for port, permit in PORTS.items():
            if self.is_aborting:
                break
            if not permit:
                continue
            if self._scan_host_port(ip_address, port):
                ok_ports.append(port)
                result = True
        return result, ok_ports

    def ping_host(self, ip_address: str):
        try:
            if self.is_aborting:
                return False, []
            
            system = platform.system().lower()
            
            startupinfo = None
            if system == "windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            if system == "windows":
                ping_cmd = ['ping', '-n', '1', '-w', '100', ip_address]
            else:
                ping_cmd = ['ping', '-c', '1', '-W', '1', ip_address]
            
            process = subprocess.Popen(ping_cmd,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       startupinfo=startupinfo)
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                return True, ['icmp']
            else:
                return False, []
        except Exception as e:
            logger.debug(f"ScannerRunnable: ping error for {ip_address}: {e}")
            return False, []

    def execute(self):
        if self.icmp:
            func_scan = self.ping_host
        else:
            func_scan = self.scan_host
        
        with ThreadPoolExecutor(max_workers=config.app.network.thread_count) as executor:
            future_to_ip = {
                executor.submit(func_scan, ip): ip
                for ip in self.ips if not self.is_aborting
            }

            for future in as_completed(future_to_ip):
                if self.is_aborting:
                    break
                ip = future_to_ip[future]
                try:
                    result, ok_ports = future.result()
                except Exception as e:
                    logger.error(f"ScannerRunnable: error scanning {ip}: {e}")
                    continue
                
                hostname = None
                if result:
                    try:
                        hostname = socket.gethostbyaddr(ip)[0] or ip
                    except Exception:
                        hostname = ip

                mac_address = ""
                if result and self.resolve_mac:
                    mac_address = get_mac_address(ip) or ""

                self.scan_finished.emit(ip, hostname or ip, result, ok_ports, mac_address)

        self.finished.emit()


class PortsEditorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактирование портов для сканирования")
        self.setMinimumSize(400, 300)
        self.layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Порт", "Включен"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Добавить")
        self.remove_btn = QPushButton("Удалить")
        self.save_btn = QPushButton("Сохранить")
        self.cancel_btn = QPushButton("Отмена")
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.cancel_btn)
        self.layout.addLayout(btn_layout)

        self.add_btn.clicked.connect(self.add_port)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.save_btn.clicked.connect(self.on_save)
        self.cancel_btn.clicked.connect(self.reject)

        self.load_ports()

    def load_ports(self):
        self.table.setRowCount(0)
        for port in sorted(PORTS.keys()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(str(port))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            chk = QCheckBox()
            chk.setChecked(bool(PORTS.get(port, False)))
            w = QWidget()
            l = QVBoxLayout(w)
            l.addWidget(chk)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 1, w)

    def add_port(self):
        # QInputDialog.getInt требует позиционные аргументы: (parent, title, label, value, min, max)
        port, ok = QInputDialog.getInt(self, "Добавить порт", "Номер порта:", 1, 1, 65535)
        if ok:
            # Avoid duplicates
            if port in PORTS or any(self.table.item(r, 0) and int(self.table.item(r, 0).text()) == port for r in range(self.table.rowCount())):
                QMessageBox.warning(self, "Внимание", f"Порт {port} уже существует.")
                return
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(str(port))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            chk = QCheckBox()
            chk.setChecked(True)
            w = QWidget()
            l = QVBoxLayout(w)
            l.addWidget(chk)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 1, w)

    def remove_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Инфо", "Выберите строку для удаления.")
            return
        for r in rows:
            self.table.removeRow(r)

    def on_save(self):
        new_ports = {}
        try:
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if not item:
                    continue
                try:
                    p = int(item.text())
                except Exception:
                    QMessageBox.warning(self, "Ошибка", f"Неверный номер порта в строке {r+1}.")
                    return
                # read checkbox
                cell_widget = self.table.cellWidget(r, 1)
                chk = cell_widget.findChild(QCheckBox) if cell_widget else None
                enabled = bool(chk.isChecked()) if chk else False
                new_ports[p] = enabled
            if not new_ports:
                # confirm empty list
                resp = QMessageBox.question(self, "Подтверждение", "Сохранить пустой список портов?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if resp != QMessageBox.StandardButton.Yes:
                    return
            # Update global PORTS in-place so other modules keep same object reference
            PORTS.clear()
            new_ports = sorted(new_ports.items())
            PORTS.update(new_ports)

            config.save()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при сохранении: {e}")


class IPScannerDialog(QDialog):

    exported_device = Signal(object, object)  # devices list, folder (None для определения в MW)

    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)

        self.setWindowTitle("Поточный сканнер хостов")
        self.main_window = main_window
        self._init_ui()

        self.total_hosts = 0
        self.hosts_scanned = 0
        self.progress_dialog = None
        self.ip_range = []
        self.scan_in_progress = False
        self.worker = None
        self.thread = None

    def _init_ui(self):    

        self.setMinimumSize(640, 480)

        layout = QVBoxLayout()

        self.ip_label = QLabel("IP-адрес сети/маска (например, 192.168.1.0/24):")
        self.ip_input = QLineEdit("192.168.1.0/24")
        layout.addWidget(self.ip_label)
        layout.addWidget(self.ip_input)

        self.port_combo_box = CheckableComboBox()
        
        scan_frame = QFrame()
        scan_layout = QGridLayout()
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setHorizontalSpacing(6)
        scan_layout.setVerticalSpacing(4)

        scan_label = QLabel("Тип сканирования:")
        layout.addWidget(scan_label)

        self.radio1 = QRadioButton("ICMP")
        self.radio2 = QRadioButton("По открытым портам")
        self.radio1.setChecked(True)

        scan_layout.addWidget(self.radio1, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        scan_layout.addWidget(self.radio2, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        scan_frame.setLayout(scan_layout)      

        port_edit_container = QWidget()
        port_edit_layout = QHBoxLayout(port_edit_container)
        port_edit_layout.setContentsMargins(0, 0, 0, 0)
        port_edit_layout.setSpacing(6)

        # кнопка редактирования портов рядом с комбобоксом
        self.edit_ports_button = QPushButton("Редактировать порты")
        # Не фиксируем высоты — даём виджетам определять высоту по содержимому,
        # чтобы вертикальное выравнивание работало корректно.
        # Кнопка фиксирована по ширине, комбобокс растягивается по горизонтали.
        self.edit_ports_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.port_combo_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.edit_ports_button.clicked.connect(self.open_ports_editor)

        port_edit_layout.addWidget(self.port_combo_box)
        port_edit_layout.addWidget(self.edit_ports_button)
        # Сделать combobox растягиваемым в контейнере, а кнопку — нет
        port_edit_layout.setStretch(0, 1)
        port_edit_layout.setStretch(1, 0)
        port_edit_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Контейнер может растягиваться по горизонтали; поместим его в ту же строку, что и вторая радиокнопка
        port_edit_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scan_layout.addWidget(port_edit_container, 1, 1)
         # Разрешаем второй столбцу (контейнеру портов) растягиваться
        scan_layout.setColumnStretch(1, 1)

        # Привязываем layout к frame после всех добавлений
        scan_frame.setLayout(scan_layout)
        layout.addWidget(scan_frame)

        self.radio_group = QButtonGroup(self)
        self.radio_group.addButton(self.radio1, 1)
        self.radio_group.addButton(self.radio2, 2)

        self.radio_group.buttonClicked.connect(self.radio_group_clicked)

        for port, permit in PORTS.items():
            self.port_combo_box.addItem(text=f"{port}", checked=permit)

        self.port_combo_box.setEnabled(False)

        self.port_combo_box.item_check_state_changed.connect(self.check_state_changed)

        # Горизонтальный layout для чекбоксов опций
        options_layout = QHBoxLayout()

        # Чекбокс для определения MAC-адресов
        self.check_resolve_mac = QCheckBox("Определять MAC-адреса")
        self.check_resolve_mac.setChecked(False)
        options_layout.addWidget(self.check_resolve_mac)

        # Чекбокс только активные хосты
        self.check_online_only = QCheckBox("Отображать только активные хосты")
        self.check_online_only.setChecked(True)
        options_layout.addWidget(self.check_online_only)

        options_layout.addStretch()
        layout.addLayout(options_layout)

        # Панель кнопок сканирования
        scan_btn_layout = QHBoxLayout()
        self.scan_button = QPushButton("Сканировать")
        self.scan_button.clicked.connect(self.start_threaded_scan)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_results)
        scan_btn_layout.addWidget(self.scan_button)
        scan_btn_layout.addWidget(self.clear_button)
        layout.addLayout(scan_btn_layout)

        # Дерево результатов с колонками
        self.results_tree = IPScannerTree()
        layout.addWidget(self.results_tree)

        # Панель кнопок импорта
        import_btn_layout = QHBoxLayout()
        import_button = QPushButton("Добавить выбранные хосты")
        import_button.clicked.connect(self.export_scanned_devices)
        import_btn_layout.addStretch()
        import_btn_layout.addWidget(import_button)
        layout.addLayout(import_btn_layout)

        self.setLayout(layout)


    def generate_ip_range(self, network_mask_str):
        try:
            network = ipaddress.ip_network(network_mask_str, strict=False)
            return [str(ip) for ip in network.hosts()]
        except ValueError:
            return []

    def _parse_network_input(self, input_text: str) -> tuple:
        """
        Парсит ввод пользователя и определяет маску подсети.
        
        Returns:
            tuple: (network с маской или None, prefix_len - длина маски)
        """
        input_text = input_text.strip()
        
        if not input_text:
            return None, 24  # Значение по умолчанию
        
        try:
            # Пробуем распарсить как сеть с маской (192.168.1.0/24)
            network = ipaddress.ip_network(input_text, strict=False)
            return network, network.prefixlen
        except ValueError:
            pass
        
        # Проверяем, это диапазон через дефис? (192.168.1.1-192.168.1.100)
        if '-' in input_text:
            # Для диапазонов используем /24 по умолчанию
            return None, 24
        
        # Проверяем, это одиночный IP?
        try:
            ipaddress.ip_address(input_text)
            # Одиночный IP - использовать /32
            return None, 32
        except ValueError:
            pass
        
        # Не удалось распарсить - используем /24 по умолчанию
        return None, 24

    def _get_subnet_for_ip(self, ip_address_str: str) -> str:
        """
        Определяет подсеть для заданного IP-адреса на основе введённой маски.
        
        Args:
            ip_address_str: IP-адрес хоста
            
        Returns:
            Строка подсети в формате "192.168.0.0/23"
        """
        input_text = self.ip_input.text().strip()
        network, prefix_len = self._parse_network_input(input_text)
        
        try:
            ip = ipaddress.ip_address(ip_address_str)
            
            if network is not None:
                # Вычисляем подсеть с заданной маской
                ip_int = int(ip)
                # Маска подсети
                mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
                network_int = ip_int & mask
                # Создаём сеть для получения корректного представления
                subnet_network = ipaddress.ip_network((network_int, prefix_len), strict=False)
                return str(subnet_network)
            else:
                # Для одиночного IP или диапазона без явной маски
                if prefix_len == 32:
                    # Одиночный IP - возвращаем /32
                    return f"{ip_address_str}/32"
                else:
                    # Используем /24 по умолчанию
                    subnet = '.'.join(ip_address_str.split('.')[:3]) + f'.0/{prefix_len}'
                    return subnet
        except ValueError:
            # В случае ошибки возвращаем подсеть по умолчанию /24
            try:
                return '.'.join(ip_address_str.split('.')[:3]) + '.0/24'
            except Exception:
                return f"{ip_address_str}/32"

    def start_threaded_scan(self):
        network_mask = self.ip_input.text()
        self.ip_range = self.generate_ip_range(network_mask)
        self.total_hosts = len(self.ip_range)

        self.hosts_scanned = 0
        # НЕ очищаем дерево, а обновляем существующие записи
        self.scan_in_progress = True

        if not self.ip_range:
            logger.warning("Некорректная маска сети или нет доступных хостов.")
            self.scan_in_progress = False
            return

        self.progress_dialog = QProgressDialog("Сканирование...", "Отмена", 0, self.total_hosts, self)
        self.progress_dialog.setWindowTitle("Выполнение сканирования")
        self.progress_dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.canceled.connect(self.cancel_scan)
        self.progress_dialog.setValue(0)
        self.progress_dialog.show()

        self.scan_button.setEnabled(False)

        icmp = self.radio1.isChecked()
        resolve_mac = self.check_resolve_mac.isChecked()
        self.worker = ScannerRunnable(self.ip_range, icmp, resolve_mac)
        self.thread = QThread()

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.execute)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.scan_finished.connect(self.update_results)
        self.thread.finished.connect(self.thread_finished)

        self.thread.start()


    def cancel_scan(self):
        # if self.progress_dialog and self.progress_dialog.isVisible():
        #     self.progress_dialog.cancel()
        self.worker.abort()


    def thread_finished(self):
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.scan_in_progress = False
        if self.progress_dialog and self.progress_dialog.isVisible():
            self.progress_dialog.close()
        self.scan_button.setEnabled(True)


    def closeEvent(self, event):
        if hasattr(self, 'worker') and self.worker is not None:
            self.worker.abort()
        if hasattr(self, 'thread') and self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


    def export_scanned_devices(self):
        devices = self.results_tree.get_checked_devices()

        if not devices:
            QMessageBox.warning(self, "Сканирование", "Не выбраны хосты для добавления.")
            return

        # Эмитим сигнал с устройствами, а папку определим в MainWindow
        self.exported_device.emit(devices, None)
            

    def check_state_changed(self, text, checked):
        if int(text) in PORTS:
            PORTS[int(text)] = checked


    def radio_group_clicked(self, button: QRadioButton):
        checked_id = self.radio_group.checkedId()
        self.port_combo_box.setEnabled(checked_id == 2)

    def clear_results(self):
        """Очистить результаты сканирования"""
        self.results_tree.clear_tree()
        logger.debug("IPScannerDialog: Results cleared")

    def update_results(self, ip_address, hostname, is_active, ok_ports, mac_address=""):
        """Обновить результаты сканирования (mac_address уже вычислен в рабочем потоке)"""
        if not self.scan_in_progress:
            return
        self.hosts_scanned += 1
        if self.progress_dialog and self.progress_dialog.isVisible():
            self.progress_dialog.setValue(self.hosts_scanned)
            self.progress_dialog.setLabelText(f"Сканирование... ({self.hosts_scanned}/{self.total_hosts})")

        if self.check_online_only.isChecked() and not is_active:
            return

        display_mac = mac_address if mac_address else None

        device_data = {
            'name': hostname or ip_address,
            'host': ip_address,
            'port': None,
            'mac_address': display_mac
        }
        device = DeviceModel(device_data)

        # Определяем подсеть для группировки
        subnet = self._get_subnet_for_ip(ip_address)

        # Ищем или создаем папку
        folder = None
        for i in range(self.results_tree._model.rowCount()):
            item = self.results_tree._model.item(i)
            if item.text() == subnet:
                folder = item
                break

        if not folder:
            folder = self.results_tree.add_folder(subnet)

        # Обновляем или добавляем устройство в папке
        self.results_tree.update_device_in_folder(folder, device, is_active, ok_ports, display_mac)

    def open_ports_editor(self):
        dlg = PortsEditorDialog(self)
        if dlg.exec():
            # Обновляем комбобокс после изменений
            try:
                self.port_combo_box.clear()
            except Exception:
                self.port_combo_box = CheckableComboBox()
            for port, permit in PORTS.items():
                self.port_combo_box.addItem(text=f"{port}", checked=permit)