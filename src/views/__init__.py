"""
IKTool - Инструмент для управления устройствами
Version: 1.0.0
"""

# from src.models import CustomTreeItem, CustomItemModel, CustomListItem
# from src.views import MainWindow, EditDeviceDialog
# from src.workers import CommandWorker, PingWorker, PingThread

# __version__ = '1.0.0'

# __all__ = [
#     'CustomTreeItem',
#     'CustomItemModel', 
#     'CustomListItem',
#     'MainWindow',
#     'EditDeviceDialog',
#     'PingThread',
# ]


from src.views.main_window import MainWindow

__all__ = [
    'MainWindow'
]
