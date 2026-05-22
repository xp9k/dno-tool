"""Пакет фоновых исполнителей (workers)."""

from .base import BaseWorker
from .ffmpeg_stream_manager import FFmpegStreamManager
from .gstreamer_stream_manager import GStreamerStreamManager

# Импорт исполнителей команд
from .command.orchestrator import CommandWorker
from .command.executor_base import BaseCommandExecutor
from .command.ssh import SSHWorker
from .command.sftp import SFTPWorker
from .command.local import LocalWorker

# Импорт сетевых исполнителей
from .network.host_timer import HostPingTimerManager, get_host_ping_timer_manager, reset_host_ping_timer_manager
from .network.worker_pool import WorkerPoolManager, get_worker_pool, get_ping_worker_pool, get_command_worker_pool

# Импорт моделей Command из домена
from src.domain.models import Command, CommandType

__all__ = [
    # Base
    'BaseWorker',
    'FFmpegStreamManager',
    'GStreamerStreamManager',

    # Command - Models
    'Command',
    'CommandType',

    # Command - Workers
    'CommandWorker',
    'BaseCommandExecutor',
    'SSHWorker',
    'SFTPWorker',
    'LocalWorker',

    # Per-host Timer
    'HostPingTimerManager',
    'get_host_ping_timer_manager',
    'reset_host_ping_timer_manager',

    # Pool Manager
    'WorkerPoolManager',
    'get_worker_pool',
    'get_ping_worker_pool',
    'get_command_worker_pool',
]
