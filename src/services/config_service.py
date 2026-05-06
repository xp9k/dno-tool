"""
Config Service - Сервис для управления конфигурацией.

Предоставляет бизнес-логику для операций с конфигурацией:
- Чтение/запись настроек
- Валидация
- Публикация событий об изменениях
"""

from typing import Dict, Any, Tuple, Optional
from src.config import config, PORTS, DEFAULT_PORTS
from .base import BaseService
from src.architecture import EventType


class ConfigService(BaseService):
    """
    Сервис для управления конфигурацией приложения.
    
    Инкапсулирует всю бизнес-логику работы с настройками,
    предоставляя UI компонентам простой интерфейс.
    """

    def __init__(self, event_bus: Optional['EventBus'] = None):
        """
        Инициализация сервиса конфигурации.
        
        Args:
            event_bus: Экземпляр EventBus (опционально)
        """
        super().__init__(event_bus)
        self._config = config

    # ========== ЧТЕНИЕ НАСТРОЕК ==========

    def get_ssh_config(self) -> Dict[str, Any]:
        """
        Получить SSH настройки.
        
        Returns:
            Словарь с SSH настройками
        """
        return {
            'username': self._config.app.ssh.username,
            'password': self._config.app.ssh.password,
            'port': self._config.app.ssh.port,
            'strict_host_checking': self._config.app.ssh.strict_host_checking,
            'command_timeout': self._config.app.ssh.command_timeout,
            'connect_timeout': self._config.app.ssh.connect_timeout,
            'ssh_connect_timeout': self._config.app.ssh.ssh_connect_timeout,
            'max_as_completed_timeout': self._config.app.ssh.max_as_completed_timeout,
        }

    def get_network_config(self) -> Dict[str, Any]:
        """
        Получить сетевые настройки.
        
        Returns:
            Словарь с сетевыми настройками
        """
        return {
            'ping_timeout': self._config.app.network.ping_timeout,
            'ping_interval': self._config.app.network.ping_interval,
            'thread_count': self._config.app.network.thread_count,
            'ports': dict(PORTS),
        }

    def get_expand_setting(self) -> bool:
        """
        Получить настройку разворачивания деревьев.
        
        Returns:
            True если деревья разворачиваются
        """
        return self._config.app.expand

    def get_all_config(self) -> Dict[str, Any]:
        """
        Получить всю конфигурацию.
        
        Returns:
            Словарь со всеми настройками
        """
        return {
            'ssh': self.get_ssh_config(),
            'network': self.get_network_config(),
            'expand': self.get_expand_setting(),
        }

    # ========== ИЗМЕНЕНИЕ НАСТРОЕК ==========

    def update_ssh_config(
        self,
        username: str = None,
        password: str = None,
        port: int = None,
        strict_host_checking: bool = None,
        command_timeout: int = None,
        connect_timeout: int = None
    ) -> bool:
        """
        Обновить SSH настройки.
        
        Args:
            username: Новый пользователь
            password: Новый пароль
            port: Новый порт
            strict_host_checking: Строгая проверка ключей
            command_timeout: Таймаут команд
            connect_timeout: Таймаут подключения (для оффлайн хостов)
        
        Returns:
            True если успешно
        """
        try:
            if username is not None:
                self._config.app.ssh.username = username
            if password is not None:
                self._config.app.ssh.password = password
            if port is not None:
                self._config.app.ssh.port = port
            if strict_host_checking is not None:
                self._config.app.ssh.strict_host_checking = strict_host_checking
            if command_timeout is not None:
                self._config.app.ssh.command_timeout = command_timeout
            if connect_timeout is not None:
                self._config.app.ssh.connect_timeout = connect_timeout
            
            self._logger.info("ConfigService: SSH settings updated")
            return True
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error updating SSH settings: {e}")
            return False

    def update_network_config(
        self,
        ping_timeout: float = None,
        ping_interval: int = None,
        thread_count: int = None,
        ports: Dict[int, bool] = None
    ) -> bool:
        """
        Обновить сетевые настройки.
        
        Args:
            ping_timeout: Таймаут пинга
            ping_interval: Интервал пинга
            thread_count: Количество потоков
            ports: Словарь портов
        
        Returns:
            True если успешно
        """
        try:
            if ping_timeout is not None:
                self._config.app.network.ping_timeout = ping_timeout
            if ping_interval is not None:
                self._config.app.network.ping_interval = ping_interval
            if thread_count is not None:
                self._config.app.network.thread_count = thread_count
            if ports is not None:
                PORTS.clear()
                PORTS.update(ports)
            
            self._logger.info("ConfigService: Network settings updated")
            return True
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error updating network settings: {e}")
            return False

    def update_expand_setting(self, expand: bool) -> bool:
        """
        Обновить настройку разворачивания деревьев.
        
        Args:
            expand: Разворачивать ли деревья
        
        Returns:
            True если успешно
        """
        try:
            self._config.app.expand = expand
            self._logger.info(f"ConfigService: Expand setting updated to {expand}")
            return True
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error updating expand setting: {e}")
            return False

    # ========== СОХРАНЕНИЕ/ЗАГРУЗКА ==========

    def save_config(self) -> Tuple[bool, str]:
        """
        Сохранить конфигурацию в файл.
        
        Returns:
            (success, message)
        """
        try:
            self._config.save()
            self._logger.info("ConfigService: Configuration saved to file")
            
            # Публикуем событие
            self._event_bus.publish_typed(
                EventType.DATA_SAVED,
                source='ConfigService',
                data={'type': 'config'}
            )
            
            return True, "Конфигурация сохранена"
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error saving configuration: {e}")
            return False, str(e)

    def load_config(self) -> Tuple[bool, str]:
        """
        Загрузить конфигурацию из файла.
        
        Returns:
            (success, message)
        """
        try:
            self._config.load()
            self._logger.info("ConfigService: Configuration loaded from file")
            
            # Публикуем событие
            self._event_bus.publish_typed(
                EventType.DATA_LOADED,
                source='ConfigService',
                data={'type': 'config', 'file': self._config.config_file}
            )
            
            return True, "Конфигурация загружена"
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error loading configuration: {e}")
            return False, str(e)

    # ========== МЕДИА НАСТРОЙКИ ==========

    def get_media_config(self) -> Dict[str, Any]:
        """
        Получить медиа-настройки.

        Returns:
            Словарь с медиа-настройками
        """
        return {
            'ffmpeg_path': self._config.app.media.ffmpeg_path,
            'ffplay_path': self._config.app.media.ffplay_path,
            'vlc_path': self._config.app.media.vlc_path,
        }

    def update_media_config(self, **kwargs) -> bool:
        """
        Обновить медиа-настройки.

        Args:
            **kwargs: Ключ-значение для обновления

        Returns:
            True если успешно
        """
        try:
            for key, value in kwargs.items():
                if hasattr(self._config.app.media, key):
                    setattr(self._config.app.media, key, value)
            self._logger.info("ConfigService: Media settings updated")
            return True
        except Exception as e:
            self._logger.error(f"ConfigService: Error updating media settings: {e}")
            return False

    def get_media_path(self, name: str) -> str:
        """
        Получить путь к медиа-утилите.

        Args:
            name: Имя утилиты (ffmpeg, ffplay, vlc)

        Returns:
            Путь к исполняемому файлу
        """
        return getattr(self._config.app.media, f"{name}_path", name)

    def update_media_path(self, name: str, path: str) -> bool:
        """
        Обновить путь к медиа-утилите.

        Args:
            name: Имя утилиты (ffmpeg, ffplay, vlc)
            path: Новый путь

        Returns:
            True если успешно
        """
        try:
            attr_name = f"{name}_path"
            if hasattr(self._config.app.media, attr_name):
                setattr(self._config.app.media, attr_name, path)
                self._logger.info(f"ConfigService: {name} path updated to {path}")
                return True
            return False
        except Exception as e:
            self._logger.error(f"ConfigService: Error updating {name} path: {e}")
            return False

    # ========== ВАЛИДАЦИЯ ==========

    def validate_config(self, config_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Валидировать конфигурацию.
        
        Args:
            config_data: Данные для валидации
        
        Returns:
            (is_valid, error_message)
        """
        # Проверка SSH настроек
        if 'ssh' in config_data:
            ssh = config_data['ssh']
            
            if 'port' in ssh:
                port = ssh['port']
                if not isinstance(port, int) or not (1 <= port <= 65535):
                    return False, f"Неверный SSH порт: {port}"
            
            if 'command_timeout' in ssh:
                timeout = ssh['command_timeout']
                if not isinstance(timeout, int) or timeout < 1:
                    return False, f"Неверный таймаут команд: {timeout}"
            
            if 'connect_timeout' in ssh:
                timeout = ssh['connect_timeout']
                if not isinstance(timeout, int) or timeout < 1:
                    return False, f"Неверный таймаут подключения: {timeout}"
        
        # Проверка сетевых настроек
        if 'network' in config_data:
            network = config_data['network']
            
            if 'ping_timeout' in network:
                timeout = network['ping_timeout']
                if not isinstance(timeout, (int, float)) or timeout < 0:
                    return False, f"Неверный таймаут пинга: {timeout}"
            
            if 'ping_interval' in network:
                interval = network['ping_interval']
                if not isinstance(interval, int) or interval < 1:
                    return False, f"Неверный интервал пинга: {interval}"
            
            if 'thread_count' in network:
                count = network['thread_count']
                if not isinstance(count, int) or count < 1 or count > 32:
                    return False, f"Неверное количество потоков: {count}"
        
        return True, ""

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def reset_to_defaults(self) -> bool:
        """
        Сбросить конфигурацию к значениям по умолчанию.
        
        Returns:
            True если успешно
        """
        try:
            # Сброс SSH настроек
            self._config.app.ssh.username = 'root'
            self._config.app.ssh.password = ''
            self._config.app.ssh.port = 22
            self._config.app.ssh.strict_host_checking = False
            self._config.app.ssh.command_timeout = 30
            self._config.app.ssh.connect_timeout = 5
            
            # Сброс сетевых настроек
            self._config.app.network.ping_timeout = 2.0
            self._config.app.network.ping_interval = 30
            self._config.app.network.thread_count = 8
            
            # Сброс портов
            PORTS.clear()
            PORTS.update(DEFAULT_PORTS)
            
            # Сброс expand
            self._config.app.expand = False
            
            self._logger.info("ConfigService: Configuration reset to defaults")
            return True
            
        except Exception as e:
            self._logger.error(f"ConfigService: Error resetting configuration: {e}")
            return False
