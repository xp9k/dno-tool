"""Фоновая музыка из модуля XM через QAudioSink.

Синтез (``src.utils.audio.tracker``) идёт в GUI-потоке по таймеру: тик
модуля занимает доли миллисекунды, а буфер устройства держит около 250 мс,
так что короткие задержки GUI не слышны. Python-обработчиков, которые
вызывал бы аудиопоток Qt, нет: режим push, данные пишет таймер.

Нет устройства вывода, неподдерживаемый формат или битый файл - музыка
просто не играет, причина пишется в журнал DEBUG.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer

from src.utils.audio.tracker import XmFormatError, XmModule, XmRenderer, parse_xm

try:
    from src.logger import logger
except ImportError:  # pragma: no cover - ранний импорт без журнала
    import logging

    logger = logging.getLogger(__name__)

BUFFER_SEC = 0.25
FEED_INTERVAL_MS = 20
LEVEL_BLOCK = 1024
# Шкала уровня по RMS блока при усилении рендерера 0.35: у модуля окна
# медиана около 0.3, 95-й процентиль 0.45, тишина в паузах ниже 0.08.
LEVEL_FLOOR = 0.08
LEVEL_RANGE = 0.45


def load_module(path: str) -> XmModule | None:
    """Прочитать модуль; ``None``, если файла нет или он повреждён."""
    try:
        with open(path, "rb") as handle:
            return parse_xm(handle.read())
    except (OSError, XmFormatError) as exc:
        logger.debug(f"Музыка: модуль {path} не загружен: {exc}")
        return None


class ModuleMusicPlayer(QObject):
    """Бесконечное воспроизведение модуля XM с измерением текущего уровня."""

    def __init__(self, module: XmModule, parent: QObject | None = None,
                 volume: float = 0.45) -> None:
        super().__init__(parent)
        self._module = module
        self._volume = volume
        self._renderer: XmRenderer | None = None
        self._sink = None
        self._io = None
        self._format_kind = ""
        self._channels = 2
        self._rate = 44100
        self._frame_bytes = 4
        self._failed = False
        self._written = 0
        self._levels: deque[tuple[int, float]] = deque()
        self._level = 0.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FEED_INTERVAL_MS)
        self._timer.timeout.connect(self._feed)

    @property
    def is_playing(self) -> bool:
        return self._timer.isActive()

    def start(self) -> bool:
        """Начать или продолжить воспроизведение с места остановки."""
        if self.is_playing:
            return True
        if self._failed or not self._ensure_sink():
            return False
        try:
            self._io = self._sink.start()
        except Exception as exc:  # noqa: BLE001 - звук не должен ронять окно
            logger.debug(f"Музыка: не удалось открыть вывод: {exc}")
            self._io = None
        if self._io is None:
            self._failed = True
            return False
        self._written = 0
        self._levels.clear()
        self._feed()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Музыка: ошибка остановки вывода: {exc}")
        self._io = None
        self._levels.clear()
        self._level = 0.0

    def level(self) -> float:
        """Уровень звучащего сейчас фрагмента, 0..1."""
        if not self.is_playing or self._sink is None:
            return 0.0
        played = int(self._sink.processedUSecs() * self._rate / 1_000_000)
        while self._levels and self._levels[0][0] <= played:
            self._level = self._levels.popleft()[1]
        return self._level

    # ── Устройство ──

    def _ensure_sink(self) -> bool:
        if self._sink is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except ImportError as exc:
            logger.debug(f"Музыка: нет QtMultimedia: {exc}")
            self._failed = True
            return False
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            logger.debug("Музыка: нет устройства вывода звука")
            self._failed = True
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        kinds = {
            QAudioFormat.SampleFormat.Int16: ("<i2", 2),
            QAudioFormat.SampleFormat.Int32: ("<i4", 4),
            QAudioFormat.SampleFormat.Float: ("<f4", 4),
        }
        kind = kinds.get(fmt.sampleFormat())
        if kind is None or fmt.channelCount() < 1 or fmt.sampleRate() < 8000:
            logger.debug(
                f"Музыка: неподдерживаемый формат вывода {fmt.sampleFormat()}, "
                f"{fmt.channelCount()} кан., {fmt.sampleRate()} Гц"
            )
            self._failed = True
            return False
        self._format_kind = kind[0]
        self._channels = fmt.channelCount()
        self._rate = fmt.sampleRate()
        self._frame_bytes = kind[1] * self._channels
        self._renderer = XmRenderer(self._module, self._rate)
        sink = QAudioSink(device, fmt, self)
        sink.setBufferSize(int(self._rate * BUFFER_SEC) * self._frame_bytes)
        sink.setVolume(self._volume)
        self._sink = sink
        return True

    # ── Подача данных ──

    def _encode(self, audio: np.ndarray) -> bytes:
        if self._channels == 1:
            audio = audio.mean(axis=1, keepdims=True)
        elif self._channels > 2:
            wide = np.zeros((len(audio), self._channels), dtype=np.float32)
            wide[:, :2] = audio
            audio = wide
        if self._format_kind == "<f4":
            return audio.astype("<f4").tobytes()
        if self._format_kind == "<i4":
            return (audio * 2147483520.0).astype("<i4").tobytes()
        return (audio * 32767.0).astype("<i2").tobytes()

    def _remember_levels(self, audio: np.ndarray) -> None:
        for start in range(0, len(audio), LEVEL_BLOCK):
            block = audio[start : start + LEVEL_BLOCK]
            rms = float(np.sqrt(np.mean(block * block)))
            self._levels.append(
                (
                    self._written + start + len(block),
                    max(0.0, min(1.0, (rms - LEVEL_FLOOR) / LEVEL_RANGE)),
                )
            )
        self._written += len(audio)

    def _feed(self) -> None:
        if self._io is None or self._sink is None or self._renderer is None:
            return
        frames = self._sink.bytesFree() // self._frame_bytes
        frames = min(frames, int(self._rate * BUFFER_SEC))
        if frames < 64:
            return
        try:
            audio = self._renderer.render(frames)
            self._remember_levels(audio)
            self._io.write(self._encode(audio))
        except Exception as exc:  # noqa: BLE001 - сбой синтеза выключает музыку
            logger.debug(f"Музыка: ошибка синтеза: {exc}")
            self._failed = True
            self.stop()
