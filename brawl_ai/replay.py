"""Render and play an analyzed game with touch paths and character proposals."""
import bisect
import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import time

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .playstyle import TouchTracker, gesture_action, rotate_point, touch_control
from .hud import hud_label

TASKS = (("video", "Save game video"), ("input", "Save phone input"),
         ("index", "Read video timeline"), ("gestures", "Interpret finger gestures"),
         ("examples", "Learn player actions"), ("replay", "Recognize characters and prepare replay"),
         ("model", "Save learned playstyle"))
COLORS = ("#00e5ff", "#ffcf40", "#ff66d4", "#67ff8d", "#ab95ff", "#ff9366")


def emit_task(events, task, current=None, total=None, label=None):
    events.put(("task", {"task": task, "current": current, "total": total, "label": label}))


def touch_timeline(folder, metadata, offset):
    rows = [json.loads(line) for line in (folder/"input.jsonl").read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row["device"] is not None and row["host"] <= metadata["stopped_host"]]
    if not rows:
        return []
    clock = min(row["host"]-row["device"] for row in rows)
    tracker = TouchTracker(metadata["input_device"], metadata["rotation"])
    timeline = []
    # Aspect ratio, not video resolution, determines gesture direction/radius.
    size = metadata["video_size"]
    for row in rows:
        now = row["device"]+clock
        tracker.feed(row["line"], now)
        contacts = []
        for slot, contact in tracker.slots.items():
            if "origin" not in contact:
                continue
            point = rotate_point(contact["x"], contact["y"], tracker.device, tracker.rotation)
            gesture = {"start": contact["origin"], "end": point}
            action = gesture_action(gesture, metadata["controls"], set(metadata["calibrated"]), size)
            control = touch_control(contact["origin"], metadata["controls"], set(metadata["calibrated"]), size)
            dx = (point[0]-contact["origin"][0])*size[0]
            dy = (point[1]-contact["origin"][1])*size[1]
            contacts.append({"slot": slot, "born": contact["started"], "origin": contact["origin"],
                             "point": point, "control": control, "action": action or (f"{control} hold" if control else "unmapped touch"),
                             "drag": math.hypot(dx, dy) > min(size)*.015})
        if not timeline or timeline[-1]["contacts"] != contacts:
            timeline.append({"time": now-metadata["video_origin_host"]+offset, "contacts": contacts})
    return timeline


def draw_overlay(image, stamp, timeline, times, detections, recognition_ready=True):
    image = image.copy().convert("RGB")
    draw = ImageDraw.Draw(image)
    w, h = image.size
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(12, w//64))
    except OSError:
        font = ImageFont.load_default()
    def label(x, y, text, color):
        bounds = draw.textbbox((0, 0), text, font=font)
        x, y = max(0, min(x, w-bounds[2]-4)), max(0, min(y, h-bounds[3]-4))
        box = draw.textbbox((x, y), text, font=font)
        draw.rectangle(box, fill="black")
        draw.text((x, y), text, font=font, fill=color)
    index = bisect.bisect_right(times, stamp)-1
    if index >= 0:
        for contact in timeline[index]["contacts"]:
            color = COLORS[contact["slot"] % len(COLORS)]
            origin = (contact["origin"][0]*w, contact["origin"][1]*h)
            point = (contact["point"][0]*w, contact["point"][1]*h)
            trail = []
            previous = index
            while previous >= 0 and stamp-timeline[previous]["time"] <= .8:
                for old in timeline[previous]["contacts"]:
                    if old["slot"] == contact["slot"] and old["born"] == contact["born"]:
                        trail.append((old["point"][0]*w, old["point"][1]*h))
                previous -= 1
            if len(trail) > 1:
                draw.line(list(reversed(trail)), fill=color, width=3)
            if contact["drag"]:
                draw.line([origin, point], fill=color, width=3)
                angle = math.atan2(point[1]-origin[1], point[0]-origin[0])
                head = [(point[0]-12*math.cos(angle+a), point[1]-12*math.sin(angle+a)) for a in (-.5, .5)]
                draw.polygon([point, *head], fill=color)
            draw.ellipse((origin[0]-8, origin[1]-8, origin[0]+8, origin[1]+8), outline=color, width=2)
            draw.ellipse((point[0]-6, point[1]-6, point[0]+6, point[1]+6), fill=color)
            action = f"move {contact['action']}" if contact.get("control") == "move" and contact["action"] in ("up", "down", "left", "right") else contact["action"].replace("_", " ")
            label(point[0]+10, point[1]-24, f"Finger {contact['slot']}: {action} {'(drag)' if contact['drag'] else '(press)'}", color)
    for detection in detections:
        x0, y0, x1, y1 = detection["box"]
        color = "cyan" if detection.get("kind") == "control" else "red"
        draw.rectangle((x0*w, y0*h, x1*w, y1*h), outline=color, width=2)
        label(x0*w, y0*h-22, hud_label(detection), color)
    label(8, 8, f"{stamp:.1f}s | Character proposals sampled" if recognition_ready else
          f"{stamp:.1f}s | Character recognition needs labeled examples", "white")
    return image


def read_exact(stream, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = stream.read(count-len(chunks))
        if not part:
            return None
        chunks.extend(part)
    return bytes(chunks)


def render_replay(folder, stage, metadata, duration, state_builder, offset, stop_event, events, recognition_ready=True):
    # Imports are delayed to keep media helpers independent of replay/UI construction.
    from .recordings import AnalysisCancelled, write_json
    width, height = metadata["video_size"]
    ratio = min(1, 960/width, 540/height)
    width, height = max(2, int(width*ratio)//2*2), max(2, int(height*ratio)//2*2)
    fps = 20
    timeline = touch_timeline(folder, metadata, offset)
    write_json(stage/"finger-actions.json", timeline)
    times = [item["time"] for item in timeline]
    output = stage/"replay.mp4"
    decode = encode = None
    count = 0
    expected = max(1, math.ceil(duration*fps))
    next_detection = 0
    detections = []
    proposals = []
    emit_task(events, "replay", 0, expected)
    with (stage/"replay-media.log").open("wb") as log:
        try:
            decode = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(folder/"game.mkv"),
                "-vf", f"scale={width}:{height},fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, stderr=log)
            encode = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(output)], stdin=subprocess.PIPE, stderr=log)
            while True:
                if stop_event.is_set():
                    raise AnalysisCancelled()
                raw = read_exact(decode.stdout, width*height*3)
                if raw is None:
                    break
                stamp = count/fps
                image = Image.frombytes("RGB", (width, height), raw)
                if stamp >= next_detection:
                    _, detections = state_builder(image)
                    proposals.append({"time": stamp, "detections": detections})
                    next_detection = stamp+1
                annotated = draw_overlay(image, stamp, timeline, times, detections, recognition_ready)
                encode.stdin.write(annotated.tobytes())
                count += 1
                if count % fps == 0:
                    emit_task(events, "replay", min(count, expected), expected)
            encode.stdin.close()
            if decode.wait(timeout=15) or encode.wait(timeout=30) or count == 0:
                raise RuntimeError("Replay video could not be rendered. See replay-media.log; original recording is unchanged.")
        finally:
            for process in (decode, encode):
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
    write_json(stage/"replay.json", {"width": width, "height": height, "fps": fps, "frames": count,
        "duration": count/fps, "sync_offset": offset, "recognition_ready": recognition_ready})
    write_json(stage/"character-proposals.json", [{"time": sample["time"], "detections": [item for item in sample["detections"] if item.get("kind") != "control"]} for sample in proposals])
    write_json(stage/"control-proposals.json", [{"time": sample["time"], "detections": [item for item in sample["detections"] if item.get("kind") == "control"]} for sample in proposals])
    emit_task(events, "replay", expected, expected)
    return output


class ReplayPlayer:
    """Small Tk player; decoding stays off the UI thread and the frame queue is bounded."""

    def __init__(self, root, path):
        import tkinter as tk
        self.path = Path(path)
        info = json.loads(self.path.with_suffix(".json").read_text())
        self.width, self.height, self.fps = info["width"], info["height"], info["fps"]
        self.duration = info["duration"]
        self.window = tk.Toplevel(root)
        self.window.title("Game replay - fingers, drags and character proposals")
        self.canvas = tk.Canvas(self.window, width=self.width, height=self.height)
        self.canvas.pack()
        controls = tk.Frame(self.window)
        controls.pack()
        self.paused = False
        self.position = tk.DoubleVar(value=0)
        self.caption = tk.StringVar()
        tk.Button(controls, text="Play / pause", command=self.toggle).pack(side="left")
        tk.Label(controls, textvariable=self.caption).pack(side="left")
        slider = tk.Scale(self.window, from_=0, to=self.duration, resolution=.1,
                          variable=self.position, orient="horizontal", length=self.width)
        slider.pack()
        self.scrubbing = False
        slider.bind("<ButtonPress-1>", lambda event: setattr(self, "scrubbing", True))
        slider.bind("<ButtonRelease-1>", lambda event: self.seek(self.position.get()))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.reader = None
        self.job = None
        self.seek(0)

    def toggle(self):
        self.paused = not self.paused

    def seek(self, stamp):
        stamp = max(0, min(stamp, self.duration-1/self.fps))
        self.scrubbing = False
        if self.reader is not None:
            self.reader.set()
        stop = self.reader = threading.Event()
        frames = self.frames = queue.Queue(maxsize=3)
        self.deadline = time.monotonic()
        def decode():
            process = None
            index = 0
            try:
                process = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(stamp), "-i", str(self.path),
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                while not stop.is_set():
                    raw = read_exact(process.stdout, self.width*self.height*3)
                    if raw is None:
                        break
                    while not stop.is_set():
                        try:
                            frames.put((stamp+index/self.fps, raw), timeout=.1)
                            break
                        except queue.Full:
                            continue
                    index += 1
            except Exception as error:
                try:
                    frames.put_nowait(("error", str(error)))
                except queue.Full:
                    pass
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    process.stdout.close()
        threading.Thread(target=decode, daemon=True).start()
        # Display the sought frame even when paused, then honor the pause again.
        self.sought = True
        if self.job is None:
            self.job = self.window.after(10, self.tick)

    def tick(self):
        self.job = None
        if not self.scrubbing and (not self.paused or self.sought) and time.monotonic() >= self.deadline:
            try:
                stamp, raw = self.frames.get_nowait()
                if stamp == "error":
                    self.caption.set(raw)
                    self.paused, self.sought = True, False
                    self.job = self.window.after(100, self.tick)
                    return
                self.photo = ImageTk.PhotoImage(Image.frombytes("RGB", (self.width, self.height), raw))
                self.canvas.delete("all")
                self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
                self.position.set(stamp)
                self.caption.set(f"{stamp:.1f} / {self.duration:.1f} seconds")
                self.sought = False
                self.deadline = time.monotonic()+1/self.fps
            except queue.Empty:
                pass
        self.job = self.window.after(10, self.tick)

    def close(self):
        if self.reader is not None:
            self.reader.set()
        if self.job is not None:
            self.window.after_cancel(self.job)
        self.window.destroy()
