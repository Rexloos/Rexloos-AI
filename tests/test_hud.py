"""Synthetic HUD localization, availability supervision and action masking."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from brawl_ai.detection import enemy_state
from brawl_ai.hud import HudDetector
from brawl_ai.learning import ACTIONS
from brawl_ai.learning import available_actions
from brawl_ai.ui import Console


POSITIONS = {"move": (50, 200, 15), "attack": (360, 205, 16), "super": (305, 205, 14),
             "gadget": (360, 145, 12), "hypercharge": (305, 145, 14)}


def hud_scene(gray=False, charged=False):
    image = Image.new("RGB", (400, 250), "#1e2f39")
    draw = ImageDraw.Draw(image)
    colors = {"move": (20, 130, 220), "attack": (240, 35, 25),
              "super": (250, 210, 20) if charged else (45, 100, 235),
              "gadget": (90, 90, 100) if gray else (20, 245, 30),
              "hypercharge": (90, 90, 100) if gray else (190, 20, 245)}
    for control, (x, y, radius) in POSITIONS.items():
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=colors[control], outline="black", width=2)
        draw.line((x-4, y, x+4, y), fill="white", width=2)
        draw.line((x, y-4, x, y+4), fill="white", width=2)
    return image


def calibration():
    return {name: [x/400, y/250] for name, (x, y, _) in POSITIONS.items()}


def box(control):
    x, y, r = POSITIONS[control]
    return [(x-r)/400, (y-r)/250, (x+r+1)/400, (y+r+1)/250]


class HudTests(unittest.TestCase):
    def test_capture_combines_entities_and_control_boxes(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            image = hud_scene()
            console.device = Mock()
            console.device.screenshot.return_value = image
            console.detector = Mock()
            console.detector.detect.return_value = [{"box": [.4, .4, .5, .5], "identity": "NPC"}]
            console.hud_detector = HudDetector(root)
            console.controls, console.calibrated = calibration(), set(POSITIONS)
            captured, detected = console.capture_frame()
            self.assertIs(captured, image)
            self.assertEqual(len([item for item in detected if item.get("kind") == "control"]), 5)
            self.assertTrue(any(item.get("identity") == "NPC" for item in detected))

    def test_control_review_saves_state_and_confirmed_center(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.folder = Path(root)
            console.image = hud_scene(gray=True)
            console.hud_detector = HudDetector(root)
            console.controls, console.calibrated = calibration(), set(POSITIONS)
            for name in ("pause", "finish", "status", "log", "save_controls", "refresh_detection"):
                setattr(console, name, Mock())
            console.control_dialog = Mock(return_value=("gadget", "cooldown", "confirm"))
            console.review_control({"box": box("gadget"), "control": "gadget", "state": "unknown"})
            console.finish.assert_called_once_with(terminal=True)
            self.assertEqual(console.hud_detector.samples[0]["state"], "cooldown")
            console.save_controls.assert_called_once()
            self.assertEqual(console.log.call_args.args[1]["state"], "cooldown")
            self.assertTrue((Path(root)/console.log.call_args.args[1]["image"]).exists())

    def test_localizes_all_five_controls_without_weights_or_examples(self):
        with tempfile.TemporaryDirectory() as root:
            detector = HudDetector(root)
            detected = {item["control"]: item for item in detector.detect(hud_scene(), calibration(), POSITIONS)}
            self.assertEqual(set(detected), set(POSITIONS))
            for control, item in detected.items():
                expected = calibration()[control]
                self.assertAlmostEqual(item["center"][0], expected[0], delta=.02)
                self.assertAlmostEqual(item["center"][1], expected[1], delta=.02)
            self.assertEqual(detected["super"]["state"], "uncharged")
            self.assertEqual(detected["hypercharge"]["state"], "ready")
            self.assertEqual(detected["gadget"]["state"], "ready")
            charged = detector.detect(hud_scene(charged=True), calibration(), POSITIONS)
            self.assertEqual(next(item for item in charged if item["control"] == "super")["state"], "ready")

    def test_cooldown_examples_persist_and_uncertain_gray_states_do_not_block_actions(self):
        with tempfile.TemporaryDirectory() as root:
            image = hud_scene(gray=True)
            detector = HudDetector(root)
            controls = {**calibration(), "super_mode": "drag", "gadget_mode": "drag"}
            initial = detector.detect(image, controls, POSITIONS)
            gadget = next(item for item in initial if item["control"] == "gadget")
            self.assertLess(gadget["state_score"], .9)
            self.assertIn(ACTIONS.index("gadget_up"), available_actions(controls, POSITIONS, initial))
            detector.learn(image, box("gadget"), "gadget", "cooldown")
            learned = HudDetector(root).detect(image, controls, POSITIONS)
            gadget = next(item for item in learned if item["control"] == "gadget")
            self.assertEqual(gadget["state"], "cooldown")
            self.assertEqual(gadget["state_source"], "example")
            allowed = available_actions(controls, POSITIONS, learned)
            self.assertNotIn(ACTIONS.index("gadget_up"), allowed)
            self.assertNotIn(ACTIONS.index("super_up"), allowed)
            self.assertIn(ACTIONS.index("hypercharge"), allowed)
            detector.learn(image, box("hypercharge"), "hypercharge", "uncharged")
            learned = detector.detect(image, controls, POSITIONS)
            self.assertNotIn(ACTIONS.index("hypercharge"), available_actions(controls, POSITIONS, learned))

    def test_hud_boxes_are_not_counted_as_enemies_in_policy_state(self):
        image = hud_scene()
        enemy = {"box": [.2, .2, .3, .3]}
        control = {"kind": "control", "control": "super", "box": [.8, .8, .9, .9],
                   "state": "uncharged", "state_score": .95}
        state = enemy_state(image, [enemy, control], True)
        self.assertIn("|enemy-v1:1|hud-v1:super=uncharged", state)
        self.assertNotIn("enemy-v1:1,11", state)

    def test_rejected_control_proposals_are_suppressed(self):
        with tempfile.TemporaryDirectory() as root:
            image = hud_scene()
            detector = HudDetector(root)
            proposal = next(item for item in detector.detect(image, calibration(), POSITIONS) if item["control"] == "gadget")
            detector.learn(image, proposal["box"], "gadget", "rejected")
            self.assertFalse(any(item["control"] == "gadget" for item in detector.detect(image, calibration(), POSITIONS)))

    def test_automatic_centers_preserve_existing_calibration_and_skip_weak_proposals(self):
        console = Console.__new__(Console)
        console.player_session = None
        console.busy = False
        console.calibrated = {"move"}
        console.controls = {"move": [.1, .8]}
        console.save_controls = Mock()
        console.pause = Mock()
        console.finish = Mock()
        console.status = Mock()
        console.detections = [{"kind": "control", "control": name, "center": point, "score": score, "source": "color_shape"}
                              for name, point, score in (("move", [.2, .7], .99), ("attack", [.9, .8], .96), ("gadget", [.9, .6], .78))]
        console.use_detected_controls(automatic=True)
        self.assertEqual(console.controls["move"], [.1, .8])
        self.assertIn("attack", console.calibrated)
        self.assertNotIn("gadget", console.calibrated)
        console.use_detected_controls()
        self.assertEqual(console.controls["move"], [.2, .7])
        self.assertIn("gadget", console.calibrated)

    def test_flat_menu_does_not_create_fake_controls(self):
        with tempfile.TemporaryDirectory() as root:
            detector = HudDetector(root)
            self.assertEqual(detector.detect(Image.new("RGB", (400, 250), "gray"), calibration(), POSITIONS), [])
            with self.assertRaises(ValueError):
                detector.learn(Image.new("RGB", (400, 250), "gray"), box("gadget"), "gadget", "ready")
            detector.learn(hud_scene(gray=True), box("gadget"), "gadget", "cooldown")
            self.assertEqual(detector.detect(Image.new("RGB", (400, 250), "gray"), calibration(), POSITIONS), [])
