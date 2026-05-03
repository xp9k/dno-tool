from logging.handlers import RotatingFileHandler
import logging
from os import path, makedirs, name as osname, makedirs
# from config import LOGS_PATH
import sys

def get_log_file_path():
    """
    Return log file path depending on system
    """
    # if osname == 'nt':
    return path.join(path.expanduser('~'), '.dnotool',  'dnotool.log')
    # else:
    #     return path.join('/var/log/', 'dnotool.log')
    

log_file = get_log_file_path()


if not path.exists(path.dirname(log_file)):
    makedirs(path.dirname(log_file))
    

CRITICAL = logging.CRITICAL
FATAL = logging.FATAL
ERROR = logging.ERROR
WARNING = logging.WARNING
WARN = logging.WARN
INFO = logging.INFO
DEBUG = logging.DEBUG
NOTSET = logging.NOTSET


def get_logger(name: str, level: int = INFO) -> logging.Logger:
    logfile = path.join("logs", log_file)
    log_format = f"%(asctime)s - [%(levelname)s] - %(name)s - (%(filename)s).%(funcName)s(%(lineno)d) - %(message)s"

    handler = RotatingFileHandler(logfile,
                                maxBytes=1024*1024,
                                backupCount=3,
                                encoding="utf-8")

    handler.setFormatter(logging.Formatter(log_format))

    log = logging.getLogger(name)
    log.setLevel(level)
    log.addHandler(handler)

    return log

logger = get_logger("iktool", level=ERROR)