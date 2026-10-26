"""Capture cadence, load adaptation and UI interaction safety without real ADB."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from brawl_ai.capture import CaptureLoop, capture_interval
from brawl_ai.ui import Console


class CaptureTests(unittest.TestCase):
    def test_only_automatic_preview_uses_video_frames(self):
        console = Console.__new__(Console)
        console.device = Mock()
        console.detect_scene = Mock(return_value=[])
        console.capture_preview_frame()
        console.device.preview_screenshot.assert_called_once()
        console.device.screenshot.assert_not_called()
        console.capture_frame()
        console.device.screenshot.assert_called_once()
        self.assertEqual(console.device.preview_screenshot.call_count, 1)

    def loop(self):
        root = Mock()
        root.after.return_value = "timer"
        self.now = 0
        return CaptureLoop(root, Mock(), Mock(return_value=None), Mock(), clock=lambda: self.now)

    def test_fixed_interval_waits_for_completion_and_does_not_queue_frames(self):
        loop = self.loop()
        loop.start(2, False)
        loop.tick()
        self.assertEqual(loop.root.after.call_count, 1)
        self.now = .4
        loop.capture.call_args.args[0]()
        self.assertEqual(loop.root.after.call_args.args[0], 1600)
        loop.blocked.return_value = "device busy"
        loop.tick()
        self.assertEqual(loop.capture.call_count, 1)
        self.assertEqual(loop.root.after.call_args.args[0], 250)

    def test_adaptive_interval_slows_down_for_expensive_processing(self):
        loop = self.loop()
        loop.start(.5, True)
        loop.tick()
        self.now = 3
        loop.capture.call_args.args[0]()
        self.assertEqual(loop.root.after.call_args.args[0], 750)
        self.assertIn("target 3.75s", loop.report.call_args.args[0])

    def test_stop_or_reconfigure_does_not_resurrect_old_completion(self):
        loop = self.loop()
        loop.start(1, True)
        loop.tick()
        complete = loop.capture.call_args.args[0]
        loop.stop()
        count = loop.root.after.call_count
        complete()
        self.assertEqual(loop.root.after.call_count, count)
        loop.start(2, False)
        count = loop.root.after.call_count
        complete()
        self.assertEqual(loop.root.after.call_count, count)

    def test_rejects_invalid_intervals(self):
        for value in ("bad", "nan", "inf", "0", "-1", "0.1", "61"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture_interval(value)
        self.assertEqual(capture_interval("0.5"), .5)

    def test_selection_review_and_training_block_preview(self):
        console = Console.__new__(Console)
        console.busy = console.running = console.reviewing = False
        console.mode = None
        self.assertIsNone(console.capture_blocked())
        for name, value in (("busy", True), ("running", True), ("reviewing", True), ("mode", "enemy")):
            with self.subTest(name=name):
                setattr(console, name, value)
                self.assertIsNotNone(console.capture_blocked())
                setattr(console, name, None if name == "mode" else False)

    def test_preview_does_not_modify_pending_transition_or_send_an_action(self):
        console = Console.__new__(Console)
        console.pending = ("before", 2, "original-after")
        console.reward = 10
        console.device = Mock()
        console.work = Mock()
        console.show = Mock()
        console.preview_revision = 0
        console.reviewing = False
        console.mode = None
        complete = Mock()
        console.capture_preview(complete)
        operation, callback = console.work.call_args.args
        self.assertEqual(operation, console.capture_preview_frame)
        callback(("frame", []))
        console.show.assert_called_once_with(("frame", []))
        complete.assert_called_once()
        self.assertEqual(console.pending, ("before", 2, "original-after"))
        self.assertEqual(console.reward, 10)
        console.device.action.assert_not_called()

    def test_settings_are_saved_and_invalid_values_stop_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            console = Console.__new__(Console)
            console.training_root = Path(folder)
            for name in ("capture_seconds", "capture_adaptive", "auto_capture_enabled", "capture_loop", "capture_status", "status"):
                setattr(console, name, Mock())
            console.capture_seconds.get.return_value = "1.5"
            console.capture_adaptive.get.return_value = True
            console.auto_capture_enabled.get.return_value = True
            console.configure_capture()
            console.capture_loop.start.assert_called_once_with(1.5, True)
            self.assertTrue((Path(folder) / "capture-settings.json").exists())
            console.capture_seconds.get.return_value = "nan"
            console.configure_capture()
            console.capture_loop.stop.assert_called_once()
            console.auto_capture_enabled.set.assert_called_once_with(False)
