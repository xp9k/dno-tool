"""
Потокобезопасное хранилище данных с интеграцией EventBus.

Предоставляет:
- Потокобезопасный доступ через RLock
- Валидацию данных
- События об изменениях для интеграции с EventBus
- Кэширование последних операций
"""

import copy
import json
import re
import time
from threading import RLock
from typing import Dict, List, Any, Optional, Tuple
from collections import deque
from PySide6.QtCore import QObject, Signal

from src.config import DEFAULT_HOSTS_FILE, DEFAULT_COMMANDS_FILE
from src.logger import logger
from src.utils.fs_utils import ensure_user_owned

# ARCHITECTURE: Импорт EventBus для публикации событий
from src.architecture import EventBus, EventType


class DataStore(QObject):
    """
    Потокобезопасное хранилище данных хостов и команд.

    Обеспечивает CRUD-операции над хостами и командами с поддержкой:
    - Валидации данных перед записью;
    - Истории операций для undo;
    - Публикации событий через EventBus и Qt-сигналы.

    Атрибуты:
        hosts_data_changed: Qt-сигнал, излучаемый при изменении данных хостов.
        commands_data_changed: Qt-сигнал, излучаемый при изменении данных команд.
    """
    
    # Сигналы для Qt интеграции
    hosts_data_changed = Signal(dict)  # {action, data}
    commands_data_changed = Signal(dict)  # {action, data}
    
    # Статистика для отладки
    stats_signal = Signal(dict)
    
    def __init__(self) -> None:
        super().__init__()
        self._hosts_data: List[Dict] = []  # Плоский список устройств
        self._hosts_data_raw: List[Dict] = []  # Оригинальная структура с группами
        self._commands_data: List[Dict] = []
        self._lock = RLock()  # Потокобезопасность
        
        # Кэш последних операций для undo/redo
        self._operation_history: deque = deque(maxlen=50)
        
        # Статистика операций
        self._stats = {
            'reads': 0,
            'writes': 0,
            'validations': 0,
            'errors': 0
        }
        
        # EventBus для интеграции с архитектурой (устанавливается через set_event_bus)
        self._event_bus: Optional[EventBus] = None
        
        # Загружаем начальные данные
        self._load_initial_data()
    
    def _load_initial_data(self):
        """Загрузка начальных данных из файлов"""
        with self._lock:
            self._hosts_data = self._load_hosts_from_file_internal()
            # Сохраняем и оригинальную структуру
            self._hosts_data_raw = self._load_hosts_raw_from_file_internal()
            self._commands_data = self._load_commands_from_file_internal()
            logger.info(f"DataStore loaded: {len(self._hosts_data)} hosts, {len(self._commands_data)} commands")
    
    def _load_hosts_raw_from_file_internal(self, filename: str = DEFAULT_HOSTS_FILE) -> List[Dict]:
        """Загрузка оригинальной структуры хостов с группами"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except:
            return []
    
    # ========== HOSTS OPERATIONS ==========
    
    def get_hosts_data(self) -> List[Dict]:
        """
        Получить копию данных хостов (потокобезопасно).
        
        Returns:
            Плоский список всех хостов (без групп)
        """
        with self._lock:
            self._stats['reads'] += 1
            return [dict(h) for h in self._hosts_data]
    
    def get_hosts_data_raw(self) -> List[Dict]:
        """
        Получить оригинальные данные хостов с группами.

        Returns:
            Оригинальные данные с группами
        """
        with self._lock:
            self._stats['reads'] += 1
            # Возвращаем сохранённую структуру или читаем из файла
            if self._hosts_data_raw:
                return [dict(h) if isinstance(h, dict) else h for h in self._hosts_data_raw]
            try:
                with open(DEFAULT_HOSTS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
    
    def get_hosts_count(self) -> int:
        """Получить количество хостов (быстрая операция)"""
        with self._lock:
            return len(self._hosts_data)
    
    def set_hosts_data(self, new_data: List[Dict]) -> bool:
        """
        Установить новые данные хостов.

        Args:
            new_data: Новые данные (список хостов или структура с группами)

        Returns:
            True если успешно
        """
        with self._lock:
            try:
                # Проверяем это структура с группами или плоский список
                # Структура с группами: [{"Group": [...]}, {"Group2": [...]}]
                # Плоский список: [{"name": "...", "host": "..."}]
                has_groups = False
                if new_data:
                    for item in new_data:
                        if isinstance(item, dict) and len(item) == 1:
                            # Это группа (один ключ со списком)
                            has_groups = True
                            break
                        elif isinstance(item, dict) and 'host' in item:
                            # Это устройство (есть ключ 'host')
                            pass
                
                if has_groups:
                    # Это структура с группами
                    self._hosts_data_raw = copy.deepcopy(new_data)
                    # Извлекаем плоский список
                    self._hosts_data = []
                    self._extract_hosts_recursive(new_data, self._hosts_data)
                else:
                    # Это плоский список
                    self._hosts_data = copy.deepcopy(new_data)
                    self._hosts_data_raw = []  # Очищаем raw если данные плоские

                logger.debug(f"DataStore: Hosts data set ({len(self._hosts_data)} items)")
                return True

            except Exception as e:
                logger.error(f"DataStore: Error setting hosts data: {e}")
                self._stats['errors'] += 1
                return False
    
    def update_host(self, host_data: Dict) -> Tuple[bool, str]:
        """
        Обновить или добавить хост.

        Args:
            host_data: Данные хоста (обязательное поле 'host').

        Returns:
            Кортеж (успех, сообщение).
        """
        with self._lock:
            self._stats['validations'] += 1
            
            is_valid, error_msg = self._validate_host_internal(host_data)
            if not is_valid:
                self._stats['errors'] += 1
                return False, error_msg
            
            existing_idx = self._find_host_index_internal(
                host_data.get('host'),
                host_data.get('port')
            )
            
            operation = 'update' if existing_idx is not None else 'add'
            
            if existing_idx is not None:
                old_data = copy.deepcopy(self._hosts_data[existing_idx])
                self._hosts_data[existing_idx].update(host_data)
                new_data = copy.deepcopy(self._hosts_data[existing_idx])
                logger.debug(f"DataStore: Updated host {host_data.get('host')}")
            else:
                self._hosts_data.append(copy.deepcopy(host_data))
                old_data = {}
                new_data = copy.deepcopy(host_data)
                logger.debug(f"DataStore: Added host {host_data.get('host')}")
            
            self._stats['writes'] += 1
            
            self._add_operation('hosts', operation, old_data, new_data, index=existing_idx)
        
        self._notify_change('hosts', operation, new_data)
        
        return True, f"Host {operation}ed successfully"
    
    def delete_host(self, host: str, port: Optional[int] = None) -> bool:
        """
        Удалить хост по адресу и опционально порту.

        Args:
            host: hostname или IP.
            port: Порт (None — удалить все записи с данным host).

        Returns:
            True если х найден и удалён.
        """
        with self._lock:
            indices_to_remove = []
            
            for i, h in enumerate(self._hosts_data):
                if h.get('host') == host:
                    if port is None or h.get('port') == port:
                        indices_to_remove.append(i)
            
            if not indices_to_remove:
                logger.debug(f"DataStore: Host {host} not found for deletion")
                return False
            
            removed = []
            for i in reversed(indices_to_remove):
                removed.append(copy.deepcopy(self._hosts_data[i]))
                del self._hosts_data[i]
            
            self._stats['writes'] += 1
            
            self._add_operation('hosts', 'delete', removed, {})
        
        self._notify_change('hosts', 'delete', {'host': host, 'port': port, 'removed': removed})
        
        logger.debug(f"DataStore: Deleted {len(removed)} host(s) {host}")
        return True
    
    def find_host(self, host: str, port: Optional[int] = None) -> Optional[Dict]:
        """Найти хост по host и опционально port"""
        with self._lock:
            self._stats['reads'] += 1
            for h in self._hosts_data:
                if h.get('host') == host:
                    if port is None or h.get('port') == port:
                        return copy.deepcopy(h)
            return None
    
    def get_hosts_by_folder(self, folder_path: str) -> List[Dict]:
        """Получить все хосты в папке"""
        with self._lock:
            self._stats['reads'] += 1
            return [
                copy.deepcopy(h) for h in self._hosts_data
                if h.get('folder', '') == folder_path
            ]
    
    # ========== COMMANDS OPERATIONS ==========
    
    def get_commands_data(self) -> List[Dict]:
        """Получить копию данных команд (потокобезопасно)"""
        with self._lock:
            self._stats['reads'] += 1
            return [dict(c) for c in self._commands_data]
    
    def get_commands_count(self) -> int:
        """Получить количество команд"""
        with self._lock:
            return len(self._commands_data)
    
    def set_commands_data(self, new_data: List[Dict]) -> bool:
        """
        Установить новые данные команд.
        
        Args:
            new_data: Новые данные (список команд)
            
        Returns:
            True если успешно
        """
        with self._lock:
            try:
                if not isinstance(new_data, list):
                    logger.error("DataStore: commands data must be a list")
                    self._stats['errors'] += 1
                    return False
                
                # Валидация команд
                for cmd_data in new_data:
                    if not self._validate_command_internal(cmd_data):
                        logger.error(f"DataStore: Invalid command data: {cmd_data}")
                        self._stats['errors'] += 1
                        return False
                
                old_data = copy.deepcopy(self._commands_data)
                self._commands_data = copy.deepcopy(new_data)
                self._stats['writes'] += 1
                
                self._add_operation('commands', 'set', old_data, new_data)
                self._notify_change('commands', 'set', new_data)
                
                logger.debug(f"DataStore: Commands data set ({len(new_data)} items)")
                return True
                
            except Exception as e:
                logger.error(f"DataStore: Error setting commands data: {e}")
                self._stats['errors'] += 1
                return False
    
    def update_command(self, command_data: Dict) -> Tuple[bool, str]:
        """
        Обновить или добавить команду.
        
        Returns:
            (success, message)
        """
        with self._lock:
            # Валидация
            is_valid, error_msg = self._validate_command_internal(command_data)
            if not is_valid:
                self._stats['errors'] += 1
                return False, error_msg
            
            # Ищем по имени
            existing_idx = self._find_command_index_internal(command_data.get('name'))
            
            operation = 'update' if existing_idx is not None else 'add'
            
            if existing_idx is not None:
                old_data = copy.deepcopy(self._commands_data[existing_idx])
                self._commands_data[existing_idx].update(command_data)
                new_data = copy.deepcopy(self._commands_data[existing_idx])
            else:
                self._commands_data.append(copy.deepcopy(command_data))
                old_data = {}
                new_data = copy.deepcopy(command_data)
            
            self._stats['writes'] += 1
            self._add_operation('commands', operation, old_data, new_data, index=existing_idx)
            self._notify_change('commands', operation, new_data)
            
            return True, f"Command {operation}ed successfully"
    
    def delete_command(self, name: str) -> bool:
        """Удалить команду по имени"""
        with self._lock:
            for i, cmd in enumerate(self._commands_data):
                if cmd.get('name') == name:
                    removed = copy.deepcopy(cmd)
                    del self._commands_data[i]
                    self._stats['writes'] += 1
                    self._add_operation('commands', 'delete', removed, {})
                    self._notify_change('commands', 'delete', {'name': name})
                    logger.debug(f"DataStore: Deleted command {name}")
                    return True
            return False
    
    def find_command(self, name: str) -> Optional[Dict]:
        """Найти команду по имени"""
        with self._lock:
            self._stats['reads'] += 1
            for cmd in self._commands_data:
                if cmd.get('name') == name:
                    return copy.deepcopy(cmd)
            return None
    
    # ========== FILE OPERATIONS ==========
    
    def save_hosts_to_file(self, filename: str = DEFAULT_HOSTS_FILE) -> bool:
        """Сохранить хосты в файл с сохранением иерархии"""
        with self._lock:
            try:
                # Сохраняем оригинальную структуру с группами
                data_to_save = self._hosts_data_raw if self._hosts_data_raw else self._hosts_data
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, ensure_ascii=False, indent=4, sort_keys=False)
                ensure_user_owned(filename)
                logger.debug(f"DataStore: Hosts saved to {filename}")
                self._notify_change('hosts', 'saved', {'file': filename})
                return True
            except Exception as e:
                logger.error(f"DataStore: Error saving hosts: {e}")
                self._stats['errors'] += 1
                return False
    
    def load_hosts_from_file(self, filename: str = DEFAULT_HOSTS_FILE) -> bool:
        """Загрузить хосты из файла"""
        with self._lock:
            try:
                data = self._load_hosts_from_file_internal(filename)
                self._hosts_data = data
                logger.debug(f"DataStore: Hosts loaded from {filename}")
                self._notify_change('hosts', 'loaded', {'file': filename})
                return True
            except Exception as e:
                logger.error(f"DataStore: Error loading hosts: {e}")
                self._stats['errors'] += 1
                return False
    
    def save_commands_to_file(self, filename: str = DEFAULT_COMMANDS_FILE) -> bool:
        """Сохранить команды в файл"""
        with self._lock:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(self._commands_data, f, ensure_ascii=False, indent=4, sort_keys=True)
                ensure_user_owned(filename)
                logger.debug(f"DataStore: Commands saved to {filename}")
                self._notify_change('commands', 'saved', {'file': filename})
                return True
            except Exception as e:
                logger.error(f"DataStore: Error saving commands: {e}")
                self._stats['errors'] += 1
                return False
    
    def load_commands_from_file(self, filename: str = DEFAULT_COMMANDS_FILE) -> bool:
        """Загрузить команды из файла"""
        with self._lock:
            try:
                data = self._load_commands_from_file_internal(filename)
                self._commands_data = data
                logger.debug(f"DataStore: Commands loaded from {filename}")
                self._notify_change('commands', 'loaded', {'file': filename})
                return True
            except Exception as e:
                logger.error(f"DataStore: Error loading commands: {e}")
                self._stats['errors'] += 1
                return False
    
    # ========== HISTORY & UNDO ==========
    
    def undo(self) -> Optional[Dict]:
        """
        Отменить последнюю операцию (если есть в истории).

        Returns:
            Словарь с описанием отменённой операции или None.
        """
        with self._lock:
            if not self._operation_history:
                return None
            
            operation = self._operation_history.pop()
            
            if operation['type'] == 'hosts':
                if operation['action'] == 'add':
                    idx = operation.get('index')
                    new_data = operation.get('new_data')
                    if idx is not None and idx < len(self._hosts_data):
                        if self._hosts_data[idx] == new_data or (isinstance(new_data, dict) and self._hosts_data[idx].get('host') == new_data.get('host')):
                            del self._hosts_data[idx]
                        else:
                            for i, h in enumerate(self._hosts_data):
                                if isinstance(h, dict) and isinstance(new_data, dict) and h.get('host') == new_data.get('host'):
                                    del self._hosts_data[i]
                                    break
                    elif new_data and isinstance(new_data, dict):
                        host_key = new_data.get('host')
                        self._hosts_data = [h for h in self._hosts_data if not (isinstance(h, dict) and h.get('host') == host_key)]
                    self._hosts_data_raw = []
                elif operation['action'] == 'update':
                    old_data = operation.get('old_data')
                    idx = operation.get('index')
                    if old_data and isinstance(old_data, dict):
                        host_key = old_data.get('host')
                        found = False
                        for i, h in enumerate(self._hosts_data):
                            if isinstance(h, dict) and h.get('host') == host_key:
                                self._hosts_data[i] = old_data
                                found = True
                                break
                        if not found and idx is not None and idx < len(self._hosts_data):
                            self._hosts_data[idx] = old_data
                    self._hosts_data_raw = []
                elif operation['action'] == 'delete':
                    removed = operation.get('old_data')
                    if isinstance(removed, list):
                        self._hosts_data.extend(removed)
                    elif removed:
                        self._hosts_data.append(removed)
                    self._hosts_data_raw = []
                elif operation['action'] == 'set':
                    old_data = operation.get('old_data')
                    if old_data is not None:
                        self._hosts_data = [dict(h) for h in old_data] if isinstance(old_data, list) else old_data
                    self._hosts_data_raw = []
                    
            elif operation['type'] == 'commands':
                if operation['action'] == 'add':
                    idx = operation.get('index')
                    new_data = operation.get('new_data')
                    if idx is not None and idx < len(self._commands_data):
                        if isinstance(new_data, dict) and isinstance(self._commands_data[idx], dict) and self._commands_data[idx].get('name') == new_data.get('name'):
                            del self._commands_data[idx]
                        else:
                            for i, c in enumerate(self._commands_data):
                                if isinstance(c, dict) and isinstance(new_data, dict) and c.get('name') == new_data.get('name'):
                                    del self._commands_data[i]
                                    break
                elif operation['action'] == 'update':
                    old_data = operation.get('old_data')
                    if old_data and isinstance(old_data, dict):
                        name_key = old_data.get('name')
                        found = False
                        for i, c in enumerate(self._commands_data):
                            if isinstance(c, dict) and c.get('name') == name_key:
                                self._commands_data[i] = old_data
                                found = True
                                break
                        if not found:
                            idx = operation.get('index')
                            if idx is not None and idx < len(self._commands_data) and old_data:
                                self._commands_data[idx] = old_data
                elif operation['action'] == 'delete':
                    removed = operation.get('old_data')
                    if removed:
                        self._commands_data.append(removed)
                elif operation['action'] == 'set':
                    old_data = operation.get('old_data')
                    if old_data is not None:
                        self._commands_data = [dict(c) for c in old_data] if isinstance(old_data, list) else old_data
        
        self._notify_change(operation['type'], 'undo', operation)
        logger.debug(f"DataStore: Undone {operation['action']} on {operation['type']}")
        
        return operation
    
    def get_history(self, limit: int = 10) -> List[Dict]:
        """Получить последние ``limit`` операций из истории."""
        with self._lock:
            return list(self._operation_history)[-limit:]
    
    def clear_history(self) -> None:
        """Очистить историю операций."""
        with self._lock:
            self._operation_history.clear()
            logger.debug("DataStore: History cleared")
    
    # ========== STATISTICS ==========
    
    def get_stats(self) -> Dict[str, int]:
        """Получить статистику хранилища (чтения, записи, ошибки, количество записей)."""
        with self._lock:
            return {
                **self._stats,
                'hosts_count': len(self._hosts_data),
                'commands_count': len(self._commands_data),
                'history_size': len(self._operation_history)
            }
    
    def reset_stats(self) -> None:
        """Сбросить счётчики статистики."""
        with self._lock:
            self._stats = {
                'reads': 0,
                'writes': 0,
                'validations': 0,
                'errors': 0
            }
    
    # ========== INTERNAL METHODS ==========
    
    def _load_hosts_from_file_internal(self, filename: str = DEFAULT_HOSTS_FILE) -> List[Dict]:
        """
        Внутренняя загрузка хостов (без блокировки).
        
        Извлекает все устройства из вложенной структуры с группами.
        """
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Извлекаем все устройства рекурсивно
                all_hosts = []
                self._extract_hosts_recursive(data, all_hosts)
                
                logger.debug(f"DataStore: Loaded {len(all_hosts)} hosts from file")
                return all_hosts
                
        except FileNotFoundError:
            logger.debug(f"DataStore: Hosts file not found: {filename}")
            return []
        except Exception as e:
            logger.error(f"DataStore: Error loading hosts from file: {e}")
            return []
    
    def _extract_hosts_recursive(self, data, hosts_list: List[Dict]):
        """Рекурсивное извлечение хостов из вложенной структуры"""
        if isinstance(data, list):
            for item in data:
                self._extract_hosts_recursive(item, hosts_list)
        elif isinstance(data, dict):
            # Проверяем это устройство (есть name и host)
            if "name" in data and "host" in data:
                hosts_list.append(data)
            else:
                # Это группа — извлекаем устройства из значений
                for value in data.values():
                    self._extract_hosts_recursive(value, hosts_list)
    
    def _load_commands_from_file_internal(self, filename: str = DEFAULT_COMMANDS_FILE) -> List[Dict]:
        """Внутренняя загрузка команд (без блокировки)"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except FileNotFoundError:
            logger.debug(f"DataStore: Commands file not found: {filename}")
            return []
        except Exception as e:
            logger.error(f"DataStore: Error loading commands from file: {e}")
            return []
    
    def _validate_host_internal(self, host_data: Dict, strict: bool = True) -> Tuple[bool, str]:
        """
        Валидация данных хоста.
        
        Args:
            host_data: Данные для проверки
            strict: Если True, требуется name; если False, только host
            
        Returns:
            (is_valid, error_message)
        """
        if not isinstance(host_data, dict):
            return False, "Host data must be a dictionary"
        
        # Обязательное поле: host
        if 'host' not in host_data:
            return False, "Host field is required"
        
        # Проверка формата host (IP или hostname)
        host = host_data.get('host', '')
        if not host:
            return False, "Host cannot be empty"
        
        # Опционально: name (требуется в strict режиме)
        if strict and 'name' not in host_data:
            return False, "Name field is required"
        
        # Проверка port если указан
        port = host_data.get('port')
        if port is not None:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                return False, f"Invalid port: {port}"
        
        # Проверка MAC-адреса если указан (опционально)
        mac_address = host_data.get('mac_address')
        if mac_address:
            mac_pattern = r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$|^([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$'
            if not re.match(mac_pattern, mac_address):
                return False, f"Invalid MAC address: {mac_address}"
        
        return True, ""
    
    def _validate_command_internal(self, command_data: Dict) -> Tuple[bool, str]:
        """Валидация данных команды"""
        if not isinstance(command_data, dict):
            return False, "Command data must be a dictionary"
        
        # Обязательные поля
        if 'name' not in command_data:
            return False, "Command name is required"
        
        if 'commands' not in command_data:
            return False, "Command text is required"
        
        # Проверка типа команды
        cmd_type = command_data.get('type', 'ssh')
        if cmd_type not in ('ssh', 'sftp', 'local'):
            return False, f"Invalid command type: {cmd_type}"
        
        return True, ""
    
    def _find_host_index_internal(self, host: str, port: Optional[int] = None) -> Optional[int]:
        """Найти индекс хоста (без блокировки)"""
        for i, h in enumerate(self._hosts_data):
            if h.get('host') == host:
                if port is None or h.get('port') == port:
                    return i
        return None
    
    def _find_command_index_internal(self, name: str) -> Optional[int]:
        """Найти индекс команды (без блокировки)"""
        for i, cmd in enumerate(self._commands_data):
            if cmd.get('name') == name:
                return i
        return None
    
    def _add_operation(
        self,
        op_type: str,
        action: str,
        old_data: Any,
        new_data: Any,
        index: Optional[int] = None
    ):
        """Добавить операцию в историю"""
        self._operation_history.append({
            'type': op_type,
            'action': action,
            'old_data': old_data,
            'new_data': new_data,
            'index': index,
            'timestamp': time.time()
        })
    
    def set_event_bus(self, event_bus: 'EventBus') -> None:
        """Установить EventBus для интеграции с архитектурой"""
        self._event_bus = event_bus

    def _notify_change(self, data_type: str, action: str, data: Any):
        try:
            if data_type == 'hosts':
                self.hosts_data_changed.emit({'action': action, 'data': data})
            elif data_type == 'commands':
                self.commands_data_changed.emit({'action': action, 'data': data})
            
            if self._event_bus:
                event_type = EventType.DATA_CHANGED
                self._event_bus.publish_typed(
                    event_type=event_type,
                    source='DataStore',
                    data={
                        'type': data_type,
                        'action': action,
                        'data': data,
                        'timestamp': time.time()
                    }
                )
            
        except Exception as e:
            logger.error(f"DataStore: Error notifying change: {e}")


# Глобальный экземпляр
datastore = DataStore()
