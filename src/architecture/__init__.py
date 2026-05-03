"""
pyktool Architecture Module

Универсальная структура взаимодействия между окнами и элементами интерфейса.
Предоставляет централизованное управление диалогами, шину событий и управление состоянием.
"""

from .event_bus import EventBus, EventType, Event
from .dialog_manager import DialogManager, DialogResult, DialogConfig
from .view_state import ViewState, StateChangeEvent
from .interfaces import (
    IDialogClient,
    IEventHandler,
    IStateObserver,
    IWorkerClient,
    DialogFactory,
    EventFilter
)
from .worker_bridge import WorkerBridge, WorkerEventAdapter

__all__ = [
    # Event Bus
    'EventBus',
    'EventType',
    'Event',
    
    # Dialog Manager
    'DialogManager',
    'DialogResult',
    'DialogConfig',
    
    # View State
    'ViewState',
    'StateChangeEvent',
    
    # Interfaces
    'IDialogClient',
    'IEventHandler',
    'IStateObserver',
    'IWorkerClient',
    'DialogFactory',
    'EventFilter',
    
    # Worker Bridge
    'WorkerBridge',
    'WorkerEventAdapter',
]
