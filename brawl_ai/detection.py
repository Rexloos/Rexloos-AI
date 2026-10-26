"""CPU appearance matching learned only from this profile's human annotations."""
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from .profiles import BRAWLERS, normalize
from .hud import hud_state

ENTITY_NAMES = (*BRAWLERS, "NPC", "Showdown box")


def overlap(first, second):
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    area = lambda box: (box[2] - box[0]) * (box[3] - box[1])
    return intersection / max(1e-9, area(first) + area(second) - intersection)


class EnemyDetector:
    """A small template bank, not a pretrained or general-purpose detector."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / "enemy-detector.json"
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.samples = data.get("samples", [])
        self.rejected_boxes = data.get("rejected_boxes", [])
        self.threshold = 0.94
        if not self.path.exists():
            annotations = self.folder / "annotations.jsonl"
            if annotations.exists():
                for line in annotations.read_text().splitlines()[-200:]:
                    try:
                        record = json.loads(line)
                        path = (self.folder / record["image"]).resolve()
                        if not path.is_relative_to(self.folder.resolve()):
                            continue
                        if record["label"].strip().casefold() not in ("enemy", "not_enemy"):
                            continue
                        with Image.open(path) as image:
                            self.learn(image, record["box"], record["label"], record.get("identity"))
                    except (OSError, ValueError, KeyError, TypeError):
                        continue

    @property
    def ready(self):
        return any(sample["label"] == "enemy" for sample in self.samples)

    def learn(self, image, box, label, identity=None):
        label = label.strip().casefold()
        if label not in ("enemy", "not_enemy"):
            return False
        if identity is not None:
            identity = normalize(identity)
            identity = next((name for name in ENTITY_NAMES if name.casefold() == identity.casefold()), identity)
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Annotation must be a normalized nonempty box.")
        width, height = image.size
        crop = image.convert("RGB").crop((round(x0 * width), round(y0 * height),
                                          round(x1 * width), round(y1 * height)))
        if min(crop.size) < 3 or max(ImageStat.Stat(crop).stddev) < 8:
            raise ValueError("Select a detailed character crop, not an empty or flat region.")
        sample = {"label": label, "identity": identity if label == "enemy" else None,
                  "size": [x1 - x0, y1 - y0],
                  "pixels": list(crop.resize((12, 12)).tobytes())}
        # A correction replaces contradictory supervision of the identical crop.
        self.samples = [item for item in self.samples if item["pixels"] != sample["pixels"]]
        # Keep both positive examples and human corrections within a bounded CPU budget.
        category = lambda item: (item["label"], item.get("identity"))
        same = [item for item in self.samples if category(item) == category(sample)][-11:]
        other = [item for item in self.samples if category(item) != category(sample)]
        self.samples = other + same + [sample]
        self.save()
        return True

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 3, "samples": self.samples,
                                         "rejected_boxes": self.rejected_boxes}))
        temporary.replace(self.path)

    def correct_box(self, image, original, kind, corrected=None, identity=None):
        if kind not in ("refined", "too_broad", "wrong"):
            raise ValueError("Unknown box feedback type.")
        if kind != "wrong":
            if corrected is None:
                raise ValueError("Draw a corrected box first.")
            self.learn(image, corrected, "enemy", identity)
            if list(corrected) == list(original):
                return
        width, height = image.size
        x0, y0, x1, y1 = original
        crop = image.convert("RGB").crop((round(x0*width), round(y0*height), round(x1*width), round(y1*height)))
        if min(crop.size) < 1:
            raise ValueError("Rejected box must contain pixels.")
        self.rejected_boxes = self.rejected_boxes[-23:] + [{
            "pixels": list(crop.resize((12, 12)).tobytes()),
            "size": [x1-x0, y1-y0], "kind": kind}]
        self.save()

    def detect(self, image):
        # UI corrections replace these lists; an in-flight scan keeps one consistent view.
        samples = tuple(self.samples)
        rejected_boxes = tuple(self.rejected_boxes)
        if not any(sample["label"] == "enemy" for sample in samples):
            return []
        frame = image.convert("RGB")
        frame.thumbnail((320, 180))
        width, height = frame.size
        positives = [sample for sample in samples if sample["label"] == "enemy"][-12:]
        negatives = [Image.frombytes("RGB", (12, 12), bytes(sample["pixels"]))
                     for sample in samples if sample["label"] == "not_enemy"]
        rejected = [(sample, Image.frombytes("RGB", (12, 12), bytes(sample["pixels"])))
                    for sample in rejected_boxes]
        candidates = []

        def similarity(a, b):
            return 1 - sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / (3 * 255)

        for sample in positives:
            template = Image.frombytes("RGB", (12, 12), bytes(sample["pixels"]))
            for scale in (0.85, 1.0, 1.15):
                w = max(4, round(sample["size"][0] * width * scale))
                h = max(4, round(sample["size"][1] * height * scale))
                if w > width or h > height:
                    continue
                stride = max(2, min(w, h) // 5)
                for y in range(0, height - h + 1, stride):
                    for x in range(0, width - w + 1, stride):
                        patch = frame.crop((x, y, x + w, y + h)).resize((12, 12))
                        score = similarity(template, patch)
                        if score < self.threshold:
                            continue
                        if any(abs(w/width - sample["size"][0]) < 0.015
                               and abs(h/height - sample["size"][1]) < 0.015
                               and similarity(rejected_crop, patch) >= 0.98
                               for sample, rejected_crop in rejected):
                            continue
                        if any(similarity(negative, patch) >= score - 0.02 for negative in negatives):
                            continue
                        candidates.append({"box": [x / width, y / height, (x + w) / width, (y + h) / height],
                                           "score": score})
        selected = []
        for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
            if all(overlap(candidate["box"], old["box"]) < 0.3 for old in selected):
                selected.append(candidate)
                if len(selected) == 10:
                    break
        for item in selected:
            box = item["box"]
            crop = frame.crop((round(box[0]*width), round(box[1]*height),
                               round(box[2]*width), round(box[3]*height))).resize((12, 12))
            named = [(similarity(Image.frombytes("RGB", (12, 12), bytes(sample["pixels"])), crop), sample["identity"])
                     for sample in samples if sample["label"] == "enemy" and sample.get("identity")]
            score, identity = max(named, key=lambda pair: pair[0]) if named else (0.0, "Unknown")
            item.update(identity=identity, identity_score=score)
        return selected


def enemy_state(image, detections, ready):
    from .learning import state_key
    base = state_key(image)
    if not ready:
        return hud_state(base, detections)
    cells = sorted({min(2, int((item["box"][1] + item["box"][3]) * 1.5)) * 4
                    + min(3, int((item["box"][0] + item["box"][2]) * 2)) for item in detections if item.get("kind") != "control"})
    return hud_state(base + "|enemy-v1:" + ",".join(map(str, cells)), detections)
