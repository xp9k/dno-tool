"""
Command и CommandType - Модели данных для команд.

Этот модуль содержит только классы данных Command и CommandType.
Для выполнения команд используйте CommandWorker из command_worker.py.
"""

from typing import Dict
from enum import Enum
from src.domain.utils import command_param_replacer


class CommandType(str, Enum):
    """Типы поддерживаемых команд."""
    SSH = "ssh"
    SFTP = "sftp"
    LOCAL = "local"

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value


class Command:
    """
    Модель команды для выполнения на устройстве.

    Поддерживает два формата:
    - Строковый (legacy): "sftp source::destination" или "ssh command"
    - Словарь (новый): {"type": "ssh", "text": "command"}
    """

    def __init__(self, command_data: Dict | str) -> None:
        """
        Инициализация команды.

        Args:
            command_data: Строка или словарь с данными команды
        """
        if isinstance(command_data, str):
            # Legacy support для старого строкового формата
            # Проверяем на 'local', 'sftp', остальное - SSH
            if command_data.startswith("sftp"):
                self.commandType = CommandType.SFTP
            elif command_data.startswith("local"):
                self.commandType = CommandType.LOCAL
            else:
                self.commandType = CommandType.SSH
            self.text = command_data
        else:
            # Новый формат с типом и текстом
            self.commandType = CommandType(command_data.get("type", "ssh"))
            self.text = command_data.get("text", "")

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"{self.commandType}:{self.text}"

    def toString(self) -> str:
        """Возвращает текст команды (для совместимости)."""
        return self.text

    def to_dict(self) -> Dict:
        """Конвертация в словарь."""
        return {
            "type": self.commandType,
            "text": self.text
        }

    def replace_params(self, params: Dict = None):
        """
        Замена параметров в команде.

        Args:
            params: Словарь параметров для замены
        """
        self.text = command_param_replacer.replace_custom(self.text, params or {})
