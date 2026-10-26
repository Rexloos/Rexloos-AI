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
    console.capture_loop = Mock()
    console.auto_capture_enabled = Mock()
    console.capture_status = Mock()
    console.reviewing = False
    console.work_kind = None
    console.preview_revision = 0
    console.deferred_preview = None
    console.player_session = None
    console.use_style = Mock(get=Mock(return_value=False))
    return console


class UiActionTests(unittest.TestCase):
    @patch("brawl_ai.ui.time.sleep")
    def test_bootstrap_availability_stays_with_the_completed_action_frame(self, sleep):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.controls["super"] = [.8, .8]
            console.calibrated.add("super")
            blocked = {"kind": "control", "control": "super", "state": "uncharged", "state_score": .95,
                       "score": .98, "box": [.7, .7, .8, .8]}
            console.capture_frame = Mock(return_value=(console.image, [blocked]))
            console.one_step()
            operation, callback = console.work.call_args.args
            callback(operation())
            saved = list(console.pending_available)
            self.assertNotIn(ACTIONS.index("super"), saved)
            console.detections = [{**blocked, "state": "ready"}]
            console.reward = 1
            console.update_profile_status = Mock()
            console.refresh_recommendation = Mock()
            Console.finish(console)
            self.assertEqual(console.agent.update.call_args.kwargs["available"], saved)

    @patch("brawl_ai.ui.time.sleep")
    @patch("brawl_ai.ui.random.random", return_value=0)
    def test_loaded_player_style_can_override_reward_policy_choice(self, random, sleep):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.use_style.get.return_value = True
            console.style_strength = Mock(get=Mock(return_value=1))
            console.style = Mock()
            console.style.choose.return_value = ACTIONS.index("attack")
            console.style.action_controls.return_value = console.controls
            console.capture_frame = Mock(return_value=(console.image, []))
            console.one_step()
            operation, callback = console.work.call_args.args
            callback(operation())
            console.device.action.assert_called_once_with("attack", (160, 90), console.controls)
            self.assertEqual(console.pending[1], ACTIONS.index("attack"))

    def test_click_reviews_displayed_box_without_waiting_for_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            complete = Mock()
            console.capture_preview(complete)
            received = console.work.call_args.args[1]
            console.busy = True
            console.work_kind = "preview"
            item = {"box": [.1, .1, .5, .9], "identity": "NPC", "score": .99}
            console.detections = [item]
            console.review_detection = Mock()
            frame = console.image
            console.press(SimpleNamespace(x=30, y=30))
            console.review_detection.assert_called_once_with(item)
            self.assertEqual(console.preview_revision, 1)
            # The old result is allowed to finish, but cannot replace the reviewed frame.
            received((Image.new("RGB", frame.size, "black"), []))
            console.show.assert_not_called()
            complete.assert_called_once()
            self.assertIs(console.image, frame)

    def test_click_waits_for_real_ai_action_but_explains_why(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.busy = True
            console.work_kind = "device"
            console.detections = [{"box": [0, 0, 1, 1], "score": 1}]
            console.review_detection = Mock()
            console.press(SimpleNamespace(x=30, y=30))
            console.review_detection.assert_not_called()
            self.assertIn("AI action", console.status.set.call_args.args[0])

    def test_review_correction_queues_refresh_until_old_preview_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            console = console_fixture(folder)
            console.busy = True
            console.work_kind = "preview"
            console.ask_identity = Mock(return_value="NPC")
            item = {"box": [20/160, 20/90, 60/160, 70/90], "score": .99, "identity": "Shelly"}
            console.review_detection(item)
            self.assertTrue(console.detector.ready)
            self.assertIsNotNone(console.deferred_preview)
            console.work.assert_not_called()
            console.events = queue.Queue()
            console.events.put((Mock(), None, None))
            console.poll()
            self.assertIsNone(console.deferred_preview)
            self.assertEqual(console.work.call_args.kwargs["kind"], "preview")

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
