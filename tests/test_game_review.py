"""Recorded-frame corrections teach only explicit human labels in one profile."""
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from brawl_ai.detection import EnemyDetector
from brawl_ai.hud import HudDetector
from brawl_ai.player import PlayerDetector
from brawl_ai.review import ReviewStore, review_candidates


def fixture(root):
    folder = Path(root)/"profile"
    stage = folder/"playstyles"/"player"/"recordings"/"game"/"analyses"/"one"
    (stage/"review").mkdir(parents=True)
    image = Image.new("RGB", (160, 100), "navy")
    draw = ImageDraw.Draw(image)
    for x in range(0, 160, 8):
        draw.rectangle((x, 10, x+3, 90), fill="orange")
    image.save(stage/"review/frame.png")
    data = {"brawler": "Shelly", "map": "One", "frames": [
        {"time": 0, "image": "review/frame.png", "items": [
            {"box": [.1, .1, .5, .8], "identity": "NPC", "decision": "pending"},
            {"box": [.6, .4, .9, .9], "kind": "control", "control": "attack", "state": "ready", "decision": "pending"}]},
        {"time": 1, "image": "review/frame.png", "items": []}]}
    (stage/"review.json").write_text(json.dumps(data))
    return folder, stage, image


class GameReviewTests(unittest.TestCase):
    def test_pending_proposals_do_not_train_and_calibration_is_only_a_hint(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            store.image(0)
            self.assertFalse((folder/"enemy-detector.json").exists())
            self.assertFalse((folder/"hud-detector.json").exists())
        detections = [{"identity": "NPC", "box": [.1, .2, .3, .4]}]
        candidates = review_candidates(detections, {"calibrated": ["move"], "controls": {"move": [.1, .8]}}, (160, 100))
        self.assertNotIn("decision", detections[0])
        self.assertTrue(all(item["decision"] == "pending" for item in candidates))
        self.assertEqual(candidates[-1]["source"], "recorded_calibration")
        self.assertEqual(candidates[-1]["state"], "unknown")

    def test_confirm_correct_reject_and_resume_entity(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, image = fixture(root)
            store = ReviewStore(stage, folder)
            box = [.1, .1, .5, .8]
            store.decide(0, 0, True, "entity", box, "Custom opponent")
            self.assertEqual(EnemyDetector(folder).samples[0]["identity"], "Custom opponent")
            store.decide(0, 0, True, "entity", box, "NPC")
            self.assertEqual(len(EnemyDetector(folder).samples), 1)
            self.assertEqual(EnemyDetector(folder).samples[0]["identity"], "NPC")
            store.decide(0, 0, False, "entity", box)
            self.assertEqual(EnemyDetector(folder).samples[0]["label"], "not_enemy")
            reopened = ReviewStore(stage, folder)
            self.assertEqual(reopened.data["frames"][0]["items"][0]["decision"], "rejected")
            self.assertEqual(reopened.image(0).tobytes(), image.tobytes())

    def test_control_availability_correction_and_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            box = [.6, .4, .9, .9]
            store.decide(0, 1, True, "control", box, control="attack", state="unavailable")
            self.assertEqual(HudDetector(folder).samples[0]["state"], "unavailable")
            store.decide(0, 1, False, "control", box, control="attack")
            self.assertEqual(HudDetector(folder).samples[0]["state"], "rejected")

    def test_missing_player_and_object_can_be_added_without_proposals(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, image = fixture(root)
            store = ReviewStore(stage, folder)
            store.decide(1, None, True, "player", [.1, .1, .5, .8])
            self.assertEqual(PlayerDetector(folder, "Shelly").detect(image)[0]["identity"], "Shelly")
            store.decide(1, None, True, "entity", [.5, .1, .9, .8], "Showdown box")
            self.assertEqual(len(store.data["frames"][1]["items"]), 2)
            self.assertEqual(EnemyDetector(folder).samples[0]["identity"], "Showdown box")
            self.assertFalse((Path(root)/"other-profile"/"enemy-detector.json").exists())

    def test_redrawing_suppresses_old_geometry_and_keeps_new_positive(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            store.decide(0, 0, True, "entity", [.15, .2, .45, .7], "NPC")
            labels = [sample["label"] for sample in EnemyDetector(folder).samples]
            self.assertIn("enemy", labels)
            self.assertIn("not_enemy", labels)

    def test_invalid_edits_cannot_damage_existing_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            store.decide(0, 0, True, "entity", [.1, .1, .5, .8], "NPC")
            before = (folder/"enemy-detector.json").read_text()
            for box, name in (([0, 0, 0, 0], "NPC"), ([.1, .1, .2, .2], "Unknown"),
                              ([.1, .1, .2, .2], "x"*101), ([0, 0, .01, .01], "NPC")):
                with self.assertRaises(ValueError):
                    store.decide(0, 0, True, "entity", box, name)
            self.assertEqual((folder/"enemy-detector.json").read_text(), before)

    def test_profile_and_frame_path_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            with self.assertRaises(ValueError):
                ReviewStore(stage, Path(root)/"another-profile")
            store = ReviewStore(stage, folder)
            store.data["frames"][0]["image"] = str(Path(root)/"outside.png")
            with self.assertRaises(ValueError):
                store.image(0)
