"""Desktop teaching console. Device work stays off Tk's UI thread."""
import json
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from PIL import ImageTk
from .android import Device, cache_root
from .learning import ACTIONS, Learner, available_actions
from .profiles import BRAWLERS, Profile, choices
from .curriculum import recommend
from .detection import EnemyDetector, enemy_state
from .maps import MapRecognizer, map_state


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
        self.reward = 0.0
        self.auto_job = None
        selector = tk.Frame(self.root)
        selector.pack()
        self.brawler_name = tk.StringVar(value=self.profile.brawler)
        self.mode_name = tk.StringVar(value=self.profile.mode)
        tk.Label(selector, text="Brawler:").pack(side="left")
        brawlers, modes = choices(self.training_root)
        self.brawler_select = ttk.Combobox(selector, textvariable=self.brawler_name, values=brawlers)
        self.brawler_select.pack(side="left")
        tk.Label(selector, text="Mode:").pack(side="left")
        self.mode_select = ttk.Combobox(selector, textvariable=self.mode_name, values=modes)
        self.mode_select.pack(side="left")
        tk.Button(selector, text="Load / create profile", command=self.switch_profile).pack(side="left")
        self.profile_status = tk.StringVar()
        tk.Label(self.root, textvariable=self.profile_status).pack()
        maps = tk.Frame(self.root)
        maps.pack()
        tk.Label(maps, text="Map:").pack(side="left")
        self.map_name = tk.StringVar()
        self.map_select = ttk.Combobox(maps, textvariable=self.map_name, values=self.map_recognizer.names, width=24)
        self.map_select.pack(side="left")
        tk.Button(maps, text="Set / teach map", command=self.teach_map).pack(side="left")
        tk.Button(maps, text="Confirm map suggestion", command=self.confirm_map).pack(side="left")
        tk.Button(maps, text="Clear active map", command=self.clear_map).pack(side="left")
        self.map_status = tk.StringVar(value="Active map: unassigned. Capture and enter its name.")
        tk.Label(self.root, textvariable=self.map_status).pack()
        self.update_profile_status()
        self.status = tk.StringVar(value="Open the game manually, enter the training area, then capture and calibrate.")
        tk.Label(self.root, textvariable=self.status, wraplength=950).pack()
        self.recommendation_status = tk.StringVar()
        tk.Label(self.root, textvariable=self.recommendation_status, wraplength=950, justify="left").pack()
        suggestions = tk.Frame(self.root)
        suggestions.pack()
        tk.Button(suggestions, text="Refresh recommendation", command=self.refresh_recommendation).pack(side="left")
        tk.Button(suggestions, text="Load recommended profile", command=self.load_recommendation).pack(side="left")
        self.refresh_recommendation()
        self.canvas = tk.Canvas(self.root, width=960, height=540, bg="#222222")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        bar = tk.Frame(self.root)
        bar.pack()
        for label, command in [("Capture", self.capture), ("Calibrate move", lambda: self.set_mode("move")),
                               ("Calibrate attack", lambda: self.set_mode("attack")),
                               ("Calibrate super", lambda: self.set_mode("super")),
                               ("Calibrate gadget", lambda: self.set_mode("gadget")),
                               ("Calibrate hypercharge", lambda: self.set_mode("hypercharge")),
                               ("Label object", lambda: self.set_mode("label"))]:
            tk.Button(bar, text=label, command=command).pack(side="left")
        options = tk.Frame(self.root)
        options.pack()
        self.control_modes = {}
        for control in ("super", "gadget"):
            tk.Label(options, text=f"{control.title()} input:").pack(side="left")
            variable = tk.StringVar(value=self.controls.get(control + "_mode", "tap"))
            self.control_modes[control] = variable
            selector = ttk.Combobox(options, textvariable=variable, values=("tap", "drag"), state="readonly", width=6)
            selector.pack(side="left")
            selector.bind("<<ComboboxSelected>>", self.save_control_modes)
        bar = tk.Frame(self.root)
        bar.pack()
        for label, command in [("One AI step", self.step),
                               ("Start", self.start), ("Pause", self.pause),
                               ("Reward +1", lambda: self.feedback(1)), ("Penalty -1", lambda: self.feedback(-1)),
                               ("Episode ended", self.terminal)]:
            tk.Button(bar, text=label, command=command).pack(side="left")
        rewards = tk.Frame(self.root)
        rewards.pack()
        tk.Label(rewards, text="Custom reward (positive or negative):").pack(side="left")
        self.reward_input = tk.StringVar(value="10")
        tk.Entry(rewards, textvariable=self.reward_input, width=10).pack(side="left")
        tk.Button(rewards, text="Add reward", command=self.custom_feedback).pack(side="left")
        tk.Button(rewards, text="Clear pending reward", command=self.clear_feedback).pack(side="left")
        self.reward_status = tk.StringVar(value="Pending reward: +0")
        tk.Label(rewards, textvariable=self.reward_status).pack(side="left")
        detection_bar = tk.Frame(self.root)
        detection_bar.pack()
        tk.Button(detection_bar, text="Label enemy", command=lambda: self.set_mode("enemy")).pack(side="left")
        tk.Button(detection_bar, text="Label false detection", command=lambda: self.set_mode("not_enemy")).pack(side="left")
        tk.Button(detection_bar, text="Cancel box edit", command=self.cancel_box_edit).pack(side="left")
        self.detection_status = tk.StringVar(value="Enemy detector: label examples to begin.")
        tk.Label(detection_bar, textvariable=self.detection_status).pack(side="left")
        tk.Label(self.root, text="Click a detected box to confirm or correct its identity. Label enemy teaches a new character or NPC.").pack()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)
        self.load_gadget_mode()

    def update_profile_status(self):
        self.profile_status.set(f"Active: {self.profile.brawler} / {self.profile.mode} | Learned transitions: {self.agent.steps}")

    def update_map_status(self):
        text = f"Active map: {self.active_map or 'unassigned'}"
        if self.map_prediction:
            proposal = self.map_prediction
            qualifier = "suggestion" if proposal["confident"] else "uncertain match"
            text += f" | {qualifier}: {proposal['name']} ({proposal['score']:.2f}); confirm or enter the actual map."
        else:
            text += " | No learned map match."
        self.map_status.set(text)

    def teach_map(self):
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
        self.pause()
        if self.busy:
            self.status.set("Wait for the current operation, then load the profile again.")
            return
        try:
            profile = Profile(self.training_root, self.brawler_name.get(), self.mode_name.get())
            folder = profile.prepare()
            agent = Learner(folder / "policy.json")
            detector = EnemyDetector(folder)
            map_recognizer = MapRecognizer(self.training_root, profile.mode)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        if profile.folder == self.profile.folder:
            self.status.set("This profile is already active.")
            return
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
        self.detection_status.set("Enemy detector: capture a frame for this profile.")
        self.mode = self.image = None
        self.box_edit = None
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

    def work(self, operation, callback):
        if self.busy:
            return
        self.busy = True
        def worker():
            try:
                self.events.put((callback, operation(), None))
            except Exception as error:
                self.events.put((callback, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            callback, result, error = self.events.get_nowait()
            self.busy = False
            if error:
                self.running = False
                self.status.set(error)
            else:
                callback(result)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def capture_frame(self):
        image = self.device.screenshot()
        return image, self.detector.detect(image)

    def show(self, result):
        image, self.detections = result
        self.image = image
        self.map_prediction = self.map_recognizer.predict(image)
        self.update_map_status()
        preview = image.copy()
        preview.thumbnail((960, 540))
        self.preview_size = preview.size
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        w, h = self.preview_size
        for item in self.detections:
            x0, y0, x1, y1 = item["box"]
            self.canvas.create_rectangle(x0*w, y0*h, x1*w, y1*h, outline="red", width=2)
            identity = item.get("identity", "Unknown")
            score = item.get("identity_score", 0)
            self.canvas.create_text(min(x1*w + 4, w - 150), max(12, y0*h), anchor="nw", fill="red",
                                    text=f"{identity}? {score:.2f}")
        self.detection_status.set(f"Enemy matches: {len(self.detections)} | Examples: {len(self.detector.samples)}"
                                  if self.detector.ready else "Enemy detector: label examples to begin.")

    def capture(self):
        if self.mode == "refine":
            self.status.set("Finish drawing the corrected box or cancel the box edit first.")
            return
        if not self.running:
            self.work(self.capture_frame, self.show)

    def set_mode(self, mode):
        self.pause()
        if self.busy or self.image is None:
            self.status.set("Wait for the current step and capture a frame first.")
            return
        if self.mode == "refine":
            self.cancel_box_edit()
        self.mode = mode
        self.status.set("Drag a tight box around the character or false detection."
                        if mode in ("label", "enemy", "not_enemy") else "Click the center of the on-screen control.")

    def press(self, event):
        if self.busy:
            return
        if not self.mode:
            if self.image is None:
                return
            w, h = self.preview_size
            matches = [item for item in self.detections
                       if item["box"][0] <= event.x/w <= item["box"][2]
                       and item["box"][1] <= event.y/h <= item["box"][3]]
            if matches:
                self.review_detection(max(matches, key=lambda item: item["score"]))
            return
        w, h = self.preview_size
        if not (0 <= event.x < w and 0 <= event.y < h):
            return
        self.origin = (event.x, event.y)
        if self.mode not in ("label", "enemy", "not_enemy", "refine"):
            self.finish(terminal=True)
            self.controls[self.mode] = [event.x / w, event.y / h]
            self.calibrated.add(self.mode)
            self.controls["calibrated"] = sorted(self.calibrated)
            self.save_controls()
            self.status.set(f"Saved {self.mode} control.")
            self.mode = None
        else:
            self.box = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="lime", width=2)

    def drag(self, event):
        if self.mode in ("label", "enemy", "not_enemy", "refine") and hasattr(self, "box"):
            self.canvas.coords(self.box, *self.origin, event.x, event.y)

    def release(self, event):
        if self.mode not in ("label", "enemy", "not_enemy", "refine") or not hasattr(self, "box"):
            return
        w, h = self.preview_size
        x0, x1 = sorted((self.origin[0], max(0, min(event.x, w))))
        y0, y1 = sorted((self.origin[1], max(0, min(event.y, h))))
        if x1 - x0 < 3 or y1 - y0 < 3:
            self.status.set("Box is too small. Draw it again or cancel the box edit.")
            return
        if self.mode == "refine":
            item, kind = self.box_edit
            identity = self.ask_identity(item.get("identity", "Unknown"))
            if identity is not None:
                self.save_box_feedback(item, "wrong" if identity == "not_enemy" else kind,
                                       [x0/w, y0/h, x1/w, y1/h], identity)
            self.cancel_box_edit()
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
                    image = self.image.copy()
                    self.work(lambda: (image, self.detector.detect(image)), self.show)
                except ValueError as error:
                    self.status.set(str(error))
        self.mode = None
        self.canvas.delete(self.box)
        del self.box

    def ask_identity(self, suggestion="Unknown", review=False):
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm character identity")
        dialog.transient(self.root)
        result = []
        tk.Label(dialog, text=f"Closest learned identity: {suggestion}\nConfirm it or select the actual character.").pack(padx=16, pady=10)
        value = tk.StringVar(value="" if suggestion == "Unknown" else suggestion)
        names = sorted(set(BRAWLERS) | {"NPC"} | {
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
            image = self.image.copy()
            self.work(lambda: (image, self.detector.detect(image)), self.show)
            self.status.set("Identity feedback saved. Capture or resume when ready.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def cancel_box_edit(self):
        if self.mode != "refine":
            return
        self.mode = None
        if hasattr(self, "box"):
            self.canvas.delete(self.box)
            del self.box
        self.box_edit = None

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
            image = self.image.copy()
            self.work(lambda: (image, self.detector.detect(image)), self.show)
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
        available = available_actions(self.controls, self.calibrated)
        self.agent.update(state, action, self.reward, next_state, terminal, available=available)
        self.log("transitions.jsonl", {"state": state, "action": ACTIONS[action], "reward": self.reward, "next_state": next_state, "terminal": terminal})
        self.agent.save()
        self.update_profile_status()
        self.reward = 0.0
        if hasattr(self, "reward_status"):
            self.reward_status.set("Pending reward: +0")
        self.pending = None
        if self.agent.steps % 20 == 0:
            self.refresh_recommendation()

    def step(self):
        if self.busy or self.mode:
            return
        if self.image is None:
            self.status.set("Capture a frame and calibrate controls first.")
            return
        if not {"move", "attack"}.issubset(self.calibrated):
            self.status.set("Calibrate move and attack controls before training.")
            self.running = False
            return
        self.finish()
        state = map_state(enemy_state(self.image, self.detections, self.detector.ready), self.active_map)
        action = self.agent.choose(state, available_actions(self.controls, self.calibrated))
        def execute():
            self.device.action(ACTIONS[action], self.image.size, self.controls)
            time.sleep(0.15)
            return self.capture_frame()
        def complete(result):
            self.show(result)
            self.pending = (state, action, map_state(enemy_state(self.image, self.detections, self.detector.ready), self.active_map))
            self.status.set(f"Action: {ACTIONS[action]} | Learned transitions: {self.agent.steps} | Give feedback now.")
            if self.running:
                self.auto_job = self.root.after(1500, self.auto_step)
        self.work(execute, complete)

    def auto_step(self):
        self.auto_job = None
        if self.running:
            self.step()

    def start(self):
        if self.busy or self.mode or self.running:
            return
        self.running = True
        self.step()

    def pause(self):
        self.running = False
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None

    def terminal(self):
        self.pause()
        if self.busy:
            self.status.set("Wait for the current action, then mark episode end again.")
            return
        self.finish(terminal=True)
        self.refresh_recommendation()
        self.image = None
        self.active_map = self.map_prediction = None
        self.map_name.set("")
        self.update_map_status()
        self.status.set("Episode saved. Reset the game manually and capture a new frame.")

    def close(self):
        self.pause()
        if self.busy:
            messagebox.showinfo("Action in progress", "Wait for the current action before closing.")
            return
        self.finish()
        self.agent.save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
