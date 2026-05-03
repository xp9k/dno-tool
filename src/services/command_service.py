"""
Command Service - Сервис для управления командами.

Предоставляет бизнес-логику для операций с командами:
- CRUD операции
- Валидация
- Импорт/экспорт
- Публикация событий об изменениях
"""

from typing import List, Dict, Optional, Tuple, Any
from src.architecture import EventType
from src.data import datastore
from .base import BaseService


class CommandService(BaseService):
    """
    Сервис для управления командами.
    
    Инкапсулирует всю бизнес-логику работы с командами,
    предоставляя UI компонентам простой интерфейс.
    """

    def __init__(self, event_bus: Optional['EventBus'] = None):
        """
        Инициализация сервиса команд.
        
        Args:
            event_bus: Экземпляр EventBus (опционально)
        """
        super().__init__(event_bus)
        self._datastore = datastore

    # ========== ЧТЕНИЕ ДАННЫХ ==========

    def get_all_commands(self) -> List[Dict]:
        """
        Получить все команды.
        
        Returns:
            Список всех команд
        """
        return self._datastore.get_commands_data()

    def get_command(self, name: str) -> Optional[Dict]:
        """
        Найти команду по имени.
        
        Args:
            name: Имя команды
        
        Returns:
            Данные команды или None
        """
        return self._datastore.find_command(name)

    def get_commands_count(self) -> int:
        """
        Получить количество команд.
        
        Returns:
            Количество команд
        """
        return self._datastore.get_commands_count()

    # ========== СОЗДАНИЕ И ОБНОВЛЕНИЕ ==========

    def add_command(
        self,
        command_data: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Добавить новую команду.
        
        Args:
            command_data: Данные команды (name, commands, type, description, params)
        
        Returns:
            (success, message, command)
        """
        try:
            # Валидация обязательных полей
            if 'name' not in command_data or not command_data['name']:
                return False, "Имя команды является обязательным полем", None
            
            if 'commands' not in command_data:
                return False, "Команды являются обязательным полем", None
            
            # Проверка на дубликат
            existing = self.get_command(command_data['name'])
            if existing:
                return False, f"Команда '{command_data['name']}' уже существует", None
            
            # Сохраняем через datastore
            success, message = self._datastore.update_command(command_data)
            
            if success:
                self._logger.info(f"CommandService: Added command {command_data['name']}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.CUSTOM,  # Можно добавить COMMAND_ADDED
                    source='CommandService',
                    data={
                        'command': command_data['name'],
                        'action': 'added'
                    }
                )
                
                return True, message, command_data
            else:
                return False, message, None
                
        except Exception as e:
            self._logger.error(f"CommandService: Error adding command: {e}")
            return False, str(e), None

    def update_command(
        self,
        name: str,
        command_data: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Обновить существующую команду.
        
        Args:
            name: Имя команды для обновления
            command_data: Новые данные команды
        
        Returns:
            (success, message, updated_command)
        """
        try:
            # Проверяем существование
            existing = self.get_command(name)
            if not existing:
                return False, f"Команда '{name}' не найдена", None
            
            # Обновляем данные
            update_data = {**existing, **command_data}
            update_data['name'] = name  # Сохраняем оригинальное имя для поиска
            
            success, message = self._datastore.update_command(update_data)
            
            if success:
                self._logger.info(f"CommandService: Updated command {name}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.DATA_CHANGED,
                    source='CommandService',
                    data={
                        'command': name,
                        'action': 'updated'
                    }
                )
                
                return True, message, update_data
            else:
                return False, message, None
                
        except Exception as e:
            self._logger.error(f"CommandService: Error updating command: {e}")
            return False, str(e), None

    def add_or_update_command(
        self,
        command_data: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Добавить или обновить команду (upsert).
        
        Args:
            command_data: Данные команды
        
        Returns:
            (success, message, command)
        """
        name = command_data.get('name')
        if not name:
            return False, "Имя команды является обязательным полем", None
        
        existing = self.get_command(name)
        
        if existing:
            return self.update_command(name, command_data)
        else:
            return self.add_command(command_data)

    def delete_command(self, name: str) -> Tuple[bool, str]:
        """
        Удалить команду.
        
        Args:
            name: Имя команды
        
        Returns:
            (success, message)
        """
        try:
            # Проверяем существование
            existing = self.get_command(name)
            if not existing:
                return False, f"Команда '{name}' не найдена"
            
            success = self._datastore.delete_command(name)
            
            if success:
                self._logger.info(f"CommandService: Deleted command {name}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.CUSTOM,  # Можно добавить COMMAND_REMOVED
                    source='CommandService',
                    data={
                        'command': name,
                        'action': 'deleted'
                    }
                )
                
                return True, "Команда удалена"
            else:
                return False, "Ошибка при удалении команды"
                
        except Exception as e:
            self._logger.error(f"CommandService: Error deleting command: {e}")
            return False, str(e)

    # ========== МАССОВЫЕ ОПЕРАЦИИ ==========

    def save_commands(self, commands_data: List[Dict]) -> bool:
        """
        Сохранить все команды (полная замена).
        
        Args:
            commands_data: Новые данные всех команд
        
        Returns:
            True если успешно
        """
        try:
            success = self._datastore.set_commands_data(commands_data)
            
            if success:
                self._logger.info(f"CommandService: Saved {len(commands_data)} commands")
                
                self._event_bus.publish_typed(
                    EventType.DATA_SAVED,
                    source='CommandService',
                    data={'type': 'commands', 'count': len(commands_data)}
                )
            
            return success
            
        except Exception as e:
            self._logger.error(f"CommandService: Error saving commands: {e}")
            return False

    def import_commands_from_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Импортировать команды из файла.
        
        Args:
            file_path: Путь к файлу
        
        Returns:
            (success, message)
        """
        try:
            success = self._datastore.load_commands_from_file(file_path)
            
            if success:
                count = self.get_commands_count()
                self._logger.info(f"CommandService: Imported commands from {file_path} ({count} commands)")
                
                self._event_bus.publish_typed(
                    EventType.DATA_LOADED,
                    source='CommandService',
                    data={'type': 'commands', 'file': file_path, 'count': count}
                )
                
                return True, f"Импортировано {count} команд"
            else:
                return False, "Ошибка при загрузке файла"
                
        except Exception as e:
            self._logger.error(f"CommandService: Error importing commands: {e}")
            return False, str(e)

    def export_commands_to_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Экспортировать команды в файл.
        
        Args:
            file_path: Путь к файлу
        
        Returns:
            (success, message)
        """
        try:
            success = self._datastore.save_commands_to_file(file_path)
            
            if success:
                count = self.get_commands_count()
                self._logger.info(f"CommandService: Exported commands to {file_path} ({count} commands)")
                
                return True, f"Экспортировано {count} команд"
            else:
                return False, "Ошибка при сохранении файла"
                
        except Exception as e:
            self._logger.error(f"CommandService: Error exporting commands: {e}")
            return False, str(e)

    # ========== ВАЛИДАЦИЯ ==========

    def validate_command(self, command_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Валидировать данные команды.
        
        Args:
            command_data: Данные для валидации
        
        Returns:
            (is_valid, error_message)
        """
        if not isinstance(command_data, dict):
            return False, "Данные команды должны быть словарем"
        
        if 'name' not in command_data or not command_data['name']:
            return False, "Имя команды является обязательным полем"
        
        if 'commands' not in command_data:
            return False, "Команды являются обязательным полем"
        
        if not isinstance(command_data['commands'], list):
            return False, "Команды должны быть списком"
        
        # Проверка типа команды
        cmd_type = command_data.get('type', 'ssh')
        if cmd_type not in ('ssh', 'sftp', 'local'):
            return False, f"Неверный тип команды: {cmd_type}"
        
        return True, ""
