from types import SimpleNamespace
from unittest.mock import MagicMock

from scenes.idle.themes.solar_idle_theme import (
    USED_TODAY_X,
    USED_TODAY_Y,
    VALUE_FONT,
    SolarIdleTheme,
)
from setup import colours
from setup.configuration import Config


def _make_theme(reading, monkeypatch):
    cfg = SimpleNamespace(
        solar_client_id="client",
        solar_client_secret="secret",
        solar_api_key="key",
    )
    monkeypatch.setattr(Config, "instance", lambda: cfg)

    theme = SolarIdleTheme.__new__(SolarIdleTheme)
    theme.solar = MagicMock()
    theme.solar.get.return_value = reading
    theme.panel = MagicMock()
    theme.canvas = object()
    theme.last_gen_str = None
    theme.last_use_str = None
    theme.last_today_str = None
    theme.last_used_today_str = None
    return theme


def test_solar_theme_draws_daily_consumption_in_kwh(monkeypatch):
    theme = _make_theme(
        {
            "current_power_w": 1250.0,
            "consumption_power_w": 500.0,
            "energy_today_wh": 2300.0,
            "consumption_energy_today_wh": 1845.0,
        },
        monkeypatch,
    )

    theme.draw_content(0)

    theme.panel.draw_text.assert_any_call(
        theme.canvas, VALUE_FONT, USED_TODAY_X, USED_TODAY_Y, colours.RED, "1.8kWh"
    )


def test_solar_theme_shows_placeholder_when_daily_consumption_is_missing(monkeypatch):
    theme = _make_theme(
        {
            "current_power_w": 1250.0,
            "consumption_power_w": None,
            "energy_today_wh": 2300.0,
            "consumption_energy_today_wh": None,
        },
        monkeypatch,
    )

    theme.draw_content(0)

    theme.panel.draw_text.assert_any_call(
        theme.canvas, VALUE_FONT, USED_TODAY_X, USED_TODAY_Y, colours.RED, "--"
    )
