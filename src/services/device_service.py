"""
Device Service - Сервис для управления устройствами.

Предоставляет бизнес-логику для операций с устройствами:
- CRUD операции
- Валидация
- Импорт/экспорт
- Публикация событий об изменениях
"""

from typing import List, Dict, Optional, Tuple, Any
from src.domain.models.device import DeviceModel
from src.architecture import EventType
from src.data import datastore
from .base import BaseService


class DeviceService(BaseService):
    """
    Сервис для управления устройствами.
    
    Инкапсулирует всю бизнес-логику работы с устройствами,
    предоставляя UI компонентам простой интерфейс.
    """

    def __init__(self, event_bus: Optional['EventBus'] = None) -> None:
        super().__init__(event_bus)
        self._datastore = datastore

    # ========== ЧТЕНИЕ ДАННЫХ ==========

    def get_all_devices(self) -> List[DeviceModel]:
        """
        Получить все устройства.
        
        Returns:
            Плоский список всех устройств
        """
        return self._datastore.get_hosts_data()

    def get_devices_raw(self) -> List[Dict]:
        """
        Получить оригинальные данные устройств (с группами).
        
        Returns:
            Оригинальные данные с иерархией групп
        """
        return self._datastore.get_hosts_data_raw()

    def get_device(self, host: str, port: Optional[int] = None) -> Optional[DeviceModel]:
        """
        Найти устройство по host и опционально port.
        
        Args:
            host: hostname или IP
            port: порт (None = найти все совпадения по host)
        
        Returns:
            Устройство или None если не найдено
        """
        return self._datastore.find_host(host, port)

    def get_devices_by_folder(self, folder_path: str) -> List[DeviceModel]:
        """
        Получить все устройства в папке.
        
        Args:
            folder_path: Путь к папке
        
        Returns:
            Список устройств в папке
        """
        return self._datastore.get_hosts_by_folder(folder_path)

    def get_devices_count(self) -> int:
        """
        Получить количество устройств.
        
        Returns:
            Количество устройств
        """
        return self._datastore.get_hosts_count()

    # ========== СОЗДАНИЕ И ОБНОВЛЕНИЕ ==========

    def add_device(
        self,
        device_data: Dict[str, Any],
        folder: Optional[str] = None
    ) -> Tuple[bool, str, Optional[DeviceModel]]:
        """
        Добавить новое устройство.
        
        Args:
            device_data: Данные устройства (name, host, port, login, password)
            folder: Опционально папка для устройства
        
        Returns:
            (success, message, device)
        """
        try:
            # Валидация обязательных полей
            if 'host' not in device_data or not device_data['host']:
                return False, "Host является обязательным полем", None
            
            if 'name' not in device_data or not device_data['name']:
                return False, "Name является обязательным полем", None
            
            # Проверка на дубликат
            existing = self.get_device(device_data['host'], device_data.get('port'))
            if existing:
                return False, f"Устройство {device_data['host']} уже существует", None
            
            # Создаем устройство
            device = DeviceModel(device_data)
            
            # Добавляем folder если указан
            if folder:
                device_data['folder'] = folder
            
            # Сохраняем через datastore
            success, message = self._datastore.update_host(device_data)
            
            if success:
                self._logger.info(f"DeviceService: Added device {device.host}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.DEVICE_ADDED,
                    source='DeviceService',
                    data={
                        'device': device.host,
                        'name': device.name,
                        'folder': folder
                    }
                )
                
                return True, message, device
            else:
                return False, message, None
                
        except Exception as e:
            self._logger.error(f"DeviceService: Error adding device: {e}")
            return False, str(e), None

    def update_device(
        self,
        host: str,
        device_data: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[DeviceModel]]:
        """
        Обновить существующее устройство.
        
        Args:
            host: hostname или IP устройства для обновления
            device_data: Новые данные устройства
        
        Returns:
            (success, message, updated_device)
        """
        try:
            # Проверяем существование
            existing = self.get_device(host)
            if not existing:
                return False, f"Устройство {host} не найдено", None
            
            # Обновляем данные
            update_data = {**existing.to_dict(), **device_data}
            update_data['host'] = host  # Сохраняем оригинальный host для поиска
            
            success, message = self._datastore.update_host(update_data)
            
            if success:
                updated_device = DeviceModel(update_data)
                self._logger.info(f"DeviceService: Updated device {host}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.DEVICE_UPDATED,
                    source='DeviceService',
                    data={
                        'device': host,
                        'changes': list(device_data.keys())
                    }
                )
                
                return True, message, updated_device
            else:
                return False, message, None
                
        except Exception as e:
            self._logger.error(f"DeviceService: Error updating device: {e}")
            return False, str(e), None

    def add_or_update_device(
        self,
        device_data: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[DeviceModel]]:
        """
        Добавить или обновить устройство (upsert).
        
        Args:
            device_data: Данные устройства
        
        Returns:
            (success, message, device)
        """
        host = device_data.get('host')
        if not host:
            return False, "Host является обязательным полем", None
        
        existing = self.get_device(host)
        
        if existing:
            return self.update_device(host, device_data)
        else:
            return self.add_device(device_data)

    def add_devices_batch(
        self,
        devices_data: List[Dict[str, Any]],
        folder: Optional[str] = None
    ) -> Tuple[int, int, str]:
        """
        Добавить несколько устройств пакетом.
        
        Args:
            devices_data: Список данных устройств
            folder: Опционально папка для всех устройств
        
        Returns:
            (added_count, skipped_count, message)
        """
        added = 0
        skipped = 0
        
        for device_data in devices_data:
            success, _, _ = self.add_device(device_data, folder)
            if success:
                added += 1
            else:
                skipped += 1
        
        message = f"Добавлено: {added}, пропущено: {skipped}"
        self._logger.info(f"DeviceService: Batch add completed - {message}")
        
        return added, skipped, message

    # ========== УДАЛЕНИЕ ==========

    def delete_device(self, host: str, port: Optional[int] = None) -> Tuple[bool, str]:
        """
        Удалить устройство.
        
        Args:
            host: hostname или IP
            port: порт (None = удалить все совпадения по host)
        
        Returns:
            (success, message)
        """
        try:
            # Проверяем существование
            existing = self.get_device(host, port)
            if not existing:
                return False, f"Устройство {host} не найдено"
            
            success = self._datastore.delete_host(host, port)
            
            if success:
                self._logger.info(f"DeviceService: Deleted device {host}")
                
                # Публикуем событие
                self._event_bus.publish_typed(
                    EventType.DEVICE_REMOVED,
                    source='DeviceService',
                    data={
                        'device': host,
                        'port': port
                    }
                )
                
                return True, "Устройство удалено"
            else:
                return False, "Ошибка при удалении устройства"
                
        except Exception as e:
            self._logger.error(f"DeviceService: Error deleting device: {e}")
            return False, str(e)

    # ========== МАССОВЫЕ ОПЕРАЦИИ ==========

    def save_devices(self, devices_data: List[Dict]) -> bool:
        """
        Сохранить все устройства (полная замена).
        
        Args:
            devices_data: Новые данные всех устройств
        
        Returns:
            True если успешно
        """
        try:
            success = self._datastore.set_hosts_data(devices_data)
            
            if success:
                self._logger.info(f"DeviceService: Saved {len(devices_data)} devices")
                
                self._event_bus.publish_typed(
                    EventType.DATA_SAVED,
                    source='DeviceService',
                    data={'type': 'devices', 'count': len(devices_data)}
                )
            
            return success
            
        except Exception as e:
            self._logger.error(f"DeviceService: Error saving devices: {e}")
            return False

    def import_devices_from_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Импортировать устройства из файла.
        
        Args:
            file_path: Путь к файлу
        
        Returns:
            (success, message)
        """
        try:
            success = self._datastore.load_hosts_from_file(file_path)
            
            if success:
                count = self.get_devices_count()
                self._logger.info(f"DeviceService: Imported devices from {file_path} ({count} devices)")
                
                self._event_bus.publish_typed(
                    EventType.DATA_LOADED,
                    source='DeviceService',
                    data={'type': 'devices', 'file': file_path, 'count': count}
                )
                
                return True, f"Импортировано {count} устройств"
            else:
                return False, "Ошибка при загрузке файла"
                
        except Exception as e:
            self._logger.error(f"DeviceService: Error importing devices: {e}")
            return False, str(e)

    def export_devices_to_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Экспортировать устройства в файл.
        
        Args:
            file_path: Путь к файлу
        
        Returns:
            (success, message)
        """
        try:
            success = self._datastore.save_hosts_to_file(file_path)
            
            if success:
                count = self.get_devices_count()
                self._logger.info(f"DeviceService: Exported devices to {file_path} ({count} devices)")
                
                return True, f"Экспортировано {count} устройств"
            else:
                return False, "Ошибка при сохранении файла"
                
        except Exception as e:
            self._logger.error(f"DeviceService: Error exporting devices: {e}")
            return False, str(e)

    # ========== ВАЛИДАЦИЯ ==========

    def validate_device(self, device_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Валидировать данные устройства.
        
        Args:
            device_data: Данные для валидации
        
        Returns:
            (is_valid, error_message)
        """
        if not isinstance(device_data, dict):
            return False, "Данные должны быть словарем"
        
        if 'host' not in device_data:
            return False, "Host является обязательным полем"
        
        host = device_data.get('host', '')
        if not host:
            return False, "Host не может быть пустым"
        
        # Проверка формата IP или hostname
        import re
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
        
        if not (re.match(ip_pattern, host) or re.match(hostname_pattern, host)):
            return False, f"Неверный формат host: {host}"
        
        # Проверка port
        port = device_data.get('port')
        if port is not None:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                return False, f"Неверный порт: {port}"
        
        return True, ""
