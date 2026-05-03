import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
    QSplitter, QMessageBox, QAbstractItemView, QWidget, QDialogButtonBox,
    QProgressBar, QPushButton
)
from PySide6.QtCore import Qt, QThread, Signal

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
            "audio": lambda out: [("Аудиоустройства PCI", out.strip())] if out.strip() else [],
            "alsa": lambda out: [("Звуковые карты ALSA", out.strip())] if out.strip() else [],
        },
        "fields_order": [],
    },
    "Сеть — Подключения": {
        "order": 10,
        "commands": {
            "listening": "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
            "established": "ss -tnp state established 2>/dev/null || netstat -tnp 2>/dev/null | grep ESTABLISHED",
        },
        "parse": {
            "listening": lambda out: [("Слушающие порты", out.strip())] if out.strip() else [],
            "established": lambda out: [("Установленные соединения", out.strip())] if out.strip() else [],
        },
        "fields_order": [],
    },
    "Пользователи": {
        "order": 11,
        "commands": {
            "logged_in": "who -H 2>/dev/null || w -h 2>/dev/null",
            "all_users": "cat /etc/passwd | grep -v nologin | grep -v false | grep -v sync | cut -d: -f1,3,7",
            "sudo_users": "getent group sudo wheel adm 2>/dev/null",
            "last_logins": "last -n 10 2>/dev/null || echo 'NO_LAST'",
            "failed_logins": "lastb -n 10 2>/dev/null || echo 'NO_LASTB'",
            "user_count": "cat /etc/passwd | wc -l",
            "home_dirs": "du -sh /home/* 2>/dev/null | sort -rh",
        },
        "parse": {
            "logged_in": lambda out: [("Текущие сеансы", out.strip())] if out.strip() and 'NO_' not in out else [],
            "all_users": lambda out: _parse_passwd_users(out),
            "sudo_users": lambda out: [("Пользователи с sudo", out.strip())] if out.strip() else [],
            "last_logins": lambda out: [("Последние входы", out.strip())] if out.strip() and 'NO_LAST' not in out else [],
            "failed_logins": lambda out: [("Неудачные входы", out.strip())] if out.strip() and 'NO_LASTB' not in out else [],
            "user_count": lambda out: [("Всего пользователей (системных)", out.strip())] if out.strip() else [],
            "home_dirs": lambda out: [("Папки пользователей", out.strip())] if out.strip() else [],
        },
        "fields_order": ["Всего пользователей (системных)", "Пользователи с sudo", "Текущие сеансы", "Последние входы", "Неудачные входы", "Обычные пользователи", "Папки пользователей"],
    },
    "Группы": {
        "order": 12,
        "commands": {
            "groups": "cat /etc/group | cut -d: -f1",
            "group_details": "cat /etc/group",
        },
        "parse": {
            "groups": lambda out: [("Группы", out.strip())] if out.strip() else [],
            "group_details": lambda out: _parse_group_details(out),
        },
        "fields_order": ["Группы", "Группы с пользователями"],
    },
    "Дисковое пространство": {
        "order": 13,
        "commands": {
            "df_all": "df -h --total 2>/dev/null || df -h",
        },
        "parse": {
            "df_all": lambda out: _parse_df_detailed(out),
        },
        "fields_order": [],
    },
}


TREE_GROUPS = {
    "Система": ["Компьютер", "Процессор", "Память", "Материнская плата"],
    "Устройства": ["Накопители", "Видеокарта", "USB", "Звук", "Оптические приводы"],
    "Сеть": ["Сеть", "Сеть — Подключения"],
    "Пользователи и безопасность": ["Пользователи", "Группы"],
    "Хранение": ["Дисковое пространство"],
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


def _parse_df_detailed(output: str) -> List[Tuple[str, str]]:
    result = []
    for line in output.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) >= 6:
            filesystem = parts[0]
            if filesystem == 'Filesystem' or filesystem == 'Файловая':
                continue
            if filesystem == 'total':
                label = "ИТОГО"
            else:
                label = filesystem
            size = parts[1]
            used = parts[2]
            avail = parts[3]
            pcent = parts[4]
            target = parts[5]
            result.append((label, f"{size} (использовано {used} / {pcent}, свободно {avail}) -> {target}"))
    return result


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
            port = self.device.port or config.app.ssh.port
            creds = get_credentials(self.device, use_key=True)

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
                        cat_results[cmd_name] = f"ERROR: {e}"

                all_results[cat_name] = cat_results
                self.finished_signal.emit(cat_name, cat_results)

            self.all_finished_signal.emit(all_results)

        except Exception as e:
            self.error_signal.emit("connection", f"Ошибка подключения: {e}")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def abort(self):
        self._aborting = True


class DeviceInfoDialog(QDialog):
    def __init__(self, device: DeviceModel, parent=None):
        super().__init__(parent)
        self.device = device
        self.worker = None
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
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMouseTracking(True)
        self.table.viewport().installEventFilter(self)
        splitter.addWidget(self.table)

        splitter.setSizes([220, 780])
        layout.addWidget(splitter, stretch=1)

        bottom_layout = QHBoxLayout()

        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(200)
        bottom_layout.addWidget(self.status_label, stretch=1)

        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 100)
        self.status_progress.setFixedWidth(200)
        self.status_progress.setTextVisible(True)
        self.status_progress.setVisible(False)
        bottom_layout.addWidget(self.status_progress)

        self.save_btn = QPushButton("Сохранить...")
        self.save_btn.setFixedWidth(100)
        self.save_btn.clicked.connect(self._save_report)
        bottom_layout.addWidget(self.save_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        bottom_layout.addWidget(buttons)

        layout.addLayout(bottom_layout)

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
                group_items[group_name] = group_item
            else:
                group_item = group_items[group_name]

            child_item = QTreeWidgetItem(group_item, [cat_name])
            child_item.setData(0, Qt.ItemDataRole.UserRole, cat_name)

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
                for field, value in self._parsed_data[cat_name]:
                    if value and value != "—" and not value.startswith("ERROR"):
                        rows.append((field, value))

        self._fill_table(rows)

    def _show_category(self, cat_name: str):
        self.table.setRowCount(0)
        if cat_name not in self._parsed_data:
            return
        self._fill_table(self._parsed_data[cat_name])

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
        self.status_progress.setVisible(False)
        self.status_label.setText(f"Ошибка: {message}")

    def _on_progress(self, percent: int, msg: str):
        self.status_progress.setValue(percent)
        self.status_label.setText(msg)

    def _on_all_finished(self, all_results: dict):
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

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == event.Type.MouseMove:
            index = self.table.indexAt(event.pos())
            if index.isValid():
                self.table.selectRow(index.row())
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.abort()
            self.worker.wait(3000)
        super().closeEvent(event)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.abort()
            self.worker.wait(3000)
        super().reject()