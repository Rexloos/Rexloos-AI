# Brawl Stars AI

A Python-managed Android emulator and a small, human-guided reinforcement learning prototype. All code and documentation are in English.

Python downloads and launches the official Android Emulator (QEMU); this project does not implement an Android operating system or CPU emulator in Python. Android images, game installation, training data and learned policies live outside the repository by default.

## Current capabilities

- Install official Android command-line tools, emulator and an Android 11 Google Play x86_64 image.
- Launch a two-core, 2 GB RAM virtual device with a 720 × 1280 display and software graphics.
- Capture the game through ADB and calibrate movement and attack controls visually.
- Start a tabular Q policy from zero, explore actions, learn from positive and negative human feedback, and resume saved learning.
- Learn enemy appearance examples and false-detection corrections from human bounding boxes, then display matches and include their locations in policy states.

This is a working development baseline, not a trained player. The visual representation combines a coarse 4 × 3 grayscale grid with enemy locations once the profile has enemy examples. Enemy recognition uses a small appearance template bank learned from human annotations, without pretrained weights. Other object labels are stored but do not train recognition. It has no automatic health/damage reward extraction, match navigation, automatic resets, targeting model or simultaneous multi-touch controls. Movement uses short swipes and attack uses the game's attack button. Meaningful autonomous gameplay needs those later components; learning from scratch using only live screen interaction can take substantial time.

## Requirements

Linux x86_64, Python 3.11+, Java 17+, Tkinter, internet access for setup, and working KVM virtualization. Pillow is the only Python dependency.

This baseline trains on CPU. The emulator still consumes several GB of disk space locally; keeping Git small does not remove that requirement.

## Start

To prepare the environment and launch both the emulator and teaching console:

```bash
bash train.sh
```

Choose an initial brawler and mode with optional arguments:

```bash
bash train.sh --brawler "Colt" --mode "Gem Grab"
```

The script installs Android on the first run, waits for you to open the game's training area, and then opens the teaching console. Run it from a desktop terminal with no other Android emulator running. Close the emulator window separately when finished. Subsequent runs reuse the virtual environment and Android installation. Android SDK licenses still require interactive acceptance.

Alternatively, run the commands individually:

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

If Linux reports that the emulator window is not responding, close that window before restarting. The launcher now uses software graphics to avoid host GPU driver problems and starts ADB before Android. CPU virtualization still uses KVM. The smaller display reduces rendering work; recalibrate game controls after this resolution change. Existing Android data and training profiles are preserved. This configuration is a troubleshooting measure, not a verified fix for every freeze.

Software graphics can be slower. To compare hardware rendering, close the emulator and run `python -m brawl_ai emulator --gpu host` (or `--gpu auto`). See Android's [graphics acceleration documentation](https://developer.android.com/studio/run/emulator-acceleration). Logs from `train.sh` are stored at `~/.cache/brawl-ai/emulator.log` (or under `BRAWL_AI_HOME`).

Open Google Play inside Android and install Brawl Stars yourself. Game compatibility with the emulator, image, architecture and current game version must be checked on the actual device; it is not guaranteed. A legally obtained local APK can alternatively be installed with:

```bash
python -m brawl_ai install-apk /absolute/path/to/game.apk
```

The APK command handles a single APK, not split bundles, XAPK files or missing game assets. Game binaries are not provided or downloaded by this project.

## Teach and train

### Physical Android phone over USB

Instead of the emulator, connect an Android phone using a USB data cable. Enable Developer options and USB debugging, unlock the phone, and accept the computer's debugging authorization on the phone. See the official [USB device setup](https://developer.android.com/studio/run/device) instructions. ADB must be installed (the project's cached platform-tools or `adb` on PATH); Java, KVM and an Android system image are not needed for this mode. iPhones are not supported.

Open Brawl Stars on the phone in landscape orientation and enter the training area, then run:

```bash
bash train.sh --usb
# Or, with the virtual environment already prepared:
python -m brawl_ai teach --usb --brawler "Shelly" --mode "Training Ground"
```

Place `--usb` first when using `train.sh`. Connect exactly one authorized USB phone; selection fails if none or multiple are available. An emulator is never selected by USB mode. You can alternatively target a known device using `python -m brawl_ai teach --serial DEVICE_SERIAL`.

The console captures screenshots and sends taps/swipes through ADB; it is not a continuous video mirror. Click **Capture** and recalibrate movement and attack for the phone's screen before stepping. Keep the phone unlocked with the game in the foreground. Existing profile learning is reused. If authorization fails, check the prompt on the phone; if Linux reports insufficient permissions, configure the distribution's Android USB udev rules. Physical-device gameplay and input compatibility still require manual verification on the connected phone.

Keep the emulator open. In a second terminal with the virtual environment activated:

```bash
python -m brawl_ai teach --serial emulator-5554
```

### Map names and recognition

After entering the match, pause and **Capture** a gameplay frame. Type its name into **Map** and click **Set / teach map**. You can enter any map name; previously taught names appear in the dropdown. This confirms the active map and saves a labeled scene example. Teach the same name from several different viewpoints, positions and matches, rather than from menus or loading screens.

Subsequent captures and AI steps display the closest learned map and its similarity score. **Confirm map suggestion** accepts that proposal and teaches the current scene; if it is wrong, enter the correct name and use **Set / teach map**. Suggestions never automatically change the active map or train themselves. Low similarity or a small gap between competing maps is displayed as an uncertain match. An empty model makes no proposal. **Clear active map** clears the current assignment while keeping learned examples.

Confirmed map names are included in policy state keys and new transition/annotation records. Brawler/mode policies stay independent, with separate Q values for each named map inside the policy. Previously learned unnamed states remain untouched. Changing the active map pauses stepping and commits the pending transition to the previous map as an episode boundary. The active name is cleared at episode end, profile switch and startup to avoid silently carrying a map into another match. Confirm or enter it again after capturing the next match. Editing the text field alone does not change the active map.

Map recognition starts without pretrained weights and stores up to 12 scene descriptors per name. It is shared across brawlers within the same game mode, in `training/maps/<mode-key>/recognizer.json`; different modes keep separate map examples. Confirmed screenshots and `map-examples.jsonl` stay in the active brawler/mode profile. Case and repeated whitespace do not create duplicate map names, and custom names are supported. The current rotation and match changes are not fetched or detected automatically.

Recognition compares coarse RGB scene appearance after excluding most screen edges and controls. Camera motion, destroyed obstacles, effects and visually similar maps can confuse it. A score is a visual similarity, not a probability or proof of mastery. This is a basic human-taught recognizer, not a guarantee of eventual recognition of every map. Synthetic automated tests cover matching, ambiguous scenes, persistence, mode isolation and safe context switching; map recognition has not been manually verified in the game.

### Training recommendations and profiles

The console automatically recommends a **brawler, mode and exercise**, with an explanation. Recommendations refresh at startup, after switching profiles, every 20 committed transitions and at episode end. **Refresh recommendation** updates it on demand; **Load recommended profile** opens the suggested profile using the normal safe switching rules. You still select the matching brawler and mode manually in the game.

The recommendation starts with 20 nonzero-feedback actions in the active brawler's Training Ground, then asks for 30 rated actions in the selected profile. After this initial coverage, it prioritizes saved profiles with less than 60% positive feedback among their last 50 rated actions, otherwise a least-reviewed saved profile. Only the active brawler's basics and profiles you have created are considered. Zero-reward steps do not count as reviewed. These thresholds organize practice; they do not establish mastery or measure win rate. Object annotations are not treated as evidence of learned recognition.

You can also inspect the next suggestion without opening Android or the GUI:

```bash
python -m brawl_ai recommend --brawler "Colt" --mode "Gem Grab"
```

Each brawler/mode combination has an independent policy, exploration progress, reward history and annotation dataset. New combinations start from zero; switching back resumes their saved learning. The default is `Shelly` / `Training Ground`.

Use the editable **Brawler** and **Mode** selectors and click **Load / create profile**. The brawler menu includes all 108 released brawlers, checked against [Brawlify's roster](https://brawlify.com/brawlers) on October 26, 2026, plus previously saved custom names. The mode menu contains example modes and previously saved names. You can type any brawler or mode, including future releases and temporary events. Names are case-insensitive and repeated spaces are normalized. The active profile is displayed separately from the editable fields.

Switching pauses training and commits any pending reward to the old profile as an episode boundary. It clears the old frame and requires a new capture. Wait for an in-flight device operation to complete before switching. Control calibration is shared between profiles. Select the matching brawler and mode in the actual game yourself; the profile selector does not navigate the game or detect your selection.

The mode menu includes Solo Showdown, Duo Showdown, Trio Showdown, Brawl Ball, Gem Grab, Knockout, Heist, Hot Zone, Brawl Hockey, Basket Brawl, Wipeout, Knockout 5v5, Brawl Ball 5v5, Wipeout 5v5, Duels, Bounty and Brawl Arena, plus Training Ground for basic practice. Each variant has an independent policy.

The learning algorithm and action set remain the same for all profiles. Recommendations provide suggested exercises for these modes, but rewards still come from your feedback. Mode selection does not add automatic objective detection, ball/puck interactions or new controls. Enemy appearance examples are learned independently for each profile; other object labels remain saved annotations.

To select a profile directly without the launcher:

```bash
python -m brawl_ai teach --brawler "Shelly" --mode "Solo Showdown"
```

1. Open the game and enter its training area manually. Use landscape orientation.
2. Click **Capture**. Calibrate **move** at the joystick center and **attack** at the attack button center. Recalibrate after changing resolution or orientation.
3. Click **One AI step**. This pauses continuous training, exits unfinished ordinary labeling/calibration and executes exactly one action. A box refinement must be finished or cancelled first. The policy starts without pretrained weights or demonstrations and initially explores randomly. The status line shows the selected action while it is running and confirms completion; `idle` intentionally makes no movement. Missing calibration and busy operations produce an explicit message instead of silently ignoring the click.
4. Click **Reward +1** or **Penalty -1** to evaluate the displayed transition. Feedback applies to the most recently completed action, not a historical action. Multiple clicks add together. With no feedback the reward is zero.
5. The next step commits that feedback and updates the policy. **Start** repeats steps with a 1.5-second feedback window after each captured result. **Pause** stops scheduling new actions; an in-flight action finishes.
6. At a death or episode boundary, pause, wait for the in-flight action, assign the final reward and click **Episode ended**. Reset the game manually, then capture again. This prevents bootstrapping across episode boundaries.
7. To explain what objects are, pause, capture, choose **Label object**, drag a rectangle and enter an English name such as `player`, `enemy`, `wall` or `health_bar`. The `enemy` and `not_enemy` labels train the appearance matcher immediately; other labels are saved for future recognition work. Dedicated enemy labeling buttons avoid typing these two labels.

Closing the console commits the last completed transition and saves the policy. ADB operations run in a background thread so the interface can respond. Device commands time out after 15 seconds. Capture errors stop continuous stepping.

### Automatic preview capture

Enable **Auto capture** to refresh screenshots and enemy/map recognition without sending game actions. Set **Interval / minimum (seconds)** from `0.2` to `60` and click **Apply capture settings**. With **Adaptive** unchecked, this is the target time between capture starts. If processing takes longer, captures wait for completion and leave at least 0.1 seconds before the next one; frames never build up in a queue.

With **Adaptive** checked, the interval is a minimum. The scheduler targets the greater of that minimum and twice the smoothed capture/processing time, leaving room for interaction instead of continuously running detection. For example, a 1-second minimum and 2-second processing time give an initial 4-second target interval. The separate capture status shows the target and latest processing duration. This is a timing heuristic, not CPU/GPU utilization measurement or a proven optimal frame rate. Start with the default 2 seconds and Adaptive enabled.

Preview capture waits during device operations, calibration, labeling, box refinements and identity review dialogs. During continuous AI training it waits because each AI step already captures its result. It resumes after the blocking activity ends; **Pause** stops AI stepping but does not disable preview capture. Uncheck **Auto capture** to stop preview updates. Errors, profile switches, episode end and closing the console disable it. An already running capture can still finish after disabling. Interval and adaptive preferences persist in `training/capture-settings.json`, but automatic capture starts off each time the console opens.

Preview refreshes do not perform AI actions, change pending transition states, assign rewards, train on predictions, or save every frame to disk. If you refresh while a transition awaits feedback, the reward still applies to the last completed AI action and its original captured result. Disable auto capture while assessing that exact result if you want it to stay visible. Labeled examples continue to save normally. Automated tests cover scheduling, adaptation, cancellation, interaction blocking and reward/transition preservation; live automatic capture has not been verified on a phone.

### Super, gadget, hypercharge and reward sizes

Pause, capture a frame, then use **Calibrate super**, **Calibrate gadget** or **Calibrate hypercharge** and click the corresponding button center in the preview. You can teach these controls gradually: an optional ability is excluded from AI choices until calibrated. Set **Super input** and **Gadget input** to `tap` for button activation/auto-aim, or `drag` for directional aiming. Drag mode exposes four separate actions (up, down, left, right), using a swipe of 10% of the smaller screen dimension. Hypercharge uses a tap. This does not detect charge, remaining gadget uses or ability availability in the game; feedback must teach when an action is useful. Live ability behavior has not yet been verified on a physical device.

Calibration and super input mode are shared across profiles; recalibrate when the layout changes. Gadget input is saved separately for each brawler, shared across that brawler's game modes. On first opening a brawler without a saved gadget setting, the console asks whether the equipped gadget requires dragging. Answer Yes for drag or No for tap. Change this later using **Gadget input**, including when equipping a different gadget. Previously shared gadget settings are not assumed correct for a new brawler. Existing policies retain their learned values for the original actions; newly added actions start at zero.

Enter a number under **Custom reward** and click **Add reward** after an AI step, for example `0.2` for a small improvement, `10` for completing an exercise, or `-5` for a mistake. Each click adds to the visible pending reward, including the existing +1/-1 buttons. **Clear pending reward** resets that sum before it is committed. Feedback applies to the most recently completed action, and is committed on the next step or episode end. Pause for more time to assess and reward an action. Larger magnitudes produce larger Q-value updates; recommendations still count positive/negative feedback rather than interpreting magnitude as mastery.

### Teaching enemy recognition

1. Pause and capture a frame containing an enemy.
2. Click **Label enemy** (it captures automatically if no frame is loaded). The cursor becomes a crosshair. In the captured preview on the computer, hold the left mouse button, drag a tight box around the enemy character and release. Select its brawler name or **NPC** in the identity dialog and save. Include different appearances and animation poses across multiple captures. **Cancel selection (Esc)** exits labeling or calibration without adding an example; simply clicking without dragging does not create a usable box.
3. Detection refreshes on that frame and on subsequent captures and AI steps. Red boxes mark appearance matches. Beside each match, the console proposes the closest learned identity with a question mark and a similarity score. This score is not a calibrated probability. Without named examples it displays **Unknown**.
4. Click a detected box to pause and review it. Use **Confirm suggestion**, or select/type the actual identity and click **Save correction / new example**. **Cancel** leaves training data unchanged. The list includes all 108 brawlers, verified against [Brawlify](https://brawlify.com/brawlers) on October 26, 2026, plus **NPC** and previously taught custom names. Listing a category does not supply pretrained appearance knowledge: names are learned only from your confirmed examples.

Review opens from the displayed frame even while a background preview capture or detection is running. The displayed frame is frozen immediately; results from that older preview are discarded, so they cannot move the box during confirmation or refinement. Corrections save immediately, and their visual refresh waits for an in-flight worker rather than delaying the dialog. A real AI action must still finish before reviewing its result; the status explains this wait. Each scan uses a stable snapshot of detector examples so concurrent human corrections cannot change its interpretation halfway through.

5. If a box marks an ally, scenery or a control, use **Not an enemy** in the review dialog, or click **Label false detection** and draw that region. Draw around the incorrect region, not around a real enemy elsewhere. NPC is a character category, not a label for background or allies.

Each detection is outlined by a rectangular quadrilateral. Click inside it to review both identity and box placement:

- **Refine box**: redraw a more accurate rectangle around the character, then confirm/select its identity.
- **Box too broad**: redraw a tight rectangle excluding the unwanted surrounding area, then confirm/select the character. This trains the corrected crop as an enemy and rejects the original box geometry; it does not label the real character as background.
- **Box completely wrong**: reject the proposed box immediately without supplying a replacement or a character identity.

While redrawing, the captured image stays fixed and AI stepping is blocked. **Cancel selection (Esc)** discards an unfinished correction. Tiny rectangles must be redrawn. Box feedback is saved immediately to the active profile's `box-feedback.jsonl` and detector file. The model keeps the latest 24 rejected box appearances/sizes and suppresses very similar proposals of approximately the same size. This is limited appearance-based feedback, not a trained general bounding-box regression model; it may still propose other incorrect boxes or miss a character in a new pose. Corrected examples are also saved as annotations. These controls have automated tests but have not been manually verified in the actual game.

If an operation fails, the status line shows the error. Unexpected console callback errors also print a traceback in the launching terminal and stop continuous training; the worker-result queue continues polling so a later capture can recover. After updating the code, close and reopen the console with `bash train.sh --usb` to load the changes. Automated callback tests cover labeling, cancellation, single-step input dispatch and callback failure recovery using mocked widgets and ADB; they do not establish live phone input compatibility.

The detector starts empty and stores at most 12 examples per identity, 12 unnamed enemy examples and 12 negative examples per brawler/mode profile. Localization scans the latest 12 positive templates to bound CPU work; identity proposals compare each localized crop against all named examples. Older identities remain stored, but a sufficiently different appearance may need a fresh manually drawn box. A correction replaces contradictory supervision for an identical crop. Unconfirmed predictions never train the detector automatically. Previously saved `enemy`/`not_enemy` annotations are imported automatically when no detector file exists (the last 200 annotation records are considered); old unnamed examples remain usable without invented identities. Examples and the detector model are saved immediately in the local cache, independently of other profiles. Recognition runs in the background with ADB capture, on images reduced to at most 320 × 180, at three scales and a fixed similarity threshold of 0.94. It is appearance matching, not a neural network or a semantic understanding of enemies. Different skins, poses, backgrounds, scale changes and teammates with similar appearances may cause misses or false detections. No game accuracy claim has been established.

After the first enemy example, policy state keys include occupied enemy cells in a 4 × 3 grid. Existing Q values remain stored, but these enriched states start with zero values until visited and rewarded. This supplies enemy location to learning; it does not automatically aim attacks at detected enemies. Changing detector examples commits the pending transition as an episode boundary before changing visual interpretation. Rewards train action selection; boxes train appearance matching. A small, flat-color crop is rejected as an uninformative example.

## Local storage and Git

Default layout:

```text
~/.cache/brawl-ai/
  sdk/                      # Android tools and system images
  avd/                      # Writable emulator disks
  android/                  # Android user configuration
  training/
    controls.json           # Normalized control coordinates and calibration status
    capture-settings.json   # Preview interval and adaptive timing preference
    maps/<mode-key>/
      recognizer.json       # Confirmed map names and scene descriptors
    profiles/
      <brawler-key>/<mode-key>/
        profile.json        # Original brawler and mode names
        policy.json         # Q values and transition count for this combination
        transitions.jsonl   # Transitions, rewards and profile names
        annotations.jsonl   # Image filenames, labels, normalized xyxy boxes
        enemy-detector.json # Human-taught appearance templates and corrections
        box-feedback.jsonl  # Original/corrected rectangles and rejection reasons
        map-examples.jsonl  # Confirmed map names and screenshot filenames
        map-frame-*.png     # Human-confirmed map examples
        frame-*.png         # Annotated screenshots for this combination
```

Override the location with `BRAWL_AI_HOME=/absolute/path` in every terminal using the tools. Prefer a directory outside the repository. The `.gitignore` also excludes common SDK, image, dataset and model paths. Do not force-add these assets to Git. Screenshots, game data and Android account state remain local. There is no automatic Git push or upload.

The policy is persisted as JSON after each transition. To start one profile over, close the console and move its `policy.json` aside. This preserves annotations, other profiles and control calibration. Models and datasets from the earlier unprofiled version in `training/` remain untouched and are not assigned to any brawler automatically.

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
