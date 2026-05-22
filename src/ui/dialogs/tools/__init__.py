"""Пакет инструментальных диалогов: SFTP, сканер IP, пинг-монитор, SSH-ключи, Polkit, удалённая запись."""

# Tool dialogs

from .ip_scanner import IPScannerDialog
from .known_hosts import KnownHostsDialog
from .pinger import PingMonitorDialog
from .sftp import SFTPDialog
from .ssh_manage import SSHManageDialog
from .polkit_editor import PolkitEditorDialog

__all__ = [
    'IPScannerDialog',
    'KnownHostsDialog',
    'PingMonitorDialog',
    'SFTPDialog',
    'SSHManageDialog',
    'PolkitEditorDialog',
]
