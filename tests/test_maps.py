"""Synthetic map matching and map-context boundaries, not live-game accuracy."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from brawl_ai.maps import MapRecognizer, map_state
from brawl_ai.profiles import Profile
from brawl_ai.ui import Console


def scene(color):
    image = Image.new("RGB", (160, 90), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 60, 70), fill="white")
    draw.ellipse((80, 20, 120, 60), fill="black")
    return image


class MapTests(unittest.TestCase):
    def test_names_persist_without_examples_and_survive_example_corrections(self):
        with tempfile.TemporaryDirectory() as root:
            model = MapRecognizer(root, "Gem Grab")
            self.assertEqual(model.save_name("  Saved   Map "), "Saved Map")
            loaded = MapRecognizer(root, "gem grab")
            self.assertEqual(loaded.names, ["Saved Map"])
            self.assertEqual(loaded.save_name("saved map"), "Saved Map")
            self.assertEqual(loaded.samples, [])
            self.assertIsNone(loaded.predict(scene("red")))
            loaded.learn(scene("red"), "Saved Map")
            loaded.learn(scene("red"), "Corrected Map")
            self.assertEqual(MapRecognizer(root, "Gem Grab").names, ["Corrected Map", "Saved Map"])
            self.assertEqual(MapRecognizer(root, "Heist").names, [])

    def test_ui_saves_name_without_capture_or_changing_learning_context(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.map_recognizer = MapRecognizer(root, "Gem Grab")
            console.map_name = Mock(get=Mock(return_value="New Map"))
            console.map_select = Mock()
            console.status = Mock()
            console.active_map = "Current Map"
            console.pending = ("before", 0, "after")
            console.save_map_name()
            self.assertEqual(MapRecognizer(root, "Gem Grab").names, ["New Map"])
            self.assertEqual(console.active_map, "Current Map")
            self.assertEqual(console.pending, ("before", 0, "after"))

    def test_learns_names_recognizes_and_isolates_modes(self):
        with tempfile.TemporaryDirectory() as root:
            model = MapRecognizer(root, "Gem Grab")
            self.assertIsNone(model.predict(scene("red")))
            model.learn(scene("red"), "Red Map")
            model.learn(scene("blue"), "Blue Map")
            loaded = MapRecognizer(root, "gem grab")
            result = loaded.predict(scene("red"))
            self.assertEqual(result["name"], "Red Map")
            self.assertTrue(result["confident"])
            self.assertEqual(loaded.learn(scene("red"), " red   map "), "Red Map")
            self.assertEqual(len(loaded.samples), 2)
            self.assertEqual(MapRecognizer(root, "Heist").names, [])
            loaded.learn(scene("red"), "Corrected Map")
            self.assertEqual(loaded.predict(scene("red"))["name"], "Corrected Map")

    def test_unknown_and_ambiguous_scenes_are_not_confident(self):
        with tempfile.TemporaryDirectory() as root:
            model = MapRecognizer(root, "Mode")
            model.learn(scene("red"), "One")
            self.assertFalse(model.predict(scene("blue"))["confident"])
            almost_same = scene("red")
            ImageDraw.Draw(almost_same).rectangle((70, 40, 75, 45), fill="yellow")
            model.learn(almost_same, "Two")
            self.assertFalse(model.predict(scene("red"))["confident"])

    def test_map_names_separate_policy_states_and_preserve_legacy(self):
        self.assertEqual(map_state("state", None), "state")
        self.assertNotEqual(map_state("state", "Map One"), map_state("state", "Map Two"))
        self.assertEqual(map_state("state", "Map One"), map_state("state", " map  one "))

    def test_teaching_map_commits_old_context_first(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.profile = Profile(root, "Shelly", "Gem Grab")
            console.folder = console.profile.prepare()
            console.map_recognizer = MapRecognizer(root, "Gem Grab")
            console.image = scene("red")
            console.active_map = "Old Map"
            console.busy = False
            console.mode = None
            for name in ("pause", "status", "map_select", "map_status", "log"):
                setattr(console, name, Mock())
            console.map_name = Mock(get=Mock(return_value="New Map"))
            contexts = []
            console.finish = Mock(side_effect=lambda **kwargs: contexts.append(console.active_map))
            console.teach_map()
            self.assertEqual(contexts, ["Old Map"])
            self.assertEqual(console.active_map, "New Map")
            self.assertEqual(console.log.call_args.args[1]["map"], "New Map")
            self.assertTrue((console.folder / console.log.call_args.args[1]["image"]).exists())
            console.busy = True
            console.map_name.get.return_value = "Other Map"
            console.teach_map()
            self.assertEqual(console.active_map, "New Map")
            self.assertEqual(contexts, ["Old Map"])
