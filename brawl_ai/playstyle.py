"""Learn action preferences from a player's own USB touch demonstrations."""
from collections import deque
import json
import math
from pathlib import Path
import queue
import random
import re
import subprocess
import threading
import time

from .android import environment
from .learning import ACTIONS, available_actions
from .profiles import key, normalize


def touch_devices(output):
    devices, current = [], None
    for line in output.splitlines():
        match = re.search(r"add device \d+: (/dev/input/event\d+)", line)
        if match:
            current = {"path": match[1]}
            devices.append(current)
        if current is not None:
            if "ABS_MT_SLOT" in line:
                current["protocol"] = "B"
            for axis in ("X", "Y"):
                match = re.search(r"ABS_MT_POSITION_" + axis + r"\s*:.*?min\s+(-?\d+),\s*max\s+(-?\d+)", line)
                if match:
                    current[axis] = tuple(map(int, match.groups()))
    for device in devices:
        device.setdefault("protocol", "A")
    return [device for device in devices if all(axis in device and device[axis][1] > device[axis][0] for axis in ("X", "Y"))]


def rotate_point(x, y, device, rotation):
    x = (x-device["X"][0])/(device["X"][1]-device["X"][0])
    y = (y-device["Y"][0])/(device["Y"][1]-device["Y"][0])
    x, y = max(0, min(1, x)), max(0, min(1, y))
    return {0: (x, y), 90: (y, 1-x), 180: (1-x, 1-y), 270: (1-y, x)}[rotation]


class TouchTracker:
    """Linux type-A contacts and type-B slots, with held movement segments."""

    def __init__(self, device, rotation):
        self.device, self.rotation = device, rotation
        self.slot = 0
        self.slots = {}
        self.packet = {}
        self.contacts = []

    def feed(self, line, now):
        if "SYN_DROPPED" in line:
            raise RuntimeError("Phone dropped touch events; stop and restart observation.")
        if self.device.get("protocol", "B") == "A":
            return self.feed_type_a(line, now)
        match = re.search(r"(ABS_MT_SLOT|ABS_MT_TRACKING_ID|ABS_MT_POSITION_X|ABS_MT_POSITION_Y)\s+([0-9a-fA-F]+)", line)
        if not match:
            return []
        code, value = match.groups()
        value = int(value, 16)
        if code == "ABS_MT_SLOT":
            self.slot = value
            return []
        if code == "ABS_MT_TRACKING_ID":
            old = self.slots.pop(self.slot, None)
            result = self.emit(old, now) if old else None
            if value != 0xffffffff:
                self.slots[self.slot] = {"started": now, "segment": now}
            return [result] if result else []
        touch = self.slots.get(self.slot)
        if touch is not None:
            touch["x" if code.endswith("X") else "y"] = value
            if "x" in touch and "y" in touch and "origin" not in touch:
                touch["origin"] = rotate_point(touch["x"], touch["y"], self.device, self.rotation)
        return []

    def feed_type_a(self, line, now):
        if "SYN_MT_REPORT" in line or "SYN_REPORT" in line:
            if all(axis in self.packet for axis in ("x", "y")) and self.packet.get("id", 0) != 0xffffffff:
                self.contacts.append(self.packet)
            self.packet = {}
            if "SYN_MT_REPORT" in line:
                return []
            present = set()
            results = []
            if len(self.contacts) > 1 and any("id" not in contact for contact in self.contacts):
                raise RuntimeError("This type-A device does not identify simultaneous fingers reliably.")
            for index, contact in enumerate(self.contacts):
                slot = contact.get("id", index)
                present.add(slot)
                touch = self.slots.setdefault(slot, {"started": now, "segment": now,
                    "origin": rotate_point(contact["x"], contact["y"], self.device, self.rotation)})
                touch.update(x=contact["x"], y=contact["y"])
            for slot in set(self.slots)-present:
                result = self.emit(self.slots.pop(slot), now)
                if result:
                    result["slot"] = slot
                    results.append(result)
            self.contacts = []
            return results
        match = re.search(r"ABS_MT_(POSITION_X|POSITION_Y|TRACKING_ID)\s+([0-9a-fA-F]+)", line)
        if match:
            code, value = match.groups()
            self.packet[{"POSITION_X": "x", "POSITION_Y": "y", "TRACKING_ID": "id"}[code]] = int(value, 16)
        return []

    def emit(self, touch, now):
        if not touch or "origin" not in touch or now-touch["segment"] < .03:
            return None
        result = {"start": touch["origin"], "end": rotate_point(touch["x"], touch["y"], self.device, self.rotation),
                  "touch_started": touch["started"],
                  "started": touch["segment"], "ended": now, "slot": self.slot}
        touch["segment"] = now
        return result

    def held(self, now):
        results = []
        for slot, touch in self.slots.items():
            if now-touch["segment"] >= .5:
                result = self.emit(touch, now)
                if result:
                    result["slot"] = slot
                    results.append(result)
        return results


def touch_control(origin, controls, calibrated, size):
    w, h = size
    candidates = []
    for control in ("move", "attack", "super", "gadget", "hypercharge"):
        if control in calibrated and control in controls:
            point = controls[control]
            distance = ((origin[0]-point[0])*w)**2 + ((origin[1]-point[1])*h)**2
            if distance <= (min(size)*.14)**2:
                candidates.append((distance, control))
    if not candidates:
        return None
    return min(candidates)[1]


def gesture_action(gesture, controls, calibrated, size):
    """Map observed gestures to the existing discrete action vocabulary."""
    w, h = size
    origin, end = gesture["start"], gesture["end"]
    control = touch_control(origin, controls, calibrated, size)
    if control is None:
        return None
    dx, dy = (end[0]-origin[0])*w, (end[1]-origin[1])*h
    if control == "move":
        # A held joystick remains displaced from its initial touch point.
        if max(abs(dx), abs(dy)) < min(size)*.015:
            return None
    elif control in ("super", "gadget") and controls.get(control+"_mode", "tap") == "drag":
        if max(abs(dx), abs(dy)) < min(size)*.015:
            return None
    else:
        # Taps/presses are one example on release, not repeated during a hold.
        if gesture.get("held"):
            return None
        return control
    direction = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) else ("down" if dy > 0 else "up")
    return direction if control == "move" else control+"_"+direction


class Playstyle:
    def __init__(self, profile_folder, player):
        self.player = normalize(player)
        self.folder = Path(profile_folder) / "playstyles" / key(self.player)
        self.path = self.folder / "style.json"
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.counts = data.get("counts", {})
        self.total = data.get("total", [0]*len(ACTIONS))
        self.applied_recordings = data.get("applied_recordings", [])
        self.motion = data.get("motion", {})
        self.examples = sum(self.total)
        self.contexts = {}
        for state, values in self.counts.items():
            context = self.context(state)
            aggregate = self.contexts.setdefault(context, [0]*len(ACTIONS))
            for i, value in enumerate(values):
                aggregate[i] += value

    @staticmethod
    def context(state):
        if state.startswith("map-v1:"):
            name, _ = json.JSONDecoder().raw_decode(state[len("map-v1:"):])
            return json.dumps(name)
        return "null"

    def observe(self, state, action, gesture=None, size=None):
        index = ACTIONS.index(action)
        values = self.counts.setdefault(state, [0]*len(ACTIONS))
        values[index] += 1
        self.total[index] += 1
        self.contexts.setdefault(self.context(state), [0]*len(ACTIONS))[index] += 1
        self.examples += 1
        if gesture and size and not gesture.get("idle") and action not in ("attack", "super", "gadget", "hypercharge"):
            distance = math.hypot((gesture["end"][0]-gesture["start"][0])*size[0],
                                  (gesture["end"][1]-gesture["start"][1])*size[1])/min(size)
            stats = self.motion.setdefault(self.context(state), {}).setdefault(action, {"count": 0, "radius": 0, "duration": 0})
            stats["count"] += 1
            stats["radius"] += max(.02, min(.3, distance))
            stats["duration"] += max(.05, min(.8, gesture["ended"]-gesture["started"]))

    def action_controls(self, state, action, controls):
        controls = dict(controls)
        stats = self.motion.get(self.context(state), {}).get(action)
        if stats and stats["count"]:
            control = action.split("_", 1)[0] if "_" in action else "move"
            controls[control+"_radius"] = stats["radius"]/stats["count"]
            controls[control+"_duration"] = round(1000*stats["duration"]/stats["count"])
        return controls

    def choose(self, state, available):
        local = self.counts.get(state)
        # Fall back to the player's action frequencies in unseen visual states.
        values = local if local and sum(local[i] for i in available) >= 3 else self.contexts.get(self.context(state), [0]*len(ACTIONS))
        weights = [values[i] for i in available]
        return random.choices(available, weights=weights)[0] if sum(weights) else None

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "player": self.player,
                                         "counts": self.counts, "total": self.total,
                                         "applied_recordings": self.applied_recordings, "motion": self.motion}))
        temporary.replace(self.path)


class PlayerSession:
    """Read-only phone observer; this class never injects input or enables root."""

    def __init__(self, device, style, controls, calibrated, rotation, interval, state_builder, recording, metadata=None):
        self.device, self.style = device, style
        self.controls, self.calibrated = dict(controls), set(calibrated)
        self.rotation, self.interval = rotation, interval
        self.state_builder, self.recording = state_builder, recording
        self.metadata = metadata or {}
        self.session_id = str(time.time_ns())
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.stopped_at = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.stopped_at is None:
            self.stopped_at = time.monotonic()
        self.stop_event.set()

    def record_gesture(self, gesture, frames):
        if not self.recording or not frames:
            return False
        action = "idle" if gesture.get("idle") else gesture_action(gesture, self.controls, self.calibrated, frames[-1]["image"].size)
        began = gesture["touch_started"] if action in ("attack", "super", "gadget", "hypercharge") else gesture["started"]
        prior = next((frame for frame in reversed(frames) if frame["captured"] <= began), None)
        if prior is None or began-prior["captured"] > 2 or action is None:
            return False
        if ACTIONS.index(action) not in available_actions(self.controls, self.calibrated):
            return False
        self.style.folder.mkdir(parents=True, exist_ok=True)
        if not prior.get("filename"):
            prior["filename"] = f"frame-{time.time_ns()}.jpg"
            preview = prior["image"].copy()
            preview.thumbnail((960, 540))
            preview.save(self.style.folder / prior["filename"], quality=85)
        record = {**self.metadata, "session": self.session_id,
                  "state": prior["state"], "action": action, "gesture": gesture,
                  "image": prior["filename"], "frame_time": prior["captured"],
                  "player": self.style.player, "rotation": self.rotation}
        with (self.style.folder / "demonstrations.jsonl").open("a") as stream:
            stream.write(json.dumps(record)+"\n")
        self.style.observe(prior["state"], action, gesture, frames[-1]["image"].size)
        self.style.save()
        return True

    def run(self):
        process = None
        try:
            devices = touch_devices(self.device.command("shell", "getevent", "-lp").decode(errors="replace"))
            if len(devices) != 1:
                raise RuntimeError("Expected one readable multitouch touchscreen. This phone may block getevent; "
                                   "USB debugging alone does not guarantee access. No root changes were made.")
            tracker = TouchTracker(devices[0], self.rotation)
            lines = queue.Queue(maxsize=20000)
            process = subprocess.Popen([self.device.adb, "-s", self.device.serial, "shell", "getevent", "-lt", devices[0]["path"]],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=environment())

            def read():
                for line in process.stdout:
                    try:
                        lines.put_nowait((time.monotonic(), line))
                    except queue.Full:
                        self.stop_event.set()
                        self.events.put(("error", "Input queue overflow; recording stopped to avoid mislabeling."))
                        break
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            frames = deque(maxlen=8)
            next_capture = 0
            pending = []
            last_point = None
            skipped = 0
            last_touch = last_idle = None
            self.events.put(("status", "Observing USB input. Check the touch marker against your finger position."))
            while True:
                now = self.stopped_at if self.stopped_at is not None else time.monotonic()
                # Process events with host arrival timestamps, preserving order across captures.
                while True:
                    try:
                        arrived, line = lines.get_nowait()
                    except queue.Empty:
                        break
                    if self.stopped_at is not None and arrived > self.stopped_at:
                        continue
                    pending.extend(tracker.feed(line, arrived))
                for gesture in tracker.held(now):
                    gesture["held"] = True
                    pending.append(gesture)
                for gesture in pending:
                    last_point = gesture["end"]
                    last_touch = gesture["ended"]
                    if self.recording and not self.record_gesture(gesture, frames):
                        skipped += 1
                pending.clear()
                if tracker.slots:
                    last_touch = now
                elif self.recording and last_touch is not None and now-max(last_touch, last_idle or last_touch) >= 1:
                    idle = {"idle": True, "started": now, "ended": now, "touch_started": now,
                            "start": None, "end": None, "slot": None}
                    if not self.record_gesture(idle, frames):
                        skipped += 1
                    last_idle = now
                if self.stop_event.is_set():
                    break
                if process.poll() is not None:
                    raise RuntimeError("Phone input stream closed. Check USB and getevent permissions.")
                if now >= next_capture:
                    image = self.device.screenshot()
                    captured = time.monotonic()
                    state, detections = self.state_builder(image)
                    frames.append({"image": image, "state": state, "captured": captured})
                    self.events.put(("frame", (image, detections, last_point, self.style.examples, skipped)))
                    next_capture = time.monotonic()+self.interval
                self.stop_event.wait(.02)
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    process.stdout.close()
            finally:
                self.events.put(("done", None))
