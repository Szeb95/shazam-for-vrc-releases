"""Application configuration models and local JSON persistence."""

from __future__ import annotations

import json
import math
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_path

APP_NAME = "Shazam for VRC"
DEFAULT_RECORD_SECONDS = 12.0
MIN_RECORD_SECONDS = 3.0
MAX_RECORD_SECONDS = 60.0
DEFAULT_RETRY_COUNT = 1
MAX_RETRY_COUNT = 5
DEFAULT_CONTROLLER_HOLD_SECONDS = 0.8
MIN_CONTROLLER_HOLD_SECONDS = 0.3
MAX_CONTROLLER_HOLD_SECONDS = 3.0
DEFAULT_CONTROLLER_DOUBLE_PRESS_SECONDS = 0.5
MIN_CONTROLLER_DOUBLE_PRESS_SECONDS = 0.2
MAX_CONTROLLER_DOUBLE_PRESS_SECONDS = 1.5
DEFAULT_CONTROLLER_BUTTON = "b"
CONTROLLER_BUTTONS = frozenset({"a", "b", "thumbstick", "trigger"})
DEFAULT_CONTROLLER_TRIGGER_MODE = "long_press"
CONTROLLER_TRIGGER_MODES = frozenset({"long_press", "double_press"})
DEFAULT_KEYBOARD_HOTKEY = "F9"
KEYBOARD_HOTKEYS = frozenset(f"F{number}" for number in range(1, 13))
DEFAULT_OSC_INPUT_PARAMETER = "ShazamListen"
DEFAULT_OSC_INPUT_PORT = 9001
MAX_OSC_INPUT_PARAMETER_LENGTH = 256


class ConfigError(RuntimeError):
    """Raised when application settings cannot be loaded or saved."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """User-controlled settings for the basic overlay."""

    record_seconds: float = DEFAULT_RECORD_SECONDS
    retry_count: int = DEFAULT_RETRY_COUNT
    system_audio_fallback_enabled: bool = False
    keep_last_sample: bool = False
    always_on_top: bool = True
    show_copied_indicators: bool = True
    automatic_update_checks: bool = True
    steamvr_input_enabled: bool = True
    controller_button: str = DEFAULT_CONTROLLER_BUTTON
    controller_trigger_mode: str = DEFAULT_CONTROLLER_TRIGGER_MODE
    controller_hold_seconds: float = DEFAULT_CONTROLLER_HOLD_SECONDS
    controller_double_press_seconds: float = DEFAULT_CONTROLLER_DOUBLE_PRESS_SECONDS
    keyboard_hotkey_enabled: bool = True
    keyboard_hotkey: str = DEFAULT_KEYBOARD_HOTKEY
    osc_input_enabled: bool = True
    osc_input_parameter: str = DEFAULT_OSC_INPUT_PARAMETER
    osc_input_port: int = DEFAULT_OSC_INPUT_PORT
    xsoverlay_notifications_enabled: bool = True
    vrchat_chatbox_enabled: bool = False
    history_path: str = ""
    debug_directory: str = ""

    def __post_init__(self) -> None:
        if (
            isinstance(self.record_seconds, bool)
            or not isinstance(self.record_seconds, (int, float))
            or not math.isfinite(float(self.record_seconds))
            or not MIN_RECORD_SECONDS <= float(self.record_seconds) <= MAX_RECORD_SECONDS
        ):
            raise ValueError(
                f"record_seconds must be between {MIN_RECORD_SECONDS:g} and {MAX_RECORD_SECONDS:g}"
            )
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or not 0 <= self.retry_count <= MAX_RETRY_COUNT
        ):
            raise ValueError(f"retry_count must be between 0 and {MAX_RETRY_COUNT}")
        if not isinstance(self.keep_last_sample, bool):
            raise ValueError("keep_last_sample must be true or false")
        if not isinstance(self.system_audio_fallback_enabled, bool):
            raise ValueError("system_audio_fallback_enabled must be true or false")
        if not isinstance(self.always_on_top, bool):
            raise ValueError("always_on_top must be true or false")
        if not isinstance(self.show_copied_indicators, bool):
            raise ValueError("show_copied_indicators must be true or false")
        if not isinstance(self.automatic_update_checks, bool):
            raise ValueError("automatic_update_checks must be true or false")
        if not isinstance(self.steamvr_input_enabled, bool):
            raise ValueError("steamvr_input_enabled must be true or false")
        if self.controller_button not in CONTROLLER_BUTTONS:
            raise ValueError("controller_button is not supported")
        if self.controller_trigger_mode not in CONTROLLER_TRIGGER_MODES:
            raise ValueError("controller_trigger_mode is not supported")
        if (
            isinstance(self.controller_hold_seconds, bool)
            or not isinstance(self.controller_hold_seconds, (int, float))
            or not math.isfinite(float(self.controller_hold_seconds))
            or not MIN_CONTROLLER_HOLD_SECONDS
            <= float(self.controller_hold_seconds)
            <= MAX_CONTROLLER_HOLD_SECONDS
        ):
            raise ValueError(
                "controller_hold_seconds must be between "
                f"{MIN_CONTROLLER_HOLD_SECONDS:g} and {MAX_CONTROLLER_HOLD_SECONDS:g}"
            )
        if (
            isinstance(self.controller_double_press_seconds, bool)
            or not isinstance(self.controller_double_press_seconds, (int, float))
            or not math.isfinite(float(self.controller_double_press_seconds))
            or not MIN_CONTROLLER_DOUBLE_PRESS_SECONDS
            <= float(self.controller_double_press_seconds)
            <= MAX_CONTROLLER_DOUBLE_PRESS_SECONDS
        ):
            raise ValueError(
                "controller_double_press_seconds must be between "
                f"{MIN_CONTROLLER_DOUBLE_PRESS_SECONDS:g} and "
                f"{MAX_CONTROLLER_DOUBLE_PRESS_SECONDS:g}"
            )
        if not isinstance(self.keyboard_hotkey_enabled, bool):
            raise ValueError("keyboard_hotkey_enabled must be true or false")
        if self.keyboard_hotkey not in KEYBOARD_HOTKEYS:
            raise ValueError("keyboard_hotkey must be a function key from F1 to F12")
        if not isinstance(self.osc_input_enabled, bool):
            raise ValueError("osc_input_enabled must be true or false")
        _validate_osc_input_parameter(self.osc_input_parameter)
        if (
            isinstance(self.osc_input_port, bool)
            or not isinstance(self.osc_input_port, int)
            or not 1 <= self.osc_input_port <= 65_535
        ):
            raise ValueError("osc_input_port must be between 1 and 65535")
        if not isinstance(self.xsoverlay_notifications_enabled, bool):
            raise ValueError("xsoverlay_notifications_enabled must be true or false")
        if not isinstance(self.vrchat_chatbox_enabled, bool):
            raise ValueError("vrchat_chatbox_enabled must be true or false")
        _validate_optional_absolute_path(self.history_path, "history_path")
        _validate_optional_absolute_path(self.debug_directory, "debug_directory")


def default_config_path() -> Path:
    """Return the per-user settings path without hardcoding a Windows profile."""

    return user_config_path(APP_NAME, appauthor=False) / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    """Load settings, returning defaults when no settings file exists."""

    config_path = path or default_config_path()
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AppConfig()
    except OSError as error:
        raise ConfigError("The application settings could not be read.") from error

    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("settings root must be an object")
        return AppConfig(
            record_seconds=raw.get("record_seconds", DEFAULT_RECORD_SECONDS),
            retry_count=raw.get("retry_count", DEFAULT_RETRY_COUNT),
            system_audio_fallback_enabled=raw.get(
                "system_audio_fallback_enabled",
                False,
            ),
            keep_last_sample=raw.get("keep_last_sample", False),
            always_on_top=raw.get("always_on_top", True),
            show_copied_indicators=raw.get("show_copied_indicators", True),
            automatic_update_checks=raw.get("automatic_update_checks", True),
            steamvr_input_enabled=raw.get("steamvr_input_enabled", True),
            controller_button=raw.get(
                "controller_button",
                DEFAULT_CONTROLLER_BUTTON,
            ),
            controller_trigger_mode=raw.get(
                "controller_trigger_mode",
                DEFAULT_CONTROLLER_TRIGGER_MODE,
            ),
            controller_hold_seconds=raw.get(
                "controller_hold_seconds",
                DEFAULT_CONTROLLER_HOLD_SECONDS,
            ),
            controller_double_press_seconds=raw.get(
                "controller_double_press_seconds",
                DEFAULT_CONTROLLER_DOUBLE_PRESS_SECONDS,
            ),
            keyboard_hotkey_enabled=raw.get("keyboard_hotkey_enabled", True),
            keyboard_hotkey=raw.get("keyboard_hotkey", DEFAULT_KEYBOARD_HOTKEY),
            osc_input_enabled=raw.get("osc_input_enabled", True),
            osc_input_parameter=raw.get(
                "osc_input_parameter",
                DEFAULT_OSC_INPUT_PARAMETER,
            ),
            osc_input_port=raw.get("osc_input_port", DEFAULT_OSC_INPUT_PORT),
            xsoverlay_notifications_enabled=raw.get(
                "xsoverlay_notifications_enabled",
                True,
            ),
            vrchat_chatbox_enabled=raw.get("vrchat_chatbox_enabled", False),
            history_path=raw.get("history_path", ""),
            debug_directory=raw.get("debug_directory", ""),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ConfigError(
            "The application settings are invalid. Delete the settings file to restore defaults."
        ) from error


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Atomically save validated settings and return their path."""

    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    config_path = path or default_config_path()
    temporary_path = config_path.with_suffix(".tmp")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(asdict(config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(config_path)
    except OSError as error:
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)
        raise ConfigError("The application settings could not be saved.") from error
    return config_path


def _validate_optional_absolute_path(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    if not value:
        return
    if not Path(value).is_absolute():
        raise ValueError(f"{field_name} must be an absolute path or empty")


def _validate_osc_input_parameter(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("osc_input_parameter must not be empty")
    if value != value.strip():
        raise ValueError("osc_input_parameter must not have surrounding whitespace")
    if len(value) > MAX_OSC_INPUT_PARAMETER_LENGTH:
        raise ValueError(
            f"osc_input_parameter must be at most {MAX_OSC_INPUT_PARAMETER_LENGTH} characters"
        )
    if (
        value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("osc_input_parameter contains an invalid path segment")
