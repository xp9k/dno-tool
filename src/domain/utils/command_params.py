"""
Command Parameter Replacer - Единая точка замены параметров в командах.

Использование:
    from src.domain.utils.command_params import CommandParamReplacer

    # Для подстановки устройства
    replacer = CommandParamReplacer()
    text = replacer.replace_for_device(command_text, device)

    # Для подстановки custom-параметров
    text = replacer.replace_custom(command_text, {"%CUSTOM%": "value"})

    # Комбинированная замена
    text = replacer.replace(command_text, device, custom_params)
"""

from typing import Dict, Optional
from datetime import datetime
from src.config import config


class CommandParamReplacer:
    """
    Утилита для замены плейсхолдеров в командах.

    Поддерживаемые плейсхолдеры устройства:
    - %name% - имя устройства (псевдоним)
    - %login%, %user%, %username% - логин устройства
    - %hostname%, %host%, %ip% - хост устройства
    - %port% - порт устройства или порт по умолчанию
    - %password%, %pass% - пароль устройства
    - %date% - текущая дата (YYYY.MM.DD)
    - %time% - текущее время (HHMMSS)
    - %timestamp% - дата и время (YYYY.MM.DD_HHMMSS)
    - %mac% - MAC адрес устройства

    Custom плейсхолдеры заменяются из переданного словаря.
    """

    @staticmethod
    def _get_credentials(device) -> tuple[str, Optional[str]]:
        """
        Получить учётные данные с правильным приоритетом.

        Приоритет:
        1. Параметры хоста (device.login, device.password)
        2. Глобальные настройки (config.app.ssh.username, config.app.ssh.password)
        3. Значение по умолчанию ('root')

        Returns:
            (username, password)
        """
        username = (
            (device.login or '').strip() or
            (config.app.ssh.username or '').strip() or
            'root'
        )

        password = (
            (device.password or '').strip() or
            (config.app.ssh.password or '').strip() or
            None
        )

        return username, password

    def replace_for_device(self, text: str, device) -> str:
        """
        Замена плейсхолдеров устройства в тексте.

        Args:
            text: Текст с плейсхолдерами
            device: Устройство для подстановки значений

        Returns:
            Текст с заменёнными плейсхолдерами устройства
        """
        if device is None:
            return text

        username, password = self._get_credentials(device)
        now = datetime.now()

        replacements = {
            '%name%': device.name,
            '%login%': username,
            '%user%': username,
            '%username%': username,
            '%hostname%': device.host,
            '%host%': device.host,
            '%ip%': device.host,
            '%port%': str(device.port or config.app.ssh.port or 22),
            '%password%': password or '',
            '%pass%': password or '',
            '%date%': now.strftime("%Y.%m.%d"),
            '%time%': now.strftime("%H%M%S"),
            '%timestamp%': now.strftime("%Y.%m.%d_%H%M%S"),
            '%mac%': device.mac_address or "unknown",
        }

        result = text
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)

        return result

    def replace_custom(self, text: str, params: Dict) -> str:
        """
        Замена custom-параметров в тексте.

        Args:
            text: Текст с плейсхолдерами
            params: Словарь параметров (ключ без %, значение - подстановка)

        Returns:
            Текст с заменёнными custom-параметрами
        """
        if not params:
            return text

        result = text
        for param_name, param_value in params.items():
            result = result.replace(f"%{param_name}%", str(param_value))

        return result

    def replace(self, text: str, device=None, custom_params: Dict = None) -> str:
        """
        Комбинированная замена: custom-параметры + устройство.

        Порядок замены:
        1. Custom-параметры (чтобы устройство могло переопределить)
        2. Плейсхолдеры устройства

        Args:
            text: Текст с плейсхолдерами
            device: Устройство для подстановки (опционально)
            custom_params: Custom-параметры (опционально)

        Returns:
            Текст с заменёнными всеми параметрами
        """
        result = text

        # Сначала custom-параметры
        if custom_params:
            result = self.replace_custom(result, custom_params)

        # Затем устройство
        if device:
            result = self.replace_for_device(result, device)

        return result


# Глобальный экземпляр для удобства
command_param_replacer = CommandParamReplacer()
