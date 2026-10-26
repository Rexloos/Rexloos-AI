"""Exercise button callbacks and worker recovery without a desktop or real ADB."""
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from brawl_ai.detection import EnemyDetector
from brawl_ai.learning import ACTIONS
from brawl_ai.ui import Console


def console_fixture(folder):
    console = Console.__new__(Console)
    console.folder = Path(folder)
    console.root = Mock()
    console.status = Mock()
    console.canvas = Mock()
    console.device = Mock()
    console.agent = Mock()
    console.agent.steps = 0
    console.agent.choose.return_value = ACTIONS.index("right")
    console.detector = EnemyDetector(folder)
    console.detections = []
    console.image = Image.new("RGB", (160, 90), "green")
    ImageDraw.Draw(console.image).rectangle((20, 20, 60, 70), fill="red")
    ImageDraw.Draw(console.image).ellipse((28, 25, 52, 45), fill="yellow")
    console.preview_size = console.image.size
    console.busy = console.running = False
    console.mode = console.pending = console.auto_job = None
    console.controls = {"move": [.1, .8], "attack": [.9, .8], "radius": .1}
    console.calibrated = {"move", "attack"}
    console.active_map = None
    console.finish = Mock()
    console.log = Mock()
    console.work = Mock()
    console.show = Mock()
    return console


class UiActionTests(unittest.TestCase):
    @patch("brawl_ai.ui.time.sleep")
    def test_one_step_exits_labeling_and_executes_exactly_one_device_action(self, sleep):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.mode = "enemy"
            console.box = 42
            console.running = True
            console.auto_job = "scheduled-step"
            console.capture_frame = Mock(return_value=(console.image, []))
            console.one_step()
            self.assertFalse(console.running)
            self.assertIsNone(console.mode)
            console.root.after_cancel.assert_called_once_with("scheduled-step")
            operation, callback = console.work.call_args.args
            result = operation()
            console.device.action.assert_called_once_with("right", (160, 90), console.controls)
            callback(result)
            self.assertEqual(console.pending[1], ACTIONS.index("right"))
            console.root.after.assert_not_called()

    def test_label_enemy_drag_opens_identity_dialog_and_saves_example(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.ask_identity = Mock(return_value="NPC")
            console.set_mode("enemy")
            self.assertEqual(console.mode, "enemy")
            console.press(SimpleNamespace(x=20, y=20))
            console.drag(SimpleNamespace(x=60, y=70))
            console.release(SimpleNamespace(x=60, y=70))
            console.ask_identity.assert_called_once()
            self.assertIsNone(console.mode)
            self.assertTrue(console.detector.ready)
            self.assertEqual(console.detector.samples[0]["identity"], "NPC")
            record = console.log.call_args.args[1]
            self.assertEqual(record["box"], [20/160, 20/90, 60/160, 70/90])
            self.assertTrue((Path(folder) / record["image"]).exists())

    def test_label_enemy_captures_automatically_when_no_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            image = console.image
            console.image = None
            console.set_mode("enemy")
            operation, callback = console.work.call_args.args
            self.assertEqual(operation, console.capture_frame)
            console.show.side_effect = lambda result: setattr(console, "image", result[0])
            callback((image, []))
            self.assertEqual(console.mode, "enemy")

    def test_tiny_box_can_be_cancelled_instead_of_blocking_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.set_mode("enemy")
            console.press(SimpleNamespace(x=20, y=20))
            console.release(SimpleNamespace(x=21, y=21))
            self.assertEqual(console.mode, "enemy")
            console.cancel_box_edit()
            console.one_step()
            console.work.assert_called_once()

    def test_blocked_steps_explain_why_and_do_not_execute(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.busy = True
            console.one_step()
            self.assertIn("still running", console.status.set.call_args.args[0])
            console.busy = False
            console.mode = "refine"
            console.one_step()
            self.assertIn("Cancel selection", console.status.set.call_args.args[0])
            console.mode = None
            console.calibrated = {"move"}
            console.one_step()
            self.assertIn("attack", console.status.set.call_args.args[0])
            console.work.assert_not_called()

    @patch("brawl_ai.ui.traceback.print_exception")
    def test_callback_failure_keeps_polling_and_accepts_next_result(self, report):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.events = queue.Queue()
            bad = Mock(side_effect=ValueError("bad render"))
            good = Mock()
            console.events.put((bad, None, None))
            console.events.put((good, "next frame", None))
            console.busy = True
            console.poll()
            self.assertFalse(console.busy)
            self.assertIn("bad render", console.status.set.call_args.args[0])
            console.poll()
            good.assert_called_once_with("next frame")
            self.assertEqual(console.root.after.call_count, 2)
