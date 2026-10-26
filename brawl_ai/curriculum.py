"""Transparent training suggestions based on saved human feedback."""
from collections import deque
from dataclasses import dataclass
import json

from .profiles import Profile


@dataclass(frozen=True)
class Recommendation:
    profile: Profile
    task: str
    reason: str

    def text(self):
        return f"Next: {self.profile.brawler} / {self.profile.mode}\n{self.task}\nWhy: {self.reason}"


def evidence(profile):
    """Zero reward means unknown feedback, not a successful action."""
    reviewed = 0
    recent = deque(maxlen=50)
    path = profile.folder / "transitions.jsonl"
    if path.exists():
        with path.open() as stream:
            for line in stream:
                try:
                    reward = float(json.loads(line)["reward"])
                except (ValueError, KeyError, TypeError):
                    continue
                if reward > 0 or reward < 0:
                    reviewed += 1
                    recent.append(reward > 0)
    return reviewed, sum(recent) / len(recent) if recent else None


def objective(mode):
    mode = mode.casefold()
    if mode == "training ground":
        return "Practice movement and attacking stationary targets. Reward useful movement or hits; penalize wasted attacks."
    if "showdown" in mode or mode.startswith(("knockout", "wipeout")) or mode in ("bounty", "duels"):
        return "Practice staying alive and useful attacks. Reward safe positioning and hits; penalize avoidable danger."
    if mode == "gem grab":
        return "Practice approaching gems safely and retreating when carrying them. Reward useful positioning; penalize reckless advances."
    if mode.startswith("brawl ball"):
        return "Practice moving toward the ball and supporting the goal. Reward useful positioning; penalize moving away from play."
    if mode == "heist":
        return "Practice approaching the enemy safe and attacking it. Reward progress and useful attacks."
    if mode == "hot zone":
        return "Practice entering and staying in the zone safely. Reward useful zone positioning."
    if mode == "brawl hockey":
        return "Practice positioning near the puck and protecting your goal. Reward useful positioning and safe advances."
    if mode == "basket brawl":
        return "Practice approaching the ball and finding space near the basket. Reward useful positioning and support."
    if mode == "brawl arena":
        return "Practice safe positioning and useful attacks. Pick one visible arena objective per session and reward progress toward it."
    return "Choose one observable objective for this mode. Reward actions toward it and penalize clear mistakes."


def recommend(root, active):
    # Use only the active brawler and profiles the user has actually created.
    profiles = {active.folder: active}
    for path in (root / "profiles").glob("*/*/profile.json"):
        try:
            record = json.loads(path.read_text())
            profile = Profile(root, record["brawler"], record["mode"])
            profiles[profile.folder] = profile
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    basics = Profile(root, active.brawler, "Training Ground")
    count, _ = evidence(basics)
    if count < 20:
        return Recommendation(basics, objective(basics.mode),
                              f"Start with basic controls for this brawler: {count}/20 actions with nonzero feedback. Give 20 reviewed actions before moving on.")
    stats = [(profile, *evidence(profile)) for profile in profiles.values()]
    # Continue the chosen mode before rotating through other saved combinations.
    count, rate = evidence(active)
    if count < 30:
        return Recommendation(active, objective(active.mode),
                              f"This profile has {count}/30 reviewed actions. Collect more feedback; unattended zero-reward steps do not count.")
    weak = [(profile, count, rate) for profile, count, rate in stats
            if count >= 30 and rate is not None and rate < 0.6]
    if weak:
        profile, count, rate = min(weak, key=lambda item: (item[2], item[1], str(item[0].folder)))
        return Recommendation(profile, objective(profile.mode),
                              f"Only {rate:.0%} of its last up to 50 rated actions were positive. Repeat this exercise and give consistent feedback.")
    profile, count, rate = min(stats, key=lambda item: (item[1], str(item[0].folder)))
    return Recommendation(profile, objective(profile.mode),
                          f"This is a least-reviewed saved profile ({count} rated actions). Practice it to balance coverage. Counts and reward signs are a heuristic, not proof of skill.")
