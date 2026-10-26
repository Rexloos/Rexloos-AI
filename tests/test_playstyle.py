"""Player input parsing and supervised preferences, without live game claims."""
import json
import queue
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from brawl_ai.learning import ACTIONS
from brawl_ai.maps import map_state
from brawl_ai.playstyle import Playstyle, PlayerSession, TouchTracker, gesture_action, rotate_point, touch_devices
from brawl_ai.ui import Console


DEVICE = {"path": "/dev/input/event4", "X": (0, 1000), "Y": (0, 1000), "protocol": "B"}
CONTROLS = {"move": [.1, .8], "attack": [.9, .8], "super": [.7, .8], "gadget": [.6, .8],
            "gadget_mode": "drag", "super_mode": "tap", "hypercharge": [.5, .8], "radius": .1}


def gesture(start, end, started=2, touch_started=None):
    return {"start": start, "end": end, "started": started, "touch_started": started if touch_started is None else touch_started,
            "ended": started+.2, "slot": 0}


class PlaystyleTests(unittest.TestCase):
    def test_later_sessions_accumulate_previous_examples_in_same_profile(self):
        with tempfile.TemporaryDirectory() as root:
            for action in ("right", "attack", "right"):
                model = Playstyle(Path(root)/"one", "Player")
                model.observe("same-state", action)
                model.save()
            loaded = Playstyle(Path(root)/"one", "Player")
            self.assertEqual(loaded.examples, 3)
            self.assertEqual(loaded.counts["same-state"][ACTIONS.index("right")], 2)
            self.assertEqual(loaded.counts["same-state"][ACTIONS.index("attack")], 1)
            self.assertEqual(Playstyle(Path(root)/"other", "Player").examples, 0)
            self.assertEqual(Playstyle(Path(root)/"one", "Another player").examples, 0)

    def test_motion_preferences_persist_and_only_adjust_imitated_controls(self):
        with tempfile.TemporaryDirectory() as root:
            model = Playstyle(root, "Player")
            state = map_state("state", "One")
            example = gesture((.1, .8), (.2, .8))
            model.observe(state, "right", example, (1000, 500))
            model.save()
            loaded = Playstyle(root, "Player")
            changed = loaded.action_controls(state, "right", CONTROLS)
            self.assertAlmostEqual(changed["move_radius"], .2)
            self.assertEqual(changed["move_duration"], 200)
            self.assertNotIn("move_duration", CONTROLS)
            self.assertEqual(loaded.action_controls(map_state("state", "Two"), "right", CONTROLS), CONTROLS)

    def test_discovers_honor_type_a_and_type_b_touch_capabilities(self):
        output = '''add device 1: /dev/input/event4
 name: "honor,ts_kit"
 ABS_MT_POSITION_X : value 0, min 0, max 1199, fuzz 0
 ABS_MT_POSITION_Y : value 0, min 0, max 1919, fuzz 0
add device 2: /dev/input/event0
 KEY_POWER
'''
        devices = touch_devices(output)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["protocol"], "A")
        self.assertEqual(devices[0]["Y"], (0, 1919))
        self.assertEqual(touch_devices(output+" ABS_MT_SLOT : value 0, min 0, max 9\n")[0]["protocol"], "A")
        self.assertEqual(touch_devices(output.replace(' name:', ' ABS_MT_SLOT : value 0\n name:'))[0]["protocol"], "B")
        self.assertEqual(touch_devices("could not open /dev/input/event4, Permission denied"), [])

    def test_maps_all_rotations(self):
        for rotation, expected in ((0, (.2, .3)), (90, (.3, .8)), (180, (.8, .7)), (270, (.7, .2))):
            self.assertEqual(rotate_point(200, 300, DEVICE, rotation), expected)

    def test_type_b_tracks_two_fingers_and_held_motion(self):
        tracker = TouchTracker(DEVICE, 0)
        for line in ("ABS_MT_TRACKING_ID 00000001", "ABS_MT_POSITION_X 00000064", "ABS_MT_POSITION_Y 00000320",
                     "ABS_MT_SLOT 00000001", "ABS_MT_TRACKING_ID 00000002", "ABS_MT_POSITION_X 00000384", "ABS_MT_POSITION_Y 00000320"):
            tracker.feed(line, 1)
        tracker.feed("ABS_MT_SLOT 00000000", 1.2)
        tracker.feed("ABS_MT_POSITION_X 000000c8", 1.2)
        held = tracker.held(1.6)
        self.assertEqual(len(held), 2)
        self.assertEqual(held[0]["end"], (.2, .8))
        self.assertEqual(tracker.feed("ABS_MT_TRACKING_ID ffffffff", 1.8)[0]["slot"], 0)
        self.assertEqual(len(tracker.slots), 1)

    def test_type_a_identifies_contacts_when_order_changes_and_on_release(self):
        tracker = TouchTracker({**DEVICE, "protocol": "A"}, 0)
        def packet(contact_id, x, now):
            for line in (f"ABS_MT_TRACKING_ID {contact_id:08x}", f"ABS_MT_POSITION_X {x:08x}",
                         "ABS_MT_POSITION_Y 00000320", "SYN_MT_REPORT 00000000"):
                tracker.feed(line, now)
        packet(5, 100, 1)
        packet(7, 900, 1)
        tracker.feed("SYN_REPORT 00000000", 1)
        packet(7, 900, 1.2)
        packet(5, 200, 1.2)
        tracker.feed("SYN_REPORT 00000000", 1.2)
        packet(7, 900, 1.3)
        released = tracker.feed("SYN_REPORT 00000000", 1.3)
        self.assertEqual(released[0]["slot"], 5)
        self.assertEqual(released[0]["end"], (.2, .8))
        final = tracker.feed("SYN_REPORT 00000000", 1.4)
        self.assertEqual(final[0]["slot"], 7)
        with self.assertRaises(RuntimeError):
            tracker.feed("SYN_DROPPED 00000000", 2)

    def test_calibrated_gestures_map_only_to_supported_actions(self):
        calibrated = set(CONTROLS)
        cases = [(gesture((.1, .8), (.2, .8)), "right"),
                 (gesture((.9, .8), (.9, .8)), "attack"),
                 (gesture((.6, .8), (.6, .7)), "gadget_up"),
                 (gesture((.5, .8), (.5, .8)), "hypercharge"),
                 (gesture((.4, .1), (.4, .1)), None)]
        for example, expected in cases:
            self.assertEqual(gesture_action(example, CONTROLS, calibrated, (1000, 500)), expected)
        self.assertIsNone(gesture_action(gesture((.5, .8), (.5, .8)), CONTROLS, {"move", "attack"}, (1000, 500)))
        held = {**gesture((.9, .8), (.9, .8)), "held": True}
        self.assertIsNone(gesture_action(held, CONTROLS, calibrated, (1000, 500)))

    def test_player_models_start_empty_persist_and_keep_map_contexts_separate(self):
        with tempfile.TemporaryDirectory() as root:
            model = Playstyle(Path(root)/"profile", "Adam")
            available = [ACTIONS.index("right"), ACTIONS.index("attack")]
            self.assertIsNone(model.choose("state", available))
            one, two = map_state("state", "Map One"), map_state("state", "Map Two")
            for _ in range(3):
                model.observe(one, "right")
            model.observe(two, "attack")
            model.save()
            loaded = Playstyle(Path(root)/"profile", "adam")
            self.assertEqual(loaded.examples, 4)
            self.assertEqual(loaded.choose(one, available), ACTIONS.index("right"))
            self.assertEqual(loaded.choose(map_state("unseen", "Map Two"), available), ACTIONS.index("attack"))
            self.assertIsNone(loaded.choose(map_state("unseen", "Map Three"), available))
            self.assertEqual(Playstyle(Path(root)/"other-profile", "Adam").examples, 0)
            self.assertEqual(Playstyle(Path(root)/"profile", "Other player").examples, 0)
            self.assertIsNone(loaded.choose(one, [0]))

    def test_recording_uses_only_pre_action_frames_and_saves_immediately(self):
        with tempfile.TemporaryDirectory() as root:
            model = Playstyle(root, "Adam")
            device = Mock()
            session = PlayerSession(device, model, CONTROLS, {"move", "attack"}, 0, .5, Mock(), True,
                                    {"brawler": "Shelly", "mode": "Gem Grab", "map": "One"})
            frames = [{"image": Image.new("RGB", (1000, 500)), "captured": 1.8, "state": "before"},
                      {"image": Image.new("RGB", (1000, 500)), "captured": 2.1, "state": "after"}]
            self.assertTrue(session.record_gesture(gesture((.9, .8), (.9, .8)), frames))
            self.assertEqual(Playstyle(root, "Adam").examples, 1)
            record = json.loads((model.folder/"demonstrations.jsonl").read_text())
            self.assertEqual(record["state"], "before")
            self.assertEqual(record["action"], "attack")
            self.assertEqual(record["map"], "One")
            self.assertTrue((model.folder/record["image"]).exists())
            self.assertFalse(session.record_gesture(gesture((.9, .8), (.9, .8), started=10), frames))
            self.assertFalse(session.record_gesture(gesture((.9, .8), (.9, .8), started=1), frames))
            session.recording = False
            self.assertFalse(session.record_gesture(gesture((.9, .8), (.9, .8)), frames))
            device.action.assert_not_called()

    def test_idle_demonstrations_and_held_buttons_use_correct_pre_action_state(self):
        with tempfile.TemporaryDirectory() as root:
            model = Playstyle(root, "Player")
            session = PlayerSession(Mock(), model, CONTROLS, {"move", "attack"}, 0, .5, Mock(), True)
            frames = [{"image": Image.new("RGB", (1000, 500)), "captured": 1.8, "state": "before-press"},
                      {"image": Image.new("RGB", (1000, 500)), "captured": 2.5, "state": "after-press"}]
            self.assertTrue(session.record_gesture(gesture((.9, .8), (.9, .8), started=3, touch_started=2), frames))
            self.assertIn("before-press", model.counts)
            self.assertNotIn("after-press", model.counts)
            idle = {"idle": True, "started": 3, "ended": 3, "touch_started": 3,
                    "start": None, "end": None, "slot": None}
            self.assertTrue(session.record_gesture(idle, frames))
            self.assertEqual(model.counts["after-press"][ACTIONS.index("idle")], 1)

    @patch("brawl_ai.playstyle.subprocess.Popen")
    def test_permission_failure_leaves_models_untouched_and_finishes(self, popen):
        with tempfile.TemporaryDirectory() as root:
            device = Mock()
            device.command.return_value = b"Permission denied"
            style = Playstyle(root, "Player")
            session = PlayerSession(device, style, {}, set(), 0, .5, Mock(), True)
            session.run()
            kinds = [session.events.get_nowait()[0], session.events.get_nowait()[0]]
            self.assertEqual(kinds, ["error", "done"])
            self.assertFalse(style.path.exists())
            popen.assert_not_called()

    def test_observation_blocks_ai_and_profile_changes(self):
        console = Console.__new__(Console)
        console.player_session = Mock()
        console.pause = Mock()
        console.status = Mock()
        console.step()
        console.switch_profile()
        self.assertEqual(console.pause.call_count, 2)
        self.assertIn("Stop player observation", console.status.set.call_args.args[0])

    def test_finished_session_unlocks_ui_and_exposes_learned_style(self):
        with tempfile.TemporaryDirectory() as root:
            console = Console.__new__(Console)
            console.root = Mock()
            console.style_status = Mock()
            console.status = Mock()
            session = Mock()
            session.style = Playstyle(root, "Player")
            session.style.observe("state", "attack")
            session.events = queue.Queue()
            session.events.put(("done", None))
            console.player_session = session
            console.poll_player()
            self.assertIsNone(console.player_session)
            self.assertEqual(console.style.examples, 1)
            console.root.after.assert_not_called()
