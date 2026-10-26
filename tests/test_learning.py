import tempfile
import unittest
from pathlib import Path
from PIL import Image
from brawl_ai.learning import Learner, state_key


class LearningTests(unittest.TestCase):
    def test_terminal_feedback_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            learner = Learner(path)
            learner.values("next")[0] = 100
            learner.update("first", 2, -1, "next", terminal=True)
            self.assertAlmostEqual(learner.values("first")[2], -0.15)
            learner.save()
            loaded = Learner(path)
            self.assertEqual(loaded.table, learner.table)
            self.assertEqual(loaded.steps, 1)

    def test_nonterminal_bootstraps(self):
        with tempfile.TemporaryDirectory() as folder:
            learner = Learner(Path(folder) / "policy.json")
            learner.values("next")[1] = 2
            learner.update("first", 0, 1, "next")
            self.assertAlmostEqual(learner.values("first")[0], 0.42)

    def test_visual_states(self):
        self.assertNotEqual(state_key(Image.new("RGB", (1280, 720), "black")),
                            state_key(Image.new("RGB", (1280, 720), "white")))


if __name__ == "__main__":
    unittest.main()
