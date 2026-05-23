"""Базовый класс исполнителя команд и вспомогательные функции.

Предоставляет CredentialsResult dataclass, get_credentials() и BaseCommandExecutor
с общим интерфейсом для SSH/SFTP/local команд."""

import threading
from abc import ABC, abstractmethod
from typing import Tuple, Optional, Dict, Any, NamedTuple
from PySide6.QtCore import QObject, Signal
from src.config import config, SSH_RECV_BUFFER_SIZE
from src.domain.models.device import DeviceModel
from src.logger import logger
import os

import paramiko

_SSH_DIR = os.path.join(os.path.expanduser("~"), ".ssh")

_KEY_TYPE_NAMES = {
    "id_ed25519": "ssh-ed25519",
    "id_rsa": "ssh-rsa",
    "id_ecdsa": "ecdsa-sha2-nistp256",
}

_KEY_CLASSES = [
    paramiko.Ed25519Key,
    paramiko.RSAKey,
    paramiko.ECDSAKey,
]


def _detect_key_type(privkey_path: str) -> Optional[str]:
    """Определяет тип ключа по имени файла (без чтения файла)."""
    basename = os.path.basename(privkey_path)
    for prefix, type_name in _KEY_TYPE_NAMES.items():
        if basename == prefix or basename.startswith(prefix + "."):
            return type_name
    return None


def load_private_key(key_path: str, password: str = "") -> Optional[paramiko.PKey]:
    """Загружает приватный SSH-ключ, автоматически определяя тип (Ed25519, RSA, ECDSA, DSA).

    Args:
        key_path: Путь к файлу приватного ключа.
        password: Пароль от ключа (пустая строка — без пароля).

    Returns:
        Объект PKey или None, если ключ не удалось загрузить.

    Raises:
        paramiko.PasswordRequiredException: если ключ защищён паролем.
    """
    for key_class in _KEY_CLASSES:
        try:
            return key_class.from_private_key_file(key_path, password)
        except paramiko.PasswordRequiredException:
            raise
        except Exception:
            continue
    return None


def get_default_key_path() -> str:
    """Возвращает путь к приватному ключу по умолчанию.

    Приоритет: id_ed25519 > id_rsa > id_ecdsa.
    Если ни один файл не найден, возвращает id_ed25519.
    """
    for name in ("id_ed25519", "id_rsa", "id_ecdsa"):
        path = os.path.join(_SSH_DIR, name)
        if os.path.exists(path):
            return path
    return os.path.join(_SSH_DIR, "id_ed25519")


PRIVATE_KEY_PATH = get_default_key_path()


def _is_set(value: Optional[str]) -> bool:
    """Проверяет что значение установлено (не None и не пустая строка)"""
    return value is not None and value.strip() != ""


class CredentialsResult(NamedTuple):
    """Результат получения учётных данных"""
    username: str
    password: Optional[str]
    private_key: Optional[Any]  # paramiko.PKey


def get_credentials(
    device: DeviceModel,
    use_key: bool = True,
    key_path: Optional[str] = None
) -> CredentialsResult:
    """
    Получить учётные данные для SSH подключения с правильным приоритетом.

    Приоритет параметров:
    1. Параметры хоста (device.login, device.password)
    2. Глобальные настройки приложения (config.app.ssh.username, config.app.ssh.password)
    3. SSH ключ (если use_key=True)

    Args:
        device: Устройство для получения данных
        use_key: Использовать ли SSH ключ (False для диалогов где ключ не должен учитываться)
        key_path: Путь к приватному ключу (по умолчанию ~/.ssh/id_rsa)

    Returns:
        CredentialsResult с username, password и private_key
    """
    if key_path is None:
        key_path = PRIVATE_KEY_PATH

    # Приоритет 1 -> 2: имя пользователя (хост -> глобальные -> 'root')
    username = (
        (device.login or '').strip() or
        (config.app.ssh.username or '').strip() or
        'root'
    )

    # Приоритет 1 -> 2: пароль (хост -> глобальные)
    password = (
        (device.password or '').strip() or
        (config.app.ssh.password or '').strip() or
        None
    )

    # Приоритет 3: SSH ключ (только если use_key=True)
    private_key = None
    if use_key and os.path.exists(key_path):
        try:
            private_key = load_private_key(key_path)
            if private_key:
                logger.debug(f"Загружен SSH ключ ({private_key.get_name()}): {key_path}")
            else:
                logger.warning(f"Не удалось определить тип приватного ключа {key_path}")
        except paramiko.PasswordRequiredException:
            logger.warning(f"Приватный ключ {key_path} защищён паролем, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка загрузки приватного ключа {key_path}: {e}")

    return CredentialsResult(
        username=username,
        password=password,
        private_key=private_key
    )


class BaseCommandExecutor(ABC):
    """
    Базовый класс для всех исполнителей команд.

    Предоставляет:
    - Общую логику замены параметров
    - Методы для создания SSH клиента
    - Базовую обработку ошибок
    """

    def __init__(self, progress_callback: Optional[QObject] = None):
        """
        Инициализация исполнителя.

        Args:
            progress_callback: Объект для отправки прогресса (сигнал progress_update)
        """
        self._progress_callback = progress_callback
        self._abort_event = threading.Event()

    @property
    def aborting(self) -> bool:
        """Проверить, запрошено ли прерывание."""
        return self._abort_event.is_set()

    @aborting.setter
    def aborting(self, value: bool) -> None:
        """Установить или снять флаг прерывания."""
        if value:
            self._abort_event.set()
        else:
            self._abort_event.clear()

    def get_client(
        self,
        device: DeviceModel,
        timeout: int = config.app.ssh.command_timeout
    ) -> paramiko.SSHClient:
        """
        Создание и подключение SSH клиента.

        Args:
            device: Устройство для подключения
            timeout: Таймаут выполнения команды (используется как таймаут сессии)

        Returns:
            Подключенный SSH клиент

        Raises:
            Exception: Ошибка подключения
        """
        client = paramiko.SSHClient()

        # Настройка политики проверки ключей хоста
        if config.app.ssh.strict_host_checking:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            logger.debug(f"Используется строгая проверка ключей хоста для {device.host}")
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            logger.debug(f"Используется автодобавление ключей хоста для {device.host}")

        # Получение учётных данных с правильным приоритетом
        creds = get_credentials(device)

        hostname = device.host
        port = device.port or config.app.ssh.port or 22

        # Отдельный таймаут для установки TCP-соединения (быстрый отказ при оффлайн)
        connect_timeout = config.app.ssh.connect_timeout

        logger.info(
            f"Подключение к {device.host} "
            f"(Порт: {port}, Пользователь: {creds.username}, "
            f"Connect timeout: {connect_timeout}s)"
        )

        try:
            client.connect(
                hostname=hostname,
                port=port,
                username=creds.username,
                password=creds.password,
                pkey=creds.private_key,
                timeout=connect_timeout,
                banner_timeout=connect_timeout,
                auth_timeout=connect_timeout
            )
            logger.info(f"Успешно подключено к {device.host}")
        except Exception as e:
            logger.error(f"Не удалось подключиться к {device.host}: {str(e)}")
            raise

        return client

    def get_session(self, client: paramiko.SSHClient) -> paramiko.Channel:
        """
        Создание SSH сессии.

        Args:
            client: SSH клиент

        Returns:
            SSH сессия (Channel)
        """
        transport = client.get_transport()
        session = transport.open_session()
        session.get_pty()  # Запрос псевдо-терминала для интерактивного вывода
        return session

    def emit_progress(self, device: DeviceModel, message: str) -> None:
        """
        Отправка сообщения о прогрессе.

        Args:
            device: Устройство
            message: Сообщение
        """
        if self._progress_callback and hasattr(self._progress_callback, 'progress_update'):
            self._progress_callback.progress_update.emit(device, message)

    @abstractmethod
    def execute(
        self,
        device: DeviceModel,
        command_text: str,
        timeout: int = config.app.ssh.command_timeout
    ) -> Tuple[str, bool]:
        """
        Выполнение команды.

        Args:
            device: Устройство для выполнения
            command_text: Текст команды
            timeout: Таймаут выполнения

        Returns:
            (вывод, успех)
        """
        pass

    def abort(self) -> None:
        """Запросить прерывание выполнения."""
        logger.info(f"{self.__class__.__name__}: Запрошено прерывание")
        self._abort_event.set()
