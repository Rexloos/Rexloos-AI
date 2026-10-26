"""Device selection must not silently control a different target."""
import subprocess
import unittest
from unittest.mock import patch

from brawl_ai.android import usb_serial


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
