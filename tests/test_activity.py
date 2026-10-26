"""Activity retention and rate limits without requiring a desktop."""
from pathlib import Path
from io import StringIO
import tempfile
import unittest
from unittest.mock import patch

from brawl_ai.activity import ActivityLog


class ActivityTests(unittest.TestCase):
    def test_console_output_is_immediate_and_survives_file_failure(self):
        with tempfile.TemporaryDirectory() as root, patch("sys.stdout", new_callable=StringIO) as output:
            log = ActivityLog(Path(root))
            log.add("progress", "Learning")
            self.assertEqual(output.getvalue(), log.snapshot()[0] + "\n")
            log.add("progress", "Learning")
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            log.failed()
            log.add("error", "Disk unavailable")
            self.assertIn("[error] Disk unavailable\n", output.getvalue())
            self.assertNotIn("Disk unavailable", log.path.read_text())
            log.close()

    def test_rate_limit_duplicates_and_idle_heartbeat(self):
        with tempfile.TemporaryDirectory() as root:
            log = ActivityLog(Path(root))
            with patch("brawl_ai.activity.time.monotonic", side_effect=[0, 1, 3, 4, 5, 20]):
                log.add("progress", "one", interval=2)
                log.add("progress", "two", interval=2)
                log.add("progress", "three", interval=2)
                log.add("progress", "three")
                log.add("heartbeat", "idle", interval=15, repeat=True)
                log.add("heartbeat", "idle", interval=15, repeat=True)
            lines = log.snapshot()
            log.close()
            self.assertEqual(len(lines), 4)
            self.assertIn("[progress] one", lines[0])
            self.assertIn("[progress] three", lines[1])
            self.assertEqual(log.path.read_text().count("[heartbeat] idle"), 2)

    def test_bounded_memory_and_rotated_files(self):
        with tempfile.TemporaryDirectory() as root:
            log = ActivityLog(Path(root))
            log.logger.handlers[0].maxBytes = 200
            for index in range(600):
                log.add("event", f"Event {index}: details")
            log.close()
            self.assertEqual(len(log.snapshot()), 500)
            self.assertLessEqual(len(list(Path(root).glob("activity.log*"))), 4)
            self.assertIn("Event 599", log.path.read_text())

    def test_file_failure_preserves_visible_history(self):
        with tempfile.TemporaryDirectory() as root:
            blocked = Path(root)/"file"
            blocked.write_text("not a directory")
            log = ActivityLog(blocked)
            log.add("error", "Still visible")
            self.assertIsNotNone(log.failure)
            self.assertIn("Still visible", log.snapshot()[0])
            log.close()
