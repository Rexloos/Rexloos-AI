"""Storage retention must never discard unapplied learning or training references."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from brawl_ai.storage import cleanup_recordings


class StorageTests(unittest.TestCase):
    def fixture(self, root, token, learned=True, complete=True):
        folder = Path(root)/"recordings"/token
        stage = folder/"analyses"/"1"
        stage.mkdir(parents=True)
        for name in ("game.mkv", "input.jsonl", "recorder.log", "session.json"):
            (folder/name).write_bytes(b"raw")
        for name in ("frame-0.jpg", "examples.jsonl", "replay.json", "replay.mp4"):
            (stage/name).write_bytes(b"data")
        if complete:
            (stage/"result.json").write_text(json.dumps({"recording": token}))
        return stage

    def test_only_applied_successful_recordings_lose_raw_media(self):
        with tempfile.TemporaryDirectory() as root:
            done = self.fixture(root, "done")
            pending = self.fixture(root, "pending")
            broken = self.fixture(root, "broken", complete=False)
            style = SimpleNamespace(folder=Path(root), applied_recordings=["done", "broken"])
            self.assertEqual(cleanup_recordings(style), 9)
            self.assertFalse((done.parent.parent/"game.mkv").exists())
            for stage in (pending, broken):
                self.assertTrue((stage.parent.parent/"game.mkv").exists())
            for name in ("frame-0.jpg", "examples.jsonl", "replay.mp4"):
                self.assertTrue((done/name).exists())
            self.assertEqual(cleanup_recordings(style), 0)

    def test_budget_removes_old_replays_but_keeps_newest_and_training(self):
        with tempfile.TemporaryDirectory() as root:
            old, new = [self.fixture(root, token) for token in ("old", "new")]
            os.utime(old/"replay.mp4", (1, 1))
            os.utime(new/"replay.mp4", (2, 2))
            style = SimpleNamespace(folder=Path(root), applied_recordings=["old", "new"])
            cleanup_recordings(style, budget=0)
            self.assertFalse((old/"replay.mp4").exists())
            self.assertFalse((old/"replay.json").exists())
            self.assertTrue((new/"replay.mp4").exists())
            self.assertTrue((old/"frame-0.jpg").exists())
            self.assertTrue((old/"examples.jsonl").exists())

    def test_symlinks_and_path_traversal_are_ignored(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            stage = self.fixture(outside, "safe")
            (Path(root)/"recordings").mkdir()
            (Path(root)/"recordings"/"link").symlink_to(stage.parent.parent, target_is_directory=True)
            style = SimpleNamespace(folder=Path(root), applied_recordings=["link", "../safe"])
            self.assertEqual(cleanup_recordings(style), 0)
            self.assertTrue((stage.parent.parent/"game.mkv").exists())
