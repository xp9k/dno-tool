"""
Service Initialization - Инициализация сервисов приложения.

Регистрирует все сервисы в DI контейнере с правильными зависимостями.
DI контейнер автоматически разрешает зависимости через type hints.
"""

from src.di import get_container, DIContainer
from src.services import DeviceService, CommandService, ConfigService
from src.architecture import EventBus, DialogManager, WorkerBridge, ViewState
from src.data import DataStore, datastore
from src.logger import logger


def initialize_services(container: DIContainer = None) -> DIContainer:
    """
    Инициализировать и зарегистрировать все сервисы.
    
    Args:
        container: DI контейнер (по умолчанию используется глобальный)
    
    Returns:
        Настроенный DI контейнер
    """
    if container is None:
        container = get_container()
    
    logger.info("Initializing services...")
    
    # Создаём архитектурные компоненты с правильными зависимостями
    event_bus = EventBus()
    dialog_manager = DialogManager(event_bus=event_bus)
    worker_bridge = WorkerBridge(event_bus=event_bus)
    view_state = ViewState()
    
    # Регистрируем архитектурные компоненты как singleton-экземпляры
    container.register_instance(EventBus, event_bus)
    container.register_instance(DialogManager, dialog_manager)
    container.register_instance(WorkerBridge, worker_bridge)
    container.register_instance(ViewState, view_state)
    
    # Регистрируем DataStore как singleton и привязываем EventBus
    datastore.set_event_bus(event_bus)
    container.register_instance(DataStore, datastore)
    
    # Регистрируем сервисы — DI автоматически внедряет EventBus через type hints
    container.register(DeviceService, DeviceService, singleton=True)
    container.register(CommandService, CommandService, singleton=True)
    container.register(ConfigService, ConfigService, singleton=True)
    
    logger.info("Services initialized successfully")
    logger.info(f"  - EventBus: {id(event_bus)}")
    logger.info(f"  - DialogManager: {id(dialog_manager)}")
    logger.info(f"  - WorkerBridge: {id(worker_bridge)}")
    logger.info(f"  - ViewState: {id(view_state)}")
    logger.info(f"  - DataStore: registered")
    logger.info(f"  - DeviceService: registered")
    logger.info(f"  - CommandService: registered")
    logger.info(f"  - ConfigService: registered")
    
    return container