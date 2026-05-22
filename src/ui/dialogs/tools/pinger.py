"""Диалог мониторинга пинга с графическим отображением задержки в реальном времени."""

"""
PingMonitorDialog — диалог мониторинга пинга с правильным управлением потоками.

Исправления:
- Использование QThread вместо threading.Thread
- Правильная очистка ресурсов при закрытии
- Защита от повторного запуска
- Корректная остановка worker
"""

import sys
import subprocess
import re
import time
from datetime import datetime
from typing import Optional, List

from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                               QLineEdit, QPushButton, QLabel, QWidget)
from PySide6.QtCore import Signal, QObject, QThread, QTimer
from PySide6.QtGui import QCloseEvent
import pyqtgraph as pg

from src.logger import logger


class PingThread(QThread):
    """Поток для пинга с прямым переопределением run()"""

    ping_result = Signal(float, datetime)
    error = Signal(str)

    def __init__(self, target: str, interval: float = 1.0, parent=None):
        super().__init__(parent)
        self.target = target
        self.interval = interval
        self._stop_requested = False

    def run(self):
        """Основной цикл пинга в отдельном потоке"""
        logger.debug(f"PingThread: Started pinging {self.target}")
        
        while not self._stop_requested:
            try:
                # Универсальная команда ping для разных ОС
                command = ["ping", "-n", "1", self.target] if sys.platform == "win32" else ["ping", "-c", "1", self.target]
                ping_process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                stdout, _ = ping_process.communicate()

                try:
                    stdout = stdout.decode('cp866')
                except Exception:
                    stdout = stdout.decode('utf-8', errors='replace')

                # Парсим вывод ping
                if sys.platform == "win32":
                    match = re.search(r"время[=<](\d+)мс", stdout)
                else:
                    match = re.search(r"time[=<](\d+\.?\d*)\s*ms", stdout)

                if match:
                    delay = float(match.group(1))
                    self.ping_result.emit(delay, datetime.now())
                else:
                    self.ping_result.emit(0, datetime.now())

            except Exception as e:
                logger.error(f"PingThread: Error during ping: {e}")
                self.error.emit(str(e))
                self.ping_result.emit(0, datetime.now())

            # Ждем интервал, проверяя флаг остановки
            if not self._stop_requested:
                start = time.time()
                while time.time() - start < self.interval and not self._stop_requested:
                    time.sleep(0.1)
        
        logger.debug(f"PingThread: Stopped pinging {self.target}")

    def stop(self):
        """Остановка пинга"""
        self._stop_requested = True
        self.wait(3000)


class PingMonitorDialog(QDialog):
    """Диалог мониторинга пинга с правильным управлением ресурсами"""
    
    def __init__(self, parent=None, target: str = "8.8.8.8"):
        super().__init__(parent)
        self.setWindowTitle("Ping-мониторинг")
        self.resize(800, 600)

        # Состояние
        self._is_running = False
        self._target = target

        # Поток
        self._ping_thread: Optional[PingThread] = None

        # Данные графика
        self._data_times: List[datetime] = []
        self._data_delays: List[float] = []
        self._max_points = 100

        self._init_ui()

    def _init_ui(self):
        """Инициализация интерфейса"""
        layout = QVBoxLayout()

        # Панель управления
        control_layout = QHBoxLayout()
        self.address_input = QLineEdit(self._target)
        self.address_input.setPlaceholderText("Введите IP или hostname")
        self.start_button = QPushButton("Старт")
        self.start_button.clicked.connect(self._start_ping)
        self.stop_button = QPushButton("Стоп")
        self.stop_button.clicked.connect(self._stop_ping)
        self.stop_button.setEnabled(False)

        control_layout.addWidget(QLabel("Цель:"))
        control_layout.addWidget(self.address_input, 1)
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)

        # Настройка графика
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground((30, 30, 30))
        self.plot_widget.setLabel('left', 'Задержка (мс)', color='w')
        self.plot_widget.setLabel('bottom', 'Время (сек)', color='w')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setYRange(0, 200)
        # Цвет осей и клеток
        self.plot_widget.getAxis('left').setPen('w')
        self.plot_widget.getAxis('bottom').setPen('w')
        self.plot_widget.getAxis('left').setTextPen('w')
        self.plot_widget.getAxis('bottom').setTextPen('w')

        # Кривая графика (цвет будет обновляться динамически)
        self.plot_curve = self.plot_widget.plot(
            pen=pg.mkPen('g', width=2),
            symbol='o',
            symbolSize=5,
            symbolBrush='g'
        )

        # Статус
        self.status_label = QLabel("Готов")

        layout.addLayout(control_layout)
        layout.addWidget(self.plot_widget, 1)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def _start_ping(self):
        """Запуск мониторинга"""
        target = self.address_input.text().strip()
        if not target:
            logger.warning("PingMonitorDialog: Target address not specified")
            return
        
        if self._is_running:
            logger.warning("PingMonitorDialog: Already running")
            return

        logger.info(f"PingMonitorDialog: Starting ping to {target}")
        
        self._is_running = True
        self._target = target
        
        # UI обновления
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.address_input.setEnabled(False)
        self.status_label.setText(f"Пинг {target}...")

        # Очищаем данные
        self._data_times = []
        self._data_delays = []
        self.plot_curve.setData([], [])

        # Создаем и запускаем поток
        self._ping_thread = PingThread(target, interval=1.0)

        # Подключаем сигналы
        self._ping_thread.ping_result.connect(self._update_graph)
        self._ping_thread.error.connect(self._on_error)

        # Запускаем поток
        self._ping_thread.start()

    def _stop_ping(self):
        """Остановка мониторинга"""
        if not self._is_running:
            return

        logger.info("PingMonitorDialog: Stopping ping")

        # Останавливаем поток
        if self._ping_thread:
            self._ping_thread.stop()
            self._ping_thread = None

        self._is_running = False

        # UI обновления
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.address_input.setEnabled(True)
        self.status_label.setText("Остановлено")

    def _update_graph(self, delay: float, timestamp: datetime):
        """Обновление графика (вызывается в UI потоке)"""
        # Игнорируем нулевые значения
        if delay <= 0:
            return

        # Добавляем данные
        self._data_delays.append(delay)
        self._data_times.append(timestamp)

        # Ограничиваем количество точек
        if len(self._data_times) > self._max_points:
            self._data_times = self._data_times[-self._max_points:]
            self._data_delays = self._data_delays[-self._max_points:]

        if not self._data_times:
            return

        # Преобразуем время в секунды
        first_time = self._data_times[0]
        time_sec = [(t - first_time).total_seconds() for t in self._data_times]

        # Округляем для отображения
        if time_sec:
            seconds = int(time_sec[-1])
            if seconds % 60 == 0:
                time_sec[-1] = seconds % 60

        # Обновляем график
        color = self._get_ping_color(self._data_delays[-1])
        self.plot_curve.setPen(pg.mkPen(color, width=2))
        self.plot_curve.setSymbolBrush(pg.mkBrush(color))
        self.plot_curve.setData(time_sec, self._data_delays)

        # Автомасштабирование
        if self._data_delays:
            max_delay = max(self._data_delays)
            self.plot_widget.setYRange(0, max(100, max_delay * 1.2))
            self.plot_widget.setXRange(0, max(10, time_sec[-1] * 1.1 if time_sec else 10))

        # Обновляем статус
        color = self._get_ping_color(delay)
        self.status_label.setText(f"Пинг {self._target}: {delay:.1f} мс")

    def _on_error(self, error_msg: str):
        """Обработка ошибки"""
        logger.error(f"PingMonitorDialog: Error: {error_msg}")
        self.status_label.setText(f"Ошибка: {error_msg}")

    def _get_ping_color(self, delay: float) -> str:
        """Получить цвет линии в зависимости от пинга"""
        if delay <= 50:
            return '#00ff00'  # ярко-зеленый (0-50 мс)
        elif delay <= 200:
            return '#ffff00'  # ярко-желтый (51-200 мс)
        else:
            return '#ff0000'  # красный (>200 мс)

    def closeEvent(self, event: QCloseEvent):
        """Корректная очистка при закрытии"""
        logger.debug("PingMonitorDialog: Closing, cleaning up resources")
        self._stop_ping()
        super().closeEvent(event)
        logger.debug("PingMonitorDialog: Closed")

    def reject(self):
        """Обработка закрытия по Escape"""
        self._stop_ping()
        # Даем потоку время на завершение
        if self._ping_thread and self._ping_thread.isRunning():
            self._ping_thread.wait(1000)
        super().reject()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = PingMonitorDialog()
    dialog.show()
    sys.exit(app.exec())
