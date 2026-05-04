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

def get_asset_path(filename):
    """Get path to asset file in assets directory."""
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
    """Thread-safe dict for PORTS that locks on mutations."""
    def __setitem__(self, key, value):
        with _ports_lock:
            super().__setitem__(key, value)
    def __delitem__(self, key):
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
DEFAULT_SSH_PRIVATE_KEY_PATH = expanduser('~') + os.sep + '.ssh' + os.sep + 'id_rsa' #'~/.ssh/id_rsa'
DEFAULT_SSH_PUBLIC_KEY_PATH = expanduser('~') + os.sep + '.ssh' + os.sep + 'id_rsa.pub' #'~/.ssh/id_rsa.pub' 
LOGS_PATH = expanduser('~') + os.sep + config_folder_name + os.sep  #'~/.dnotool'

SSH_RECV_BUFFER_SIZE = 4096

try:
    configs_dir = os.path.dirname(DEFAULT_SETTINGS_FILE)
    if not os.path.exists(configs_dir):
        logger.info(f"Creating {configs_dir} directory")
        os.makedirs(configs_dir)
except Exception as e:
    logger.error(f"Error creating {configs_dir} directory: {e}")
    print(e)


@dataclass
class SSHConfig:
    username: str = "root"
    password: str = ""
    port: int = 22                      # SSH port
    strict_host_checking: bool = False  # Enable strict host key checking
    command_timeout: int = 30           # Timeout in seconds
    ssh_connect_timeout: int = 10       # SSH connection timeout in seconds
    max_as_completed_timeout: int = 600 # Maximum async completed timeout in seconds

@dataclass
class NetworkConfig:
    ping_timeout: float = 2.0
    ping_interval: int = 30     # Seconds between pings
    thread_count: int = 8       # Number of threads


@dataclass
class MediaConfig:
    ffmpeg_path: str = "ffmpeg"
    ffplay_path: str = "ffplay"
    vlc_path: str = "vlc"
    recording_path: str = ""


@dataclass
class AppConfig:
    ssh: SSHConfig = field(default_factory=SSHConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    expand: bool = False 

class Config:
    def __init__(self):
        self.app = AppConfig()
        self.config_file = DEFAULT_SETTINGS_FILE
        self.load()

    def load(self):
        """Load configuration from file"""
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
                    if 'media' in data:
                        try:
                            self.app.media = MediaConfig(**data['media'])
                        except Exception as e:
                            logger.error(f"Error loading media config: {e}")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            print(f"Error loading config: {e}")

    def save(self):
        """Save configuration to file"""
        try:
            data = {
                'ssh': asdict(self.app.ssh),
                'network': asdict(self.app.network),
                'expand': self.app.expand,
                'media': asdict(self.app.media),
            }
            # Save ports under network -> ports (keys as strings for JSON)
            try:
                data['network']['ports'] = {str(k): v for k, v in PORTS.items()}
            except Exception as e:
                logger.error(f"Error serializing ports for save: {e}")
            logger.info(f"Saving config to {self.config_file}")
            if not os.path.exists(os.path.dirname(self.config_file)):
                os.makedirs(os.path.dirname(self.config_file))
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            print(f"Error saving config: {e}")

# Global config instance
config = Config()