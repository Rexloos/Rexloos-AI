"""Independent learning storage for any brawler and game mode."""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

# Released roster checked on 2026-10-26: https://brawlify.com/brawlers
# Editable selectors also accept future releases and custom names.
BRAWLERS = (
    "8-Bit", "Alli", "Amber", "Angelo", "Ash", "Barley", "Bea", "Belle",
    "Berry", "Bibi", "Bo", "Bolt", "Bonnie", "Brock", "Bull", "Buster",
    "Buzz", "Byron", "Carl", "Charlie", "Chester", "Chuck", "Clancy",
    "Colette", "Colt", "Cordelius", "Cosmo", "Crow", "Damian", "Darryl",
    "Doug", "Draco", "Dynamike", "Edgar", "El Primo", "Emz", "Eve", "Fang",
    "Finx", "Frank", "Gale", "Gene", "Gigi", "Glowy", "Gray", "Griff",
    "Grom", "Gus", "Hank", "Jacky", "Jae-Yong", "Janet", "Jessie", "Juju",
    "Kaze", "Kenji", "Kit", "Larry & Lawrie", "Leon", "Lily", "Lola", "Lou",
    "Lumi", "Maisie", "Mandy", "Max", "Meeple", "Meg", "Melodie", "Mico",
    "Mina", "Moe", "Mortis", "Mr. P", "Najia", "Nani", "Nita", "Nori",
    "Ollie", "Otis", "Pam", "Pearl", "Penny", "Pierce", "Piper", "Poco",
    "R-T", "Rico", "Rosa", "Ruffs", "Sam", "Sandy", "Shade", "Shelly",
    "Sirius", "Spike", "Sprout", "Squeak", "Starr Nova", "Stu", "Surge",
    "Tara", "Tick", "Trunk", "Vince", "Wendy", "Willow", "Ziggy",
)
MODES = (
    "Training Ground", "Solo Showdown", "Duo Showdown", "Trio Showdown",
    "Brawl Ball", "Gem Grab", "Knockout", "Heist", "Hot Zone",
    "Brawl Hockey", "Basket Brawl", "Wipeout", "Knockout 5v5",
    "Brawl Ball 5v5", "Wipeout 5v5", "Duels", "Bounty", "Brawl Arena",
)


def normalize(name):
    name = " ".join(name.split())
    if not name or len(name) > 100:
        raise ValueError("Enter a brawler and mode name between 1 and 100 characters.")
    return name


def key(name):
    normalized = normalize(name).casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:40] or "custom"
    return slug + "-" + hashlib.sha256(normalized.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Profile:
    root: Path
    brawler: str
    mode: str

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "brawler", normalize(self.brawler))
        object.__setattr__(self, "mode", normalize(self.mode))

    @property
    def folder(self):
        return self.root / "profiles" / key(self.brawler) / key(self.mode)

    def prepare(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        metadata = self.folder / "profile.json"
        metadata.write_text(json.dumps(self.record(), indent=2))
        return self.folder

    def record(self):
        return {"brawler": self.brawler, "mode": self.mode}

    def gadget_mode(self):
        path = self.folder.parent / "gadget.json"
        if not path.exists():
            return None
        mode = json.loads(path.read_text()).get("mode")
        return mode if mode in ("tap", "drag") else None

    def save_gadget_mode(self, mode):
        if mode not in ("tap", "drag"):
            raise ValueError("Gadget input must be tap or drag.")
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        path = self.folder.parent / "gadget.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": mode}, indent=2))
        temporary.replace(path)


def choices(root):
    brawlers, modes = set(BRAWLERS), set(MODES)
    for path in (Path(root) / "profiles").glob("*/*/profile.json"):
        try:
            data = json.loads(path.read_text())
            brawlers.add(normalize(data["brawler"]))
            modes.add(normalize(data["mode"]))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(brawlers), sorted(modes)
