"""Desktop teaching console. Device work stays off Tk's UI thread."""
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from PIL import ImageTk
from .android import Device, cache_root
from .learning import ACTIONS, Learner, state_key


class Console:
    def __init__(self, serial):
        self.device = Device(serial)
        self.root = tk.Tk()
        self.root.title("Brawl AI — teaching console")
        self.folder = cache_root() / "training"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.agent = Learner(self.folder / "policy.json")
        self.controls_path = self.folder / "controls.json"
        self.calibrated = set()
        self.calibrated = set(json.loads(self.controls_path.read_text()).get("calibrated", [])) if self.controls_path.exists() else set()
        self.controls = json.loads(self.controls_path.read_text()) if self.controls_path.exists() else {"move": [0.15, 0.75], "attack": [0.85, 0.75], "radius": 0.10}
        self.events = queue.Queue()
        self.busy = self.running = False
        self.image = self.pending = self.mode = None
        self.reward = 0.0
        self.status = tk.StringVar(value="Open the game manually, enter the training area, then capture and calibrate.")
        tk.Label(self.root, textvariable=self.status, wraplength=950).pack()
        self.canvas = tk.Canvas(self.root, width=960, height=540, bg="#222222")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        bar = tk.Frame(self.root)
        bar.pack()
        for label, command in [("Capture", self.capture), ("Calibrate move", lambda: self.set_mode("move")),
                               ("Calibrate attack", lambda: self.set_mode("attack")),
                               ("Label object", lambda: self.set_mode("label")), ("One AI step", self.step),
                               ("Start", self.start), ("Pause", self.pause),
                               ("Reward +1", lambda: self.feedback(1)), ("Penalty -1", lambda: self.feedback(-1)),
                               ("Episode ended", self.terminal)]:
            tk.Button(bar, text=label, command=command).pack(side="left")
        tk.Label(self.root, text="Labels are saved as bounding boxes for a future visual detector. Rewards train the current Q policy. Pause before labeling.").pack()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)

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

    def show(self, image):
        self.image = image
        preview = image.copy()
        preview.thumbnail((960, 540))
        self.preview_size = preview.size
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

    def capture(self):
        if not self.running:
            self.work(self.device.screenshot, self.show)

    def set_mode(self, mode):
        self.pause()
        if self.busy or self.image is None:
            self.status.set("Wait for the current step and capture a frame first.")
            return
        self.mode = mode
        self.status.set("Drag a box around an object." if mode == "label" else "Click the center of the on-screen control.")

    def press(self, event):
        if not self.mode or self.busy:
            return
        w, h = self.preview_size
        if not (0 <= event.x < w and 0 <= event.y < h):
            return
        self.origin = (event.x, event.y)
        if self.mode != "label":
            self.controls[self.mode] = [event.x / w, event.y / h]
            self.calibrated.add(self.mode)
            self.controls["calibrated"] = sorted(self.calibrated)
            self.controls_path.write_text(json.dumps(self.controls, indent=2))
            self.status.set(f"Saved {self.mode} control.")
            self.mode = None
        else:
            self.box = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="lime", width=2)

    def drag(self, event):
        if self.mode == "label" and hasattr(self, "box"):
            self.canvas.coords(self.box, *self.origin, event.x, event.y)

    def release(self, event):
        if self.mode != "label" or not hasattr(self, "box"):
            return
        w, h = self.preview_size
        x0, x1 = sorted((self.origin[0], max(0, min(event.x, w))))
        y0, y1 = sorted((self.origin[1], max(0, min(event.y, h))))
        if x1 - x0 < 3 or y1 - y0 < 3:
            return
        label = simpledialog.askstring("Object label", "English label (e.g. player, enemy, health_bar):", parent=self.root)
        if label and label.strip():
            name = f"frame-{time.time_ns()}.png"
            self.image.save(self.folder / name)
            self.log("annotations.jsonl", {"image": name, "label": label.strip(), "box": [x0/w, y0/h, x1/w, y1/h]})
            self.status.set(f"Saved label: {label.strip()}")
        self.mode = None
        del self.box

    def log(self, name, record):
        with (self.folder / name).open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    def feedback(self, value):
        if self.pending is None:
            self.status.set("Run an AI step before assigning feedback.")
            return
        self.reward += value
        self.status.set(f"Pending reward: {self.reward:+g}. Applied on the next step or episode end.")

    def finish(self, terminal=False):
        if self.pending is None:
            return
        state, action, next_state = self.pending
        self.agent.update(state, action, self.reward, next_state, terminal)
        self.log("transitions.jsonl", {"state": state, "action": ACTIONS[action], "reward": self.reward, "next_state": next_state, "terminal": terminal})
        self.agent.save()
        self.reward = 0.0
        self.pending = None

    def step(self):
        if self.busy or self.mode:
            return
        if self.image is None:
            self.status.set("Capture a frame and calibrate controls first.")
            return
        if self.calibrated != {"move", "attack"}:
            self.status.set("Calibrate move and attack controls before training.")
            self.running = False
            return
        self.finish()
        state = state_key(self.image)
        action = self.agent.choose(state)
        def execute():
            self.device.action(ACTIONS[action], self.image.size, self.controls)
            time.sleep(0.15)
            return self.device.screenshot()
        def complete(image):
            self.show(image)
            self.pending = (state, action, state_key(image))
            self.status.set(f"Action: {ACTIONS[action]} | Learned transitions: {self.agent.steps} | Give feedback now.")
            if self.running:
                self.root.after(1500, self.auto_step)
        self.work(execute, complete)

    def auto_step(self):
        if self.running:
            self.step()

    def start(self):
        if self.busy or self.mode or self.running:
            return
        self.running = True
        self.step()

    def pause(self):
        self.running = False

    def terminal(self):
        self.pause()
        if self.busy:
            self.status.set("Wait for the current action, then mark episode end again.")
            return
        self.finish(terminal=True)
        self.image = None
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
