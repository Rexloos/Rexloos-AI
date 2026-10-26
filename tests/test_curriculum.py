import json
from pathlib import Path
import tempfile
import unittest

from brawl_ai.curriculum import evidence, recommend
from brawl_ai.profiles import Profile


def feedback(profile, rewards):
    folder = profile.prepare()
    with (folder / "transitions.jsonl").open("a") as stream:
        for reward in rewards:
            stream.write(json.dumps({"reward": reward}) + "\n")


class CurriculumTests(unittest.TestCase):
    def test_new_brawler_starts_with_basics_without_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = recommend(root, Profile(root, "Colt", "Gem Grab"))
            self.assertEqual(result.profile.mode, "Training Ground")
            self.assertIn("0/20", result.reason)
            self.assertEqual(list(root.iterdir()), [])

    def test_zero_rewards_do_not_unlock_next_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            basics = Profile(root, "Colt", "Training Ground")
            feedback(basics, [0] * 100)
            self.assertEqual(evidence(basics), (0, None))
            self.assertEqual(recommend(root, Profile(root, "Colt", "Heist")).profile, basics)

    def test_basics_then_selected_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feedback(Profile(root, "Colt", "Training Ground"), [1] * 20)
            active = Profile(root, "Colt", "Gem Grab")
            result = recommend(root, active)
            self.assertEqual(result.profile, active)
            self.assertIn("gems", result.task)

    def test_weak_profile_is_recommended_after_initial_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = Profile(root, "Colt", "Training Ground")
            weak = Profile(root, "Shelly", "Solo Showdown")
            feedback(active, [1] * 30)
            feedback(weak, [-1] * 30)
            result = recommend(root, active)
            self.assertEqual(result.profile, weak)
            self.assertIn("0%", result.reason)

    def test_balances_saved_profiles_and_uses_recent_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = Profile(root, "Colt", "Training Ground")
            other = Profile(root, "New Brawler", "Custom Event")
            feedback(active, [-1] * 80 + [1] * 50)
            feedback(other, [1] * 30)
            self.assertEqual(evidence(active), (130, 1.0))
            self.assertEqual(recommend(root, active).profile, other)


if __name__ == "__main__":
    unittest.main()
