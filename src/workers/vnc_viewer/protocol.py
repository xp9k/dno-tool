"""Интерактивный клиент RFB для окна VNC, без Qt.

Модуль собирает сообщения клиента (ввод, буфер обмена, запросы кадров) и
разбирает поток сервера на события. Транспортом владеет рабочий поток окна.

Кодирования: Raw, CopyRect, Zlib, Tight (заливка, JPEG, палитра, градиент) и
псевдокодирования DesktopSize, ExtendedDesktopSize (раскладка мониторов),
RichCursor, PointerPos, LastRect, уровни сжатия и качества JPEG, Extended
Clipboard (текст UTF-8) и уменьшенный кадр от kmsvnc для «живого» клиента
(расширение миниатюр версии ≥ 3). Классический обмен текстом
(ServerCutText/ClientCutText) остаётся для серверов без Extended Clipboard.

Вход: None и классический VNC Auth (первые 8 байт пароля UTF-8). Транспорт не
шифруется, как и у остальных VNC-клиентов программы.
"""

from __future__ import annotations

import socket
import struct
import time
import zlib
from collections.abc import Generator
from dataclasses import dataclass

from src.services.thumbnails.rfb import (
    THUMBNAIL_LIVE_ENCODING,
    THUMBNAIL_LIVE_VERSION,
    THUMBNAIL_MESSAGE,
    vnc_response,
)
from src.utils.network.dns_resolver import DnsBusyError, DnsResolver, DnsTimeoutError

# Кодирования прямоугольников.
ENC_RAW = 0
ENC_COPYRECT = 1
ENC_ZLIB = 6
ENC_TIGHT = 7
# Псевдокодирования.
ENC_DESKTOP_SIZE = -223
ENC_LAST_RECT = -224
ENC_POINTER_POS = -232
ENC_RICH_CURSOR = -239
ENC_EXT_DESKTOP_SIZE = -308
ENC_EXT_CLIPBOARD = -1063131698  # 0xC0A1E5CE как знаковое 32-битное
ENC_COMPRESS_LEVEL_0 = -256
ENC_QUALITY_LEVEL_0 = -32
# «Живой» клиент kmsvnc (не псевдокодировка миниатюр: с ней старый kmsvnc
# снимал бы окно раз в секунду).
ENC_SERVER_SCALING = THUMBNAIL_LIVE_ENCODING
SERVER_SCALING_VERSION = THUMBNAIL_LIVE_VERSION

# Tight: старшие биты управляющего байта.
TIGHT_FILL = 0x08
TIGHT_JPEG = 0x09
TIGHT_FILTER_COPY = 0
TIGHT_FILTER_PALETTE = 1
TIGHT_FILTER_GRADIENT = 2
TIGHT_MAX_WIDTH = 2048
TIGHT_MIN_TO_COMPRESS = 12

# Кодек, который клиент предпочитает (первым в SetEncodings).
CODEC_TIGHT = "tight"
CODEC_ZLIB = "zlib"
CODEC_RAW = "raw"
CODECS = (CODEC_TIGHT, CODEC_ZLIB, CODEC_RAW)

# Флаги Extended Clipboard.
CB_TEXT = 1 << 0
CB_FORMAT_MASK = 0xFFFF
CB_CAPS = 1 << 24
CB_REQUEST = 1 << 25
CB_PEEK = 1 << 26
CB_NOTIFY = 1 << 27
CB_PROVIDE = 1 << 28

# Защитные пределы разбора: сервер не должен заставить держать лишнюю память.
MAX_SCREEN_PIXELS = 64_000_000
MAX_TEXT_BYTES = 1_048_576
MAX_CLIPBOARD_BYTES = 4 * 1_048_576
MAX_RECTANGLES = 65_535
MAX_CURSOR_SIDE = 256
MAX_SCREENS = 64
MAX_TIGHT_LENGTH = 1 << 22  # предел компактной длины Tight (3 байта по 7 бит)

_RESOLVER = DnsResolver("vnc-viewer-dns", workers=1, queue_size=16)


class RfbViewerError(Exception):
    """Ошибка подключения или протокола; текст безопасен для показа."""


class RfbAuthError(RfbViewerError):
    """Сервер требует пароль или отклонил его."""

    def __init__(self, message: str, *, password_missing: bool = False) -> None:
        super().__init__(message)
        self.password_missing = password_missing


@dataclass(frozen=True)
class PixelFormat:
    """Формат пикселей, который клиент просит у сервера (всегда true colour)."""

    bits: int
    depth: int
    red_max: int
    green_max: int
    blue_max: int
    red_shift: int
    green_shift: int
    blue_shift: int

    @property
    def bytes_per_pixel(self) -> int:
        return self.bits // 8

    def pack(self) -> bytes:
        return struct.pack(
            ">BBBBHHHBBB3x",
            self.bits,
            self.depth,
            0,  # little-endian: совпадает с раскладкой QImage на x86/ARM
            1,
            self.red_max,
            self.green_max,
            self.blue_max,
            self.red_shift,
            self.green_shift,
            self.blue_shift,
        )


# BGRX в памяти = QImage.Format_RGB32; RGB565 = QImage.Format_RGB16.
PIXEL_FORMAT_32 = PixelFormat(32, 24, 255, 255, 255, 16, 8, 0)
PIXEL_FORMAT_16 = PixelFormat(16, 16, 31, 63, 31, 11, 5, 0)


@dataclass(frozen=True)
class ServerInit:
    width: int
    height: int
    name: str


# ---------- События сервера ----------


@dataclass(frozen=True)
class Resize:
    width: int
    height: int


@dataclass(frozen=True)
class Screen:
    """Монитор удалённого рабочего стола в координатах кадра."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Screens:
    screens: tuple[Screen, ...]


@dataclass(frozen=True)
class RectData:
    x: int
    y: int
    width: int
    height: int
    data: bytes


@dataclass(frozen=True)
class FillRect:
    """Tight: область одного цвета (0xRRGGBB)."""

    x: int
    y: int
    width: int
    height: int
    color: int


@dataclass(frozen=True)
class JpegRect:
    """Tight: область в JPEG; декодирует рабочий поток (Qt)."""

    x: int
    y: int
    width: int
    height: int
    data: bytes


@dataclass(frozen=True)
class RgbRect:
    """Tight: пиксели RGB888 построчно (без выравнивания строк)."""

    x: int
    y: int
    width: int
    height: int
    data: bytes


@dataclass(frozen=True)
class PaletteRect:
    """Tight: индексы палитры; bits=1 — строки по байтам, старший бит слева."""

    x: int
    y: int
    width: int
    height: int
    bits: int
    data: bytes
    colors: tuple[int, ...]


@dataclass(frozen=True)
class ScalingInfo:
    """Подтверждение расширения миниатюр kmsvnc: версия и полный размер экрана."""

    version: int
    max_divisor: int
    width: int
    height: int


@dataclass(frozen=True)
class CopyRect:
    x: int
    y: int
    width: int
    height: int
    src_x: int
    src_y: int


@dataclass(frozen=True)
class CursorShape:
    hot_x: int
    hot_y: int
    width: int
    height: int
    pixels: bytes
    mask: bytes


@dataclass(frozen=True)
class PointerPos:
    x: int
    y: int


@dataclass(frozen=True)
class UpdateDone:
    pass


@dataclass(frozen=True)
class Bell:
    pass


@dataclass(frozen=True)
class ClipboardText:
    """Текст буфера обмена сервера (классический или из Extended Clipboard)."""

    text: str


@dataclass(frozen=True)
class ExtClipboard:
    """Служебное сообщение Extended Clipboard: обрабатывает ExtClipboardState."""

    flags: int
    payload: bytes


# ---------- Сообщения клиента ----------


def msg_set_pixel_format(pixel_format: PixelFormat) -> bytes:
    return b"\x00\x00\x00\x00" + pixel_format.pack()


def msg_set_encodings(encodings: list[int]) -> bytes:
    return struct.pack(">BBH", 2, 0, len(encodings)) + b"".join(
        struct.pack(">i", value) for value in encodings
    )


def msg_update_request(incremental: bool, x: int, y: int, w: int, h: int) -> bytes:
    return struct.pack(">BBHHHH", 3, int(incremental), x, y, w, h)


def msg_server_scaling(width: int, height: int) -> bytes:
    """Размер кадра для «живого» клиента kmsvnc (0×0 — полный размер).

    Отправлять только после ScalingInfo с версией ≥ SERVER_SCALING_VERSION:
    чужой сервер на неизвестное сообщение закрывает соединение.
    """
    width = min(max(int(width), 0), 0xFFFF)
    height = min(max(int(height), 0), 0xFFFF)
    return struct.pack(">BBHH", THUMBNAIL_MESSAGE, 0, width, height)


def msg_key(keysym: int, down: bool) -> bytes:
    return struct.pack(">BBxxI", 4, int(down), keysym & 0xFFFFFFFF)


def msg_pointer(mask: int, x: int, y: int) -> bytes:
    return struct.pack(">BBHH", 5, mask & 0xFF, max(0, x) & 0xFFFF, max(0, y) & 0xFFFF)


def msg_client_cut_text(text: str) -> bytes:
    """Классический ClientCutText: Latin-1, иначе UTF-8 (так понимают многие серверы)."""
    normalized = text.replace("\r\n", "\n")
    try:
        data = normalized.encode("latin-1")
    except UnicodeEncodeError:
        data = normalized.encode("utf-8")
    data = data[:MAX_TEXT_BYTES]
    return struct.pack(">BxxxI", 6, len(data)) + data


def msg_ext_clipboard(flags: int, payload: bytes = b"") -> bytes:
    body = struct.pack(">I", flags) + payload
    return struct.pack(">Bxxxi", 6, -len(body)) + body


def ext_clipboard_caps() -> bytes:
    """Возможности клиента: только текст, все действия."""
    flags = CB_CAPS | CB_TEXT | CB_REQUEST | CB_PEEK | CB_NOTIFY | CB_PROVIDE
    return msg_ext_clipboard(flags, struct.pack(">I", MAX_CLIPBOARD_BYTES))


def ext_clipboard_provide(text: str) -> bytes:
    """Provide: zlib-поток с длиной и текстом UTF-8 (CRLF, завершающий ноль)."""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    data = data[: MAX_CLIPBOARD_BYTES - 1] + b"\x00"
    return msg_ext_clipboard(
        CB_PROVIDE | CB_TEXT, zlib.compress(struct.pack(">I", len(data)) + data)
    )


def decode_cut_text(data: bytes) -> str:
    """Классический ServerCutText: UTF-8, если корректен, иначе Latin-1."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.replace("\r\n", "\n").rstrip("\x00")


def _format_sizes(flags: int, payload: bytes) -> dict[int, int]:
    """Размеры форматов из сообщения caps: по одному u32 на каждый бит формата."""
    sizes: dict[int, int] = {}
    offset = 0
    for bit in range(16):
        if not flags & (1 << bit):
            continue
        if offset + 4 > len(payload):
            break
        sizes[1 << bit] = struct.unpack(">I", payload[offset : offset + 4])[0]
        offset += 4
    return sizes


def parse_ext_provide(flags: int, payload: bytes) -> str | None:
    """Текст из provide; None, если текста нет или данные повреждены."""
    if not flags & CB_TEXT:
        return None
    try:
        decompressor = zlib.decompressobj()
        data = decompressor.decompress(payload, MAX_CLIPBOARD_BYTES + 64)
    except zlib.error:
        return None
    offset = 0
    for bit in range(16):
        fmt = 1 << bit
        if not flags & fmt:
            continue
        if offset + 4 > len(data):
            return None
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        if offset + size > len(data):
            return None
        if fmt == CB_TEXT:
            raw = data[offset : offset + size].split(b"\x00", 1)[0]
            return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        offset += size
    return None


class ExtClipboardState:
    """Обмен Extended Clipboard: что сервер умеет и что отвечать на его сообщения."""

    def __init__(self) -> None:
        self.enabled = False
        self.server_flags = 0
        self.server_sizes: dict[int, int] = {}
        self.local_text = ""

    def on_message(self, message: ExtClipboard) -> tuple[list[bytes], str | None]:
        """Вернуть (ответы серверу, полученный текст или None)."""
        flags = message.flags
        replies: list[bytes] = []
        if flags & CB_CAPS:
            self.enabled = True
            self.server_flags = flags
            self.server_sizes = _format_sizes(flags, message.payload)
            replies.append(ext_clipboard_caps())
            return replies, None
        if not self.enabled:
            return replies, None
        if flags & CB_REQUEST and flags & CB_TEXT:
            replies.append(ext_clipboard_provide(self.local_text))
        if flags & CB_PEEK:
            replies.append(msg_ext_clipboard(CB_NOTIFY | (CB_TEXT if self.local_text else 0)))
        if flags & CB_NOTIFY and flags & CB_TEXT:
            replies.append(msg_ext_clipboard(CB_REQUEST | CB_TEXT))
        if flags & CB_PROVIDE:
            return replies, parse_ext_provide(flags, message.payload)
        return replies, None

    def announce(self, text: str) -> list[bytes]:
        """Сообщения о новом локальном тексте (notify или сразу provide)."""
        self.local_text = text
        if self.server_flags & CB_NOTIFY:
            return [msg_ext_clipboard(CB_NOTIFY | CB_TEXT)]
        if self.server_flags & CB_PROVIDE:
            limit = self.server_sizes.get(CB_TEXT, 0)
            if not limit or len(text.encode("utf-8")) < limit:
                return [ext_clipboard_provide(text)]
        return []


# ---------- Подключение ----------


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            data = sock.recv(size - len(chunks))
        except TimeoutError as error:
            raise RfbViewerError("Сервер VNC не отвечает") from error
        if not data:
            raise RfbViewerError("Сервер VNC закрыл соединение")
        chunks.extend(data)
    return bytes(chunks)


def _read_reason(sock: socket.socket) -> str:
    size = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if size > MAX_TEXT_BYTES:
        raise RfbViewerError("Сервер VNC прислал слишком длинное сообщение")
    text = _recv_exact(sock, size).decode("utf-8", errors="replace")
    # Текст чужой: только печатные символы и без лишней длины.
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:200]


def open_socket(host: str, port: int, timeout: float) -> socket.socket:
    """TCP-соединение с ограниченным временем DNS и подключения."""
    deadline = time.monotonic() + timeout
    try:
        addresses = _RESOLVER.resolve(host, port, deadline)
    except (DnsBusyError, DnsTimeoutError) as error:
        raise RfbViewerError(f"Не удалось разрешить имя {host}") from error
    except OSError as error:
        raise RfbViewerError(f"Не удалось разрешить имя {host}: {error}") from error
    last_error: OSError | None = None
    for family, kind, proto, _, endpoint in addresses[:4]:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(remaining)
            sock.connect(endpoint)
        except OSError as error:
            sock.close()
            last_error = error
            continue
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        return sock
    if isinstance(last_error, ConnectionRefusedError):
        raise RfbViewerError(
            f"Порт {port} на {host} закрыт: VNC-сервер не запущен"
        ) from last_error
    if last_error is None or isinstance(last_error, TimeoutError):
        raise RfbViewerError(f"{host}:{port} не отвечает") from last_error
    raise RfbViewerError(f"Нет соединения с {host}:{port}: {last_error}") from last_error


def handshake(sock: socket.socket, password: str) -> ServerInit:
    """Версия, вход, ClientInit (общий доступ) и ServerInit."""
    banner = _recv_exact(sock, 12)
    if banner[:8] != b"RFB 003." or banner[-1:] != b"\n" or not banner[8:11].isdigit():
        raise RfbViewerError("Это не VNC-сервер (неизвестный ответ на порту)")
    minor = int(banner[8:11])
    version = 8 if minor >= 8 else 7 if minor >= 7 else 3
    sock.sendall(f"RFB 003.00{version}\n".encode())
    if version == 3:
        security = struct.unpack(">I", _recv_exact(sock, 4))[0]
        if security == 0:
            raise RfbViewerError(f"Сервер VNC отказал: {_read_reason(sock) or 'без причины'}")
    else:
        count = _recv_exact(sock, 1)[0]
        if not count:
            raise RfbViewerError(f"Сервер VNC отказал: {_read_reason(sock) or 'без причины'}")
        offered = _recv_exact(sock, count)
        if 1 in offered:
            security = 1
        elif 2 in offered:
            security = 2
        else:
            names = ", ".join(str(value) for value in offered)
            raise RfbViewerError(
                f"Сервер VNC требует неподдерживаемый способ входа (типы {names})"
            )
        if security == 2 and not password:
            raise RfbAuthError("Сервер VNC требует пароль", password_missing=True)
        sock.sendall(bytes([security]))
    if security not in (1, 2):
        raise RfbViewerError(f"Неподдерживаемый способ входа VNC ({security})")
    if security == 2:
        if not password:
            raise RfbAuthError("Сервер VNC требует пароль", password_missing=True)
        challenge = _recv_exact(sock, 16)
        sock.sendall(vnc_response(challenge, password))
    if security == 2 or version == 8:
        result = struct.unpack(">I", _recv_exact(sock, 4))[0]
        if result:
            reason = _read_reason(sock) if version == 8 else ""
            message = "Неверный пароль VNC"
            if reason:
                message += f" ({reason})"
            raise RfbAuthError(message)
    sock.sendall(b"\x01")  # общий доступ: не отключать других клиентов
    init = _recv_exact(sock, 24)
    width, height = struct.unpack(">HH", init[:4])
    name_size = struct.unpack(">I", init[20:24])[0]
    if name_size > MAX_TEXT_BYTES:
        raise RfbViewerError("Сервер VNC прислал слишком длинное имя")
    name = _recv_exact(sock, name_size).decode("utf-8", errors="replace") if name_size else ""
    if not width or not height or width * height > MAX_SCREEN_PIXELS:
        raise RfbViewerError(f"Недопустимый размер экрана {width}×{height}")
    return ServerInit(width, height, "".join(ch for ch in name if ch.isprintable())[:200])


def default_encodings(
    compress_level: int = 6,
    *,
    cursor_shape: bool = True,
    codec: str = CODEC_TIGHT,
    quality: int | None = 6,
    server_scaling: bool = False,
) -> list[int]:
    """Кодирования клиента.

    cursor_shape: форма курсора отдельно (рисует окно, без задержки сети). Без
    неё сервер рисует курсор в кадре: так в режиме просмотра виден курсор хоста.
    codec: предпочитаемое кодирование — Tight (с запасным Zlib), Zlib или Raw.
    quality: уровень JPEG 0–9 для Tight; None — Tight без потерь (без JPEG).
    server_scaling: объявить «живого» клиента kmsvnc; чужой сервер и kmsvnc
    до 0.0.6-12 псевдокодировку игнорируют, новый отвечает подтверждением
    (ScalingInfo).
    """
    level = min(max(int(compress_level), 0), 9)
    tight = codec == CODEC_TIGHT
    encodings = [ENC_COPYRECT]
    if tight:
        encodings += [ENC_TIGHT, ENC_ZLIB]
    elif codec == CODEC_ZLIB:
        encodings.append(ENC_ZLIB)
    encodings += [ENC_RAW, ENC_EXT_DESKTOP_SIZE, ENC_DESKTOP_SIZE]
    if cursor_shape:
        encodings += [ENC_RICH_CURSOR, ENC_POINTER_POS]
    encodings += [ENC_LAST_RECT, ENC_EXT_CLIPBOARD, ENC_COMPRESS_LEVEL_0 + level]
    if tight and quality is not None:
        encodings.append(ENC_QUALITY_LEVEL_0 + min(max(int(quality), 0), 9))
    if server_scaling:
        encodings.append(ENC_SERVER_SCALING)
    return encodings


# ---------- Разбор потока сервера ----------


class RfbStreamParser:
    """Разбор сообщений сервера порциями из сокета.

    Генератор приостанавливается на границах полей, поэтому неполное сообщение
    TCP дожидается следующей порции без потери данных.
    """

    def __init__(self, width: int, height: int, pixel_format: PixelFormat) -> None:
        self.width = width
        self.height = height
        self._bpp = pixel_format.bytes_per_pixel
        self._format = pixel_format
        # TPIXEL Tight: 3 байта RGB только для 32 бит глубины 24 с 8 битами на канал.
        self._tpixel = (
            pixel_format.bits == 32
            and pixel_format.depth == 24
            and (pixel_format.red_max, pixel_format.green_max, pixel_format.blue_max)
            == (255, 255, 255)
        )
        self._zlib = zlib.decompressobj()
        self._tight_streams: list = [None, None, None, None]
        self._buffer = bytearray()
        self._events: list[object] = []
        self._gen = self._messages()
        self._needed = next(self._gen)

    def feed(self, data: bytes) -> list[object]:
        self._buffer.extend(data)
        while len(self._buffer) >= self._needed:
            size = self._needed
            chunk = bytes(memoryview(self._buffer)[:size])
            del self._buffer[:size]
            self._needed = self._gen.send(chunk)
        events, self._events = self._events, []
        return events

    def _skip(self, size: int) -> Generator[int, bytes, None]:
        while size > 0:
            chunk = min(size, 65_536)
            yield chunk
            size -= chunk

    def _resize(self, width: int, height: int) -> None:
        if not width or not height or width * height > MAX_SCREEN_PIXELS:
            raise RfbViewerError(f"Недопустимый размер экрана {width}×{height}")
        self.width, self.height = width, height
        self._events.append(Resize(width, height))

    def _check_bounds(self, x: int, y: int, w: int, h: int) -> None:
        if x + w > self.width or y + h > self.height:
            raise RfbViewerError("Сервер VNC прислал область за пределами экрана")

    def _messages(self) -> Generator[int, bytes, None]:
        while True:
            message_type = (yield 1)[0]
            if message_type == 0:
                yield from self._update()
            elif message_type == 1:  # SetColourMapEntries: true colour, пропускаем
                header = yield 5
                count = struct.unpack(">H", header[3:5])[0]
                yield from self._skip(count * 6)
            elif message_type == 2:
                self._events.append(Bell())
            elif message_type == 3:
                header = yield 7
                length = struct.unpack(">i", header[3:7])[0]
                if length < 0:
                    size = -length
                    if size < 4 or size > MAX_CLIPBOARD_BYTES + 64:
                        raise RfbViewerError("Недопустимое сообщение буфера обмена")
                    body = yield size
                    flags = struct.unpack(">I", body[:4])[0]
                    self._events.append(ExtClipboard(flags, body[4:]))
                elif length > MAX_CLIPBOARD_BYTES:
                    yield from self._skip(length)
                elif length:
                    data = yield length
                    self._events.append(ClipboardText(decode_cut_text(data)))
            else:
                raise RfbViewerError(f"Неизвестное сообщение сервера VNC ({message_type})")

    def _update(self) -> Generator[int, bytes, None]:
        header = yield 3
        count = struct.unpack(">H", header[1:3])[0]
        bpp = self._bpp
        pixel_rects = 0
        info_rects = 0
        for _ in range(count):
            x, y, w, h, encoding = struct.unpack(">HHHHi", (yield 12))
            if encoding == ENC_LAST_RECT:
                break
            if encoding == ENC_SERVER_SCALING:
                # Подтверждение kmsvnc: x — версия, y — наибольший делитель.
                self._events.append(ScalingInfo(x, y, w, h))
                info_rects += 1
                continue
            pixel_rects += 1
            if encoding == ENC_TIGHT:
                yield from self._tight(x, y, w, h)
                continue
            if encoding == ENC_RAW:
                if not w or not h:
                    continue
                self._check_bounds(x, y, w, h)
                # yield не внутри append: feed подменяет список событий на паузе.
                data = yield w * h * bpp
                self._events.append(RectData(x, y, w, h, data))
            elif encoding == ENC_COPYRECT:
                src_x, src_y = struct.unpack(">HH", (yield 4))
                if not w or not h:
                    continue
                self._check_bounds(x, y, w, h)
                self._check_bounds(src_x, src_y, w, h)
                self._events.append(CopyRect(x, y, w, h, src_x, src_y))
            elif encoding == ENC_ZLIB:
                length = struct.unpack(">I", (yield 4))[0]
                # Границы до распаковки: иначе размер области задаёт сервер.
                self._check_bounds(x, y, w, h)
                expected = w * h * bpp
                if length > expected + 65_536 + 1024:
                    raise RfbViewerError("Недопустимый размер сжатых данных")
                compressed = (yield length) if length else b""
                try:
                    data = self._zlib.decompress(compressed, expected + 1) if expected else b""
                except zlib.error as error:
                    raise RfbViewerError("Повреждённые сжатые данные VNC") from error
                if len(data) != expected:
                    raise RfbViewerError("Повреждённые сжатые данные VNC")
                if expected:
                    self._events.append(RectData(x, y, w, h, data))
            elif encoding == ENC_DESKTOP_SIZE:
                self._resize(w, h)
            elif encoding == ENC_EXT_DESKTOP_SIZE:
                head = yield 4
                number = head[0]
                if number > MAX_SCREENS:
                    raise RfbViewerError("Слишком много мониторов в раскладке")
                screens = []
                for _ in range(number):
                    _, sx, sy, sw, sh, _ = struct.unpack(">IHHHHI", (yield 16))
                    screens.append(Screen(sx, sy, sw, sh))
                if y == 0:  # статус 0: раскладка действует
                    if (w, h) != (self.width, self.height):
                        self._resize(w, h)
                    self._events.append(Screens(tuple(screens)))
            elif encoding == ENC_RICH_CURSOR:
                if w > MAX_CURSOR_SIDE or h > MAX_CURSOR_SIDE:
                    raise RfbViewerError("Слишком большой курсор")
                pixels = (yield w * h * bpp) if w and h else b""
                mask = (yield ((w + 7) // 8) * h) if w and h else b""
                self._events.append(CursorShape(x, y, w, h, pixels, mask))
            elif encoding == ENC_POINTER_POS:
                self._events.append(PointerPos(x, y))
            else:
                raise RfbViewerError(f"Сервер VNC прислал неподдерживаемое кодирование ({encoding})")
        # Обновление только с подтверждением расширения kmsvnc пишет вне
        # очереди: на запрос клиента оно не отвечает.
        if info_rects and not pixel_rects:
            return
        self._events.append(UpdateDone())

    # ---------- Tight ----------

    def _compact_length(self) -> Generator[int, bytes, int]:
        value = 0
        for shift in (0, 7, 14):
            byte = (yield 1)[0]
            if shift == 14:
                value |= byte << 14
                break
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        if value > MAX_TIGHT_LENGTH:
            raise RfbViewerError("Недопустимая длина данных Tight")
        return value

    def _tight_pixel_size(self) -> int:
        return 3 if self._tpixel else self._bpp

    def _rgb(self, pixel: bytes) -> int:
        """Цвет 0xRRGGBB из TPIXEL или пикселя в формате клиента."""
        if self._tpixel:
            return (pixel[0] << 16) | (pixel[1] << 8) | pixel[2]
        fmt = self._format
        value = int.from_bytes(pixel, "little")
        r = ((value >> fmt.red_shift) & fmt.red_max) * 255 // fmt.red_max
        g = ((value >> fmt.green_shift) & fmt.green_max) * 255 // fmt.green_max
        b = ((value >> fmt.blue_shift) & fmt.blue_max) * 255 // fmt.blue_max
        return (r << 16) | (g << 8) | b

    def _tight(self, x: int, y: int, w: int, h: int) -> Generator[int, bytes, None]:
        self._check_bounds(x, y, w, h)
        control = (yield 1)[0]
        for stream in range(4):
            if control & (1 << stream):
                self._tight_streams[stream] = None
        subtype = control >> 4
        if subtype == TIGHT_FILL:
            pixel = yield self._tight_pixel_size()
            if w and h:
                self._events.append(FillRect(x, y, w, h, self._rgb(pixel)))
            return
        if subtype == TIGHT_JPEG:
            length = yield from self._compact_length()
            if length > w * h * 4 + 65_536:
                raise RfbViewerError("Недопустимый размер JPEG")
            data = (yield length) if length else b""
            if w and h:
                self._events.append(JpegRect(x, y, w, h, data))
            return
        if subtype & 0x08:
            raise RfbViewerError(f"Неподдерживаемый вид сжатия Tight ({subtype})")
        if w > TIGHT_MAX_WIDTH:
            raise RfbViewerError("Слишком широкая область Tight")
        stream = subtype & 0x03
        pixel_size = self._tight_pixel_size()
        filter_id = TIGHT_FILTER_COPY
        if subtype & 0x04:
            filter_id = (yield 1)[0]
        colors: tuple[int, ...] = ()
        bits = 8
        if filter_id == TIGHT_FILTER_PALETTE:
            count = (yield 1)[0] + 1
            raw = yield count * pixel_size
            colors = tuple(
                self._rgb(raw[i * pixel_size : (i + 1) * pixel_size]) for i in range(count)
            )
            bits = 1 if count == 2 else 8
            row_bytes = (w + 7) // 8 if bits == 1 else w
        elif filter_id in (TIGHT_FILTER_COPY, TIGHT_FILTER_GRADIENT):
            row_bytes = w * pixel_size
        else:
            raise RfbViewerError(f"Неизвестный фильтр Tight ({filter_id})")
        expected = row_bytes * h
        if expected < TIGHT_MIN_TO_COMPRESS:
            data = (yield expected) if expected else b""
        else:
            length = yield from self._compact_length()
            if length > expected + expected // 100 + 1024:
                raise RfbViewerError("Недопустимый размер сжатых данных")
            compressed = (yield length) if length else b""
            decompressor = self._tight_streams[stream]
            if decompressor is None:
                decompressor = self._tight_streams[stream] = zlib.decompressobj()
            try:
                data = decompressor.decompress(compressed, expected + 1)
            except zlib.error as error:
                raise RfbViewerError("Повреждённые сжатые данные Tight") from error
            if len(data) != expected or decompressor.unconsumed_tail:
                raise RfbViewerError("Повреждённые сжатые данные Tight")
        if not w or not h:
            return
        if filter_id == TIGHT_FILTER_PALETTE:
            self._events.append(PaletteRect(x, y, w, h, bits, data, colors))
            return
        if filter_id == TIGHT_FILTER_GRADIENT:
            data = self._ungradient(data, w, h)
        if self._tpixel:
            self._events.append(RgbRect(x, y, w, h, data))
        else:
            self._events.append(RectData(x, y, w, h, data))

    def _ungradient(self, data: bytes, w: int, h: int) -> bytes:
        """Обратный градиентный фильтр Tight (медленный путь: с JPEG серверы его не шлют)."""
        if self._tpixel:
            out = bytearray(data)
            stride = w * 3
            for row in range(h):
                base = row * stride
                for col in range(w):
                    index = base + col * 3
                    for c in range(3):
                        left = out[index - 3 + c] if col else 0
                        up = out[index - stride + c] if row else 0
                        corner = out[index - stride - 3 + c] if row and col else 0
                        predicted = min(max(left + up - corner, 0), 255)
                        out[index + c] = (predicted + data[index + c]) & 0xFF
            return bytes(out)
        fmt = self._format
        size = self._bpp
        channels = (
            (fmt.red_shift, fmt.red_max),
            (fmt.green_shift, fmt.green_max),
            (fmt.blue_shift, fmt.blue_max),
        )
        values = [int.from_bytes(data[i : i + size], "little") for i in range(0, len(data), size)]
        result = [0] * len(values)
        for row in range(h):
            for col in range(w):
                index = row * w + col
                pixel = 0
                for shift, maximum in channels:
                    left = (result[index - 1] >> shift) & maximum if col else 0
                    up = (result[index - w] >> shift) & maximum if row else 0
                    corner = (result[index - w - 1] >> shift) & maximum if row and col else 0
                    predicted = min(max(left + up - corner, 0), maximum)
                    delta = (values[index] >> shift) & maximum
                    pixel |= ((predicted + delta) & maximum) << shift
                result[index] = pixel
        return b"".join(value.to_bytes(size, "little") for value in result)
