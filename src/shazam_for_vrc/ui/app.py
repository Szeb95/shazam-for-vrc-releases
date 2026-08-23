"""Modern dark desktop overlay for on-demand VRChat music recognition."""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from shazam_for_vrc import __version__
from shazam_for_vrc.application import (
    DebugSampleError,
    ListeningOutcome,
    ListeningProgress,
    ListeningResultKind,
    ListeningService,
    ListeningStage,
    ListenOptions,
    PlayerDebugInfo,
    PlayerDebugService,
    PlayerTimeUnavailableError,
    default_debug_sample_path,
    delete_last_sample,
)
from shazam_for_vrc.config import (
    KEYBOARD_HOTKEYS,
    MAX_CONTROLLER_DOUBLE_PRESS_SECONDS,
    MAX_CONTROLLER_HOLD_SECONDS,
    MAX_RECORD_SECONDS,
    MAX_RETRY_COUNT,
    MIN_CONTROLLER_DOUBLE_PRESS_SECONDS,
    MIN_CONTROLLER_HOLD_SECONDS,
    MIN_RECORD_SECONDS,
    AppConfig,
    ConfigError,
    load_config,
    save_config,
)
from shazam_for_vrc.input import InputListener, InputSource, InputStatus, InputStatusKind
from shazam_for_vrc.input.hotkey import KeyboardHotkeyListener
from shazam_for_vrc.input.osc import VRChatOscInputListener, normalize_avatar_parameter_name
from shazam_for_vrc.input.steamvr import SteamVRInputListener
from shazam_for_vrc.output import (
    ChatboxOutputError,
    ChatboxSender,
    ChatboxTrackContext,
    Notification,
    NotificationError,
    NotificationSender,
    NotificationStyle,
    VRChatOscChatbox,
    XSOverlayNotifier,
)
from shazam_for_vrc.output.history import (
    HistoryEntry,
    HistoryError,
    HistoryStore,
    TrackLogKind,
    copyable_log_text,
    default_history_path,
)
from shazam_for_vrc.recognition import ShazamRecognitionError
from shazam_for_vrc.release_notes import RELEASE_NOTES
from shazam_for_vrc.streams.audio_capture import AudioCaptureError
from shazam_for_vrc.streams.playback_position import format_media_time
from shazam_for_vrc.streams.resolver import StreamResolutionError
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider
from shazam_for_vrc.streams.system_audio_capture import SystemAudioCaptureError
from shazam_for_vrc.updates import (
    DownloadedUpdate,
    UpdateCheckResult,
    UpdateClient,
    UpdateError,
    UpdateInfo,
)
from shazam_for_vrc.vrchat.player_tracker import MediaSelectionError

logger = logging.getLogger(__name__)

SteamVRListenerFactory = Callable[..., InputListener]
HotkeyListenerFactory = Callable[..., InputListener]
OscListenerFactory = Callable[..., InputListener]

BACKGROUND = "#1e1f22"
SIDEBAR = "#111214"
PANEL = "#2b2d31"
PANEL_HOVER = "#35373c"
INPUT_BACKGROUND = "#383a40"
TEXT = "#f2f3f5"
MUTED = "#b5bac1"
FAINT = "#80848e"
ACCENT = "#10a37f"
ACCENT_HOVER = "#18b991"

CONTROLLER_BUTTON_LABELS = {
    "a": "Right A",
    "b": "Right B",
    "thumbstick": "Right thumbstick click",
    "trigger": "Right trigger click",
}
TRIGGER_MODE_LABELS = {
    "long_press": "Long press",
    "double_press": "Double press",
}


class _HoverTooltip:
    """Small accessible hover label used by mix-information icons."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: tk.Event[tk.Misc]) -> None:
        if self._window is not None:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            window,
            text=self.text,
            background="#111214",
            foreground=TEXT,
            relief=tk.SOLID,
            borderwidth=1,
            padx=9,
            pady=6,
            justify=tk.LEFT,
            wraplength=360,
            font=("Segoe UI", 9),
        ).pack()
        self._window = window

    def _hide(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None


class OverlayApp:
    """Own the desktop UI while core listening and inputs run independently."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        service: ListeningService | None = None,
        player_debug_service: PlayerDebugService | None = None,
        history_store: HistoryStore | None = None,
        steamvr_listener_factory: SteamVRListenerFactory = SteamVRInputListener,
        hotkey_listener_factory: HotkeyListenerFactory = KeyboardHotkeyListener,
        osc_listener_factory: OscListenerFactory = VRChatOscInputListener,
        notification_sender: NotificationSender | None = None,
        chatbox_sender: ChatboxSender | None = None,
        update_client: UpdateClient | None = None,
    ) -> None:
        self.root = root
        self.service = service or ListeningService()
        self.player_debug_service = player_debug_service or PlayerDebugService()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_listening = False
        self._steamvr_listener_factory = steamvr_listener_factory
        self._hotkey_listener_factory = hotkey_listener_factory
        self._osc_listener_factory = osc_listener_factory
        self._notification_sender = notification_sender or XSOverlayNotifier()
        self._chatbox_sender = chatbox_sender or VRChatOscChatbox()
        self._update_client = update_client or UpdateClient()
        self._input_listeners: list[InputListener] = []
        self._input_statuses: dict[InputSource, InputStatus] = {}
        self._input_generation = 0
        self._input_trigger_pending = False
        self._input_trigger_lock = threading.Lock()
        self._settings_widgets: list[tuple[tk.Widget, str]] = []
        self._pages: dict[str, tk.Frame] = {}
        self._nav_buttons: dict[str, tk.Button] = {}
        self._player_debug_window: tk.Toplevel | None = None
        self._player_debug_refreshing = False
        self._player_debug_after_id: str | None = None
        self._update_checking = False
        self._update_downloading = False
        self._available_update: UpdateInfo | None = None
        self._last_listening_stage = ListeningStage.FINDING_PLAYER
        self._log_filter_buttons: dict[str, ttk.Button] = {}
        startup_messages: list[str] = []

        try:
            self.config = load_config()
        except ConfigError as error:
            logger.warning("Could not load overlay settings: %s", error)
            self.config = AppConfig()
            startup_messages.append(str(error))

        self.history_store = history_store or HistoryStore(_history_path_for_config(self.config))
        self.debug_sample_path = _debug_sample_path_for_config(self.config)
        try:
            self.history_entries = self.history_store.load()
        except HistoryError as error:
            logger.warning("Could not load the track log: %s", error)
            self.history_entries: list[HistoryEntry] = []
            startup_messages.append(str(error))

        self.record_seconds = tk.StringVar(value=f"{self.config.record_seconds:g}")
        self.retry_count = tk.StringVar(value=str(self.config.retry_count))
        self.system_audio_fallback_enabled = tk.BooleanVar(
            value=self.config.system_audio_fallback_enabled
        )
        self.keep_last_sample = tk.BooleanVar(value=self.config.keep_last_sample)
        self.always_on_top = tk.BooleanVar(value=self.config.always_on_top)
        self.show_copied_indicators = tk.BooleanVar(
            value=self.config.show_copied_indicators
        )
        self.show_tracks = tk.BooleanVar(value=True)
        self.show_mixes = tk.BooleanVar(value=True)
        self.show_live = tk.BooleanVar(value=True)
        self.expand_log_details = tk.BooleanVar(value=False)
        self.show_errors = tk.BooleanVar(value=False)
        self.automatic_update_checks = tk.BooleanVar(
            value=self.config.automatic_update_checks
        )
        self.steamvr_input_enabled = tk.BooleanVar(value=self.config.steamvr_input_enabled)
        self.controller_button = tk.StringVar(
            value=CONTROLLER_BUTTON_LABELS[self.config.controller_button]
        )
        self.controller_trigger_mode = tk.StringVar(
            value=TRIGGER_MODE_LABELS[self.config.controller_trigger_mode]
        )
        self.controller_hold_seconds = tk.StringVar(
            value=f"{self.config.controller_hold_seconds:g}"
        )
        self.controller_double_press_seconds = tk.StringVar(
            value=f"{self.config.controller_double_press_seconds:g}"
        )
        self.keyboard_hotkey_enabled = tk.BooleanVar(value=self.config.keyboard_hotkey_enabled)
        self.keyboard_hotkey = tk.StringVar(value=self.config.keyboard_hotkey)
        self.osc_input_enabled = tk.BooleanVar(value=self.config.osc_input_enabled)
        self.osc_input_parameter = tk.StringVar(value=self.config.osc_input_parameter)
        self.osc_input_port = tk.StringVar(value=str(self.config.osc_input_port))
        self.xsoverlay_notifications_enabled = tk.BooleanVar(
            value=self.config.xsoverlay_notifications_enabled
        )
        self.vrchat_chatbox_enabled = tk.BooleanVar(value=self.config.vrchat_chatbox_enabled)
        self.history_path = tk.StringVar(value=self.config.history_path)
        self.debug_directory = tk.StringVar(value=self.config.debug_directory)
        self.status_text = tk.StringVar(
            value=" ".join(startup_messages) if startup_messages else "Ready to listen."
        )
        self.settings_status_text = tk.StringVar(value="")
        self.input_status_text = tk.StringVar(value="Starting input listeners...")
        self.debug_text = tk.StringVar()
        self.player_debug_text = tk.StringVar(value="Reading the current VRChat player...")
        self.update_status_text = tk.StringVar(
            value=f"Installed version: {__version__}. Not checked yet."
        )
        self.update_progress_text = tk.StringVar(value="")

        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._show_page("listen")
        self._refresh_history()
        self._refresh_debug_controls()
        self.root.after(100, self._poll_events)
        self._restart_input_listeners(self.config)
        if self.config.automatic_update_checks:
            self.root.after(1_500, lambda: self._start_update_check(silent=True))

    def _configure_window(self) -> None:
        self.root.title("Shazam for VRC by Szeb")
        self.root.geometry("1100x760")
        self.root.minsize(900, 620)
        self.root.configure(background=BACKGROUND)
        self.root.attributes("-topmost", self.config.always_on_top)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="white",
            borderwidth=0,
            focusthickness=0,
            padding=(16, 10),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_HOVER), ("disabled", "#426e63")],
        )
        style.configure(
            "Secondary.TButton",
            background=INPUT_BACKGROUND,
            foreground=TEXT,
            borderwidth=0,
            focusthickness=0,
            padding=(11, 7),
            font=("Segoe UI", 9),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", PANEL_HOVER), ("disabled", PANEL)],
            foreground=[("disabled", FAINT)],
        )
        style.configure(
            "LogToggleOn.TButton",
            background=ACCENT,
            foreground="white",
            borderwidth=0,
            focusthickness=0,
            padding=(10, 7),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "LogToggleOn.TButton",
            background=[("active", ACCENT_HOVER)],
        )
        style.configure(
            "LogToggleOff.TButton",
            background=INPUT_BACKGROUND,
            foreground=MUTED,
            borderwidth=0,
            focusthickness=0,
            padding=(10, 7),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "LogToggleOff.TButton",
            background=[("active", PANEL_HOVER)],
            foreground=[("active", TEXT)],
        )
        style.configure(
            "Dark.TCheckbutton",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 10),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", PANEL)],
            foreground=[("disabled", FAINT)],
            indicatorcolor=[("selected", ACCENT), ("!selected", INPUT_BACKGROUND)],
        )
        style.configure(
            "Dark.TSpinbox",
            fieldbackground=INPUT_BACKGROUND,
            background=INPUT_BACKGROUND,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=INPUT_BACKGROUND,
            lightcolor=INPUT_BACKGROUND,
            darkcolor=INPUT_BACKGROUND,
            padding=6,
        )
        style.configure(
            "Dark.TEntry",
            fieldbackground=INPUT_BACKGROUND,
            foreground=TEXT,
            bordercolor=INPUT_BACKGROUND,
            insertcolor=TEXT,
            padding=7,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=INPUT_BACKGROUND,
            background=INPUT_BACKGROUND,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=INPUT_BACKGROUND,
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", INPUT_BACKGROUND)],
            foreground=[("readonly", TEXT), ("disabled", FAINT)],
            selectbackground=[("readonly", INPUT_BACKGROUND)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "Teal.Horizontal.TProgressbar",
            troughcolor=INPUT_BACKGROUND,
            background=ACCENT,
            bordercolor=INPUT_BACKGROUND,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor=BACKGROUND,
            background=INPUT_BACKGROUND,
            arrowcolor=MUTED,
            bordercolor=BACKGROUND,
        )

    def _build_shell(self) -> None:
        shell = tk.Frame(self.root, background=BACKGROUND)
        shell.pack(fill=tk.BOTH, expand=True)
        sidebar = tk.Frame(shell, background=SIDEBAR, width=190)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        content = tk.Frame(shell, background=BACKGROUND)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            sidebar,
            text="SHAZAM\nFOR VRC",
            background=SIDEBAR,
            foreground=TEXT,
            justify=tk.LEFT,
            anchor=tk.W,
            font=("Segoe UI Semibold", 15),
        ).pack(fill=tk.X, padx=20, pady=(25, 28))

        for page_name, label in (
            ("listen", "Listen & Track Log"),
            ("settings", "Settings"),
            ("about", "About"),
        ):
            button = tk.Button(
                sidebar,
                text=label,
                command=lambda name=page_name: self._show_page(name),
                anchor=tk.W,
                background=SIDEBAR,
                activebackground=PANEL,
                foreground=MUTED,
                activeforeground=TEXT,
                relief=tk.FLAT,
                borderwidth=0,
                highlightthickness=0,
                padx=18,
                pady=12,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            button.pack(fill=tk.X, padx=9, pady=2)
            self._nav_buttons[page_name] = button

        tk.Label(
            sidebar,
            text="Clean stream recognition\nfor VRChat",
            background=SIDEBAR,
            foreground=FAINT,
            justify=tk.LEFT,
            anchor=tk.SW,
            font=("Segoe UI", 9),
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=22)

        updates_button = tk.Button(
            sidebar,
            text="Updates & changes",
            command=lambda: self._show_page("updates"),
            anchor=tk.W,
            background=SIDEBAR,
            activebackground=PANEL,
            foreground=MUTED,
            activeforeground=TEXT,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=18,
            pady=12,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        updates_button.pack(side=tk.BOTTOM, fill=tk.X, padx=9, pady=2)
        self._nav_buttons["updates"] = updates_button

        for name in ("listen", "settings", "about", "updates"):
            page = tk.Frame(content, background=BACKGROUND)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._pages[name] = page
        self._build_listen_page(self._pages["listen"])
        self._build_settings_page(self._pages["settings"])
        self._build_about_page(self._pages["about"])
        self._build_updates_page(self._pages["updates"])

    def _show_page(self, page_name: str) -> None:
        self._pages[page_name].tkraise()
        for name, button in self._nav_buttons.items():
            active = name == page_name
            button.configure(
                background=PANEL if active else SIDEBAR,
                foreground=TEXT if active else MUTED,
            )

    def _page_heading(self, parent: tk.Widget, title: str, subtitle: str) -> None:
        tk.Label(
            parent,
            text=title,
            background=BACKGROUND,
            foreground=TEXT,
            anchor=tk.W,
            font=("Segoe UI Semibold", 22),
        ).pack(fill=tk.X)
        tk.Label(
            parent,
            text=subtitle,
            background=BACKGROUND,
            foreground=MUTED,
            anchor=tk.W,
            font=("Segoe UI", 10),
        ).pack(fill=tk.X, pady=(3, 18))

    def _section(
        self,
        parent: tk.Widget,
        title: str,
        description: str = "",
    ) -> tk.Frame:
        card = tk.Frame(parent, background=PANEL, padx=18, pady=16)
        card.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            card,
            text=title,
            background=PANEL,
            foreground=TEXT,
            anchor=tk.W,
            font=("Segoe UI Semibold", 12),
        ).pack(fill=tk.X)
        if description:
            tk.Label(
                card,
                text=description,
                background=PANEL,
                foreground=MUTED,
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=760,
                font=("Segoe UI", 9),
            ).pack(fill=tk.X, pady=(3, 12))
        body = tk.Frame(card, background=PANEL)
        body.pack(fill=tk.X)
        return body

    def _build_listen_page(self, page: tk.Frame) -> None:
        outer = tk.Frame(page, background=BACKGROUND, padx=28, pady=24)
        outer.pack(fill=tk.BOTH, expand=True)
        self._page_heading(
            outer,
            "Listen & Track Log",
            "Recognize what is playing and keep a private local log with VRChat context.",
        )

        action_card = tk.Frame(outer, background=PANEL, padx=18, pady=16)
        action_card.pack(fill=tk.X)
        self.listen_button = ttk.Button(
            action_card,
            text="Listen now",
            command=self._start_listening,
            style="Accent.TButton",
        )
        self.listen_button.pack(side=tk.LEFT)
        self.player_debug_button = ttk.Button(
            action_card,
            text="Check current player",
            command=self._open_player_debug,
            style="Secondary.TButton",
        )
        self.player_debug_button.pack(side=tk.LEFT, padx=(10, 0))
        self.progress = ttk.Progressbar(
            action_card,
            mode="indeterminate",
            length=150,
            style="Teal.Horizontal.TProgressbar",
        )
        self.progress.pack(side=tk.LEFT, padx=(16, 0))
        tk.Label(
            action_card,
            textvariable=self.status_text,
            background=PANEL,
            foreground=MUTED,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=500,
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0))

        tk.Label(
            outer,
            text="Track log",
            background=BACKGROUND,
            foreground=TEXT,
            anchor=tk.W,
            font=("Segoe UI Semibold", 14),
        ).pack(fill=tk.X, pady=(22, 9))

        log_toolbar = tk.Frame(outer, background=BACKGROUND)
        log_toolbar.pack(fill=tk.X, pady=(0, 10))
        filter_buttons = tk.Frame(log_toolbar, background=BACKGROUND)
        filter_buttons.pack(side=tk.LEFT)
        self._build_log_toggle(filter_buttons, "tracks", "Tracks", self.show_tracks, 7)
        self._build_log_toggle(filter_buttons, "mixes", "Mixes", self.show_mixes, 7)
        self._build_log_toggle(filter_buttons, "live", "Live", self.show_live, 6)
        self._build_log_toggle(
            filter_buttons,
            "details",
            "Expandable details",
            self.expand_log_details,
            18,
        )
        self._build_log_toggle(filter_buttons, "errors", "Error", self.show_errors, 7)

        log_actions = tk.Frame(log_toolbar, background=BACKGROUND)
        log_actions.pack(side=tk.RIGHT)
        ttk.Button(
            log_actions,
            text="Clear log",
            command=self._clear_log,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)
        self.copy_whole_log_button = ttk.Button(
            log_actions,
            text="Copy whole list",
            command=self._copy_whole_log,
            width=14,
            style="LogToggleOff.TButton",
        )
        self.copy_whole_log_button.pack(side=tk.LEFT, padx=(8, 0))

        history_container = tk.Frame(outer, background=BACKGROUND)
        history_container.pack(fill=tk.BOTH, expand=True)
        self.history_canvas = tk.Canvas(
            history_container,
            background=BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
        )
        history_scrollbar = ttk.Scrollbar(
            history_container,
            orient=tk.VERTICAL,
            command=self.history_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.history_rows = tk.Frame(self.history_canvas, background=BACKGROUND)
        self._history_window = self.history_canvas.create_window(
            (0, 0),
            window=self.history_rows,
            anchor=tk.NW,
        )
        self.history_canvas.configure(yscrollcommand=history_scrollbar.set)
        self.history_rows.bind("<Configure>", self._resize_history_scrollregion)
        self.history_canvas.bind("<Configure>", self._resize_history_width)
        self.history_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        history_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._bind_mousewheel_tree(history_container, self.history_canvas)

    def _build_log_toggle(
        self,
        parent: tk.Widget,
        name: str,
        text: str,
        variable: tk.BooleanVar,
        width: int,
    ) -> None:
        button = ttk.Button(
            parent,
            text=text,
            command=lambda: self._toggle_log_option(variable),
            width=width,
            style="LogToggleOff.TButton",
        )
        button.pack(side=tk.LEFT, padx=(0, 6))
        self._log_filter_buttons[name] = button

    def _build_settings_page(self, page: tk.Frame) -> None:
        canvas, inner = self._create_scrollable_page(page)

        self._page_heading(
            inner,
            "Settings",
            "Recognition, advanced inputs and outputs, and portable storage locations.",
        )
        self._build_recognition_settings(inner)
        self._build_history_settings(inner)
        self._build_input_settings(inner)
        self._build_output_settings(inner)
        self._build_update_settings(inner)
        self._build_storage_settings(inner)
        self._build_debug_settings(inner)

        save_row = tk.Frame(inner, background=BACKGROUND)
        save_row.pack(fill=tk.X, pady=(2, 24))
        save_button = ttk.Button(
            save_row,
            text="Save settings",
            command=self._save_settings,
            style="Accent.TButton",
        )
        save_button.pack(side=tk.LEFT)
        tk.Label(
            save_row,
            textvariable=self.settings_status_text,
            background=BACKGROUND,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(14, 0))
        self._register_setting(save_button)
        self._bind_mousewheel_tree(inner, canvas)

    def _build_recognition_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Recognition",
            "Try clean player audio first, with an optional final attempt using the audio "
            "currently playing through Windows.",
        )
        self._field_label(body, "Record seconds", 0, 0)
        duration = ttk.Spinbox(
            body,
            from_=MIN_RECORD_SECONDS,
            to=MAX_RECORD_SECONDS,
            increment=1,
            width=9,
            textvariable=self.record_seconds,
            style="Dark.TSpinbox",
        )
        duration.grid(row=1, column=0, sticky=tk.W, padx=(0, 22))
        self._field_label(body, "Clean retries (when fallback is off)", 0, 1)
        retries = ttk.Spinbox(
            body,
            from_=0,
            to=MAX_RETRY_COUNT,
            increment=1,
            width=9,
            textvariable=self.retry_count,
            style="Dark.TSpinbox",
        )
        retries.grid(row=1, column=1, sticky=tk.W, padx=(0, 22))
        keep_sample = ttk.Checkbutton(
            body,
            text="Keep latest recording for debugging",
            variable=self.keep_last_sample,
            style="Dark.TCheckbutton",
        )
        keep_sample.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(14, 0))
        system_audio_fallback = ttk.Checkbutton(
            body,
            text="Use VRChat/computer audio for the third attempt",
            variable=self.system_audio_fallback_enabled,
            style="Dark.TCheckbutton",
        )
        system_audio_fallback.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky=tk.W,
            pady=(14, 0),
        )
        tk.Label(
            body,
            text=(
                "When enabled, Listen makes two clean-stream attempts, then records the "
                "default Windows output once. For a mix whose player time is unavailable, "
                "it goes directly to this fallback. The recording may include VRChat voices, "
                "world sounds, notifications, and other applications. "
                "The temporary recording is deleted after recognition unless debug retention "
                "is enabled."
            ),
            background=PANEL,
            foreground=FAINT,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=760,
            font=("Segoe UI", 8),
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))
        topmost = ttk.Checkbutton(
            body,
            text="Keep app always on top",
            variable=self.always_on_top,
            command=self._apply_topmost,
            style="Dark.TCheckbutton",
        )
        topmost.grid(row=2, column=2, sticky=tk.W, pady=(14, 0))
        for widget in (
            duration,
            retries,
            keep_sample,
            system_audio_fallback,
            topmost,
        ):
            self._register_setting(widget)

    def _build_history_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Track log",
            "Copied state is stored in the selected history file and stays after restarting.",
        )
        copied_indicators = ttk.Checkbutton(
            body,
            text="Show ✓ Copied indicators",
            variable=self.show_copied_indicators,
            style="Dark.TCheckbutton",
        )
        copied_indicators.pack(anchor=tk.W)
        self._register_setting(copied_indicators)

    def _build_input_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Advanced inputs",
            "Controller, keyboard, and avatar OSC inputs call the same listening operation "
            "as the button on the first page.",
        )
        steamvr_enabled = ttk.Checkbutton(
            body,
            text="Enable SteamVR controller input",
            variable=self.steamvr_input_enabled,
            style="Dark.TCheckbutton",
        )
        steamvr_enabled.grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self._field_label(body, "Controller button", 1, 0, pady=(14, 4))
        controller_button = ttk.Combobox(
            body,
            values=list(CONTROLLER_BUTTON_LABELS.values()),
            textvariable=self.controller_button,
            state="readonly",
            width=24,
            style="Dark.TCombobox",
        )
        controller_button.grid(row=2, column=0, sticky=tk.W, padx=(0, 18))
        self._field_label(body, "Gesture", 1, 1, pady=(14, 4))
        trigger_mode = ttk.Combobox(
            body,
            values=list(TRIGGER_MODE_LABELS.values()),
            textvariable=self.controller_trigger_mode,
            state="readonly",
            width=18,
            style="Dark.TCombobox",
        )
        trigger_mode.grid(row=2, column=1, sticky=tk.W, padx=(0, 18))
        self._field_label(body, "Long-press seconds", 1, 2, pady=(14, 4))
        hold_seconds = ttk.Spinbox(
            body,
            from_=MIN_CONTROLLER_HOLD_SECONDS,
            to=MAX_CONTROLLER_HOLD_SECONDS,
            increment=0.1,
            width=9,
            textvariable=self.controller_hold_seconds,
            style="Dark.TSpinbox",
        )
        hold_seconds.grid(row=2, column=2, sticky=tk.W, padx=(0, 18))
        self._field_label(body, "Double-press window", 1, 3, pady=(14, 4))
        double_press = ttk.Spinbox(
            body,
            from_=MIN_CONTROLLER_DOUBLE_PRESS_SECONDS,
            to=MAX_CONTROLLER_DOUBLE_PRESS_SECONDS,
            increment=0.1,
            width=9,
            textvariable=self.controller_double_press_seconds,
            style="Dark.TSpinbox",
        )
        double_press.grid(row=2, column=3, sticky=tk.W)

        keyboard_enabled = ttk.Checkbutton(
            body,
            text="Enable global keyboard shortcut",
            variable=self.keyboard_hotkey_enabled,
            style="Dark.TCheckbutton",
        )
        keyboard_enabled.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(18, 0))
        self._field_label(body, "Keyboard key", 3, 2, pady=(18, 4))
        keyboard_hotkey = ttk.Combobox(
            body,
            values=sorted(KEYBOARD_HOTKEYS, key=lambda item: int(item[1:])),
            textvariable=self.keyboard_hotkey,
            state="readonly",
            width=9,
            style="Dark.TCombobox",
        )
        keyboard_hotkey.grid(row=4, column=2, sticky=tk.W)

        osc_enabled = ttk.Checkbutton(
            body,
            text="Enable VRChat avatar OSC trigger",
            variable=self.osc_input_enabled,
            style="Dark.TCheckbutton",
        )
        osc_enabled.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(18, 0))
        self._field_label(body, "Avatar parameter", 5, 2, pady=(18, 4))
        osc_parameter = ttk.Entry(
            body,
            textvariable=self.osc_input_parameter,
            width=25,
            style="Dark.TEntry",
        )
        osc_parameter.grid(row=6, column=2, sticky=tk.W, padx=(0, 18))
        self._field_label(body, "Receive port", 5, 3, pady=(18, 4))
        osc_port = ttk.Spinbox(
            body,
            from_=1,
            to=65_535,
            increment=1,
            width=9,
            textvariable=self.osc_input_port,
            style="Dark.TSpinbox",
        )
        osc_port.grid(row=6, column=3, sticky=tk.W)
        tk.Label(
            body,
            text="Use this Bool in a Button, Toggle, or Contact Receiver. Enable VRChat OSC.",
            background=PANEL,
            foreground=FAINT,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=400,
            font=("Segoe UI", 9),
        ).grid(row=6, column=0, columnspan=2, sticky=tk.W, padx=(24, 18))
        tk.Label(
            body,
            textvariable=self.input_status_text,
            background=PANEL,
            foreground=MUTED,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=780,
            font=("Segoe UI", 9),
        ).grid(row=7, column=0, columnspan=4, sticky=tk.W, pady=(14, 0))
        for widget, state in (
            (steamvr_enabled, "normal"),
            (controller_button, "readonly"),
            (trigger_mode, "readonly"),
            (hold_seconds, "normal"),
            (double_press, "normal"),
            (keyboard_enabled, "normal"),
            (keyboard_hotkey, "readonly"),
            (osc_enabled, "normal"),
            (osc_parameter, "normal"),
            (osc_port, "normal"),
        ):
            self._register_setting(widget, state)

    def _build_output_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Advanced outputs",
            "Optional local outputs do not block recognition if their target app is closed.",
        )
        xsoverlay = ttk.Checkbutton(
            body,
            text="XSOverlay notifications",
            variable=self.xsoverlay_notifications_enabled,
            style="Dark.TCheckbutton",
        )
        xsoverlay.grid(row=0, column=0, sticky=tk.W)
        tk.Label(
            body,
            text="Start, result, no-match and error notifications inside VR.",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky=tk.W, padx=(20, 0))
        chatbox = ttk.Checkbutton(
            body,
            text="VRChat chatbox track results",
            variable=self.vrchat_chatbox_enabled,
            style="Dark.TCheckbutton",
        )
        chatbox.grid(row=1, column=0, sticky=tk.W, pady=(12, 0))
        tk.Label(
            body,
            text="Uses live-stream, song-in-mix, song, or mix wording; others may see it.",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky=tk.W, padx=(20, 0), pady=(12, 0))
        self._register_setting(xsoverlay)
        self._register_setting(chatbox)

    def _build_update_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Updates",
            "Checks the public release page without a GitHub login or token. Downloads and "
            "installation always require confirmation.",
        )
        automatic_checks = ttk.Checkbutton(
            body,
            text="Check for updates automatically when the app starts",
            variable=self.automatic_update_checks,
            style="Dark.TCheckbutton",
        )
        automatic_checks.pack(anchor=tk.W)
        self._register_setting(automatic_checks)

    def _build_storage_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Storage locations",
            "Leave a field empty to use Windows' normal per-user AppData location. "
            "Each friend can choose their own locations after receiving the app.",
        )
        self._field_label(body, "Track log file", 0, 0)
        history_entry = ttk.Entry(
            body,
            textvariable=self.history_path,
            style="Dark.TEntry",
        )
        history_entry.grid(row=1, column=0, sticky=tk.EW, padx=(0, 8))
        history_button = ttk.Button(
            body,
            text="Choose…",
            command=self._choose_history_path,
            style="Secondary.TButton",
        )
        history_button.grid(row=1, column=1)
        tk.Label(
            body,
            text=f"Default: {default_history_path()}",
            background=PANEL,
            foreground=FAINT,
            anchor=tk.W,
            font=("Segoe UI", 8),
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 12))

        self._field_label(body, "Debug recording folder", 3, 0)
        debug_entry = ttk.Entry(
            body,
            textvariable=self.debug_directory,
            style="Dark.TEntry",
        )
        debug_entry.grid(row=4, column=0, sticky=tk.EW, padx=(0, 8))
        debug_button = ttk.Button(
            body,
            text="Choose…",
            command=self._choose_debug_directory,
            style="Secondary.TButton",
        )
        debug_button.grid(row=4, column=1)
        tk.Label(
            body,
            text=f"Default: {default_debug_sample_path().parent}",
            background=PANEL,
            foreground=FAINT,
            anchor=tk.W,
            font=("Segoe UI", 8),
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        body.columnconfigure(0, weight=1)
        for widget in (history_entry, history_button, debug_entry, debug_button):
            self._register_setting(widget)

    def _build_debug_settings(self, parent: tk.Widget) -> None:
        body = self._section(
            parent,
            "Debug recording",
            "Only one WAV is retained, and only when debug retention is enabled.",
        )
        tk.Label(
            body,
            textvariable=self.debug_text,
            background=PANEL,
            foreground=MUTED,
            anchor=tk.W,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.play_sample_button = ttk.Button(
            body,
            text="Play last",
            command=self._play_last_sample,
            style="Secondary.TButton",
        )
        self.play_sample_button.pack(side=tk.LEFT, padx=(8, 0))
        self.delete_sample_button = ttk.Button(
            body,
            text="Delete",
            command=self._delete_last_sample,
            style="Secondary.TButton",
        )
        self.delete_sample_button.pack(side=tk.LEFT, padx=(6, 0))

    def _build_about_page(self, page: tk.Frame) -> None:
        canvas, outer = self._create_scrollable_page(page)
        self._page_heading(
            outer,
            "About",
            "What Shazam for VRC does, how it works, and what to expect.",
        )
        body = self._section(outer, f"Shazam for VRC {__version__} by Szeb")
        about_text = (
            "This app reads the current VRChat output log, finds the active media player, "
            "records a short clean sample from the player stream, and asks Shazam's online "
            "recognition service to identify the song. It avoids recording voices and world "
            "audio when a supported clean stream is available. An explicit setting can use "
            "the complete Windows output as one final fallback attempt.\n\n"
            "Recognition uses ShazamIO to communicate with Shazam's service. This is not an "
            "official Shazam or Apple application. Recognition can fail or return no match "
            "because of network problems, service changes, songs missing from the catalogue, "
            "quiet sections, remixes, background noise in the source, or samples that are too "
            "short. Retrying during a clearer part of the song may help.\n\n"
            "For prerecorded YouTube media, exact song metadata is used when available. Short "
            "song videos are sampled at representative sections; long mixes use an automatic "
            "low-confidence estimate based on the AVPro open time. The player debug view shows "
            "that estimate and its limitations."
        )
        tk.Label(
            body,
            text=about_text,
            background=PANEL,
            foreground=MUTED,
            justify=tk.LEFT,
            anchor=tk.NW,
            wraplength=780,
            font=("Segoe UI", 10),
        ).pack(fill=tk.X)

        privacy = self._section(outer, "Privacy and saved data")
        tk.Label(
            privacy,
            text=(
                "• VRChat logs are read locally and are never copied into the track log.\n"
                "• Temporary audio is deleted unless debug retention is enabled.\n"
                "• The optional computer-audio fallback may capture voices, world sounds, "
                "notifications, and other applications.\n"
                "• History stores track details and VRChat context where you choose.\n"
                "• XSOverlay and VRChat OSC outputs stay on the local computer."
            ),
            background=PANEL,
            foreground=MUTED,
            justify=tk.LEFT,
            anchor=tk.NW,
            font=("Segoe UI", 10),
        ).pack(fill=tk.X)
        self._bind_mousewheel_tree(outer, canvas)

    def _build_updates_page(self, page: tk.Frame) -> None:
        canvas, outer = self._create_scrollable_page(page)
        self._page_heading(
            outer,
            "Updates & changes",
            "Check for signed releases and see what changed in each installed version.",
        )
        controls = self._section(
            outer,
            "Software update",
            "Every automatic installer must match the private release signature held by Szeb.",
        )
        tk.Label(
            controls,
            textvariable=self.update_status_text,
            background=PANEL,
            foreground=TEXT,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=760,
            font=("Segoe UI", 10),
        ).pack(fill=tk.X)
        self.update_progress = ttk.Progressbar(
            controls,
            mode="determinate",
            maximum=100,
            style="Teal.Horizontal.TProgressbar",
        )
        self.update_progress.pack(fill=tk.X, pady=(12, 4))
        tk.Label(
            controls,
            textvariable=self.update_progress_text,
            background=PANEL,
            foreground=MUTED,
            anchor=tk.W,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X)
        button_row = tk.Frame(controls, background=PANEL)
        button_row.pack(fill=tk.X, pady=(13, 0))
        self.check_update_button = ttk.Button(
            button_row,
            text="Check for updates",
            command=lambda: self._start_update_check(silent=False),
            style="Secondary.TButton",
        )
        self.check_update_button.pack(side=tk.LEFT)
        self.install_update_button = ttk.Button(
            button_row,
            text="Download and install",
            command=self._download_available_update,
            style="Accent.TButton",
            state=tk.DISABLED,
        )
        self.install_update_button.pack(side=tk.LEFT, padx=(8, 0))
        self.open_release_button = ttk.Button(
            button_row,
            text="Open release page",
            command=self._open_update_release_page,
            style="Secondary.TButton",
            state=tk.DISABLED,
        )
        self.open_release_button.pack(side=tk.LEFT, padx=(8, 0))

        for notes in RELEASE_NOTES:
            body = self._section(outer, f"Version {notes.version} — {notes.title}")
            tk.Label(
                body,
                text="\n".join(f"• {change}" for change in notes.changes),
                background=PANEL,
                foreground=MUTED,
                justify=tk.LEFT,
                anchor=tk.NW,
                wraplength=780,
                font=("Segoe UI", 10),
            ).pack(fill=tk.X)
        self._bind_mousewheel_tree(outer, canvas)

    def _create_scrollable_page(self, page: tk.Frame) -> tuple[tk.Canvas, tk.Frame]:
        canvas = tk.Canvas(page, background=BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            page,
            orient=tk.VERTICAL,
            command=canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        inner = tk.Frame(canvas, background=BACKGROUND, padx=28, pady=24)
        window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        inner.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )
        canvas.bind(
            "<MouseWheel>",
            lambda event: self._scroll_canvas(event, canvas),
        )
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return canvas, inner

    def _bind_mousewheel_tree(self, widget: tk.Widget, canvas: tk.Canvas) -> None:
        widget.bind(
            "<MouseWheel>",
            lambda event: self._scroll_canvas(event, canvas),
        )
        for child in widget.winfo_children():
            self._bind_mousewheel_tree(child, canvas)

    @staticmethod
    def _scroll_canvas(event: tk.Event[tk.Misc], canvas: tk.Canvas) -> str | None:
        units = _mousewheel_scroll_units(event.delta)
        if units == 0:
            return None
        canvas.yview_scroll(units, "units")
        return "break"

    def _field_label(
        self,
        parent: tk.Widget,
        text: str,
        row: int,
        column: int,
        *,
        pady: tuple[int, int] = (0, 4),
    ) -> None:
        tk.Label(
            parent,
            text=text,
            background=PANEL,
            foreground=MUTED,
            anchor=tk.W,
            font=("Segoe UI", 9),
        ).grid(row=row, column=column, sticky=tk.W, pady=pady)

    def _register_setting(self, widget: tk.Widget, idle_state: str = "normal") -> None:
        self._settings_widgets.append((widget, idle_state))

    def _resize_history_scrollregion(self, _event: tk.Event[tk.Misc]) -> None:
        self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))

    def _resize_history_width(self, event: tk.Event[tk.Misc]) -> None:
        self.history_canvas.itemconfigure(self._history_window, width=event.width)

    def _toggle_log_option(self, variable: tk.BooleanVar) -> None:
        variable.set(not variable.get())
        self._reset_copy_whole_log_state()
        self._refresh_history()

    def _reset_copy_whole_log_state(self) -> None:
        self.copy_whole_log_button.configure(style="LogToggleOff.TButton")

    def _update_log_toggle_styles(self) -> None:
        states = {
            "tracks": self.show_tracks.get(),
            "mixes": self.show_mixes.get(),
            "live": self.show_live.get(),
            "details": self.expand_log_details.get(),
            "errors": self.show_errors.get(),
        }
        for name, selected in states.items():
            self._log_filter_buttons[name].configure(
                style="LogToggleOn.TButton" if selected else "LogToggleOff.TButton"
            )

    def _visible_log_entries(self) -> list[HistoryEntry]:
        enabled_kinds: set[TrackLogKind] = set()
        if self.show_tracks.get():
            enabled_kinds.add(TrackLogKind.TRACK)
        if self.show_mixes.get():
            enabled_kinds.add(TrackLogKind.MIX)
        if self.show_live.get():
            enabled_kinds.add(TrackLogKind.LIVE)
        if self.show_errors.get():
            enabled_kinds.add(TrackLogKind.ERROR)
        return [entry for entry in self.history_entries if entry.kind in enabled_kinds]

    def _refresh_history(self) -> None:
        self._update_log_toggle_styles()
        for child in self.history_rows.winfo_children():
            child.destroy()
        visible_entries = self._visible_log_entries()
        if not visible_entries:
            empty_message = (
                "The track log is empty."
                if not self.history_entries
                else "No entries match the selected filters."
            )
            tk.Label(
                self.history_rows,
                text=empty_message,
                background=BACKGROUND,
                foreground=MUTED,
                anchor=tk.W,
                font=("Segoe UI", 10),
                pady=16,
            ).pack(fill=tk.X)
            self._bind_mousewheel_tree(self.history_rows, self.history_canvas)
            return

        for entry in visible_entries:
            row = tk.Frame(self.history_rows, background=PANEL, padx=16, pady=12)
            row.pack(fill=tk.X, pady=(0, 8), padx=(0, 8))
            title_row = tk.Frame(row, background=PANEL)
            title_row.pack(fill=tk.X)
            badge_foreground = "#ffb74d" if entry.kind is TrackLogKind.ERROR else ACCENT
            tk.Label(
                title_row,
                text=entry.kind.value.title(),
                background=INPUT_BACKGROUND,
                foreground=badge_foreground,
                padx=7,
                pady=2,
                font=("Segoe UI Semibold", 8),
            ).pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(
                title_row,
                text=entry.primary_text,
                background=PANEL,
                foreground=TEXT,
                anchor=tk.W,
                font=("Segoe UI Semibold", 11),
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            if entry.notice:
                self._add_notice_icon(title_row, entry.notice)

            if entry.is_copyable:
                tk.Label(
                    title_row,
                    text=(
                        "✓ Copied"
                        if self.config.show_copied_indicators and entry.copied_at
                        else ""
                    ),
                    background=PANEL,
                    foreground=ACCENT,
                    font=("Segoe UI Semibold", 9),
                    width=8,
                    anchor=tk.E,
                ).pack(side=tk.LEFT, padx=(8, 0))
                ttk.Button(
                    title_row,
                    text="Copy track",
                    command=lambda item=entry: self._copy_entry(item),
                    style="Secondary.TButton",
                ).pack(side=tk.LEFT, padx=(6, 0))
                if entry.link:
                    ttk.Button(
                        title_row,
                        text="Open link",
                        command=lambda item=entry: self._open_entry_link(item),
                        style="Secondary.TButton",
                    ).pack(side=tk.LEFT, padx=(6, 0))
                if entry.group_url:
                    ttk.Button(
                        title_row,
                        text="Open group",
                        command=lambda item=entry: self._open_group_link(item),
                        style="Secondary.TButton",
                    ).pack(side=tk.LEFT, padx=(6, 0))

            if entry.kind is TrackLogKind.ERROR and entry.error_message:
                tk.Label(
                    row,
                    text=entry.error_message,
                    background=PANEL,
                    foreground=MUTED,
                    anchor=tk.W,
                    justify=tk.LEFT,
                    wraplength=760,
                    font=("Segoe UI", 9),
                ).pack(fill=tk.X, pady=(7, 0))
            if self.expand_log_details.get():
                self._build_log_details(row, entry)
        self._bind_mousewheel_tree(self.history_rows, self.history_canvas)

    def _add_notice_icon(self, parent: tk.Widget, notice: str) -> None:
        icon = tk.Canvas(
            parent,
            width=18,
            height=18,
            background=PANEL,
            highlightthickness=0,
            cursor="question_arrow",
        )
        icon.create_oval(2, 2, 16, 16, outline=ACCENT, width=1)
        icon.create_text(9, 9, text="!", fill=ACCENT, font=("Segoe UI Semibold", 9))
        icon.pack(side=tk.LEFT, padx=(7, 0))
        _HoverTooltip(icon, notice)

    def _build_log_details(self, row: tk.Widget, entry: HistoryEntry) -> None:
        details = [f"Detected: {entry.display_time}", f"World: {entry.world_name}"]
        if entry.display_group:
            details.append(f"Group: {entry.display_group}")
        if entry.instance_type:
            details.append(f"Access: {entry.instance_type}")
        details.append(f"Media source: {entry.provider.replace('_', ' ').title()}")
        if entry.recognition_provider:
            details.append(
                "Recognition: " + entry.recognition_provider.replace("_", " ").title()
            )
        if entry.recording_attempts:
            suffix = "attempt" if entry.recording_attempts == 1 else "attempts"
            details.append(f"Recordings: {entry.recording_attempts} {suffix}")
        if entry.notice:
            details.append(entry.notice)
        tk.Label(
            row,
            text="\n".join(details),
            background=PANEL,
            foreground=MUTED,
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=760,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(8, 0))

    def _copy_whole_log(self) -> None:
        entries = [entry for entry in self._visible_log_entries() if entry.is_copyable]
        clipboard_text = copyable_log_text(entries)
        if not clipboard_text:
            self.status_text.set("There are no visible track-log entries to copy.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(clipboard_text)
        self.root.update_idletasks()
        self.copy_whole_log_button.configure(style="LogToggleOn.TButton")
        noun = "entry" if len(entries) == 1 else "entries"
        self.status_text.set(f"Copied {len(entries)} track-log {noun}.")

    def _clear_log(self) -> None:
        if not self.history_entries:
            self.status_text.set("The track log is already empty.")
            return
        if not messagebox.askyesno(
            "Clear track log",
            "Clear all tracks, mixes, live entries, and errors from the local track log?",
            parent=self.root,
        ):
            return
        try:
            self.history_store.save([])
        except HistoryError as error:
            logger.warning("Could not clear the track log: %s", error)
            messagebox.showerror("Clear track log", str(error), parent=self.root)
            return
        self.history_entries = []
        self._reset_copy_whole_log_state()
        self.status_text.set("Track log cleared.")
        self._refresh_history()

    def _copy_entry(self, entry: HistoryEntry) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(entry.copy_text)
        self.root.update_idletasks()
        for index, current in enumerate(self.history_entries):
            if current is entry:
                self.history_entries[index] = entry.with_copied_time()
                break
        try:
            self.history_store.save(self.history_entries)
        except HistoryError as error:
            logger.warning("Could not save copied history state: %s", error)
            self.status_text.set(f"Copied {entry.copy_text}, but the indicator was not saved.")
        else:
            self.status_text.set(f"Copied: {entry.copy_text}")
        self._refresh_history()

    def _open_entry_link(self, entry: HistoryEntry) -> None:
        if not entry.link:
            return
        try:
            opened = webbrowser.open(entry.link)
        except webbrowser.Error:
            opened = False
        if not opened:
            messagebox.showerror(
                "Open link",
                "Windows could not open this recognition link.",
                parent=self.root,
            )

    def _open_group_link(self, entry: HistoryEntry) -> None:
        group_url = entry.group_url
        if not group_url:
            return
        try:
            opened = webbrowser.open(group_url)
        except webbrowser.Error:
            opened = False
        if not opened:
            messagebox.showerror(
                "Open group",
                "Windows could not open this VRChat group page.",
                parent=self.root,
            )

    def _choose_history_path(self) -> None:
        current = (
            Path(self.history_path.get()) if self.history_path.get() else default_history_path()
        )
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Choose track log file",
            initialdir=str(current.parent),
            initialfile=current.name,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            self.history_path.set(selected)

    def _choose_debug_directory(self) -> None:
        current = (
            Path(self.debug_directory.get())
            if self.debug_directory.get()
            else default_debug_sample_path().parent
        )
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose debug recording folder",
            initialdir=str(current),
        )
        if selected:
            self.debug_directory.set(selected)

    def _read_controls(self) -> AppConfig:
        try:
            record_seconds = float(self.record_seconds.get().strip())
            retry_count = int(self.retry_count.get().strip())
            controller_hold_seconds = float(self.controller_hold_seconds.get().strip())
            controller_double_press_seconds = float(
                self.controller_double_press_seconds.get().strip()
            )
            osc_input_port = int(self.osc_input_port.get().strip())
        except ValueError:
            raise ValueError(
                "Recording, retry, long-press, double-press, and OSC port values must be "
                "numbers."
            ) from None
        osc_input_parameter = normalize_avatar_parameter_name(
            self.osc_input_parameter.get()
        )
        controller_button = _value_for_label(
            CONTROLLER_BUTTON_LABELS,
            self.controller_button.get(),
        )
        trigger_mode = _value_for_label(
            TRIGGER_MODE_LABELS,
            self.controller_trigger_mode.get(),
        )
        return AppConfig(
            record_seconds=record_seconds,
            retry_count=retry_count,
            system_audio_fallback_enabled=self.system_audio_fallback_enabled.get(),
            keep_last_sample=self.keep_last_sample.get(),
            always_on_top=self.always_on_top.get(),
            show_copied_indicators=self.show_copied_indicators.get(),
            automatic_update_checks=self.automatic_update_checks.get(),
            steamvr_input_enabled=self.steamvr_input_enabled.get(),
            controller_button=controller_button,
            controller_trigger_mode=trigger_mode,
            controller_hold_seconds=controller_hold_seconds,
            controller_double_press_seconds=controller_double_press_seconds,
            keyboard_hotkey_enabled=self.keyboard_hotkey_enabled.get(),
            keyboard_hotkey=self.keyboard_hotkey.get(),
            osc_input_enabled=self.osc_input_enabled.get(),
            osc_input_parameter=osc_input_parameter,
            osc_input_port=osc_input_port,
            xsoverlay_notifications_enabled=self.xsoverlay_notifications_enabled.get(),
            vrchat_chatbox_enabled=self.vrchat_chatbox_enabled.get(),
            history_path=self.history_path.get().strip(),
            debug_directory=self.debug_directory.get().strip(),
        )

    def _save_settings(self, *, show_confirmation: bool = True) -> AppConfig | None:
        try:
            config = self._read_controls()
            new_history_store = HistoryStore(_history_path_for_config(config))
            new_debug_path = _debug_sample_path_for_config(config)
            history_changed = new_history_store.path != self.history_store.path
            if history_changed:
                if new_history_store.path.exists():
                    new_history_entries = new_history_store.load()
                else:
                    new_history_store.save(self.history_entries)
                    new_history_entries = list(self.history_entries)
            else:
                new_history_entries = self.history_entries
            save_config(config)
            if not config.keep_last_sample:
                delete_last_sample(self.debug_sample_path)
                if new_debug_path != self.debug_sample_path:
                    delete_last_sample(new_debug_path)
        except (ValueError, ConfigError, HistoryError, DebugSampleError) as error:
            messagebox.showerror("Settings", str(error), parent=self.root)
            return None

        previous_config = self.config
        self.config = config
        self.history_store = new_history_store
        self.history_entries = new_history_entries
        self.debug_sample_path = new_debug_path
        self._apply_topmost()
        self._refresh_history()
        self._refresh_debug_controls()
        if self._input_settings_changed(previous_config, config):
            self._restart_input_listeners(config)
        if show_confirmation:
            self.settings_status_text.set("Settings saved.")
        return config

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top.get())

    def _open_player_debug(self) -> None:
        window = self._player_debug_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
            self._queue_player_debug_refresh()
            return

        window = tk.Toplevel(self.root)
        self._player_debug_window = window
        window.title("Current VRChat player")
        window.geometry("620x470")
        window.minsize(540, 390)
        window.configure(background=BACKGROUND)
        window.attributes("-topmost", self.always_on_top.get())
        window.protocol("WM_DELETE_WINDOW", self._close_player_debug)

        outer = tk.Frame(window, background=BACKGROUND, padx=24, pady=22)
        outer.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            outer,
            text="Current VRChat player",
            background=BACKGROUND,
            foreground=TEXT,
            anchor=tk.W,
            font=("Segoe UI Semibold", 19),
        ).pack(fill=tk.X)
        tk.Label(
            outer,
            text=(
                "This view rereads the VRChat log every two seconds. Prerecorded "
                "positions are estimates because pause, seek, and world sync are not logged."
            ),
            background=BACKGROUND,
            foreground=MUTED,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=560,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(4, 15))

        card = tk.Frame(outer, background=PANEL, padx=18, pady=16)
        card.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            card,
            textvariable=self.player_debug_text,
            background=PANEL,
            foreground=TEXT,
            justify=tk.LEFT,
            anchor=tk.NW,
            wraplength=530,
            font=("Segoe UI", 10),
        ).pack(fill=tk.BOTH, expand=True)
        ttk.Button(
            outer,
            text="Refresh now",
            command=self._queue_player_debug_refresh,
            style="Secondary.TButton",
        ).pack(anchor=tk.E, pady=(12, 0))

        self.player_debug_text.set("Reading the current VRChat player...")
        self._queue_player_debug_refresh()

    def _queue_player_debug_refresh(self) -> None:
        window = self._player_debug_window
        if window is None or not window.winfo_exists():
            return
        if self._player_debug_after_id is not None:
            with suppress(tk.TclError):
                window.after_cancel(self._player_debug_after_id)
            self._player_debug_after_id = None
        if self._player_debug_refreshing:
            return
        self._player_debug_refreshing = True
        worker = threading.Thread(
            target=self._player_debug_worker,
            name="shazam-for-vrc-player-debug",
            daemon=True,
        )
        worker.start()

    def _player_debug_worker(self) -> None:
        try:
            info = self.player_debug_service.inspect()
        except Exception as error:
            logger.info("Current-player inspection failed (%s).", type(error).__name__)
            self.events.put(("player_debug_error", error))
        else:
            self.events.put(("player_debug", info))

    def _handle_player_debug(self, info: PlayerDebugInfo) -> None:
        self._player_debug_refreshing = False
        window = self._player_debug_window
        if window is None or not window.winfo_exists():
            return
        self.player_debug_text.set(_player_debug_summary(info))
        self._schedule_player_debug_refresh()

    def _handle_player_debug_error(self, error: Exception) -> None:
        self._player_debug_refreshing = False
        window = self._player_debug_window
        if window is None or not window.winfo_exists():
            return
        self.player_debug_text.set(
            f"Could not inspect the current player.\n\n{self._friendly_error(error)}"
        )
        self._schedule_player_debug_refresh()

    def _schedule_player_debug_refresh(self) -> None:
        window = self._player_debug_window
        if window is None or not window.winfo_exists():
            return
        self._player_debug_after_id = window.after(
            2_000,
            self._queue_player_debug_refresh,
        )

    def _close_player_debug(self) -> None:
        window = self._player_debug_window
        if window is None:
            return
        if self._player_debug_after_id is not None:
            with suppress(tk.TclError):
                window.after_cancel(self._player_debug_after_id)
            self._player_debug_after_id = None
        self._player_debug_window = None
        window.destroy()

    def _start_update_check(self, *, silent: bool) -> None:
        if self._update_checking or self._update_downloading:
            return
        self._update_checking = True
        self.check_update_button.configure(state=tk.DISABLED)
        self.update_status_text.set(f"Checking for updates to version {__version__}...")
        self.update_progress_text.set("Contacting the public GitHub release page.")
        worker = threading.Thread(
            target=self._update_check_worker,
            args=(silent,),
            name="shazam-for-vrc-update-check",
            daemon=True,
        )
        worker.start()

    def _update_check_worker(self, silent: bool) -> None:
        try:
            result = self._update_client.check(__version__)
        except Exception as error:
            logger.info("Update check failed (%s).", type(error).__name__)
            self.events.put(("update_error", (error, silent)))
        else:
            self.events.put(("update_check", result))

    def _handle_update_check(self, result: UpdateCheckResult) -> None:
        self._update_checking = False
        self.check_update_button.configure(state=tk.NORMAL)
        self.update_progress.configure(value=0)
        self.update_progress_text.set("")
        self._available_update = result.update
        if result.update is not None:
            immutability = (
                "immutable GitHub release"
                if result.update.immutable
                else "signed release"
            )
            self.update_status_text.set(
                f"Version {result.update.version} is available as a verified {immutability}."
            )
            self.install_update_button.configure(state=tk.NORMAL)
            self.open_release_button.configure(state=tk.NORMAL)
            self._nav_buttons["updates"].configure(text="Updates & changes  •")
            self.status_text.set(
                f"Update {result.update.version} is available. Open Updates & changes."
            )
        elif result.latest_version is None:
            self.update_status_text.set(
                f"Installed version: {__version__}. No published release is available yet."
            )
            self.install_update_button.configure(state=tk.DISABLED)
            self.open_release_button.configure(state=tk.DISABLED)
        else:
            self.update_status_text.set(
                f"Installed version {__version__} is up to date."
            )
            self.install_update_button.configure(state=tk.DISABLED)
            self.open_release_button.configure(state=tk.DISABLED)
            self._nav_buttons["updates"].configure(text="Updates & changes")

    def _handle_update_error(self, error: Exception, *, silent: bool) -> None:
        self._update_checking = False
        self.check_update_button.configure(state=tk.NORMAL)
        self.update_progress.configure(value=0)
        self.update_progress_text.set("")
        message = str(error) if isinstance(error, UpdateError) else "The update check failed."
        self.update_status_text.set(message)
        if not silent:
            messagebox.showerror("Updates", message, parent=self.root)

    def _download_available_update(self) -> None:
        update = self._available_update
        if update is None or self._update_downloading:
            return
        if self.is_listening:
            messagebox.showinfo(
                "Listening in progress",
                "Wait for listening to finish before downloading an update.",
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "Download update",
            f"Download Shazam for VRC {update.version}?\n\n"
            "The installer will be checked against Szeb's private release signature before "
            "Windows is allowed to open it.",
            parent=self.root,
        )
        if not confirmed:
            return
        self._update_downloading = True
        self.listen_button.configure(state=tk.DISABLED)
        self.check_update_button.configure(state=tk.DISABLED)
        self.install_update_button.configure(state=tk.DISABLED)
        self.update_status_text.set(f"Downloading version {update.version}...")
        self.update_progress.configure(value=0)
        worker = threading.Thread(
            target=self._update_download_worker,
            args=(update,),
            name="shazam-for-vrc-update-download",
            daemon=True,
        )
        worker.start()

    def _update_download_worker(self, update: UpdateInfo) -> None:
        try:
            downloaded = self._update_client.download(
                update,
                lambda received, total: self.events.put(
                    ("update_progress", (received, total))
                ),
            )
        except Exception as error:
            logger.info("Update download failed (%s).", type(error).__name__)
            self.events.put(("update_download_error", error))
        else:
            self.events.put(("update_downloaded", downloaded))

    def _handle_update_progress(self, received: int, total: int) -> None:
        percent = min(100.0, received / total * 100) if total else 0.0
        self.update_progress.configure(value=percent)
        self.update_progress_text.set(
            f"{received / (1024 * 1024):.1f} of {total / (1024 * 1024):.1f} MiB"
        )

    def _handle_update_downloaded(self, downloaded: DownloadedUpdate) -> None:
        self._update_downloading = False
        self.listen_button.configure(state=tk.NORMAL)
        self.check_update_button.configure(state=tk.NORMAL)
        self.install_update_button.configure(state=tk.NORMAL)
        self.update_progress.configure(value=100)
        self.update_progress_text.set("Download and signature verification completed.")
        self.update_status_text.set(f"Version {downloaded.version} is verified and ready.")
        install = messagebox.askyesno(
            "Install verified update",
            f"Install version {downloaded.version} now?\n\n"
            "Shazam for VRC will close and the normal installer will open. Settings and "
            "history will be preserved.",
            parent=self.root,
        )
        if not install:
            return
        try:
            self._update_client.launch_installer(downloaded)
        except UpdateError as error:
            messagebox.showerror("Updates", str(error), parent=self.root)
            return
        self._on_close()

    def _handle_update_download_error(self, error: Exception) -> None:
        self._update_downloading = False
        self.listen_button.configure(state=tk.NORMAL)
        self.check_update_button.configure(state=tk.NORMAL)
        self.install_update_button.configure(
            state=tk.NORMAL if self._available_update else tk.DISABLED
        )
        self.update_progress.configure(value=0)
        self.update_progress_text.set("")
        message = str(error) if isinstance(error, UpdateError) else "The update download failed."
        self.update_status_text.set(message)
        messagebox.showerror("Updates", message, parent=self.root)

    def _open_update_release_page(self) -> None:
        update = self._available_update
        if update is None:
            return
        try:
            opened = webbrowser.open(update.release_page)
        except webbrowser.Error:
            opened = False
        if not opened:
            messagebox.showerror(
                "Updates",
                "Windows could not open the GitHub release page.",
                parent=self.root,
            )

    def _start_listening(self) -> None:
        if self.is_listening:
            return
        config = self._save_settings(show_confirmation=False)
        if config is None:
            return
        options = ListenOptions(
            record_seconds=config.record_seconds,
            retry_count=config.retry_count,
            use_system_audio_fallback=config.system_audio_fallback_enabled,
            keep_last_sample=config.keep_last_sample,
            debug_sample_path=self.debug_sample_path,
        )
        self._last_listening_stage = ListeningStage.FINDING_PLAYER
        self._set_listening_state(True)
        self._show_page("listen")
        self._send_notification(
            Notification(
                title="Start listening",
                content="Finding music in the active VRChat player…",
                timeout_seconds=3.0,
            )
        )
        worker = threading.Thread(
            target=self._listen_worker,
            args=(options,),
            name="shazam-for-vrc-listener",
            daemon=True,
        )
        worker.start()

    def _listen_worker(self, options: ListenOptions) -> None:
        try:
            outcome = asyncio.run(self.service.listen(options, self._queue_progress))
        except Exception as error:
            logger.exception("The listening operation failed (%s).", type(error).__name__)
            self.events.put(("error", error))
        else:
            self.events.put(("success", outcome))

    def _queue_progress(self, progress: ListeningProgress) -> None:
        self.events.put(("progress", progress))

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.events.get_nowait()
                if event_type == "progress" and isinstance(payload, ListeningProgress):
                    self._last_listening_stage = payload.stage
                    self.status_text.set(payload.message)
                elif event_type == "success" and isinstance(payload, ListeningOutcome):
                    self._handle_outcome(payload)
                elif event_type == "error" and isinstance(payload, Exception):
                    self._handle_error(payload)
                elif event_type == "player_debug" and isinstance(
                    payload,
                    PlayerDebugInfo,
                ):
                    self._handle_player_debug(payload)
                elif event_type == "player_debug_error" and isinstance(
                    payload,
                    Exception,
                ):
                    self._handle_player_debug_error(payload)
                elif event_type == "update_check" and isinstance(
                    payload,
                    UpdateCheckResult,
                ):
                    self._handle_update_check(payload)
                elif event_type == "update_error":
                    error, silent = payload  # type: ignore[misc]
                    if isinstance(error, Exception):
                        self._handle_update_error(error, silent=bool(silent))
                elif event_type == "update_progress":
                    received, total = payload  # type: ignore[misc]
                    self._handle_update_progress(int(received), int(total))
                elif event_type == "update_downloaded" and isinstance(
                    payload,
                    DownloadedUpdate,
                ):
                    self._handle_update_downloaded(payload)
                elif event_type == "update_download_error" and isinstance(
                    payload,
                    Exception,
                ):
                    self._handle_update_download_error(payload)
                elif event_type == "input_trigger":
                    if payload == self._input_generation:
                        with self._input_trigger_lock:
                            self._input_trigger_pending = False
                        self._start_listening()
                elif event_type == "input_status":
                    generation, status = payload  # type: ignore[misc]
                    if generation == self._input_generation and isinstance(
                        status,
                        InputStatus,
                    ):
                        self._handle_input_status(status)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_outcome(self, outcome: ListeningOutcome) -> None:
        self._set_listening_state(False)
        result = outcome.recognition
        if outcome.result_kind is ListeningResultKind.MIX and outcome.media_title:
            entry = HistoryEntry.create(
                artist="Mix",
                title=outcome.media_title,
                world_name=outcome.world.name or "Unknown world",
                group_id=outcome.world.group_id,
                instance_type=outcome.world.instance_type,
                provider=outcome.provider.value,
                link=outcome.source_url,
                kind=TrackLogKind.MIX,
                notice=outcome.notice,
                recognition_provider=result.provider,
                recording_attempts=outcome.recording_attempts,
            )
            saved = self._prepend_log_entry(entry)
            status = (
                f"No track found from computer audio; added mix: {entry.title}"
                if outcome.used_system_audio_fallback
                else f"Added mix: {entry.title}"
            )
            if not saved:
                status += " (the track log could not be saved)"
            self.status_text.set(status)
            self._send_notification(
                Notification(
                    title="Mix",
                    content=(
                        f"{entry.title}. Computer-audio fallback also found no track."
                        if outcome.used_system_audio_fallback
                        else f"{entry.title}. Current track unavailable: mix position unknown."
                    ),
                    style=NotificationStyle.WARNING,
                    timeout_seconds=6.0,
                )
            )
            self._send_chatbox_mix(entry.title)
            self._refresh_history()
        elif result.track is None:
            message = (
                "No song found after 3 attempts: 2 clean-stream recordings and 1 "
                "VRChat/computer-audio recording."
                if outcome.used_system_audio_fallback
                else f"No song found after {outcome.recording_attempts} recording attempt(s)."
            )
            self.status_text.set(message)
            self._prepend_log_entry(
                HistoryEntry.create_error(
                    stage="Recognition",
                    message=message,
                    world_name=outcome.world.name or "Unknown world",
                    group_id=outcome.world.group_id,
                    instance_type=outcome.world.instance_type,
                    provider=outcome.provider.value,
                    recording_attempts=outcome.recording_attempts,
                )
            )
            self._send_notification(
                Notification(
                    title="No track found",
                    content=message,
                    style=NotificationStyle.WARNING,
                    timeout_seconds=5.0,
                )
            )
            self._refresh_history()
        else:
            kind = (
                TrackLogKind.LIVE
                if outcome.result_kind is ListeningResultKind.LIVE
                else TrackLogKind.TRACK
            )
            entry = HistoryEntry.create(
                artist=result.track.artist,
                title=result.track.title,
                world_name=outcome.world.name or "Unknown world",
                group_id=outcome.world.group_id,
                instance_type=outcome.world.instance_type,
                provider=outcome.provider.value,
                link=outcome.source_url or result.track.track_url,
                kind=kind,
                notice=outcome.notice,
                recognition_provider=result.provider,
                recording_attempts=outcome.recording_attempts,
            )
            if not self._prepend_log_entry(entry):
                self.status_text.set(
                    f"Recognized {entry.display_track}, but the track log was not saved."
                )
            else:
                status_prefix = (
                    "Recognized from VRChat/computer audio"
                    if outcome.used_system_audio_fallback
                    else "Recognized"
                )
                self.status_text.set(f"{status_prefix}: {entry.display_track}")
            self._send_notification(
                Notification(
                    title=(
                        "Track recognized from computer audio"
                        if outcome.used_system_audio_fallback
                        else "Track recognized"
                    ),
                    content=entry.display_track,
                    timeout_seconds=6.0,
                )
            )
            self._send_chatbox_track(
                result.track.artist,
                result.track.title,
                outcome.provider,
                _chatbox_track_context(outcome.result_kind, outcome.media_title),
            )
            self._refresh_history()
        self._refresh_debug_controls()

    def _handle_error(self, error: Exception) -> None:
        self._set_listening_state(False)
        message = self._friendly_error(error)
        title = (
            "Player time unavailable"
            if isinstance(error, PlayerTimeUnavailableError)
            else (
                "Computer audio unavailable"
                if isinstance(error, SystemAudioCaptureError)
                else "Listening error"
            )
        )
        self.status_text.set(message)
        self._prepend_log_entry(
            HistoryEntry.create_error(
                stage=self._error_stage(error),
                message=message,
            )
        )
        self._refresh_history()
        self._send_notification(
            Notification(
                title=title,
                content=message,
                style=NotificationStyle.ERROR,
                timeout_seconds=6.0,
            )
        )
        self._refresh_debug_controls()
        messagebox.showerror(title, message, parent=self.root)

    def _prepend_log_entry(self, entry: HistoryEntry) -> bool:
        self.history_entries.insert(0, entry)
        self.history_entries = self.history_entries[: self.history_store.limit]
        if entry.is_copyable:
            self._reset_copy_whole_log_state()
        try:
            self.history_store.save(self.history_entries)
        except HistoryError as error:
            logger.warning("Could not save the track log: %s", error)
            return False
        return True

    def _error_stage(self, error: Exception) -> str:
        if isinstance(error, MediaSelectionError):
            return "Finding player"
        if isinstance(error, StreamResolutionError):
            return "Resolving stream"
        if isinstance(error, SystemAudioCaptureError):
            return "Recording computer audio"
        if isinstance(error, AudioCaptureError):
            return "Recording"
        if isinstance(error, ShazamRecognitionError):
            return "Recognition"
        if isinstance(error, DebugSampleError):
            return "Saving debug sample"
        return self._last_listening_stage.value.replace("_", " ").title()

    def _send_notification(self, notification: Notification) -> None:
        if not self.config.xsoverlay_notifications_enabled:
            return
        try:
            self._notification_sender.send(notification)
        except NotificationError as error:
            logger.warning("Could not send XSOverlay notification: %s", error)

    def _send_chatbox_track(
        self,
        artist: str,
        title: str,
        provider: Provider,
        context: ChatboxTrackContext,
    ) -> None:
        if not self.config.vrchat_chatbox_enabled:
            return
        try:
            self._chatbox_sender.send_track(
                artist,
                title,
                provider=provider,
                context=context,
            )
        except ChatboxOutputError as error:
            logger.warning("Could not send VRChat chatbox output: %s", error)

    def _send_chatbox_mix(self, title: str) -> None:
        if not self.config.vrchat_chatbox_enabled:
            return
        try:
            self._chatbox_sender.send_mix(title)
        except ChatboxOutputError as error:
            logger.warning("Could not send VRChat chatbox mix output: %s", error)

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        expected_errors = (
            MediaSelectionError,
            StreamResolutionError,
            SystemAudioCaptureError,
            AudioCaptureError,
            ShazamRecognitionError,
            DebugSampleError,
        )
        if isinstance(error, expected_errors) and str(error).strip():
            return str(error)
        return "An unexpected error stopped the listening operation."

    def _set_listening_state(self, listening: bool) -> None:
        self.is_listening = listening
        self.listen_button.configure(state=tk.DISABLED if listening else tk.NORMAL)
        for widget, idle_state in self._settings_widgets:
            widget.configure(state=tk.DISABLED if listening else idle_state)
        if listening:
            self.status_text.set("Starting...")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)

    def _refresh_debug_controls(self) -> None:
        exists = self.debug_sample_path.is_file()
        if exists:
            try:
                size_kib = self.debug_sample_path.stat().st_size / 1024
                self.debug_text.set(
                    f"Last retained sample: {size_kib:.0f} KiB — {self.debug_sample_path}"
                )
            except OSError:
                self.debug_text.set("A retained sample exists.")
        else:
            self.debug_text.set(
                "No retained sample. Enable debug retention before listening to keep one."
            )
        state = tk.NORMAL if exists else tk.DISABLED
        self.play_sample_button.configure(state=state)
        self.delete_sample_button.configure(state=state)

    @staticmethod
    def _input_settings_changed(previous: AppConfig, current: AppConfig) -> bool:
        return any(
            (
                previous.steamvr_input_enabled != current.steamvr_input_enabled,
                previous.controller_button != current.controller_button,
                previous.controller_trigger_mode != current.controller_trigger_mode,
                previous.controller_hold_seconds != current.controller_hold_seconds,
                previous.controller_double_press_seconds != current.controller_double_press_seconds,
                previous.keyboard_hotkey_enabled != current.keyboard_hotkey_enabled,
                previous.keyboard_hotkey != current.keyboard_hotkey,
                previous.osc_input_enabled != current.osc_input_enabled,
                previous.osc_input_parameter != current.osc_input_parameter,
                previous.osc_input_port != current.osc_input_port,
            )
        )

    def _restart_input_listeners(self, config: AppConfig) -> None:
        self._stop_input_listeners()
        self._input_generation += 1
        generation = self._input_generation
        self._input_statuses = {}

        def queue_status(status: InputStatus) -> None:
            self.events.put(("input_status", (generation, status)))

        def queue_trigger(source: InputSource) -> bool:
            return self._queue_input_trigger(source, generation)

        if config.steamvr_input_enabled:
            try:
                listener = self._steamvr_listener_factory(
                    lambda: queue_trigger(InputSource.STEAMVR),
                    queue_status,
                    hold_seconds=config.controller_hold_seconds,
                    trigger_mode=config.controller_trigger_mode,
                    double_press_seconds=config.controller_double_press_seconds,
                    controller_button=config.controller_button,
                )
            except Exception as error:
                logger.warning("Could not configure SteamVR input: %s", error)
                self._input_statuses[InputSource.STEAMVR] = InputStatus(
                    InputSource.STEAMVR,
                    InputStatusKind.ERROR,
                    "SteamVR input could not be configured.",
                )
            else:
                self._input_listeners.append(listener)
        else:
            self._input_statuses[InputSource.STEAMVR] = InputStatus(
                InputSource.STEAMVR,
                InputStatusKind.STOPPED,
                "SteamVR input disabled.",
            )

        if config.keyboard_hotkey_enabled:
            try:
                listener = self._hotkey_listener_factory(
                    lambda: queue_trigger(InputSource.KEYBOARD),
                    queue_status,
                    hotkey=config.keyboard_hotkey,
                )
            except Exception as error:
                logger.warning("Could not configure keyboard input: %s", error)
                self._input_statuses[InputSource.KEYBOARD] = InputStatus(
                    InputSource.KEYBOARD,
                    InputStatusKind.ERROR,
                    "Keyboard input could not be configured.",
                )
            else:
                self._input_listeners.append(listener)
        else:
            self._input_statuses[InputSource.KEYBOARD] = InputStatus(
                InputSource.KEYBOARD,
                InputStatusKind.STOPPED,
                "Keyboard shortcut disabled.",
            )

        if config.osc_input_enabled:
            try:
                listener = self._osc_listener_factory(
                    lambda: queue_trigger(InputSource.OSC),
                    queue_status,
                    parameter_name=config.osc_input_parameter,
                    port=config.osc_input_port,
                )
            except Exception as error:
                logger.warning("Could not configure VRChat OSC input: %s", error)
                self._input_statuses[InputSource.OSC] = InputStatus(
                    InputSource.OSC,
                    InputStatusKind.ERROR,
                    "VRChat OSC input could not be configured.",
                )
            else:
                self._input_listeners.append(listener)
        else:
            self._input_statuses[InputSource.OSC] = InputStatus(
                InputSource.OSC,
                InputStatusKind.STOPPED,
                "VRChat OSC input disabled.",
            )

        self._refresh_input_status_text()
        for listener in self._input_listeners:
            listener.start()

    def _stop_input_listeners(self) -> None:
        for listener in self._input_listeners:
            try:
                listener.stop()
            except Exception:
                logger.exception("Could not stop an input listener")
        self._input_listeners.clear()
        with self._input_trigger_lock:
            self._input_trigger_pending = False

    def _queue_input_trigger(self, _source: InputSource, generation: int) -> bool:
        with self._input_trigger_lock:
            if (
                generation != self._input_generation
                or self.is_listening
                or self._input_trigger_pending
            ):
                return False
            self._input_trigger_pending = True
        self.events.put(("input_trigger", generation))
        return True

    def _handle_input_status(self, status: InputStatus) -> None:
        self._input_statuses[status.source] = status
        self._refresh_input_status_text()

    def _refresh_input_status_text(self) -> None:
        messages = [
            self._input_statuses[source].message
            for source in (InputSource.STEAMVR, InputSource.KEYBOARD, InputSource.OSC)
            if source in self._input_statuses
        ]
        self.input_status_text.set(
            " ".join(messages) if messages else "Starting input listeners..."
        )

    def _play_last_sample(self) -> None:
        path = Path(self.debug_sample_path)
        if not path.is_file():
            self._refresh_debug_controls()
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            messagebox.showerror(
                "Debug recording",
                "Windows could not open the latest debug recording.",
                parent=self.root,
            )

    def _delete_last_sample(self) -> None:
        try:
            delete_last_sample(self.debug_sample_path)
        except DebugSampleError as error:
            messagebox.showerror("Debug recording", str(error), parent=self.root)
            return
        self._refresh_debug_controls()
        self.settings_status_text.set("Latest debug recording deleted.")

    def _on_close(self) -> None:
        if self._update_downloading:
            messagebox.showinfo(
                "Update download in progress",
                "Please wait for the verified update download to finish.",
                parent=self.root,
            )
            return
        if self.is_listening:
            messagebox.showinfo(
                "Listening in progress",
                "Please wait for listening to finish so temporary audio can be cleaned up.",
                parent=self.root,
            )
            return
        self._stop_input_listeners()
        if self._player_debug_window is not None:
            self._close_player_debug()
        self.root.destroy()


def _chatbox_track_context(
    result_kind: ListeningResultKind,
    media_title: str | None,
) -> ChatboxTrackContext:
    """Map a listening result to the public wording used by the VRChat chatbox."""

    if result_kind is ListeningResultKind.LIVE:
        return ChatboxTrackContext.LIVE_STREAM
    if media_title:
        return ChatboxTrackContext.MIX_TRACK
    return ChatboxTrackContext.TRACK


def _player_debug_summary(info: PlayerDebugInfo) -> str:
    stream = info.stream
    metadata = stream.metadata
    provider = stream.provider.value.replace("_", " ").title()
    playback = stream.playback_type.value.replace("_", " ").title()
    lines = [
        f"World: {info.world.name or 'Unknown world'}",
        f"Current item: {info.current_item}",
        f"Provider: {provider}",
        f"Playback: {playback}",
    ]
    if metadata is not None:
        content_kind = metadata.content_kind.value.replace("_", " ").title()
        confidence = metadata.confidence.value
        lines.append(f"Detected content: {content_kind} ({confidence} confidence)")
        if metadata.title and metadata.title != info.current_item:
            lines.append(f"Media title: {metadata.title}")
    lines.append(f"Length: {format_media_time(stream.duration_seconds)}")

    if stream.playback_type is PlaybackType.LIVE:
        lines.append("Position: Live now")
        lines.append("Timing confidence: Current livestream edge")
    elif info.position is None:
        lines.append("Player time: Not available from this VRChat player")
        lines.append(
            "Why: The log contains the video URL, but not the current playhead minute/second"
        )
        lines.append("Needed for an estimate: An AVPro open time or a start time in the video URL")
        lines.append("Exact timing: Pause, seek, and world synchronization are not exposed")
    else:
        label = (
            "Estimated loop position"
            if info.position.past_reported_duration
            else "Estimated position"
        )
        lines.append(f"{label}: {format_media_time(info.position.seconds)}")
        lines.append("Timing confidence: Low — pause, seek, and world sync are not logged")
        if info.position.past_reported_duration:
            lines.append("Loop note: Elapsed time passed the media length, so looping is assumed")

    opened = info.candidate.opened_at
    lines.append(f"AVPro opened: {opened.replace('T', ' ') if opened else 'Not logged'}")
    lines.append("Log refresh: Every 2 seconds while this window is open")
    return "\n\n".join(lines)


def _history_path_for_config(config: AppConfig) -> Path:
    return Path(config.history_path) if config.history_path else default_history_path()


def _debug_sample_path_for_config(config: AppConfig) -> Path:
    directory = (
        Path(config.debug_directory)
        if config.debug_directory
        else default_debug_sample_path().parent
    )
    return directory / "last-sample.wav"


def _value_for_label(values: dict[str, str], selected_label: str) -> str:
    for value, label in values.items():
        if label == selected_label:
            return value
    raise ValueError("A selected input option is not supported.")


def _mousewheel_scroll_units(delta: int) -> int:
    """Translate Windows wheel/trackpad deltas without dropping small movements."""

    if delta == 0:
        return 0
    full_notches = int(-delta / 120)
    if full_notches != 0:
        return full_notches
    return -1 if delta > 0 else 1


def run_overlay() -> None:
    """Create and run the desktop overlay."""

    root = tk.Tk()
    OverlayApp(root)
    root.mainloop()
