"""Preview freshness, bounded retention, fallback and media-process lifecycle."""
import os
import io
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from brawl_ai.android import Device
from brawl_ai.stream import PreviewStream


class StreamTests(unittest.TestCase):
    def bare_stream(self):
        stream = PreviewStream.__new__(PreviewStream)
        stream.condition = threading.Condition()
        stream.stopped = threading.Event()
        stream.error = None
        stream.image = Image.new("RGB", (8, 8), "red")
        stream.received = time.monotonic()-1
        return stream

    def test_stale_frame_is_not_reused(self):
        stream = self.bare_stream()
        with self.assertRaises(TimeoutError):
            stream.frame(timeout=.01)

    def test_waits_for_new_frame_and_returns_independent_copy(self):
        stream = self.bare_stream()
        def publish():
            with stream.condition:
                stream.image = Image.new("RGB", (8, 8), "blue")
                stream.received = time.monotonic()
                stream.condition.notify_all()
        timer = threading.Timer(.02, publish)
        timer.start()
        try:
            frame = stream.frame(timeout=1)
            self.assertEqual(frame.getpixel((0, 0)), (0, 0, 255))
            self.assertIsNot(frame, stream.image)
        finally:
            timer.join()

    def test_stopped_and_failed_streams_do_not_return_last_image(self):
        for failed in (False, True):
            stream = self.bare_stream()
            if failed:
                stream.error = "Disconnected"
            else:
                stream.stopped.set()
            with self.assertRaises(RuntimeError):
                stream.frame()

    def make_device(self):
        with patch("brawl_ai.android.adb_path", return_value="adb"), patch.dict(os.environ, {"BRAWL_AI_PREVIEW": "stream"}):
            device = Device("phone", "png")
        device._screen_size = (1280, 800)
        return device

    def test_preview_failure_falls_back_once_without_changing_snapshot_mode(self):
        device = self.make_device()
        stream = Mock(started=time.monotonic())
        stream.frame.side_effect = TimeoutError("Stalled")
        device._preview_stream = stream
        device.screenshot = Mock(return_value=Image.new("RGB", (1280, 800)))
        device.preview_screenshot()
        self.assertEqual(device.capture_metrics["preview_fallback"], "Stalled")
        self.assertTrue(device._preview_failed)
        device.preview_screenshot()
        self.assertEqual(stream.frame.call_count, 1)
        self.assertEqual(device.screenshot.call_count, 2)
        self.assertEqual(device.capture_mode, "png")

    def test_manual_screenshot_stops_stream_and_updates_dimensions(self):
        device = self.make_device()
        stream = Mock()
        device._preview_stream = stream
        output = io.BytesIO()
        Image.new("RGB", (80, 128)).save(output, format="PNG")
        device.command = Mock(return_value=output.getvalue())
        self.assertEqual(device.screenshot().size, (80, 128))
        stream.close.assert_called_once()
        self.assertIsNone(device._preview_stream)
        self.assertEqual(device._screen_size, (80, 128))

    @patch("brawl_ai.stream.PreviewStream")
    def test_renews_stream_before_android_time_limit(self, factory):
        device = self.make_device()
        previous = Mock(started=time.monotonic()-111)
        device._preview_stream = previous
        factory.return_value.frame.return_value = Image.new("RGB", (1280, 800))
        self.assertEqual(device.preview_screenshot().size, (1280, 800))
        previous.close.assert_called_once()
        factory.assert_called_once()
        self.assertEqual(device.capture_metrics["backend"], "h264-preview")
        device.stop_preview()
        factory.return_value.close.assert_called_once()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for streaming integration")
    def test_real_decoder_and_processes_stop_cleanly(self):
        popen = subprocess.Popen
        processes = []
        def launch(args, **kwargs):
            if args[0] == "synthetic-adb":
                args = [shutil.which("ffmpeg"), "-v", "error", "-re", "-f", "lavfi", "-i",
                        "testsrc=size=64x48:rate=15", "-t", "5", "-c:v", "libx264",
                        "-preset", "ultrafast", "-tune", "zerolatency", "-f", "h264", "pipe:1"]
            process = popen(args, **kwargs)
            if len(processes) == 0:
                process.stdout = Mock(wraps=process.stdout)
                process.stdout.readline.return_value = b"BRAWL_PREVIEW_PID=123\n"
            processes.append(process)
            return process
        with patch("brawl_ai.stream.subprocess.Popen", side_effect=launch), patch("brawl_ai.stream.subprocess.run") as remote:
            stream = PreviewStream("synthetic-adb", "phone", (64, 48), os.environ.copy())
            try:
                self.assertEqual(stream.frame(timeout=4).size, (64, 48))
                self.assertEqual(stream.frame(timeout=1).mode, "RGB")
            finally:
                stream.close()
            self.assertFalse(stream.thread.is_alive())
            self.assertTrue(all(process.poll() is not None for process in processes))
            remote.assert_called_once()
            self.assertEqual(remote.call_args.args[0][-3:], ["kill", "-INT", "123"])
