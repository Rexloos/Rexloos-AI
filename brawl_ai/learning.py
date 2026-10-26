"""Small CPU-only Q learner; no pretrained weights or demonstrations."""
import json
import random
from pathlib import Path

ACTIONS = ("idle", "up", "down", "left", "right", "attack", "super", "gadget", "hypercharge",
           "super_up", "super_down", "super_left", "super_right",
           "gadget_up", "gadget_down", "gadget_left", "gadget_right")


def available_actions(controls, calibrated):
    actions = list(range(6))
    for control in ("super", "gadget", "hypercharge"):
        if control not in calibrated:
            continue
        if control != "hypercharge" and controls.get(control + "_mode", "tap") == "drag":
            actions.extend(i for i, name in enumerate(ACTIONS) if name.startswith(control + "_"))
        else:
            actions.append(ACTIONS.index(control))
    return actions


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
        values = self.table.setdefault(state, [0.0] * len(ACTIONS))
        # Preserve the original six action indices when opening older policies.
        values.extend([0.0] * (len(ACTIONS) - len(values)))
        return values

    def choose(self, state, available=None):
        available = list(range(len(ACTIONS))) if available is None else available
        epsilon = max(0.15, 1.0 - self.steps / 10000)
        if random.random() < epsilon:
            return random.choice(available)
        values = self.values(state)
        best = max(values[i] for i in available)
        return random.choice([i for i in available if values[i] == best])

    def update(self, state, action, reward, next_state, terminal=False, available=None):
        values = self.values(state)
        available = range(len(ACTIONS)) if available is None else available
        target = reward + (0 if terminal else 0.9 * max(self.values(next_state)[i] for i in available))
        values[action] += 0.15 * (target - values[action])
        self.steps += 1

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"table": self.table, "steps": self.steps}))
        temporary.replace(self.path)
