import json
from pathlib import Path

import pytest

from shazam_for_vrc.config import AppConfig, ConfigError, load_config, save_config


def test_missing_config_uses_overlay_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.json")

    assert config == AppConfig()
    assert config.record_seconds == 12.0
    assert config.retry_count == 1
    assert not config.system_audio_fallback_enabled
    assert not config.keep_last_sample
    assert config.always_on_top
    assert config.show_copied_indicators
    assert config.automatic_update_checks
    assert config.steamvr_input_enabled
    assert config.controller_button == "b"
    assert config.controller_trigger_mode == "long_press"
    assert config.controller_hold_seconds == 0.8
    assert config.controller_double_press_seconds == 0.5
    assert config.keyboard_hotkey_enabled
    assert config.keyboard_hotkey == "F9"
    assert config.osc_input_enabled
    assert config.osc_input_parameter == "ShazamListen"
    assert config.osc_input_port == 9001
    assert config.xsoverlay_notifications_enabled
    assert not config.vrchat_chatbox_enabled
    assert config.history_path == ""
    assert config.debug_directory == ""


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "config.json"
    expected = AppConfig(
        record_seconds=18.5,
        retry_count=3,
        system_audio_fallback_enabled=True,
        keep_last_sample=True,
        always_on_top=False,
        show_copied_indicators=False,
        automatic_update_checks=False,
        steamvr_input_enabled=False,
        controller_button="a",
        controller_trigger_mode="double_press",
        controller_hold_seconds=1.4,
        controller_double_press_seconds=0.7,
        keyboard_hotkey_enabled=False,
        keyboard_hotkey="F4",
        osc_input_enabled=False,
        osc_input_parameter="ListenForMusic",
        osc_input_port=9101,
        xsoverlay_notifications_enabled=False,
        vrchat_chatbox_enabled=True,
        history_path=str(tmp_path / "custom-history.json"),
        debug_directory=str(tmp_path / "debug"),
    )

    assert save_config(expected, path) == path

    assert load_config(path) == expected
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize(
    "values",
    [
        {"record_seconds": 0},
        {"record_seconds": 61},
        {"retry_count": -1},
        {"retry_count": 6},
        {"keep_last_sample": "yes"},
        {"system_audio_fallback_enabled": "yes"},
        {"show_copied_indicators": "yes"},
        {"automatic_update_checks": 1},
        {"controller_hold_seconds": 0.2},
        {"controller_hold_seconds": 3.1},
        {"controller_button": "menu"},
        {"controller_trigger_mode": "triple_press"},
        {"controller_double_press_seconds": 0.1},
        {"controller_double_press_seconds": 1.6},
        {"steamvr_input_enabled": "yes"},
        {"keyboard_hotkey_enabled": 1},
        {"keyboard_hotkey": "B"},
        {"osc_input_enabled": "yes"},
        {"osc_input_parameter": ""},
        {"osc_input_parameter": "folder//ShazamListen"},
        {"osc_input_port": 0},
        {"osc_input_port": 65_536},
        {"xsoverlay_notifications_enabled": "yes"},
        {"vrchat_chatbox_enabled": 1},
        {"history_path": "relative.json"},
        {"debug_directory": "relative"},
    ],
)
def test_invalid_saved_config_reports_actionable_error(
    tmp_path: Path,
    values: dict[str, object],
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")

    with pytest.raises(ConfigError, match="settings are invalid"):
        load_config(path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"record_seconds": True},
        {"retry_count": True},
        {"always_on_top": 1},
        {"controller_hold_seconds": True},
        {"osc_input_port": True},
    ],
)
def test_config_model_rejects_ambiguous_value_types(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AppConfig(**kwargs)  # type: ignore[arg-type]
