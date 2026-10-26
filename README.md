# Brawl Stars AI

A Python-managed Android emulator and a small, human-guided reinforcement learning prototype. All code and documentation are in English.

Python downloads and launches the official Android Emulator (QEMU); this project does not implement an Android operating system or CPU emulator in Python. Android images, game installation, training data and learned policies live outside the repository by default.

## Current capabilities

- Install official Android command-line tools, emulator and an Android 11 Google Play x86_64 image.
- Launch a two-core, 2 GB RAM virtual device with a 720 × 1280 display and software graphics.
- Capture the game through ADB, propose control locations automatically, and confirm/correct calibration visually.
- Start a tabular Q policy from zero, explore actions, learn from positive and negative human feedback, and resume saved learning.
- Learn enemy appearance examples and false-detection corrections from human bounding boxes, then display matches and include their locations in policy states.
- Observe the player's own USB phone gestures, save demonstrations, and optionally mix learned action preferences into AI decisions.

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

Map names are also saved independently of scene examples. Type a name and click **Save map name** to add it without taking a screenshot or changing the active map. **Set / teach map** saves the name automatically as well. Saved names reappear in the **Map** dropdown after restarting and are shared across brawlers in the same game mode. Correcting the label of a scene does not remove its old name from the saved list. Case and repeated spaces do not create duplicates. The name list is stored alongside recognition examples in `training/maps/<mode-key>/recognizer.json`; existing recognition files remain compatible. Saving a name alone does not teach its appearance or activate it for gameplay.

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

The console uses a resizable workspace: the game preview stays on the left and scrollable **Train**, **Teach** and **Setup** tabs stay on the right. **Capture** is always above the preview; the current instruction and activity summary remain in the footer. Drag the divider to adjust the space allocated to the preview and controls. The preview scales to fit while preserving its aspect ratio. Resizing is deferred during a box selection or review so annotation coordinates remain stable.

- **Setup:** brawler/mode profile, control-center calibration, tap/drag settings and automatic capture. It opens first when movement/attack have not been calibrated.
- **Train:** single-step/continuous AI, rewards, playstyle recording and training recommendations.
- **Teach:** player/enemy/object labels, control availability examples and map identity. Click existing boxes in the preview to confirm or correct them.
- **Activity log:** always accessible in the header; opens the timestamped history.

Automated desktop smoke checks exercised all three tabs, preview rendering, resizing from 1320 × 900 to 950 × 650 and opening the activity viewer with a temporary profile and synthetic image. The full automated test suite also passes. Actual-game interaction with the redesigned workspace has not been manually verified.

1. Open the game and enter its training area manually. Use landscape orientation.
2. Click **Capture**. Calibrate **move** at the joystick center and **attack** at the attack button center. Recalibrate after changing resolution or orientation.
3. Click **One AI step**. This pauses continuous training, exits unfinished ordinary labeling/calibration and executes exactly one action. A box refinement must be finished or cancelled first. The policy starts without pretrained weights or demonstrations and initially explores randomly. The status line shows the selected action while it is running and confirms completion; `idle` intentionally makes no movement. Missing calibration and busy operations produce an explicit message instead of silently ignoring the click.
4. Click **Reward +1** or **Penalty -1** to evaluate the displayed transition. Feedback applies to the most recently completed action, not a historical action. Multiple clicks add together. With no feedback the reward is zero.
5. The next step commits that feedback and updates the policy. **Start AI** repeats steps with a 1.5-second feedback window after each captured result. **Pause AI** stops scheduling new actions; an in-flight action finishes.
6. At a death or episode boundary, pause, wait for the in-flight action, assign the final reward and click **Episode ended**. Reset the game manually, then capture again. This prevents bootstrapping across episode boundaries.
7. To explain what objects are, pause, capture, choose **Label object**, drag a rectangle and enter an English name such as `enemy`, `wall` or `health_bar`. The `enemy` and `not_enemy` labels train the appearance matcher immediately; other labels are saved for future recognition work. Use **Label player** for your controlled character and **Label enemy** for other entities.

Closing the console commits the last completed transition and saves the policy. ADB operations run in a background thread so the interface can respond. Device commands time out after 15 seconds. Capture errors stop continuous stepping.

### Recognize your controlled character

Captures and analyzed replays automatically propose a lime **Player** box around your own character. Recognition starts without examples or pretrained weights: it combines a green health bar and its outline with a green name or foot ring. It does not assume that the player is at the center. The brawler name comes from the selected profile, not visual brawler identification or name OCR.

Click the lime box to confirm it, redraw it, or reject it as **Not my player**. **Label player** lets you draw your own box when no proposal appears. Corrections teach body geometry relative to the health marker and save exact-frame confirmations and rejected appearances. Examples stay separate for each brawler/mode profile; existing `player`/`self` object annotations are imported when creating the detector.

Your detected position enters the learning state, and overlapping entity proposals are removed so the same body is not counted as an enemy. Missing or ambiguous markers leave the player unknown; they do not imply death. Occlusion, very low health, crowded scenes and different marker colors can prevent recognition. This feature does not read numeric HP. Raw game recording remains free of detection work; recognition runs during capture and post-game analysis.

Verification: automated tests cover ownership markers, ambiguity, partial health, corrections, persistence, profile separation, learning states and overlays. A lime box was also inspected on a saved real gameplay screenshot. Live gameplay and the interactive correction dialog have not been manually verified.

### Learn a player's playstyle from the USB phone

This optional mode learns from demonstrations you record yourself. New models still start with no bundled demonstrations or pretrained weights. It does not require a downloaded player model.

1. Connect the Android phone over USB, select the brawler/mode and map in the main console, and calibrate movement/attack once for the phone's layout.
2. Open **Learn player playstyle...** and click **Start recording**. A separate **Brawl AI - Live recording** window shows the actual recorded phone screen continuously. The learning window shows elapsed time and file size while you play on the phone.
3. Click **Stop recording / cancel analysis** after the match. A list shows separate progress bars for saving video, saving phone input, reading video, interpreting fingers, learning actions, rendering character proposals/replay, and saving the model. Unknown-duration operations show an animated bar; known counts show percentages. Completed steps stay visible while later steps run. Once saved, analysis starts automatically. Closing the live video window normally also ends recording and starts this sequence.
4. After analysis and replay preparation succeed, the model is saved, **Use learned playstyle for AI decisions** is enabled, and the annotated replay player opens automatically. It supports play/pause and timeline seeking. Each finger has its own color, location marker, recent motion trail, origin ring, drag arrow and interpreted action label. Brawler/NPC proposals appear as boxes and names with question marks. **Advanced settings / saved games → Watch analyzed game** reopens it later. A successfully analyzed recording cannot be counted twice. Cancellation keeps the raw game and previous learning.

Video is encoded on the phone and saved continuously as `game.mkv` using [scrcpy recording](https://github.com/Genymobile/scrcpy/blob/master/doc/recording.md), with live playback but no audio or device control. Timestamped input is written alongside it. No enemy recognition, state extraction or demonstration training runs during recording. The actual frame rate depends on the phone and game; the default 30 FPS setting is a limit, not a constant-frame-rate guarantee. The recording is not subject to Android screenrecord's three-minute limit. Keep the phone in the same orientation for the entire game.

**Advanced settings / saved games** is collapsed by default. It contains player selection/loading, the FPS limit, optional input preview, rotation override, timing adjustment, previously saved recordings and **Analyze selected saved game** for resuming a cancelled analysis later. Touch rotation is detected from Android's input display orientation when available, otherwise the saved/manual value is used. Disable automatic rotation and use the optional cyan touch-marker preview if that mapping is wrong. Input preview gap controls only this calibration preview, not recording cadence.

Timing is estimated automatically from device/host input timestamps and the video recorder's start log. This is not guaranteed to be frame-exact. If results look misaligned, advanced **Preview recorded action** and **Video/input offset** let you inspect and adjust timing before manually analyzing an unlearned saved recording. Positive offsets select a later point in the video for the gesture. Previously applied recordings remain protected against double counting. Raw video remains available until learning is successfully saved; afterward use the annotated replay.

### Activity log

Activity entries also print immediately to standard output in the terminal that launched the application, with the same timestamps and throttling as the viewer. Console output continues if file logging becomes unavailable.

The history viewer skips repainting when no entries have changed. Enemy matching shares search crops between templates with identical dimensions and sums small RGB difference buffers directly, preserving the similarity calculation and search resolution. Replay overlays reuse loaded fonts. A local synthetic 320 × 180 frame with 12 same-size examples took 4.03 seconds before these detector changes and 0.57 seconds afterward, with exactly identical proposals and scores. This measures detector computation only, not overall application latency or live-game performance. ADB capture, video decoding and replay encoding can still take significant time; analysis still launches a video decoder for each distinct example timestamp.

The main console continuously shows the active profile, AI running/paused state, device operation and its elapsed time, player recording/analysis activity, and capture blocking reason. **Activity log** opens a scrolling history with local timestamps. Status changes, analysis progress, model-save messages, rewards, map/profile changes, operation completion times and errors are logged automatically. Repeated progress is throttled; a heartbeat is recorded every 15 seconds, including while idle. A heartbeat reports the UI's known state, not proof that a device or external media process is responsive. If Tk itself freezes, its heartbeat also stops.

Logs survive restarts in `training/logs/activity.log` inside the local cache. Rotation keeps the active file and three backups of approximately 2 MiB each (about 8 MiB total). The viewer retains the latest 500 entries from the current session; older entries remain in the rotated files until overwritten. If file logging fails, the current session history remains available in the viewer. Logs can contain profile/map names, local paths and error details. Automated tests verify rotation, bounded history, throttling, repeated heartbeats and file-failure handling; the desktop viewer has not been manually verified.

### Automatic storage cleanup

Cleanup runs before a new phone recording and after successful post-game learning, for the current player/brawler/mode combination. After the learned model is saved and a completed, nonempty replay exists, it removes that game's original `game.mkv`, raw `input.jsonl` and `recorder.log`. The annotated replay replaces the raw video for watching the game. Failed or cancelled analysis removes its own partial export folder but preserves the original recording for retry. Interrupted exports left by a process crash are not automatically removed.

Annotated replays have a 2 GiB budget per player/brawler/mode combination. Oldest replay videos and their playback metadata are removed first; the newest replay is always kept, even if it alone exceeds the budget. **Watch analyzed game** explains when a replay is no longer available. This is a replay budget, not a limit on total cache size: unapplied recordings, example images, annotations, action records, rewards, models and emulator files are preserved. Cleanup never resets learned progress. Existing eligible media are cleaned when the next recording starts or analysis succeeds; no bulk cleanup runs merely by opening the application.

Automated tests cover retention order, unapplied and failed recordings, preservation of training references, symlink/path protections and successful/cancelled analysis. Disk cleanup has not been manually verified during live phone recording.

Advanced **Imitation influence** controls how often the style supplies an action: for example `0.7` means a 70% chance, with the reward policy deciding otherwise. `0` uses only the reward policy. If no applicable style examples exist, the reward policy is used even at `1`. Continue assigning your own rewards during AI play as usual.

Full-game recording and analysis require `scrcpy` with `--capture-orientation` support, `ffmpeg` and `ffprobe` on PATH, plus a working graphical desktop for live preview. Missing tools produce an error before recording. These tools were present on the development host; setup does not install them automatically. scrcpy is given the project's ADB executable and selected serial so it targets the same phone. Capture errors leave the raw session marked failed and do not train a model.

The style model counts demonstrated actions per visual state. Once a state has at least three applicable examples, it samples from those counts; otherwise it uses that player's action frequencies on the same confirmed map, or the unassigned-map context. It also learns mean drag distance and duration for each movement/directional ability in the same map context. Imitated swipes use those means (normalized radius bounded to 2–30% of the smaller screen dimension, duration 50–800 ms); normal reward-policy actions retain their calibrated defaults. Calibration is never overwritten by these preferences. Full curved touch paths are shown in the replay, while AI execution remains one cardinal swipe at a time. The model does not mix examples between players, brawlers, modes or map contexts. This is a basic supervised preference model, not neural behavior cloning, tactical understanding or exact trajectory reproduction. Samples do not receive fabricated positive rewards, increment the reinforcement learner's transition count, or count as mastery in recommendations. Imitation settings start off when the console opens.

During calibration preview, full-game capture and post-game analysis, ordinary auto capture is stopped. AI actions, labeling, profile/map changes and control recalibration are blocked until the session stops, so a recording cannot silently cross profiles or maps. Record one match with the same brawler, mode, map and control layout, stopping before navigating menus or changing them. The recorder does not detect whether Brawl Stars is in the foreground and cannot infer that a menu gesture is not gameplay. Closing the learning dialog or main console first requests a clean stop/cancellation and suppresses automatic analysis; close it again after the session stops. The saved game can be analyzed later from advanced settings.

Input is read using Android's [getevent tool](https://source.android.com/docs/core/interaction/input/getevent), with [type-A contacts and type-B slots](https://www.kernel.org/doc/html/latest/input/multi-touch-protocol.html). Exactly one readable multitouch touchscreen is currently supported. Some phones deny input-device access even when USB debugging is enabled; this mode reports the limitation without trying to root the phone or change permissions. Anonymous simultaneous type-A contacts without tracking IDs are rejected because they cannot be paired reliably. Event loss or queue overflow stops observation rather than silently training on corrupted input.

Offline analysis reconstructs type-A/type-B touches, splits held motion into roughly half-second segments and samples touch-free pauses at most once per second after input begins. Touch positions near calibrated controls become the existing discrete actions. Simultaneous fingers yield separate examples but not simultaneous AI execution. Diagonal movement becomes the dominant cardinal direction, and normal attack aiming is reduced to `attack`. Input events use their device monotonic timestamps, related to the host timeline by the smallest observed host/device offset. The recorder-start log provides an approximate video origin; USB startup/buffering and manual offset error can still misalign examples. Analysis selects a frame at least 40 ms before the associated action, rejects gaps over one second, and ignores unsupported/out-of-video actions. It uses the recording's saved calibration and map, not any subsequently edited map field.

Analysis stages example journals, JPEG reference frames and an annotated replay locally, computes preferences in a separate model instance, then applies one atomic model save after analysis and rendering succeed. The replay is an H.264 MP4 at 20 FPS, up to 960 × 540, requiring FFmpeg's `libx264` encoder. Character proposals are sampled once per second using the profile's confirmed appearance examples and held until the next sampled result; this is not continuous object tracking. Without enemy examples, the video explicitly says recognition needs labels. Predictions are never used as new ground-truth identity labels. A failed/cancelled analysis keeps the existing model intact; staging files and raw video remain for inspection. Audio is not recorded. Files live in the local profile cache, and reward policies remain intact. Legacy screenshot-based demonstrations are preserved.

Verification: the connected HONOR's touchscreen capabilities were readable via `adb getevent -lp`, including its type-A coordinate ranges. Automated tests cover both input protocols, rotations, mapping, profile/map isolation, imitation selection, capture without training, cancellation, atomic application and duplicate prevention. FFmpeg integration tests encode/decode an actual synthetic video and learn its scripted actions. Full-game scrcpy capture, synchronization accuracy, gameplay imitation quality and the GUI workflow have not been manually verified on the phone.

### Automatic preview capture

Enable **Auto capture** to refresh screenshots and enemy/map recognition without sending game actions. Set **Interval / minimum (seconds)** from `0.2` to `60` and click **Apply capture settings**. With **Adaptive** unchecked, this is the target time between capture starts. If processing takes longer, captures wait for completion and leave at least 0.1 seconds before the next one; frames never build up in a queue.

With **Adaptive** checked, the interval is a minimum. The scheduler targets the greater of that minimum and twice the smoothed capture/processing time, leaving room for interaction instead of continuously running detection. For example, a 1-second minimum and 2-second processing time give an initial 4-second target interval. The separate capture status shows the target and latest processing duration. This is a timing heuristic, not CPU/GPU utilization measurement or a proven optimal frame rate. Start with the default 2 seconds and Adaptive enabled.

Preview capture waits during device operations, calibration, labeling, box refinements and identity review dialogs. During continuous AI training it waits because each AI step already captures its result. It resumes after the blocking activity ends; **Pause** stops AI stepping but does not disable preview capture. Uncheck **Auto capture** to stop preview updates. Errors, profile switches, episode end and closing the console disable it. An already running capture can still finish after disabling. Interval and adaptive preferences persist in `training/capture-settings.json`, but automatic capture starts off each time the console opens.

Preview refreshes do not perform AI actions, change pending transition states, assign rewards, train on predictions, or save every frame to disk. If you refresh while a transition awaits feedback, the reward still applies to the last completed AI action and its original captured result. Disable auto capture while assessing that exact result if you want it to stay visible. Labeled examples continue to save normally. Automated tests cover scheduling, adaptation, cancellation, interaction blocking and reward/transition preservation; live automatic capture has not been verified on a phone.

### Super, gadget, hypercharge and reward sizes

Pause and capture a frame. Controls are proposed automatically; use **Calibrate super**, **Calibrate gadget** or **Calibrate hypercharge** for manual center selection when needed. An optional ability is excluded from AI choices until calibrated or detected with sufficiently strong localization. Set **Super input** and **Gadget input** to `tap` for button activation/auto-aim, or `drag` for directional aiming. Drag mode exposes four separate actions (up, down, left, right), initially using a swipe of 10% of the smaller screen dimension. Hypercharge uses a tap. Availability proposals can exclude confidently recognized uncharged/cooldown abilities, as described below. Remaining gadget uses, exact charge percentages and numeric cooldown countdowns are not read. Live ability behavior has not yet been verified on a physical device.

Calibration and super input mode are shared across profiles; recalibrate when the layout changes. Gadget input is saved separately for each brawler, shared across that brawler's game modes. On first opening a brawler without a saved gadget setting, the console asks whether the equipped gadget requires dragging. Answer Yes for drag or No for tap. Change this later using **Gadget input**, including when equipping a different gadget. Previously shared gadget settings are not assumed correct for a new brawler. Existing policies retain their learned values for the original actions; newly added actions start at zero.

Enter a number under **Custom reward** and click **Add reward** after an AI step, for example `0.2` for a small improvement, `10` for completing an exercise, or `-5` for a mistake. Each click adds to the visible pending reward, including the existing +1/-1 buttons. **Clear pending reward** resets that sum before it is committed. Feedback applies to the most recently completed action, and is committed on the next step or episode end. Pause for more time to assess and reward an action. Larger magnitudes produce larger Q-value updates; recommendations still count positive/negative feedback rather than interpreting magnitude as mastery.

### Automatic control boxes and availability

Captures and analyzed replays show cyan boxes for **Movement joystick**, **Attack**, **Super**, **Gadget** and **Hypercharge**, separately from red entity boxes. Localization starts without pretrained weights: it proposes circular colored HUD regions, uses confirmed centers for dim controls, and can refine them using your own appearance examples. It is a heuristic with human correction, not a universal game UI detector. Strong proposals fill only missing calibration entries; existing calibrated centers are preserved. **Use detected centers** explicitly accepts proposals with a lower threshold, pauses AI stepping and saves the centers. Weak proposals need manual confirmation.

The box labels include `ready`, `uncharged`, `cooldown`, `unavailable`, `visible` or `unknown`. Bright green gadget and bright purple hypercharge regions can suggest readiness; the super's blue/yellow appearance suggests uncharged/ready. These color rules are proposals, not calibrated probabilities. Dim gadget appearance alone cannot reliably distinguish cooldown from exhausted uses, so it carries a low-confidence question mark until examples help. Attack ammo and exact numeric cooldowns are not inferred.

Click a cyan box to confirm or correct its control type and current state. **Confirm center and learn state** saves both the appearance example and the center. **Redraw control box** lets you replace inaccurate bounds. **Not this control** rejects a false proposal. **Label control** lets you draw a box around a missed control. You can teach gray gadget states as `cooldown` or `unavailable`, and super/hypercharge as `uncharged`. The detector keeps up to 12 examples per control/state in the active brawler/mode profile's `hud-detector.json`; source crops are logged in `hud-annotations.jsonl`. Examples and calibration persist after closing the console. Controls can be reviewed while a background preview is running, with the reviewed frame held fixed.

HUD availability is included in learning state keys. Super, gadget and hypercharge actions (including directional variants) are excluded when both localization and uncharged/cooldown/unavailable evidence meet the confidence thresholds. Uncertain state proposals do not disable actions. Reward targets use availability from the original action-result frame, even if auto capture later updates the display. Existing learned values remain stored; newly enriched visual states start without values until taught. HUD boxes are never counted as enemies. Replay HUD proposals are sampled alongside entity proposals and saved separately in `control-proposals.json`; they work even before any enemy identities have been taught. Raw scrcpy recording remains free of detection work.

Verification: all five control regions were inspected on a locally saved gameplay/control-edit screenshot, and automated tests cover synthetic location/state changes, persistence, correction, uncertain cooldown handling, action masking, calibration preservation and overlays. This is saved-frame verification, not manual verification of detection or availability during live Brawl Stars play. Visual effects, translucent controls, layouts and future HUD changes may require new examples.

### Teaching enemy recognition

The entity selector also includes **Showdown box** for the destructible box containing a Power Cube. Use **Label enemy**, draw a tight box around it and select **Showdown box**, or choose that identity when correcting a detection. It is learned from your confirmed examples and appears by that name in captures and annotated replays, just like brawler and NPC identities.

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
        player-detector.json # Self-marker geometry and human appearance corrections
        player-annotations.jsonl # Confirmed/rejected controlled-character boxes
        player-frame-*.png  # Player teaching screenshots
        hud-detector.json   # Per-profile control/state appearance examples
        hud-annotations.jsonl # Confirmed/rejected HUD crops and availability states
        hud-frame-*.png     # HUD teaching screenshots
        box-feedback.jsonl  # Original/corrected rectangles and rejection reasons
        map-examples.jsonl  # Confirmed map names and screenshot filenames
        map-frame-*.png     # Human-confirmed map examples
        playstyles/<player-key>/
          style.json       # Action counts learned from this player's demonstrations
          demonstrations.jsonl # States, gestures, actions and session/map metadata
          frame-*.jpg      # Only frames referenced by demonstrations
          recordings/<session-id>/
            session.json   # Profile/map, calibration, timing and capture status
            game.mkv       # Continuous game video, without audio
            input.jsonl    # Raw device input and host/device timestamps
            recorder.log   # scrcpy startup and capture diagnostics
            analyses/<analysis-id>/
              examples.jsonl # Offline action/state examples
              result.json  # Analysis parameters and recording token
              frame-*.jpg  # Reference frames used by the analysis
              replay.mp4   # Annotated game, automatically opened after success
              replay.json  # Playback dimensions, timing and recognition status
              finger-actions.json # Finger positions, interpreted actions and drag status
              character-proposals.json # Sampled brawler/NPC proposals
              control-proposals.json # Sampled control boxes and availability states
              player-proposals.json # Sampled controlled-character boxes and evidence
              replay-media.log # FFmpeg rendering diagnostics
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
