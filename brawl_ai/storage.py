"""Prune disposable recording media only after its learning was committed."""
import json
from pathlib import Path


REPLAY_BUDGET = 2 * 1024**3


def cleanup_recordings(style, budget=REPLAY_BUDGET):
    """Keep training references and the newest replay, even if it exceeds budget.

    Never follow symlinks or infer learning success from an export alone.
    Failures are nonfatal; a subsequent pass can retry individual deletions.
    """
    root = style.folder / "recordings"
    removed = 0
    replays = []

    def discard(path):
        nonlocal removed
        try:
            if path.is_file() and not path.is_symlink():
                size = path.stat().st_size
                path.unlink()
                removed += size
        except OSError:
            pass

    for token in set(style.applied_recordings):
        if not isinstance(token, str) or Path(token).name != token or token in ("", ".", ".."):
            continue
        folder = root/token
        if root.is_symlink() or folder.is_symlink():
            continue
        analyses = folder/"analyses"
        if analyses.is_symlink():
            continue
        completed = []
        preserve_raw = False
        for stage in analyses.glob("*"):
            if stage.is_symlink() or not stage.is_dir():
                continue
            try:
                result = stage/"result.json"
                if result.is_symlink():
                    continue
                data = json.loads(result.read_text())
                if data["recording"] != token:
                    continue
                replay = stage/"replay.mp4"
                if replay.is_file() and not replay.is_symlink() and replay.stat().st_size:
                    completed.append(replay)
                    preserve_raw = preserve_raw or bool(data.get("timing_warning"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if not completed:
            continue
        # The model and a playable replacement both exist before raw media goes.
        if not preserve_raw:
            for name in ("game.mkv", "input.jsonl", "recorder.log"):
                discard(folder/name)
        for replay in completed:
            replays.append((replay.stat().st_mtime_ns, replay, replay.stat().st_size))
    replays.sort(reverse=True)
    total = sum(item[2] for item in replays)
    for _, replay, size in reversed(replays[1:]):
        if total <= budget:
            break
        discard(replay)
        if not replay.exists():
            total -= size
            discard(replay.with_suffix(".json"))
    return removed
