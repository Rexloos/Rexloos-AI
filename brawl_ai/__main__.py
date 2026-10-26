import argparse
import os
import shutil
import sys
import subprocess
from .android import cache_root, setup, start, Device, usb_serial


def main():
    parser = argparse.ArgumentParser(description="Local Android emulator and human-guided Brawl Stars AI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local prerequisites without downloading anything")
    sub.add_parser("setup", help="Download Android tools and create an emulator; review licenses interactively")
    emulator = sub.add_parser("emulator", help="Start the project Android emulator")
    emulator.add_argument("--gpu", choices=("software", "auto", "host"), default="software",
                          help="Graphics backend (default: software for driver compatibility)")
    teach = sub.add_parser("teach", help="Open the teaching and training console")
    target = teach.add_mutually_exclusive_group()
    target.add_argument("--serial", default="emulator-5554")
    target.add_argument("--usb", action="store_true", help="Use one authorized Android phone over USB")
    teach.add_argument("--brawler", default="Shelly", help="Any brawler name")
    teach.add_argument("--mode", default="Training Ground", help="Any game mode name")
    recommendation = sub.add_parser("recommend", help="Suggest what to teach next from saved feedback")
    recommendation.add_argument("--brawler", default="Shelly")
    recommendation.add_argument("--mode", default="Training Ground")
    install = sub.add_parser("install-apk", help="Install a locally supplied APK on the selected device")
    install.add_argument("path")
    install.add_argument("--serial", default="emulator-5554")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            print(f"Local cache: {cache_root()}")
            print(f"Java: {shutil.which('java') or 'MISSING (install Java 17+)'}")
            print(f"KVM accessible: {os.access('/dev/kvm', os.R_OK | os.W_OK)}")
            print(f"Emulator installed: {(cache_root() / 'sdk/emulator/emulator').exists()}")
            print("KVM must be accessible from the terminal running the emulator.")
        elif args.command == "recommend":
            from .profiles import Profile
            from .curriculum import recommend
            root = cache_root() / "training"
            print(recommend(root, Profile(root, args.brawler, args.mode)).text())
        elif args.command == "setup":
            setup()
        elif args.command == "emulator":
            start(gpu=args.gpu)
        elif args.command == "install-apk":
            from pathlib import Path
            path = Path(args.path).resolve(strict=True)
            print(Device(args.serial).command("install", "-r", str(path)).decode())
        else:
            from .ui import Console
            serial = usb_serial() if args.usb else args.serial
            Console(serial, args.brawler, args.mode).run()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
