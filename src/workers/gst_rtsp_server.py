#!/usr/bin/env python3
"""GStreamer RTSP Server — загружается на удалённый хост и запускает RTSP-стрим."""

import json
import os
import sys

_typelib_paths = "/usr/lib64/girepository-1.0:/usr/lib/girepository-1.0"
existing = os.environ.get("GI_TYPELIB_PATH", "")
if existing:
    _typelib_paths = _typelib_paths + ":" + existing
os.environ["GI_TYPELIB_PATH"] = _typelib_paths

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstRtsp', '1.0')
gi.require_version('GstRtspServer', '1.0')

from gi.repository import Gst, GstRtspServer, GLib


def make_caps(caps_str):
    return Gst.Caps.from_string(caps_str)


def el(factory_name):
    return Gst.ElementFactory.make(factory_name, None)


def build_pipeline(config):
    """Создаёт Gst.Pipeline с видео-пайплайном. pay0 — обязательное имя для RTSP payloader."""
    pipeline = Gst.Pipeline.new(None)
    capture_type = config.get("capture_type", "x11grab")
    capture_input = config.get("capture_input", "")
    input_format = config.get("input_format", "") or "mjpeg"
    width = config["width"]
    height = config["height"]
    fps = config["fps"]

    # --- Источник ---
    if capture_type == "x11grab":
        src = el("ximagesrc")
        src.set_property("use-damage", False)
        display = capture_input.split(".")[0] if capture_input else ":0"
        src.set_property("display-name", display)
        src_caps = el("capsfilter")
        src_caps.set_property("caps", make_caps(f"video/x-raw,framerate={fps}/1"))

        crop_left = config.get("crop_left")
        crop_top = config.get("crop_top")
        crop_right = config.get("crop_right")
        crop_bottom = config.get("crop_bottom")

        has_crop = crop_left is not None
        if has_crop:
            videocrop = el("videocrop")
            videocrop.set_property("left", int(crop_left))
            videocrop.set_property("top", int(crop_top))
            videocrop.set_property("right", int(crop_right))
            videocrop.set_property("bottom", int(crop_bottom))
            crop_convert = el("videoconvert")

        videoscale = el("videoscale")
        scale_caps = el("capsfilter")
        scale_caps.set_property("caps", make_caps(f"video/x-raw,width={width},height={height}"))
        videoconvert = el("videoconvert")

        if has_crop:
            pre_encoder = [src, src_caps, videocrop, crop_convert, videoscale, scale_caps, videoconvert]
        else:
            pre_encoder = [src, src_caps, videoscale, scale_caps, videoconvert]

    elif capture_type == "v4l2":
        src = el("v4l2src")
        if capture_input:
            src.set_property("device", capture_input)
        if input_format == "mjpeg":
            src_caps = el("capsfilter")
            src_caps.set_property("caps", make_caps(
                f"image/jpeg,framerate={fps}/1,width={width},height={height}"
            ))
            jpegdec = el("jpegdec")
            videoconvert = el("videoconvert")
            pre_encoder = [src, src_caps, jpegdec, videoconvert]
        elif input_format in ("yuyv422", "yuyv"):
            src_caps = el("capsfilter")
            src_caps.set_property("caps", make_caps(
                f"video/x-raw,format=YUY2,framerate={fps}/1,width={width},height={height}"
            ))
            videoconvert = el("videoconvert")
            pre_encoder = [src, src_caps, videoconvert]
        else:
            src_caps = el("capsfilter")
            src_caps.set_property("caps", make_caps(
                f"video/x-raw,framerate={fps}/1,width={width},height={height}"
            ))
            videoconvert = el("videoconvert")
            pre_encoder = [src, src_caps, videoconvert]
    else:
        print(f"Unsupported capture_type: {capture_type}", file=sys.stderr, flush=True)
        return None

    # --- Общий caps перед энкодером ---
    conv_caps = el("capsfilter")
    conv_caps.set_property("caps", make_caps(
        f"video/x-raw,format=I420,width={width},height={height},framerate={fps}/1"
    ))
    queue = el("queue")
    queue.set_property("max-size-buffers", 0)
    queue.set_property("max-size-time", 0)
    queue.set_property("max-size-bytes", 0)

    codec = config.get("codec", "libx264")
    bitrate = config.get("bitrate_kbps", 2000)

    if codec in ("libx264", "h264"):
        encoder = el("x264enc")
        encoder.set_property("tune", 0x00000004)
        encoder.set_property("speed-preset", 1)
        encoder.set_property("bitrate", bitrate)
        enc_caps = el("capsfilter")
        enc_caps.set_property("caps", make_caps("video/x-h264,stream-format=byte-stream"))
        payloader = Gst.ElementFactory.make("rtph264pay", "pay0")
        elements = pre_encoder + [conv_caps, queue, encoder, enc_caps, payloader]

    elif codec in ("libx265", "h265", "hevc"):
        encoder = el("x265enc")
        encoder.set_property("tune", 0x00000004)
        encoder.set_property("bitrate", bitrate)
        enc_caps = el("capsfilter")
        enc_caps.set_property("caps", make_caps("video/x-h265,stream-format=byte-stream"))
        payloader = Gst.ElementFactory.make("rtph265pay", "pay0")
        elements = pre_encoder + [conv_caps, queue, encoder, enc_caps, payloader]

    elif codec == "mpeg2video":
        videorate = el("videorate")
        encoder = el("avenc_mpeg2video")
        encoder.set_property("bitrate", bitrate * 1000)
        payloader = Gst.ElementFactory.make("rtpmpvpay", "pay0")
        elements = pre_encoder + [conv_caps, queue, videorate, encoder, payloader]

    elif codec == "mpeg4":
        videorate = el("videorate")
        encoder = el("avenc_mpeg4")
        encoder.set_property("bitrate", bitrate * 1000)
        payloader = Gst.ElementFactory.make("rtpmp4vpay", "pay0")
        elements = pre_encoder + [conv_caps, queue, videorate, encoder, payloader]

    else:
        print(f"Unsupported codec: {codec}", file=sys.stderr, flush=True)
        return None

    for e in elements:
        pipeline.add(e)
    for i in range(len(elements) - 1):
        elements[i].link(elements[i + 1])

    return pipeline


class RTSPFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def do_create_element(self, url):
        print(f"RTSP client connected: {url.get_request_uri()}", flush=True)
        return build_pipeline(self.config)


class RTSPServer:
    def __init__(self, port, mount_point, config_json):
        self.port = port
        self.mount_point = mount_point
        self.config = json.loads(config_json)
        self.loop = GLib.MainLoop()

    def run(self):
        Gst.init(None)

        server = GstRtspServer.RTSPServer()
        server.set_service(str(self.port))
        factory = RTSPFactory(self.config)
        mount_points = server.get_mount_points()
        mount_points.add_factory(self.mount_point, factory)
        server.attach(None)

        print(f"RTSP server: rtsp://0.0.0.0:{self.port}{self.mount_point}", flush=True)
        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <port> <mount_point> <config_json>", file=sys.stderr)
        sys.exit(1)
    srv = RTSPServer(int(sys.argv[1]), sys.argv[2], sys.argv[3])
    srv.run()