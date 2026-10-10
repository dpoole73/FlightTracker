import json
from datetime import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from scenes.idle import idle_scene
from scenes.idle.themes import solar_idle_theme, solar_intraday_idle_theme
from setup.configuration import Config


class _FakeTheme:
    default_display_mode = "always"

    def __init__(self, canvas, panel):
        self.draw_calls = 0
        self.enter_calls = 0
        self.eligibility_calls = 0

    def draw(self):
        self.draw_calls += 1

    def on_enter(self):
        self.enter_calls += 1

    def should_display(self):
        self.eligibility_calls += 1
        return True


class _ConditionalTheme(_FakeTheme):
    default_display_mode = "eligible"

    def should_display(self):
        self.eligibility_calls += 1
        return False


def _configure_rotator(monkeypatch, order, schedules=None):
    cfg = SimpleNamespace(
        idle_theme_order=order,
        idle_theme_rotation_seconds=3,
        idle_theme_schedule_mode=lambda name: (schedules or {}).get(name),
    )
    registry = {
        "classic": _FakeTheme,
        "conditional": _ConditionalTheme,
        "forecast": _FakeTheme,
    }
    monkeypatch.setattr(idle_scene.Config, "instance", lambda: cfg)
    monkeypatch.setattr(idle_scene, "_load_themes", lambda: registry)
    monkeypatch.setattr(idle_scene.frames, "PER_SECOND", 1)
    return idle_scene.RotatingIdleScene(None, None)


def test_first_idle_theme_stays_four_rotation_intervals(monkeypatch):
    scene = _configure_rotator(monkeypatch, ["classic", "forecast"])

    for _ in range(11):
        scene.draw()
    assert scene.index == 0

    scene.draw()
    assert scene.index == 1


def test_rotator_skips_theme_that_declines_its_slot(monkeypatch):
    scene = _configure_rotator(monkeypatch, ["classic", "conditional", "forecast"])
    scene.primary_frame_switch_threshold = 1

    scene.draw()

    assert scene.index == 2
    assert scene.themes[1].eligibility_calls == 1


def test_first_eligible_screen_becomes_long_duration_anchor(monkeypatch):
    scene = _configure_rotator(monkeypatch, ["conditional", "classic"])

    scene.reset()

    assert scene.index == 1
    assert scene.anchor_index == 1


def test_disabled_schedule_does_not_call_theme_eligibility(monkeypatch):
    scene = _configure_rotator(
        monkeypatch,
        ["classic", "conditional", "forecast"],
        schedules={"conditional": "disabled"},
    )
    scene.primary_frame_switch_threshold = 1

    scene.draw()

    assert scene.index == 2
    assert scene.themes[1].eligibility_calls == 0


def test_always_schedule_bypasses_theme_eligibility(monkeypatch):
    scene = _configure_rotator(
        monkeypatch,
        ["classic", "conditional", "forecast"],
        schedules={"conditional": "always"},
    )
    scene.primary_frame_switch_threshold = 1

    scene.draw()

    assert scene.index == 1
    assert scene.themes[1].eligibility_calls == 0


def test_all_disabled_screens_leave_the_display_blank(monkeypatch):
    scene = _configure_rotator(
        monkeypatch,
        ["classic", "conditional"],
        schedules={"classic": "disabled", "conditional": "disabled"},
    )

    scene.reset()
    scene.draw()

    assert scene.index == -1
    assert [theme.draw_calls for theme in scene.themes] == [0, 0]


def test_solar_theme_requires_change_greater_than_500_w():
    theme = solar_idle_theme.SolarIdleTheme.__new__(solar_idle_theme.SolarIdleTheme)
    theme.previous_power_reading = (1000.0, 200.0)
    theme.solar = MagicMock()
    theme.solar.get.return_value = {
        "current_power_w": 1500.0,
        "consumption_power_w": 200.0,
    }

    assert theme.should_display() is False

    theme.solar.get.return_value = {
        "current_power_w": 1501.0,
        "consumption_power_w": 200.0,
    }
    theme.previous_power_reading = (1000.0, 200.0)
    assert theme.should_display() is True


def test_intraday_theme_is_eligible_every_fourth_daytime_check(monkeypatch):
    theme = solar_intraday_idle_theme.SolarIntradayIdleTheme.__new__(
        solar_intraday_idle_theme.SolarIntradayIdleTheme
    )
    theme.eligibility_checks = 0
    cfg = SimpleNamespace(observer_lat=51.5, observer_lng=-0.1)
    monkeypatch.setattr(solar_intraday_idle_theme.Config, "instance", lambda: cfg)
    daylight = {"value": True}
    monkeypatch.setattr(
        solar_intraday_idle_theme,
        "is_daytime",
        lambda lat, lng: daylight["value"],
    )

    assert [theme.should_display() for _ in range(4)] == [False, False, False, True]
    daylight["value"] = False
    assert [theme.should_display() for _ in range(4)] == [False, False, False, False]


def test_idle_theme_schedule_sanitizes_entries_and_wraps_overnight():
    cfg = Config.__new__(Config)
    cfg.data_store = {
        "idle_theme_schedules": {
            "solar": [
                {"time": "20:00", "mode": "disabled"},
                {"time": "08:00", "mode": "eligible"},
                {"time": "08:00", "mode": "always"},
                {"time": "99:99", "mode": "disabled"},
                {"time": "12:00", "mode": "invalid"},
            ],
            "unknown": [{"time": "00:00", "mode": "always"}],
        }
    }

    assert cfg.idle_theme_schedules["solar"] == [
        {"time": "08:00", "mode": "always"},
        {"time": "20:00", "mode": "disabled"},
    ]
    assert cfg.idle_theme_schedule_mode("solar", time(10, 0)) == "always"
    assert cfg.idle_theme_schedule_mode("solar", time(22, 0)) == "disabled"
    assert cfg.idle_theme_schedule_mode("solar", time(6, 0)) == "disabled"


def test_idle_theme_schedule_form_parser_validates_json():
    from web.app import _parse_idle_theme_schedules_form

    result = _parse_idle_theme_schedules_form(
        {
            "idle_theme_schedules_json": json.dumps(
                {
                    "solar": [
                        {"time": "21:00", "mode": "disabled"},
                        {"time": "07:00", "mode": "eligible"},
                        {"time": "07:00", "mode": "always"},
                        {"time": "12:00", "mode": "bad"},
                    ],
                    "unknown": [{"time": "00:00", "mode": "always"}],
                }
            )
        }
    )

    assert result == {
        "idle_theme_schedules": {
            "solar": [
                {"time": "07:00", "mode": "always"},
                {"time": "21:00", "mode": "disabled"},
            ]
        }
    }