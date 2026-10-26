"""Simple recording flow, automatic analysis handoff and visible phase progress."""
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from brawl_ai.playstyle import Playstyle
from brawl_ai.recordings import MatchRecording, RecordingAnalysis
from brawl_ai.ui import Console


def ui():
    console = Console.__new__(Console)
    for name in ("root", "status", "style_status", "player_phase", "player_progress", "recording_name", "use_style"):
        setattr(console, name, Mock())
    console.player_bar = None
    console.player_session = None
    console.auto_analyze = True
    return console


class RecordingFlowTests(unittest.TestCase):
    def test_review_only_analysis_opens_editor_without_enabling_imitation(self):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            session = RecordingAnalysis(Path(root)/"12345", Playstyle(root, "Player"), Mock(), 0)
            session.success = session.review_only = True
            session.replay_path = Path(root)/"replay.mp4"
            console.open_game_review = Mock()
            session.events.put(("done", None))
            console.player_session = session
            console.poll_player()
            console.use_style.set.assert_not_called()
            console.open_game_review.assert_called_once_with(session.replay_path)
            self.assertIn("No aligned actions", console.style_status.set.call_args.args[0])

    def test_saved_recording_starts_analysis_without_another_user_action(self):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            session = MatchRecording.__new__(MatchRecording)
            session.style = Playstyle(root, "Player")
            session.folder = Path(root)/"12345"
            session.metadata = {"status": "ready"}
            session.events = queue.Queue()
            session.events.put(("progress", {"label": "Saving", "current": 1, "total": 3}))
            session.events.put(("done", None))
            console.player_session = session
            replacement = object()
            def start_analysis():
                console.player_session = replacement
                console.root.after(100, console.poll_player)
            console.analyze_game = Mock(side_effect=start_analysis)
            console.poll_player()
            console.analyze_game.assert_called_once()
            console.recording_name.set.assert_called_once_with("12345")
            self.assertIs(console.player_session, replacement)
            # The finishing recorder must not install a second analysis polling loop.
            self.assertEqual(console.root.after.call_count, 1)
            self.assertIn("33%", console.player_phase.set.call_args_list[0].args[0])

    def test_closing_flow_keeps_recording_without_starting_analysis(self):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            console.auto_analyze = False
            session = MatchRecording.__new__(MatchRecording)
            session.style = Playstyle(root, "Player")
            session.folder = Path(root)/"12345"
            session.metadata = {"status": "ready"}
            session.events = queue.Queue()
            session.events.put(("done", None))
            console.player_session = session
            console.analyze_game = Mock()
            console.poll_player()
            console.analyze_game.assert_not_called()
            self.assertIsNone(console.player_session)

    def test_successful_analysis_shows_completion_and_enables_style(self):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            session = RecordingAnalysis(Path(root)/"12345", Playstyle(root, "Player"), Mock(), 0)
            session.success = True
            session.replay_path = Path(root)/"replay.mp4"
            console.open_game_review = Mock()
            session.events.put(("progress", {"label": "Analyzing", "current": 4, "total": 10}))
            session.events.put(("done", None))
            console.player_session = session
            console.poll_player()
            self.assertIn("40%", console.player_phase.set.call_args_list[0].args[0])
            self.assertIn("100%", console.player_phase.set.call_args.args[0])
            console.use_style.set.assert_called_once_with(True)
            console.open_game_review.assert_called_once_with(session.replay_path)
            self.assertIsNone(console.player_session)

    def test_failed_analysis_is_not_presented_as_learning_success(self):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            session = RecordingAnalysis(Path(root)/"12345", Playstyle(root, "Player"), Mock(), 0)
            session.error = "Video decoding failed"
            console.open_game_review = Mock()
            session.events.put(("error", session.error))
            session.events.put(("done", None))
            console.player_session = session
            console.poll_player()
            self.assertIn("Analysis failed", console.player_phase.set.call_args.args[0])
            console.use_style.set.assert_not_called()
            console.open_game_review.assert_not_called()

    @patch("brawl_ai.ui.MatchRecording")
    @patch("brawl_ai.ui.video_tools")
    def test_start_recording_does_not_require_manual_alignment_checkboxes(self, tools, recorder):
        with tempfile.TemporaryDirectory() as root:
            console = ui()
            console.folder = Path(root)
            console.profile = Mock(record=Mock(return_value={"brawler": "Shelly", "mode": "Gem Grab"}))
            console.device = Mock()
            console.controls = {"move": [.1, .8], "attack": [.9, .8], "radius": .1}
            console.calibrated = {"move", "attack"}
            console.busy = False
            console.active_map = "One"
            console.detector = Mock()
            for name, value in (("touch_rotation", "90"), ("demo_interval", ".5"), ("player_name", "Player"),
                                ("video_fps", "30"), ("touch_auto", True)):
                setattr(console, name, Mock(get=Mock(return_value=value)))
            console.touch_aligned = Mock(get=Mock(return_value=False))
            console.sync_checked = Mock(get=Mock(return_value=False))
            for name in ("pause", "stop_capture", "cancel_box_edit", "finish"):
                setattr(console, name, Mock())
            console.start_player(True)
            recorder.return_value.start.assert_called_once()
            self.assertTrue(console.auto_analyze)
            console.touch_aligned.get.assert_not_called()
            console.sync_checked.get.assert_not_called()
            self.assertTrue(recorder.call_args.kwargs["auto_rotation"])
