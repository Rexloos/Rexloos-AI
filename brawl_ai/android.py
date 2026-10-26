"""Manage official Android tools outside the source repository."""
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

NAME = "brawl_ai"
IMAGE = "system-images;android-30;google_apis_playstore;x86_64"


def cache_root():
    return Path(os.environ.get("BRAWL_AI_HOME", str(Path.home() / ".cache/brawl-ai"))).expanduser().resolve()


def environment():
    root = cache_root()
    env = os.environ.copy()
    env.update(ANDROID_HOME=str(root / "sdk"), ANDROID_SDK_ROOT=str(root / "sdk"),
               ANDROID_USER_HOME=str(root / "android"), ANDROID_AVD_HOME=str(root / "avd"))
    return env


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], env=environment(), check=True, **kwargs)


def setup():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Automatic setup currently supports Linux x86_64 only.")
    if not shutil.which("java"):
        raise RuntimeError("Install a JDK (Java 17 or newer) first.")
    root = cache_root()
    for directory in (root / "sdk", root / "android", root / "avd"):
        directory.mkdir(parents=True, exist_ok=True)
    manager = root / "sdk/cmdline-tools/latest/bin/sdkmanager"
    if not manager.exists():
        print("Resolving official command-line tools from Google's SDK repository...", flush=True)
        with urllib.request.urlopen("https://dl.google.com/android/repository/repository2-1.xml", timeout=60) as response:
            tree = ET.fromstring(response.read())
        candidates = []
        for package in tree.findall(".//remotePackage"):
            if package.attrib.get("path", "").startswith("cmdline-tools;"):
                for archive in package.findall("./archives/archive"):
                    if archive.findtext("host-os") == "linux":
                        major = int(package.findtext("./revision/major", "0"))
                        candidates.append((major, archive.find("complete")))
        if not candidates:
            raise RuntimeError("Google's repository did not contain Linux command-line tools.")
        _, archive = max(candidates, key=lambda item: item[0])
        url = archive.findtext("url")
        if not url or "/" in url or not url.endswith(".zip"):
            raise RuntimeError("Unexpected SDK archive URL.")
        checksum = archive.find("checksum")
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            path = Path(temporary) / "tools.zip"
            print("Downloading command-line tools...", flush=True)
            with urllib.request.urlopen("https://dl.google.com/android/repository/" + url, timeout=120) as response, path.open("wb") as output:
                shutil.copyfileobj(response, output)
            algorithm = checksum.attrib.get("type", "sha1")
            if hashlib.file_digest(path.open("rb"), algorithm).hexdigest() != checksum.text.strip():
                raise RuntimeError("SDK download checksum mismatch.")
            with zipfile.ZipFile(path) as bundle:
                for item in bundle.infolist():
                    destination = (Path(temporary) / item.filename).resolve()
                    if not destination.is_relative_to(Path(temporary).resolve()):
                        raise RuntimeError("Unsafe archive path.")
                bundle.extractall(temporary)
                for item in bundle.infolist():
                    if item.external_attr >> 16 & 0o111:
                        (Path(temporary) / item.filename).chmod(0o755)
            manager.parent.parent.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(temporary) / "cmdline-tools"), manager.parent.parent)
    print("Review and accept the Android SDK licenses in this terminal.", flush=True)
    run([manager, "--licenses"])
    run([manager, "platform-tools", "emulator", IMAGE])
    avd = root / "avd" / (NAME + ".ini")
    if not avd.exists():
        run([manager.with_name("avdmanager"), "create", "avd", "-n", NAME,
             "-k", IMAGE, "--device", "pixel_2"], input="no\n", text=True)
    print(f"Android installed in {root}. Run: python -m brawl_ai emulator")


def start():
    executable = cache_root() / "sdk/emulator/emulator"
    if not executable.exists():
        raise RuntimeError("Run 'python -m brawl_ai setup' first.")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("KVM is unavailable. Enable Intel VT-x in firmware and give your user access to /dev/kvm, then retry outside a sandbox.")
    run([executable, "-avd", NAME, "-memory", "2048", "-cores", "2", "-no-snapshot", "-no-boot-anim"])


class Device:
    def __init__(self, serial):
        self.serial = serial
        local = cache_root() / "sdk/platform-tools/adb"
        self.adb = str(local) if local.exists() else shutil.which("adb")
        if not self.adb:
            raise RuntimeError("ADB not found. Run setup first.")

    def command(self, *args):
        return run([self.adb, "-s", self.serial, *args], capture_output=True, timeout=15).stdout

    def screenshot(self):
        from PIL import Image
        return Image.open(io.BytesIO(self.command("exec-out", "screencap", "-p"))).convert("RGB")

    def action(self, action, size, controls):
        width, height = size
        def point(key):
            x, y = controls[key]
            return round(x * width), round(y * height)
        if action == "idle":
            return
        if action == "attack":
            x, y = point("attack")
            self.command("shell", "input", "tap", str(x), str(y))
        else:
            x, y = point("move")
            dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[action]
            radius = round(min(size) * controls["radius"])
            self.command("shell", "input", "swipe", str(x), str(y), str(x + dx * radius), str(y + dy * radius), "180")
