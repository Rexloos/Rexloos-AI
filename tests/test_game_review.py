"""Recorded-frame corrections teach only explicit human labels in one profile."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from brawl_ai.detection import EnemyDetector
from brawl_ai.hud import HudDetector
from brawl_ai.player import PlayerDetector
from brawl_ai.review import GameReview, ReviewStore, review_candidates


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
    def test_new_samples_use_previously_confirmed_models_without_self_training(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            store.decide(0, 0, True, "entity", [.1, .1, .5, .8], "NPC")
            before = (folder/"enemy-detector.json").read_text()
            proposal = {"identity": "NPC", "box": [.2, .2, .6, .9], "score": .97}
            with patch("brawl_ai.review.EnemyDetector.detect", return_value=[proposal]) as detect:
                suggestions = store.suggest(1)
            detect.assert_called_once()
            store.update_suggestions(1, suggestions)
            self.assertEqual(store.data["frames"][1]["items"][0]["identity"], "NPC")
            self.assertEqual(store.data["frames"][1]["items"][0]["decision"], "pending")
            self.assertEqual((folder/"enemy-detector.json").read_text(), before)

    def test_rescan_preserves_human_decisions_and_suppresses_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            folder, stage, _ = fixture(root)
            store = ReviewStore(stage, folder)
            store.decide(0, 0, True, "entity", [.1, .1, .5, .8], "NPC")
            store.decide(0, 1, False, "control", [.6, .4, .9, .9], control="attack")
            human = list(store.data["frames"][0]["items"])
            store.update_suggestions(0, [
                {"identity": "Wrong", "box": [.1, .1, .5, .8], "decision": "confirmed"},
                {"kind": "control", "control": "attack", "box": [.6, .4, .9, .9]},
                {"kind": "control", "control": "attack", "box": [.05, .85, .15, 1]},
                {"identity": "New NPC", "box": [.6, .05, .9, .25]}])
            items = ReviewStore(stage, folder).data["frames"][0]["items"]
            self.assertEqual(items[:2], human)
            self.assertEqual(len(items), 4)
            self.assertTrue(all(item["decision"] == "pending" for item in items[2:]))

    def test_late_background_result_cannot_replace_new_correction_or_active_edit(self):
        import queue
        for stale, editing in ((True, False), (False, True)):
            view = GameReview.__new__(GameReview)
            view.closed = False
            view.index, view.revision, view.editing = 0, 2, editing
            view.scan_results = queue.Queue()
            view.scan_results.put((0, 1 if stale else 2, [], None))
            view.store = Mock()
            view.start_scan = Mock()
            view.window = Mock()
            view.poll_scan()
            view.store.update_suggestions.assert_not_called()

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
