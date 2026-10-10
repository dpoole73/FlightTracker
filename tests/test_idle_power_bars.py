from unittest.mock import MagicMock

from scenes.idle.themes.classic_idle_theme import (
    POWER_BAR_BASE_Y,
    POWER_BAR_MAX_HEIGHT,
    POWER_CONSUMPTION_X,
    POWER_GENERATION_X,
    ClassicIdleTheme,
)
from setup import colours
from setup.configuration import Config, DEFAULT_IDLE_SOLAR_BARS_ENABLED, DEFAULTS
from utilities.solar_service import SolarService


def test_solar_bars_enabled_by_default_for_existing_configs():
    cfg = Config.__new__(Config)
    cfg.data_store = {}

    assert DEFAULTS["idle_solar_bars_enabled"] is DEFAULT_IDLE_SOLAR_BARS_ENABLED
    assert cfg.idle_solar_bars_enabled is True


def test_solar_bars_setting_can_be_disabled():
    cfg = Config.__new__(Config)
    cfg.data_store = {"idle_solar_bars_enabled": False}

    assert cfg.idle_solar_bars_enabled is False


def test_power_bars_scale_generation_and_consumption_to_15_kw():
    theme = ClassicIdleTheme.__new__(ClassicIdleTheme)
    theme.solar = MagicMock()
    theme.solar.get.return_value = {
        "current_power_w": 15_000.0,
        "consumption_power_w": 5_000.0,
    }
    theme.panel = MagicMock()
    theme.canvas = object()
    theme.last_power_bar_heights = None

    theme.draw_power_bars()

    theme.panel.draw_square.assert_any_call(
        theme.canvas,
        POWER_GENERATION_X,
        POWER_BAR_BASE_Y - POWER_BAR_MAX_HEIGHT,
        POWER_GENERATION_X + 2,
        POWER_BAR_BASE_Y,
        colours.GREEN,
    )
    theme.panel.draw_square.assert_any_call(
        theme.canvas,
        POWER_CONSUMPTION_X,
        POWER_BAR_BASE_Y - 5,
        POWER_CONSUMPTION_X + 2,
        POWER_BAR_BASE_Y,
        colours.RED,
    )


def test_missing_consumption_reading_only_draws_generation_bar():
    theme = ClassicIdleTheme.__new__(ClassicIdleTheme)
    theme.solar = MagicMock()
    theme.solar.get.return_value = {
        "current_power_w": 500.0,
        "consumption_power_w": None,
    }
    theme.panel = MagicMock()
    theme.canvas = object()
    theme.last_power_bar_heights = None

    theme.draw_power_bars()

    assert theme.last_power_bar_heights == (1, 0)
    assert theme.panel.draw_square.call_count == 1


def test_disabled_bars_do_not_start_solar_service(monkeypatch):
    theme = ClassicIdleTheme.__new__(ClassicIdleTheme)
    theme.solar = None
    theme.panel = MagicMock()
    theme.canvas = object()
    theme.last_power_bar_heights = None
    cfg = Config.__new__(Config)
    cfg.data_store = {"idle_solar_bars_enabled": False}
    monkeypatch.setattr(Config, "instance", lambda: cfg)
    solar_instance = MagicMock()
    monkeypatch.setattr(SolarService, "instance", solar_instance)

    theme.draw_power_bars()

    solar_instance.assert_not_called()
    theme.panel.draw_square.assert_not_called()
    theme.panel.draw_line.assert_not_called()