"""Desktop teaching console. Device work stays off Tk's UI thread."""
import json
import math
import random
import traceback
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from PIL import ImageTk
from .android import Device, cache_root
from .learning import ACTIONS, Learner, available_actions
from .profiles import Profile, choices
from .curriculum import recommend
from .detection import ENTITY_NAMES, EnemyDetector, enemy_state
from .maps import MapRecognizer, map_state
from .capture import capture_interval
from .playstyle import Playstyle, PlayerSession
from .recordings import (MatchRecording, RecordingAnalysis, video_tools, load_gestures,
                         video_info, action_requests, decode_frame)
from .replay import ReplayPlayer
from .hud import CONTROLS, STATES, HudDetector, hud_label
from .player import PlayerDetector, merge_player
from .activity import ActivityLog
from .dashboard import build_dashboard


class Console:
    def __init__(self, serial, brawler="Shelly", mode="Training Ground"):
        self.training_root = cache_root() / "training"
        self.profile = Profile(self.training_root, brawler, mode)
        self.device = Device(serial)
        self.root = tk.Tk()
        self.root.title("Brawl AI — teaching console")
        self.folder = self.profile.prepare()
        self.agent = Learner(self.folder / "policy.json")
        self.detector = EnemyDetector(self.folder)
        self.hud_detector = HudDetector(self.folder)
        self.self_detector = PlayerDetector(self.folder, self.profile.brawler)
        self.detections = []
        self.map_recognizer = MapRecognizer(self.training_root, self.profile.mode)
        self.active_map = self.map_prediction = None
        self.controls_path = self.training_root / "controls.json"
        self.calibrated = set(json.loads(self.controls_path.read_text()).get("calibrated", [])) if self.controls_path.exists() else set()
        self.controls = json.loads(self.controls_path.read_text()) if self.controls_path.exists() else {"move": [0.15, 0.75], "attack": [0.85, 0.75], "radius": 0.10}
        self.events = queue.Queue()
        self.busy = self.running = False
        self.image = self.pending = self.mode = None
        self.box_edit = None
        self.hud_box_edit = None
        self.pending_available = None
        self.reviewing = False
        self.work_kind = None
        self.preview_revision = 0
        self.deferred_preview = None
        self.player_session = None
        self.reward = 0.0
        self.auto_job = None
        build_dashboard(self)
        self.activity = ActivityLog(self.training_root/"logs")
        self.activity_window = self.activity_text = None
        for name in ("status", "style_status", "player_phase", "capture_status", "detection_status", "profile_status", "reward_status", "map_status"):
            variable = getattr(self, name)
            variable.trace_add("write", lambda *args, name=name, variable=variable:
                               self.activity.add(name, variable.get(), 2 if name in ("player_phase", "detection_status", "capture_status") else 0))
        self.activity.add("session", f"Console started | {self.profile.brawler} / {self.profile.mode} | log: {self.activity.path}")
        self.root.after(1000, self.activity_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)
        self.load_gadget_mode()

    def resize_preview(self, event):
        if getattr(self, "preview_resize_job", None) is not None:
            self.root.after_cancel(self.preview_resize_job)
        # Keep coordinates stable while a rectangle is being drawn or reviewed.
        if self.mode or self.reviewing:
            return
        def resize():
            self.preview_resize_job = None
            if self.mode or self.reviewing:
                return
            self.preview_limit = (max(1, event.width), max(1, event.height))
            if self.image is not None:
                self.render_preview()
        self.preview_resize_job = self.root.after(120, resize)

    def open_activity_log(self):
        if self.activity_window is not None and self.activity_window.winfo_exists():
            self.activity_window.lift()
            return
        self.activity_window = tk.Toplevel(self.root)
        self.activity_window.title("Brawl AI - Activity log")
        tk.Label(self.activity_window, text=f"Saved to: {self.activity.path}", wraplength=850).pack()
        from tkinter.scrolledtext import ScrolledText
        self.activity_text = ScrolledText(self.activity_window, width=110, height=24, state="disabled", wrap="word")
        self.activity_text.pack(fill="both", expand=True)
        self.activity_rendered = None
        self.render_activity()

    def render_activity(self):
        if self.activity_text is not None and self.activity_text.winfo_exists():
            lines = self.activity.snapshot()
            if lines == getattr(self, "activity_rendered", None):
                return
            at_bottom = self.activity_text.yview()[1] >= .99
            position = self.activity_text.yview()[0]
            self.activity_text.configure(state="normal")
            self.activity_text.delete("1.0", "end")
            self.activity_text.insert("end", "\n".join(lines))
            self.activity_rendered = lines
            self.activity_text.configure(state="disabled")
            self.activity_text.yview_moveto(1 if at_bottom else position)

    def activity_tick(self):
        session = self.player_session
        task = ("recording" if session.recording else "analysis/input preview") if session else "none"
        elapsed = time.monotonic()-getattr(self, "work_started", time.monotonic()) if self.busy else 0
        message = (f"Profile: {self.profile.brawler}/{self.profile.mode} | AI: {'running' if self.running else 'paused'} | "
                   f"Device: {self.work_kind or 'idle'} ({elapsed:.0f}s) | Player task: {task} | "
                   f"Capture: {self.capture_blocked() or 'ready'}")
        self.activity.add("heartbeat", message, interval=15, repeat=True)
        self.activity_summary.set(self.activity.failure or message)
        self.render_activity()
        self.root.after(1000, self.activity_tick)

    def update_profile_status(self):
        self.profile_status.set(f"Active: {self.profile.brawler} / {self.profile.mode} | Learned transitions: {self.agent.steps}")

    def player_guard(self):
        if getattr(self, "player_session", None) is not None:
            self.pause()
            self.status.set("Stop player observation before changing profiles, maps, controls or sending AI actions.")
            return True
        return False

    def player_window(self):
        if self.player_dialog is not None and self.player_dialog.winfo_exists():
            self.player_dialog.lift()
            return
        window = tk.Toplevel(self.root)
        self.player_dialog = window
        window.title("Learn a player's playstyle")
        tk.Label(window, wraplength=560, justify="left", text="Start recording, play on your phone, then stop. A live video window shows what is recorded. Saving and analysis run automatically after Stop.").pack(padx=12, pady=10)
        primary = tk.Frame(window)
        primary.pack(pady=8)
        self.player_record_button = tk.Button(primary, text="Start recording", command=lambda: self.start_player(True))
        self.player_record_button.pack(side="left", padx=8)
        self.player_stop_button = tk.Button(primary, text="Stop recording / cancel analysis", command=self.stop_player)
        self.player_stop_button.pack(side="left", padx=8)
        tk.Label(window, textvariable=self.player_phase, wraplength=560).pack(padx=12, pady=8)
        self.player_bar = ttk.Progressbar(window, variable=self.player_progress, maximum=100, length=500)
        self.player_bar.pack(padx=12, pady=6)
        task_list = tk.Frame(window)
        task_list.pack(padx=12, pady=8)
        self.task_bars = {}
        for index, (key, item) in enumerate(self.player_tasks.items()):
            tk.Label(task_list, text=item["label"], anchor="w").grid(row=index, column=0, sticky="w", padx=4)
            bar = ttk.Progressbar(task_list, variable=item["value"], maximum=100, length=180)
            bar.grid(row=index, column=1, padx=4, pady=2)
            tk.Label(task_list, textvariable=item["text"], width=12).grid(row=index, column=2)
            self.task_bars[key] = bar
        tk.Label(window, textvariable=self.style_status, wraplength=560).pack(padx=12, pady=8)
        tk.Checkbutton(window, text="Use learned playstyle for AI decisions", variable=self.use_style).pack()
        advanced = tk.Frame(window)
        def toggle_advanced():
            if advanced.winfo_manager():
                advanced.pack_forget()
            else:
                advanced.pack(padx=12, pady=10)
        tk.Button(window, text="Advanced settings / saved games", command=toggle_advanced).pack(pady=6)
        row = tk.Frame(advanced)
        row.pack()
        tk.Label(row, text="Player:").pack(side="left")
        tk.Entry(row, textvariable=self.player_name).pack(side="left")
        tk.Button(row, text="Load style", command=self.load_style).pack(side="left")
        tk.Checkbutton(advanced, text="Detect touch rotation automatically", variable=self.touch_auto).pack()
        row = tk.Frame(advanced)
        row.pack()
        tk.Label(row, text="Touch rotation:").pack(side="left")
        rotation = ttk.Combobox(row, textvariable=self.touch_rotation, values=("0", "90", "180", "270"), width=6, state="readonly")
        rotation.pack(side="left")
        tk.Label(row, text="Input preview gap (seconds):").pack(side="left")
        tk.Spinbox(row, textvariable=self.demo_interval, from_=.2, to=3, increment=.1, width=6).pack(side="left")
        row = tk.Frame(advanced)
        row.pack(pady=8)
        tk.Button(row, text="Preview phone input", command=lambda: self.start_player(False)).pack(side="left")
        row = tk.Frame(advanced)
        row.pack()
        tk.Label(row, text="Video FPS limit:").pack(side="left")
        ttk.Combobox(row, textvariable=self.video_fps, values=("15", "30", "60"), state="readonly", width=5).pack(side="left")
        tk.Label(row, text="Saved game:").pack(side="left")
        recordings = ttk.Combobox(row, textvariable=self.recording_name, width=24, state="readonly")
        recordings.configure(postcommand=lambda: recordings.configure(values=sorted(
            path.parent.name for path in (self.style.folder/"recordings").glob("*/session.json"))))
        recordings.pack(side="left")
        row = tk.Frame(advanced)
        row.pack(pady=8)
        tk.Label(row, text="Video/input offset (seconds):").pack(side="left")
        offset = tk.Entry(row, textvariable=self.sync_offset, width=7)
        offset.pack(side="left")
        tk.Label(row, text="Action:").pack(side="left")
        tk.Spinbox(row, textvariable=self.review_action, from_=1, to=100000, width=6).pack(side="left")
        tk.Button(row, text="Preview recorded action", command=self.preview_game).pack(side="left")
        tk.Button(advanced, text="Analyze selected saved game", command=self.analyze_game).pack(pady=6)
        tk.Button(advanced, text="Watch analyzed game", command=self.watch_saved_game).pack(pady=4)
        tk.Scale(advanced, variable=self.style_strength, from_=0, to=1, resolution=.05,
                 orient="horizontal", label="Imitation influence (0 = reward policy, 1 = player preferences)", length=470).pack()
        tk.Label(advanced, wraplength=560, text="Names, controls and map come from the active profile. If touch mapping or timing needs correction, use the optional preview and offset settings. Recording does not train the model; analysis does.").pack(padx=12, pady=10)
        def close_window():
            if self.player_session is not None:
                self.auto_analyze = False
                self.stop_player()
            else:
                window.destroy()
        window.protocol("WM_DELETE_WINDOW", close_window)
        self.update_player_buttons()

    def update_player_buttons(self):
        for name, enabled in (("player_record_button", self.player_session is None),
                              ("player_stop_button", self.player_session is not None)):
            button = getattr(self, name, None)
            if button is not None and button.winfo_exists():
                button.configure(state="normal" if enabled else "disabled")

    def reset_player_tasks(self, saved=False):
        for key in getattr(self, "player_tasks", {}):
            item = self.player_tasks[key]
            item["state"] = "waiting"
            item["value"].set(0)
            item["text"].set("Waiting")
            bar = self.task_bars.get(key)
            if bar is not None and bar.winfo_exists():
                bar.stop()
                bar.configure(mode="determinate")
        if saved:
            self.set_player_task("video", 1, 1)
            self.set_player_task("input", 1, 1)

    def set_player_task(self, task, current=None, total=None, label=None):
        if task not in getattr(self, "player_tasks", {}):
            return
        item = self.player_tasks[task]
        item["state"] = "done" if total and current >= total else "running"
        item["text"].set(f"{100*current/total:.0f}%" if total else "Working...")
        if total:
            item["value"].set(100*current/total)
        bar = self.task_bars.get(task)
        if bar is not None and bar.winfo_exists():
            bar.stop()
            bar.configure(mode="determinate" if total else "indeterminate")
            if not total:
                bar.start(50)

    def stop_player_tasks(self, failed=False):
        for key, item in getattr(self, "player_tasks", {}).items():
            if item["state"] == "running":
                item["state"] = "failed" if failed else "cancelled"
                item["text"].set("Failed" if failed else "Cancelled")
                bar = self.task_bars.get(key)
                if bar is not None and bar.winfo_exists():
                    bar.stop()

    def open_replay(self, path):
        if path is None:
            return
        try:
            self.replay_player = ReplayPlayer(self.root, path)
        except (OSError, ValueError, tk.TclError) as error:
            self.status.set(f"Learning was saved, but the replay could not open: {error}")

    def watch_saved_game(self):
        if self.player_guard():
            return
        try:
            path, _, _ = self.selected_game()
            candidates = sorted((path/"analyses").glob("*/replay.json"))
            if not candidates:
                raise ValueError("No replay is available. Older replays may have been removed by automatic storage cleanup; learned progress is preserved.")
            self.open_replay(candidates[-1].with_suffix(".mp4"))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def set_player_progress(self, label, current=None, total=None):
        if not hasattr(self, "player_phase"):
            return
        self.player_phase.set(f"{label} ({100*current/total:.0f}%)" if total else label)
        bar = self.player_bar
        if bar is not None and bar.winfo_exists():
            bar.stop()
            bar.configure(mode="determinate" if total else "indeterminate")
            if not total:
                bar.start(50)
        if total:
            self.player_progress.set(100*current/total)
        self.update_player_buttons()

    def load_style(self):
        if self.player_guard():
            return
        try:
            style = Playstyle(self.folder, self.player_name.get())
            self.style = style
            self.recording_name.set("")
            self.style_status.set(f"Loaded {style.player}: {style.examples} player examples")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def start_player(self, recording):
        if self.player_guard():
            return
        if self.busy:
            self.status.set("Wait for the current capture or AI action before observing the player.")
            return
        if recording and not {"move", "attack"}.issubset(self.calibrated):
            self.status.set("Calibrate move and attack in the main console before recording.")
            self.set_player_progress("Calibrate move and attack first", 0, 1)
            return
        try:
            rotation = int(self.touch_rotation.get())
            if rotation not in (0, 90, 180, 270):
                raise ValueError("Touch rotation must be 0, 90, 180 or 270.")
            interval = capture_interval(self.demo_interval.get())
            if interval > 3:
                raise ValueError("Player capture gap must be at most 3 seconds.")
            style = Playstyle(self.folder, self.player_name.get())
            fps = int(self.video_fps.get())
            if fps not in (15, 30, 60):
                raise ValueError("Video FPS limit must be 15, 30 or 60.")
            if recording:
                video_tools()
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(str(error))
            self.set_player_progress(f"Could not start recording: {error}", 0, 1)
            return
        self.pause()
        self.stop_capture()
        self.cancel_box_edit(announce=False)
        self.finish(terminal=True)
        active_map = self.active_map
        detector = self.detector
        hud = getattr(self, "hud_detector", None)
        own = getattr(self, "self_detector", None)
        control_snapshot, calibration = dict(self.controls), set(self.calibrated)
        def state_builder(image):
            detections = detector.detect(image)
            if hud is not None:
                detections += hud.detect(image, control_snapshot, calibration)
            if own is not None:
                detections = merge_player(detections, own.detect(image))
            return map_state(enemy_state(image, detections, detector.ready), active_map), detections
        if recording:
            self.reset_player_tasks()
            self.player_session = MatchRecording(self.device, style, self.controls, self.calibrated,
                                                 rotation, fps, {**self.profile.record(), "map": active_map},
                                                 auto_rotation=self.touch_auto.get())
            self.auto_analyze = True
        else:
            self.player_session = PlayerSession(self.device, style, self.controls, self.calibrated,
                                                rotation, interval, state_builder, False)
        self.player_session.start()
        self.set_player_progress("Starting recording and live preview..." if recording else "Input preview is starting...")
        self.style_status.set("Starting full-game video recording (no learning)..." if recording else "Starting input preview (no learning)...")
        self.root.after(100, self.poll_player)

    def stop_player(self):
        if self.player_session is not None:
            self.player_session.stop()
            self.set_player_progress("Saving recorded game..." if isinstance(self.player_session, MatchRecording) else "Stopping analysis / preview...")
            self.style_status.set("Stopping/finalizing recording or cancelling analysis. Please wait...")

    def selected_game(self):
        token = self.recording_name.get()
        if not token.isdigit():
            raise ValueError("Select a saved game first.")
        path = self.style.folder/"recordings"/token
        metadata = json.loads((path/"session.json").read_text())
        if any(metadata[name].casefold() != getattr(self.profile, name).casefold() for name in ("brawler", "mode")):
            raise ValueError("Load the recording's brawler and mode before analyzing it.")
        offset = float(self.sync_offset.get())
        if not math.isfinite(offset) or abs(offset) > 30:
            raise ValueError("Video/input offset must be between -30 and 30 seconds.")
        return path, metadata, offset

    def preview_game(self):
        if self.player_guard() or self.busy:
            return
        try:
            path, metadata, offset = self.selected_game()
            index = max(0, int(self.review_action.get())-1)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        self.pause()
        self.stop_capture()
        self.cancel_box_edit(announce=False)
        def operation():
            metadata, gestures = load_gestures(path)
            timestamps, size = video_info(path/"game.mkv")
            requests = action_requests(metadata, gestures, timestamps, size, offset)
            if not requests:
                raise ValueError("No supported actions align with this video. Check touch rotation and timing.")
            request = requests[min(index, len(requests)-1)]
            return decode_frame(path/"game.mkv", request["frame_time"]), request, len(requests)
        def complete(result):
            image, request, count = result
            self.show((image, []))
            if request["gesture"]["start"] is not None:
                x, y = request["gesture"]["start"]
                w, h = self.preview_size
                self.canvas.create_oval(x*w-8, y*h-8, x*w+8, y*h+8, outline="cyan", width=3)
            self.style_status.set(f"Recorded action {min(index+1, count)}/{count}: {request['action']} at video {request['video_time']:.2f}s. Adjust offset if needed.")
        self.status.set("Preparing recording synchronization preview...")
        self.work(operation, complete)

    def analyze_game(self):
        if self.player_guard() or self.busy:
            return
        try:
            path, metadata, offset = self.selected_game()
            video_tools()
            style = Playstyle(self.folder, self.style.player)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(str(error))
            self.set_player_progress(f"Could not start analysis: {error}", 0, 1)
            return
        self.pause()
        self.stop_capture()
        self.cancel_box_edit(announce=False)
        self.finish(terminal=True)
        detector = self.detector
        hud = getattr(self, "hud_detector", None)
        own = getattr(self, "self_detector", None)
        def state_builder(image):
            detections = detector.detect(image)
            if hud is not None:
                detections += hud.detect(image, metadata["controls"], metadata["calibrated"])
            if own is not None:
                detections = merge_player(detections, own.detect(image))
            return map_state(enemy_state(image, detections, detector.ready), metadata.get("map")), detections
        self.reset_player_tasks(saved=True)
        self.player_session = RecordingAnalysis(path, style, state_builder, offset, recognition_ready=detector.ready)
        self.player_session.start()
        self.set_player_progress("Analysis: reading saved video and actions...")
        self.style_status.set("Analyzing the saved game; model updates apply only after successful completion...")
        self.root.after(100, self.poll_player)

    def poll_player(self):
        session = self.player_session
        if session is None:
            return
        try:
            for _ in range(10):
                try:
                    kind, value = session.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "frame":
                    image, detections, point, count, skipped = value
                    self.show((image, detections))
                    if point:
                        x, y = point[0]*self.preview_size[0], point[1]*self.preview_size[1]
                        self.canvas.create_oval(x-7, y-7, x+7, y+7, outline="cyan", width=3)
                    self.style_status.set(f"{'Recording' if session.recording else 'Input preview'} | Examples: {count} | Skipped: {skipped} | Last touch: {point}")
                elif kind == "error":
                    self.auto_analyze = False
                    self.status.set(value)
                    self.style_status.set("Player observation failed; see the console status.")
                    self.set_player_progress(f"Failed: {value}", 0, 1)
                    self.stop_player_tasks(failed=True)
                elif kind == "task":
                    self.set_player_task(**value)
                elif kind == "progress":
                    self.set_player_progress(**{key: value[key] for key in ("label", "current", "total") if key in value})
                elif kind == "status":
                    self.style_status.set(value)
                elif kind == "done":
                    self.style = session.style
                    self.player_session = None
                    if isinstance(session, MatchRecording) and session.metadata.get("status") == "ready":
                        self.recording_name.set(session.folder.name)
                        self.style_status.set("Game saved. Starting post-game analysis...")
                        if self.auto_analyze:
                            self.analyze_game()
                        else:
                            self.set_player_progress("Game saved. Analysis can be started from saved games.", 1, 1)
                    elif isinstance(session, RecordingAnalysis):
                        if session.success:
                            self.use_style.set(True)
                            self.style_status.set(f"Learning complete | {self.style.examples} player examples | imitation enabled")
                            self.set_player_progress("Done. Player playstyle saved and ready to use.", 1, 1)
                            self.open_replay(session.replay_path)
                        elif session.error:
                            self.set_player_progress(f"Analysis failed: {session.error}", 0, 1)
                        else:
                            self.stop_player_tasks()
                            self.set_player_progress("Analysis stopped. Previous learning was preserved.", 0, 1)
                    elif not isinstance(session, RecordingAnalysis):
                        self.style_status.set(f"Loaded {self.style.player}: {self.style.examples} examples | observation stopped")
                    self.update_player_buttons()
                    return
        except Exception as error:
            session.stop()
            self.status.set(f"Player observation error: {error}")
        finally:
            if self.player_session is session:
                self.root.after(100, self.poll_player)

    def capture_blocked(self):
        if getattr(self, "player_session", None) is not None:
            return "player observation owns capture"
        if self.busy:
            return "device operation or detection in progress"
        if self.running:
            return "AI training supplies its own captures"
        if self.mode or self.reviewing:
            return "finish or cancel the current selection/review"
        return None

    def capture_preview(self, complete):
        revision = self.preview_revision
        def received(result):
            self.show_preview(result, revision)
            complete()
        self.work(self.capture_frame, received, kind="preview")

    def show_preview(self, result, revision):
        if revision == self.preview_revision and not self.reviewing and not self.mode:
            self.show(result)

    def refresh_detection(self, image):
        image = image.copy()
        revision = self.preview_revision
        if self.busy:
            self.deferred_preview = (image, revision)
            return
        self.work(lambda: (image, self.detect_scene(image)),
                  lambda result: self.show_preview(result, revision), kind="preview")

    def configure_capture(self):
        try:
            seconds = capture_interval(self.capture_seconds.get())
            adaptive = self.capture_adaptive.get()
            path = self.training_root / "capture-settings.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"seconds": seconds, "adaptive": adaptive}, indent=2))
            temporary.replace(path)
            if self.auto_capture_enabled.get():
                self.capture_loop.start(seconds, adaptive)
            else:
                self.stop_capture()
        except (OSError, ValueError, TypeError) as error:
            self.stop_capture()
            self.status.set(str(error))

    def stop_capture(self):
        self.capture_loop.stop()
        self.auto_capture_enabled.set(False)
        self.capture_status.set("Auto capture: off")

    def update_map_status(self):
        text = f"Active map: {self.active_map or 'unassigned'}"
        if self.map_prediction:
            proposal = self.map_prediction
            qualifier = "suggestion" if proposal["confident"] else "uncertain match"
            text += f" | {qualifier}: {proposal['name']} ({proposal['score']:.2f}); confirm or enter the actual map."
        else:
            text += " | No learned map match."
        self.map_status.set(text)

    def save_map_name(self):
        if self.player_guard():
            return
        try:
            name = self.map_recognizer.save_name(self.map_name.get())
            self.map_name.set(name)
            self.map_select.configure(values=self.map_recognizer.names)
            self.status.set(f"Saved map name: {name}. It stays available after restart. Set / teach map confirms it for gameplay.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def teach_map(self):
        if self.player_guard():
            return
        self.pause()
        if self.busy or self.mode or self.image is None:
            self.status.set("Finish the current operation and capture gameplay before teaching a map.")
            return
        try:
            # Commit to the previous map before its name or visual interpretation changes.
            from .profiles import normalize
            name = normalize(self.map_name.get())
            self.finish(terminal=True)
            name = self.map_recognizer.learn(self.image, name)
            self.active_map = name
            self.map_name.set(name)
            self.map_select.configure(values=self.map_recognizer.names)
            self.map_prediction = self.map_recognizer.predict(self.image)
            filename = f"map-frame-{time.time_ns()}.png"
            self.image.save(self.folder / filename)
            self.log("map-examples.jsonl", {"image": filename, "map": name})
            self.update_map_status()
            self.status.set("Map confirmed and example saved. Capture more viewpoints and teach the same name.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def confirm_map(self):
        if self.busy or not self.map_prediction:
            self.status.set("Capture a frame with a map suggestion first.")
            return
        self.map_name.set(self.map_prediction["name"])
        self.teach_map()

    def clear_map(self):
        if self.player_guard():
            return
        self.pause()
        if self.busy:
            self.status.set("Wait for the current operation before clearing the map.")
            return
        self.finish(terminal=True)
        self.active_map = None
        self.map_name.set("")
        self.update_map_status()

    def load_gadget_mode(self):
        mode = self.profile.gadget_mode()
        if mode is None:
            draggable = messagebox.askyesno(
                "Gadget input", f"Does {self.profile.brawler}'s equipped gadget require dragging to aim?\n\n"
                "Yes: drag. No: tap. You can change this later under Gadget input.", parent=self.root)
            mode = "drag" if draggable else "tap"
            self.profile.save_gadget_mode(mode)
        self.controls["gadget_mode"] = mode
        self.control_modes["gadget"].set(mode)

    def save_controls(self):
        # Gadget input belongs to the brawler; coordinates remain shared.
        shared = {key: value for key, value in self.controls.items() if key != "gadget_mode"}
        self.controls_path.write_text(json.dumps(shared, indent=2))

    def save_control_modes(self, event=None):
        if self.player_guard():
            for control, variable in self.control_modes.items():
                variable.set(self.controls.get(control+"_mode", "tap"))
            return
        self.pause()
        if self.busy:
            for control, variable in self.control_modes.items():
                variable.set(self.controls.get(control + "_mode", "tap"))
            self.status.set("Wait for the current action before changing input modes.")
            return
        self.finish(terminal=True)
        for control, variable in self.control_modes.items():
            self.controls[control + "_mode"] = variable.get()
        self.profile.save_gadget_mode(self.controls["gadget_mode"])
        self.save_controls()
        self.status.set("Input modes saved. Drag actions aim in four directions from the calibrated center.")

    def refresh_recommendation(self):
        self.recommendation = recommend(self.training_root, self.profile)
        self.recommendation_status.set(self.recommendation.text())

    def load_recommendation(self):
        if self.busy:
            self.status.set("Wait for the current operation before loading a recommendation.")
            return
        self.brawler_name.set(self.recommendation.profile.brawler)
        self.mode_name.set(self.recommendation.profile.mode)
        self.switch_profile()

    def switch_profile(self):
        if self.player_guard():
            return
        self.pause()
        if self.busy:
            self.status.set("Wait for the current operation, then load the profile again.")
            return
        try:
            profile = Profile(self.training_root, self.brawler_name.get(), self.mode_name.get())
            folder = profile.prepare()
            agent = Learner(folder / "policy.json")
            detector = EnemyDetector(folder)
            hud_detector = HudDetector(folder)
            self_detector = PlayerDetector(folder, profile.brawler)
            map_recognizer = MapRecognizer(self.training_root, profile.mode)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        if profile.folder == self.profile.folder:
            self.status.set("This profile is already active.")
            return
        self.stop_capture()
        self.preview_revision += 1
        self.deferred_preview = None
        # Commit feedback to the old model before changing the active profile.
        self.finish(terminal=True)
        self.agent.save()
        self.profile, self.folder, self.agent = profile, folder, agent
        self.map_recognizer = map_recognizer
        self.active_map = self.map_prediction = None
        self.map_name.set("")
        self.map_select.configure(values=self.map_recognizer.names)
        self.update_map_status()
        self.detector, self.detections = detector, []
        self.hud_detector = hud_detector
        self.self_detector = self_detector
        self.detection_status.set("Enemy detector: capture a frame for this profile.")
        self.mode = self.image = None
        self.box_edit = None
        self.hud_box_edit = None
        self.canvas.delete("all")
        self.brawler_name.set(profile.brawler)
        self.mode_name.set(profile.mode)
        brawlers, modes = choices(self.training_root)
        self.brawler_select.configure(values=brawlers)
        self.mode_select.configure(values=modes)
        self.update_profile_status()
        self.refresh_recommendation()
        self.status.set("Profile loaded. Select the matching brawler and mode in the game manually, then capture.")
        self.load_gadget_mode()
        self.load_style()

    def work(self, operation, callback, kind="device"):
        if self.busy:
            self.status.set("A device operation or detection is still running. Please wait.")
            return
        self.busy = True
        self.work_kind = kind
        self.work_started = time.monotonic()
        activity = getattr(self, "activity", None)
        if activity is not None:
            activity.add("operation", f"Started {kind}", interval=2 if kind == "preview" else 0)
        def worker():
            try:
                self.events.put((callback, operation(), None))
            except Exception as error:
                if activity is not None:
                    activity.add("error", traceback.format_exc())
                self.events.put((callback, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            callback, result, error = self.events.get_nowait()
            activity = getattr(self, "activity", None)
            if activity is not None:
                activity.add("operation_done", f"{self.work_kind}: {'failed' if error else 'completed'} in {time.monotonic()-self.work_started:.2f}s",
                             interval=2 if self.work_kind == "preview" else 0)
            self.busy = False
            self.work_kind = None
            if error:
                self.pause()
                self.stop_capture()
                self.status.set(f"Operation failed: {error}. Retry Capture after checking the device.")
            else:
                callback(result)
        except queue.Empty:
            pass
        except Exception as error:
            self.callback_error(type(error), error, error.__traceback__)
        finally:
            if not self.busy and not self.reviewing and self.deferred_preview is not None:
                image, revision = self.deferred_preview
                self.deferred_preview = None
                if revision == self.preview_revision:
                    self.refresh_detection(image)
            # A failed render/callback must not permanently stop the worker queue.
            self.root.after(100, self.poll)

    def callback_error(self, error_type, error, trace):
        self.pause()
        self.stop_capture()
        self.cancel_box_edit()
        traceback.print_exception(error_type, error, trace)
        if getattr(self, "activity", None) is not None:
            self.activity.add("error", "".join(traceback.format_exception(error_type, error, trace)))
        self.status.set(f"Console error: {error}. Details are in the terminal; try Capture again.")

    def capture_frame(self):
        image = self.device.screenshot()
        return image, self.detect_scene(image)

    def detect_scene(self, image):
        detections = self.detector.detect(image)
        hud = getattr(self, "hud_detector", None)
        if hud is not None:
            detections += hud.detect(image, dict(self.controls), set(self.calibrated))
        own = getattr(self, "self_detector", None)
        if own is not None:
            detections = merge_player(detections, own.detect(image))
        return detections

    def use_detected_controls(self, automatic=False):
        if not automatic:
            self.pause()
        if getattr(self, "player_session", None) is not None or (self.busy and not automatic):
            return
        changed = []
        for item in self.detections:
            if item.get("kind") != "control" or item.get("source") not in ("color_shape", "example"):
                continue
            control = item["control"]
            if item["score"] < (.9 if automatic else .75) or (automatic and control in self.calibrated):
                continue
            changed.append(item)
        if changed:
            if not automatic:
                self.finish(terminal=True)
            for item in changed:
                self.controls[item["control"]] = list(item["center"])
                self.calibrated.add(item["control"])
            self.controls["calibrated"] = sorted(self.calibrated)
            self.save_controls()
            if not automatic:
                self.status.set("Saved detected centers: " + ", ".join(item["control"] for item in changed))

    def show(self, result):
        image, self.detections = result
        self.image = image
        self.use_detected_controls(automatic=True)
        self.map_prediction = self.map_recognizer.predict(image)
        self.update_map_status()
        self.render_preview()
        controls = [item for item in self.detections if item.get("kind") == "control"]
        own = [item for item in self.detections if item.get("kind") == "player"]
        entities = len(self.detections)-len(controls)-len(own)
        self.detection_status.set(f"Entity matches: {entities} | Player: {'found' if own else 'unknown'} | Controls: {len(controls)} | Click to review.")

    def render_preview(self):
        preview = self.image.copy()
        preview.thumbnail(self.preview_limit)
        self.preview_size = preview.size
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        w, h = self.preview_size
        for item in self.detections:
            x0, y0, x1, y1 = item["box"]
            color = "cyan" if item.get("kind") == "control" else "lime" if item.get("kind") == "player" else "red"
            self.canvas.create_rectangle(x0*w, y0*h, x1*w, y1*h, outline=color, width=2)
            self.canvas.create_text(max(0, min(x0*w, w - 200)), max(12, y0*h), anchor="sw", fill=color, text=hud_label(item))

    def capture(self):
        if self.player_guard():
            return
        if self.mode == "refine":
            self.status.set("Finish drawing the corrected box or cancel the box edit first.")
            return
        if not self.running:
            if self.busy:
                self.status.set("Capture is waiting for the current device operation or detection. Please wait.")
                return
            self.cancel_box_edit()
            self.status.set("Capturing the device screen and checking detections...")
            self.capture_preview(lambda: None)
        else:
            self.status.set("Pause continuous training before using Capture.")

    def set_mode(self, mode):
        if self.player_guard():
            return
        self.pause()
        if self.busy:
            self.status.set("Wait for the current device operation or detection, then click the labeling button again.")
            return
        if self.image is None:
            self.status.set("Capturing a frame before selecting the control or character...")
            def ready(result):
                self.show(result)
                self.set_mode(mode)
            self.work(self.capture_frame, ready)
            return
        self.cancel_box_edit()
        self.mode = mode
        self.canvas.configure(cursor="crosshair")
        self.status.set("Labeling active: hold the left mouse button, draw a tight box, then release to choose its identity/state. Esc cancels."
                        if mode in ("label", "enemy", "not_enemy", "hud", "player") else "Click the center of the on-screen control.")
        if mode == "player":
            self.status.set("Draw a tight box around your controlled character. The active profile supplies its brawler name. Esc cancels.")

    def press(self, event):
        if self.player_guard():
            return
        if not self.mode:
            if self.image is None:
                return
            w, h = self.preview_size
            matches = [item for item in self.detections
                       if item["box"][0] <= event.x/w <= item["box"][2]
                       and item["box"][1] <= event.y/h <= item["box"][3]]
            if matches:
                if self.busy and self.work_kind != "preview":
                    self.status.set("Wait for the current AI action before reviewing its result.")
                    return
                # Freeze the displayed frame immediately; late preview results are stale.
                self.preview_revision += 1
                item = max(matches, key=lambda item: ((2 if item.get("kind") == "control" else 1 if item.get("kind") == "player" else 0), item["score"]))
                if item.get("kind") == "control":
                    self.review_control(item)
                elif item.get("kind") == "player":
                    self.review_player(item)
                else:
                    self.review_detection(item)
            return
        if self.busy and self.work_kind != "preview":
            return
        w, h = self.preview_size
        if not (0 <= event.x < w and 0 <= event.y < h):
            return
        self.origin = (event.x, event.y)
        if self.mode not in ("label", "enemy", "not_enemy", "refine", "hud", "player"):
            self.finish(terminal=True)
            self.controls[self.mode] = [event.x / w, event.y / h]
            self.calibrated.add(self.mode)
            self.controls["calibrated"] = sorted(self.calibrated)
            self.save_controls()
            self.status.set(f"Saved {self.mode} control.")
            self.mode = None
            self.canvas.configure(cursor="")
        else:
            if hasattr(self, "box"):
                self.canvas.delete(self.box)
            self.box = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="lime", width=2)

    def drag(self, event):
        if self.mode in ("label", "enemy", "not_enemy", "refine", "hud", "player") and hasattr(self, "box"):
            self.canvas.coords(self.box, *self.origin, event.x, event.y)

    def release(self, event):
        if (self.busy and self.work_kind != "preview") or self.mode not in ("label", "enemy", "not_enemy", "refine", "hud", "player") or not hasattr(self, "box"):
            return
        w, h = self.preview_size
        x0, x1 = sorted((self.origin[0], max(0, min(event.x, w))))
        y0, y1 = sorted((self.origin[1], max(0, min(event.y, h))))
        if x1 - x0 < 3 or y1 - y0 < 3:
            self.status.set("Box is too small. Draw it again or cancel the box edit.")
            return
        if self.mode == "player":
            self.save_player_example([x0/w, y0/h, x1/w, y1/h])
            self.cancel_box_edit(announce=False)
            return
        if self.mode == "hud":
            item = self.hud_box_edit or {"control": "move", "state": "unknown"}
            result = self.control_dialog(item)
            if result is not None:
                control, state, _ = result
                self.save_control_example([x0/w, y0/h, x1/w, y1/h], control, state)
            self.hud_box_edit = None
            self.cancel_box_edit(announce=False)
            return
        if self.mode == "refine":
            item, kind = self.box_edit
            identity = self.ask_identity(item.get("identity", "Unknown"))
            if identity is not None:
                self.save_box_feedback(item, "wrong" if identity == "not_enemy" else kind,
                                       [x0/w, y0/h, x1/w, y1/h], identity)
            self.cancel_box_edit(announce=False)
            return
        label = self.mode if self.mode != "label" else simpledialog.askstring(
            "Object label", "English label (e.g. player, enemy, health_bar):", parent=self.root)
        identity = None
        if label and label.strip().casefold() == "enemy":
            identity = self.ask_identity()
            if identity is None:
                label = None
            elif identity == "not_enemy":
                label, identity = "not_enemy", None
        if label and label.strip():
            name = f"frame-{time.time_ns()}.png"
            self.image.save(self.folder / name)
            self.log("annotations.jsonl", {"image": name, "label": label.strip(), "identity": identity,
                                            "box": [x0/w, y0/h, x1/w, y1/h]})
            self.status.set(f"Saved label: {label.strip()}")
            if label.strip().casefold() in ("enemy", "not_enemy"):
                self.finish(terminal=True)
                try:
                    self.detector.learn(self.image, [x0/w, y0/h, x1/w, y1/h], label, identity)
                    self.refresh_detection(self.image)
                except ValueError as error:
                    self.status.set(str(error))
        self.cancel_box_edit(announce=False)

    def ask_identity(self, suggestion="Unknown", review=False):
        self.reviewing = True
        try:
            return self.identity_dialog(suggestion, review)
        finally:
            self.reviewing = False

    def save_player_example(self, box, accepted=True):
        self.finish(terminal=True)
        try:
            bound = self.self_detector.learn(self.image, box, accepted)
            name = f"player-frame-{time.time_ns()}.png"
            self.image.save(self.folder/name)
            self.log("player-annotations.jsonl", {"image": name, "box": box, "accepted": accepted,
                                                 "role": "self", "identity": self.profile.brawler, "marker_geometry": bound})
            self.refresh_detection(self.image)
            self.status.set("Player example saved." if bound or not accepted else
                            "Player example saved for this frame. Automatic recognition needs visible self markers.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def review_player(self, item):
        self.pause()
        self.reviewing = True
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title("Review my player")
            dialog.transient(self.root)
            tk.Label(dialog, text=f"Controlled player (profile: {self.profile.brawler})\nConfirm the role and box, or correct this proposal.", wraplength=420).pack(padx=16, pady=10)
            result = []
            def accept(action):
                result.append(action)
                dialog.destroy()
            for text, action in (("Confirm my player", "confirm"), ("Redraw player box", "redraw"), ("Not my player", "reject")):
                tk.Button(dialog, text=text, command=lambda action=action: accept(action)).pack(fill="x", padx=16)
            tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=8)
            dialog.geometry(f"+{self.root.winfo_rootx()+40}+{self.root.winfo_rooty()+40}")
            dialog.wait_visibility()
            dialog.grab_set()
            self.root.wait_window(dialog)
            if not result:
                return
            if result[0] == "redraw":
                self.mode = "player"
                self.canvas.configure(cursor="crosshair")
                self.status.set("Draw a tighter box around your controlled character. Esc cancels.")
            else:
                self.save_player_example(item["box"], result[0] == "confirm")
        finally:
            self.reviewing = False

    def control_dialog(self, item):
        self.reviewing = True
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title("Confirm control and availability")
            dialog.transient(self.root)
            tk.Label(dialog, text="Choose the control and the state visible in this frame.").pack(padx=16, pady=10)
            control = tk.StringVar(value=item.get("control", "move"))
            state = tk.StringVar(value=item.get("state", "unknown"))
            ttk.Combobox(dialog, textvariable=control, values=CONTROLS, state="readonly").pack(padx=16, pady=6)
            ttk.Combobox(dialog, textvariable=state, values=STATES, state="readonly").pack(padx=16, pady=6)
            result = []
            def accept(action):
                result.append((control.get(), state.get(), action))
                dialog.destroy()
            tk.Button(dialog, text="Confirm center and learn state", command=lambda: accept("confirm")).pack(fill="x", padx=16)
            if "box" in item:
                tk.Button(dialog, text="Redraw control box", command=lambda: accept("redraw")).pack(fill="x", padx=16)
                tk.Button(dialog, text="Not this control", command=lambda: accept("reject")).pack(fill="x", padx=16)
            tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=8)
            dialog.geometry(f"+{self.root.winfo_rootx()+40}+{self.root.winfo_rooty()+40}")
            dialog.wait_visibility()
            dialog.grab_set()
            self.root.wait_window(dialog)
            return result[0] if result else None
        finally:
            self.reviewing = False

    def save_control_example(self, box, control, state):
        self.finish(terminal=True)
        try:
            self.hud_detector.learn(self.image, box, control, state)
            name = f"hud-frame-{time.time_ns()}.png"
            self.image.save(self.folder/name)
            self.log("hud-annotations.jsonl", {"image": name, "box": box, "control": control, "state": state})
            if state != "rejected":
                self.controls[control] = [(box[0]+box[2])/2, (box[1]+box[3])/2]
                self.calibrated.add(control)
                self.controls["calibrated"] = sorted(self.calibrated)
                self.save_controls()
            self.refresh_detection(self.image)
            self.status.set(f"Saved {control} / {state}. Control recognition learns from this example.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def review_control(self, item):
        self.pause()
        result = self.control_dialog(item)
        if result is None:
            return
        control, state, action = result
        if action == "redraw":
            self.mode = "hud"
            self.hud_box_edit = {"control": control, "state": state}
            self.canvas.configure(cursor="crosshair")
            self.status.set("Draw a tight box around the actual control, then confirm its state.")
            return
        self.save_control_example(item["box"], control, "rejected" if action == "reject" else state)

    def identity_dialog(self, suggestion="Unknown", review=False):
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm entity identity")
        dialog.transient(self.root)
        result = []
        tk.Label(dialog, text=f"Closest learned identity: {suggestion}\nConfirm it or select the actual entity.").pack(padx=16, pady=10)
        value = tk.StringVar(value="" if suggestion == "Unknown" else suggestion)
        names = sorted(set(ENTITY_NAMES) | {
            item["identity"] for item in self.detector.samples if item.get("identity")})
        selector = ttk.Combobox(dialog, textvariable=value, values=names, width=32)
        selector.pack(padx=16, pady=8)

        def accept(identity):
            if not identity.strip():
                return
            result.append(identity.strip())
            dialog.destroy()

        tk.Button(dialog, text="Confirm suggestion", state="disabled" if suggestion == "Unknown" else "normal",
                  command=lambda: accept(suggestion)).pack(fill="x", padx=16)
        tk.Button(dialog, text="Save correction / new example", command=lambda: accept(value.get())).pack(fill="x", padx=16)
        tk.Button(dialog, text="Not an enemy", command=lambda: accept("not_enemy")).pack(fill="x", padx=16)
        if review:
            tk.Button(dialog, text="Refine box", command=lambda: accept("__refined__")).pack(fill="x", padx=16)
            tk.Button(dialog, text="Box too broad", command=lambda: accept("__too_broad__")).pack(fill="x", padx=16)
            tk.Button(dialog, text="Box completely wrong", command=lambda: accept("__wrong__")).pack(fill="x", padx=16)
        tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=8)
        dialog.geometry(f"+{self.root.winfo_rootx() + 40}+{self.root.winfo_rooty() + 40}")
        dialog.wait_visibility()
        dialog.grab_set()
        selector.focus_set()
        self.root.wait_window(dialog)
        return result[0] if result else None

    def review_detection(self, item):
        self.pause()
        identity = self.ask_identity(item.get("identity", "Unknown"), review=True)
        if identity is None:
            return
        if identity in ("__refined__", "__too_broad__"):
            self.mode = "refine"
            self.box_edit = (dict(item), "refined" if identity == "__refined__" else "too_broad")
            self.status.set("Draw a new tight rectangle around the actual character on this same frame. Cancel box edit discards this edit.")
            return
        if identity == "__wrong__":
            self.save_box_feedback(item, "wrong")
            return
        self.finish(terminal=True)
        label = "not_enemy" if identity == "not_enemy" else "enemy"
        identity = identity if label == "enemy" else None
        try:
            self.detector.learn(self.image, item["box"], label, identity)
            name = f"frame-{time.time_ns()}.png"
            self.image.save(self.folder / name)
            self.log("annotations.jsonl", {"image": name, "label": label, "identity": identity,
                                            "box": item["box"], "suggested_identity": item.get("identity", "Unknown")})
            self.refresh_detection(self.image)
            self.status.set("Identity feedback saved. Capture or resume when ready.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def cancel_box_edit(self, announce=True):
        if not self.mode:
            return
        self.mode = None
        if hasattr(self, "box"):
            self.canvas.delete(self.box)
            del self.box
        self.box_edit = None
        self.hud_box_edit = None
        self.canvas.configure(cursor="")
        if announce:
            self.status.set("Selection cancelled. Capture, One AI step and labeling are available.")

    def save_box_feedback(self, item, kind, corrected=None, identity=None):
        self.finish(terminal=True)
        try:
            self.detector.correct_box(self.image, item["box"], kind, corrected, identity)
            name = f"frame-{time.time_ns()}.png"
            self.image.save(self.folder / name)
            self.log("box-feedback.jsonl", {"image": name, "kind": kind, "original_box": item["box"],
                                            "corrected_box": corrected, "identity": identity,
                                            "suggested_identity": item.get("identity", "Unknown")})
            if kind != "wrong":
                self.log("annotations.jsonl", {"image": name, "label": "enemy", "identity": identity,
                                                "box": corrected})
            self.refresh_detection(self.image)
            self.status.set("Box feedback saved. Capture or resume when ready.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def log(self, name, record):
        record = {"map": self.active_map, **record, **self.profile.record()}
        with (self.folder / name).open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    def feedback(self, value):
        if self.pending is None:
            self.status.set("Run an AI step before assigning feedback.")
            return
        if not math.isfinite(value) or not math.isfinite(self.reward + value):
            self.status.set("Reward must be a finite number.")
            return
        self.reward += value
        if hasattr(self, "reward_status"):
            self.reward_status.set(f"Pending reward: {self.reward:+g}")
        self.status.set(f"Pending reward: {self.reward:+g}. Applied on the next step or episode end.")

    def custom_feedback(self):
        try:
            value = float(self.reward_input.get())
        except ValueError:
            self.status.set("Enter a numeric reward, for example 0.2, 10 or -5.")
            return
        self.feedback(value)

    def clear_feedback(self):
        self.reward = 0.0
        self.reward_status.set("Pending reward: +0")
        self.status.set("Pending reward cleared.")

    def finish(self, terminal=False):
        if self.pending is None:
            return
        state, action, next_state = self.pending
        available = getattr(self, "pending_available", None) or available_actions(self.controls, self.calibrated)
        self.agent.update(state, action, self.reward, next_state, terminal, available=available)
        self.log("transitions.jsonl", {"state": state, "action": ACTIONS[action], "reward": self.reward, "next_state": next_state, "terminal": terminal})
        self.agent.save()
        self.update_profile_status()
        self.reward = 0.0
        if hasattr(self, "reward_status"):
            self.reward_status.set("Pending reward: +0")
        self.pending = None
        self.pending_available = None
        if self.agent.steps % 20 == 0:
            self.refresh_recommendation()

    def one_step(self):
        self.pause()
        self.step()

    def step(self):
        if self.player_guard():
            return
        if self.busy:
            self.status.set("An action, capture or detection is still running. Please wait.")
            return
        if self.mode == "refine":
            self.status.set("Finish the corrected box or click Cancel selection (Esc) before an AI step.")
            self.running = False
            return
        # An explicit action request exits unfinished labeling or calibration.
        self.cancel_box_edit()
        if self.image is None:
            self.status.set("Capture a frame and calibrate controls first.")
            self.running = False
            return
        if not {"move", "attack"}.issubset(self.calibrated):
            missing = sorted({"move", "attack"} - self.calibrated)
            self.status.set("Calibrate these controls before training: " + ", ".join(missing))
            self.running = False
            return
        self.finish()
        state = map_state(enemy_state(self.image, self.detections, self.detector.ready), self.active_map)
        available = available_actions(self.controls, self.calibrated, self.detections)
        action = self.agent.choose(state, available)
        action_controls = self.controls
        if self.use_style.get() and random.random() < self.style_strength.get():
            imitated = self.style.choose(state, available)
            if imitated is not None:
                action = imitated
                action_controls = self.style.action_controls(state, ACTIONS[action], self.controls)
        name = ACTIONS[action]
        self.status.set(f"Executing AI action: {name}. Waiting for the device and updated detections...")
        def execute():
            self.device.action(ACTIONS[action], self.image.size, action_controls)
            time.sleep(0.15)
            return self.capture_frame()
        def complete(result):
            image, detections = result
            self.pending = (state, action, map_state(enemy_state(image, detections, self.detector.ready), self.active_map))
            self.pending_available = available_actions(self.controls, self.calibrated, detections)
            self.show(result)
            detail = " (intentional no movement)" if name == "idle" else ""
            self.status.set(f"Completed: {name}{detail} | Learned transitions: {self.agent.steps} | Give feedback now.")
            if self.running:
                self.auto_job = self.root.after(1500, self.auto_step)
        self.work(execute, complete)

    def auto_step(self):
        self.auto_job = None
        if self.running:
            self.step()

    def start(self):
        if self.running:
            self.status.set("Continuous training is already running. Pause to stop it.")
            return
        if self.busy:
            self.status.set("Wait for the current device operation or detection before starting.")
            return
        self.running = True
        self.step()

    def pause(self):
        self.running = False
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None

    def terminal(self):
        if self.player_guard():
            return
        self.pause()
        self.stop_capture()
        if self.busy:
            self.status.set("Wait for the current action, then mark episode end again.")
            return
        self.cancel_box_edit(announce=False)
        self.finish(terminal=True)
        self.refresh_recommendation()
        self.image = None
        self.active_map = self.map_prediction = None
        self.map_name.set("")
        self.update_map_status()
        self.status.set("Episode saved. Reset the game manually and capture a new frame.")

    def close(self):
        if self.player_session is not None:
            self.auto_analyze = False
            self.stop_player()
            self.status.set("Wait for player observation to stop, then close the console again.")
            return
        self.pause()
        self.stop_capture()
        if self.busy:
            messagebox.showinfo("Action in progress", "Wait for the current action before closing.")
            return
        self.finish()
        self.agent.save()
        if getattr(self, "activity", None) is not None:
            self.activity.add("session", "Console closed; pending learning and policy saved.")
            self.activity.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
