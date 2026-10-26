"""Human review of sampled gameplay frames; proposals never teach themselves."""
import copy
import json
from pathlib import Path
import queue
import threading

from PIL import Image, ImageStat, ImageTk

from .detection import ENTITY_NAMES, EnemyDetector, overlap
from .hud import CONTROLS, STATES, HudDetector
from .player import PlayerDetector, merge_player
from .profiles import normalize


def review_candidates(detections, metadata, size):
    items = [dict(copy.deepcopy(item), decision="pending") for item in detections]
    width, height = size
    radius = min(size)*.06
    for control in metadata.get("calibrated", []):
        if control not in CONTROLS or any(item.get("control") == control for item in items):
            continue
        cx, cy = metadata["controls"][control]
        items.append({"kind": "control", "control": control, "state": "unknown", "score": .25,
                      "source": "recorded_calibration", "decision": "pending",
                      "box": [max(0, cx-radius/width), max(0, cy-radius/height),
                              min(1, cx+radius/width), min(1, cy+radius/height)]})
    return items


class ReviewStore:
    def __init__(self, stage, profile_folder):
        self.stage, self.profile_folder = Path(stage).resolve(), Path(profile_folder).resolve()
        if not self.stage.is_relative_to(self.profile_folder):
            raise ValueError("Review must belong to its recorded profile.")
        self.path = self.stage/"review.json"
        self.data = json.loads(self.path.read_text())
        if not self.data.get("frames"):
            raise ValueError("This analysis has no review frames.")

    def image(self, index):
        path = (self.stage/self.data["frames"][index]["image"]).resolve()
        if not path.is_relative_to(self.stage):
            raise ValueError("Review image is outside this analysis.")
        with Image.open(path) as image:
            return image.convert("RGB")

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data))
        temporary.replace(self.path)

    def suggest(self, index):
        """Read the latest human-taught models; never train on these predictions."""
        image = self.image(index)
        metadata = self.data
        if "controls" not in metadata:
            session = self.stage.parent.parent/"session.json"
            metadata = json.loads(session.read_text()) if session.exists() else {}
        enemies = EnemyDetector(self.profile_folder).detect(image)
        controls = HudDetector(self.profile_folder).detect(image, metadata.get("controls", {}), metadata.get("calibrated", []))
        player = PlayerDetector(self.profile_folder, self.data["brawler"]).detect(image)
        return review_candidates(merge_player(enemies+controls, player), metadata, image.size)

    def update_suggestions(self, index, suggestions):
        frame = self.data["frames"][index]
        human = [item for item in frame["items"] if item["decision"] != "pending"]
        def resolved(candidate):
            kind = candidate.get("kind", "entity")
            for item in human:
                old_kind = item.get("kind", "entity")
                if kind == old_kind:
                    if kind == "control":
                        if item.get("control") != candidate.get("control"):
                            continue
                        if item["decision"] == "confirmed" or overlap(item["box"], candidate["box"]) >= .3:
                            return True
                    elif overlap(item["box"], candidate["box"]) >= .3:
                        return True
                if kind == "entity" and old_kind == "player" and item["decision"] == "confirmed" and overlap(item["box"], candidate["box"]) >= .3:
                    return True
            return False
        frame["items"] = human+[dict(copy.deepcopy(item), decision="pending") for item in suggestions if not resolved(item)]
        self.save()

    def teach(self, image, item, accepted):
        box = item["box"]
        if item["kind"] == "control":
            HudDetector(self.profile_folder).learn(image, box, item["control"], item["state"] if accepted else "rejected")
        elif item["kind"] == "player":
            PlayerDetector(self.profile_folder, self.data["brawler"]).learn(image, box, accepted)
        else:
            detector = EnemyDetector(self.profile_folder)
            try:
                detector.learn(image, box, "enemy" if accepted else "not_enemy",
                               item.get("identity") if accepted else None)
            except ValueError:
                if accepted:
                    raise
                detector.correct_box(image, box, "wrong")

    def decide(self, frame_index, item_index, accepted, kind, box, identity="", control="move", state="unknown"):
        if kind not in ("entity", "player", "control"):
            raise ValueError("Choose an object type.")
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError("Draw a nonempty box inside the frame.")
        if kind == "control" and (control not in CONTROLS or state not in STATES):
            raise ValueError("Choose a control and availability state.")
        if kind == "entity" and accepted and (not identity.strip() or identity.strip() == "Unknown"):
            raise ValueError("Name the entity, for example a brawler, NPC or Showdown box.")
        if accepted and kind == "entity":
            identity = normalize(identity)
        image = self.image(frame_index)
        if accepted:
            crop = image.crop((round(box[0]*image.width), round(box[1]*image.height),
                               round(box[2]*image.width), round(box[3]*image.height)))
            if min(crop.size) < (3 if kind == "entity" else 5):
                raise ValueError("Draw a larger object box.")
            if kind != "player" and max(ImageStat.Stat(crop).stddev) < (8 if kind == "entity" else 6):
                raise ValueError("Select the detailed object rather than an empty or flat region.")
        frame = self.data["frames"][frame_index]
        original = frame["items"][item_index] if item_index is not None else None
        if not accepted and original is None:
            raise ValueError("Select an existing proposal to reject.")
        item = dict(original or {}, kind=kind, box=list(box), decision="confirmed" if accepted else "rejected")
        if kind == "control":
            item.update(control=control, state=state)
        elif kind == "player":
            item["identity"] = self.data["brawler"]
        else:
            item["identity"] = identity.strip()
        if kind != "control":
            item.pop("control", None)
            item.pop("state", None)
        # A changed/rejected proposal must suppress its original geometry/role.
        if original and (not accepted or original["box"] != list(box) or original.get("kind", "entity") != kind
                         or (kind == "control" and original.get("control") != control)):
            self.teach(image, dict(original, kind=original.get("kind", "entity")), False)
        if accepted:
            self.teach(image, item, True)
        else:
            item = dict(original, decision="rejected")
        if item_index is None:
            frame["items"].append(item)
        else:
            frame["items"][item_index] = item
        self.save()


class GameReview:
    def __init__(self, root, store, changed, replay):
        import tkinter as tk
        from tkinter import ttk
        self.store, self.changed = store, changed
        self.index, self.selected, self.box = 0, None, None
        self.revision = 0
        self.refreshed = set()
        self.scan_running = False
        self.scan_results = queue.Queue(maxsize=1)
        self.closed = self.editing = False
        self.window = tk.Toplevel(root)
        self.window.title(f"Review recorded game - {store.data['brawler']}")
        tk.Label(self.window, text="Review samples across the whole game. Select a box to confirm/correct/reject, or draw a missing object.").pack(padx=8, pady=6)
        row = tk.Frame(self.window)
        row.pack()
        self.canvas = tk.Canvas(row, width=960, height=540, background="black")
        self.canvas.pack(side="left")
        self.items = tk.Listbox(row, width=40, exportselection=False)
        self.items.pack(side="left", fill="y")
        self.items.bind("<<ListboxSelect>>", self.select)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        nav = tk.Frame(self.window)
        nav.pack()
        tk.Button(nav, text="Previous sample", command=lambda: self.move(-1)).pack(side="left")
        tk.Button(nav, text="Next sample", command=lambda: self.move(1)).pack(side="left")
        tk.Button(nav, text="Watch replay", command=replay).pack(side="left")
        self.position = tk.IntVar(value=0)
        tk.Scale(self.window, from_=0, to=len(store.data["frames"])-1, variable=self.position,
                 orient="horizontal", length=700, command=self.seek).pack()
        self.kind = tk.StringVar(value="entity")
        self.identity = tk.StringVar(value="NPC")
        self.control = tk.StringVar(value="move")
        self.state = tk.StringVar(value="unknown")
        edit = tk.Frame(self.window)
        edit.pack(pady=6)
        for label, variable, values, width, readonly in (
            ("Type", self.kind, ("entity", "player", "control"), 9, True),
            ("Identity", self.identity, ENTITY_NAMES, 18, False),
            ("Control", self.control, CONTROLS, 12, True),
            ("State", self.state, STATES, 12, True)):
            tk.Label(edit, text=label).pack(side="left")
            ttk.Combobox(edit, textvariable=variable, values=values, width=width,
                         state="readonly" if readonly else "normal").pack(side="left")
        buttons = tk.Frame(self.window)
        buttons.pack()
        tk.Button(buttons, text="Confirm / save correction", command=lambda: self.commit(True)).pack(side="left")
        tk.Button(buttons, text="Reject proposal", command=lambda: self.commit(False)).pack(side="left")
        tk.Button(buttons, text="Add missing object", command=self.add).pack(side="left")
        tk.Button(buttons, text="Close (progress saved)", command=self.close).pack(side="left")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar()
        tk.Label(self.window, textvariable=self.status, wraplength=1000).pack(pady=6)
        self.load()
        self.scan_job = self.window.after(50, self.poll_scan)

    def load(self, refresh=True):
        self.selected, self.box = None, None
        self.editing = False
        self.image = self.store.image(self.index)
        self.image.thumbnail((960, 540))
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas.configure(width=self.image.width, height=self.image.height)
        self.items.delete(0, "end")
        for item in self.store.data["frames"][self.index]["items"]:
            self.items.insert("end", f"{item['decision']}: {item.get('kind', 'entity')} / {item.get('control', item.get('identity', 'Unknown'))}")
        self.draw()
        frame = self.store.data["frames"][self.index]
        pending = sum(item["decision"] == "pending" for sample in self.store.data["frames"] for item in sample["items"])
        self.status.set(f"Sample {self.index+1}/{len(self.store.data['frames'])} | {frame['time']:.1f}s | {pending} pending proposals. Missing entities need human labels.")
        if self.store.data.get("timing_warning"):
            self.status.set(self.status.get()+" "+self.store.data["timing_warning"])
        if refresh:
            self.start_scan()

    def start_scan(self):
        if self.closed or self.scan_running or self.editing or (self.index, self.revision) in self.refreshed:
            return
        index, revision = self.index, self.revision
        self.scan_running = True
        self.status.set(self.status.get()+" | Updating proposals from previous human examples...")
        def scan():
            try:
                result = (index, revision, self.store.suggest(index), None)
            except Exception as error:
                result = (index, revision, None, str(error))
            self.scan_results.put_nowait(result)
        threading.Thread(target=scan, daemon=True).start()

    def poll_scan(self):
        if self.closed:
            return
        try:
            index, revision, suggestions, error = self.scan_results.get_nowait()
            self.scan_running = False
            if revision == self.revision and not (index == self.index and self.editing):
                if error:
                    if index == self.index:
                        self.status.set(f"Proposal update failed; saved proposals retained: {error}")
                    self.refreshed.add((index, revision))
                else:
                    self.store.update_suggestions(index, suggestions)
                    self.refreshed.add((index, revision))
                    if index == self.index:
                        self.load(refresh=False)
        except queue.Empty:
            pass
        except (OSError, ValueError) as error:
            self.status.set(f"Could not save updated proposals: {error}")
            self.refreshed.add((index, revision))
        self.start_scan()
        self.scan_job = self.window.after(50, self.poll_scan)

    def close(self):
        self.closed = True
        self.window.after_cancel(self.scan_job)
        self.window.destroy()

    def draw(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        for index, item in enumerate(self.store.data["frames"][self.index]["items"]):
            box = self.box if index == self.selected and self.box is not None else item["box"]
            color = "gray" if item["decision"] == "rejected" else "lime" if item["decision"] == "confirmed" else "cyan" if item.get("kind") == "control" else "orange"
            self.canvas.create_rectangle(*self.coords(box), outline="yellow" if index == self.selected else color, width=2)
            self.canvas.create_text(box[0]*self.image.width, max(10, box[1]*self.image.height), text=str(index+1), fill=color, anchor="sw")
        if self.selected is None and self.box is not None:
            self.canvas.create_rectangle(*self.coords(self.box), outline="yellow", width=2)

    def coords(self, box):
        return [box[0]*self.image.width, box[1]*self.image.height, box[2]*self.image.width, box[3]*self.image.height]

    def select(self, event=None):
        selected = self.items.curselection()
        if not selected:
            return
        self.selected = selected[0]
        self.editing = True
        item = self.store.data["frames"][self.index]["items"][self.selected]
        self.box = list(item["box"])
        self.kind.set(item.get("kind", "entity"))
        self.identity.set(item.get("identity", "Unknown"))
        self.control.set(item.get("control", "move"))
        self.state.set(item.get("state", "unknown"))
        self.draw()

    def point(self, event):
        return [max(0, min(1, event.x/self.image.width)), max(0, min(1, event.y/self.image.height))]

    def press(self, event):
        self.editing = True
        self.origin = self.point(event)

    def drag(self, event):
        end = self.point(event)
        self.box = [min(self.origin[0], end[0]), min(self.origin[1], end[1]),
                    max(self.origin[0], end[0]), max(self.origin[1], end[1])]
        self.draw()

    def release(self, event):
        end = self.point(event)
        if abs(end[0]-self.origin[0])+abs(end[1]-self.origin[1]) > .015:
            self.drag(event)
            return
        for index, item in reversed(list(enumerate(self.store.data["frames"][self.index]["items"]))):
            x0, y0, x1, y1 = item["box"]
            if x0 <= end[0] <= x1 and y0 <= end[1] <= y1:
                self.items.selection_clear(0, "end")
                self.items.selection_set(index)
                self.select()
                return

    def add(self):
        self.editing = True
        self.selected, self.box = None, None
        self.items.selection_clear(0, "end")
        self.draw()
        self.status.set("Choose the type and name/control, draw the missing object, then save.")

    def commit(self, accepted):
        try:
            if self.box is None:
                raise ValueError("Select a proposal or draw a missing object first.")
            self.store.decide(self.index, self.selected, accepted, self.kind.get(), self.box,
                              self.identity.get(), self.control.get(), self.state.get())
            self.changed()
            self.revision += 1
            self.refreshed.clear()
            self.load()
        except (OSError, ValueError, KeyError) as error:
            self.status.set(f"Could not save review: {error}")

    def seek(self, value):
        index = int(float(value))
        if index != self.index:
            self.index = index
            self.load()

    def move(self, direction):
        self.position.set(max(0, min(len(self.store.data["frames"])-1, self.index+direction)))
        self.seek(self.position.get())
