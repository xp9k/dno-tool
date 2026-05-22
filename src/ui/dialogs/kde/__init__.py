"""Пакет диалогов управления настройками KDE Plasma на удалённых хостах."""

"""
KDE Config Dialog Module - Модуль управления настройками KDE.
"""

from .kde_config_dialog import KDEConfigDialog, RemoteKDEConfigManager, KDEConfigWorker

__all__ = ['KDEConfigDialog', 'RemoteKDEConfigManager', 'KDEConfigWorker']
