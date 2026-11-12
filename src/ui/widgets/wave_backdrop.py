"""Анимированный фон из волн для окна «О программе».

Виджет лежит под остальными дочерними виджетами окна и пропускает мышь.
Цвета берутся из палитры (``Highlight`` и соседние оттенки), поэтому фон
подходит и к светлой, и к тёмной теме. Источник уровня (функция 0..1)
раскачивает волны в такт музыке. Таймер работает, только пока окно видно.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QWidget

FRAME_MS = 33
POINT_STEP = 6

# Слой: высота гребня (доля высоты), амплитуда (px), длина волны (px),
# скорость (рад/с), сдвиг фазы, непрозрачность, сдвиг оттенка (градусы).
_LAYERS = (
    (0.62, 10.0, 260.0, 0.55, 0.0, 0.14, -38),
    (0.69, 13.0, 190.0, -0.80, 1.7, 0.19, 26),
    (0.76, 11.0, 310.0, 1.05, 3.1, 0.25, -12),
    (0.84, 9.0, 150.0, -1.35, 4.4, 0.33, 0),
)


class WaveBackdrop(QWidget):
    def __init__(self, parent: QWidget, level_source: Callable[[], float] | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._level_source = level_source
        self._level = 0.0
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self.update)

    def set_level_source(self, source: Callable[[], float] | None) -> None:
        self._level_source = source

    def showEvent(self, event) -> None:  # noqa: N802 - имя Qt
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - имя Qt
        self._timer.stop()
        super().hideEvent(event)

    def stop(self) -> None:
        self._timer.stop()

    def _next_level(self) -> float:
        target = 0.0
        if self._level_source is not None:
            try:
                target = max(0.0, min(1.0, float(self._level_source())))
            except Exception:  # noqa: BLE001 - фон не должен ронять окно
                target = 0.0
        # Быстрый подъём и плавный спад, как у стрелочного индикатора.
        factor = 0.45 if target > self._level else 0.08
        self._level += (target - self._level) * factor
        return self._level

    def paintEvent(self, event) -> None:  # noqa: N802 - имя Qt
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        seconds = self._clock.elapsed() / 1000.0
        level = self._next_level()
        swell = 0.75 + 1.6 * level

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.palette().color(QPalette.ColorRole.Highlight)

        glow = QLinearGradient(0, height * 0.5, 0, height)
        glow_color = QColor(accent)
        glow_color.setAlphaF(0.0)
        glow.setColorAt(0.0, glow_color)
        glow_color.setAlphaF(0.10 + 0.10 * level)
        glow.setColorAt(1.0, glow_color)
        painter.fillRect(0, int(height * 0.5), width, height, glow)

        for index, (crest, amplitude, wavelength, speed, phase, alpha, hue_shift) in enumerate(_LAYERS):
            color = _shift_hue(accent, hue_shift)
            base = height * crest
            k = 2.0 * math.pi / wavelength
            t = seconds * speed + phase
            amp = amplitude * swell
            crest_line = QPainterPath()
            x = 0.0
            while True:
                y = (
                    base
                    + amp * math.sin(k * x + t)
                    + amp * 0.35 * math.sin(k * 2.3 * x - t * 1.7 + index)
                )
                if x == 0.0:
                    crest_line.moveTo(x, y)
                else:
                    crest_line.lineTo(x, y)
                if x >= width:
                    break
                x = min(float(width), x + POINT_STEP)
            path = QPainterPath(crest_line)
            path.lineTo(width, height)
            path.lineTo(0, height)
            path.closeSubpath()

            fill = QLinearGradient(0, base - amp, 0, height)
            top = QColor(color)
            top.setAlphaF(alpha)
            bottom = QColor(color)
            bottom.setAlphaF(alpha * 0.35)
            fill.setColorAt(0.0, top)
            fill.setColorAt(1.0, bottom)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPath(path)

            if index == len(_LAYERS) - 1:
                edge = QColor(color)
                edge.setAlphaF(0.45 + 0.35 * level)
                painter.setPen(QPen(edge, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(crest_line)
        painter.end()


def _shift_hue(color: QColor, degrees: int) -> QColor:
    hue, saturation, value, _alpha = color.getHsv()
    if hue < 0:  # серый цвет без оттенка
        return QColor(color)
    return QColor.fromHsv((hue + degrees) % 360, saturation, value)
