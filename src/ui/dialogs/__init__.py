"""Пакет диалоговых окон приложения."""

# UI Dialogs package

from .common.base import BaseDialog
from .common.about import AboutDialog
from .common.common import DeviceEditDialog, ConfigDialog, CommandInfoDialog, ExportCommandDlg, ImportCommandDlg, BashViewerDialog  # noqa: F401 — re-export below
from .device.device_info import DeviceInfoDialog
from .common.params import ParamsInputDialog

from .command.edit import CommandEditorDialog
from .command.result import CommandResultDialog
from .command.sftp_edit import SFTPCommandEditorDialog

from .editors.bash_edit import BashEditorDialog

from .tools.ip_scanner import IPScannerDialog
from .tools.known_hosts import KnownHostsDialog
from .tools.pinger import PingMonitorDialog
from .tools.sftp import SFTPDialog
from .tools.ssh_manage import SSHManageDialog
from .tools.remote_recording import RemoteRecordingDialog
from .tools.polkit_editor import PolkitEditorDialog

__all__ = [
    # Base
    'BaseDialog',
    
    # Common
    'AboutDialog',
    'DeviceEditDialog',
    'ConfigDialog',
    'DeviceInfoDialog',
    'CommandInfoDialog',
    'ExportCommandDlg',
    'ImportCommandDlg',
    'BashViewerDialog',
    'ParamsInputDialog',
    
    # Command
    'CommandEditorDialog',
    'CommandResultDialog',
    'SFTPCommandEditorDialog',
    
    # Editors
    'BashEditorDialog',
    
    # Tools
    'IPScannerDialog',
    'KnownHostsDialog',
    'PingMonitorDialog',
    'RemoteRecordingDialog',
    'SFTPDialog',
    'SSHManageDialog',
    'PolkitEditorDialog',
]
