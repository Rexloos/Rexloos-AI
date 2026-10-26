"""Desktop workspace layout; callbacks and learning stay in the console."""
import json
import tkinter as tk
from tkinter import ttk

from .capture import CaptureLoop, capture_interval
from .playstyle import Playstyle
from .profiles import choices
from .replay import TASKS


def section(parent, title):
    frame = ttk.LabelFrame(parent, text=title, padding=12)
    frame.pack(fill="x", pady=(0, 12))
    return frame


def buttons(parent, actions, columns=2):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=4)
    for col in range(columns):
        row.columnconfigure(col, weight=1, uniform="actions")
    for index, (label, command) in enumerate(actions):
        ttk.Button(row, text=label, command=command).grid(row=index//columns, column=index % columns,
                                                       sticky="ew", padx=3, pady=3)
    return row


def note(parent, text=None, variable=None):
    label = ttk.Label(parent, text=text, textvariable=variable, wraplength=320, justify="left")
    label.pack(fill="x", pady=4)
    return label


def scroll_tab(notebook, title):
    outer = ttk.Frame(notebook)
    notebook.add(outer, text=title)
    canvas = tk.Canvas(outer, highlightthickness=0, width=370, background="#f3f5f8")
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    content = ttk.Frame(canvas, padding=10)
    item = canvas.create_window(0, 0, anchor="nw", window=content)
    content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
    def wheel(event):
        canvas.yview_scroll(-1 if event.num == 4 or getattr(event, "delta", 0) > 0 else 1, "units")
        return "break"
    # Bind only this tab's widgets; do not steal scrolling from other windows.
    def bind_children(widget):
        if not isinstance(widget, (ttk.Combobox, tk.Spinbox)):
            for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(event, wheel, add="+")
        for child in widget.winfo_children():
            bind_children(child)
    content.after_idle(lambda: bind_children(outer))
    return content


def build_dashboard(c):
    root = c.root
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("TFrame", background="#f3f5f8")
    style.configure("TLabel", background="#f3f5f8", foreground="#253348")
    style.configure("TLabelframe", background="#f3f5f8")
    style.configure("TLabelframe.Label", background="#f3f5f8", foreground="#253348", font=("TkDefaultFont", 10, "bold"))
    style.configure("TButton", padding=(8, 7))
    style.configure("Title.TLabel", font=("TkDefaultFont", 18, "bold"))
    style.configure("Accent.TButton", background="#235ed7", foreground="white")
    style.map("Accent.TButton", background=[("active", "#1948ad")])
    root.configure(background="#f3f5f8")
    width, height = min(1320, root.winfo_screenwidth()-60), min(900, root.winfo_screenheight()-80)
    root.geometry(f"{width}x{height}")
    root.minsize(min(900, width), min(600, height))
    header = ttk.Frame(root, padding=(18, 12))
    header.pack(fill="x")
    ttk.Label(header, text="Brawl AI", style="Title.TLabel").pack(side="left")
    ttk.Button(header, text="Activity log", command=c.open_activity_log).pack(side="right")
    c.profile_status = tk.StringVar()
    ttk.Label(header, textvariable=c.profile_status).pack(side="left", padx=20)
    c.update_profile_status()
    footer = ttk.Frame(root, padding=(18, 8))
    footer.pack(side="bottom", fill="x")
    c.status = tk.StringVar(value="Start in Setup: load a profile, open the game and capture a frame.")
    c.activity_summary = tk.StringVar(value="Activity logging started")
    status = ttk.Label(footer, textvariable=c.status, font=("TkDefaultFont", 10, "bold"), wraplength=1100)
    status.pack(fill="x")
    activity = ttk.Label(footer, textvariable=c.activity_summary, wraplength=1100)
    activity.pack(fill="x", pady=(4, 0))
    footer.bind("<Configure>", lambda event: [label.configure(wraplength=max(200, event.width-36)) for label in (status, activity)])
    workspace = ttk.Panedwindow(root, orient="horizontal")
    workspace.pack(fill="both", expand=True, padx=18, pady=(0, 8))
    preview = ttk.Frame(workspace, padding=(0, 0, 12, 0))
    sidebar = ttk.Notebook(workspace, width=390)
    workspace.add(preview, weight=3)
    workspace.add(sidebar, weight=1)
    toolbar = ttk.Frame(preview)
    toolbar.pack(fill="x", pady=(0, 8))
    ttk.Label(toolbar, text="Game preview", font=("TkDefaultFont", 12, "bold")).pack(side="left")
    ttk.Button(toolbar, text="Capture", style="Accent.TButton", command=c.capture).pack(side="right")
    c.preview_limit = (max(300, width-460), max(180, height-240))
    c.canvas = tk.Canvas(preview, width=480, height=270, bg="#172131", highlightthickness=0)
    c.canvas.pack(fill="both", expand=True)
    c.canvas.create_text(24, 30, anchor="nw", fill="#dbe5f4", text="No frame captured\n\nOpen the game, then click Capture.", font=("TkDefaultFont", 13))
    c.canvas.bind("<ButtonPress-1>", c.press)
    c.canvas.bind("<B1-Motion>", c.drag)
    c.canvas.bind("<ButtonRelease-1>", c.release)
    c.canvas.bind("<Configure>", c.resize_preview)
    c.detection_status = tk.StringVar(value="Capture a frame to see player, enemy and control proposals.")
    ttk.Label(preview, textvariable=c.detection_status, wraplength=650).pack(fill="x", pady=(8, 2))
    ttk.Label(preview, text="Green: you    Red: entities    Cyan: controls\nClick a box to review it. Draw boxes in the preview. Esc cancels.", wraplength=650).pack(fill="x")
    train, teach, setup = [scroll_tab(sidebar, title) for title in ("Train", "Teach", "Setup")]
    c.workspace_tabs = sidebar
    c.setup_tab = 2

    actions = section(train, "1. Run the AI")
    note(actions, "Capture and calibrate movement + attack in Setup before the first AI step.")
    buttons(actions, [("One AI step", c.one_step), ("Start AI", c.start), ("Pause AI", c.pause), ("Episode ended", c.terminal)])
    rewards = section(train, "2. Guide with rewards")
    note(rewards, "Reward a useful result. Use a larger value for a bigger achievement.")
    buttons(rewards, [("Reward +1", lambda: c.feedback(1)), ("Penalty -1", lambda: c.feedback(-1))])
    c.reward_input = tk.StringVar(value="10")
    c.reward_status = tk.StringVar(value="Pending reward: +0")
    ttk.Label(rewards, text="Custom reward (positive or negative)").pack(anchor="w")
    ttk.Entry(rewards, textvariable=c.reward_input).pack(fill="x", pady=4)
    buttons(rewards, [("Add reward", c.custom_feedback), ("Clear reward", c.clear_feedback)])
    note(rewards, variable=c.reward_status)
    c.player_name = tk.StringVar(value="Player 1")
    c.use_style = tk.BooleanVar(value=False)
    c.style_strength = tk.DoubleVar(value=.7)
    c.touch_rotation = tk.StringVar(value="90")
    c.demo_interval = tk.StringVar(value="0.5")
    c.video_fps = tk.StringVar(value="30")
    c.recording_name = tk.StringVar()
    c.sync_offset = tk.StringVar(value="0")
    c.review_action = tk.IntVar(value=1)
    c.touch_auto = tk.BooleanVar(value=True)
    c.player_phase = tk.StringVar(value="Ready to record a game")
    c.player_progress = tk.DoubleVar(value=0)
    c.player_bar = c.player_dialog = None
    c.auto_analyze = False
    c.player_tasks = {key: {"label": label, "value": tk.DoubleVar(value=0), "text": tk.StringVar(value="Waiting"), "state": "waiting"} for key, label in TASKS}
    c.task_bars = {}
    c.style = Playstyle(c.folder, c.player_name.get())
    c.style_status = tk.StringVar(value=f"Player style: {c.style.examples} examples | imitation off")
    recording = section(train, "3. Learn from your playstyle")
    note(recording, "Record on your phone, stop after the game, then follow the analysis progress.")
    buttons(recording, [("Learn player playstyle...", c.player_window)], columns=1)
    note(recording, variable=c.style_status)
    recommendation = section(train, "Training recommendation")
    c.recommendation_status = tk.StringVar()
    note(recommendation, variable=c.recommendation_status)
    buttons(recommendation, [("Refresh", c.refresh_recommendation), ("Load suggested profile", c.load_recommendation)])
    c.refresh_recommendation()

    objects = section(teach, "Characters and objects")
    note(objects, "Choose what to teach, then draw a tight rectangle in the game preview.")
    buttons(objects, [("Label enemy", lambda: c.set_mode("enemy")), ("Label player", lambda: c.set_mode("player")),
                      ("False detection", lambda: c.set_mode("not_enemy")), ("Label object", lambda: c.set_mode("label"))])
    buttons(objects, [("Cancel selection (Esc)", c.cancel_box_edit)], columns=1)
    controls = section(teach, "Control appearance and availability")
    note(controls, "Teach control boxes and states such as cooldown or uncharged. Click existing cyan boxes to correct them.")
    buttons(controls, [("Label control", lambda: c.set_mode("hud"))], columns=1)
    maps = section(teach, "Map identity")
    c.map_name = tk.StringVar()
    c.map_select = ttk.Combobox(maps, textvariable=c.map_name, values=c.map_recognizer.names)
    c.map_select.pack(fill="x", pady=4)
    buttons(maps, [("Save name", c.save_map_name), ("Set / teach map", c.teach_map), ("Confirm suggestion", c.confirm_map), ("Clear active map", c.clear_map)])
    c.map_status = tk.StringVar(value="Active map: unassigned. Capture and enter its name.")
    note(maps, variable=c.map_status)

    profile = section(setup, "1. Choose a profile")
    brawlers, modes = choices(c.training_root)
    c.brawler_name = tk.StringVar(value=c.profile.brawler)
    c.mode_name = tk.StringVar(value=c.profile.mode)
    ttk.Label(profile, text="Brawler").pack(anchor="w")
    c.brawler_select = ttk.Combobox(profile, textvariable=c.brawler_name, values=brawlers)
    c.brawler_select.pack(fill="x", pady=(2, 8))
    ttk.Label(profile, text="Game mode").pack(anchor="w")
    c.mode_select = ttk.Combobox(profile, textvariable=c.mode_name, values=modes)
    c.mode_select.pack(fill="x", pady=2)
    buttons(profile, [("Load / create profile", c.switch_profile)], columns=1)
    calibration = section(setup, "2. Calibrate controls")
    note(calibration, "Capture a frame, choose a control below, then click its center in the preview.")
    buttons(calibration, [(f"Calibrate {name}", lambda name=name: c.set_mode(name)) for name in ("move", "attack", "super", "gadget", "hypercharge")])
    buttons(calibration, [("Use detected centers", c.use_detected_controls)], columns=1)
    c.control_modes = {}
    for control in ("super", "gadget"):
        row = ttk.Frame(calibration)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=f"{control.title()} input").pack(side="left")
        variable = tk.StringVar(value=c.controls.get(control+"_mode", "tap"))
        c.control_modes[control] = variable
        combo = ttk.Combobox(row, textvariable=variable, values=("tap", "drag"), state="readonly", width=9)
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", c.save_control_modes)
    capture = section(setup, "3. Automatic capture")
    try:
        path = c.training_root/"capture-settings.json"
        settings = json.loads(path.read_text()) if path.exists() else {}
        seconds, adaptive = capture_interval(settings.get("seconds", .5)), bool(settings.get("adaptive", True))
    except (OSError, ValueError, TypeError, AttributeError):
        seconds, adaptive = .5, True
    c.auto_capture_enabled = tk.BooleanVar(value=False)
    c.capture_seconds = tk.StringVar(value=str(seconds))
    c.capture_adaptive = tk.BooleanVar(value=adaptive)
    ttk.Checkbutton(capture, text="Auto capture", variable=c.auto_capture_enabled, command=c.configure_capture).pack(anchor="w")
    ttk.Label(capture, text="Interval / minimum (seconds)").pack(anchor="w", pady=(8, 2))
    tk.Spinbox(capture, from_=.2, to=60, increment=.2, width=6, textvariable=c.capture_seconds).pack(fill="x")
    ttk.Checkbutton(capture, text="Adapt interval to processing speed", variable=c.capture_adaptive, command=c.configure_capture).pack(anchor="w", pady=4)
    buttons(capture, [("Apply capture settings", c.configure_capture)], columns=1)
    c.capture_status = tk.StringVar(value="Auto capture: off")
    note(capture, variable=c.capture_status)
    c.capture_loop = CaptureLoop(root, c.capture_preview, c.capture_blocked, c.capture_status.set)
    root.report_callback_exception = c.callback_error
    root.bind("<Escape>", lambda event: c.cancel_box_edit())
    sidebar.select(2 if not {"move", "attack"}.issubset(c.calibrated) else 0)
