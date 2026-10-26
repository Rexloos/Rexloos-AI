"""Map names and scene examples taught by the user, without pretrained assets."""
import json
from pathlib import Path

from PIL import ImageStat

from .profiles import key, normalize


def map_state(state, name):
    # JSON encoding keeps arbitrary custom names unambiguous.
    return state if name is None else "map-v1:" + json.dumps(normalize(name).casefold()) + "|" + state


class MapRecognizer:
    def __init__(self, training_root, mode):
        self.path = Path(training_root) / "maps" / key(mode) / "recognizer.json"
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.samples = data.get("samples", [])
        self.saved_names = set(data.get("names", [])) | {sample["name"] for sample in self.samples}

    @property
    def names(self):
        return sorted(self.saved_names, key=str.casefold)

    def canonical_name(self, name):
        try:
            name = normalize(name)
        except ValueError as error:
            raise ValueError("Enter a map name between 1 and 100 characters.") from error
        return next((known for known in self.names if known.casefold() == name.casefold()), name)

    def save(self, samples, names):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 2, "names": sorted(names, key=str.casefold), "samples": samples}))
        temporary.replace(self.path)
        self.samples, self.saved_names = samples, names

    def save_name(self, name):
        name = self.canonical_name(name)
        self.save(self.samples, self.saved_names | {name})
        return name

    def features(self, image):
        w, h = image.size
        # Exclude most HUD and controls. Camera movement still limits matching.
        crop = image.convert("RGB").crop((round(w*.08), round(h*.12), round(w*.92), round(h*.82)))
        return list(crop.resize((16, 9)).tobytes())

    def learn(self, image, name):
        name = self.canonical_name(name)
        pixels = self.features(image)
        if max(ImageStat.Stat(image.convert("RGB")).stddev) < 8:
            raise ValueError("Capture a detailed gameplay scene before teaching a map.")
        # Correcting an identical scene must not leave conflicting labels behind.
        samples = [sample for sample in self.samples if sample["pixels"] != pixels]
        same = [sample for sample in samples if sample["name"].casefold() == name.casefold()][-11:]
        other = [sample for sample in samples if sample["name"].casefold() != name.casefold()]
        updated = other + same + [{"name": name, "pixels": pixels, "aspect": image.width/image.height}]
        self.save(updated, self.saved_names | {name})
        return name

    def predict(self, image):
        if not self.samples:
            return None
        pixels = self.features(image)
        scores = {}
        for sample in self.samples:
            if abs(sample["aspect"] - image.width/image.height) > .1:
                continue
            score = 1 - sum(abs(a-b) for a, b in zip(pixels, sample["pixels"])) / (255*len(pixels))
            scores[sample["name"]] = max(score, scores.get(sample["name"], 0))
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        name, score = ranked[0]
        margin = score - ranked[1][1] if len(ranked) > 1 else score
        return {"name": name, "score": score, "confident": score >= .9 and margin >= .025}
