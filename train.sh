#!/usr/bin/env bash
# Prepare the project and open Android plus the teaching console.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v python >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required." >&2
    exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
    python -m venv .venv
fi

source .venv/bin/activate
python -m pip install -e .

# USB mode requires only ADB and bypasses emulator, Java and KVM setup.
if [[ "${1:-}" == "--usb" ]]; then
    shift
    exec python -m brawl_ai teach --usb "$@"
fi

python -m brawl_ai doctor

if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    echo "KVM is unavailable. Enable hardware virtualization and access to /dev/kvm, then rerun this script from a desktop terminal." >&2
    exit 1
fi

if ! command -v java >/dev/null 2>&1; then
    echo "Install Java 17 or newer, then rerun this script." >&2
    exit 1
fi

cache_root=$(python -c 'from brawl_ai.android import cache_root; print(cache_root())')
if [[ ! -f "$cache_root/avd/brawl_ai.ini" || ! -x "$cache_root/sdk/emulator/emulator" ]]; then
    python -m brawl_ai setup
fi

mkdir -p -- "$cache_root"
echo "Starting Android. Emulator output: $cache_root/emulator.log"
python -m brawl_ai emulator >"$cache_root/emulator.log" 2>&1 &
emulator_pid=$!

echo "Wait for Android to boot, install Brawl Stars using Google Play, and open the training area."
read -r -p "Press Enter when the game is ready (Ctrl+C to stop this script)... "

if ! kill -0 "$emulator_pid" 2>/dev/null; then
    echo "The emulator exited. Check $cache_root/emulator.log for details." >&2
    exit 1
fi

# Forward optional profile arguments, e.g. --brawler Colt --mode "Gem Grab".
python -m brawl_ai teach "$@"
echo "Teaching console closed. The emulator remains open; close its window when finished."
