"""Small CPU-only Q learner; no pretrained weights or demonstrations."""
import json
import random
from pathlib import Path

ACTIONS = ("idle", "up", "down", "left", "right", "attack")


def state_key(image):
    # Coarse visual state keeps a tabular baseline small; it is not object recognition.
    pixels = image.convert("L").resize((4, 3))
    return "".join(str(value // 64) for value in pixels.tobytes())


class Learner:
    def __init__(self, path):
        self.path = Path(path)
        self.table = {}
        self.steps = 0
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.table, self.steps = data["table"], data["steps"]

    def values(self, state):
        return self.table.setdefault(state, [0.0] * len(ACTIONS))

    def choose(self, state):
        epsilon = max(0.15, 1.0 - self.steps / 10000)
        if random.random() < epsilon:
            return random.randrange(len(ACTIONS))
        values = self.values(state)
        best = max(values)
        return random.choice([i for i, value in enumerate(values) if value == best])

    def update(self, state, action, reward, next_state, terminal=False):
        values = self.values(state)
        target = reward + (0 if terminal else 0.9 * max(self.values(next_state)))
        values[action] += 0.15 * (target - values[action])
        self.steps += 1

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"table": self.table, "steps": self.steps}))
        temporary.replace(self.path)
