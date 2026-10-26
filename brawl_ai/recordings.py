"""Record smooth video first; align, analyze and learn only after gameplay."""
import bisect
import io
import json
import math
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time

from PIL import Image

from .android import environment
from .learning import ACTIONS, available_actions
from .playstyle import Playstyle, TouchTracker, gesture_action, touch_devices
from .replay import emit_task, render_replay
from .storage import cleanup_recordings


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def video_tools():
    missing = [name for name in ("scrcpy", "ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise RuntimeError("Install these video tools first: " + ", ".join(missing))


class AnalysisCancelled(Exception):
    pass


def media_command(args, timeout, stop_event=None):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic()+timeout
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise AnalysisCancelled()
            if time.monotonic() > deadline:
                raise TimeoutError("Video processing timed out; the recording was preserved.")
            try:
                stdout, stderr = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError("Video processing failed: " + stderr.decode(errors="replace")[-600:])
        return stdout
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def stop_process(process, interrupt=False):
    if process is None:
        return
    if process.poll() is None:
        process.send_signal(signal.SIGINT if interrupt else signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait(timeout=3)
            if interrupt:
                raise RuntimeError("Video recorder did not finalize cleanly; raw files were kept.") from error


class MatchRecording:
    def __init__(self, device, style, controls, calibrated, rotation, fps, metadata, auto_rotation=True):
        self.device, self.style = device, style
        self.recording = True
        self.folder = style.folder / "recordings" / str(time.time_ns())
        self.metadata = {**metadata, "player": style.player, "controls": json.loads(json.dumps(controls)),
                         "calibrated": sorted(calibrated), "rotation": rotation, "fps_limit": fps,
                         "status": "recording", "video_origin_host": None}
        self.auto_rotation = auto_rotation
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.stopped_at = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.stopped_at is None:
            self.stopped_at = time.monotonic()
        self.stop_event.set()

    def run(self):
        video = inputs = None
        readers = []
        reader_errors = []
        error = None
        try:
            self.events.put(("progress", {"label": "Starting recording and live preview..."}))
            video_tools()
            try:
                cleanup_recordings(self.style)
            except OSError:
                pass
            devices = touch_devices(self.device.command("shell", "getevent", "-lp").decode(errors="replace"))
            if len(devices) != 1:
                raise RuntimeError("Expected one readable multitouch touchscreen for video recording.")
            if self.auto_rotation:
                try:
                    output = self.device.command("shell", "dumpsys", "input").decode(errors="replace")
                    orientation = re.search(r"SurfaceOrientation:\s*([0-3])", output)
                    if orientation:
                        self.metadata["rotation"] = int(orientation[1])*90
                except subprocess.SubprocessError:
                    pass
            self.folder.mkdir(parents=True)
            self.metadata.update(input_device=devices[0], started_host=time.monotonic())
            write_json(self.folder/"session.json", self.metadata)
            inputs = subprocess.Popen([self.device.adb, "-s", self.device.serial, "shell", "getevent", "-lt", devices[0]["path"]],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=environment())

            def read_input():
                with (self.folder/"input.jsonl").open("w", buffering=1) as stream:
                    for line in inputs.stdout:
                        received = time.monotonic()
                        match = re.search(r"\[\s*([0-9]+\.[0-9]+)\]", line)
                        stream.write(json.dumps({"host": received, "device": float(match[1]) if match else None,
                                                 "line": line.rstrip()})+"\n")

            env = environment()
            env["ADB"] = self.device.adb
            video = subprocess.Popen([shutil.which("scrcpy"), "--serial", self.device.serial,
                "--no-control", "--no-audio", "--capture-orientation=@",
                "--window-title=Brawl AI - Live recording", "--window-width=960",
                "--max-size=1280", f"--max-fps={self.metadata['fps_limit']}", "--video-bit-rate=8M",
                f"--record={self.folder/'game.mkv'}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env)

            def read_video():
                with (self.folder/"recorder.log").open("w", buffering=1) as stream:
                    for line in video.stdout:
                        stamp = time.monotonic()
                        stream.write(f"{stamp:.6f} {line}")
                        if "Recording started to" in line and self.metadata["video_origin_host"] is None:
                            self.metadata["video_origin_host"] = stamp
                            self.events.put(("status", "Video recording started. Play on the phone; analysis is deferred until after Stop."))

            def guarded_reader(target):
                try:
                    target()
                except Exception as caught:
                    reader_errors.append(str(caught))
                    self.stop_event.set()

            for target in (read_input, read_video):
                reader = threading.Thread(target=guarded_reader, args=(target,), daemon=True)
                readers.append(reader)
                reader.start()
            self.events.put(("status", "Preparing game recording..."))
            last_report = 0
            while not self.stop_event.wait(.1):
                if video.poll() == 0:
                    self.stopped_at = time.monotonic()
                    break
                if video.poll() is not None or inputs.poll() is not None:
                    raise RuntimeError("Video or input recording ended unexpectedly. Raw files were kept; check recorder.log and USB.")
                now = time.monotonic()
                origin = self.metadata["video_origin_host"]
                if origin is not None and now-last_report >= 1:
                    movie = self.folder/"game.mkv"
                    size = movie.stat().st_size/1048576 if movie.exists() else 0
                    elapsed = int(now-origin)
                    self.events.put(("progress", {"label": f"Recording {elapsed//60:02d}:{elapsed%60:02d} | {size:.1f} MB | live preview is open"}))
                    last_report = now
        except Exception as caught:
            error = str(caught)
        finally:
            self.metadata["stopped_host"] = self.stopped_at or time.monotonic()
            emit_task(self.events, "video")
            self.events.put(("progress", {"label": "Saving game: finalizing video...", "current": 0, "total": 3}))
            for index, (process, interrupt) in enumerate(((video, True), (inputs, False)), 1):
                try:
                    stop_process(process, interrupt)
                    if index == 1:
                        movie = self.folder/"game.mkv"
                        if movie.exists() and movie.stat().st_size > 0 and self.metadata["video_origin_host"] is not None:
                            emit_task(self.events, "video", 1, 1)
                        emit_task(self.events, "input")
                    label = "Video saved. Saving your actions..." if index == 1 else "Video and actions saved. Finishing..."
                    self.events.put(("progress", {"label": label, "current": index, "total": 3}))
                except Exception as caught:
                    error = error or str(caught)
            for reader in readers:
                reader.join(timeout=3)
                if reader.is_alive():
                    error = error or "A recording stream did not close cleanly."
            if reader_errors:
                error = error or reader_errors[0]
            for process in (video, inputs):
                if process is not None and process.stdout is not None:
                    process.stdout.close()
            if self.folder.exists():
                movie = self.folder/"game.mkv"
                if not movie.exists() or movie.stat().st_size == 0 or self.metadata["video_origin_host"] is None:
                    error = error or "No finalized video was recorded."
                self.metadata.update(status="failed" if error else "ready", error=error,
                                     timing_note="Video origin is a host log estimate; an optional sync offset can be configured.")
                try:
                    write_json(self.folder/"session.json", self.metadata)
                except OSError as caught:
                    error = str(caught)
            if error:
                self.events.put(("error", error))
            else:
                emit_task(self.events, "input", 1, 1)
                self.events.put(("progress", {"label": "Game saved. Preparing post-game analysis...", "current": 3, "total": 3}))
            self.events.put(("done", None))


def load_gestures(folder):
    metadata = json.loads((folder/"session.json").read_text())
    if metadata["status"] != "ready":
        raise ValueError("This recording did not finish successfully; keep the raw files and record again.")
    rows = [json.loads(line) for line in (folder/"input.jsonl").read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row["device"] is not None and row["host"] <= metadata["stopped_host"]]
    if not rows:
        raise ValueError("No timestamped phone input was recorded.")
    clock_offset = min(row["host"]-row["device"] for row in rows)
    tracker = TouchTracker(metadata["input_device"], metadata["rotation"])
    results = []
    tick = rows[0]["device"]+clock_offset
    last_touch = last_idle = None

    def advance(until):
        nonlocal tick, last_touch, last_idle
        while tick <= until:
            for held in tracker.held(tick):
                held["held"] = True
                results.append(held)
            if tracker.slots:
                last_touch = tick
            elif last_touch is not None and tick-max(last_touch, last_idle or last_touch) >= 1:
                results.append({"idle": True, "started": tick, "ended": tick, "touch_started": tick,
                                "start": None, "end": None, "slot": None})
                last_idle = tick
            tick += .5

    for row in rows:
        now = row["device"]+clock_offset
        advance(now)
        gestures = tracker.feed(row["line"], now)
        results.extend(gestures)
        if tracker.slots or gestures:
            last_touch = now
    advance(metadata["stopped_host"])
    return metadata, sorted(results, key=lambda item: item["started"])


def video_info(video, stop_event=None):
    result = media_command(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                             "-show_entries", "frame=best_effort_timestamp_time,width,height", "-of", "json", str(video)],
                            timeout=120, stop_event=stop_event)
    frames = json.loads(result)["frames"]
    valid = [(float(frame["best_effort_timestamp_time"]), (frame["width"], frame["height"]))
             for frame in frames if "best_effort_timestamp_time" in frame]
    if not valid:
        raise ValueError("Video contains no decodable timestamped frames.")
    if len({size for _, size in valid}) != 1:
        raise ValueError("Display dimensions changed during recording; record again without rotating the phone.")
    return [stamp for stamp, _ in valid], valid[0][1]


def preceding_frame(timestamps, action_time):
    index = bisect.bisect_right(timestamps, action_time-.04)-1
    if index < 0 or action_time-timestamps[index] > 1:
        return None
    return timestamps[index]


def decode_frame(video, stamp, stop_event=None):
    result = media_command(["ffmpeg", "-v", "error", "-ss", f"{max(0, stamp-.00001):.6f}", "-i", str(video),
                             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                            timeout=30, stop_event=stop_event)
    return Image.open(io.BytesIO(result)).convert("RGB")


def action_requests(metadata, gestures, timestamps, size, offset):
    if not math.isfinite(offset) or abs(offset) > 30:
        raise ValueError("Video sync offset must be between -30 and 30 seconds.")
    available = available_actions(metadata["controls"], set(metadata["calibrated"]))
    requests = []
    for gesture in gestures:
        action = "idle" if gesture.get("idle") else gesture_action(gesture, metadata["controls"], set(metadata["calibrated"]), size)
        if action is None or ACTIONS.index(action) not in available:
            continue
        began = gesture["touch_started"] if action in ("attack", "super", "gadget", "hypercharge") else gesture["started"]
        action_time = began-metadata["video_origin_host"]+offset
        stamp = preceding_frame(timestamps, action_time)
        if stamp is not None and action_time <= timestamps[-1]:
            requests.append({"action": action, "gesture": gesture, "video_time": action_time, "frame_time": stamp})
    return requests


class RecordingAnalysis:
    def __init__(self, folder, style, state_builder, offset, recognition_ready=True):
        self.folder, self.style, self.state_builder, self.offset = Path(folder), style, state_builder, offset
        self.recording = False
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.success = False
        self.error = None
        self.recognition_ready = recognition_ready
        self.replay_path = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        stage = None
        try:
            self.events.put(("progress", {"label": "Analysis: reading video and recorded actions..."}))
            token = self.folder.name
            if token in self.style.applied_recordings:
                raise ValueError("This recording was already learned; it will not be counted twice.")
            emit_task(self.events, "gestures")
            metadata, gestures = load_gestures(self.folder)
            emit_task(self.events, "gestures", 1, 1)
            emit_task(self.events, "index")
            timestamps, size = video_info(self.folder/"game.mkv", self.stop_event)
            metadata["video_size"] = size
            emit_task(self.events, "index", 1, 1)
            requests = action_requests(metadata, gestures, timestamps, size, self.offset)
            if not requests:
                raise ValueError("No supported actions align with the video. Check rotation, controls and sync offset.")
            self.events.put(("progress", {"label": "Analysis: extracting examples...", "current": 0, "total": len(requests)}))
            # Stage an entire analysis; only one final atomic model save applies it.
            working = Playstyle(self.style.folder.parent.parent, self.style.player)
            if token in working.applied_recordings:
                raise ValueError("This recording was already learned; it will not be counted twice.")
            stage = self.folder/"analyses"/str(time.time_ns())
            stage.mkdir(parents=True)
            cache = {}
            emit_task(self.events, "examples", 0, len(requests))
            with (stage/"examples.jsonl").open("w") as stream:
                for index, request in enumerate(requests):
                    if self.stop_event.is_set():
                        self.events.put(("status", "Analysis cancelled. No player-model updates were applied."))
                        return
                    stamp = request["frame_time"]
                    if stamp not in cache:
                        image = decode_frame(self.folder/"game.mkv", stamp, self.stop_event)
                        state, detections = self.state_builder(image)
                        filename = f"frame-{index}.jpg"
                        image.thumbnail((960, 540))
                        image.save(stage/filename, quality=85)
                        cache[stamp] = (state, filename, detections)
                    state, filename, detections = cache[stamp]
                    working.observe(state, request["action"], request["gesture"], size)
                    stream.write(json.dumps({**request, "state": state, "image": filename, "detections": detections})+"\n")
                    emit_task(self.events, "examples", index+1, len(requests))
                    self.events.put(("status", f"Post-game analysis: {index+1}/{len(requests)} actions"))
                    self.events.put(("progress", {"label": f"Analysis: {index+1}/{len(requests)} actions", "current": index+1, "total": len(requests)}))
            if self.stop_event.is_set():
                self.events.put(("status", "Analysis cancelled. No player-model updates were applied."))
                return
            self.replay_path = render_replay(self.folder, stage, metadata,
                timestamps[-1]+max(.02, min(.2, timestamps[-1]-timestamps[-2] if len(timestamps)>1 else .1)),
                self.state_builder, self.offset, self.stop_event, self.events, self.recognition_ready)
            if self.stop_event.is_set():
                raise AnalysisCancelled()
            write_json(stage/"result.json", {"recording": token, "examples": len(requests),
                                             "sync_offset": self.offset, "map": metadata.get("map"),
                                             "replay": "replay.mp4"})
            working.applied_recordings.append(token)
            self.events.put(("progress", {"label": "Saving learned playstyle..."}))
            emit_task(self.events, "model")
            working.save()
            emit_task(self.events, "model", 1, 1)
            self.style = working
            self.success = True
            try:
                reclaimed = cleanup_recordings(working)
                self.events.put(("status", f"Automatic cleanup freed {reclaimed/1048576:.1f} MB. Learning data and the newest replay were kept."))
            except OSError:
                self.events.put(("status", "Learning saved; automatic cleanup will retry next time."))
            self.events.put(("progress", {"label": f"Done: learned {len(requests)} actions", "current": 1, "total": 1}))
            self.events.put(("status", f"Analysis complete: learned {len(requests)} actions from the saved game."))
        except AnalysisCancelled:
            self.events.put(("status", "Analysis cancelled. No player-model updates were applied."))
        except Exception as error:
            self.error = str(error)
            self.events.put(("error", str(error)))
        finally:
            if stage is not None and not self.success:
                try:
                    shutil.rmtree(stage)
                except OSError:
                    pass
            self.events.put(("done", None))
