"""Диалог информации об устройстве: сбор системных данных по SSH и отображение в иерархическом виде."""

import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
    QSplitter, QMessageBox, QAbstractItemView, QWidget, QDialogButtonBox,
    QProgressBar, QPushButton, QStatusBar
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon

from src.config import ICONS
from src.domain.models.device import DeviceModel
from src.workers.command.executor_base import get_credentials
from src.logger import logger


INFO_CATEGORIES = {
    "Компьютер": {
        "order": 0,
        "commands": {
            "hostname": "hostname",
            "os": "cat /etc/os-release",
            "kernel": "uname -r",
            "arch": "uname -m",
            "uptime": "uptime -p 2>/dev/null || uptime",
            "hostname_fqdn": "hostname -f 2>/dev/null || hostname",
            "loadavg": "cat /proc/loadavg",
            "proc_count": "ps -e --no-headers | wc -l",
            "boot_time": "who -b 2>/dev/null || uptime -s 2>/dev/null",
            "timezone": "timedatectl show 2>/dev/null || cat /etc/timezone 2>/dev/null || readlink /etc/localtime 2>/dev/null",
            "last_reboot": "last reboot 2>/dev/null | head -5",
            "services_count": "systemctl list-units --type=service --state=running --no-legend 2>/dev/null | wc -l",
            "users_count": "who | wc -l",
        },
        "parse": {
            "hostname": lambda out: [("Имя хоста", out.strip())] if out.strip() else [],
            "os": lambda out: _parse_os_release(out),
            "kernel": lambda out: [("Ядро", out.strip())] if out.strip() else [],
            "arch": lambda out: [("Архитектура", out.strip())] if out.strip() else [],
            "uptime": lambda out: [("Время работы", out.strip())] if out.strip() else [],
            "hostname_fqdn": lambda out: [("FQDN", out.strip())] if out.strip() else [],
            "loadavg": lambda out: _parse_loadavg(out),
            "proc_count": lambda out: [("Процессов", out.strip())] if out.strip() and out.strip().isdigit() else [],
            "boot_time": lambda out: [("Время загрузки", out.strip())] if out.strip() else [],
            "timezone": lambda out: _parse_timezone(out),
            "last_reboot": lambda out: [("Последние перезагрузки", "\n".join(out.strip().split('\n')[:5]))] if out.strip() else [],
            "services_count": lambda out: [("Служб запущено", out.strip())] if out.strip() else [],
            "users_count": lambda out: [("Пользователей онлайн", out.strip())] if out.strip() and out.strip().isdigit() else [],
        },
        "fields_order": ["Имя хоста", "FQDN", "Операционная система", "Версия ОС", "ID ОС", "Ядро", "Архитектура", "Время работы", "Время загрузки", "Часовой пояс", "Нагрузка (1/5/15 мин)", "Процессов", "Служб запущено", "Пользователей онлайн", "Последние перезагрузки"],
    },
    "Материнская плата": {
        "order": 1,
        "commands": {
            "board": "cat /sys/devices/virtual/dmi/id/board_vendor 2>/dev/null; cat /sys/devices/virtual/dmi/id/board_name 2>/dev/null; cat /sys/devices/virtual/dmi/id/board_version 2>/dev/null",
            "bios": "cat /sys/devices/virtual/dmi/id/bios_vendor 2>/dev/null; cat /sys/devices/virtual/dmi/id/bios_version 2>/dev/null; cat /sys/devices/virtual/dmi/id/bios_date 2>/dev/null",
            "sys_vendor": "cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null",
            "product": "cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null; cat /sys/devices/virtual/dmi/id/product_version 2>/dev/null",
            "serial": "cat /sys/devices/virtual/dmi/id/product_serial 2>/dev/null; cat /sys/devices/virtual/dmi/id/board_serial 2>/dev/null",
        },
        "parse": {
            "board": lambda out: _parse_lines(out, ["Производитель платы", "Модель платы", "Версия платы"]),
            "bios": lambda out: _parse_lines(out, ["Производитель BIOS", "Версия BIOS", "Дата BIOS"]),
            "sys_vendor": lambda out: [("Производитель системы", out.strip())] if out.strip() else [],
            "product": lambda out: _parse_lines(out, ["Продукт", "Версия продукта"]),
            "serial": lambda out: _parse_lines(out, ["Серийный номер продукта", "Серийный номер платы"]),
        },
        "fields_order": ["Производитель системы", "Продукт", "Версия продукта", "Производитель платы", "Модель платы", "Версия платы", "Производитель BIOS", "Версия BIOS", "Дата BIOS", "Серийный номер продукта", "Серийный номер платы"],
    },
    "Процессор": {
        "order": 2,
        "commands": {
            "cpuinfo": "cat /proc/cpuinfo",
            "lscpu": "lscpu",
        },
        "parse": {
            "cpuinfo": lambda out: _parse_cpuinfo(out),
            "lscpu": lambda out: _parse_lscpu(out),
        },
        "fields_order": ["Модель процессора", "Сокет", "Ядра", "Потоки", "Частота (МГц)", "Макс. частота (МГц)", "Мин. частота (МГц)", "Архитектура", "Гипервизор", "L1d кэш", "L1i кэш", "L2 кэш", "L3 кэш", "Побочная нагрузка (BogoMIPS)", "Флаги CPU"],
    },
    "Память": {
        "order": 3,
        "commands": {
            "meminfo": "cat /proc/meminfo",
            "dimm": "dmidecode -t memory 2>/dev/null || echo 'NO_ACCESS'",
        },
        "parse": {
            "meminfo": lambda out: _parse_meminfo(out),
            "dimm": lambda out: _parse_dimm(out),
        },
        "fields_order": ["Общая памяти", "Доступная память", "Буферы", "Кэш", "Swap всего", "Swap свободно"],
    },
    "Видеокарта": {
        "order": 4,
        "commands": {
            "gpu": "lspci -v -s $(lspci | grep -i vga | cut -d' ' -f1) 2>/dev/null || lspci | grep -i vga",
            "gpu_all": "lspci | grep -iE 'vga|3d|display'",
        },
        "parse": {
            "gpu": lambda out: _parse_lspci_vga(out),
            "gpu_all": lambda out: [("Все видеоустройства", out.strip())] if out.strip() else [],
        },
        "fields_order": ["Видеокарта", "Драйвер", "Все видеоустройства"],
    },
    "Накопители": {
        "order": 5,
        "commands": {
            "lsblk": "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL -n -b",
            "smart": "ls -1 /dev/sd* /dev/nvme* 2>/dev/null | head -20",
            "df": "df -h --output=source,size,used,avail,pcent,target 2>/dev/null | grep -v tmpfs | grep -v devtmpfs",
        },
        "parse": {
            "lsblk": lambda out: _parse_lsblk(out),
            "smart": lambda out: [("Устройства", out.strip())] if out.strip() else [],
            "df": lambda out: _parse_df(out),
        },
        "fields_order": [],
    },
    "Сеть": {
        "order": 6,
        "commands": {
            "interfaces": "ip -br addr",
            "ip_addr": "ip addr show",
            "routes": "ip route",
            "dns": "cat /etc/resolv.conf 2>/dev/null",
            "hostname_domain": "hostname -d 2>/dev/null || echo ''",
        },
        "parse": {
            "interfaces": lambda out: _parse_ip_br(out),
            "ip_addr": lambda out: _parse_ip_addr(out),
            "routes": lambda out: [("Маршруты", out.strip())] if out.strip() else [],
            "dns": lambda out: _parse_resolv_conf(out),
            "hostname_domain": lambda out: [("Домен", out.strip())] if out.strip() else [],
        },
        "fields_order": [],
    },
    "Оптические приводы": {
        "order": 7,
        "commands": {
            "cdrom": "ls -1 /dev/cd* /dev/dvd* /dev/sr* 2>/dev/null || echo 'NO_OPTICAL'",
            "cdrom_info": "lspci | grep -i 'cdrom\\|dvd\\|ATAPI' 2>/dev/null || echo ''",
        },
        "parse": {
            "cdrom": lambda out: [("Устройства", out.strip())] if out.strip() else [],
            "cdrom_info": lambda out: [("PCI устройство", out.strip())] if out.strip() else [],
        },
        "fields_order": [],
    },
    "USB": {
        "order": 8,
        "commands": {
            "usb": "lsusb 2>/dev/null || cat /sys/kernel/debug/usb/devices 2>/dev/null || echo 'NO_USB_INFO'",
            "usb_controllers": "lspci | grep -i usb",
            "usb_tree": "lsusb -t 2>/dev/null || echo 'NO_USB_TREE'",
            "usb_devices_detail": "usb-devices 2>/dev/null || echo 'NO_USB_DETAIL'",
        },
        "parse": {
            "usb": lambda out: _parse_lsusb(out),
            "usb_controllers": lambda out: [("USB контроллеры", out.strip())] if out.strip() else [],
            "usb_tree": lambda out: [("USB дерево", out.strip())] if out.strip() and 'NO_USB_TREE' not in out else [],
            "usb_devices_detail": lambda out: _parse_usb_devices(out),
        },
        "fields_order": [],
    },
    "Звук": {
        "order": 9,
        "commands": {
            "audio": "lspci | grep -i audio",
            "alsa": "cat /proc/asound/cards 2>/dev/null || echo ''",
        },
        "parse": {
            "audio": lambda out: _parse_lspci_audio(out),
            "alsa": lambda out: _parse_alsa_cards(out),
        },
        "fields_order": [],
    },
    "Прослушиваемые порты": {
        "order": 10,
        "is_table": True,
        "table_columns": ["Состояние", "Recv-Q", "Send-Q", "Локальный адрес", "Удалённый адрес", "Процесс"],
        "commands": {
            "listening": "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
        },
        "parse": {
            "listening": lambda out: _parse_ss_listening(out),
        },
        "fields_order": [],
    },
    "Установленные соединения": {
        "order": 11,
        "is_table": True,
        "table_columns": ["Состояние", "Recv-Q", "Send-Q", "Локальный адрес", "Удалённый адрес", "Процесс"],
        "commands": {
            "established": "ss -tnp state established 2>/dev/null || netstat -tnp 2>/dev/null | grep ESTABLISHED",
        },
        "parse": {
            "established": lambda out: _parse_ss_established(out),
        },
        "fields_order": [],
    },
    "Сводка пользователей": {
        "order": 11,
        "commands": {
            "user_count": "cat /etc/passwd | wc -l",
            "sys_user_count": "awk -F: '$3 < 1000 {count++} END {print count+0}' /etc/passwd",
            "sudo_users": "getent group sudo wheel adm 2>/dev/null",
            "logged_in_count": "who | wc -l",
        },
        "parse": {
            "user_count": lambda out: [("Всего пользователей", out.strip())] if out.strip() else [],
            "sys_user_count": lambda out: [("Системных пользователей", out.strip())] if out.strip() else [],
            "sudo_users": lambda out: [("Группы привилегий", out.strip())] if out.strip() else [],
            "logged_in_count": lambda out: [("Активных сеансов", out.strip())] if out.strip() else [],
        },
        "fields_order": ["Всего пользователей", "Системных пользователей", "Активных сеансов", "Группы привилегий"],
    },
    "Все пользователи": {
        "order": 12,
        "is_table": True,
        "table_columns": ["Пользователь", "UID", "GID", "Домашняя папка", "Shell", "Тип"],
        "commands": {
            "all_users_table": "getent passwd",
        },
        "parse": {
            "all_users_table": lambda out: _parse_passwd_table(out),
        },
        "fields_order": [],
    },
    "Активные сеансы": {
        "order": 13,
        "is_table": True,
        "table_columns": ["Пользователь", "Терминал", "Дата", "Время", "Откуда"],
        "commands": {
            "who_table": "who",
        },
        "parse": {
            "who_table": lambda out: _parse_who_table(out),
        },
        "fields_order": [],
    },
    "Последние входы": {
        "order": 14,
        "is_table": True,
        "table_columns": ["Пользователь", "Терминал", "Хост", "Дата/время", "Статус"],
        "commands": {
            "last_table": "last -n 20 2>/dev/null || echo 'NO_LAST'",
        },
        "parse": {
            "last_table": lambda out: _parse_last_table(out) if 'NO_LAST' not in out else [],
        },
        "fields_order": [],
    },
    "Неудачные входы": {
        "order": 15,
        "is_table": True,
        "table_columns": ["Пользователь", "Терминал", "Хост", "Дата/время", "Статус"],
        "commands": {
            "lastb_table": "lastb -n 20 2>/dev/null || echo 'NO_LASTB'",
        },
        "parse": {
            "lastb_table": lambda out: _parse_last_table(out) if 'NO_LASTB' not in out else [],
        },
        "fields_order": [],
    },
    "Папки пользователей": {
        "order": 16,
        "is_table": True,
        "table_columns": ["Пользователь", "Размер"],
        "size_columns": {1: "bytes"},
        "commands": {
            "home_dirs_table": "du -sb /home/* 2>/dev/null | sort -rn",
        },
        "parse": {
            "home_dirs_table": lambda out: _parse_home_dirs_table(out),
        },
        "fields_order": [],
    },
    "Сводка групп": {
        "order": 17,
        "commands": {
            "group_count": "cat /etc/group | wc -l",
            "sys_groups": "awk -F: '$3 < 1000 {count++} END {print count+0}' /etc/group",
            "user_priv_groups": "getent group sudo wheel adm root 2>/dev/null",
        },
        "parse": {
            "group_count": lambda out: [("Всего групп", out.strip())] if out.strip() else [],
            "sys_groups": lambda out: [("Системных групп", out.strip())] if out.strip() else [],
            "user_priv_groups": lambda out: [("Группы привилегий", out.strip())] if out.strip() else [],
        },
        "fields_order": ["Всего групп", "Системных групп", "Группы привилегий"],
    },
    "Все группы": {
        "order": 18,
        "is_table": True,
        "table_columns": ["Группа", "GID", "Пользователи"],
        "commands": {
            "group_table": "getent group",
        },
        "parse": {
            "group_table": lambda out: _parse_group_table(out),
        },
        "fields_order": [],
    },
    "Дисковое пространство": {
        "order": 13,
        "is_table": True,
        "table_columns": ["Файловая система", "Размер", "Использовано", "Свободно", "Исп.", "Точка монтирования"],
        "size_columns": {1: "kb", 2: "kb", 3: "kb", 4: "pct"},
        "commands": {
            "df_all": "df --total 2>/dev/null | tail -n +2 | awk '{print $1,$2,$3,$4,$5,$6}' || df | tail -n +2 | awk '{print $1,$2,$3,$4,$5,$6}'",
        },
        "parse": {
            "df_all": lambda out: _parse_df_table(out),
        },
        "fields_order": [],
    },
}


TREE_GROUPS = {
    "Система": ["Компьютер", "Процессор", "Память", "Материнская плата"],
    "Устройства": ["Накопители", "Видеокарта", "USB", "Звук", "Оптические приводы"],
    "Сеть": ["Сеть", "Прослушиваемые порты", "Установленные соединения"],
    "Пользователи и безопасность": ["Сводка пользователей", "Все пользователи", "Активные сеансы", "Последние входы", "Неудачные входы", "Папки пользователей", "Сводка групп", "Все группы"],
    "Хранение": ["Дисковое пространство"],
}

CATEGORY_ICONS = {
    "Система": "info_system",
    "Устройства": "info_devices",
    "Сеть": "info_network",
    "Пользователи и безопасность": "info_users",
    "Хранение": "info_storage",
    "Компьютер": "info_computer",
    "Процессор": "info_cpu",
    "Память": "info_memory",
    "Материнская плата": "info_motherboard",
    "Накопители": "info_disk",
    "Видеокарта": "info_gpu",
    "USB": "info_usb",
    "Звук": "info_sound",
    "Оптические приводы": "info_optical",
    "Прослушиваемые порты": "info_listening",
    "Установленные соединения": "info_connected",
    "Пользователи": "info_user",
    "Сводка пользователей": "info_users",
    "Все пользователи": "info_user",
    "Активные сеансы": "info_user",
    "Последние входы": "info_user",
    "Неудачные входы": "info_user",
    "Папки пользователей": "info_disk_space",
    "Группы": "info_groups",
    "Сводка групп": "info_groups",
    "Все группы": "info_groups",
    "Дисковое пространство": "info_disk_space",
}


def _parse_lines(output: str, labels: List[str]) -> List[Tuple[str, str]]:
    lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
    result = []
    for i, label in enumerate(labels):
        value = lines[i] if i < len(lines) else ""
        if value and value not in ("None", "To Be Filled By O.E.M.", ""):
            result.append((label, value))
        elif value:
            result.append((label, ""))
        else:
            pass
    return result


def _parse_os_release(output: str) -> List[Tuple[str, str]]:
    result = []
    data = {}
    for line in output.strip().split('\n'):
        if '=' in line:
            key, _, value = line.partition('=')
            data[key.strip()] = value.strip().strip('"')
    if 'PRETTY_NAME' in data:
        result.append(("Операционная система", data['PRETTY_NAME']))
    elif 'NAME' in data:
        result.append(("Операционная система", data['NAME']))
    if 'VERSION' in data:
        result.append(("Версия ОС", data['VERSION']))
    if 'ID' in data:
        result.append(("ID ОС", data['ID']))
    return result


def _parse_cpuinfo(output: str) -> List[Tuple[str, str]]:
    result = []
    model_name = None
    sockets = set()
    cores = set()
    threads = set()
    bogomips = None

    for line in output.split('\n'):
        line = line.strip()
        if line.startswith('model name'):
            model_name = line.split(':', 1)[1].strip()
        elif line.startswith('physical id'):
            sockets.add(line.split(':', 1)[1].strip())
        elif line.startswith('core id'):
            cores.add(line.split(':', 1)[1].strip())
        elif line.startswith('processor'):
            threads.add(line.split(':', 1)[1].strip())
        elif line.startswith('bogomips'):
            val = line.split(':', 1)[1].strip()
            if bogomips is None:
                bogomips = val

    if model_name:
        result.append(("Модель процессора", model_name))
    if sockets:
        result.append(("Сокет", str(len(sockets))))
    if cores:
        result.append(("Ядра", str(len(cores) * max(1, len(sockets)))))
    if threads:
        result.append(("Потоки", str(len(threads))))
    if bogomips:
        result.append(("Побочная нагрузка (BogoMIPS)", bogomips))
    return result


def _parse_lscpu(output: str) -> List[Tuple[str, str]]:
    result = []
    mapping = {
        'Architecture': 'Архитектура',
        'CPU max MHz': 'Макс. частота (МГц)',
        'CPU min MHz': 'Мин. частота (МГц)',
        'CPU MHz': 'Частота (МГц)',
        'Hypervisor vendor': 'Гипервизор',
        'L1d cache': 'L1d кэш',
        'L1i cache': 'L1i кэш',
        'L2 cache': 'L2 кэш',
        'L3 cache': 'L3 кэш',
        'Flags': 'Флаги CPU',
    }
    for line in output.split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            if key in mapping:
                result.append((mapping[key], value.strip()))
    return result


def _parse_meminfo(output: str) -> List[Tuple[str, str]]:
    result = []
    mapping = {
        'MemTotal': 'Общая память',
        'MemAvailable': 'Доступная память',
        'Buffers': 'Буферы',
        'Cached': 'Кэш',
        'SwapTotal': 'Swap всего',
        'SwapFree': 'Swap свободно',
    }
    for line in output.split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            if key in mapping:
                val = value.strip()
                try:
                    kb = int(val.split()[0])
                    if kb >= 1048576:
                        val = f"{kb / 1048576:.1f} ГБ"
                    elif kb >= 1024:
                        val = f"{kb / 1024:.1f} МБ"
                except (ValueError, IndexError):
                    pass
                result.append((mapping[key], val))
    return result


def _parse_dimm(output: str) -> List[Tuple[str, str]]:
    if 'NO_ACCESS' in output:
        return []
    result = []
    devices = output.split('\n\n')
    idx = 1
    for dev in devices:
        dev = dev.strip()
        if not dev:
            continue
        loc = ""
        size = ""
        speed = ""
        typ = ""
        manu = ""
        part = ""
        for line in dev.split('\n'):
            line = line.strip()
            if line.startswith('Locator:'):
                loc = line.split(':', 1)[1].strip()
            elif line.startswith('Size:'):
                size = line.split(':', 1)[1].strip()
            elif line.startswith('Speed:'):
                speed = line.split(':', 1)[1].strip()
            elif line.startswith('Type:'):
                typ = line.split(':', 1)[1].strip()
            elif line.startswith('Manufacturer:'):
                manu = line.split(':', 1)[1].strip()
            elif line.startswith('Part Number:'):
                part = line.split(':', 1)[1].strip()
        if size and size != 'No Module Installed':
            label = f"Модуль RAM {idx}"
            value_parts = []
            if loc:
                value_parts.append(loc)
            if size:
                value_parts.append(size)
            if typ:
                value_parts.append(typ)
            if speed:
                value_parts.append(speed)
            if manu and manu != 'Unknown':
                value_parts.append(manu)
            result.append((label, " | ".join(value_parts)))
            idx += 1
    return result


def _parse_lspci_vga(output: str) -> List[Tuple[str, str]]:
    result = []
    driver = None
    device = None
    for line in output.split('\n'):
        line = line.strip()
        if 'VGA compatible controller' in line or 'Display controller' in line:
            device = line.split(':', 1)[1].strip() if ':' in line else line
        elif line.startswith('Kernel driver in use:'):
            driver = line.split(':', 1)[1].strip()
        elif 'Subsystem' in line:
            pass
    if device:
        result.append(("Видеокарта", device))
    if driver:
        result.append(("Драйвер", driver))
    return result


def _parse_lspci_audio(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if ':' not in line:
            continue
        slot, _, desc = line.partition(':')
        slot = slot.strip()
        desc = desc.strip()
        if 'audio' in desc.lower() or 'Audio' in desc:
            result.append((slot, desc))
        elif desc:
            result.append((slot, desc))
    return result


def _parse_alsa_cards(output: str) -> List[Tuple[str, str]]:
    if not output.strip():
        return []
    result = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)\s+\[([^\]]*)\s*\]\s*:\s*(.+)$', line)
        if m:
            idx = m.group(1)
            short = m.group(2).strip()
            full = m.group(3).strip()
            result.append((f"Карта {idx}", f"{short} — {full}" if short else full))
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                result.append((parts[0], parts[1]))
            elif len(parts) == 1 and parts[0]:
                result.append(("", parts[0]))
    return result


def _parse_lsblk(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) >= 5:
            name = parts[0]
            try:
                size_bytes = int(parts[1])
                if size_bytes >= 1099511627776:
                    size = f"{size_bytes / 1099511627776:.1f} ТБ"
                elif size_bytes >= 1073741824:
                    size = f"{size_bytes / 1073741824:.1f} ГБ"
                elif size_bytes >= 1048576:
                    size = f"{size_bytes / 1048576:.1f} МБ"
                else:
                    size = f"{size_bytes} Б"
            except (ValueError, IndexError):
                size = parts[1]
            dev_type = parts[2]
            fstype = parts[3] if len(parts) > 3 else ""
            mount = parts[4] if len(parts) > 4 else ""
            model = " ".join(parts[5:]) if len(parts) > 5 else ""

            label = f"/dev/{name}" if not name.startswith("/") else name
            value = f"{size} [{dev_type}]"
            if fstype:
                value += f" {fstype}"
            if mount:
                value += f" -> {mount}"
            if model:
                value += f" ({model})"
            result.append((label, value))
    return result


def _parse_df(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) >= 6:
            result.append((parts[0], f"{parts[1]} (использовано {parts[4]}) -> {parts[5]}"))
    return result


def _parse_ip_br(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) >= 2:
            iface = parts[0]
            status = parts[1]
            addr = parts[2] if len(parts) > 2 else ""
            result.append((iface, f"{status} {addr}".strip()))
    return result


def _parse_ip_addr(output: str) -> List[Tuple[str, str]]:
    result = []
    current_iface = ""
    for line in output.split('\n'):
        line = line.strip()
        if re.match(r'^\d+:', line):
            match = re.match(r'^\d+:\s*(\S+):', line)
            if match:
                current_iface = match.group(1)
        elif 'inet ' in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == 'inet' and i + 1 < len(parts):
                    result.append((f"  {current_iface} IPv4", parts[i + 1]))
        elif 'inet6 ' in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == 'inet6' and i + 1 < len(parts):
                    result.append((f"  {current_iface} IPv6", parts[i + 1]))
    return result


def _parse_resolv_conf(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if line.startswith('nameserver'):
            result.append(("DNS сервер", line.split(None, 1)[1] if len(line.split()) > 1 else ""))
        elif line.startswith('search'):
            result.append(("Поисковый домен", line.split(None, 1)[1] if len(line.split()) > 1 else ""))
    return result


def _parse_loadavg(output: str) -> List[Tuple[str, str]]:
    parts = output.strip().split()
    if len(parts) >= 3:
        return [("Нагрузка (1/5/15 мин)", f"{parts[0]} / {parts[1]} / {parts[2]}")]
    return []


def _parse_timezone(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if line.startswith('Timezone='):
            tz = line.split('=', 1)[1].strip()
            result.append(("Часовой пояс", tz))
            return result
        elif line.startswith('LocalRTC='):
            pass
        elif line and '=' not in line and '/' in line:
            result.append(("Часовой пояс", line))
            return result
    raw = output.strip()
    if raw:
        result.append(("Часовой пояс", raw))
    return result


def _parse_lsusb(output: str) -> List[Tuple[str, str]]:
    if 'NO_USB_INFO' in output:
        return []
    result = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        result.append(("USB устройство", line))
    return result


def _parse_usb_devices(output: str) -> List[Tuple[str, str]]:
    if 'NO_USB_DETAIL' in output:
        return []
    result = []
    current_device = None
    details = {}
    for line in output.split('\n'):
        line = line.strip()
        if line.startswith('T:'):
            if current_device and details:
                val_parts = []
                if details.get('prod'):
                    val_parts.append(details['prod'])
                if details.get('vendor') and details.get('prodid'):
                    val_parts.append(f"VID:PID={details['vendor']}:{details['prodid']}")
                if details.get('driver'):
                    val_parts.append(f"drv={details['driver']}")
                if details.get('speed'):
                    val_parts.append(f"speed={details['speed']}")
                if val_parts:
                    result.append((f"USB {current_device}", " | ".join(val_parts)))
            match = re.match(r'T:\s+Bus=(\S+)\s+Lev=(\S+)', line)
            if match:
                current_device = f"Bus {match.group(1)} Lev {match.group(2)}"
                details = {}
        elif line.startswith('P:') and current_device:
            for part in line.split():
                if part.startswith('Vendor='):
                    details['vendor'] = part.split('=', 1)[1]
                elif part.startswith('ProdID='):
                    details['prodid'] = part.split('=', 1)[1]
        elif line.startswith('S:') and current_device:
            if 'Product=' in line:
                details['prod'] = line.split('Product=', 1)[1].strip()
        elif line.startswith('D:') and current_device:
            for part in line.split():
                if part.startswith('Ver='):
                    details['ver'] = part.split('=', 1)[1]
        elif line.startswith('C:') and current_device:
            for part in line.split():
                if part.startswith('Driver='):
                    details['driver'] = part.split('=', 1)[1]
        elif line.startswith('Spd=') or 'Spd=' in line:
            match_s = re.search(r'Spd=(\S+)', line)
            if match_s:
                details['speed'] = match_s.group(1)
    if current_device and details:
        val_parts = []
        if details.get('prod'):
            val_parts.append(details['prod'])
        if details.get('vendor') and details.get('prodid'):
            val_parts.append(f"VID:PID={details['vendor']}:{details['prodid']}")
        if details.get('driver'):
            val_parts.append(f"drv={details['driver']}")
        if details.get('speed'):
            val_parts.append(f"speed={details['speed']}")
        if val_parts:
            result.append((f"USB {current_device}", " | ".join(val_parts)))
    return result


def _parse_passwd_users(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        parts = line.strip().split(':')
        if len(parts) >= 3:
            username = parts[0]
            try:
                uid = int(parts[1])
                shell = parts[2] if len(parts) > 2 else ""
                human = uid >= 1000 and uid < 65534
                if human:
                    shell_info = f" ({shell})" if shell else ""
                    result.append((username, f"UID={uid}{shell_info}"))
            except (ValueError, IndexError):
                pass
    final = []
    if result:
        final.append(("Обычные пользователи", "\n".join(f"{u}: {v}" for u, v in result)))
    return final


def _parse_home_stats(output: str) -> List[Tuple[str, str]]:
    def _fmt_size(b: str) -> str:
        try:
            b = int(b)
            if b >= 1073741824:
                return f"{b / 1073741824:.1f} ГБ"
            elif b >= 1048576:
                return f"{b / 1048576:.1f} МБ"
            elif b >= 1024:
                return f"{b / 1024:.1f} КБ"
            else:
                return f"{b} Б"
        except (ValueError, TypeError):
            return b

    lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
    if not lines:
        return []
    stats = []
    for line in lines:
        parts = line.split('|')
        if len(parts) >= 4:
            user, size, files, dirs = parts[0], parts[1], parts[2], parts[3]
            stats.append(f"{user}: {_fmt_size(size)}, файлов: {files}, папок: {dirs}")
    if stats:
        return [("Статистика /home", "\n".join(stats))]
    return []


def _parse_passwd_table(output: str) -> List[Tuple[str, ...]]:
    """Parse getent passwd into rows for table: [Пользователь, UID, GID, Домашняя папка, Shell, Тип]."""
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(':')
        if len(parts) < 7:
            continue
        username, passwd, uid, gid, gecos, home, shell = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            uid_int = -1
        if uid_int == 0:
            user_type = "root"
        elif uid_int < 1000:
            user_type = "system"
        elif uid_int >= 1000 and uid_int < 65534:
            user_type = "regular"
        else:
            user_type = "system"
        rows.append((username, uid, gid, home, shell, user_type))
    return rows


def _parse_who_table(output: str) -> List[Tuple[str, ...]]:
    """Parse who into rows for table: [Пользователь, Терминал, Дата, Время, Откуда]."""
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        user = parts[0]
        tty = parts[1]
        date = parts[2]
        time = parts[3]
        from_host = parts[4] if len(parts) > 4 else ""
        rows.append((user, tty, date, time, from_host))
    return rows


def _parse_last_table(output: str) -> List[Tuple[str, ...]]:
    """Parse last/lastb into rows for table: [Пользователь, Терминал, Хост, Дата/время, Статус]."""
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('wtmp'):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        user = parts[0]
        tty = parts[1]
        host = parts[2]
        day = parts[4]
        month = parts[5]
        time = parts[6]
        status = parts[7]
        rows.append((user, tty, host, f"{day} {month} {time}", status))
    return rows


def _parse_home_dirs_table(output: str) -> List[Tuple[str, ...]]:
    """Parse du output into rows for table: [Пользователь, Размер]."""
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) >= 2:
            size = parts[0].strip()
            path = parts[1].strip()
        else:
            sp = line.split(None, 1)
            if len(sp) >= 2:
                size = sp[0].strip()
                path = sp[1].strip()
            else:
                continue
        user = path.split('/')[-1]
        rows.append((user, size))
    return rows


def _parse_group_table(output: str) -> List[Tuple[str, ...]]:
    """Parse getent group into rows for table: [Группа, GID, Пользователи]."""
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(':')
        if len(parts) < 4:
            continue
        group_name = parts[0]
        gid = parts[2]
        members = parts[3]
        rows.append((group_name, gid, members))
    return rows


def _parse_group_details(output: str) -> List[Tuple[str, str]]:
    result = []
    groups_with_users = []
    for line in output.strip().split('\n'):
        parts = line.strip().split(':')
        if len(parts) >= 4:
            group_name = parts[0]
            members = parts[3]
            if members.strip():
                groups_with_users.append(f"{group_name}: {members}")
    if groups_with_users:
        result.append(("Группы с пользователями", "\n".join(groups_with_users)))
    return result


def _parse_df_table(output: str) -> List[Tuple[str, str]]:
    from collections import OrderedDict
    seen = OrderedDict()
    for line in output.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) < 6:
            continue
        filesystem = parts[0]
        if filesystem in ('Filesystem', 'Файловая'):
            continue
        size = parts[1]
        used = parts[2]
        avail = parts[3]
        pcent = parts[4]
        target = parts[5]
        if filesystem == 'total':
            seen['ИТОГО'] = ('ИТОГО', size, used, avail, pcent, target)
        else:
            seen[filesystem] = (filesystem, size, used, avail, pcent, target)
    return list(seen.values())


def _parse_ss_row(line: str) -> Optional[List[str]]:
    line = line.strip()
    if not line:
        return None
    low = line.lower()
    if low.startswith('state') or low.startswith('recv-q') or low.startswith('netid') or low.startswith('proto') or low.startswith('активные'):
        return None
    state_match = re.match(r'^(ESTAB|LISTEN|TIME-WAIT|SYN-SENT|SYN-RECV|FIN-WAIT-\d|CLOSE-WAIT|LAST-ACK|CLOSING|UNCONN)\s+', line)
    if state_match:
        state = state_match.group(1)
        rest = line[state_match.end():]
    else:
        state = ""
        rest = line
    parts = rest.split()
    if len(parts) < 4:
        return None
    recv_q = parts[0]
    send_q = parts[1]
    local = parts[2]
    peer = parts[3] if len(parts) > 3 else ""
    process = ""
    proc_match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', rest)
    if proc_match:
        process = f"{proc_match.group(1)}/{proc_match.group(2)}"
    return [state, recv_q, send_q, local, peer, process]


def _parse_ss_listening(output: str) -> List[Tuple[str, str]]:
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parsed = _parse_ss_row(line)
        if parsed:
            rows.append(tuple(parsed))
    if not rows:
        if output.strip():
            return [("Прослушиваемые порты", output.strip())]
        return []
    return rows


def _parse_ss_established(output: str) -> List[Tuple[str, str]]:
    rows = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parsed = _parse_ss_row(line)
        if parsed:
            rows.append(tuple(parsed))
    if not rows:
        if output.strip():
            return [("Установленные соединения", output.strip())]
        return []
    return rows


class DeviceInfoWorker(QThread):
    finished_signal = Signal(str, dict)
    error_signal = Signal(str, str)
    progress_signal = Signal(int, str)
    all_finished_signal = Signal(dict)

    def __init__(self, device: DeviceModel, categories: Dict):
        super().__init__()
        self.device = device
        self.categories = categories
        self._aborting = False
        self._client = None

    def run(self):
        import paramiko
        from src.config import config

        all_results = {}
        client = None
        total = len(self.categories)
        current = 0

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client = client
            port = self.device.port or config.app.ssh.port
            creds = get_credentials(self.device, use_key=True)

            if not self._aborting:
                self.progress_signal.emit(0, f"Подключение к {self.device.host}...")
            client.connect(
                hostname=self.device.host,
                port=port,
                username=creds.username,
                password=creds.password,
                pkey=creds.private_key,
                timeout=15
            )

            for cat_name, cat_data in self.categories.items():
                if self._aborting:
                    break

                current += 1
                percent = int((current / total) * 100)
                if not self._aborting:
                    self.progress_signal.emit(percent, f"Получение: {cat_name}...")
                cat_results = {}

                for cmd_name, cmd_text in cat_data.get("commands", {}).items():
                    if self._aborting:
                        break
                    try:
                        stdin, stdout, stderr = client.exec_command(cmd_text, timeout=30)
                        output = stdout.read().decode('utf-8', errors='replace')
                        cat_results[cmd_name] = output
                    except Exception as e:
                        if self._aborting:
                            break
                        cat_results[cmd_name] = f"ERROR: {e}"

                if self._aborting:
                    break

                all_results[cat_name] = cat_results
                self.finished_signal.emit(cat_name, cat_results)

            if not self._aborting:
                self.all_finished_signal.emit(all_results)

        except Exception as e:
            if not self._aborting:
                self.error_signal.emit("connection", f"Ошибка подключения: {e}")
        finally:
            self._client = None
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def abort(self):
        self._aborting = True
        if self._client:
            try:
                transport = self._client.get_transport()
                if transport:
                    transport.close()
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass



def _human_bytes(value: float) -> str:
    for unit, label in [(1099511627776, "T"), (1073741824, "G"), (1048576, "M"), (1024, "K")]:
        if abs(value) >= unit:
            return f"{value / unit:.1f}{label}"
    return f"{int(value)}B"


def _human_kb(value: float) -> str:
    return _human_bytes(value * 1024)


def _to_sortable_value(value: str) -> Optional[float]:
    value = value.strip()
    if value.endswith('%'):
        try:
            return float(value[:-1])
        except ValueError:
            pass
    try:
        return float(value)
    except ValueError:
        pass
    return None


class NumericTableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        my_val = self.data(Qt.ItemDataRole.UserRole)
        other_val = other.data(Qt.ItemDataRole.UserRole)
        if my_val is not None and other_val is not None:
            try:
                return float(my_val) < float(other_val)
            except (ValueError, TypeError):
                pass
        return super().__lt__(other)


class DeviceInfoDialog(QDialog):
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.worker = None
        self._closing = False
        self._raw_data = {}
        self._parsed_data = {}
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(f"Информация об устройстве — {self.device.name}")
        self.setMinimumSize(1000, 650)
        self.resize(1100, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        host_label = QLabel(f"<b>{self.device.name}</b> — {self.device.host}")
        header_layout.addWidget(host_label)
        header_layout.addStretch()

        self.refresh_btn = None
        if self.device.is_online:
            self.refresh_btn = QPushButton("Обновить")
            self.refresh_btn.setFixedWidth(100)
            self.refresh_btn.clicked.connect(self._start_fetch)
            header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(200)
        self.tree.setMaximumWidth(300)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        splitter.addWidget(self.tree)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Свойство", "Значение"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        splitter.addWidget(self.table)

        splitter.setSizes([220, 780])
        layout.addWidget(splitter, stretch=1)

        bottom_bar = QHBoxLayout()

        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 100)
        self.status_progress.setFixedWidth(200)
        self.status_progress.setTextVisible(False)
        self.status_progress.setVisible(False)
        bottom_bar.addWidget(self.status_progress)

        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(200)
        bottom_bar.addWidget(self.status_label, 1)

        self.save_btn = QPushButton("Сохранить...")
        self.save_btn.setIcon(QIcon(ICONS.get('menu_save', '')))
        self.save_btn.setFixedWidth(100)
        self.save_btn.clicked.connect(self._save_report)
        bottom_bar.addWidget(self.save_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        bottom_bar.addWidget(buttons)

        layout.addLayout(bottom_bar)

        self._populate_tree()
        self._show_summary()

        if self.device.is_online:
            self.status_progress.setVisible(True)
            self.status_progress.setValue(0)
            self.status_label.setText("Загрузка информации...")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._start_fetch)
        else:
            self.status_label.setText("Устройство офлайн — подключение невозможно")

    def _populate_tree(self):
        self.tree.clear()
        sorted_cats = sorted(INFO_CATEGORIES.items(), key=lambda x: x[1]["order"])

        summary_item = QTreeWidgetItem(self.tree, ["Сводка"])
        summary_item.setData(0, Qt.ItemDataRole.UserRole, "__summary__")
        summary_item.setIcon(0, QIcon(ICONS.get('menu_info', '')))

        cat_to_group = {}
        for group_name, cats in TREE_GROUPS.items():
            for cat in cats:
                cat_to_group[cat] = group_name

        group_items = {}
        for cat_name, cat_data in sorted_cats:
            group_name = cat_to_group.get(cat_name, cat_name)
            if group_name not in group_items:
                group_item = QTreeWidgetItem(self.tree, [group_name])
                group_item.setData(0, Qt.ItemDataRole.UserRole, f"__group__:{group_name}")
                group_icon = CATEGORY_ICONS.get(group_name)
                if group_icon:
                    group_item.setIcon(0, QIcon(ICONS.get(group_icon, '')))
                group_items[group_name] = group_item
            else:
                group_item = group_items[group_name]

            child_item = QTreeWidgetItem(group_item, [cat_name])
            child_item.setData(0, Qt.ItemDataRole.UserRole, cat_name)
            cat_icon = CATEGORY_ICONS.get(cat_name)
            if cat_icon:
                child_item.setIcon(0, QIcon(ICONS.get(cat_icon, '')))

        self.tree.expandAll()
        self.tree.setCurrentItem(summary_item)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key == "__summary__":
            self._show_summary()
        elif key in self._parsed_data:
            self._show_category(key)
        else:
            self.table.setRowCount(0)

    def _show_summary(self):
        self.table.setRowCount(0)
        rows = []

        rows.append(("Имя устройства", self.device.name))
        rows.append(("Хост", self.device.host))
        rows.append(("Порт", str(self.device.port or 22)))
        rows.append(("Пользователь", self.device.login or "(по умолчанию)"))
        rows.append(("MAC-адрес", self.device.mac_address or "—"))
        rows.append(("Статус", "Онлайн" if self.device.is_online else "Офлайн"))

        for cat_name in INFO_CATEGORIES:
            if cat_name in self._parsed_data:
                cat_config = INFO_CATEGORIES.get(cat_name, {})
                if cat_config.get("is_table"):
                    continue
                for item in self._parsed_data[cat_name]:
                    field, value = item[0], item[1]
                    if value and value != "—" and not value.startswith("ERROR"):
                        rows.append((field, value))

        self._fill_table(rows)

    def _show_category(self, cat_name: str):
        if cat_name not in self._parsed_data:
            self.table.setRowCount(0)
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Свойство", "Значение"])
            return
        cat_config = INFO_CATEGORIES.get(cat_name, {})
        if cat_config.get("is_table") and cat_config.get("table_columns"):
            self._fill_multi_column_table(cat_name, cat_config)
        else:
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Свойство", "Значение"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self._fill_table(self._parsed_data[cat_name])

    def _fill_multi_column_table(self, cat_name: str, cat_config: dict):
        columns = cat_config["table_columns"]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        for col in range(len(columns)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        if len(columns) > 1:
            self.table.horizontalHeader().setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)

        rows = self._parsed_data.get(cat_name, [])
        if not rows:
            self.table.setRowCount(0)
            self.table.setSortingEnabled(True)
            return

        size_columns = cat_config.get("size_columns", {})
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, row_data in enumerate(rows):
            for col in range(len(columns)):
                raw_value = str(row_data[col]) if col < len(row_data) else ""
                unit = size_columns.get(col)
                sort_val = None
                if unit:
                    try:
                        num = float(raw_value)
                        sort_val = num * (1024 if unit == "kb" else 1)
                        if unit == "kb":
                            display_value = _human_kb(num)
                        else:
                            display_value = _human_bytes(num)
                    except ValueError:
                        display_value = raw_value
                else:
                    display_value = raw_value
                    sort_val = _to_sortable_value(raw_value)
                if sort_val is not None:
                    item = NumericTableItem(display_value)
                    item.setData(Qt.ItemDataRole.UserRole, sort_val)
                else:
                    item = QTableWidgetItem(display_value)
                item.setToolTip(display_value)
                self.table.setItem(i, col, item)

        self.table.setSortingEnabled(True)
        self.table.resizeRowsToContents()

    def _fill_table(self, rows: List[Tuple[str, str]]):
        grouped_rows = []
        for prop, val in rows:
            lines = val.strip().split('\n')
            if len(lines) > 1:
                grouped_rows.append((prop, '\n'.join(lines)))
            else:
                grouped_rows.append((prop, val))

        self.table.setRowCount(len(grouped_rows))
        for i, (prop, val) in enumerate(grouped_rows):
            prop_item = QTableWidgetItem(prop)
            val_item = QTableWidgetItem(val)
            val_item.setToolTip(val)
            self.table.setItem(i, 0, prop_item)
            self.table.setItem(i, 1, val_item)

        self.table.resizeRowsToContents()

    def _start_fetch(self):
        if self.worker and self.worker.isRunning():
            return

        self.worker = DeviceInfoWorker(self.device, INFO_CATEGORIES)
        self.worker.finished_signal.connect(self._on_category_data)
        self.worker.error_signal.connect(self._on_error)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.all_finished_signal.connect(self._on_all_finished)
        self.worker.start()

        self.status_progress.setVisible(True)
        self.status_progress.setValue(0)
        self.status_label.setText("Загрузка информации...")
        if self.refresh_btn:
            self.refresh_btn.setEnabled(False)

    def _on_category_data(self, cat_name: str, raw_results: dict):
        if self._closing:
            return
        self._raw_data[cat_name] = raw_results
        cat_config = INFO_CATEGORIES.get(cat_name, {})
        parsers = cat_config.get("parse", {})

        parsed = []
        fields_order = cat_config.get("fields_order", [])

        for cmd_name, output in raw_results.items():
            if cmd_name in parsers:
                try:
                    items = parsers[cmd_name](output)
                    parsed.extend(items)
                except Exception as e:
                    logger.warning(f"Parse error for {cat_name}/{cmd_name}: {e}")

        if fields_order:
            ordered = []
            field_map = {k: v for k, v in parsed}
            for f in fields_order:
                if f in field_map:
                    ordered.append((f, field_map[f]))
            for k, v in parsed:
                if k not in fields_order:
                    ordered.append((k, v))
            parsed = ordered

        self._parsed_data[cat_name] = parsed

        current_item = self.tree.currentItem()
        if current_item:
            key = current_item.data(0, Qt.ItemDataRole.UserRole)
            if key == cat_name:
                self._show_category(cat_name)
            elif key == "__summary__":
                self._show_summary()

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == cat_name:
                break

    def _on_error(self, error_type: str, message: str):
        if self._closing:
            return
        self.status_progress.setVisible(False)
        self.status_label.setText(f"Ошибка: {message}")

    def _on_progress(self, percent: int, msg: str):
        if self._closing:
            return
        self.status_progress.setValue(percent)
        self.status_label.setText(msg)

    def _on_all_finished(self, all_results: dict):
        if self._closing:
            return
        self.status_progress.setVisible(False)
        self.status_label.setText("Информация загружена")
        if self.refresh_btn:
            self.refresh_btn.setEnabled(True)

        current_item = self.tree.currentItem()
        if current_item:
            key = current_item.data(0, Qt.ItemDataRole.UserRole)
            if key == "__summary__":
                self._show_summary()

    def _save_report(self):
        from PySide6.QtWidgets import QFileDialog
        from datetime import datetime

        default_name = f"device_info_{self.device.host}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт", default_name, "Текстовые файлы (*.txt);;Все файлы (*)"
        )
        if not file_path:
            return

        lines = []
        lines.append(f"Информация об устройстве: {self.device.name} ({self.device.host})")
        lines.append(f"Порт: {self.device.port or 22}")
        if self.device.login:
            lines.append(f"Пользователь: {self.device.login}")
        if self.device.mac_address:
            lines.append(f"MAC-адрес: {self.device.mac_address}")
        lines.append(f"Статус: {'Онлайн' if self.device.is_online else 'Офлайн'}")
        lines.append("")

        for cat_name, cat_data in INFO_CATEGORIES.items():
            if cat_name not in self._parsed_data:
                continue
            parsed = self._parsed_data[cat_name]
            if not parsed:
                continue
            lines.append(f"--- {cat_name} ---")
            for prop, val in parsed:
                for line_idx, line in enumerate(val.split('\n')):
                    if line_idx == 0:
                        lines.append(f"  {prop}: {line}")
                    else:
                        lines.append(f"  {' ' * (len(prop) + 2)} {line}")
            lines.append("")

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            self.status_label.setText(f"Отчёт сохранён: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить отчёт:\n{e}")

    def _stop_threads(self):
        self._closing = True
        if self.worker is not None:
            self.worker.abort()
            self.worker.blockSignals(True)
            if self.worker.isRunning():
                self.worker.wait(5000)

    def closeEvent(self, event):
        self._stop_threads()
        event.accept()

    def reject(self):
        self._stop_threads()
        super().reject()

    def accept(self):
        self._stop_threads()
        super().accept()