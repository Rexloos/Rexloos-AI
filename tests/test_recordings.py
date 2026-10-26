"""Real synthetic-video analysis plus isolated capture lifecycle tests."""
import io
import json
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from brawl_ai.learning import state_key
from brawl_ai.playstyle import Playstyle
from brawl_ai.recordings import (MatchRecording, RecordingAnalysis, action_requests,
                                decode_frame, load_gestures, preceding_frame, video_info)


DEVICE_TEXT = '''add device 1: /dev/input/event4
 ABS_MT_SLOT : value 0, min 0, max 9
 ABS_MT_POSITION_X : value 0, min 0, max 1000
 ABS_MT_POSITION_Y : value 0, min 0, max 1000
'''


def recording_fixture(root, encode=True):
    style = Playstyle(Path(root)/"profile", "Player")
    folder = style.folder/"recordings"/"12345"
    folder.mkdir(parents=True)
    metadata = {"status": "ready", "video_origin_host": 10., "stopped_host": 12., "rotation": 0,
                "input_device": {"path": "/dev/input/event4", "X": [0, 1000], "Y": [0, 1000], "protocol": "B"},
                "controls": {"move": [.1, .8], "attack": [.9, .8], "radius": .1},
                "calibrated": ["move", "attack"], "brawler": "Shelly", "mode": "Gem Grab", "map": "One"}
    (folder/"session.json").write_text(json.dumps(metadata))
    rows = []
    def event(stamp, code, value):
        rows.append({"host": stamp+5, "device": stamp,
                     "line": f"[ {stamp:.6f}] EV_ABS {code} {value:08x}"})
    event(5.4, "ABS_MT_TRACKING_ID", 1)
    event(5.4, "ABS_MT_POSITION_X", 900)
    event(5.4, "ABS_MT_POSITION_Y", 800)
    event(5.6, "ABS_MT_TRACKING_ID", 0xffffffff)
    event(5.8, "ABS_MT_TRACKING_ID", 2)
    event(5.8, "ABS_MT_POSITION_X", 100)
    event(5.8, "ABS_MT_POSITION_Y", 800)
    event(6.0, "ABS_MT_POSITION_X", 200)
    event(6.2, "ABS_MT_TRACKING_ID", 0xffffffff)
    (folder/"input.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
    if encode:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
                        "-t", "2", "-c:v", "ffv1", str(folder/"game.mkv")],
                       check=True, capture_output=True, timeout=20)
    return style, folder, metadata


class RecordingTests(unittest.TestCase):
    def test_selects_only_preceding_frames_and_rejects_invalid_offsets(self):
        self.assertEqual(preceding_frame([0, .1, .2, .3], .26), .2)
        self.assertIsNone(preceding_frame([0, .1], 0))
        self.assertIsNone(preceding_frame([0, .1], 2))
        with tempfile.TemporaryDirectory() as root:
            _, folder, metadata = recording_fixture(root, False)
            _, gestures = load_gestures(folder)
            requests = action_requests(metadata, gestures, [i/10 for i in range(20)], (160, 90), 0)
            self.assertEqual([item["action"] for item in requests], ["attack", "right"])
            self.assertTrue(all(item["frame_time"] < item["video_time"] for item in requests))
            for offset in (float("nan"), float("inf"), 31):
                with self.assertRaises(ValueError):
                    action_requests(metadata, gestures, [0, 1], (160, 90), offset)

    @patch("brawl_ai.recordings.video_tools")
    @patch("brawl_ai.recordings.shutil.which", return_value="scrcpy")
    @patch("brawl_ai.recordings.subprocess.Popen")
    def test_video_capture_records_without_screenshots_detection_or_model_updates(self, popen, which, tools):
        with tempfile.TemporaryDirectory() as root:
            style = Playstyle(root, "Player")
            device = Mock(adb="adb", serial="phone")
            device.command.side_effect = lambda *args: b"SurfaceOrientation: 3" if "dumpsys" in args else DEVICE_TEXT.encode()
            inputs, video = Mock(), Mock()
            inputs.stdout = io.StringIO("[ 5.0] EV_SYN SYN_REPORT 00000000\n")
            video.stdout = io.StringIO("INFO: Recording started to mkv file: game.mkv\n")
            for process in (inputs, video):
                process.poll.return_value = None
            session = MatchRecording(device, style, {}, set(), 90, 30, {})
            def create_process(*args, **kwargs):
                if args[0][0] == "scrcpy":
                    (session.folder/"game.mkv").write_bytes(b"test-video")
                    return video
                return inputs
            popen.side_effect = create_process
            session.stop()
            session.run()
            device.screenshot.assert_not_called()
            device.action.assert_not_called()
            self.assertFalse(style.path.exists())
            self.assertEqual(style.examples, 0)
            video.send_signal.assert_called_once_with(signal.SIGINT)
            self.assertEqual(json.loads((session.folder/"session.json").read_text())["status"], "ready")
            args = popen.call_args_list[1].args[0]
            self.assertIn("--no-control", args)
            self.assertIn("--no-audio", args)
            self.assertNotIn("--no-window", args)
            self.assertNotIn("--no-playback", args)
            self.assertIn("--window-title=Brawl AI - Live recording", args)
            self.assertEqual(session.metadata["rotation"], 270)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
    def test_synthetic_game_decodes_analyzes_and_is_not_learned_twice(self):
        with tempfile.TemporaryDirectory() as root:
            style, folder, _ = recording_fixture(root)
            stamps, size = video_info(folder/"game.mkv")
            self.assertEqual(size, (160, 90))
            self.assertEqual(len(stamps), 20)
            self.assertEqual(decode_frame(folder/"game.mkv", .3).size, size)
            analysis = RecordingAnalysis(folder, style, lambda image: (state_key(image), []), 0)
            analysis.run()
            learned = Playstyle(Path(root)/"profile", "Player")
            self.assertEqual(learned.examples, 2)
            self.assertIn("12345", learned.applied_recordings)
            again = RecordingAnalysis(folder, learned, Mock(), 0)
            again.run()
            self.assertEqual(Playstyle(Path(root)/"profile", "Player").examples, 2)
            self.assertIsNotNone(again.error)
            self.assertFalse(again.success)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
    def test_failed_or_cancelled_analysis_does_not_apply_partial_examples(self):
        with tempfile.TemporaryDirectory() as root:
            style, folder, _ = recording_fixture(root)
            style.observe("old", "idle")
            style.save()
            before = style.path.read_bytes()
            analysis = RecordingAnalysis(folder, style, Mock(side_effect=ValueError("detector failed")), 0)
            analysis.run()
            self.assertEqual(style.path.read_bytes(), before)
            cancelled = RecordingAnalysis(folder, style, Mock(), 0)
            cancelled.stop()
            cancelled.run()
            self.assertEqual(style.path.read_bytes(), before)
            messages = list(cancelled.events.queue)
            self.assertTrue(any(kind == "status" and "cancelled" in value for kind, value in messages))
            self.assertFalse(cancelled.success)
