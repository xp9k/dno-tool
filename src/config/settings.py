"""
Модуль конфигурации приложения DNO Tool.

Содержит классы данных для настроек SSH, сети и приложения в целом,
а также глобальный синглтон ``config`` для доступа к конфигурации.
Определяет пути к файлам настроек, хостов, команд и логов,
порты для сканирования и словарь иконок.
"""

import os
import sys
import json
import threading
from dataclasses import dataclass, asdict, field
from typing import Dict
from pathlib import Path
from os.path import expanduser

# Import logger with fallback for compatibility
try:
    from src.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

def get_asset_path(filename: str) -> str:
    """
    Получить абсолютный путь к ресурсному файлу в каталоге ``assets/``.

    При запуске из PyInstaller-пакета используется ``sys._MEIPASS``,
    иначе — корень проекта (на два уровня выше ``src/config/``).

    Args:
        filename: Имя файла в каталоге ``assets/``.

    Returns:
        Абсолютный путь к файлу ресурса.
    """
    if getattr(sys, 'frozen', False):  # Если программа собрана в exe
        base_path = Path(sys._MEIPASS)
    else:  # Если запуск как обычный Python-скрипт
        # Go up two levels from src/config/ to reach project root
        base_path = Path(__file__).parent.parent.parent
    return str(base_path / "assets" / filename)

# Resource paths configuration
ICONS: Dict[str, str] = {
    'folder': get_asset_path('folder.svg'),
    'online': get_asset_path('online.svg'),
    'offline': get_asset_path('offline.svg'),
    'default': get_asset_path('default.svg'),
    'shield': get_asset_path('shield.svg'),
    'command': get_asset_path('command.svg'),
    'result_success': get_asset_path('result_success.svg'),
    'result_failure': get_asset_path('result_failure.svg'),
    'result_cancelled': get_asset_path('result_cancelled.svg'),
    'result_warning': get_asset_path('result_warning.svg'),
    'result_pending': get_asset_path('result_pending.svg'),
    'result_executing': get_asset_path('result_executing.svg'),
    'result_connection_lost': get_asset_path('result_connection_lost.svg'),
    'key_exists': get_asset_path('key_exists.svg'),
    'key_missing': get_asset_path('key_missing.svg'),
    'menu_file': get_asset_path('menu_file.svg'),
    'menu_save': get_asset_path('menu_save.svg'),
    'menu_import': get_asset_path('menu_import.svg'),
    'menu_export': get_asset_path('menu_export.svg'),
    'menu_hosts': get_asset_path('menu_hosts.svg'),
    'menu_commands': get_asset_path('menu_commands.svg'),
    'menu_settings': get_asset_path('menu_settings.svg'),
    'menu_tools': get_asset_path('menu_tools.svg'),
    'menu_terminal': get_asset_path('menu_terminal.svg'),
    'menu_sftp': get_asset_path('menu_sftp.svg'),
    'menu_scanner': get_asset_path('menu_scanner.svg'),
    'menu_ping': get_asset_path('menu_ping.svg'),
    'menu_info': get_asset_path('menu_info.svg'),
    'menu_help': get_asset_path('menu_help.svg'),
    'menu_delete': get_asset_path('menu_delete.svg'),
    'menu_exit': get_asset_path('menu_exit.svg'),
    'menu_kde': get_asset_path('menu_kde.svg'),
    'menu_recording': get_asset_path('menu_recording.svg'),
    'menu_polkit': get_asset_path('menu_polkit.svg'),
    'menu_folder': get_asset_path('folder.svg'),
    'menu_import_host': get_asset_path('menu_import.svg'),
    'menu_export_host': get_asset_path('menu_export.svg'),
    'menu_tools_grid': get_asset_path('menu_tools_grid.svg'),
    'info_system': get_asset_path('info_system.svg'),
    'info_devices': get_asset_path('info_devices.svg'),
    'info_network': get_asset_path('info_network.svg'),
    'info_users': get_asset_path('info_users.svg'),
    'info_storage': get_asset_path('info_storage.svg'),
    'info_computer': get_asset_path('info_computer.svg'),
    'info_cpu': get_asset_path('info_cpu.svg'),
    'info_memory': get_asset_path('info_memory.svg'),
    'info_motherboard': get_asset_path('info_motherboard.svg'),
    'info_gpu': get_asset_path('info_gpu.svg'),
    'info_disk': get_asset_path('info_disk.svg'),
    'info_usb': get_asset_path('info_usb.svg'),
    'info_sound': get_asset_path('info_sound.svg'),
    'info_optical': get_asset_path('info_optical.svg'),
    'info_listening': get_asset_path('info_listening.svg'),
    'info_connected': get_asset_path('info_connected.svg'),
    'info_user': get_asset_path('info_user.svg'),
    'info_groups': get_asset_path('info_groups.svg'),
    'info_disk_space': get_asset_path('info_disk_space.svg'),
}

# Default ports (used if nothing stored in settings)
DEFAULT_PORTS = {
    21: True,
    22: True,
    23: False,
    53: True,
    80: True,
    88: False,
    123: False,
    135: False,
    139: False,
    389: False,
    443: True,
    445: False,
    3389: True,
    8080: True,
    8443: True,
    8888: False
}

_ports_lock = threading.Lock()

class _PortsDict(dict):
    """
    Потокобезопасный словарь для портов сканирования.

    Делегирует мутабельные операции через ``RLock``, чтобы
    ``PORTS`` можно было безопасно обновлять из разных потоков.
    """

    def __setitem__(self, key: int, value: bool) -> None:
        with _ports_lock:
            super().__setitem__(key, value)
    def __delitem__(self, key: int) -> None:
        with _ports_lock:
            super().__delitem__(key)
    def update(self, *args, **kwargs):
        with _ports_lock:
            super().update(*args, **kwargs)
    def clear(self):
        with _ports_lock:
            super().clear()

# Module-level PORTS dict (will be updated from saved config on load)
PORTS = _PortsDict(DEFAULT_PORTS)

config_folder_name = ".dnotool"


DEFAULT_SETTINGS_FILE = expanduser('~') + os.sep + config_folder_name + os.sep + 'settings.json' # 'settings.json'
DEFAULT_COMMANDS_FILE = expanduser('~') + os.sep + config_folder_name + os.sep + 'commands.json' #'commands.json'
DEFAULT_HOSTS_FILE = expanduser('~') + os.sep + config_folder_name + os.sep + 'hosts.json' #'hosts.json'
DEFAULT_SSH_PRIVATE_KEY_PATH = expanduser('~') + os.sep + '.ssh' + os.sep + 'id_ed25519' #'~/.ssh/id_ed25519'
DEFAULT_SSH_PUBLIC_KEY_PATH = expanduser('~') + os.sep + '.ssh' + os.sep + 'id_ed25519.pub' #'~/.ssh/id_ed25519.pub'
LOGS_PATH = expanduser('~') + os.sep + config_folder_name + os.sep  #'~/.dnotool'

SSH_RECV_BUFFER_SIZE = 4096

try:
    configs_dir = os.path.dirname(DEFAULT_SETTINGS_FILE)
    if not os.path.exists(configs_dir):
        logger.info(f"Creating {configs_dir} directory")
        os.makedirs(configs_dir)
        from src.utils.fs_utils import ensure_user_owned
        ensure_user_owned(configs_dir)
except Exception as e:
    logger.error(f"Error creating {configs_dir} directory: {e}")
    print(e)


@dataclass
class SSHConfig:
    """Настройки SSH-подключения к удалённым устройствам."""

    username: str = "root"
    password: str = ""
    port: int = 22                       # SSH-порт
    strict_host_checking: bool = False   # Строгая проверка ключей хоста
    command_timeout: int = 30            # Таймаут выполнения команды (секунды)
    ssh_connect_timeout: int = 10       # Таймаут SSH-соединения (секунды)
    connect_timeout: int = 5             # TCP-таймаут для оффлайн-хостов (секунды)
    max_as_completed_timeout: int = 600  # Максимальный таймаут асинхронного выполнения (секунды)

@dataclass
class NetworkConfig:
    """Сетевые настройки (пинг, потоки)."""

    ping_timeout: float = 2.0
    ping_interval: int = 30     # Интервал между пингами (секунды)
    thread_count: int = 8       # Количество потоков


@dataclass
class AppConfig:
    """Корневая конфигурация приложения."""

    ssh: SSHConfig = field(default_factory=SSHConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    expand: bool = False

class Config:
    """
    Менеджер конфигурации приложения.

    Загружает настройки из JSON-файла при инициализации и позволяет
    сохранять изменения обратно. Синглтон-экземпляр доступен как ``config``.
    """

    def __init__(self) -> None:
        self.app = AppConfig()
        self.config_file = DEFAULT_SETTINGS_FILE
        self.load()

    def load(self) -> None:
        """Загрузить конфигурацию из файла ``self.config_file``."""
        try:
            if os.path.exists(self.config_file):
                logger.info(f"Loading config from {self.config_file}")
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'ssh' in data:
                        self.app.ssh = SSHConfig(**data['ssh'])
                    if 'network' in data:
                        # Separate 'ports' (stored under network) from the rest before creating NetworkConfig
                        network_data = dict(data['network'])
                        ports = network_data.pop('ports', None)
                        try:
                            self.app.network = NetworkConfig(**network_data)
                        except Exception as e:
                            logger.error(f"Error loading network config: {e}")

                        # Load ports if present (stored under 'network' -> 'ports')
                        if ports is not None:
                            try:
                                loaded_ports = {}
                                for k, v in ports.items():
                                    try:
                                        loaded_ports[int(k)] = bool(v)
                                    except Exception:
                                        continue
                                PORTS.clear()
                                PORTS.update(loaded_ports)
                            except Exception as e:
                                logger.error(f"Error loading ports from config: {e}")
                    if 'expand' in data:
                        self.app.expand = bool(data['expand'])
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            print(f"Error loading config: {e}")

    def save(self) -> None:
        """Сохранить конфигурацию в файл ``self.config_file``."""
        try:
            data = {
                'ssh': asdict(self.app.ssh),
                'network': asdict(self.app.network),
                'expand': self.app.expand,
            }
            # Save ports under network -> ports (keys as strings for JSON)
            try:
                data['network']['ports'] = {str(k): v for k, v in PORTS.items()}
            except Exception as e:
                logger.error(f"Error serializing ports for save: {e}")
            logger.info(f"Saving config to {self.config_file}")
            if not os.path.exists(os.path.dirname(self.config_file)):
                os.makedirs(os.path.dirname(self.config_file))
                from src.utils.fs_utils import ensure_user_owned
                ensure_user_owned(os.path.dirname(self.config_file))
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            from src.utils.fs_utils import ensure_user_owned
            ensure_user_owned(self.config_file)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            print(f"Error saving config: {e}")

# Глобальный экземпляр конфигурации
config: Config = Config()