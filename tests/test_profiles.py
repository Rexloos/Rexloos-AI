import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from brawl_ai.learning import Learner
from brawl_ai.profiles import Profile, choices
from brawl_ai.ui import Console


class ProfileTests(unittest.TestCase):
    def test_models_are_isolated_by_brawler_and_mode(self):
        with tempfile.TemporaryDirectory() as root:
            first = Profile(root, "Shelly", "Gem Grab")
            other_brawler = Profile(root, "Colt", "Gem Grab")
            other_mode = Profile(root, "Shelly", "Solo Showdown")
            learner = Learner(first.prepare() / "policy.json")
            learner.update("state", 1, 1, "end", terminal=True)
            learner.save()
            for profile in (other_brawler, other_mode):
                fresh = Learner(profile.prepare() / "policy.json")
                self.assertEqual(fresh.steps, 0)
                self.assertEqual(fresh.table, {})
            self.assertEqual(Learner(first.folder / "policy.json").steps, 1)

    def test_custom_names_are_safe_and_discoverable(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Profile(root, "../Future Brawler", "Custom / Event")
            profile.prepare()
            self.assertTrue(profile.folder.resolve().is_relative_to(Path(root).resolve()))
            self.assertEqual(profile.folder, Profile(root, " ../future   brawler ", "custom / event").folder)
            brawlers, modes = choices(root)
            self.assertIn(profile.brawler, brawlers)
            self.assertIn(profile.mode, modes)
            self.assertNotEqual(profile.folder, Profile(root, "Future Brawler", "Custom Event").folder)
            with self.assertRaises(ValueError):
                Profile(root, " ", "Gem Grab")

    def test_switch_commits_reward_to_old_profile_and_invalidates_frame(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.training_root = Path(root)
            console.profile = Profile(root, "Shelly", "Gem Grab")
            console.folder = console.profile.prepare()
            old_folder = console.folder
            console.agent = Learner(old_folder / "policy.json")
            console.pending = ("before", 1, "after")
            console.reward = 2
            console.active_map = "Old Map"
            console.map_prediction = None
            console.controls = {}
            console.calibrated = {"move", "attack"}
            console.busy = console.running = False
            console.auto_job = None
            console.image = object()
            console.mode = "label"
            for name in ("status", "profile_status", "recommendation_status", "detection_status", "canvas", "brawler_select", "mode_select", "map_name", "map_select", "map_status"):
                setattr(console, name, Mock())
            console.brawler_name = Mock(get=Mock(return_value="Colt"))
            console.mode_name = Mock(get=Mock(return_value="Heist"))
            console.load_gadget_mode = Mock()
            console.switch_profile()
            old = Learner(old_folder / "policy.json")
            self.assertEqual(old.steps, 1)
            self.assertAlmostEqual(old.table["before"][1], 0.3)
            self.assertEqual(console.agent.steps, 0)
            self.assertIsNone(console.pending)
            self.assertIsNone(console.image)
            record = json.loads((old_folder / "transitions.jsonl").read_text())
            self.assertEqual(record["brawler"], "Shelly")
            self.assertEqual(record["map"], "Old Map")
            self.assertIsNone(console.active_map)
            self.assertTrue(record["terminal"])

    def test_busy_switch_does_not_change_profile(self):
        console = Console.__new__(Console)
        console.busy = True
        console.pause = Mock()
        console.status = Mock()
        console.profile = original = object()
        console.switch_profile()
        self.assertIs(console.profile, original)
        console.pause.assert_called_once()


if __name__ == "__main__":
    unittest.main()
