import argparse
import os
import shutil
import sys
import subprocess
from .android import cache_root, setup, start, Device


def main():
    parser = argparse.ArgumentParser(description="Local Android emulator and human-guided Brawl Stars AI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local prerequisites without downloading anything")
    sub.add_parser("setup", help="Download Android tools and create an emulator; review licenses interactively")
    sub.add_parser("emulator", help="Start the project Android emulator")
    teach = sub.add_parser("teach", help="Open the teaching and training console")
    teach.add_argument("--serial", default="emulator-5554")
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
        elif args.command == "setup":
            setup()
        elif args.command == "emulator":
            start()
        elif args.command == "install-apk":
            from pathlib import Path
            path = Path(args.path).resolve(strict=True)
            print(Device(args.serial).command("install", "-r", str(path)).decode())
        else:
            from .ui import Console
            Console(args.serial).run()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
