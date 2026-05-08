"""
Remote Recording Dialog — Диалог удалённой записи видео.

Улучшенная версия с:
- Визуальным статусом сервера
- Разделёнными зонами статуса
- Кнопкой «Копировать URL»
- Валидацией путей и SSH-проверкой
- Пресетами качества
- Цветным логом
- Auto-detect бинарников из PATH
- Неблокирующим запуском плеера
- Сохранением полной сессии
- Валидацией битрейта
- Подтверждением закрытия при стриме
- Кэшем результатов сканирования
"""

import html as html_module
import os
import re
import shlex
import shutil
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import (
    Qt, QProcess, Signal, QThread, QEvent, QTimer, QRegularExpression,
)
from PySide6.QtGui import QCloseEvent, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QTextEdit, QTabWidget, QGroupBox,
    QFileDialog, QMessageBox, QApplication, QSizePolicy,
)

from src.domain.models import DeviceModel
from src.workers.ffmpeg_stream_manager import FFmpegStreamManager
from src.workers.gstreamer_stream_manager import GStreamerStreamManager


# ---------------------------------------------------------------------------
# Профили качества
# ---------------------------------------------------------------------------
_PROFILES = {
    0: {
        "name": "Минимальная задержка",
        "codec": "mpeg2video",
        "resolution": "1280x720",
        "fps": 15,
        "bitrate": "1M",
    },
    1: {
        "name": "Баланс",
        "codec": "libx264",
        "resolution": "1280x720",
        "fps": 25,
        "bitrate": "2M",
    },
    2: {
        "name": "Максимальное качество",
        "codec": "libx264",
        "resolution": "1920x1080",
        "fps": 30,
        "bitrate": "4M",
    },
}


# ---------------------------------------------------------------------------
# Кэш сканирования (TTL 5 мин)
# ---------------------------------------------------------------------------
_scan_cache_video: dict[str, tuple[float, list]] = {}
_scan_cache_audio: dict[str, tuple[float, list]] = {}


def _get_cached_scan(host: str, cache: dict) -> Optional[list]:
    if host not in cache:
        return None
    ts, results = cache[host]
    if time.time() - ts > 300:
        del cache[host]
        return None
    return results


def _set_cached_scan(host: str, results: list, cache: dict) -> None:
    cache[host] = (time.time(), results)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def _find_in_path(name: str) -> Optional[str]:
    path = shutil.which(name)
    if path:
        return path
    if os.name == "nt" and not name.endswith(".exe"):
        path = shutil.which(name + ".exe")
        if path:
            return path
    return None


def _validate_binary(text: str) -> bool:
    return bool(_find_in_path(text.strip()))


# ---------------------------------------------------------------------------
# Поток проверки SSH
# ---------------------------------------------------------------------------
class SSHCheckThread(QThread):
    result = Signal(bool, str)

    def __init__(self, device: DeviceModel) -> None:
        super().__init__()
        self.device = device

    def run(self) -> None:
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            t0 = time.time()
            client = worker.get_client(self.device)
            stdin, stdout, stderr = client.exec_command("echo OK")
            exit_status = stdout.channel.recv_exit_status()
            stdout.read().decode("utf-8", errors="ignore").strip()
            client.close()
            latency = (time.time() - t0) * 1000
            if exit_status == 0:
                self.result.emit(True, f"SSH OK ({latency:.0f} ms)")
            else:
                err = stderr.read().decode("utf-8", errors="ignore").strip()
                self.result.emit(False, f"SSH ошибка (код {exit_status}): {err}")
        except Exception as e:
            self.result.emit(False, f"SSH ошибка: {e}")


# ---------------------------------------------------------------------------
# Поток сканирования видео и аудио
# ---------------------------------------------------------------------------
class ScanThread(QThread):
    result = Signal(list, list, str)

    def __init__(self, device: DeviceModel):
        super().__init__()
        self.device = device

    def run(self) -> None:
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device)

            # --- Видео ---
            video_cmd = (
                'for dev in /dev/video*; do '
                '[ -c "$dev" ] || continue; '
                'echo "DEVICE:$dev"; '
                'if command -v v4l2-ctl >/dev/null 2>&1; then '
                'v4l2-ctl --device="$dev" --list-formats-ext 2>&1 '
                '| grep -E "Size:|Interval:|^\\[" | sed "s/^[[:space:]]*//"; '
                'else '
                'ffmpeg -f v4l2 -list_formats all -i "$dev" 2>&1 '
                '| grep -iE "support" || true; '
                'fi; '
                'echo "END_DEVICE"; '
                'done 2>/dev/null'
            )

            stdin, stdout, stderr = client.exec_command(video_cmd)
            video_output = stdout.read().decode("utf-8", errors="ignore").strip()
            video_err = stderr.read().decode("utf-8", errors="ignore").strip()
            if not video_output and video_err:
                video_output = video_err
            video_devices = ScanThread._parse_output(video_output)

            # --- Аудио ---
            audio_cmd = (
                'if command -v pactl >/dev/null 2>&1; then '
                '  pactl list short sources 2>/dev/null; '
                'fi; '
                'if command -v arecord >/dev/null 2>&1; then '
                '  arecord -l 2>/dev/null; '
                'fi'
            )

            stdin2, stdout2, stderr2 = client.exec_command(audio_cmd)
            audio_output = stdout2.read().decode("utf-8", errors="ignore").strip()
            audio_err = stderr2.read().decode("utf-8", errors="ignore").strip()
            if not audio_output and audio_err:
                audio_output = audio_err
            audio_devices = ScanThread._parse_audio_output(audio_output)

            client.close()
            self.result.emit(video_devices, audio_devices, "")
        except Exception as e:
            self.result.emit([], [], str(e))

    @staticmethod
    def _parse_output(output: str) -> list:
        # Старый парсер видеоустройств
        devices = []
        current_device: Optional[str] = None
        current_size: Optional[str] = None
        current_capabilities: dict[str, list] = {}
        current_format: Optional[str] = None
        current_mjpeg_caps: dict[str, list] = {}

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("DEVICE:"):
                current_device = line[7:]
                current_capabilities = {}
                current_mjpeg_caps = {}
                current_size = None
                current_format = None
            elif line == "END_DEVICE":
                if current_device:
                    devices.append({
                        "device": current_device,
                        "capabilities": current_capabilities,
                        "mjpeg_caps": current_mjpeg_caps,
                    })
                current_device = None
                current_size = None
                current_format = None
            elif re.match(r"\[\d+\]:", line):
                fmt_match = re.search(r"'(\w+)'", line)
                if fmt_match:
                    current_format = fmt_match.group(1).upper()
            elif line.startswith("Size: Discrete"):
                parts = line.split()
                current_size = parts[-1] if parts else None
                if current_size and current_size not in current_capabilities:
                    current_capabilities[current_size] = []
            elif line.startswith("Size: Stepwise"):
                match = re.search(r'(\d+)x(\d+)\s*-\s*(\d+)x(\d+)', line)
                if match:
                    min_w, min_h = int(match.group(1)), int(match.group(2))
                    max_w, max_h = int(match.group(3)), int(match.group(4))
                    common = [
                        (1920, 1080), (1280, 720),
                        (1024, 768), (800, 600), (640, 480),
                    ]
                    for w, h in common:
                        if min_w <= w <= max_w and min_h <= h <= max_h:
                            res = f"{w}x{h}"
                            current_size = res
                            if res not in current_capabilities:
                                current_capabilities[res] = []
            elif line.startswith("Interval: Discrete") and current_size:
                match = re.search(r'\((\d+(?:\.\d+)?)\s+fps\)', line)
                if match:
                    fps = int(float(match.group(1)))
                    if current_size in current_capabilities:
                        if fps not in current_capabilities[current_size]:
                            current_capabilities[current_size].append(fps)
                    if current_format == "MJPEG" and current_size:
                        if current_size not in current_mjpeg_caps:
                            current_mjpeg_caps[current_size] = []
                        if fps not in current_mjpeg_caps[current_size]:
                            current_mjpeg_caps[current_size].append(fps)
            elif current_device and "support" in line.lower():
                pairs = re.findall(r'(\d+x\d+)\s+(\d+)\s+fps', line)
                for res, fps_str in pairs:
                    if res not in current_capabilities:
                        current_capabilities[res] = []
                    fps_val = int(fps_str)
                    if fps_val not in current_capabilities[res]:
                        current_capabilities[res].append(fps_val)
                    current_size = res

        return devices

    @staticmethod
    def _parse_audio_output(output: str) -> list:
        """Парсит вывод pactl list short sources или arecord -l."""
        devices = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # pactl list short sources
            # Формат: ID	NAME	Module	SampleSpec	ChannelMap	State
            if re.match(r'^\d+\s+\S+', line) and 'card' in line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    name = parts[1].strip()
                    desc = parts[1].strip()
                    devices.append({"name": name, "description": desc, "driver": "pulse"})
            elif re.match(r'^\d+\s+\S+', line):
                parts = line.split('\t')
                if len(parts) >= 2:
                    name = parts[1].strip()
                    devices.append({"name": name, "description": name, "driver": "pulse"})
            # arecord -l
            # Формат: card 0: PCH [Intel PCH], device 0: ALC256 Analog [ALC256 Analog]
            elif line.lower().startswith("card "):
                m = re.search(r'card\s+(\d+)[^,]+,\s*device\s+(\d+):\s+(.+)', line, re.IGNORECASE)
                if m:
                    card_num = m.group(1)
                    dev_num = m.group(2)
                    desc = m.group(3).strip()
                    hw_name = f"hw:{card_num},{dev_num}"
                    devices.append({"name": hw_name, "description": desc, "driver": "alsa"})
        return devices


# ---------------------------------------------------------------------------
# Основной диалог
# ---------------------------------------------------------------------------
class RemoteRecordingDialog(QDialog):
    """
    Диалог удалённой записи видео с устройства.
    FFmpeg запускается на удалённом хосте через SSH как сервер.
    Открывается в non-modal режиме.
    """

    def __init__(self, device: DeviceModel, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"Удалённая запись — {device.name}")
        self.resize(820, 650)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._stream_manager = FFmpegStreamManager(self)
        self._stream_manager.started.connect(self._on_started)
        self._stream_manager.stopped.connect(self._on_stopped)
        self._stream_manager.output.connect(self._on_output)
        self._stream_manager.error.connect(self._on_error)

        self._gst_manager = GStreamerStreamManager(self)
        self._gst_manager.started.connect(self._on_gst_started)
        self._gst_manager.stopped.connect(self._on_gst_stopped)
        self._gst_manager.output.connect(self._on_gst_output)
        self._gst_manager.error.connect(self._on_gst_error)
        self._gst_manager.gst_install_finished.connect(self._on_gst_install_finished)

        self._active_engine: str = "ffmpeg"
        self._session_settings: dict = {
            "ffplay_path": "ffplay",
            "vlc_path": "vlc",
            "recording_path": "/tmp/recordings",
        }

        self._player_processes: list[QProcess] = []
        self._scan_thread: Optional[QThread] = None
        self._ssh_check_thread: Optional[QThread] = None
        self._scan_results: list = []
        self._scan_audio_results: list = []
        self._server_state: str = "idle"
        self._is_recording: bool = False
        self._rec_filepath: str = ""
        self._rec_start_time: float = 0.0
        self._rec_pid_file: str = ""
        self._rec_log_lines: list[tuple[str, Optional[str]]] = []

        self._init_ui()
        self._load_settings()
        self._update_connection_url()
        self._update_buttons()
        self._update_status_label(False)

    # -------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # --- Верхний статус + SSH-проверка ---
        top_row = QHBoxLayout()
        self.status_label = QLabel("🔴 Сервер остановлен")
        top_row.addWidget(self.status_label)
        top_row.addStretch()
        self.ssh_check_btn = QPushButton("🔌 Проверить SSH")
        self.ssh_check_btn.setToolTip("Проверить доступность SSH-соединения")
        self.ssh_check_btn.clicked.connect(self._check_ssh)
        top_row.addWidget(self.ssh_check_btn)
        self.ssh_status_label = QLabel("")
        top_row.addWidget(self.ssh_status_label)
        layout.addLayout(top_row)

        # --- Вкладка "Источник" (верхний уровень) ---
        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        source_layout.setSpacing(6)

        # --- Тип захвата ---
        capture_row = QFormLayout()
        capture_row.setSpacing(4)

        self.capture_type_combo = QComboBox()
        self.capture_type_combo.addItem("Экран (x11grab)", "x11grab")
        self.capture_type_combo.addItem("Камера (v4l2)", "v4l2")
        self.capture_type_combo.currentIndexChanged.connect(self._on_capture_type_changed)

        self.capture_input_combo = QComboBox()
        self.capture_input_combo.setEditable(True)
        self.capture_input_combo.setPlaceholderText(":0.0")
        self.capture_input_combo.currentTextChanged.connect(self._update_connection_url)
        self.capture_input_combo.currentIndexChanged.connect(self._on_device_selected)

        self.scan_btn = QPushButton("🔍 Сканировать")
        self.scan_btn.setToolTip("Сканировать доступные устройства на удалённом хосте")
        self.scan_btn.clicked.connect(self._scan_devices)

        capture_input_row = QHBoxLayout()
        capture_input_row.setSpacing(4)
        capture_input_row.addWidget(self.capture_input_combo, stretch=3)
        capture_input_row.addWidget(self.scan_btn, stretch=1)

        capture_row.addRow("Тип захвата:", self.capture_type_combo)
        capture_row.addRow("Источник:", capture_input_row)
        source_layout.addLayout(capture_row)

        # --- Видео ---
        video_group = QGroupBox("Видео")
        video_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        video_layout = QFormLayout(video_group)
        video_layout.setSpacing(4)

        self.profile_combo = QComboBox()
        for k, v in _PROFILES.items():
            self.profile_combo.addItem(v["name"], k)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)

        self.input_format_combo = QComboBox()
        self.input_format_combo.addItem("Авто", "")
        self.input_format_combo.addItem("MJPEG (высокий FPS)", "mjpeg")
        self.input_format_combo.addItem("YUYV (raw)", "yuyv422")
        self.input_format_combo.addItem("NV12", "nv12")
        self.input_format_combo.addItem("UYVY", "uyvy422")

        self.codec_combo = QComboBox()
        self.codec_combo.addItem("MPEG-2 (лёгкий)", "mpeg2video")
        self.codec_combo.addItem("MPEG-4", "mpeg4")
        self.codec_combo.addItem("H.264", "libx264")
        self.codec_combo.addItem("H.265", "libx265")

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("1920x1080", "1920x1080")
        self.resolution_combo.addItem("1280x720", "1280x720")
        self.resolution_combo.addItem("1024x768", "1024x768")
        self.resolution_combo.addItem("800x600", "800x600")
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_changed)

        self.fps_combo = QComboBox()
        self.fps_combo.addItem("25", 25)

        self.bitrate_edit = QLineEdit()
        self.bitrate_edit.setPlaceholderText("2M")
        self.bitrate_edit.setText("2M")
        rx = QRegularExpression(r"^\d+[kKmMgG]?$")
        self.bitrate_edit.setValidator(QRegularExpressionValidator(rx, self))

        video_layout.addRow("Профиль:", self.profile_combo)
        video_layout.addRow("Формат ввода:", self.input_format_combo)
        video_layout.addRow("Кодек:", self.codec_combo)
        video_layout.addRow("Разрешение:", self.resolution_combo)
        video_layout.addRow("FPS:", self.fps_combo)
        video_layout.addRow("Битрейт видео:", self.bitrate_edit)

        # --- Аудио ---
        self._audio_group = QGroupBox("Аудио")
        self._audio_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._audio_group.setCheckable(True)
        self._audio_group.setChecked(False)
        audio_layout = QFormLayout(self._audio_group)
        audio_layout.setSpacing(4)

        self.audio_source_combo = QComboBox()
        self.audio_source_combo.addItem("PulseAudio", "pulse")
        self.audio_source_combo.addItem("ALSA", "alsa")
        self.audio_source_combo.currentIndexChanged.connect(self._on_audio_driver_changed)

        self.audio_device_combo = QComboBox()
        self.audio_device_combo.setEditable(True)
        self.audio_device_combo.setPlaceholderText("Нажмите «Сканировать»")
        self.audio_device_combo.currentIndexChanged.connect(self._on_audio_device_changed)

        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItem("AAC", "aac")
        self.audio_codec_combo.addItem("MP3", "libmp3lame")
        self.audio_codec_combo.addItem("PCM 16-bit", "pcm_s16le")
        self.audio_codec_combo.addItem("Opus", "libopus")

        self.audio_bitrate_edit = QLineEdit()
        self.audio_bitrate_edit.setPlaceholderText("128k")
        self.audio_bitrate_edit.setText("128k")
        rx_audio = QRegularExpression(r"^\d+[kKmMgG]?$")
        self.audio_bitrate_edit.setValidator(QRegularExpressionValidator(rx_audio, self))

        audio_layout.addRow("Драйвер:", self.audio_source_combo)
        audio_layout.addRow("Устройство:", self.audio_device_combo)
        audio_layout.addRow("Кодек:", self.audio_codec_combo)
        audio_layout.addRow("Битрейт:", self.audio_bitrate_edit)

        params_row = QHBoxLayout()
        params_row.setSpacing(8)
        params_row.addWidget(video_group, stretch=1)
        params_row.addWidget(self._audio_group, stretch=1)
        source_layout.addLayout(params_row)

        # ===================== Вложенные вкладки Стрим/Запись =====================
        self.mode_tabs = QTabWidget()

        # --- Вкладка "Стрим" (вложенная) ---
        stream_tab = QWidget()
        stream_layout = QVBoxLayout(stream_tab)
        stream_layout.setSpacing(6)

        # --- Движок сервера ---
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("GStreamer (RTSP)", "gstreamer")
        self.engine_combo.addItem("FFmpeg", "ffmpeg")
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)

        self.gst_install_btn = QPushButton("📦 Установить")
        self.gst_install_btn.setToolTip("Установить GStreamer на удалённый хост через dnf")
        self.gst_install_btn.clicked.connect(self._install_gstreamer)

        self.gst_status_label = QLabel("")

        self.transport_combo = QComboBox()
        self.transport_combo.addItem("TCP (listen)", "tcp")
        self.transport_combo.addItem("HTTP (listen)", "http")
        self.transport_combo.currentIndexChanged.connect(self._on_transport_changed)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(8080)
        self.port_spin.valueChanged.connect(self._update_connection_url)

        engine_row = QHBoxLayout()
        engine_row.setSpacing(4)
        engine_row.addWidget(self.engine_combo, 1)
        engine_row.addWidget(self.gst_install_btn)
        engine_row.addWidget(self.gst_status_label)

        proto_row = QHBoxLayout()
        proto_row.setSpacing(6)
        lbl_proto = QLabel("Протокол:")
        lbl_proto.setBuddy(self.transport_combo)
        proto_row.addWidget(lbl_proto)
        proto_row.addWidget(self.transport_combo, 1)
        lbl_port = QLabel("Порт:")
        lbl_port.setBuddy(self.port_spin)
        proto_row.addWidget(lbl_port)
        proto_row.addWidget(self.port_spin, 1)

        proto_form = QFormLayout()
        proto_form.setSpacing(6)
        proto_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        proto_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        proto_form.addRow("Движок:", engine_row)
        proto_form.addRow(proto_row)
        stream_layout.addLayout(proto_form)

        # --- URL потока ---
        url_group = QGroupBox("URL потока")
        url_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        url_layout = QVBoxLayout(url_group)
        url_layout.setSpacing(4)
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        self.url_edit.setPlaceholderText("URL появится после запуска сервера")
        url_row.addWidget(self.url_edit)
        self.copy_url_btn = QPushButton("📋")
        self.copy_url_btn.setToolTip("Копировать URL в буфер обмена")
        self.copy_url_btn.setFixedWidth(36)
        self.copy_url_btn.clicked.connect(self._copy_url_to_clipboard)
        url_row.addWidget(self.copy_url_btn)
        url_layout.addLayout(url_row)
        stream_layout.addWidget(url_group)

        # --- Пути к плеерам ---
        paths_group = QGroupBox("Пути к плеерам")
        paths_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        paths_form = QFormLayout(paths_group)
        paths_form.setSpacing(4)

        self.ffplay_path_edit = QLineEdit()
        self.vlc_path_edit = QLineEdit()

        for edit, name in [
            (self.ffplay_path_edit, "ffplay"),
            (self.vlc_path_edit, "vlc"),
        ]:
            edit.editingFinished.connect(lambda e=edit, n=name: self._validate_binary_field(e, n))
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(edit)
            btn = QPushButton("Обзор...")
            btn.clicked.connect(lambda checked=False, n=name, e=edit: self._browse_path(n, e))
            row.addWidget(btn)
            paths_form.addRow(f"{name.capitalize()}:", row)

        stream_layout.addWidget(paths_group)

        # --- Кнопки управления потоком ---
        stream_btn_layout = QHBoxLayout()
        stream_btn_layout.setSpacing(4)
        self.start_btn = QPushButton("▶ Запустить сервер")
        self.stop_btn = QPushButton("⏹ Остановить")
        self.ffplay_btn = QPushButton("ffplay")
        self.vlc_btn = QPushButton("VLC")

        self.start_btn.clicked.connect(self._start_server)
        self.stop_btn.clicked.connect(self._stop_server)
        self.ffplay_btn.clicked.connect(lambda: self._open_in_player("ffplay"))
        self.vlc_btn.clicked.connect(lambda: self._open_in_player("vlc"))

        stream_btn_layout.addWidget(self.start_btn)
        stream_btn_layout.addWidget(self.stop_btn)
        stream_btn_layout.addStretch()
        self.stream_log_btn = QPushButton("📋 Лог")
        self.stream_log_btn.setToolTip("Просмотреть лог FFmpeg")
        self.stream_log_btn.clicked.connect(self._show_stream_log_dialog)
        stream_btn_layout.addWidget(self.stream_log_btn)
        stream_btn_layout.addWidget(self.ffplay_btn)
        stream_btn_layout.addWidget(self.vlc_btn)
        stream_layout.addLayout(stream_btn_layout)

        stream_layout.addStretch()
        self.mode_tabs.addTab(stream_tab, "Стрим")

        # --- Вкладка "Запись" (вложенная) ---
        rec_tab = QWidget()
        rec_layout = QVBoxLayout(rec_tab)
        rec_layout.setSpacing(6)

        # --- Настройки записи + Наложение текста ---
        rec_settings_group = QGroupBox("Настройки записи")
        rec_settings_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        rec_settings_layout = QVBoxLayout(rec_settings_group)
        rec_settings_layout.setSpacing(4)

        self.rec_path_edit = QLineEdit()
        self.rec_path_edit.setPlaceholderText("/home/user/recordings")
        rec_paths_form = QFormLayout()
        rec_paths_form.setSpacing(4)
        rec_paths_form.addRow("Путь на сервере:", self.rec_path_edit)

        self.rec_format_combo = QComboBox()
        self.rec_format_combo.addItem("MKV (рекомендуется)", "mkv")
        self.rec_format_combo.addItem("MP4", "mp4")
        self.rec_format_combo.addItem("TS", "ts")
        self.rec_format_combo.addItem("AVI", "avi")
        rec_paths_form.addRow("Формат:", self.rec_format_combo)

        self.rec_filename_edit = QLineEdit()
        self.rec_filename_edit.editingFinished.connect(self._on_filename_edited)
        rec_paths_form.addRow("Файл записи:", self.rec_filename_edit)

        rec_settings_layout.addLayout(rec_paths_form)
        rec_layout.addWidget(rec_settings_group)

        # --- Статус + Информация о записи ---
        rec_info_group = QGroupBox("Информация")
        rec_info_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        rec_info_layout = QFormLayout(rec_info_group)
        rec_info_layout.setSpacing(2)
        self.rec_status_label = QLabel("Запись не активна")
        self.rec_file_label = QLabel("—")
        self.rec_size_label = QLabel("—")
        self.rec_duration_label = QLabel("—")
        rec_info_layout.addRow("Статус:", self.rec_status_label)
        rec_info_layout.addRow("Файл:", self.rec_file_label)
        rec_info_layout.addRow("Размер:", self.rec_size_label)
        rec_info_layout.addRow("Длительность:", self.rec_duration_label)
        rec_layout.addWidget(rec_info_group)

        # --- Кнопки записи ---
        rec_btn_layout = QHBoxLayout()
        rec_btn_layout.setSpacing(4)
        self.rec_start_btn = QPushButton("⏺ Начать запись")
        self.rec_start_btn.clicked.connect(self._start_recording)
        self.rec_stop_btn = QPushButton("⏹ Остановить")
        self.rec_stop_btn.clicked.connect(self._stop_recording)
        self.rec_stop_btn.setEnabled(False)
        rec_btn_layout.addWidget(self.rec_start_btn)
        rec_btn_layout.addWidget(self.rec_stop_btn)
        rec_btn_layout.addStretch()
        self.rec_log_btn = QPushButton("📋 Лог")
        self.rec_log_btn.setToolTip("Просмотреть лог записи")
        self.rec_log_btn.clicked.connect(self._show_rec_log_dialog)
        rec_btn_layout.addWidget(self.rec_log_btn)
        rec_layout.addLayout(rec_btn_layout)

        rec_layout.addStretch()
        self.mode_tabs.addTab(rec_tab, "Запись")

        source_layout.addWidget(self.mode_tabs, 1)

        # --- Собираем верхний TabWidget ---
        self.tabs = QTabWidget()
        self.tabs.addTab(source_tab, "Источник")
        layout.addWidget(self.tabs, 1)

        # --- Лог (внутренний) ---
        self._log_lines: list[tuple[str, Optional[str]]] = []

        # --- Кнопка закрытия ---
        close_layout = QHBoxLayout()
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        close_layout.addStretch()
        close_layout.addWidget(self.close_btn)
        layout.addLayout(close_layout)

        # Таймер обновления информации о записи
        self._rec_info_timer = QTimer(self)
        self._rec_info_timer.timeout.connect(self._update_rec_info)

        # Обновить имя файла при изменении формата
        self.rec_format_combo.currentIndexChanged.connect(self._update_rec_filename)
        self._update_rec_filename()

        # Начальное состояние движка
        self._on_engine_changed(0)

    # -------------------------------------------------------------------
    # Настройки
    # -------------------------------------------------------------------
    def _load_settings(self) -> None:
        self.ffplay_path_edit.setText(self._session_settings.get("ffplay_path", "ffplay"))
        self.vlc_path_edit.setText(self._session_settings.get("vlc_path", "vlc"))

        recording_path = self._session_settings.get("recording_path", "/tmp/recordings") or "/tmp/recordings"
        self.rec_path_edit.setText(recording_path)
        self._update_rec_filename()

        for edit, fallback in [
            (self.ffplay_path_edit, "ffplay"),
            (self.vlc_path_edit, "vlc"),
        ]:
            if not _validate_binary(edit.text()):
                found = _find_in_path(fallback)
                if found:
                    edit.setText(found)

        self._validate_all_binaries()
        self._reset_device_combos()

    def _reset_device_combos(self) -> None:
        """Сбросить комбо-боксы аудио в начальное состояние при повторном открытии."""
        self._scan_audio_results = []
        self.audio_source_combo.blockSignals(True)
        self.audio_source_combo.setCurrentIndex(0)
        self.audio_source_combo.blockSignals(False)
        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        self.audio_device_combo.setPlaceholderText("Нажмите «Сканировать»")
        self.audio_device_combo.blockSignals(False)

    def _save_settings(self) -> None:
        self._session_settings["ffplay_path"] = self.ffplay_path_edit.text() or "ffplay"
        self._session_settings["vlc_path"] = self.vlc_path_edit.text() or "vlc"
        self._session_settings["recording_path"] = self.rec_path_edit.text()

    # -------------------------------------------------------------------
    # Валидация бинарников
    # -------------------------------------------------------------------
    def _validate_binary_field(self, edit: QLineEdit, name: str) -> bool:
        ok = _validate_binary(edit.text() or name)
        if ok:
            edit.setToolTip("")
        else:
            edit.setToolTip(f"Не найден в PATH: {edit.text() or name}")
        return ok

    def _validate_all_binaries(self) -> None:
        self._validate_binary_field(self.ffplay_path_edit, "ffplay")
        self._validate_binary_field(self.vlc_path_edit, "vlc")

    # -------------------------------------------------------------------
    # Профили
    # -------------------------------------------------------------------
    def _on_profile_changed(self, index: int) -> None:
        """При смене профиля обновить кодек и разрешение. FPS выставится автоматически через _on_resolution_changed на максимум поддерживаемого."""
        if index < 0:
            return
        key = self.profile_combo.currentData()
        prof = _PROFILES.get(key)
        if not prof:
            return
        self.codec_combo.setCurrentIndex(self.codec_combo.findData(prof["codec"]))
        res_idx = self.resolution_combo.findData(prof["resolution"])
        if res_idx >= 0:
            self.resolution_combo.setCurrentIndex(res_idx)
            self._on_resolution_changed(res_idx)
        self.bitrate_edit.setText(prof["bitrate"])

    # -------------------------------------------------------------------
    # Тип захвата
    # -------------------------------------------------------------------
    def _on_capture_type_changed(self) -> None:
        capture_type = self.capture_type_combo.currentData()
        placeholders = {
            "x11grab": ":0.0",
            "v4l2": "/dev/video0",
        }
        self.capture_input_combo.setPlaceholderText(placeholders.get(capture_type, ""))
        self.capture_input_combo.blockSignals(True)
        self.capture_input_combo.clearEditText()
        self.capture_input_combo.clear()
        self.capture_input_combo.blockSignals(False)

        if capture_type == "v4l2":
            cached = _get_cached_scan(self.device.host, _scan_cache_video)
            if cached is not None:
                self._scan_results = cached
                for item in cached:
                    caps = item.get("capabilities", {})
                    label = item["device"]
                    if caps:
                        resolutions = sorted(
                            caps.keys(),
                            key=lambda r: (int(r.split("x")[0]) * int(r.split("x")[1])),
                            reverse=True,
                        )
                        label += f" ({', '.join(resolutions[:3])})"
                    self.capture_input_combo.addItem(label, item["device"])
                if self.capture_input_combo.count() > 0:
                    self.capture_input_combo.setCurrentIndex(0)
                    self._on_device_selected(0)
        
        # Аудио кэш
        audio_cached = _get_cached_scan(self.device.host, _scan_cache_audio)
        if audio_cached is not None:
            self._scan_audio_results = audio_cached
            current_driver = self.audio_source_combo.currentData()
            self._populate_audio_combo(current_driver)
        
        if capture_type != "v4l2" or not self._scan_results:
            self._reset_resolution_defaults()

    # -------------------------------------------------------------------
    # Сканирование
    # -------------------------------------------------------------------
    def _populate_audio_combo(self, driver: Optional[str] = None) -> None:
        """Заполнить combo аудио-устройств из кэша, опционально фильтруя по драйверу."""
        if not self._scan_audio_results:
            return
        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        for item in self._scan_audio_results:
            item_driver = item.get("driver", "pulse")
            if driver and item_driver != driver:
                continue
            label = item["description"] or item["name"]
            self.audio_device_combo.addItem(f"[{item_driver}] {label}", item["name"])
        if self.audio_device_combo.count() > 0:
            self.audio_device_combo.setCurrentIndex(0)
        self.audio_device_combo.blockSignals(False)

    # -------------------------------------------------------------------
    # Аудио-драйвер
    # -------------------------------------------------------------------
    def _on_audio_driver_changed(self, index: int) -> None:
        driver = self.audio_source_combo.itemData(index)
        self._populate_audio_combo(driver)

    # -------------------------------------------------------------------
    # Сканирование
    # -------------------------------------------------------------------
    def _scan_devices(self) -> None:
        vid_cached = _get_cached_scan(self.device.host, _scan_cache_video)
        aud_cached = _get_cached_scan(self.device.host, _scan_cache_audio)
        if vid_cached is not None and aud_cached is not None:
            self._on_scan_result(vid_cached, aud_cached, "")
            return

        if self.capture_type_combo.currentData() == "v4l2":
            self.capture_input_combo.blockSignals(True)
            self.capture_input_combo.clear()
            self.capture_input_combo.blockSignals(False)
        self.scan_btn.setEnabled(False)

        self._scan_thread = ScanThread(self.device)
        self._scan_thread.result.connect(self._on_scan_result)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.start()

    def _on_scan_result(self, video_devices: list, audio_devices: list, error: str) -> None:
        self.scan_btn.setEnabled(True)
        if error:
            return

        _set_cached_scan(self.device.host, video_devices, _scan_cache_video)
        _set_cached_scan(self.device.host, audio_devices, _scan_cache_audio)

        self._scan_results = video_devices
        self._scan_audio_results = audio_devices

        # --- Видео: заполняем combo только для v4l2 ---
        capture_type = self.capture_type_combo.currentData()
        if capture_type == "v4l2":
            self.capture_input_combo.blockSignals(True)
            self.capture_input_combo.clear()
            if video_devices:
                for item in video_devices:
                    caps = item.get("capabilities", {})
                    label = item["device"]
                    if caps:
                        resolutions = sorted(
                            caps.keys(),
                            key=lambda r: (int(r.split("x")[0]) * int(r.split("x")[1])),
                            reverse=True,
                        )
                        label += f" ({', '.join(resolutions[:3])})"
                    self.capture_input_combo.addItem(label, item["device"])
                if self.capture_input_combo.count() > 0:
                    self.capture_input_combo.setCurrentIndex(0)
            self.capture_input_combo.blockSignals(False)
            if video_devices:
                self._on_device_selected(0)
        self._update_connection_url()

        # --- Аудио ---
        current_driver = self.audio_source_combo.currentData()
        self._populate_audio_combo(current_driver)

    # -------------------------------------------------------------------
    # Выбор устройства / разрешения
    # -------------------------------------------------------------------
    def _on_device_selected(self, index: int) -> None:
        if index < 0:
            return
        device_path = self.capture_input_combo.itemData(index)
        if not device_path:
            return

        device_data = None
        for item in self._scan_results:
            if item["device"] == device_path:
                device_data = item
                break

        caps = device_data.get("capabilities", {}) if device_data else {}
        self._update_connection_url()

        if caps:
            self.resolution_combo.blockSignals(True)
            self.resolution_combo.clear()
            for res in sorted(
                caps.keys(),
                key=lambda r: (int(r.split("x")[0]) * int(r.split("x")[1])),
                reverse=True,
            ):
                self.resolution_combo.addItem(res, res)
            if self.resolution_combo.count() > 0:
                self.resolution_combo.setCurrentIndex(0)
            self.resolution_combo.blockSignals(False)
            self._on_resolution_changed(0)
        self._auto_select_input_format()

    def _on_audio_device_changed(self, index: int) -> None:
        if index < 0:
            return

    def _on_resolution_changed(self, index: int) -> None:
        if index < 0:
            return
        resolution = self.resolution_combo.itemData(index)
        if not resolution:
            return

        device_path = self.capture_input_combo.currentData()
        supported_fps: list[int] = []
        for item in self._scan_results:
            if item["device"] == device_path:
                supported_fps = item.get("capabilities", {}).get(resolution, [])
                break

        self.fps_combo.blockSignals(True)
        self.fps_combo.clear()
        if supported_fps:
            supported_fps = sorted(set(supported_fps))
            for fps in supported_fps:
                self.fps_combo.addItem(str(fps), fps)
            self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)
        else:
            for fps in (15, 20, 25, 30, 60):
                self.fps_combo.addItem(str(fps), fps)
            self.fps_combo.setCurrentIndex(2)
        self.fps_combo.blockSignals(False)

        self._auto_select_input_format()

    def _reset_resolution_defaults(self) -> None:
        self.resolution_combo.blockSignals(True)
        self.resolution_combo.clear()
        for res in ("1920x1080", "1280x720", "1024x768", "800x600"):
            self.resolution_combo.addItem(res, res)
        self.resolution_combo.setCurrentIndex(0)
        self.resolution_combo.blockSignals(False)
        self.fps_combo.blockSignals(True)
        self.fps_combo.clear()
        for fps in (15, 20, 25, 30, 60):
            self.fps_combo.addItem(str(fps), fps)
        self.fps_combo.setCurrentIndex(2)
        self.fps_combo.blockSignals(False)
        self.input_format_combo.setCurrentIndex(0)

    def _auto_select_input_format(self) -> None:
        resolution = self.resolution_combo.currentData()
        fps = self.fps_combo.currentData() or 25
        device_path = self.capture_input_combo.currentData() or ""
        for item in self._scan_results:
            if item["device"] == device_path:
                mjpeg_caps = item.get("mjpeg_caps", {})
                if resolution in mjpeg_caps and fps in mjpeg_caps[resolution]:
                    self.input_format_combo.setCurrentIndex(1)  # MJPEG
                else:
                    self.input_format_combo.setCurrentIndex(0)  # Авто
                return
        self.input_format_combo.setCurrentIndex(0)

    # -------------------------------------------------------------------
    # URL / Поток
    # -------------------------------------------------------------------
    def _get_capture_input(self) -> str:
        data = self.capture_input_combo.currentData()
        if data:
            return data
        text = self.capture_input_combo.currentText().strip()
        if text:
            return text
        capture_type = self.capture_type_combo.currentData()
        defaults = {
            "x11grab": ":0.0",
            "v4l2": "/dev/video0",
        }
        return defaults.get(capture_type, "")

    def _get_stream_url(self) -> str:
        transport = self.transport_combo.currentData()
        port = self.port_spin.value()
        host = self.device.host
        if transport == "tcp":
            return f"tcp://{host}:{port}"
        elif transport == "http":
            return f"http://{host}:{port}/stream.ts"
        elif transport == "rtsp":
            return f"rtsp://{host}:{port}/stream"
        return f"tcp://{host}:{port}"

    def _update_connection_url(self) -> None:
        url = self._get_stream_url()
        self.url_edit.setPlaceholderText(f"URL: {url}")

    # -------------------------------------------------------------------
    # Выбор движка (FFmpeg / GStreamer)
    # -------------------------------------------------------------------
    def _on_engine_changed(self, index: int) -> None:
        engine = self.engine_combo.currentData() if index >= 0 else "gstreamer"
        self._active_engine = engine
        is_gst = engine == "gstreamer"

        self.gst_install_btn.setVisible(is_gst)
        self.gst_status_label.setVisible(is_gst)

        # Управляем доступностью протоколов
        self.transport_combo.blockSignals(True)
        current_transport = self.transport_combo.currentData()

        if not is_gst:
            # FFmpeg: TCP, HTTP
            self.transport_combo.clear()
            self.transport_combo.addItem("TCP (listen)", "tcp")
            self.transport_combo.addItem("HTTP (listen)", "http")
            self.port_spin.setValue(8080)
        else:
            # GStreamer: только RTSP
            self.transport_combo.clear()
            self.transport_combo.addItem("RTSP", "rtsp")
            self.port_spin.setValue(8554)

        self.transport_combo.blockSignals(False)
        self._update_connection_url()
        self._update_buttons()

    def _on_transport_changed(self, index: int) -> None:
        transport = self.transport_combo.currentData()
        if transport == "rtsp":
            self.port_spin.setValue(8554)
        elif self.port_spin.value() == 8554:
            self.port_spin.setValue(8080)
        self._update_connection_url()

    # -------------------------------------------------------------------
    # Проверка и установка GStreamer
    # -------------------------------------------------------------------
    def _install_gstreamer(self) -> None:
        self.gst_install_btn.setEnabled(False)
        self.gst_status_label.setText("Установка GStreamer...")
        self._gst_manager.install_gstreamer(self.device)

    def _on_gst_install_finished(self, device_iid: str, success: bool, message: str) -> None:
        self.gst_install_btn.setEnabled(True)
        if success:
            self.gst_status_label.setStyleSheet("color: green;")
            self.gst_status_label.setText(f"✅ {message}")
        else:
            self.gst_status_label.setStyleSheet("color: red;")
            self.gst_status_label.setText(f"❌ {message}")

    # GStreamer signal handlers
    def _on_gst_started(self, device_iid: str) -> None:
        self._server_state = "streaming"
        self._update_buttons()
        self._update_status_label(True)
        self._append_log("[INFO] GStreamer RTSP-сервер запущен", "info")

    def _on_gst_stopped(self, device_iid: str) -> None:
        self._server_state = "stopped"
        self._update_buttons()
        self.url_edit.clear()
        self._update_status_label(False)
        self._append_log("[INFO] GStreamer RTSP-сервер остановлен", "info")

    def _on_gst_output(self, device_iid: str, line: str) -> None:
        kind = None
        if line.startswith("[STDERR]"):
            lower = line.lower()
            if any(kw in lower for kw in ("error", "failed", "cannot", "denied", "not found")):
                kind = "error"
            elif "warning" in lower:
                kind = "warning"
        self._append_log(line, kind)

    def _on_gst_error(self, device_iid: str, message: str) -> None:
        if self._server_state != "stopped":
            self._server_state = "error"
            self.status_label.setText(f"🔴 {message}")
        self._update_buttons()
        self._append_log(f"[ERROR] {message}", "error")

    def _copy_url_to_clipboard(self) -> None:
        url = self.url_edit.text()
        if not url:
            # Попробуем достать из placeholder
            ph = self.url_edit.placeholderText()
            if ph.startswith("URL: "):
                url = ph[5:].split(" ")[0]
        if url:
            QApplication.clipboard().setText(url)
            self.copy_url_btn.setText("✓")
            QTimer.singleShot(1200, lambda: self.copy_url_btn.setText("📋"))

    # -------------------------------------------------------------------
    # Пути / Browse
    # -------------------------------------------------------------------
    def _browse_path(self, name: str, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Выберите {name}",
            "",
            "Исполняемые файлы (*.exe);;Все файлы (*)"
            if os.name == "nt"
            else "Все файлы (*)",
        )
        if path:
            edit.setText(path)
            self._validate_binary_field(edit, name)
            self._save_settings()

    # -------------------------------------------------------------------
    # SSH-проверка
    # -------------------------------------------------------------------
    def _check_ssh(self) -> None:
        self.ssh_check_btn.setEnabled(False)
        self.ssh_status_label.setText("Проверка...")

        thread = SSHCheckThread(self.device)
        thread.result.connect(self._on_ssh_checked)
        thread.finished.connect(thread.deleteLater)
        self._ssh_check_thread = thread
        thread.start()

    def _on_ssh_checked(self, ok: bool, message: str) -> None:
        self.ssh_check_btn.setEnabled(True)
        self.ssh_status_label.setText(message)

    # -------------------------------------------------------------------
    # Запуск / Остановка
    # -------------------------------------------------------------------
    def _start_server(self) -> None:
        if self._is_recording:
            QMessageBox.warning(
                self,
                "Запись активна",
                "Невозможно одновременно запустить стрим и запись.\n"
                "Сначала остановите запись."
            )
            return

        self._save_settings()
        self._update_connection_url()

        capture_type = self.capture_type_combo.currentData()
        capture_input = self._get_capture_input()
        engine = self.engine_combo.currentData()
        transport = self.transport_combo.currentData()

        # RTSP работает только с GStreamer
        if transport == "rtsp" and engine != "gstreamer":
            QMessageBox.warning(
                self,
                "Несовместимый протокол",
                "RTSP-стрим доступен только при выборе движка GStreamer.\n"
                "Переключите движок на «GStreamer (RTSP)» или выберите другой протокол."
            )
            return

        if capture_type == "v4l2":
            has_explicit = (
                self.capture_input_combo.currentData() is not None
                or self.capture_input_combo.currentText().strip() != ""
            )
            if not has_explicit:
                QMessageBox.warning(
                    self,
                    "Устройство не выбрано",
                    "Для захвата с камеры необходимо выбрать устройство.\n"
                    "Нажмите «Сканировать» и выберите камеру из списка, "
                    "или введите путь вручную (например, /dev/video0)."
                )
                return

        if capture_type == "x11grab" and not capture_input:
            QMessageBox.warning(
                self,
                "Источник не указан",
                "Не указан дисплей для захвата экрана.\n"
                "Укажите дисплей (например, :0.0)."
            )
            return

        resolution = self.resolution_combo.currentData()
        fps = self.fps_combo.currentData() or 25

        settings = {
            "capture_type": capture_type,
            "capture_input": capture_input,
            "input_format": self.input_format_combo.currentData(),
            "transport": transport,
            "codec": self.codec_combo.currentData(),
            "resolution": resolution,
            "bitrate": self.bitrate_edit.text() or "2M",
            "fps": fps,
            "port": self.port_spin.value(),
            "enable_audio": self._audio_group.isChecked(),
            "audio_source": self.audio_source_combo.currentData() or "pulse",
            "audio_input": self.audio_device_combo.currentData() or self.audio_device_combo.currentText() or "default",
            "audio_codec": self.audio_codec_combo.currentData() or "aac",
            "audio_bitrate": self.audio_bitrate_edit.text() or "128k",
        }

        # Валидация аудио
        if settings["enable_audio"]:
            audio_input = settings["audio_input"].strip()
            if not audio_input or audio_input.lower() in ("default", "", "сканирование..."):
                if settings["audio_source"] == "alsa":
                    QMessageBox.warning(
                        self,
                        "Аудио не настроено",
                        "ALSA требует выбора конкретного устройства.\n"
                        "Нажмите «🔍 Аудио» и выберите устройство из списка, "
                        "или отключите аудио (снимите галочку)."
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Аудио не настроено",
                        "Не выбрано аудио-устройство.\n"
                        "Нажмите «🔍 Аудио» для поиска устройств, "
                        "или отключите аудио (снимите галочку)."
                    )
                return

        self._log_lines.clear()
        self.url_edit.clear()

        if engine == "gstreamer":
            success = self._gst_manager.start(self.device, settings)
            if success:
                self.url_edit.setText(self._get_stream_url())
                self._update_buttons()
                self._update_status_label(True)
            else:
                QMessageBox.warning(
                    self, "Ошибка", "Не удалось запустить GStreamer RTSP-сервер на удалённом устройстве"
                )
                self._update_status_label(False)
        else:
            success = self._stream_manager.start(self.device, settings)
            if success:
                self.url_edit.setText(self._get_stream_url())
                self._update_buttons()
                self._update_status_label(True)
            else:
                QMessageBox.warning(
                    self, "Ошибка", "Не удалось запустить FFmpeg на удалённом устройстве"
                )
                self._update_status_label(False)

    def _stop_server(self) -> None:
        if self._active_engine == "gstreamer":
            self._gst_manager.stop()
        else:
            self._stream_manager.stop()
        self.url_edit.clear()
        self._update_buttons()
        self._update_status_label(False)

    # -------------------------------------------------------------------
    # Плеер (неблокирующий)
    # -------------------------------------------------------------------
    def _open_in_player(self, player: str) -> None:
        url = self._get_stream_url()
        transport = self.transport_combo.currentData()
        if player == "ffplay":
            path = self.ffplay_path_edit.text() or "ffplay"
            args = [url]
            if transport == "rtsp":
                args = ["-rtsp_transport", "tcp", url]
        else:
            path = self.vlc_path_edit.text() or "vlc"
            args = [url]
            if transport == "rtsp":
                args = ["--rtsp-tcp", url]

        process = QProcess(self)
        process.finished.connect(process.deleteLater)
        process.errorOccurred.connect(lambda err: self._on_player_error(player, err))
        self._player_processes.append(process)
        process.start(path, args)

    def _on_player_error(self, player: str, error: QProcess.ProcessError) -> None:
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return
        errors = {
            QProcess.ProcessError.FailedToStart: (
                f"{player} не может быть запущен. Проверьте путь."
            ),
            QProcess.ProcessError.Crashed: f"{player} завершился аварийно.",
            QProcess.ProcessError.Timedout: f"{player} не ответил вовремя.",
            QProcess.ProcessError.ReadError: f"Ошибка чтения из {player}.",
            QProcess.ProcessError.WriteError: f"Ошибка записи в {player}.",
        }
        QMessageBox.warning(self, "Ошибка", errors.get(error, f"Неизвестная ошибка {player}"))

    # -------------------------------------------------------------------
    # Обработка сигналов FFmpeg
    # -------------------------------------------------------------------
    def _on_started(self, device_iid: str) -> None:
        self._server_state = "streaming"
        self._update_buttons()
        self._update_status_label(True)
        self._append_log("[INFO] FFmpeg-сервер запущен", "info")

    def _on_stopped(self, device_iid: str) -> None:
        self._server_state = "stopped"
        self._update_buttons()
        self.url_edit.clear()
        self._update_status_label(False)
        self._append_log("[INFO] FFmpeg-сервер остановлен", "info")

    def _on_output(self, device_iid: str, line: str) -> None:
        kind = None
        if line.startswith("[STDERR]"):
            lower = line.lower()
            benign = (
                "connection reset by peer" in lower
                or "end of file" in lower
                or "url read error" in lower
            )
            if benign:
                kind = "warning"
            elif any(kw in lower for kw in (
                "error", "address already", "failed", "cannot", "denied",
                "no such", "not found",
            )):
                kind = "error"
        self._append_log(line, kind)

    def _on_error(self, device_iid: str, message: str) -> None:
        if self._server_state != "stopped":
            self._server_state = "error"
            self.status_label.setText(f"🔴 {message}")
        self._update_buttons()
        self._append_log(f"[ERROR] {message}", "error")

    # -------------------------------------------------------------------
    # Цветной лог
    # -------------------------------------------------------------------
    def _append_log(self, line: str, kind: Optional[str] = None) -> None:
        self._log_lines.append((line, kind))

    # -------------------------------------------------------------------
    # Статус / Кнопки
    # -------------------------------------------------------------------
    def _update_buttons(self) -> None:
        gst_running = self._gst_manager.is_running
        ffmpeg_running = self._stream_manager.is_running
        running = ffmpeg_running or gst_running
        recording = self._is_recording
        self.start_btn.setEnabled(not running and not recording)
        self.stop_btn.setEnabled(running)
        self.ffplay_btn.setEnabled(running)
        self.vlc_btn.setEnabled(running)
        self.rec_start_btn.setEnabled(not recording and not running)
        self.rec_stop_btn.setEnabled(recording)

    def _update_status_label(self, running: bool) -> None:
        if running:
            self._server_state = "streaming"
            engine_name = "GStreamer RTSP" if self._active_engine == "gstreamer" else "FFmpeg"
            self.status_label.setText(f"🟢 {engine_name} стрим активен")
        elif self._is_recording:
            self._server_state = "recording"
            self.status_label.setText("🟡 Запись активна")
        else:
            self._server_state = "stopped"
            self.status_label.setText("🔴 Сервер остановлен")

    # -------------------------------------------------------------------
    # События
    # -------------------------------------------------------------------
    def showEvent(self, event: QEvent) -> None:
        self._load_settings()
        self._update_connection_url()
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        any_running = self._stream_manager.is_running or self._gst_manager.is_running
        if any_running:
            engine_name = "GStreamer RTSP" if self._gst_manager.is_running else "FFmpeg"
            reply = QMessageBox.question(
                self,
                "Подтверждение",
                f"{engine_name}-сервер запущен. Закрыть диалог и остановить стрим?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._is_recording:
            self._stop_recording()
        self._save_settings()
        self._stream_manager.stop()
        self._gst_manager.stop()
        self._kill_player_processes()
        self._cancel_scan_thread()
        self._rec_info_timer.stop()
        try:
            if self._ssh_check_thread is not None and self._ssh_check_thread.isRunning():
                self._ssh_check_thread.quit()
                self._ssh_check_thread.wait(1000)
        except RuntimeError:
            pass
        event.accept()

    def _kill_player_processes(self) -> None:
        for proc in self._player_processes:
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.kill()
                    proc.waitForFinished(2000)
            except RuntimeError:
                pass
        self._player_processes.clear()

    def _cancel_scan_thread(self) -> None:
        """Отменить сканирование при закрытии диалога."""
        if self._scan_thread is not None:
            try:
                if self._scan_thread.isRunning():
                    self._scan_thread.quit()
                    self._scan_thread.wait(1000)
            except RuntimeError:
                pass
            self._scan_thread = None

    # -------------------------------------------------------------------
    # Запись (локальное сохранение потока)
    # -------------------------------------------------------------------
    def _browse_rec_path(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        path, ok = QInputDialog.getText(
            self, "Путь на сервере",
            "Укажите путь для сохранения записей на удалённой машине:",
            text=self.rec_path_edit.text(),
        )
        if ok and path.strip():
            self.rec_path_edit.setText(path.strip())
            self._update_rec_filename()
            self._save_settings()

    def _update_rec_filename(self) -> None:
        now = datetime.now()
        host = self.device.host.replace(":", "_").replace("/", "_")
        fmt = self.rec_format_combo.currentData() if hasattr(self, 'rec_format_combo') else "mkv"
        filename = f"{host}_{now.strftime('%Y%m%d_%H%M%S')}.{fmt}"
        self.rec_filename_edit.setText(filename)

    def _on_filename_edited(self) -> None:
        text = self.rec_filename_edit.text().strip()
        if not text:
            self._update_rec_filename()
            return
        fmt = self.rec_format_combo.currentData() if hasattr(self, 'rec_format_combo') else "mkv"
        ext = f".{fmt}"
        if not text.lower().endswith(ext) and not any(text.lower().endswith(f".{e}") for e in ("mkv", "mp4", "ts", "avi")):
            text = text.rsplit(".", 1)[0] if "." in text else text
            text = f"{text}{ext}"
            self.rec_filename_edit.setText(text)

    def _get_rec_filepath(self) -> str:
        rec_dir = self.rec_path_edit.text().strip().rstrip("/")
        if not rec_dir:
            rec_dir = "/tmp"
        filename = self.rec_filename_edit.text().strip()
        return f"{rec_dir}/{filename}"

    
    def _start_recording(self) -> None:
        if self._is_recording:
            return

        if self._stream_manager.is_running or self._gst_manager.is_running:
            QMessageBox.warning(
                self,
                "Стрим активен",
                "Невозможно одновременно запустить стрим и запись.\n"
                "Сначала остановите стрим."
            )
            return

        self._save_settings()
        self._update_rec_filename()

        rec_dir = self.rec_path_edit.text().strip().rstrip("/")
        if not rec_dir:
            QMessageBox.warning(self, "Ошибка", "Укажите путь для сохранения записи на сервере.")
            return

        fmt = self.rec_format_combo.currentData()
        filepath = self._get_rec_filepath()
        self._rec_filepath = filepath
        self._is_recording = True
        self._rec_start_time = time.time()
        self.rec_status_label.setText("⏺ Запись активна (сервер)")
        self.rec_file_label.setText(filepath)
        self.rec_size_label.setText("—")
        self.rec_duration_label.setText("0:00")
        self._update_buttons()
        self._update_status_label(self._stream_manager.is_running or self._gst_manager.is_running)

        self._rec_log_lines.clear()
        self._append_rec_log(f"[INFO] Запуск записи на сервере: {filepath}")

        self._start_remote_recording(filepath, fmt)

    def _start_remote_recording(self, filepath: str, fmt: str) -> None:
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device)
        except Exception as e:
            self._is_recording = False
            self.rec_status_label.setText("🔴 Ошибка SSH")
            self._append_rec_log(f"[ERROR] Ошибка подключения: {e}")
            self._update_buttons()
            return

        try:
            rec_dir = os.path.dirname(filepath).replace("\\", "/")
            client.exec_command(f"mkdir -p {shlex.quote(rec_dir)}")
            import time as _t
            _t.sleep(0.3)
        except Exception:
            pass

        ffmpeg = "ffmpeg"
        capture_type = self.capture_type_combo.currentData()
        capture_input = self._get_capture_input()
        resolution = self.resolution_combo.currentData() or "1280x720"
        fps = self.fps_combo.currentData() or 25
        input_format = self.input_format_combo.currentData()
        codec = self.codec_combo.currentData()
        bitrate = self.bitrate_edit.text() or "2M"

        env_prefix = ""
        if capture_type == "x11grab":
            display = capture_input.split(".")[0] if capture_input else ":0"
            setup_cmd = (
                f"USER=$(who | grep -E 'tty|pts' | head -1 | awk '{{print $1}}'); "
                f"if [ -n \"$USER\" ]; then "
                f"  XAUTH=$(eval echo ~$USER/.Xauthority); "
                f"  sudo -u \"$USER\" sh -c 'export DISPLAY={display}; xhost +local:'; "
                f"else "
                f"  XAUTH=$HOME/.Xauthority; "
                f"  export DISPLAY={display}; xhost +local:; "
                f"fi; "
                f"echo \"$XAUTH\""
            )
            xauth_val = ""
            try:
                x_stdin, x_stdout, x_stderr = client.exec_command(setup_cmd)
                x_stdout.channel.recv_exit_status()
                xauth_val = x_stdout.read().decode("utf-8", errors="ignore").strip()
                self._append_rec_log(f"[INFO] xhost setup: {xauth_val}")
            except Exception as e:
                self._append_rec_log(f"[WARN] xhost: {e}")
            env_prefix = f"DISPLAY={display} "
            if xauth_val:
                env_prefix += f"XAUTHORITY={shlex.quote(xauth_val)} "

        vf_parts = []
        if resolution:
            sw, sh_h = resolution.split("x")
            vf_parts.append(f"scale={sw}:{sh_h}")

        audio_source = self.audio_source_combo.currentData() or "pulse"
        audio_input = self.audio_device_combo.currentData() or self.audio_device_combo.currentText() or "default"
        audio_codec = self.audio_codec_combo.currentData() or "aac"
        audio_bitrate = self.audio_bitrate_edit.text() or "128k"

        if codec in ("libx264", "libx265"):
            codec_list = ["-c:v", codec, "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"]
        else:
            codec_list = ["-c:v", codec, "-pix_fmt", "yuv420p"]

        host_tag = self.device.host.replace('.', '_').replace(':', '_')
        rec_pid = f"/tmp/dnotool_rec_{host_tag}.pid"
        rec_log = f"/tmp/dnotool_rec_{host_tag}.log"

        self._rec_pid_file = rec_pid

        ffmpeg_args = [
            ffmpeg, "-err_detect", "ignore_err", "-fflags", "+genpts",
        ]

        if capture_type == "v4l2":
            input_format_val = input_format or "mjpeg"
            ffmpeg_args += ["-f", "v4l2", "-input_format", input_format_val,
                           "-video_size", resolution, "-framerate", str(fps),
                           "-i", capture_input]
        elif capture_type == "x11grab":
            ffmpeg_args += ["-f", "x11grab", "-framerate", str(fps),
                           "-i", capture_input or ":0.0"]
        else:
            ffmpeg_args += ["-f", capture_type, "-i", capture_input]

        if self._audio_group.isChecked():
            ffmpeg_args += ["-f", audio_source, "-ac", "2", "-ar", "48000", "-i", audio_input]

        ffmpeg_args += codec_list

        if vf_parts:
            ffmpeg_args += ["-vf", ",".join(vf_parts)]

        ffmpeg_args += ["-b:v", bitrate, "-r", str(fps)]

        if self._audio_group.isChecked():
            ffmpeg_args += ["-c:a", audio_codec, "-b:a", audio_bitrate]
        else:
            ffmpeg_args += ["-an"]

        ffmpeg_args += ["-y", filepath]

        ffmpeg_cmd_str = " ".join(shlex.quote(a) for a in ffmpeg_args)
        self._append_rec_log(f"[INFO] ffmpeg command: {ffmpeg_cmd_str}")

        script_lines = ["#!/bin/bash"]
        if env_prefix:
            for part in env_prefix.strip().split():
                script_lines.append(f"export {part}")
        script_lines.append(f"{ffmpeg_cmd_str} > {shlex.quote(rec_log)} 2>&1 &")
        script_lines.append(f"echo $! > {shlex.quote(rec_pid)}")
        script_content = "\n".join(script_lines) + "\n"
        script_b64 = __import__('base64').b64encode(script_content.encode()).decode()

        rec_script = f"/tmp/dnotool_rec_{host_tag}.sh"

        cmd = f"echo {script_b64} | base64 -d > {shlex.quote(rec_script)} && chmod +x {shlex.quote(rec_script)} && nohup bash {shlex.quote(rec_script)} &"

        self._append_rec_log(f"[INFO] Команда запуска:\n{cmd}")

        self._rec_log_file = rec_log

        try:
            client.exec_command(cmd)
            client.close()
        except Exception as e:
            self._is_recording = False
            self.rec_status_label.setText("🔴 Ошибка запуска")
            self._append_rec_log(f"[ERROR] Ошибка запуска записи: {e}")
            self._update_buttons()
            return

        self._rec_info_timer.start(2000)
        self._append_rec_log("[INFO] Запись запущена на удалённом сервере")

        QTimer.singleShot(3000, self._fetch_remote_log)

    def _stop_recording(self) -> None:
        if not self._is_recording:
            return

        self._append_rec_log("[INFO] Остановка записи на сервере...")
        self._is_recording = False
        self._rec_info_timer.stop()

        rec_pid = getattr(self, '_rec_pid_file', '')
        filepath = getattr(self, '_rec_filepath', '')
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device)
            kill_cmd = ""
            if rec_pid:
                rec_script = rec_pid.replace('.pid', '.sh')
                kill_cmd = (
                    f'PID=$(cat {shlex.quote(rec_pid)} 2>/dev/null); '
                    f'[ -n "$PID" ] && kill $PID 2>/dev/null; '
                    f'rm -f {shlex.quote(rec_pid)} {shlex.quote(rec_script)}; '
                    f'echo "Stopped PID=$PID"'
                )
            elif filepath:
                kill_cmd = (
                    f'PID=$(pgrep -f {shlex.quote(filepath)} 2>/dev/null | head -1); '
                    f'[ -n "$PID" ] && kill $PID 2>/dev/null; '
                    f'echo "Stopped PID=$PID"'
                )
            if kill_cmd:
                client.exec_command(kill_cmd)
                client.close()
                self._append_rec_log("[INFO] Сигнал остановки отправлен")
        except Exception as e:
            self._append_rec_log(f"[WARN] Не удалось отправить сигнал остановки: {e}")

        self.rec_status_label.setText("⏹ Запись остановлена")
        self._update_buttons()
        self._update_status_label(self._stream_manager.is_running or self._gst_manager.is_running)

    def _on_rec_output(self) -> None:
        pass

    def _on_rec_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        pass

    def _update_rec_info(self) -> None:
        filepath = getattr(self, '_rec_filepath', '')
        if not filepath or not self._is_recording:
            return

        rec_log_file = getattr(self, '_rec_log_file', '')
        rec_pid_file = getattr(self, '_rec_pid_file', '')
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device, timeout=5)

            stdin_s, stdout_s, stderr_s = client.exec_command(
                f'stat -c "%s" {shlex.quote(filepath)} 2>/dev/null || echo 0'
            )
            size_str = stdout_s.read().decode("utf-8", errors="ignore").strip()
            size = int(size_str) if size_str.isdigit() else 0

            if rec_pid_file:
                stdin_p, stdout_p, _ = client.exec_command(
                    f'PID=$(cat {shlex.quote(rec_pid_file)} 2>/dev/null); '
                    f'if [ -n "$PID" ]; then '
                    f'  if kill -0 $PID 2>/dev/null; then echo "alive"; else echo "dead"; fi; '
                    f'else echo "noping"; fi'
                )
                pid_status = stdout_p.read().decode("utf-8", errors="ignore").strip()
            else:
                pid_status = "noping"

            if pid_status == "dead" and rec_log_file:
                stdin_l, stdout_l, _ = client.exec_command(
                    f'tail -30 {shlex.quote(rec_log_file)} 2>/dev/null'
                )
                log_tail = stdout_l.read().decode("utf-8", errors="ignore").strip()
                if log_tail:
                    for line in log_tail.splitlines():
                        if line.strip():
                            self._append_rec_log(f"[REMOTE] {line.strip()}")
                self._is_recording = False
                self._rec_info_timer.stop()
                self.rec_status_label.setText("🔴 Запись упала (процесс завершён)")
                self._update_buttons()
                self._update_status_label(self._stream_manager.is_running or self._gst_manager.is_running)
                client.close()
                return
            elif pid_status == "dead":
                self._append_rec_log("[WARN] ffmpeg процесс завершён, но лог недоступен")
                self._is_recording = False
                self._rec_info_timer.stop()
                self.rec_status_label.setText("🔴 Запись упала")
                self._update_buttons()
                self._update_status_label(self._stream_manager.is_running or self._gst_manager.is_running)
                client.close()
                return

            client.close()

            if size >= 1024 * 1024 * 1024:
                self.rec_size_label.setText(f"{size / (1024*1024*1024):.1f} ГБ")
            elif size >= 1024 * 1024:
                self.rec_size_label.setText(f"{size / (1024*1024):.1f} МБ")
            elif size >= 1024:
                self.rec_size_label.setText(f"{size / 1024:.1f} КБ")
            else:
                self.rec_size_label.setText(f"{size} Б")
        except Exception:
            self.rec_size_label.setText("—")

        if self._is_recording and hasattr(self, '_rec_start_time'):
            duration = time.time() - self._rec_start_time
            mins, secs = divmod(int(duration), 60)
            self.rec_duration_label.setText(f"{mins}:{secs:02d}")

    def _append_rec_log(self, line: str) -> None:
        kind = None
        if line.startswith("[ERROR]"):
            kind = "error"
        elif line.startswith("[WARN]"):
            kind = "warning"
        elif line.startswith("[INFO]"):
            kind = "info"
        self._rec_log_lines.append((line, kind))

    def _show_stream_log_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Лог FFmpeg (стрим)")
        dlg.setMinimumSize(700, 400)
        dlg_layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setAcceptRichText(True)
        for line, kind in self._log_lines:
            safe = html_module.escape(line)
            if kind == "error" or line.startswith("[ERROR]"):
                text.append(f'<span style="color:#d32f2f;">{safe}</span>')
            elif kind == "warning":
                text.append(f'<span style="color:#f57c00;">{safe}</span>')
            else:
                text.append(safe)
        sb = text.verticalScrollBar()
        sb.setValue(sb.maximum())
        dlg_layout.addWidget(text)
        btn = QPushButton("Закрыть")
        btn.clicked.connect(dlg.accept)
        dlg_layout.addWidget(btn)
        dlg.exec()

    def _fetch_remote_log(self) -> None:
        rec_log_file = getattr(self, '_rec_log_file', '')
        if not rec_log_file:
            self._append_rec_log("[WARN] Файл лога на сервере не задан")
            return
        try:
            from src.workers.command.ssh import SSHWorker
            worker = SSHWorker()
            client = worker.get_client(self.device, timeout=5)
            stdin_l, stdout_l, _ = client.exec_command(
                f'tail -100 {shlex.quote(rec_log_file)} 2>/dev/null'
            )
            log_tail = stdout_l.read().decode("utf-8", errors="ignore").strip()
            client.close()
            if log_tail:
                self._append_rec_log("[INFO] --- Лог с сервера (последние 100 строк) ---")
                for line in log_tail.splitlines():
                    if line.strip():
                        self._append_rec_log(f"[REMOTE] {line.strip()}")
                self._append_rec_log("[INFO] --- Конец лога с сервера ---")
            else:
                self._append_rec_log("[INFO] Лог на сервере пуст")
        except Exception as e:
            self._append_rec_log(f"[ERROR] Не удалось прочитать лог с сервера: {e}")

    def _show_rec_log_dialog(self) -> None:
        self._fetch_remote_log()

        dlg = QDialog(self)
        dlg.setWindowTitle("Лог записи")
        dlg.setMinimumSize(700, 400)
        dlg_layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setAcceptRichText(True)
        for line, kind in self._rec_log_lines:
            safe = html_module.escape(line)
            if kind == "error" or line.startswith("[ERROR]"):
                text.append(f'<span style="color:#d32f2f;">{safe}</span>')
            elif kind == "warning":
                text.append(f'<span style="color:#f57c00;">{safe}</span>')
            elif kind == "info":
                text.append(f'<span style="color:#388e3c;">{safe}</span>')
            else:
                text.append(safe)
        sb = text.verticalScrollBar()
        sb.setValue(sb.maximum())
        dlg_layout.addWidget(text)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Обновить с сервера")
        def _refresh():
            self._fetch_remote_log()
            text.clear()
            for line, kind in self._rec_log_lines:
                safe = html_module.escape(line)
                if kind == "error" or line.startswith("[ERROR]"):
                    text.append(f'<span style="color:#d32f2f;">{safe}</span>')
                elif kind == "warning":
                    text.append(f'<span style="color:#f57c00;">{safe}</span>')
                elif kind == "info":
                    text.append(f'<span style="color:#388e3c;">{safe}</span>')
                else:
                    text.append(safe)
            sb.setValue(sb.maximum())
        refresh_btn.clicked.connect(_refresh)
        btn_row.addWidget(refresh_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dlg.exec()
