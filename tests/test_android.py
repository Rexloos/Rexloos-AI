"""Device selection must not silently control a different target."""
import subprocess
import io
import struct
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from brawl_ai.android import Device, decode_raw_screenshot, usb_serial


class ScreenshotTests(unittest.TestCase):
    def test_raw_formats_and_header_versions_preserve_rgb_pixels(self):
        for fmt, pixels in ((1, bytes([10, 20, 30, 255])), (2, bytes([10, 20, 30, 0])),
                            (3, bytes([10, 20, 30])), (5, bytes([30, 20, 10, 255]))):
            for color_header in (b"", struct.pack("<I", 1)):
                with self.subTest(format=fmt, header=len(color_header)):
                    image = decode_raw_screenshot(struct.pack("<3I", 1, 1, fmt)+color_header+pixels)
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))

    def test_invalid_raw_frames_are_rejected(self):
        for data in (b"", struct.pack("<3I", 0, 1, 1), struct.pack("<3I", 1, 1, 4)+bytes(2),
                     struct.pack("<3I", 1, 1, 1)+bytes(3),
                     struct.pack("<4I", 1, 1, 1, 2)+bytes(4)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_raw_screenshot(data)

    def make_device(self, mode="auto"):
        with patch("brawl_ai.android.adb_path", return_value="adb"):
            device = Device("phone", mode)
        output = io.BytesIO()
        Image.new("RGB", (1, 1), (10, 20, 30)).save(output, format="PNG")
        self.png = output.getvalue()
        self.raw = struct.pack("<4I", 1, 1, 1, 1)+bytes([10, 20, 30, 255])
        device.command = Mock(side_effect=lambda *args: self.png if args[-1] == "-p" else self.raw)
        return device

    def test_auto_chooses_faster_transport_without_extra_captures(self):
        for raw_elapsed, expected in ((.5, "raw"), (1.2, "png"), (.95, "png")):
            with self.subTest(raw_elapsed=raw_elapsed):
                device = self.make_device()
                with patch("brawl_ai.android.time.monotonic", side_effect=[0, .8, 1, 2, 2+raw_elapsed, 2+raw_elapsed, 4, 4.5, 4.6]):
                    for _ in range(3):
                        self.assertEqual(device.screenshot().getpixel((0, 0)), (10, 20, 30))
                self.assertEqual(device.capture_backend, expected)
                self.assertEqual(device.command.call_count, 3)
                self.assertEqual(device.command.call_args.args,
                                 ("exec-out", "screencap") if expected == "raw" else ("exec-out", "screencap", "-p"))

    def test_raw_failure_falls_back_and_stops_retrying(self):
        for failure in (b"unsupported raw output", ValueError("bad format"), subprocess.TimeoutExpired("adb", 15)):
            device = self.make_device("raw")
            device.command.side_effect = [failure, self.png, self.png]
            device.screenshot()
            self.assertIsNotNone(device.capture_metrics["fallback"])
            device.screenshot()
            self.assertEqual(device.capture_backend, "png")
            self.assertEqual(device.command.call_count, 3)
            self.assertEqual(device.command.call_args.args, ("exec-out", "screencap", "-p"))

    def test_explicit_png_does_not_probe_raw(self):
        device = self.make_device("png")
        device.screenshot()
        device.screenshot()
        self.assertTrue(all(call.args[-1] == "-p" for call in device.command.call_args_list))


class UsbSelectionTests(unittest.TestCase):
    @patch("brawl_ai.android.adb_path", return_value="adb")
    @patch("brawl_ai.android.run")
    def test_selects_physical_device_only(self, run, adb):
        run.return_value = subprocess.CompletedProcess([], 0, b"phone123\n", b"")
        self.assertEqual(usb_serial(), "phone123")
        self.assertEqual(run.call_args.args[0], ["adb", "-d", "get-serialno"])

    @patch("brawl_ai.android.adb_path", return_value="adb")
    @patch("brawl_ai.android.run")
    def test_connection_errors_do_not_fall_back(self, run, adb):
        for detail in (b"device unauthorized", b"no devices found",
                       b"more than one device/emulator", b"insufficient permissions"):
            with self.subTest(detail=detail):
                run.side_effect = subprocess.CalledProcessError(1, [], stderr=detail)
                with self.assertRaisesRegex(RuntimeError, "USB device unavailable"):
                    usb_serial()

    @patch("brawl_ai.android.adb_path", return_value="adb")
    @patch("brawl_ai.android.run")
    def test_unknown_device_is_rejected(self, run, adb):
        run.return_value = subprocess.CompletedProcess([], 0, b"unknown\n", b"")
        with self.assertRaisesRegex(RuntimeError, "No authorized USB phone"):
            usb_serial()
