"""Replay overlay content and stage progress without a desktop."""
import bisect
import io
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageFont

from brawl_ai.replay import draw_overlay, overlay_font, read_exact, touch_timeline
from brawl_ai.ui import Console
from test_recordings import recording_fixture


class ReplayTests(unittest.TestCase):
    def test_overlay_reuses_font_for_same_frame_size(self):
        overlay_font.cache_clear()
        try:
            with patch("brawl_ai.replay.ImageFont.truetype", wraps=ImageFont.truetype) as load:
                frame = Image.new("RGB", (640, 360))
                first = draw_overlay(frame, 1, [], [], [])
                calls = load.call_count
                second = draw_overlay(frame, 1, [], [], [])
                self.assertEqual(load.call_count, calls)
                self.assertEqual(first.tobytes(), second.tobytes())
        finally:
            overlay_font.cache_clear()

    def test_recorded_finger_motion_is_labeled_and_disappears_on_release(self):
        with tempfile.TemporaryDirectory() as root:
            _, folder, metadata = recording_fixture(root, False)
            metadata["video_size"] = (160, 90)
            timeline = touch_timeline(folder, metadata, 0)
            times = [item["time"] for item in timeline]
            contact = timeline[bisect.bisect_right(times, 1.05)-1]["contacts"][0]
            self.assertEqual(contact["action"], "right")
            self.assertEqual(contact["control"], "move")
            self.assertTrue(contact["drag"])
            self.assertEqual(contact["point"], (.2, .8))
            frame = Image.new("RGB", (640, 360))
            annotated = draw_overlay(frame, 1.05, timeline, times, [{"box": [.5, .2, .7, .6], "identity": "NPC", "identity_score": .95}])
            self.assertEqual(annotated.getpixel((128, 288)), (0, 229, 255))
            self.assertEqual(annotated.getpixel((320, 72)), (255, 0, 0))
            after = draw_overlay(frame, 1.3, timeline, times, [], False)
            self.assertEqual(after.getpixel((128, 288)), (0, 0, 0))
            self.assertEqual(frame.getpixel((128, 288)), (0, 0, 0))

    def test_task_progress_tracks_each_stage_and_stops_failed_stage(self):
        console = Console.__new__(Console)
        console.player_tasks = {key: {"value": Mock(), "text": Mock(), "state": "waiting"}
                                for key in ("video", "examples", "replay")}
        console.task_bars = {}
        console.set_player_task("video", 1, 1)
        console.set_player_task("examples", 3, 10)
        console.set_player_task("replay")
        self.assertEqual(console.player_tasks["video"]["state"], "done")
        console.player_tasks["examples"]["value"].set.assert_called_once_with(30)
        console.stop_player_tasks(failed=True)
        self.assertEqual(console.player_tasks["video"]["state"], "done")
        self.assertEqual(console.player_tasks["replay"]["state"], "failed")

    def test_read_exact_handles_partial_reads_and_eof(self):
        stream = io.BytesIO(b"abc")
        self.assertEqual(read_exact(stream, 3), b"abc")
        self.assertIsNone(read_exact(stream, 1))

    def test_control_boxes_render_even_without_enemy_recognition_examples(self):
        frame = Image.new("RGB", (640, 360))
        control = {"kind": "control", "control": "super", "box": [.75, .5, .9, .6],
                   "state": "uncharged", "state_score": .95}
        annotated = draw_overlay(frame, 0, [], [], [control], False)
        self.assertEqual(annotated.getpixel((480, 180)), (0, 255, 255))
