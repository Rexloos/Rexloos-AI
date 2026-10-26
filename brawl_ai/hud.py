"""Untrained HUD color/shape proposals and human-taught control/state templates."""
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

CONTROLS = ("move", "attack", "super", "gadget", "hypercharge")
STATES = ("unknown", "visible", "ready", "uncharged", "cooldown", "unavailable")
NAMES = {"move": "Movement joystick", "attack": "Attack", "super": "Super", "gadget": "Gadget", "hypercharge": "Hypercharge"}


def hud_label(item):
    if item.get("kind") != "control":
        return f"{item.get('identity', 'Unknown')}? {item.get('identity_score', 0):.2f}"
    state = item.get("state", "unknown")
    suffix = "?" if item.get("state_score", 0) < .9 else ""
    return f"{NAMES[item['control']]}: {state}{suffix}"


def hud_state(state, detections):
    visible = {item["control"]: item["state"] if item.get("state_score", 0) >= .9 else "unknown"
               for item in detections if item.get("kind") == "control"}
    return state if not visible else state+"|hud-v1:"+",".join(f"{name}={visible[name]}" for name in sorted(visible))


def similarity(first, second):
    return 1-sum(ImageStat.Stat(ImageChops.difference(first, second)).mean)/(3*255)


def hsv_pixels(image):
    data = image.convert("HSV").tobytes()
    return list(zip(data[0::3], data[1::3], data[2::3]))


class HudDetector:
    def __init__(self, folder):
        self.path = Path(folder)/"hud-detector.json"
        self.samples = json.loads(self.path.read_text())["samples"] if self.path.exists() else []

    def learn(self, image, box, control, state):
        if control not in CONTROLS or state not in (*STATES, "rejected"):
            raise ValueError("Select a known control and availability state.")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Draw a nonempty box around the control.")
        w, h = image.size
        crop = image.convert("RGB").crop((round(x0*w), round(y0*h), round(x1*w), round(y1*h)))
        if min(crop.size) < 5:
            raise ValueError("Draw a larger control box.")
        if state != "rejected" and max(ImageStat.Stat(crop).stddev) < 6:
            raise ValueError("Select the actual control and its icon, not an empty region.")
        sample = {"control": control, "state": state, "box": box, "pixels": list(crop.resize((16, 16)).tobytes())}
        samples = [old for old in self.samples if not (old["control"] == control and old["pixels"] == sample["pixels"])]
        same = [old for old in samples if (old["control"], old["state"]) == (control, state)][-11:]
        other = [old for old in samples if (old["control"], old["state"]) != (control, state)]
        updated = other+same+[sample]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "samples": updated}))
        temporary.replace(self.path)
        self.samples = updated

    @staticmethod
    def circle_score(image, cx, cy, radius):
        w, h = image.size
        differences = []
        for index in range(24):
            angle = index*math.tau/24
            colors = []
            for factor in (.82, 1.15):
                x, y = round(cx+math.cos(angle)*radius*factor), round(cy+math.sin(angle)*radius*factor)
                if not (0 <= x < w and 0 <= y < h):
                    break
                colors.append(image.getpixel((x, y)))
            if len(colors) == 2:
                differences.append(sum(abs(a-b) for a, b in zip(*colors))/3)
        if not differences:
            return 0
        supported = sum(value >= 18 for value in differences)/len(differences)
        return .65*supported+.35*min(1, sum(differences)/len(differences)/55)

    def color_candidates(self, frame, controls):
        w, h = frame.size
        colors = hsv_pixels(frame)
        masks = {name: set() for name in CONTROLS}
        for y in range(round(h*.2), round(h*.98)):
            for x in range(w):
                hue, saturation, value = colors[y*w+x]
                if saturation < 65 or value < 75:
                    continue
                if x < w*.35 and y > h*.5 and 115 <= hue <= 180:
                    masks["move"].add(y*w+x)
                if x > w*.7 and y > h*.5 and (hue < 28 or hue > 240):
                    masks["attack"].add(y*w+x)
                if x > w*.6 and y > h*.45 and (125 <= hue <= 180 or 28 <= hue <= 55):
                    masks["super"].add(y*w+x)
                if x > w*.65 and 60 <= hue <= 115 and saturation >= 120 and value >= 170:
                    masks["gadget"].add(y*w+x)
                if x > w*.6 and 180 <= hue <= 238 and saturation >= 100:
                    masks["hypercharge"].add(y*w+x)
        results = []
        defaults = {"move": (.13, .8), "attack": (.9, .8), "super": (.8, .78), "gadget": (.9, .6), "hypercharge": (.8, .65)}
        for control, remaining in masks.items():
            candidates = []
            while remaining:
                seed = remaining.pop()
                stack, component = [seed], [seed]
                while stack:
                    pos = stack.pop()
                    x, y = pos % w, pos//w
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
                        nx, ny = x+dx, y+dy
                        neighbor = ny*w+nx
                        if 0 <= nx < w and 0 <= ny < h and neighbor in remaining:
                            remaining.remove(neighbor)
                            stack.append(neighbor)
                            component.append(neighbor)
                if len(component) < 12:
                    continue
                xs, ys = [pos % w for pos in component], [pos//w for pos in component]
                x0, x1, y0, y1 = min(xs), max(xs)+1, min(ys), max(ys)+1
                bw, bh = x1-x0, y1-y0
                if not (.65 <= bw/bh <= 1.45 and min(w, h)*.035 <= min(bw, bh) <= min(w, h)*.24):
                    continue
                cx, cy, radius = (x0+x1)/2, (y0+y1)/2, (bw+bh)/4
                circle = self.circle_score(frame, cx, cy, radius)
                if circle < .55:
                    continue
                expected = controls.get(control, defaults[control])
                distance = math.hypot(cx/w-expected[0], cy/h-expected[1])
                score = .7*circle+.3*max(0, 1-distance/.5)
                pad = radius*.14
                box = [max(0, (x0-pad)/w), max(0, (y0-pad)/h), min(1, (x1+pad)/w), min(1, (y1+pad)/h)]
                candidates.append({"kind": "control", "control": control, "box": box,
                                   "center": [cx/w, cy/h], "score": score, "source": "color_shape"})
            if candidates:
                results.extend(sorted(candidates, key=lambda item: item["score"], reverse=True)[:5])
        return results

    def detect(self, image, controls=None, calibrated=()):
        controls = dict(controls or {})
        frame = image.convert("RGB")
        frame.thumbnail((400, 250))
        w, h = frame.size
        samples = tuple(self.samples)
        candidates = self.color_candidates(frame, controls)
        # Confirmed coordinates help recover dim/gray states, but do not claim a visual match by themselves.
        for control in CONTROLS:
            if control in calibrated and control in controls and not any(item["control"] == control for item in candidates):
                cx, cy = controls[control]
                radius = min(w, h)*.045
                best = max((self.circle_score(frame, cx*w, cy*h, radius*scale), radius*scale) for scale in (.8, 1, 1.25))
                if best[0] >= .65:
                    r = best[1]
                    candidates.append({"kind": "control", "control": control,
                        "box": [max(0, cx-r/w), max(0, cy-r/h), min(1, cx+r/w), min(1, cy+r/h)],
                        "center": [cx, cy], "score": best[0]*.85, "source": "calibrated_circle"})
        # Human examples localize controls even when their colors disappear during cooldown.
        for sample in samples:
            if sample["state"] == "rejected":
                continue
            template = Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))
            contrast = max(ImageStat.Stat(template).stddev)
            x0, y0, x1, y1 = sample["box"]
            bw, bh = max(5, round((x1-x0)*w)), max(5, round((y1-y0)*h))
            best = None
            for dx in (-.04, -.02, 0, .02, .04):
                for dy in (-.04, -.02, 0, .02, .04):
                    left, top = round((x0+dx)*w), round((y0+dy)*h)
                    if left < 0 or top < 0 or left+bw > w or top+bh > h:
                        continue
                    crop = frame.crop((left, top, left+bw, top+bh)).resize((16, 16))
                    if max(ImageStat.Stat(crop).stddev) < max(6, contrast*.45):
                        continue
                    score = similarity(template, crop)
                    if score >= .94 and (best is None or score > best["score"]):
                        best = {"kind": "control", "control": sample["control"], "source": "example",
                            "score": score, "box": [left/w, top/h, (left+bw)/w, (top+bh)/h],
                            "center": [(left+bw/2)/w, (top+bh/2)/h]}
            if best:
                candidates.append(best)
        results = []
        for control in CONTROLS:
            matching = [item for item in candidates if item["control"] == control]
            if not matching:
                continue
            for item in sorted(matching, key=lambda candidate: candidate["score"], reverse=True):
                x0, y0, x1, y1 = item["box"]
                crop = frame.crop((round(x0*w), round(y0*h), round(x1*w), round(y1*h)))
                feature = crop.resize((16, 16))
                rejected = any(sample["control"] == control and sample["state"] == "rejected"
                    and math.hypot((sample["box"][0]+sample["box"][2])/2-item["center"][0],
                                   (sample["box"][1]+sample["box"][3])/2-item["center"][1]) <= .1
                    and similarity(feature, Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))) >= .97
                    for sample in samples)
                if not rejected:
                    break
            else:
                continue
            comparisons = [(similarity(feature, Image.frombytes("RGB", (16, 16), bytes(sample["pixels"]))), sample["state"])
                           for sample in samples if sample["control"] == control and sample["state"] != "rejected"]
            state, confidence = self.color_state(crop, control)
            by_state = {}
            for score, label in comparisons:
                if label != "rejected":
                    by_state[label] = max(score, by_state.get(label, 0))
            ranked = sorted(by_state.items(), key=lambda pair: pair[1], reverse=True)
            source = "color"
            if ranked and ranked[0][1] >= .94 and (len(ranked) == 1 or ranked[0][1]-ranked[1][1] >= .025):
                state, confidence, source = ranked[0][0], ranked[0][1], "example"
            item.update(identity=NAMES[control], state=state, state_score=confidence, state_source=source)
            results.append(item)
        return results

    @staticmethod
    def color_state(crop, control):
        if control == "move":
            return "visible", 1.0
        hsv = hsv_pixels(crop.resize((20, 20)))
        count = len(hsv)
        fraction = lambda low, high: sum(low <= hue <= high and sat >= 120 and value >= 150 for hue, sat, value in hsv)/count
        if control == "gadget" and fraction(60, 115) >= .18:
            return "ready", .92
        if control == "hypercharge" and fraction(180, 238) >= .25:
            return "ready", .92
        if control == "super":
            if fraction(28, 55) >= .25:
                return "ready", .92
            if fraction(125, 180) >= .25:
                return "uncharged", .92
        if control == "gadget":
            return "cooldown", .55
        if control in ("super", "hypercharge"):
            return "uncharged", .55
        return "unknown", 0.0
