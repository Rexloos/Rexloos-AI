"""Recognize the controlled character from self markers, without pretrained weights."""
import hashlib
import json
import math
from pathlib import Path
import statistics

from PIL import Image

from .hud import hsv_pixels, similarity


def frame_digest(image):
    rgb = image.convert("RGB")
    return hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()


def intersection_over_union(first, second):
    common = max(0, min(first[2], second[2])-max(first[0], second[0]))*max(0, min(first[3], second[3])-max(first[1], second[1]))
    area = lambda box: (box[2]-box[0])*(box[3]-box[1])
    return common/max(1e-9, area(first)+area(second)-common)


def player_state(state, detections):
    own = next((item for item in detections if item.get("kind") == "player"), None)
    if own is None:
        return state
    box = own["box"]
    cell = min(2, int((box[1]+box[3])*1.5))*4+min(3, int((box[0]+box[2])*2))
    return state+f"|self-v1:{cell}"


def merge_player(detections, players):
    """The same body must not simultaneously become the self and an enemy target."""
    if not players:
        return list(detections)
    return [item for item in detections if item.get("kind") != "player" and (item.get("kind") == "control" or
            not any(intersection_over_union(item["box"], own["box"]) >= .3 for own in players))]+players


class PlayerDetector:
    def __init__(self, folder, brawler):
        self.path = Path(folder)/"player-detector.json"
        self.brawler = brawler
        self.samples = json.loads(self.path.read_text())["samples"] if self.path.exists() else []
        if not self.path.exists():
            for journal in (Path(folder)/"annotations.jsonl", Path(folder)/"player-annotations.jsonl"):
                if not journal.exists():
                    continue
                for line in journal.read_text().splitlines()[-200:]:
                    try:
                        record = json.loads(line)
                        if str(record.get("label", "")).strip().casefold() not in ("player", "self") and record.get("role") != "self":
                            continue
                        path = (Path(folder)/record["image"]).resolve()
                        if not path.is_relative_to(Path(folder).resolve()):
                            continue
                        with Image.open(path) as image:
                            self.learn(image, record["box"], record.get("accepted", True))
                    except (OSError, ValueError, KeyError, TypeError):
                        continue

    @staticmethod
    def anchors(frame):
        w, h = frame.size
        colors = hsv_pixels(frame)
        green = {index for index, (hue, sat, value) in enumerate(colors)
                 if 60 <= hue <= 115 and sat >= 120 and value >= 150}
        remaining = set(green)
        anchors = []
        while remaining:
            seed = remaining.pop()
            stack, pixels = [seed], [seed]
            while stack:
                pos = stack.pop()
                x, y = pos % w, pos//w
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = x+dx, y+dy
                    neighbor = ny*w+nx
                    if 0 <= nx < w and 0 <= ny < h and neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
                        pixels.append(neighbor)
            xs, ys = [pos % w for pos in pixels], [pos//w for pos in pixels]
            x0, x1, y0, y1 = min(xs), max(xs)+1, min(ys), max(ys)+1
            bw, bh = x1-x0, y1-y0
            if not (2 <= bw/bh <= 25 and min(w, h)*.02 <= bw <= w*.16 and 1 <= bh <= h*.04
                    and h*.1 <= y0 <= h*.8 and w*.05 <= x0 <= w*.95):
                continue
            cx = (x0+x1)/2
            # Recover the full blue/gray bar outline when only part is green.
            outline = None
            span = min(w*.16, bw*4)
            for row in range(max(0, y0-2), min(h, y1+4)):
                run = None
                for col in range(max(0, round(cx-span)), min(w, round(cx+span))):
                    hue, sat, value = colors[row*w+col]
                    blue = 115 <= hue <= 190 and sat >= 25 and 25 <= value <= 220
                    bar_pixel = blue or row*w+col in green or value < 85 or (sat < 60 and value < 180)
                    if bar_pixel and run is None:
                        run = col
                    if run is not None and (not bar_pixel or col == min(w, round(cx+span))-1):
                        end = col if not bar_pixel else col+1
                        edge_pixels = [colors[row*w+edge] for edge in (*range(run, min(run+3, end)), *range(max(run, end-3), end))]
                        blue_edges = sum(115 <= hue <= 190 and sat >= 25 and 25 <= value <= 220 for hue, sat, value in edge_pixels)
                        if run <= cx <= end and bw <= end-run <= min(w*.16, bw*4) and blue_edges >= 2:
                            if outline is None or end-run > outline[1]-outline[0]:
                                outline = (run, end)
                        run = None
            if outline is None:
                continue
            left, right = outline
            width, cx = right-left, (left+right)/2
            if width < min(w, h)*.035:
                continue
            # The green name must be sparse glyph-like marks, not a solid bush.
            name_box = (max(0, round(cx-width*.95)), max(0, round(y0-width*.65)),
                        min(w, round(cx+width*.95)), max(0, y0-1))
            nx0, ny0, nx1, ny1 = name_box
            name_pixels = [pos for pos in green if nx0 <= pos % w < nx1 and ny0 <= pos//w < ny1]
            area = max(1, (nx1-nx0)*(ny1-ny0))
            name_score = len(name_pixels)/area
            columns = {pos % w for pos in name_pixels}
            name_ok = .025 <= name_score <= .48 and len(columns) >= width*.5
            # A green foot ring provides independent evidence when the name is occluded.
            ring_hits = 0
            ring_cy = y1+width*1.05
            for index in range(20):
                angle = index*math.tau/20
                x, y = round(cx+math.cos(angle)*width*.57), round(ring_cy+math.sin(angle)*width*.3)
                ox, oy = round(cx+math.cos(angle)*width*.8), round(ring_cy+math.sin(angle)*width*.45)
                if 0 <= x < w and 0 <= y < h and y*w+x in green:
                    if not (0 <= ox < w and 0 <= oy < h and oy*w+ox in green):
                        ring_hits += 1
            ring_ok = ring_hits >= 6
            if not name_ok and not ring_ok:
                continue
            anchors.append({"cx": cx, "bottom": y1, "width": width,
                            "score": .96 if name_ok and ring_ok else .88 if name_ok else .82,
                            "evidence": ["green health bar", "bar outline"]+
                                        (["green name"] if name_ok else [])+(["green foot ring"] if ring_ok else [])})
        return anchors

    def learn(self, image, box, accepted=True):
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Draw a nonempty box around the controlled character.")
        frame = image.convert("RGB")
        frame.thumbnail((400, 250))
        w, h = frame.size
        anchors = self.anchors(frame)
        geometry = None
        if accepted and anchors:
            cx, cy = (x0+x1)*w/2, (y0+y1)*h/2
            anchor = min(anchors, key=lambda item: math.hypot(cx-item["cx"], cy-item["bottom"]-item["width"]*.7))
            width = anchor["width"]
            if abs(cx-anchor["cx"]) < width and .2*width <= (y1-y0)*h <= 3*width:
                geometry = [(x0*w-anchor["cx"])/width, (y0*h-anchor["bottom"])/width,
                            (x1*w-anchor["cx"])/width, (y1*h-anchor["bottom"])/width]
        digest = frame_digest(image)
        crop = image.convert("RGB").crop((round(x0*image.width), round(y0*image.height), round(x1*image.width), round(y1*image.height)))
        if min(crop.size) < 5:
            raise ValueError("Draw a larger player box.")
        sample = {"box": box, "accepted": accepted, "geometry": geometry, "frame_digest": digest,
                  "pixels": list(crop.resize((16, 16)).tobytes())}
        previous = [item for item in self.samples if item["frame_digest"] != digest]
        if accepted:
            feature = Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))
            previous = [item for item in previous if item["accepted"] or intersection_over_union(item["box"], box) < .3
                        or similarity(feature, Image.frombytes("RGB", (16, 16), bytes(item["pixels"]))) < .95]
        updated = [item for item in previous if item["accepted"] != accepted]+[item for item in previous if item["accepted"] == accepted][-11:]+[sample]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "samples": updated}))
        temporary.replace(self.path)
        self.samples = updated
        return geometry is not None

    def detect(self, image):
        samples = tuple(self.samples)
        digest = frame_digest(image) if samples else None
        exact = next((item for item in reversed(samples) if item["frame_digest"] == digest and item["accepted"]), None)
        if exact is not None:
            return [self.result(exact["box"], 1.0, ["human confirmation of this exact frame"])]
        frame = image.convert("RGB")
        frame.thumbnail((400, 250))
        w, h = frame.size
        learned = [item["geometry"] for item in samples if item["accepted"] and item["geometry"] is not None]
        geometry = [statistics.median(values) for values in zip(*learned)] if learned else [-.65, .12, .65, 1.5]
        candidates = []
        for anchor in self.anchors(frame):
            cx, bottom, width = anchor["cx"], anchor["bottom"], anchor["width"]
            box = [max(0, (cx+geometry[0]*width)/w), max(0, (bottom+geometry[1]*width)/h),
                   min(1, (cx+geometry[2]*width)/w), min(1, (bottom+geometry[3]*width)/h)]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = frame.crop((round(box[0]*w), round(box[1]*h), round(box[2]*w), round(box[3]*h))).resize((16, 16))
            rejected = any(not sample["accepted"] and intersection_over_union(sample["box"], box) >= .3 and
                           (sample["frame_digest"] == digest or similarity(crop, Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))) >= .97)
                           for sample in samples)
            if not rejected:
                supported = any(sample["accepted"] and similarity(crop, Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))) >= .97 for sample in samples)
                score = min(.99, anchor["score"]+.05) if supported else anchor["score"]
                evidence = anchor["evidence"]+(["confirmed appearance supports marker"] if supported else [])
                candidates.append(self.result(box, score, evidence))
        candidates.sort(key=lambda item: item["score"], reverse=True)
        # Multiple similarly supported self markers are ambiguous; never pick by screen center.
        if len(candidates) > 1 and candidates[0]["score"]-candidates[1]["score"] < .025:
            return []
        return candidates[:1]

    def result(self, box, score, evidence):
        return {"kind": "player", "role": "self", "identity": self.brawler,
                "box": box, "score": score, "evidence": evidence, "identity_source": "selected profile"}
