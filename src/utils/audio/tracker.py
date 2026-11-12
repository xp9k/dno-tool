"""Проигрыватель модулей FastTracker 2 (XM) без Qt.

Разбор файла и синтез звука на numpy. FFmpeg в сборке МОС собран без
libopenmpt, поэтому модуль из окна «О программе» играется здесь.

Поддерживается формат XM 1.04: 8/16-битные сэмплы, петли вперёд и
«туда-обратно», огибающие громкости и панорамы с сустейном и петлёй,
затухание, автовибрато инструмента, линейные и амигийские периоды,
колонка громкости и эффекты 0-F, G, H, K, L, P, R, X. Тремор (T) и
глиссандо не воспроизводятся. Порядок арпеджио и ограничения громкости
повторяют FastTracker 2.

Синтез идёт по тикам: в начале тика пересчитываются эффекты, затем тик
смешивается с линейной интерполяцией. Громкость голоса меняется плавно в
пределах тика, а прерванная нота затухает за ``FADE_FRAMES`` отсчётов:
без этого на смене нот слышны щелчки.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

import numpy as np

XM_SIGNATURE = b"Extended Module: "
XM_VERSION = 0x0104
MAX_CHANNELS = 64
MAX_PATTERNS = 256
MAX_ROWS = 256
MAX_INSTRUMENTS = 128
MAX_SAMPLES = 16
NOTE_OFF = 97
FADE_FRAMES = 256

# Половина синусоиды FastTracker 2 (вибрато, тремоло).
_VIB_TABLE = (
    0, 24, 49, 74, 97, 120, 141, 161, 180, 197, 212, 224, 235, 244, 250, 253,
    255, 253, 250, 244, 235, 224, 212, 197, 180, 161, 141, 120, 97, 74, 49, 24,
)

# Изменение громкости при многократном перезапуске (Rxy), по значению x.
_RETRIG_VOLUME = (
    lambda v: v, lambda v: v - 1, lambda v: v - 2, lambda v: v - 4,
    lambda v: v - 8, lambda v: v - 16, lambda v: v * 2 // 3, lambda v: v // 2,
    lambda v: v, lambda v: v + 1, lambda v: v + 2, lambda v: v + 4,
    lambda v: v + 8, lambda v: v + 16, lambda v: v * 3 // 2, lambda v: v * 2,
)

# Номера эффектов XM (буквенные идут после F).
FX_GLOBAL_VOLUME = 16  # G
FX_GLOBAL_SLIDE = 17  # H
FX_KEY_OFF = 20  # K
FX_ENVELOPE_POS = 21  # L
FX_PAN_SLIDE = 25  # P
FX_MULTI_RETRIG = 27  # R
FX_EXTRA_FINE = 33  # X


class XmFormatError(ValueError):
    """Файл не является модулем XM 1.04 или повреждён."""


@dataclass(frozen=True)
class Envelope:
    """Огибающая: точки (тик, значение 0..64), сустейн и петля по индексам точек."""

    points: tuple[tuple[int, int], ...] = ()
    enabled: bool = False
    sustain: int | None = None
    loop: tuple[int, int] | None = None

    def value(self, tick: int) -> float:
        pts = self.points
        if tick <= pts[0][0]:
            return float(pts[0][1])
        for index in range(1, len(pts)):
            x1, y1 = pts[index]
            if tick <= x1:
                x0, y0 = pts[index - 1]
                if x1 == x0:
                    return float(y1)
                return y0 + (y1 - y0) * (tick - x0) / (x1 - x0)
        return float(pts[-1][1])

    def advance(self, tick: int, key_on: bool) -> int:
        """Следующий тик огибающей: стоит на сустейне, пока нота не отпущена."""
        if key_on and self.sustain is not None and tick == self.points[self.sustain][0]:
            return tick
        tick += 1
        if self.loop is not None:
            start, end = self.loop
            if tick >= self.points[end][0]:
                tick = self.points[start][0]
        return tick


@dataclass
class Sample:
    """Сэмпл в float32; петля «туда-обратно» развёрнута в обычную.

    ``data`` длиннее ``length`` на один защитный отсчёт для интерполяции.
    ``loop_length == 0`` означает сэмпл без петли.
    """

    name: str
    data: np.ndarray
    length: int
    loop_start: int
    loop_length: int
    volume: int
    finetune: int
    panning: int
    relative_note: int


@dataclass
class Instrument:
    name: str
    keymap: bytes
    samples: list[Sample]
    volume_envelope: Envelope
    panning_envelope: Envelope
    vibrato_type: int = 0
    vibrato_sweep: int = 0
    vibrato_depth: int = 0
    vibrato_rate: int = 0
    fadeout: int = 0


@dataclass
class Pattern:
    rows: int
    cells: bytes  # rows * channels * 5: нота, инструмент, громкость, эффект, параметр


@dataclass
class XmModule:
    title: str
    tracker: str
    linear: bool
    channels: int
    orders: tuple[int, ...]
    restart: int
    speed: int
    bpm: int
    patterns: list[Pattern]
    instruments: list[Instrument]
    sample_names: list[str] = field(default_factory=list)

    def credits(self) -> list[str]:
        """Строки авторства из файла.

        Название модуля, затем непустые имена инструментов и сэмплов (по
        традиции трекеров там пишут автора, год и приветы) и программа, в
        которой модуль сделан. Повторы названия пропускаются.
        """
        result: list[str] = []
        seen: list[str] = []

        def add(text: str) -> None:
            text = text.strip()
            key = "".join(ch for ch in text.lower() if ch.isalnum())
            if not key or any(key in other or other in key for other in seen):
                return
            seen.append(key)
            result.append(text)

        add(self.title)
        for instrument in self.instruments:
            add(instrument.name)
        for name in self.sample_names:
            add(name)
        add(self.tracker)
        return result


def _text(raw: bytes) -> str:
    text = raw.split(b"\0", 1)[0].decode("cp437", errors="replace")
    return "".join(ch if ch.isprintable() else " " for ch in text).strip()


def _need(data: bytes, offset: int, size: int) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise XmFormatError("Файл модуля обрезан")


def _unpack_pattern(raw: bytes, rows: int, channels: int) -> bytes:
    total = rows * channels
    cells = bytearray(total * 5)
    q = 0
    size = len(raw)
    for index in range(total):
        if q >= size:
            break
        base = index * 5
        head = raw[q]
        q += 1
        if head & 0x80:
            for k in range(5):
                if head & (1 << k):
                    if q >= size:
                        raise XmFormatError("Повреждён шаблон модуля")
                    cells[base + k] = raw[q]
                    q += 1
        else:
            if q + 4 > size:
                raise XmFormatError("Повреждён шаблон модуля")
            cells[base] = head
            cells[base + 1 : base + 5] = raw[q : q + 4]
            q += 4
    return bytes(cells)


def _envelope(header: bytes, points_at: int, count: int, flags: int,
              sustain: int, loop_start: int, loop_end: int) -> Envelope:
    count = min(count, 12)
    if not (flags & 1) or count < 1:
        return Envelope()
    pts = []
    last_x = 0
    for index in range(count):
        x, y = struct.unpack_from("<HH", header, points_at + index * 4)
        last_x = max(last_x, x)
        pts.append((last_x, min(y, 64)))
    sus = sustain if flags & 2 and sustain < count else None
    loop = None
    if flags & 4 and loop_start <= loop_end < count:
        loop = (loop_start, loop_end)
    return Envelope(tuple(pts), True, sus, loop)


def _decode_sample(raw: bytes, sixteen: bool) -> np.ndarray:
    if sixteen:
        deltas = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.int64)
        values = (np.cumsum(deltas) & 0xFFFF).astype(np.uint16).view(np.int16)
        return values.astype(np.float32) / 32768.0
    deltas = np.frombuffer(raw, dtype=np.int8).astype(np.int64)
    values = (np.cumsum(deltas) & 0xFF).astype(np.uint8).view(np.int8)
    return values.astype(np.float32) / 128.0


def _build_sample(name: str, pcm: np.ndarray, loop_start: int, loop_length: int,
                  loop_type: int, volume: int, finetune: int, panning: int,
                  relative_note: int) -> Sample:
    size = len(pcm)
    if loop_type == 0 or loop_length <= 0 or loop_start >= size:
        loop_start, loop_length = 0, 0
    else:
        loop_length = min(loop_length, size - loop_start)
    if loop_length:
        loop_end = loop_start + loop_length
        if loop_type == 2:
            body = np.concatenate((pcm[:loop_end], pcm[loop_start:loop_end][::-1]))
            loop_length *= 2
        else:
            body = pcm[:loop_end]
        guard = body[loop_start]
    else:
        body = pcm
        guard = np.float32(0.0)
    data = np.concatenate((body, np.array([guard], dtype=np.float32))).astype(np.float32)
    return Sample(
        name=name,
        data=data,
        length=len(body),
        loop_start=loop_start,
        loop_length=loop_length,
        volume=min(volume, 64),
        finetune=finetune,
        panning=panning,
        relative_note=relative_note,
    )


def parse_xm(data: bytes) -> XmModule:
    """Разобрать модуль XM 1.04. Повреждённый файл даёт ``XmFormatError``."""
    data = bytes(data)
    if len(data) < 80 or not data.startswith(XM_SIGNATURE):
        raise XmFormatError("Это не модуль XM")
    title = _text(data[17:37])
    tracker = _text(data[38:58])
    (version,) = struct.unpack_from("<H", data, 58)
    if version != XM_VERSION:
        raise XmFormatError(f"Неподдерживаемая версия XM: {version:#06x}")
    (header_size,) = struct.unpack_from("<I", data, 60)
    song_length, restart, channels, pattern_count, instrument_count, flags, speed, bpm = (
        struct.unpack_from("<8H", data, 64)
    )
    if not 1 <= channels <= MAX_CHANNELS:
        raise XmFormatError(f"Недопустимое число каналов: {channels}")
    if pattern_count > MAX_PATTERNS or instrument_count > MAX_INSTRUMENTS:
        raise XmFormatError("Слишком много шаблонов или инструментов")
    song_length = min(song_length, 256)
    if song_length < 1:
        raise XmFormatError("Пустой порядок шаблонов")
    _need(data, 80, song_length)
    orders = tuple(data[80 : 80 + song_length])

    offset = 60 + header_size
    patterns: list[Pattern] = []
    for _ in range(pattern_count):
        _need(data, offset, 9)
        packed_header, _packing, rows, packed_size = struct.unpack_from("<IBHH", data, offset)
        body = offset + packed_header
        _need(data, body, packed_size)
        if not 1 <= rows <= MAX_ROWS:
            raise XmFormatError(f"Недопустимое число строк шаблона: {rows}")
        if packed_size:
            cells = _unpack_pattern(data[body : body + packed_size], rows, channels)
        else:
            cells = bytes(rows * channels * 5)
        patterns.append(Pattern(rows, cells))
        offset = body + packed_size

    instruments: list[Instrument] = []
    sample_names: list[str] = []
    for _ in range(instrument_count):
        _need(data, offset, 4)
        (inst_size,) = struct.unpack_from("<I", data, offset)
        if inst_size < 4:
            raise XmFormatError("Повреждён заголовок инструмента")
        raw_header = data[offset : offset + inst_size]
        header = raw_header.ljust(max(263, inst_size), b"\0")
        name = _text(header[4:26])
        (sample_count,) = struct.unpack_from("<H", header, 27)
        offset += inst_size
        if sample_count > MAX_SAMPLES:
            raise XmFormatError(f"Слишком много сэмплов в инструменте: {sample_count}")
        if sample_count == 0:
            instruments.append(Instrument(name, bytes(96), [], Envelope(), Envelope()))
            continue
        (sample_header_size,) = struct.unpack_from("<I", header, 29)
        keymap = bytes(header[33:129])
        vol_env = _envelope(header, 129, header[225], header[233],
                            header[227], header[228], header[229])
        pan_env = _envelope(header, 177, header[226], header[234],
                            header[230], header[231], header[232])
        (fadeout,) = struct.unpack_from("<H", header, 239)

        headers = []
        for _ in range(sample_count):
            _need(data, offset, min(sample_header_size, 40))
            sh = data[offset : offset + sample_header_size].ljust(40, b"\0")
            length, loop_start, loop_length = struct.unpack_from("<3I", sh, 0)
            volume, finetune, sample_type, panning, relative_note = struct.unpack_from(
                "<BbBBb", sh, 12
            )
            headers.append((length, loop_start, loop_length, volume, finetune,
                            sample_type, panning, relative_note, _text(sh[18:40])))
            offset += sample_header_size

        samples: list[Sample] = []
        for (length, loop_start, loop_length, volume, finetune, sample_type,
             panning, relative_note, sample_name) in headers:
            # Обрезанный конец файла не роняет разбор: недостающие данные - тишина.
            raw = data[offset : offset + length]
            offset += length
            sixteen = bool(sample_type & 0x10)
            pcm = _decode_sample(raw, sixteen)
            unit = 2 if sixteen else 1
            samples.append(_build_sample(
                sample_name, pcm, loop_start // unit, loop_length // unit,
                sample_type & 3, volume, finetune, panning, relative_note,
            ))
            sample_names.append(sample_name)
        instruments.append(Instrument(
            name=name,
            keymap=keymap,
            samples=samples,
            volume_envelope=vol_env,
            panning_envelope=pan_env,
            vibrato_type=header[235],
            vibrato_sweep=header[236],
            vibrato_depth=header[237],
            vibrato_rate=header[238],
            fadeout=fadeout,
        ))

    return XmModule(
        title=title,
        tracker=tracker,
        linear=bool(flags & 1),
        channels=channels,
        orders=orders,
        restart=restart,
        speed=max(1, min(speed or 6, 31)),
        bpm=max(32, min(bpm or 125, 255)),
        patterns=patterns,
        instruments=instruments,
        sample_names=sample_names,
    )


class _Channel:
    """Состояние канала: нота, громкость, эффекты и их память."""

    __slots__ = (
        "instrument", "sample", "period", "target_period", "porta_speed",
        "volume", "panning", "out_period", "out_volume", "active", "pos",
        "gain_l", "gain_r", "fades", "key_on", "fade_volume", "vol_env_tick",
        "pan_env_tick", "autovib_pos", "autovib_ticks", "vib_pos", "vib_speed",
        "vib_depth", "vib_wave", "trem_pos", "trem_speed", "trem_depth",
        "trem_wave", "effect", "param", "volcmd", "delayed", "loop_row",
        "loop_count", "retrig_count", "mem_vol_slide", "mem_porta_up",
        "mem_porta_down", "mem_fine_up", "mem_fine_down", "mem_xfine_up",
        "mem_xfine_down", "mem_offset", "mem_pan_slide", "mem_global_slide",
        "mem_fine_vol_up", "mem_fine_vol_down", "mem_retrig",
    )

    def __init__(self) -> None:
        self.instrument: Instrument | None = None
        self.sample: Sample | None = None
        self.period = 0.0
        self.target_period = 0.0
        self.porta_speed = 0
        self.volume = 0
        self.panning = 128
        self.out_period = 0.0
        self.out_volume = 0
        self.active = False
        self.pos = 0.0
        self.gain_l = 0.0
        self.gain_r = 0.0
        self.fades: list[tuple[Sample, float, float, float, float]] = []
        self.key_on = False
        self.fade_volume = 32768
        self.vol_env_tick = 0
        self.pan_env_tick = 0
        self.autovib_pos = 0
        self.autovib_ticks = 0
        self.vib_pos = 0
        self.vib_speed = 0
        self.vib_depth = 0
        self.vib_wave = 0
        self.trem_pos = 0
        self.trem_speed = 0
        self.trem_depth = 0
        self.trem_wave = 0
        self.effect = 0
        self.param = 0
        self.volcmd = 0
        self.delayed: tuple[int, int, int, int, int] | None = None
        self.loop_row = 0
        self.loop_count = 0
        self.retrig_count = 0
        self.mem_vol_slide = 0
        self.mem_porta_up = 0
        self.mem_porta_down = 0
        self.mem_fine_up = 0
        self.mem_fine_down = 0
        self.mem_xfine_up = 0
        self.mem_xfine_down = 0
        self.mem_offset = 0
        self.mem_pan_slide = 0
        self.mem_global_slide = 0
        self.mem_fine_vol_up = 0
        self.mem_fine_vol_down = 0
        self.mem_retrig = 0


def _wave(position: int, wave: int) -> int:
    """Значение формы волны вибрато/тремоло 0..255 для позиции 0..255."""
    index = (position >> 2) & 31
    wave &= 3
    if wave == 0:
        return _VIB_TABLE[index]
    if wave == 1:
        value = index << 3
        return 255 - value if position & 0x80 else value
    return 255


def _mix_voice(out: np.ndarray, sample: Sample, pos: float, step: float,
               gl0: float, gr0: float, gl1: float, gr1: float) -> float | None:
    """Добавить голос в ``out`` с плавной сменой громкости; вернуть новую позицию.

    ``None`` значит, что сэмпл без петли закончился.
    """
    frames = len(out)
    if sample.length <= 0 or step <= 0.0 or frames == 0:
        return None
    ramp = np.arange(frames, dtype=np.float64)
    p = pos + step * ramp
    end_pos = pos + step * frames
    count = frames
    if sample.loop_length:
        start = sample.loop_start
        loop = sample.loop_length
        loop_end = start + loop
        if end_pos >= loop_end or pos >= loop_end:
            p = np.where(p >= loop_end, start + np.mod(p - start, loop), p)
            if end_pos >= loop_end:
                end_pos = start + math.fmod(end_pos - start, loop)
    else:
        if pos >= sample.length:
            return None
        if end_pos > sample.length:
            count = int(np.searchsorted(p, sample.length))
            p = p[:count]
            end_pos = None
    index = p.astype(np.intp)
    np.clip(index, 0, sample.length - 1, out=index)
    frac = (p - index).astype(np.float32)
    data = sample.data
    left = data[index]
    signal = left + (data[index + 1] - left) * frac
    t = (ramp[:count] / frames).astype(np.float32)
    out[:count, 0] += signal * (gl0 + (gl1 - gl0) * t)
    out[:count, 1] += signal * (gr0 + (gr1 - gr0) * t)
    return end_pos


class XmRenderer:
    """Синтез модуля в стерео float32. Песня повторяется бесконечно.

    Вызовы ``render`` не потокобезопасны: один рендерер - один поток.
    """

    def __init__(self, module: XmModule, sample_rate: int = 44100,
                 amplification: float = 0.35) -> None:
        if sample_rate < 8000:
            raise ValueError("Слишком низкая частота дискретизации")
        self.module = module
        self.sample_rate = int(sample_rate)
        self.amplification = float(amplification)
        self.loops = 0  # сколько раз песня доиграла до конца
        self._channels = [_Channel() for _ in range(module.channels)]
        self._speed = module.speed
        self._bpm = module.bpm
        self._global_volume = 64
        self._tick = 0
        self._row = 0
        self._order = 0
        self._pattern = self._pattern_at(0)
        self._pattern_delay = 0
        self._delay_pass = 0
        self._jump_order: int | None = None
        self._break_row: int | None = None
        self._loop_jump: int | None = None
        self._frame_carry = 0.0
        self._pending = np.zeros((0, 2), dtype=np.float32)

    @property
    def position(self) -> tuple[int, int]:
        """Текущая позиция: (номер в порядке шаблонов, строка)."""
        return self._order, self._row

    def render(self, frames: int) -> np.ndarray:
        """Следующие ``frames`` стереокадров, значения в пределах [-1, 1]."""
        frames = max(0, int(frames))
        chunks = [self._pending]
        have = len(self._pending)
        while have < frames:
            chunk = self._render_tick()
            chunks.append(chunk)
            have += len(chunk)
        joined = np.concatenate(chunks) if len(chunks) > 1 else self._pending
        self._pending = joined[frames:]
        result = joined[:frames] * self.amplification
        np.clip(result, -1.0, 1.0, out=result)
        return result

    # ── Порядок шаблонов ──

    def _pattern_at(self, order: int) -> Pattern:
        index = self.module.orders[order]
        if index < len(self.module.patterns):
            return self.module.patterns[index]
        return Pattern(64, bytes(64 * self.module.channels * 5))

    def _set_order(self, order: int) -> None:
        orders = self.module.orders
        if order >= len(orders):
            self.loops += 1
            order = self.module.restart if self.module.restart < len(orders) else 0
        self._order = order
        self._pattern = self._pattern_at(order)
        for channel in self._channels:
            channel.loop_row = 0
            channel.loop_count = 0

    def _advance_row(self) -> None:
        if self._loop_jump is not None:
            self._row = self._loop_jump
        elif self._jump_order is not None or self._break_row is not None:
            order = self._jump_order if self._jump_order is not None else self._order + 1
            if order <= self._order and self._jump_order is not None:
                self.loops += 1  # переход назад: песня зациклена эффектом B
            self._set_order(order)
            row = self._break_row or 0
            self._row = row if row < self._pattern.rows else 0
        else:
            self._row += 1
            if self._row >= self._pattern.rows:
                self._set_order(self._order + 1)
                self._row = 0
        self._jump_order = None
        self._break_row = None
        self._loop_jump = None

    # ── Тик ──

    def _render_tick(self) -> np.ndarray:
        for channel in self._channels:
            channel.out_period = channel.period
            channel.out_volume = channel.volume
        if self._tick == 0 and self._delay_pass == 0:
            self._read_row()
        else:
            for channel in self._channels:
                self._tick_effects(channel, self._tick)

        self._frame_carry += self.sample_rate * 2.5 / self._bpm
        frames = int(self._frame_carry)
        self._frame_carry -= frames
        out = np.zeros((frames, 2), dtype=np.float32)
        for channel in self._channels:
            self._mix_channel(channel, out)

        self._tick += 1
        if self._tick >= self._speed:
            self._tick = 0
            if self._pattern_delay > 0:
                self._pattern_delay -= 1
                self._delay_pass += 1
            else:
                self._delay_pass = 0
                self._advance_row()
        return out

    def _read_row(self) -> None:
        cells = self._pattern.cells
        base = self._row * self.module.channels * 5
        for index, channel in enumerate(self._channels):
            at = base + index * 5
            note, inst, vol, effect, param = cells[at : at + 5]
            channel.effect = effect
            channel.param = param
            channel.volcmd = vol
            channel.delayed = None
            if effect == 0xE and param >> 4 == 0xD and param & 15:
                channel.delayed = (note, inst, vol, effect, param)
                continue
            self._trigger(channel, note, inst, vol, effect, param)
            self._row_effects(channel, vol, effect, param)

    # ── Ноты ──

    def _note_period(self, note: int, finetune: int) -> float:
        if self.module.linear:
            return 7680.0 - note * 64.0 - finetune / 2.0
        return 1712.0 * 2.0 ** ((48 - note - finetune / 128.0) / 12.0)

    def _frequency(self, period: float) -> float:
        period = max(1.0, period)
        if self.module.linear:
            return 8363.0 * 2.0 ** ((4608.0 - period) / 768.0)
        return 8363.0 * 1712.0 / period

    def _start_fade(self, channel: _Channel) -> None:
        if channel.active and channel.sample is not None and (channel.gain_l or channel.gain_r):
            step = self._frequency(channel.out_period) / self.sample_rate
            channel.fades.append(
                (channel.sample, channel.pos, step, channel.gain_l, channel.gain_r)
            )
        channel.gain_l = channel.gain_r = 0.0

    def _restart_sample(self, channel: _Channel, offset: int = 0) -> None:
        self._start_fade(channel)
        sample = channel.sample
        if sample is None or offset >= sample.length:
            channel.active = False
            return
        channel.pos = float(offset)
        channel.active = True

    def _key_off(self, channel: _Channel) -> None:
        channel.key_on = False
        instrument = channel.instrument
        if instrument is None or not instrument.volume_envelope.enabled:
            channel.volume = 0
            channel.out_volume = 0

    def _trigger(self, channel: _Channel, note: int, inst: int, vol: int,
                 effect: int, param: int) -> None:
        porta = effect in (3, 5) or vol >> 4 == 0xF
        instruments = self.module.instruments
        if inst:
            channel.instrument = instruments[inst - 1] if inst <= len(instruments) else None
        if note == NOTE_OFF:
            self._key_off(channel)
            return
        if effect == 9 and param:
            channel.mem_offset = param
        instrument = channel.instrument
        if 1 <= note <= 96 and instrument is not None:
            index = instrument.keymap[note - 1]
            sample = instrument.samples[index] if index < len(instrument.samples) else None
            if sample is None:
                if not porta:
                    self._start_fade(channel)
                    channel.active = False
            else:
                real = note - 1 + sample.relative_note
                if 0 <= real < 119:
                    period = self._note_period(real, sample.finetune)
                    if porta and channel.active and channel.sample is not None:
                        channel.target_period = period
                    else:
                        channel.sample = sample
                        channel.period = channel.out_period = period
                        channel.target_period = period
                        offset = channel.mem_offset * 256 if effect == 9 else 0
                        self._restart_sample(channel, offset)
                        if not channel.vib_wave & 4:
                            channel.vib_pos = 0
                        if not channel.trem_wave & 4:
                            channel.trem_pos = 0
                        channel.retrig_count = 0
        if inst and instrument is not None and channel.sample is not None:
            channel.volume = channel.out_volume = channel.sample.volume
            channel.panning = channel.sample.panning
            channel.key_on = True
            channel.fade_volume = 32768
            channel.vol_env_tick = 0
            channel.pan_env_tick = 0
            channel.autovib_pos = 0
            channel.autovib_ticks = 0

    # ── Эффекты на первом тике строки ──

    def _row_effects(self, channel: _Channel, vol: int, effect: int, param: int) -> None:
        command, value = vol >> 4, vol & 15
        if 0x10 <= vol <= 0x50:
            channel.volume = channel.out_volume = vol - 0x10
        elif command == 0x8:
            channel.volume = channel.out_volume = max(0, channel.volume - value)
        elif command == 0x9:
            channel.volume = channel.out_volume = min(64, channel.volume + value)
        elif command == 0xA:
            channel.vib_speed = value * 4
        elif command == 0xB and value:
            channel.vib_depth = value
        elif command == 0xC:
            channel.panning = value * 16
        elif command == 0xF and value:
            channel.porta_speed = value * 64

        hi, lo = param >> 4, param & 15
        if effect == 1 and param:
            channel.mem_porta_up = param
        elif effect == 2 and param:
            channel.mem_porta_down = param
        elif effect == 3 and param:
            channel.porta_speed = param * 4
        elif effect == 4:
            if hi:
                channel.vib_speed = hi * 4
            if lo:
                channel.vib_depth = lo
        elif effect in (5, 6, 0xA) and param:
            channel.mem_vol_slide = param
        elif effect == 7:
            if hi:
                channel.trem_speed = hi * 4
            if lo:
                channel.trem_depth = lo
        elif effect == 8:
            channel.panning = param
        elif effect == 0xB:
            self._jump_order = param
            if self._break_row is None:
                self._break_row = 0
        elif effect == 0xC:
            channel.volume = channel.out_volume = min(param, 64)
        elif effect == 0xD:
            self._break_row = hi * 10 + lo
        elif effect == 0xE:
            self._extended_row_effect(channel, hi, lo)
        elif effect == 0xF and param:
            if param < 32:
                self._speed = param
            else:
                self._bpm = param
        elif effect == FX_GLOBAL_VOLUME:
            self._global_volume = min(param, 64)
        elif effect == FX_GLOBAL_SLIDE and param:
            channel.mem_global_slide = param
        elif effect == FX_KEY_OFF and param == 0:
            self._key_off(channel)
        elif effect == FX_ENVELOPE_POS:
            channel.vol_env_tick = param
            channel.pan_env_tick = param
        elif effect == FX_PAN_SLIDE and param:
            channel.mem_pan_slide = param
        elif effect == FX_MULTI_RETRIG:
            if hi or lo:
                old = channel.mem_retrig
                channel.mem_retrig = ((hi or old >> 4) << 4) | (lo or old & 15)
        elif effect == FX_EXTRA_FINE:
            if hi == 1:
                if lo:
                    channel.mem_xfine_up = lo
                channel.period = channel.out_period = max(1.0, channel.period - channel.mem_xfine_up)
            elif hi == 2:
                if lo:
                    channel.mem_xfine_down = lo
                channel.period = channel.out_period = min(32000.0, channel.period + channel.mem_xfine_down)

    def _extended_row_effect(self, channel: _Channel, hi: int, lo: int) -> None:
        if hi == 1:
            if lo:
                channel.mem_fine_up = lo
            channel.period = channel.out_period = max(1.0, channel.period - channel.mem_fine_up * 4)
        elif hi == 2:
            if lo:
                channel.mem_fine_down = lo
            channel.period = channel.out_period = min(32000.0, channel.period + channel.mem_fine_down * 4)
        elif hi == 4:
            channel.vib_wave = lo
        elif hi == 6:
            if lo == 0:
                channel.loop_row = self._row
            elif channel.loop_count == 0:
                channel.loop_count = lo
                self._loop_jump = channel.loop_row
            else:
                channel.loop_count -= 1
                if channel.loop_count:
                    self._loop_jump = channel.loop_row
        elif hi == 7:
            channel.trem_wave = lo
        elif hi == 0xA:
            if lo:
                channel.mem_fine_vol_up = lo
            channel.volume = channel.out_volume = min(64, channel.volume + channel.mem_fine_vol_up)
        elif hi == 0xB:
            if lo:
                channel.mem_fine_vol_down = lo
            channel.volume = channel.out_volume = max(0, channel.volume - channel.mem_fine_vol_down)
        elif hi == 0xC and lo == 0:
            channel.volume = channel.out_volume = 0
        elif hi == 0xE and self._delay_pass == 0 and self._pattern_delay == 0:
            self._pattern_delay = lo

    # ── Эффекты на остальных тиках ──

    def _volume_slide(self, channel: _Channel) -> None:
        param = channel.mem_vol_slide
        if param >> 4:
            channel.volume = min(64, channel.volume + (param >> 4))
        else:
            channel.volume = max(0, channel.volume - (param & 15))
        channel.out_volume = channel.volume

    def _tone_porta(self, channel: _Channel) -> None:
        target = channel.target_period
        if channel.period < target:
            channel.period = min(target, channel.period + channel.porta_speed)
        elif channel.period > target:
            channel.period = max(target, channel.period - channel.porta_speed)
        channel.out_period = channel.period

    def _vibrato(self, channel: _Channel) -> None:
        delta = (_wave(channel.vib_pos, channel.vib_wave) * channel.vib_depth) >> 5
        if channel.vib_pos & 0x80:
            delta = -delta
        channel.out_period = channel.period + delta
        channel.vib_pos = (channel.vib_pos + channel.vib_speed) & 255

    def _tick_effects(self, channel: _Channel, tick: int) -> None:
        if channel.delayed is not None:
            note, inst, vol, effect, param = channel.delayed
            if tick == param & 15:
                channel.delayed = None
                self._trigger(channel, note, inst, vol, effect, param)
                self._row_effects(channel, vol, effect, param)
            return

        vol = channel.volcmd
        command, value = vol >> 4, vol & 15
        if command == 0x6:
            channel.volume = channel.out_volume = max(0, channel.volume - value)
        elif command == 0x7:
            channel.volume = channel.out_volume = min(64, channel.volume + value)
        elif command == 0xB:
            self._vibrato(channel)
        elif command == 0xD:
            channel.panning = max(0, channel.panning - value)
        elif command == 0xE:
            channel.panning = min(255, channel.panning + value)
        elif command == 0xF:
            self._tone_porta(channel)

        effect, param = channel.effect, channel.param
        hi, lo = param >> 4, param & 15
        if effect == 0 and param:
            # Порядок FastTracker 2: счётчик тиков идёт вниз от скорости.
            phase = (self._speed - tick % self._speed) % 3
            if phase:
                shift = hi if phase == 1 else lo
                if self.module.linear:
                    channel.out_period = channel.period - shift * 64
                else:
                    channel.out_period = channel.period * 2.0 ** (-shift / 12.0)
        elif effect == 1:
            channel.period = channel.out_period = max(1.0, channel.period - channel.mem_porta_up * 4)
        elif effect == 2:
            channel.period = channel.out_period = min(32000.0, channel.period + channel.mem_porta_down * 4)
        elif effect == 3:
            self._tone_porta(channel)
        elif effect == 4:
            self._vibrato(channel)
        elif effect == 5:
            self._tone_porta(channel)
            self._volume_slide(channel)
        elif effect == 6:
            self._vibrato(channel)
            self._volume_slide(channel)
        elif effect == 7:
            delta = (_wave(channel.trem_pos, channel.trem_wave) * channel.trem_depth) >> 6
            if channel.trem_pos & 0x80:
                delta = -delta
            channel.out_volume = max(0, min(64, channel.volume + delta))
            channel.trem_pos = (channel.trem_pos + channel.trem_speed) & 255
        elif effect == 0xA:
            self._volume_slide(channel)
        elif effect == 0xE:
            if hi == 9 and lo and tick % lo == 0:
                self._restart_sample(channel)
            elif hi == 0xC and tick == lo:
                channel.volume = channel.out_volume = 0
        elif effect == FX_GLOBAL_SLIDE:
            slide = channel.mem_global_slide
            if slide >> 4:
                self._global_volume = min(64, self._global_volume + (slide >> 4))
            else:
                self._global_volume = max(0, self._global_volume - (slide & 15))
        elif effect == FX_KEY_OFF and tick == param:
            self._key_off(channel)
        elif effect == FX_PAN_SLIDE:
            slide = channel.mem_pan_slide
            if slide >> 4:
                channel.panning = min(255, channel.panning + (slide >> 4))
            else:
                channel.panning = max(0, channel.panning - (slide & 15))
        elif effect == FX_MULTI_RETRIG:
            interval = channel.mem_retrig & 15
            if interval:
                channel.retrig_count += 1
                if channel.retrig_count >= interval:
                    channel.retrig_count = 0
                    change = _RETRIG_VOLUME[channel.mem_retrig >> 4]
                    channel.volume = channel.out_volume = max(0, min(64, change(channel.volume)))
                    self._restart_sample(channel)

    # ── Смешивание ──

    def _mix_channel(self, channel: _Channel, out: np.ndarray) -> None:
        if channel.fades:
            span = out[: min(len(out), FADE_FRAMES)]
            for sample, pos, step, gl, gr in channel.fades:
                _mix_voice(span, sample, pos, step, gl, gr, 0.0, 0.0)
            channel.fades.clear()

        instrument = channel.instrument
        envelope_volume = 1.0
        panning = channel.panning
        period = channel.out_period
        if instrument is not None:
            env = instrument.volume_envelope
            if env.enabled:
                envelope_volume = env.value(channel.vol_env_tick) / 64.0
                channel.vol_env_tick = env.advance(channel.vol_env_tick, channel.key_on)
            if not channel.key_on:
                channel.fade_volume = max(0, channel.fade_volume - instrument.fadeout)
            env = instrument.panning_envelope
            if env.enabled:
                swing = env.value(channel.pan_env_tick) - 32.0
                panning = panning + swing * (128 - abs(panning - 128)) / 32.0
                channel.pan_env_tick = env.advance(channel.pan_env_tick, channel.key_on)
            if instrument.vibrato_depth and instrument.vibrato_rate:
                period += self._auto_vibrato(channel, instrument)

        if not channel.active or channel.sample is None:
            return
        level = (
            channel.out_volume / 64.0
            * envelope_volume
            * channel.fade_volume / 32768.0
            * self._global_volume / 64.0
        )
        panning = max(0.0, min(255.0, panning))
        target_l = level * math.sqrt((256.0 - panning) / 256.0)
        target_r = level * math.sqrt(panning / 256.0)
        step = self._frequency(period) / self.sample_rate
        position = _mix_voice(out, channel.sample, channel.pos, step,
                              channel.gain_l, channel.gain_r, target_l, target_r)
        if position is None:
            channel.active = False
            channel.gain_l = channel.gain_r = 0.0
        else:
            channel.pos = position
            channel.gain_l, channel.gain_r = target_l, target_r

    @staticmethod
    def _auto_vibrato(channel: _Channel, instrument: Instrument) -> float:
        depth = float(instrument.vibrato_depth)
        if instrument.vibrato_sweep and channel.key_on:
            depth *= min(1.0, channel.autovib_ticks / instrument.vibrato_sweep)
        channel.autovib_ticks += 1
        phase = channel.autovib_pos / 256.0
        kind = instrument.vibrato_type & 3
        if kind == 0:
            wave = math.sin(2.0 * math.pi * phase)
        elif kind == 1:
            wave = 1.0 if phase < 0.5 else -1.0
        elif kind == 2:
            wave = 1.0 - 2.0 * phase
        else:
            wave = 2.0 * phase - 1.0
        channel.autovib_pos = (channel.autovib_pos + instrument.vibrato_rate) & 255
        return wave * depth
