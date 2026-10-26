# Brawl Stars AI

A Python-managed Android emulator and a small, human-guided reinforcement learning prototype. All code and documentation are in English.

Python downloads and launches the official Android Emulator (QEMU); this project does not implement an Android operating system or CPU emulator in Python. Android images, game installation, training data and learned policies live outside the repository by default.

## Current capabilities

- Install official Android command-line tools, emulator and an Android 11 Google Play x86_64 image.
- Launch a two-core, 2 GB RAM virtual device.
- Capture the game through ADB and calibrate movement and attack controls visually.
- Start a tabular Q policy from zero, explore actions, learn from positive and negative human feedback, and resume saved learning.
- Annotate objects with English labels and normalized bounding boxes for a future detector.

This is a working development baseline, not a trained player. The current visual representation is a coarse 4 × 3 grayscale grid. It does not understand object names or learn a visual detector from annotations yet. It has no automatic health/damage reward extraction, match navigation, automatic resets, aiming model or simultaneous multi-touch controls. Movement uses short swipes and attack uses the game's attack button. Meaningful autonomous gameplay needs those later components; learning from scratch using only live screen interaction can take substantial time.

## Requirements

Linux x86_64, Python 3.11+, Java 17+, Tkinter, internet access for setup, and working KVM virtualization. Pillow is the only Python dependency.

This baseline trains on CPU. The emulator still consumes several GB of disk space locally; keeping Git small does not remove that requirement.

## Start

Run these commands from a normal desktop terminal in the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m brawl_ai doctor
python -m brawl_ai setup
python -m brawl_ai emulator
```

Setup downloads several GB into `~/.cache/brawl-ai`. Android SDK license acceptance is interactive. Downloads are resolved from Google's official repository and command-line tools are checksum-verified. Setup can be rerun after interruption. It does not install system packages or alter kernel configuration.

If `doctor` reports inaccessible KVM, enable hardware virtualization in firmware and configure access to `/dev/kvm` on the host. Restart your login session after changes to group membership. A restricted sandbox may hide KVM even when it works on the host. The launcher fails clearly rather than silently attempting unusably slow software emulation.

Open Google Play inside Android and install Brawl Stars yourself. Game compatibility with the emulator, image, architecture and current game version must be checked on the actual device; it is not guaranteed. A legally obtained local APK can alternatively be installed with:

```bash
python -m brawl_ai install-apk /absolute/path/to/game.apk
```

The APK command handles a single APK, not split bundles, XAPK files or missing game assets. Game binaries are not provided or downloaded by this project.

## Teach and train

Keep the emulator open. In a second terminal with the virtual environment activated:

```bash
python -m brawl_ai teach --serial emulator-5554
```

1. Open the game and enter its training area manually. Use landscape orientation.
2. Click **Capture**. Calibrate **move** at the joystick center and **attack** at the attack button center. Recalibrate after changing resolution or orientation.
3. Click **One AI step**. The policy starts without pretrained weights or demonstrations and initially explores randomly.
4. Click **Reward +1** or **Penalty -1** to evaluate the displayed transition. Feedback applies to the most recently completed action, not a historical action. Multiple clicks add together. With no feedback the reward is zero.
5. The next step commits that feedback and updates the policy. **Start** repeats steps with a 1.5-second feedback window after each captured result. **Pause** stops scheduling new actions; an in-flight action finishes.
6. At a death or episode boundary, pause, wait for the in-flight action, assign the final reward and click **Episode ended**. Reset the game manually, then capture again. This prevents bootstrapping across episode boundaries.
7. To explain what objects are, pause, capture, choose **Label object**, drag a rectangle and enter an English name such as `player`, `enemy`, `wall` or `health_bar`. These annotations are saved for subsequent detector development; they do not currently alter the policy.

Closing the console commits the last completed transition and saves the policy. ADB operations run in a background thread so the interface can respond. Device commands time out after 15 seconds. Capture errors stop continuous stepping.

## Local storage and Git

Default layout:

```text
~/.cache/brawl-ai/
  sdk/                      # Android tools and system images
  avd/                      # Writable emulator disks
  android/                  # Android user configuration
  training/
    controls.json           # Normalized control coordinates and calibration status
    policy.json             # Q values and transition count
    transitions.jsonl       # State/action/reward/next-state/terminal records
    annotations.jsonl       # Image filenames, labels, normalized xyxy boxes
    frame-*.png             # Annotated screenshots
```

Override the location with `BRAWL_AI_HOME=/absolute/path` in every terminal using the tools. Prefer a directory outside the repository. The `.gitignore` also excludes common SDK, image, dataset and model paths. Do not force-add these assets to Git. Screenshots, game data and Android account state remain local. There is no automatic Git push or upload.

The policy is persisted as JSON after each transition. To start learning over, close the console and move `training/policy.json` aside. This preserves annotations and control calibration.

## Validation

```bash
python -m unittest discover -s tests -v
python -m compileall -q brawl_ai
python -m brawl_ai doctor
```

Unit tests cover feedback updates, terminal bootstrapping and checkpoint round trips. Full Android boot, game compatibility, GUI interaction and live learning require an accessible desktop, KVM and ADB. They have not been verified in the restricted development sandbox.

## Android references

- [Command-line tools](https://developer.android.com/tools)
- [SDK Manager](https://developer.android.com/tools/sdkmanager)
- [AVD Manager](https://developer.android.com/tools/avdmanager)
- [Emulator command-line options](https://developer.android.com/studio/run/emulator-commandline)
