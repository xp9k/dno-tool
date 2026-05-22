"""Пакет кастомных виджетов приложения."""

# UI Widgets package

from .device_tree import DeviceTreeView, CustomTreeItem, CustomTreeItemModel
from .device_list import DeviceListView, CustomListItem
from .result_table import CommandResultTable
from .checkable_combo import CheckableComboBox
from .task_combo import TaskComboBox
from .syntax_highlight import BashSyntaxHighlighter

__all__ = [
    'DeviceTreeView',
    'CustomTreeItem',
    'CustomTreeItemModel',
    'DeviceListView',
    'CustomListItem',
    'CommandResultTable',
    'CheckableComboBox',
    'TaskComboBox',
    'BashSyntaxHighlighter',
]
