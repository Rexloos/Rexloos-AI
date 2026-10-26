"""Synthetic localization tests do not establish accuracy in the actual game."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from brawl_ai.detection import EnemyDetector, enemy_state, overlap
from brawl_ai.learning import state_key
from brawl_ai.ui import Console


def character():
    image = Image.new("RGB", (20, 24), "navy")
    draw = ImageDraw.Draw(image)
    draw.ellipse((3, 1, 17, 13), fill="orange")
    draw.rectangle((6, 12, 14, 23), fill="red")
    draw.rectangle((8, 4, 10, 7), fill="white")
    return image


class DetectionTests(unittest.TestCase):
    def test_broad_box_correction_preserves_character_as_positive(self):
        with tempfile.TemporaryDirectory() as root:
            image = Image.new("RGB", (40, 40), "green")
            image.paste(character(), (10, 8))
            detector = EnemyDetector(root)
            original = [0, 0, 1, 1]
            corrected = [0.25, 0.2, 0.75, 0.8]
            detector.correct_box(image, original, "too_broad", corrected, "NPC")
            loaded = EnemyDetector(root)
            self.assertEqual(loaded.rejected_boxes[0]["kind"], "too_broad")
            self.assertEqual(loaded.samples[0]["identity"], "NPC")
            self.assertEqual(loaded.samples[0]["label"], "enemy")
            self.assertAlmostEqual(loaded.samples[0]["size"][0], 0.5)
            self.assertFalse(any(item["label"] == "not_enemy" for item in loaded.samples))

    def test_wrong_box_suppresses_original_without_learning_a_character(self):
        with tempfile.TemporaryDirectory() as root:
            detector = EnemyDetector(root)
            image = character()
            detector.learn(image, [0, 0, 1, 1], "enemy", "NPC")
            detector.correct_box(image, [0, 0, 1, 1], "wrong")
            loaded = EnemyDetector(root)
            self.assertEqual(len(loaded.samples), 1)
            self.assertEqual(loaded.rejected_boxes[0]["kind"], "wrong")
            self.assertFalse(any(item["box"] == [0, 0, 1, 1] for item in loaded.detect(image)))

    def test_box_edit_waits_for_redraw_and_can_be_cancelled(self):
        console = Console.__new__(Console)
        console.pause = Mock()
        console.status = Mock()
        console.canvas = Mock()
        console.finish = Mock()
        console.ask_identity = Mock(return_value="__too_broad__")
        item = {"box": [0, 0, 1, 1], "identity": "NPC"}
        console.review_detection(item)
        self.assertEqual(console.mode, "refine")
        self.assertEqual(console.box_edit[1], "too_broad")
        console.finish.assert_not_called()
        console.box = 12
        console.cancel_box_edit()
        self.assertIsNone(console.mode)
        self.assertIsNone(console.box_edit)
        console.canvas.delete.assert_called_once_with(12)

    def test_named_proposals_and_corrections_persist(self):
        with tempfile.TemporaryDirectory() as root:
            detector = EnemyDetector(root)
            image = character()
            box = [0, 0, 1, 1]
            detector.learn(image, box, "enemy", "shelly")
            found = detector.detect(image)
            self.assertEqual(found[0]["identity"], "Shelly")
            detector.learn(image, box, "enemy", "npc")
            loaded = EnemyDetector(root)
            self.assertEqual(loaded.detect(image)[0]["identity"], "NPC")
            self.assertEqual(len(loaded.samples), 1)
            detector.learn(image, box, "enemy", "Future Custom Brawler")
            self.assertEqual(EnemyDetector(root).detect(image)[0]["identity"], "Future Custom Brawler")
            detector.learn(image, box, "enemy", "showdown BOX")
            self.assertEqual(EnemyDetector(root).detect(image)[0]["identity"], "Showdown box")

    def test_cancel_review_does_not_teach_and_confirmation_logs_identity(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.folder = Path(root)
            console.image = character()
            console.detector = EnemyDetector(root)
            for name in ("pause", "finish", "log", "work", "status"):
                setattr(console, name, Mock())
            console.ask_identity = Mock(return_value=None)
            console.busy = False
            console.preview_revision = 0
            item = {"box": [0, 0, 1, 1], "identity": "Shelly", "score": 1}
            console.review_detection(item)
            self.assertFalse(console.detector.ready)
            console.finish.assert_not_called()
            console.ask_identity.return_value = "NPC"
            console.review_detection(item)
            self.assertEqual(console.detector.samples[0]["identity"], "NPC")
            record = console.log.call_args.args[1]
            self.assertEqual(record["identity"], "NPC")
            self.assertEqual(record["suggested_identity"], "Shelly")
            console.finish.assert_called_once_with(terminal=True)

    def test_capture_passes_raw_screenshot_to_detector(self):
        console = Console.__new__(Console)
        console.device = Mock()
        console.detector = Mock()
        image = Image.new("RGB", (160, 90))
        console.device.screenshot.return_value = image
        console.detector.detect.return_value = [{"box": [0, 0, 0.1, 0.2], "score": 0.99}]
        captured, found = console.capture_frame()
        self.assertIs(captured, image)
        self.assertEqual(found, console.detector.detect.return_value)
        console.detector.detect.assert_called_once_with(image)

    def test_imports_saved_enemy_annotations(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            character().save(path / "frame.png")
            (path / "annotations.jsonl").write_text(json.dumps(
                {"image": "frame.png", "label": "enemy", "box": [0, 0, 1, 1]}) + "\n")
            detector = EnemyDetector(path)
            self.assertTrue(detector.ready)
            self.assertEqual(len(EnemyDetector(path).samples), 1)

    def test_learns_localizes_reloads_and_keeps_profiles_separate(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "first"
            detector = EnemyDetector(folder)
            source = Image.new("RGB", (160, 90), "#204020")
            source.paste(character(), (40, 20))
            self.assertEqual(detector.detect(source), [])
            detector.learn(source, [40/160, 20/90, 60/160, 44/90], "enemy")
            target = Image.new("RGB", (160, 90), "#204020")
            target.paste(character(), (84, 40))
            found = EnemyDetector(folder).detect(target)
            expected = [84/160, 40/90, 104/160, 64/90]
            self.assertTrue(any(overlap(item["box"], expected) > 0.7 for item in found))
            self.assertFalse(EnemyDetector(Path(root) / "other").ready)
            self.assertEqual(detector.detect(Image.new("RGB", (160, 90), "#204020")), [])
            detector.learn(source, [40/160, 20/90, 60/160, 44/90], "not_enemy")
            self.assertEqual(detector.detect(target), [])

    def test_state_uses_enemy_location_without_changing_untrained_keys(self):
        image = Image.new("RGB", (160, 90))
        self.assertEqual(enemy_state(image, [], False), state_key(image))
        left = [{"box": [0.1, 0.1, 0.2, 0.2]}]
        right = [{"box": [0.8, 0.1, 0.9, 0.2]}]
        self.assertNotEqual(enemy_state(image, left, True), enemy_state(image, right, True))
        self.assertNotEqual(enemy_state(image, [], True), enemy_state(image, left, True))

    def test_rejects_flat_examples_and_ignores_other_labels(self):
        with tempfile.TemporaryDirectory() as root:
            detector = EnemyDetector(root)
            image = Image.new("RGB", (100, 100), "red")
            self.assertFalse(detector.learn(image, [0, 0, 1, 1], "wall"))
            with self.assertRaises(ValueError):
                detector.learn(image, [0, 0, 1, 1], "enemy")
            self.assertFalse(detector.ready)
