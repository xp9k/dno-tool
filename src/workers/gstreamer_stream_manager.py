"""
GStreamer Stream Manager - Управление удалённым GStreamer-стримингом.

Подключается к устройству по SSH, запускает gst-launch-1.0 на удалённом хосте
для захвата экрана/камеры и стриминга по TCP.
При наличии gst-rtsp-launch поддерживает RTSP-протокол.
"""

import base64
import json
import os
import shlex
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from src.domain.models import DeviceModel
from src.logger import logger


_GST_PACKAGES = [
    "gstreamer1.0-tools",
    "gstreamer1.0-plugins-base",
    "gstreamer1.0-plugins-good",
    "gstreamer1.0-plugins-bad",
    "gstreamer1.0-plugins-ugly",
    "gstreamer1.0-rtsp-server",
    "gstreamer1.0-libav",
    "gstreamer1.0-pulse",
    "gstreamer1.0-vaapi",
    "lib64gstrtspserver-gir1.0",
    "python3-gobject",
]

_GST_ELEMENT_PACKAGES = {
    "ximagesrc": "gstreamer1.0-plugins-base",
    "v4l2src": "gstreamer1.0-plugins-good",
    "videoscale": "gstreamer1.0-plugins-base",
    "videoconvert": "gstreamer1.0-plugins-base",
    "videorate": "gstreamer1.0-plugins-base",
    "jpegdec": "gstreamer1.0-plugins-good",
    "x264enc": "gstreamer1.0-plugins-ugly",
    "x265enc": "gstreamer1.0-plugins-bad",
    "avenc_mpeg2video": "gstreamer1.0-libav",
    "avenc_mpeg4": "gstreamer1.0-libav",
    "avenc_aac": "gstreamer1.0-libav",
    "lamemp3enc": "gstreamer1.0-plugins-ugly",
    "opusenc": "gstreamer1.0-plugins-base",
    "pulsesrc": "gstreamer1.0-pulse",
    "alsasrc": "gstreamer1.0-plugins-base",
    "videocrop": "gstreamer1.0-plugins-good",
    "mpegtsmux": "gstreamer1.0-plugins-bad",
    "tcpserversink": "gstreamer1.0-plugins-base",
    "rtph264pay": "gstreamer1.0-plugins-good",
    "rtph265pay": "gstreamer1.0-plugins-bad",
    "rtpmp4apay": "gstreamer1.0-plugins-good",
    "rtpmp4vpay": "gstreamer1.0-plugins-good",
    "rtpmpvpay": "gstreamer1.0-plugins-good",
    "rtpmpapay": "gstreamer1.0-plugins-good",
    "rtpopuspay": "gstreamer1.0-plugins-base",
}


class GStreamerStreamManager(QObject):
    """
    Управляет GStreamer-стримингом на удалённом хосте.
    TCP-стрим через gst-launch-1.0; RTSP через gst-rtsp-launch (если доступен).

    Сигналы:
        started(str): device_iid
        stopped(str): device_iid
        output(str, str): device_iid, line
        error(str, str): device_iid, message
        gst_install_finished(str, bool, str): device_iid, success, message
    """

    started = Signal(str)
    stopped = Signal(str)
    output = Signal(str, str)
    error = Signal(str, str)
    gst_install_finished = Signal(str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device: Optional[DeviceModel] = None
        self._settings: dict = {}
        self._is_running = False
        self._client = None
        self._stdout = None
        self._stderr = None
        self._stdin = None
        self._read_threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._finished_streams = 0
        self._stopped_emitted = False
        self._has_rtsp_launch: bool = False

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._is_running

    @property
    def device_iid(self) -> str:
        return self._device.iid if self._device else ""

    @property
    def has_rtsp_launch(self) -> bool:
        return self._has_rtsp_launch

    def _get_ssh_client(self, device: DeviceModel, timeout: int = 10):
        from src.workers.command.ssh import SSHWorker
        worker = SSHWorker()
        return worker.get_client(device, timeout=timeout)

    # ------------------------------------------------------------------
    # Установка GStreamer
    # ------------------------------------------------------------------
    def install_gstreamer(self, device: DeviceModel) -> None:
        def _do_install():
            try:
                client = self._get_ssh_client(device, timeout=10)

                # Ищем недостающие GIR-пакеты для typelib
                needed_typelibs = [
                    "Gst-1.0",
                    "GstRtsp-1.0",
                    "GstRtspServer-1.0",
                ]
                gir_extra = []
                for tl in needed_typelibs:
                    tl_file = f"{tl}.typelib"
                    # Проверяем наличие typelib
                    stdin, stdout, stderr = client.exec_command(
                        f"find /usr/lib64/girepository-1.0 /usr/lib/girepository-1.0"
                        f" -name '{tl_file}' 2>/dev/null | head -1"
                    )
                    stdout.channel.recv_exit_status()
                    found = stdout.read().decode("utf-8", errors="ignore").strip()
                    if found:
                        self.output.emit(device.iid, f"[INFO] {tl_file} найден: {found}")
                        continue
                    # Ищем пакет через rpm/dnf
                    for search_cmd in [
                        f"rpm -qf /usr/lib64/girepository-1.0/{tl_file} 2>/dev/null",
                        f"rpm -qf /usr/lib/girepository-1.0/{tl_file} 2>/dev/null",
                        f"dnf repoquery --whatprovides '*/{tl_file}' 2>/dev/null | head -3",
                    ]:
                        stdin, stdout, stderr = client.exec_command(search_cmd)
                        stdout.channel.recv_exit_status()
                        result = stdout.read().decode("utf-8", errors="ignore").strip()
                        if result:
                            pkg = result.splitlines()[0].strip()
                            # Берём базовое имя пакета (N-V-R → N)
                            base = pkg.rsplit("-", 2)[0] if "-" in pkg else pkg
                            if base and base not in gir_extra and base not in _GST_PACKAGES:
                                gir_extra.append(base)
                                self.output.emit(device.iid, f"[INFO] Найден пакет для {tl_file}: {base}")
                            break
                    else:
                        self.output.emit(
                            device.iid,
                            f"[WARN] Пакет для {tl_file} не найден — RTSP может быть недоступен"
                        )

                packages = list(_GST_PACKAGES) + gir_extra
                cmd = "dnf install -y " + " ".join(packages)
                self.output.emit(device.iid, f"[INFO] Установка: {cmd}")
                stdin, stdout, stderr = client.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode("utf-8", errors="ignore").strip()
                err_out = stderr.read().decode("utf-8", errors="ignore").strip()
                client.close()
                if exit_status == 0:
                    self.gst_install_finished.emit(device.iid, True, "GStreamer установлен")
                else:
                    self.gst_install_finished.emit(
                        device.iid, False,
                        f"dnf завершился с кодом {exit_status}: {err_out or out}"
                    )
            except Exception as e:
                self.gst_install_finished.emit(device.iid, False, f"Ошибка установки: {e}")

        threading.Thread(target=_do_install, daemon=True).start()

    # ------------------------------------------------------------------
    # Запуск стриминга
    # ------------------------------------------------------------------
    def start(self, device: DeviceModel, settings: dict) -> bool:
        with self._state_lock:
            if self._is_running:
                logger.warning("GStreamerStreamManager: Already running")
                return False
            self._is_running = True
            self._finished_streams = 0
            self._stopped_emitted = False

        self._stop_event.clear()
        self._device = device
        self._settings = settings

        try:
            self._client = self._get_ssh_client(device)
        except Exception as e:
            self.error.emit(self.device_iid, f"SSH ошибка: {e}")
            self._reset_state()
            return False

        if not self._check_gstreamer_sync():
            self._cleanup_client()
            self._reset_state()
            return False

        try:
            transport = self._settings.get("transport", "rtsp")
            if transport == "rtsp":
                if not self._has_rtsp_launch:
                    self.error.emit(
                        self.device_iid,
                        "RTSP недоступен: GstRtspServer не найден. "
                        "Установите пакеты через кнопку «Установить»."
                    )
                    self._cleanup_client()
                    self._reset_state()
                    return False
                cmd = self._build_rtsp_pipeline_and_cmd()
            else:
                cmd = self._build_remote_cmd()
        except Exception as e:
            self.error.emit(self.device_iid, f"Ошибка формирования команды: {e}")
            self._cleanup_client()
            self._reset_state()
            return False

        logger.info(f"GStreamerStreamManager: Starting stream on {device.host}")
        logger.debug(f"GStreamerStreamManager: Command: {cmd}")

        try:
            capture_type = settings.get("capture_type", "")
            if capture_type == "x11grab":
                self._setup_xhost()

            self._stdin, self._stdout, self._stderr = self._client.exec_command(cmd)

            self._read_threads = [
                threading.Thread(target=self._read_loop, args=(self._stdout, "STDOUT", False), daemon=True),
                threading.Thread(target=self._read_loop, args=(self._stderr, "STDERR", True), daemon=True),
            ]
            for t in self._read_threads:
                t.start()

            self.started.emit(self.device_iid)
            return True
        except Exception as e:
            self.error.emit(self.device_iid, f"Ошибка запуска: {e}")
            self._cleanup_client()
            self._reset_state()
            return False

    def _check_gstreamer_sync(self) -> bool:
        """Проверка gst-launch-1.0, gst-rtsp-launch и элементов пайплайна."""
        try:
            # gst-launch-1.0
            stdin, stdout, stderr = self._client.exec_command("gst-launch-1.0 --version")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                self.error.emit(self.device_iid, "gst-launch-1.0 не найден. Установите gstreamer1.0-tools.")
                return False
            ver = stdout.read().decode("utf-8", errors="ignore").strip()
            self.output.emit(self.device_iid, f"[INFO] {ver.splitlines()[0] if ver else 'gst-launch-1.0 найден'}")

            # RTSP: проверяем python3 + gi.repository.GstRtspServer через base64-скрипт
            check_script = (
                "import gi\n"
                "gi.require_version('GstRtsp','1.0')\n"
                "gi.require_version('GstRtspServer','1.0')\n"
                "from gi.repository import GstRtsp, GstRtspServer\n"
            )
            check_b64 = base64.b64encode(check_script.encode()).decode()
            gi_typelib = "GI_TYPELIB_PATH=/usr/lib64/girepository-1.0:/usr/lib/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
            stdin, stdout, stderr = self._client.exec_command(
                f"echo '{check_b64}' | base64 -d | {gi_typelib} python3 >/dev/null 2>&1; echo $?"
            )
            exit_status = stdout.channel.recv_exit_status()
            rc = stdout.read().decode("utf-8", errors="ignore").strip()
            self._has_rtsp_launch = rc == "0"
            if self._has_rtsp_launch:
                self.output.emit(self.device_iid, "[INFO] GstRtspServer (Python GI) найден — RTSP доступен")
            else:
                # Диагностика: какие Gst* typelib есть на машине
                stdin2, stdout2, stderr2 = self._client.exec_command(
                    "ls /usr/lib64/girepository-1.0/Gst* 2>/dev/null;"
                    "ls /usr/lib/girepository-1.0/Gst* 2>/dev/null"
                )
                stdout2.channel.recv_exit_status()
                diag = stdout2.read().decode("utf-8", errors="ignore").strip()
                self.output.emit(
                    self.device_iid,
                    f"[INFO] GstRtspServer не найден — RTSP недоступен\nДоступные typelib:\n{diag}"
                )

            # Если выбран RTSP но gst-rtsp-launch нет — ошибка
            transport = self._settings.get("transport", "tcp")
            if transport == "rtsp" and not self._has_rtsp_launch:
                self.error.emit(
                    self.device_iid,
                    "RTSP недоступен: GstRtspServer не найден. "
                    "Переключите протокол на TCP или установите пакеты "
                    "gstreamer1.0-rtsp-server, lib64gstrtspserver-gir1.0, python3-gobject."
                )
                return False

            # Элементы пайплайна
            required_elements = self._get_pipeline_elements(transport)
            missing = []
            for elem in required_elements:
                stdin, stdout, stderr = self._client.exec_command(
                    f"gst-inspect-1.0 {shlex.quote(elem)} >/dev/null 2>&1; echo $?"
                )
                stdout.channel.recv_exit_status()
                rc = stdout.read().decode("utf-8", errors="ignore").strip()
                if rc != "0":
                    pkg = _GST_ELEMENT_PACKAGES.get(elem, "неизвестный пакет")
                    missing.append(f"{elem} (пакет {pkg})")

            if missing:
                self.error.emit(
                    self.device_iid,
                    "Отсутствуют элементы GStreamer:\n"
                    + "\n".join(f"  • {m}" for m in missing)
                    + "\nНажмите «Установить» для установки недостающих пакетов."
                )
                return False

            self.output.emit(self.device_iid, f"[INFO] Все элементы пайплайна найдены ({len(required_elements)} шт.)")
            return True
        except Exception as e:
            self.error.emit(self.device_iid, f"Ошибка проверки GStreamer: {e}")
            return False

    def _get_pipeline_elements(self, transport: str = "tcp") -> list[str]:
        capture_type = self._settings.get("capture_type", "x11grab")
        codec = self._settings.get("codec", "libx264")
        input_format = self._settings.get("input_format", "") or "mjpeg"
        enable_audio = self._settings.get("enable_audio", False)
        audio_source = self._settings.get("audio_source", "pulse") or "pulse"
        audio_codec = self._settings.get("audio_codec", "aac") or "aac"

        elements = []

        if capture_type == "x11grab":
            elements += ["ximagesrc", "videoscale", "videoconvert"]
            if self._settings.get("crop_left") is not None:
                elements.append("videocrop")
        elif capture_type == "v4l2":
            elements.append("v4l2src")
            if input_format == "mjpeg":
                elements.append("jpegdec")
            elements.append("videoconvert")

        if codec in ("libx264", "h264"):
            elements.append("x264enc")
            if transport == "rtsp":
                elements.append("rtph264pay")
        elif codec in ("libx265", "h265", "hevc"):
            elements.append("x265enc")
            if transport == "rtsp":
                elements.append("rtph265pay")
        elif codec == "mpeg2video":
            elements.append("avenc_mpeg2video")
            if transport == "rtsp":
                elements.append("rtpmpvpay")
        elif codec == "mpeg4":
            elements.append("avenc_mpeg4")
            if transport == "rtsp":
                elements.append("rtpmp4vpay")

        # Синкинг
        if transport == "tcp":
            elements += ["mpegtsmux", "tcpserversink"]
        elif transport == "http":
            elements += ["mpegtsmux", "tcpclientsink"]

        # Аудио
        if enable_audio:
            elements.append("pulsesrc" if audio_source == "pulse" else "alsasrc")
            if audio_codec in ("aac",):
                elements.append("avenc_aac")
                if transport == "rtsp":
                    elements.append("rtpmp4apay")
            elif audio_codec in ("libmp3lame", "mp3"):
                elements.append("lamemp3enc")
                if transport == "rtsp":
                    elements.append("rtpmpapay")
            elif audio_codec in ("opus", "libopus"):
                elements.append("opusenc")
                if transport == "rtsp":
                    elements.append("rtpopuspay")

        return list(dict.fromkeys(elements))

    def _setup_xhost(self) -> None:
        capture_input = self._settings.get("capture_input", "")
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
            f"echo \"XAUTH=$XAUTH\""
        )
        try:
            x_stdin, x_stdout, x_stderr = self._client.exec_command(setup_cmd)
            x_stdout.channel.recv_exit_status()
            xauth_line = x_stdout.read().decode("utf-8", errors="ignore").strip()
            if xauth_line.startswith("XAUTH="):
                self._settings["xauthority"] = xauth_line[6:]
        except Exception as e:
            logger.warning(f"GStreamerStreamManager: xhost command failed (non-critical): {e}")

    def stop(self) -> None:
        with self._state_lock:
            if not self._is_running:
                return
            self._is_running = False

        logger.info(f"GStreamerStreamManager: Stopping stream on {self._device.host}")
        self._stop_event.set()

        for stream in (self._stdout, self._stderr, self._stdin):
            if stream:
                try:
                    stream.channel.close()
                except Exception:
                    pass

        for t in self._read_threads:
            t.join(timeout=3.0)

        self._kill_remote_gst_async()
        self._cleanup_client()
        self._try_emit_stopped()

    def _kill_remote_gst_async(self) -> None:
        device = self._device
        settings = self._settings.copy() if self._settings else {}

        def _do_kill():
            try:
                port = settings.get("port", 8080)
                pid_file = f"/tmp/dnotool_gst_{port}.pid"
                stop_file = f"/tmp/dnotool_gst_stop_{port}"
                from src.workers.command.ssh import SSHWorker
                worker = SSHWorker()
                client = worker.get_client(device, timeout=5)
                client.exec_command(
                    f"touch {stop_file}; "
                    f"PID=$(cat {pid_file} 2>/dev/null); "
                    f"[ -n \"$PID\" ] && kill $PID 2>/dev/null; "
                    f"rm -f {pid_file}"
                )
                client.close()
            except Exception as e:
                logger.debug(f"GStreamerStreamManager: Remote kill error (non-critical): {e}")

        kill_thread = threading.Thread(target=_do_kill, daemon=True)
        kill_thread.start()

    def _reset_state(self) -> None:
        with self._state_lock:
            self._is_running = False
            self._finished_streams = 0
            self._stopped_emitted = False

    @staticmethod
    def _parse_bitrate_to_kbps(bitrate: str) -> int:
        bitrate = bitrate.strip().upper()
        multipliers = {"K": 1, "M": 1000, "G": 1000000}
        if bitrate and bitrate[-1] in multipliers:
            try:
                return int(float(bitrate[:-1]) * multipliers[bitrate[-1]])
            except ValueError:
                pass
        try:
            return int(float(bitrate))
        except ValueError:
            return 2000

    def _build_rtsp_pipeline_and_cmd(self) -> str:
        """Собрать конфиг для RTSP скрипта и вернуть команду запуска."""
        capture_type = self._settings.get("capture_type", "x11grab")
        capture_input = self._settings.get("capture_input", "")
        codec = self._settings.get("codec", "libx264")
        resolution = self._settings.get("resolution", "1920x1080")
        bitrate = self._settings.get("bitrate", "2M") or "2M"
        fps = self._settings.get("fps", 25)
        port = self._settings.get("port", 8554)

        bitrate_kbps = self._parse_bitrate_to_kbps(bitrate)

        if not capture_input:
            if capture_type == "x11grab":
                capture_input = ":0.0"
            elif capture_type == "v4l2":
                capture_input = "/dev/video0"

        sw, sh = resolution.split("x")

        config = {
            "capture_type": capture_type,
            "capture_input": capture_input,
            "input_format": self._settings.get("input_format", "") or "mjpeg",
            "codec": codec,
            "width": int(sw),
            "height": int(sh),
            "fps": int(fps),
            "bitrate_kbps": bitrate_kbps,
        }

        monitor_x = self._settings.get("monitor_x_offset")
        monitor_y = self._settings.get("monitor_y_offset")
        monitor_w = self._settings.get("monitor_width")
        monitor_h = self._settings.get("monitor_height")
        crop_left = self._settings.get("crop_left")
        crop_top = self._settings.get("crop_top")
        crop_right = self._settings.get("crop_right")
        crop_bottom = self._settings.get("crop_bottom")
        if monitor_x is not None:
            config["monitor_x_offset"] = monitor_x
        if monitor_y is not None:
            config["monitor_y_offset"] = monitor_y
        if monitor_w is not None:
            config["monitor_width"] = monitor_w
        if monitor_h is not None:
            config["monitor_height"] = monitor_h
        if crop_left is not None:
            config["crop_left"] = crop_left
        if crop_top is not None:
            config["crop_top"] = crop_top
        if crop_right is not None:
            config["crop_right"] = crop_right
        if crop_bottom is not None:
            config["crop_bottom"] = crop_bottom

        enable_audio = self._settings.get("enable_audio", False)
        if enable_audio:
            audio_source = self._settings.get("audio_source", "pulse") or "pulse"
            audio_codec = self._settings.get("audio_codec", "aac") or "aac"
            config["audio"] = True
            config["audio_source"] = audio_source
            config["audio_codec"] = audio_codec
        else:
            config["audio"] = False

        config_json = json.dumps(config)

        env_prefix = ""
        if capture_type == "x11grab":
            display = capture_input.split(".")[0] if capture_input else ":0"
            env_prefix = f"export DISPLAY={display}; "
            xauth = self._settings.get("xauthority", "")
            if xauth:
                env_prefix += f"export XAUTHORITY={shlex.quote(xauth)}; "

        pid_file = f"/tmp/dnotool_gst_{port}.pid"
        stop_file = f"/tmp/dnotool_gst_stop_{port}"

        return self._build_rtsp_script_cmd(config_json, port, env_prefix, pid_file, stop_file)

    def _build_remote_cmd(self) -> str:
        capture_type = self._settings.get("capture_type", "x11grab")
        capture_input = self._settings.get("capture_input", "")
        codec = self._settings.get("codec", "libx264")
        resolution = self._settings.get("resolution", "1920x1080")
        bitrate = self._settings.get("bitrate", "2M") or "2M"
        fps = self._settings.get("fps", 25)
        port = self._settings.get("port", 8080)

        bitrate_kbps = self._parse_bitrate_to_kbps(bitrate)

        if not capture_input:
            if capture_type == "x11grab":
                capture_input = ":0.0"
            elif capture_type == "v4l2":
                capture_input = "/dev/video0"

        sw, sh = resolution.split("x")

        pipeline_parts = []

        if capture_type == "x11grab":
            display = capture_input.split(".")[0] if capture_input else ":0"
            pipeline_parts.append(f"ximagesrc display-name={display} use-damage=false")
            crop_left = self._settings.get("crop_left")
            crop_top = self._settings.get("crop_top")
            crop_right = self._settings.get("crop_right")
            crop_bottom = self._settings.get("crop_bottom")
            if crop_left is not None:
                pipeline_parts.append(f"video/x-raw,framerate={fps}/1")
                pipeline_parts.append(
                    f"videocrop left={int(crop_left)} top={int(crop_top)}"
                    f" right={int(crop_right)} bottom={int(crop_bottom)}"
                )
                pipeline_parts.append("videoconvert")
            else:
                pipeline_parts.append(f"video/x-raw,framerate={fps}/1")
            pipeline_parts.append("videoscale")
            pipeline_parts.append(f"video/x-raw,width={sw},height={sh}")
            pipeline_parts.append("videoconvert")
        elif capture_type == "v4l2":
            input_format = self._settings.get("input_format", "") or "mjpeg"
            pipeline_parts.append(f"v4l2src device={capture_input}")
            if input_format == "mjpeg":
                pipeline_parts.append(f"image/jpeg,framerate={fps}/1,width={sw},height={sh}")
                pipeline_parts.append("jpegdec")
                pipeline_parts.append("videoconvert")
            elif input_format in ("yuyv422", "yuyv"):
                pipeline_parts.append(f"video/x-raw,format=YUY2,framerate={fps}/1,width={sw},height={sh}")
                pipeline_parts.append("videoconvert")
            else:
                pipeline_parts.append(f"video/x-raw,framerate={fps}/1,width={sw},height={sh}")
                pipeline_parts.append("videoconvert")

        pipeline_parts.append(f"video/x-raw,format=I420,width={sw},height={sh},framerate={fps}/1")

        if codec in ("libx264", "h264"):
            pipeline_parts.append(f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps}")
            pipeline_parts.append("video/x-h264,stream-format=byte-stream")
        elif codec in ("libx265", "h265", "hevc"):
            pipeline_parts.append(f"x265enc tune=zerolatency bitrate={bitrate_kbps}")
            pipeline_parts.append("video/x-h265,stream-format=byte-stream")
        elif codec == "mpeg2video":
            pipeline_parts.append(f"avenc_mpeg2video bitrate={bitrate_kbps * 1000}")
            pipeline_parts.append("video/mpeg,mpegversion=2")
        elif codec == "mpeg4":
            pipeline_parts.append(f"avenc_mpeg4 bitrate={bitrate_kbps * 1000}")
            pipeline_parts.append("video/mpeg,mpegversion=4")
        else:
            pipeline_parts.append(f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps}")
            pipeline_parts.append("video/x-h264,stream-format=byte-stream")

        enable_audio = self._settings.get("enable_audio", False)
        if enable_audio:
            audio_source = self._settings.get("audio_source", "pulse") or "pulse"
            audio_input = self._settings.get("audio_input", "default") or "default"
            audio_codec = self._settings.get("audio_codec", "aac") or "aac"

            if audio_source == "pulse":
                pipeline_parts.append(f"pulsesrc device={shlex.quote(audio_input)}")
            else:
                pipeline_parts.append(f"alsasrc device={shlex.quote(audio_input)}")
            pipeline_parts.append("audio/x-raw,rate=48000,channels=2")

            if audio_codec in ("aac",):
                pipeline_parts.append("avenc_aac")
                pipeline_parts.append("audio/mpeg")
            elif audio_codec in ("libmp3lame", "mp3"):
                pipeline_parts.append("lamemp3enc")
                pipeline_parts.append("audio/mpeg")
            elif audio_codec in ("opus", "libopus"):
                pipeline_parts.append("opusenc")
                pipeline_parts.append("audio/x-opus")
            else:
                pipeline_parts.append("avenc_aac")
                pipeline_parts.append("audio/mpeg")

        # Синк
        pipeline_parts.append("mpegtsmux")
        pipeline_parts.append(f"tcpserversink port={port} host=0.0.0.0")

        pipeline_str = " ! ".join(pipeline_parts)

        env_prefix = ""
        if capture_type == "x11grab":
            display = capture_input.split(".")[0] if capture_input else ":0"
            env_prefix = f"export DISPLAY={display}; "
            xauth = self._settings.get("xauthority", "")
            if xauth:
                env_prefix += f"export XAUTHORITY={shlex.quote(xauth)}; "

        pid_file = f"/tmp/dnotool_gst_{port}.pid"
        stop_file = f"/tmp/dnotool_gst_stop_{port}"
        pipeline_file = f"/tmp/dnotool_gst_pipeline_{port}"

        # Escape single quotes in pipeline for shell
        pipeline_escaped = pipeline_str.replace("'", "'\\''")

        cmd = (
            f"echo '{pipeline_escaped}' > {pipeline_file}; "
            f"trap 'kill $PID 2>/dev/null; rm -f {pid_file} {stop_file} {pipeline_file}; exit' HUP INT TERM EXIT; "
            f"fail=0; while ! [ -f {stop_file} ]; do "
            f"{env_prefix}gst-launch-1.0 -e $(cat {pipeline_file}) & "
            f"PID=$!; echo $PID > {pid_file}; "
            f"s=$(date +%s); wait $PID; e=$(date +%s); "
            f"if [ $((e - s)) -lt 3 ]; then fail=$((fail+1)); else fail=0; fi; "
            f"if [ $fail -ge 3 ]; then break; fi; "
            f"sleep 1; "
            f"done; rm -f {stop_file} {pid_file} {pipeline_file}"
        )

        return cmd

    @staticmethod
    def _get_rtsp_script_content() -> str:
        script_name = "gst_rtsp_server.py"
        candidates = [
            os.path.join(os.path.dirname(__file__), script_name),
            os.path.join(getattr(sys, '_MEIPASS', ''), 'src', 'workers', script_name),
            os.path.join(getattr(sys, '_MEIPASS', ''), script_name),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
        import importlib.util
        spec = importlib.util.find_spec("src.workers.gst_rtsp_server")
        if spec and spec.origin and os.path.isfile(spec.origin):
            with open(spec.origin, "r", encoding="utf-8") as f:
                return f.read()
        raise FileNotFoundError(
            f"Cannot find {script_name} for RTSP streaming. "
            "The file must be available alongside the application binary."
        )

    def _build_rtsp_script_cmd(self, config_json: str, port: int, env_prefix: str, pid_file: str, stop_file: str) -> str:
        """Сгенерировать команду для запуска RTSP Python-скрипта."""
        remote_script = "/tmp/dnotool_gst_rtsp_server.py"

        script_content = self._get_rtsp_script_content()

        b64 = base64.b64encode(script_content.encode()).decode()

        mount_point = "/stream"
        config_quoted = config_json.replace("'", "'\\''")

        gi_typelib = "GI_TYPELIB_PATH=/usr/lib64/girepository-1.0:/usr/lib/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"

        cmd = (
            f"echo '{b64}' | base64 -d > {remote_script} && chmod +x {remote_script}; "
            f"trap 'kill $PID 2>/dev/null; rm -f {pid_file} {stop_file} {remote_script}; exit' HUP INT TERM EXIT; "
            f"fail=0; while ! [ -f {stop_file} ]; do "
            f"{env_prefix}{gi_typelib} python3 {remote_script} {port} {mount_point} '{config_quoted}' & "
            f"PID=$!; echo $PID > {pid_file}; "
            f"s=$(date +%s); wait $PID; e=$(date +%s); "
            f"if [ $((e - s)) -lt 3 ]; then fail=$((fail+1)); else fail=0; fi; "
            f"if [ $fail -ge 3 ]; then break; fi; "
            f"sleep 1; "
            f"done; rm -f {stop_file} {pid_file} {remote_script}"
        )

        return cmd

    def _read_loop(self, stream, prefix: str, is_stderr: bool = False) -> None:
        channel = stream.channel
        buf = b""
        recv_ready = channel.recv_stderr_ready if is_stderr else channel.recv_ready
        recv = channel.recv_stderr if is_stderr else channel.recv
        try:
            while not self._stop_event.is_set():
                if channel.closed:
                    break
                if recv_ready():
                    try:
                        data = recv(4096)
                    except Exception:
                        break
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("utf-8", errors="ignore").rstrip()
                        if text:
                            if prefix:
                                self.output.emit(self.device_iid, f"[{prefix}] {text}")
                            else:
                                self.output.emit(self.device_iid, text)
                elif channel.exit_status_ready():
                    while recv_ready():
                        data = recv(4096)
                        if not data:
                            break
                        buf += data
                    break
                else:
                    time.sleep(0.05)
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"GStreamerStreamManager: read_loop error ({prefix}): {e}")
        finally:
            if buf:
                text = buf.decode("utf-8", errors="ignore").rstrip()
                if text:
                    if prefix:
                        self.output.emit(self.device_iid, f"[{prefix}] {text}")
                    else:
                        self.output.emit(self.device_iid, text)
            with self._state_lock:
                self._finished_streams += 1
                if self._finished_streams >= 2:
                    self._is_running = False
            self._try_emit_stopped()

    def _try_emit_stopped(self) -> None:
        with self._state_lock:
            if self._stopped_emitted or self._finished_streams < 2:
                return
            self._stopped_emitted = True
        self.stopped.emit(self.device_iid)

    def _cleanup_client(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._stdout = None
        self._stderr = None
        self._stdin = None
        self._read_threads = []