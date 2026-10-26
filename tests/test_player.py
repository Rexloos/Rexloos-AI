"""Self-role detection from visible markers, separate from enemy identity matching."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from brawl_ai.detection import enemy_state
from brawl_ai.player import PlayerDetector, merge_player
from brawl_ai.profiles import Profile
from brawl_ai.replay import draw_overlay
from brawl_ai.ui import Console


def draw_character(image, cx, bar_y, own=True, fraction=1, allied=False):
    draw = ImageDraw.Draw(image)
    draw.ellipse((cx-20, bar_y+32, cx+20, bar_y+52), fill="#194a29",
                 outline="#19f027" if own else "blue", width=3)
    draw.rectangle((cx-9, bar_y+11, cx+9, bar_y+43), fill="#b830ce")
    draw.ellipse((cx-11, bar_y+10, cx+11, bar_y+27), fill="#d166e2")
    draw.rectangle((cx-16, bar_y, cx+16, bar_y+7), fill="#284164")
    draw.rectangle((cx-14, bar_y+2, cx+14, bar_y+5), fill="#5f5f69")
    draw.rectangle((cx-14, bar_y+2, cx-14+round(28*fraction), bar_y+5),
                   fill="#1ef023" if own or allied else "red")
    draw.text((cx-15, bar_y-17), "SELF" if own else "ALLY", fill="#1ef023" if own else "blue")


def scene(cx=90, fraction=1):
    image = Image.new("RGB", (400, 250), "#8c5f69")
    draw_character(image, cx, 80, fraction=fraction)
    draw_character(image, 275, 80, own=False, allied=True)
    return image


class PlayerTests(unittest.TestCase):
    def test_finds_controlled_player_away_from_center_without_examples(self):
        with tempfile.TemporaryDirectory() as root:
            model = PlayerDetector(root, "Shelly")
            found = model.detect(scene())
            self.assertEqual(len(found), 1)
            player = found[0]
            self.assertEqual(player["kind"], "player")
            self.assertEqual(player["role"], "self")
            self.assertEqual(player["identity"], "Shelly")
            self.assertEqual(player["identity_source"], "selected profile")
            self.assertAlmostEqual((player["box"][0]+player["box"][2])/2, 90/400, delta=.03)
            self.assertIn("green name", player["evidence"])
            self.assertEqual(model.samples, [])

    def test_no_center_guess_for_unmarked_or_ambiguous_characters(self):
        with tempfile.TemporaryDirectory() as root:
            model = PlayerDetector(root, "Shelly")
            unmarked = Image.new("RGB", (400, 250), "gray")
            ImageDraw.Draw(unmarked).ellipse((180, 105, 220, 155), fill="purple")
            self.assertEqual(model.detect(unmarked), [])
            self.assertEqual(model.detect(Image.new("RGB", (400, 250), "green")), [])
            ambiguous = scene()
            draw_character(ambiguous, 275, 80, own=True)
            self.assertEqual(model.detect(ambiguous), [])

    def test_partial_green_health_does_not_shrink_the_entire_body_box(self):
        with tempfile.TemporaryDirectory() as root:
            model = PlayerDetector(root, "Shelly")
            full = model.detect(scene())[0]["box"]
            partial = model.detect(scene(fraction=.35))[0]["box"]
            self.assertAlmostEqual(full[2]-full[0], partial[2]-partial[0], delta=.02)

    def test_correction_rejection_and_profile_isolation_persist(self):
        with tempfile.TemporaryDirectory() as root:
            image = scene()
            model = PlayerDetector(Path(root)/"one", "Shelly")
            proposal = model.detect(image)[0]
            model.learn(image, proposal["box"], False)
            self.assertEqual(PlayerDetector(Path(root)/"one", "Shelly").detect(image), [])
            corrected = [78/400, 90/250, 103/400, 128/250]
            model.learn(image, corrected, True)
            reloaded = PlayerDetector(Path(root)/"one", "Shelly")
            self.assertEqual(reloaded.detect(image)[0]["box"], corrected)
            moved = reloaded.detect(scene(cx=115))
            self.assertEqual(len(moved), 1)
            self.assertAlmostEqual(moved[0]["box"][2]-moved[0]["box"][0], corrected[2]-corrected[0], delta=.02)
            self.assertEqual(PlayerDetector(Path(root)/"other", "Colt").samples, [])

    def test_self_is_not_an_enemy_and_its_location_enters_learning_state(self):
        image = scene()
        own = {"kind": "player", "role": "self", "identity": "Shelly", "box": [.2, .35, .3, .55], "score": .96}
        duplicate = {"identity": "Shelly", "box": [.21, .36, .29, .54], "score": .99}
        enemy = {"identity": "NPC", "box": [.7, .3, .8, .5], "score": .97}
        merged = merge_player([duplicate, enemy], [own])
        self.assertEqual(merged, [enemy, own])
        self.assertEqual(merge_player(merged, [own]), merged)
        state = enemy_state(image, merged, True)
        self.assertIn("|enemy-v1:7|self-v1:5", state)
        changed = enemy_state(image, [enemy, {**own, "box": [.6, .35, .7, .55]}], True)
        self.assertNotEqual(state, changed)

    def test_imports_existing_player_annotations_and_draws_green_overlay(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            image = scene()
            image.save(folder/"frame.png")
            box = [.2, .36, .3, .52]
            (folder/"annotations.jsonl").write_text(json.dumps({"image": "frame.png", "label": "player", "box": box})+"\n")
            model = PlayerDetector(folder, "Shelly")
            self.assertTrue(model.path.exists())
            found = model.detect(image)
            self.assertEqual(found[0]["box"], box)
            annotated = draw_overlay(image, 0, [], [], found, False)
            self.assertEqual(annotated.getpixel((80, 90)), (0, 255, 0))

    def test_capture_fuses_self_markers_and_manual_player_feedback_is_saved(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.folder = Path(root)
            console.profile = Profile(root, "Shelly", "Training Ground")
            console.image = scene()
            console.detector = Mock(detect=Mock(return_value=[]))
            console.self_detector = PlayerDetector(root, "Shelly")
            detected = console.detect_scene(console.image)
            self.assertEqual(len(detected), 1)
            for name in ("finish", "refresh_detection", "log", "status"):
                setattr(console, name, Mock())
            console.save_player_example(detected[0]["box"])
            self.assertTrue(console.self_detector.path.exists())
            self.assertEqual(console.log.call_args.args[1]["role"], "self")
            console.finish.assert_called_once_with(terminal=True)
