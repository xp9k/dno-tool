"""Модуль логирования DNO Tool. Настраивает ротируемый файловый обработчик с автоматическим изменением владельца файла логов при запуске от sudo/pkexec."""
from logging.handlers import RotatingFileHandler
import logging
import os
from os import path, makedirs
import sys


class UserOwnedRotatingFileHandler(RotatingFileHandler):
    """Ротируемый обработчик логов с автоматической сменой владельца файла."""

    def doRollover(self) -> None:
        """Выполнить ротацию лога и сменить владельца нового файла."""
        super().doRollover()
        try:
            from src.utils.fs_utils import ensure_user_owned
            ensure_user_owned(self.baseFilename)
        except Exception:
            pass

    def _open(self):
        """Открыть файл лога и сменить владельца."""
        stream = super()._open()
        try:
            from src.utils.fs_utils import ensure_user_owned
            ensure_user_owned(self.baseFilename)
        except Exception:
            pass
        return stream


def get_log_file_path() -> str:
    """Вернуть путь к файлу логов (~/.dnotool/dnotool.log)."""
    return path.join(path.expanduser('~'), '.dnotool',  'dnotool.log')
    

log_file = get_log_file_path()


if not path.exists(path.dirname(log_file)):
    makedirs(path.dirname(log_file))
    try:
        from src.utils.fs_utils import ensure_user_owned
        ensure_user_owned(path.dirname(log_file))
    except Exception:
        pass
    

CRITICAL = logging.CRITICAL
FATAL = logging.FATAL
ERROR = logging.ERROR
WARNING = logging.WARNING
WARN = logging.WARN
INFO = logging.INFO
DEBUG = logging.DEBUG
NOTSET = logging.NOTSET


def get_logger(name: str, level: int = INFO) -> logging.Logger:
    """Создать и настроить логгер с ротируемым файловым обработчиком."""
    logfile = path.join("logs", log_file)
    log_format = f"%(asctime)s - [%(levelname)s] - %(name)s - (%(filename)s).%(funcName)s(%(lineno)d) - %(message)s"

    handler = UserOwnedRotatingFileHandler(logfile,
                                maxBytes=1024*1024,
                                backupCount=3,
                                encoding="utf-8")

    handler.setFormatter(logging.Formatter(log_format))

    log = logging.getLogger(name)
    log.setLevel(level)
    log.addHandler(handler)

    return log

logger = get_logger("iktool", level=ERROR)