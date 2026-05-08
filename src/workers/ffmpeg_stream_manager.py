"""
FFmpeg Stream Manager - Управление удалённым FFmpeg-сервером.

Подключается к устройству по SSH, запускает FFmpeg на удалённом хосте
для захвата экрана/камеры и стриминга по сети.
"""

import shlex
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal, QMetaObject, Qt
from src.domain.models import DeviceModel
from src.logger import logger


class FFmpegStreamManager(QObject):
    """
    Управляет FFmpeg-сервером на удалённом хосте.

    Сигналы:
        started(str): device_iid — сервер запущен
        stopped(str): device_iid — сервер остановлен
        output(str, str): device_iid, line — строка вывода
        error(str, str): device_iid, message — ошибка
    """

    started = Signal(str)
    stopped = Signal(str)
    output = Signal(str, str)
    error = Signal(str, str)

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

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._is_running

    @property
    def device_iid(self) -> str:
        return self._device.iid if self._device else ""

    def _get_ssh_client(self, device: DeviceModel):
        from src.workers.command.ssh import SSHWorker
        worker = SSHWorker()
        return worker.get_client(device)

    def _check_ffmpeg(self) -> bool:
        ffmpeg = self._settings.get("ffmpeg_path", "ffmpeg") or "ffmpeg"
        try:
            stdin, stdout, stderr = self._client.exec_command(
                f"command -v {shlex.quote(ffmpeg)}"
            )
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                err = stderr.read().decode("utf-8", errors="ignore").strip()
                self.error.emit(
                    self.device_iid,
                    f"ffmpeg ({ffmpeg}) не найден на удалённом хосте: {err}"
                )
                return False
            path = stdout.read().decode("utf-8", errors="ignore").strip()
            self.output.emit(self.device_iid, f"[INFO] ffmpeg найден: {path}")
            return True
        except Exception as e:
            self.error.emit(self.device_iid, f"Ошибка проверки ffmpeg: {e}")
            return False

    def start(self, device: DeviceModel, settings: dict) -> bool:
        with self._state_lock:
            if self._is_running:
                logger.warning("FFmpegStreamManager: Already running")
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

        if not self._check_ffmpeg():
            self._cleanup_client()
            self._reset_state()
            return False

        cmd = self._build_remote_cmd()

        logger.info(f"FFmpegStreamManager: Starting remote ffmpeg server on {device.host}")
        logger.debug(f"FFmpegStreamManager: Command: {cmd}")

        try:
             # Для x11grab находим Xauthority и разрешаем доступ к дисплею
            capture_type = settings.get("capture_type", "")
            if capture_type == "x11grab":
                display = settings.get("capture_input", ":0").split(".")[0] or ":0"
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
                    logger.warning(f"FFmpegStreamManager: xhost command failed (non-critical): {e}")

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

    def stop(self) -> None:
        with self._state_lock:
            if not self._is_running:
                return
            self._is_running = False

        logger.info(f"FFmpegStreamManager: Stopping remote ffmpeg on {self._device.host}")
        self._stop_event.set()

        for stream in (self._stdout, self._stderr, self._stdin):
            if stream:
                try:
                    stream.channel.close()
                except Exception:
                    pass

        for t in self._read_threads:
            t.join(timeout=3.0)

        self._kill_remote_ffmpeg_async()

        self._cleanup_client()
        self._try_emit_stopped()

    def _kill_remote_ffmpeg_async(self) -> None:
        """Отправить kill-команду через SSH в фоновом потоке (не блокирует UI)."""
        device = self._device
        settings = self._settings.copy() if self._settings else {}

        def _do_kill():
            try:
                port = settings.get("port", 8080)
                pid_file = f"/tmp/pyktool_ffmpeg_{port}.pid"
                stop_file = f"/tmp/pyktool_ffmpeg_stop_{port}"
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
                logger.debug(f"FFmpegStreamManager: Remote kill error (non-critical): {e}")

        kill_thread = threading.Thread(target=_do_kill, daemon=True)
        kill_thread.start()

    def _reset_state(self) -> None:
        with self._state_lock:
            self._is_running = False
            self._finished_streams = 0
            self._stopped_emitted = False

    def _build_remote_cmd(self) -> str:
        ffmpeg = self._settings.get("ffmpeg_path", "ffmpeg") or "ffmpeg"
        capture_type = self._settings.get("capture_type", "x11grab")
        capture_input = self._settings.get("capture_input", "")
        codec = self._settings.get("codec", "libx264")
        resolution = self._settings.get("resolution", "1920x1080")
        bitrate = self._settings.get("bitrate", "2M") or "2M"
        fps = self._settings.get("fps", 25)
        port = self._settings.get("port", 8080)
        transport = self._settings.get("transport", "tcp")

        if not capture_input:
            if capture_type == "x11grab":
                capture_input = ":0.0"
            elif capture_type == "v4l2":
                capture_input = "/dev/video0"
            elif capture_type == "gdigrab":
                capture_input = "desktop"
            elif capture_type == "avfoundation":
                capture_input = "0"

        if transport == "tcp":
            output = f"-f mpegts tcp://0.0.0.0:{port}?listen"
        elif transport == "udp":
            # Multicast для UDP — более надёжно чем broadcast
            output = f"-f mpegts udp://239.255.0.1:{port}"
        elif transport == "http":
            output = f"-listen 1 -f mpegts http://0.0.0.0:{port}/stream.ts"
        elif transport == "rtsp":
            output = f"-rtsp_flags listen -f rtsp rtsp://0.0.0.0:{port}/stream"
        else:
            output = f"-f mpegts tcp://0.0.0.0:{port}?listen"

        extra_flags = f"-framerate {fps}"
        if capture_type == "v4l2":
            extra_flags += f" -video_size {resolution}"
            extra_flags += " -thread_queue_size 512"
            input_format = self._settings.get("input_format", "") or "mjpeg"
            extra_flags += f" -input_format {input_format}"
        elif capture_type == "x11grab":
            monitor_w = self._settings.get("monitor_width")
            monitor_h = self._settings.get("monitor_height")
            monitor_x = self._settings.get("monitor_x_offset")
            monitor_y = self._settings.get("monitor_y_offset")
            if monitor_w and monitor_h:
                extra_flags += f" -video_size {monitor_w}x{monitor_h}"
            if monitor_x is not None and monitor_y is not None:
                capture_input = f"{capture_input}+{monitor_x}+{monitor_y}"

        # Аудио
        enable_audio = self._settings.get("enable_audio", False)
        audio_flags = ""
        if enable_audio:
            audio_source = self._settings.get("audio_source", "pulse") or "pulse"
            audio_input = self._settings.get("audio_input", "default") or "default"
            audio_codec = self._settings.get("audio_codec", "aac") or "aac"
            audio_bitrate = self._settings.get("audio_bitrate", "128k") or "128k"
            audio_flags = (
                f" -f {audio_source} -i {shlex.quote(audio_input)}"
                f" -c:a {audio_codec} -b:a {audio_bitrate}"
            )

        env_prefix = ""
        if capture_type == "x11grab":
            display = capture_input.split(".")[0] if capture_input else ":0"
            env_prefix = f"export DISPLAY={display}; "
            xauth = self._settings.get("xauthority", "")
            if xauth:
                env_prefix += f"export XAUTHORITY={shlex.quote(xauth)}; "

        # Масштабируем выход до выбранного разрешения (растягиваем/сжимаем)
        sw, sh = resolution.split("x")
        scale_vf = f"scale={sw}:{sh}"

        # Флаги кодека зависят от типа — x264/x265 нужны preset/tune, mpeg — нет
        if codec in ("libx264", "libx265"):
            output_flags = (
                f"-c:v {codec} -preset ultrafast -tune zerolatency -pix_fmt yuv420p "
                f"-vf {scale_vf} -b:v {bitrate} -r {fps}"
            )
        else:
            output_flags = (
                f"-c:v {codec} -pix_fmt yuv420p "
                f"-vf {scale_vf} -b:v {bitrate} -r {fps}"
            )

        pid_file = f"/tmp/pyktool_ffmpeg_{port}.pid"
        stop_file = f"/tmp/pyktool_ffmpeg_stop_{port}"

        # Для listen-транспорта оборачиваем в цикл — сервер переживает отключение клиента
        is_listen = transport in ("tcp", "http", "rtsp")

        if is_listen:
            cmd = (
                f"trap 'kill $PID 2>/dev/null; rm -f {pid_file} {stop_file}; exit' HUP INT TERM EXIT; "
                f"fail=0; while ! [ -f {stop_file} ]; do "
                f"{env_prefix}{shlex.quote(ffmpeg)} -loglevel error -f {capture_type} {extra_flags} "
                f"-i {shlex.quote(capture_input)} "
                f"{audio_flags} "
                f"{output_flags} {output} & "
                f"PID=$!; echo $PID > {pid_file}; "
                f"s=$(date +%s); wait $PID; e=$(date +%s); "
                f"if [ $((e - s)) -lt 3 ]; then fail=$((fail+1)); else fail=0; fi; "
                f"if [ $fail -ge 3 ]; then echo 'FFmpeg failed 3 times quickly, stopping'; break; fi; "
                f"sleep 1; "
                f"done; rm -f {stop_file} {pid_file}"
            )
        else:
            cmd = (
                f"trap 'kill $PID 2>/dev/null; rm -f {pid_file}; exit' HUP INT TERM EXIT; "
                f"{env_prefix}{shlex.quote(ffmpeg)} -loglevel error -f {capture_type} {extra_flags} "
                f"-i {shlex.quote(capture_input)} "
                f"{audio_flags} "
                f"{output_flags} {output} & "
                f"PID=$!; echo $PID > {pid_file}; wait $PID"
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
                logger.error(f"FFmpegStreamManager: read_loop error ({prefix}): {e}")
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
