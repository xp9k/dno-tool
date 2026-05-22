"""
Точка входа в приложение DNO Tool.

Инициализирует DI-контейнер, регистрирует сервисы, создаёт окно приложения
и запускает главный цикл событий Qt.
"""

import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from src.config import get_asset_path
from src.logger import logger
from src import __version__

from src.di import get_container
from src.services import initialize_services

from src.views.main_window import MainWindow


def initialize_services_and_architecture() -> 'DIContainer':
    """
    ARCHITECTURE: Инициализация сервисов и архитектурных компонентов приложения.

    Создает и настраивает:
    - DI Container: контейнер зависимостей
    - EventBus: шина событий для коммуникации между компонентами
    - DialogManager: централизованное управление диалогами
    - WorkerBridge: мост для управления workers
    - ViewState: управление состоянием представлений
    - DeviceService: сервис для управления устройствами
    - CommandService: сервис для управления командами
    - ConfigService: сервис для управления конфигурацией
    """
    logger.info("Initializing services and architecture components...")

    # Получаем DI контейнер и инициализируем сервисы
    container = get_container()
    initialize_services(container)

    logger.info("Services and architecture components initialized:")
    
    # Получаем компоненты из контейнера для логирования
    from src.architecture import EventBus, DialogManager, WorkerBridge, ViewState
    
    event_bus = container.resolve(EventBus)
    dialog_manager = container.resolve(DialogManager)
    worker_bridge = container.resolve(WorkerBridge)
    view_state = container.resolve(ViewState)
    
    logger.info(f"  - EventBus: {id(event_bus)}")
    logger.info(f"  - DialogManager: {id(dialog_manager)}")
    logger.info(f"  - WorkerBridge: {id(worker_bridge)}")
    logger.info(f"  - ViewState: {id(view_state)}")
    logger.info(f"  - DeviceService: registered")
    logger.info(f"  - CommandService: registered")
    logger.info(f"  - ConfigService: registered")

    return container

def main() -> None:
    """Создаёт приложение Qt, инициализирует сервисы и запускает главный цикл."""
    # ARCHITECTURE: Инициализируем сервисы и архитектурные компоненты
    container = initialize_services_and_architecture()

    # Создаем экземпляр приложения
    app = QApplication(sys.argv)

    # Принудительное отображение иконок в меню (на Windows может быть отключено)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    # Устанавливаем имя организации и приложения для настроек
    app.setOrganizationName("DNO")
    app.setApplicationName("DNOTool")

    app.setWindowIcon(QIcon(get_asset_path('favicon.ico')))

    logger.info("Приложение запущено")

    # Создаем и показываем главное окно
    # ARCHITECTURE: MainWindow инициализирует сервисы самостоятельно через DI контейнер
    window = MainWindow()
    window.setWindowTitle(f"DNO Tool v{__version__}")
    window.resize(1024, 768)  # Начальный размер окна
    window.show()

    # Запускаем главный цикл приложения
    sys.exit(app.exec())

if __name__ == "__main__":
    logger.info("Запуск приложения")
    main()
