"""Ability routing, policy migration and human reward handling."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from brawl_ai.android import Device
from brawl_ai.learning import ACTIONS, Learner, available_actions
from brawl_ai.ui import Console
from brawl_ai.profiles import Profile


class AbilityTests(unittest.TestCase):
    @patch("brawl_ai.ui.messagebox.askyesno", return_value=True)
    def test_gadget_prompt_is_per_brawler_and_setting_is_editable(self, ask):
        with tempfile.TemporaryDirectory() as folder:
            console = Console.__new__(Console)
            console.root = Mock()
            console.controls = {"gadget_mode": "tap"}
            console.control_modes = {"gadget": Mock(), "super": Mock()}
            console.profile = Profile(folder, "Shelly", "Training Ground")
            console.load_gadget_mode()
            self.assertEqual(console.controls["gadget_mode"], "drag")
            ask.assert_called_once()
            console.profile = Profile(folder, "shelly", "Gem Grab")
            console.load_gadget_mode()
            ask.assert_called_once()
            self.assertEqual(console.controls["gadget_mode"], "drag")
            console.pause = Mock()
            console.finish = Mock()
            console.status = Mock()
            console.busy = False
            console.controls_path = Path(folder) / "controls.json"
            console.control_modes["gadget"].get.return_value = "tap"
            console.control_modes["super"].get.return_value = "drag"
            console.save_control_modes()
            self.assertEqual(console.profile.gadget_mode(), "tap")
            self.assertNotIn("gadget_mode", json.loads(console.controls_path.read_text()))
            console.profile = Profile(folder, "Colt", "Training Ground")
            ask.return_value = False
            console.load_gadget_mode()
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(console.controls["gadget_mode"], "tap")
            self.assertEqual(Profile(folder, "Shelly", "Heist").gadget_mode(), "tap")

    def test_optional_actions_require_calibration_and_respect_input_mode(self):
        self.assertEqual(available_actions({}, {"move", "attack"}), list(range(6)))
        enabled = available_actions({"gadget_mode": "drag"}, {"super", "gadget", "hypercharge"})
        names = [ACTIONS[i] for i in enabled]
        self.assertIn("super", names)
        self.assertIn("hypercharge", names)
        self.assertNotIn("gadget", names)
        self.assertTrue(all("gadget_" + direction in names for direction in ("up", "down", "left", "right")))

    def test_existing_policy_migrates_and_masks_unavailable_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps({"steps": 50, "table": {"state": [1, 2, 3, 4, 5, 6]}}))
            learner = Learner(path)
            self.assertEqual(learner.values("state")[:6], [1, 2, 3, 4, 5, 6])
            self.assertEqual(len(learner.values("state")), len(ACTIONS))
            learner.values("state")[6] = 1000
            with patch("brawl_ai.learning.random.random", return_value=1):
                self.assertEqual(learner.choose("state", list(range(6))), 5)
            learner.update("before", 0, 0, "state", available=list(range(6)))
            self.assertAlmostEqual(learner.values("before")[0], 0.81)
            learner.save()
            self.assertEqual(Learner(path).values("state")[5], 6)

    def test_taps_and_directional_swipes(self):
        device = Device.__new__(Device)
        device.command = Mock()
        controls = {"super": [0.5, 0.5], "gadget": [0.99, 0.99],
                    "hypercharge": [0.2, 0.3], "radius": 0.1}
        for name in ("super", "gadget", "hypercharge"):
            device.action(name, (1000, 500), controls)
            self.assertEqual(device.command.call_args.args[:3], ("shell", "input", "tap"))
        device.action("super_up", (1000, 500), controls)
        device.command.assert_called_with("shell", "input", "swipe", "500", "250", "500", "200", "180")
        device.action("gadget_right", (1000, 500), controls)
        device.command.assert_called_with("shell", "input", "swipe", "990", "495", "999", "495", "180")

    def test_custom_reward_accumulates_and_rejects_nonfinite_values(self):
        console = Console.__new__(Console)
        console.status = Mock()
        console.reward_status = Mock()
        console.reward_input = Mock()
        console.pending = ("before", 0, "after")
        console.reward = 0
        for text in ("0.2", "100", "-5"):
            console.reward_input.get.return_value = text
            console.custom_feedback()
        self.assertAlmostEqual(console.reward, 95.2)
        for text in ("nan", "inf", "bad"):
            console.reward_input.get.return_value = text
            console.custom_feedback()
            self.assertAlmostEqual(console.reward, 95.2)
        console.clear_feedback()
        self.assertEqual(console.reward, 0)
        console.pending = None
        console.feedback(100)
        self.assertEqual(console.reward, 0)
