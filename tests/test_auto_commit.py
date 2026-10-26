"""Exercise local commits in disposable repositories without any remotes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class AutoCommitTests(unittest.TestCase):
    def test_commits_full_scope_ignores_assets_and_skips_empty_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "--quiet")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (root/"scripts").mkdir()
            script = root/"scripts"/"auto_commit.sh"
            shutil.copyfile(Path(__file__).resolve().parents[1]/"scripts"/"auto_commit.sh", script)
            (root/".gitignore").write_text("cache/\n")
            (root/"tracked.txt").write_text("before")
            git("add", ".")
            git("commit", "--quiet", "-m", "Initial")
            (root/"tracked.txt").write_text("after")
            (root/"staged.txt").write_text("staged")
            git("add", "staged.txt")
            (root/"new.txt").write_text("new")
            (root/"cache").mkdir()
            (root/"cache"/"video.mkv").write_bytes(b"ignored")
            title = 'feat: commit local changes with "quotes" and $literal'
            run = lambda value: subprocess.run(["bash", str(script), value], cwd="/tmp", text=True, capture_output=True)
            result = run(title)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("log", "-1", "--format=%s"), title)
            self.assertEqual(git("status", "--porcelain"), "")
            self.assertNotIn("cache/video.mkv", git("ls-files"))
            head = git("rev-parse", "HEAD")
            self.assertIn("No changes", run("chore: update project").stdout)
            self.assertEqual(git("rev-parse", "HEAD"), head)
            self.assertEqual(run("\n").returncode, 2)
            self.assertEqual(git("rev-parse", "HEAD"), head)
