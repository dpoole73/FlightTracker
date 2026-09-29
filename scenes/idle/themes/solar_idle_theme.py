"""
SolarIdleTheme - live production and consumption side by side, plus
today's cumulative production. Two-column layout echoes the original
SolarScene's Used/Gen bar-graph columns, just with live numbers instead
of history bars.

Consumption comes from SolarService's latest_telemetry parsing, which
may be None if Enphase's response shape didn't match what we guessed
(see _parse_latest_telemetry's docstring in solar_service.py) - handled
here by showing "--" for that column rather than crashing or hiding the
whole theme, since production+today are still valid either way.
"""

from __future__ import annotations

from scenes.idle.idle_scene import BaseIdleScene
from setup import colours, fonts, screen
from setup.configuration import Config
from utilities.solar_service import SolarService

LABEL_FONT = fonts.regular  # "Gen"/"Use" - short enough to fit at this size
VALUE_FONT = fonts.small  # 4px/char - "regular" (6px/char) overlapped between columns

GEN_X = 1
GEN_Y = 10
USE_X = 1
USE_Y = 20
TODAY_X = 1
TODAY_Y = 30

class SolarIdleTheme(BaseIdleScene):
    """Live generation vs. consumption , plus today's total generation."""

    def theme_init(self) -> None:
        self.solar = SolarService.instance()
        self.labels_drawn = False
        self.last_gen_str: str | None = None
        self.last_use_str: str | None = None
        self.last_today_str: str | None = None

    def theme_reset(self) -> None:
        self.labels_drawn = False
        self.last_gen_str = None
        self.last_use_str = None
        self.last_today_str = None

    def draw_content(self, count: int) -> None:
        cfg = Config.instance()
        reading = self.solar.get()


        if reading is None:
            self._draw_waiting_state(cfg)
            return

        gen_kw = reading["current_power_w"] / 1000.0
        today_kwh = reading["energy_today_wh"] / 1000.0
        use_w = reading["consumption_power_w"]

        self._draw_gen(f"{gen_kw:.2f}kW", colours.GREEN)
        self._draw_use(
            f"{use_w / 1000.0:.2f}kW" if use_w is not None else "--",
            colours.RED
        )
        self._draw_today(f"{today_kwh:.1f}kWh")

    # ------------------------------------------------------------------

    def _draw_waiting_state(self, cfg) -> None:
        text = (
            "NO AUTH"
            if not (cfg.solar_client_id and cfg.solar_client_secret and cfg.solar_api_key)
            else "..."
        )
        self._draw_gen(text, colours.WHITE)
        self._erase_use()
        self._erase_today()

    # ------------------------------------------------------------------
    # Erase-then-redraw helpers - avoids flicker by only touching pixels
    # that actually changed, matching StockIdleTheme's approach.
    # ------------------------------------------------------------------

    def _draw_gen(self, text: str, colour) -> None:
        if self.last_gen_str == text:
            return
        if self.last_gen_str is not None:
            self.panel.draw_text(
                self.canvas, VALUE_FONT, GEN_X, GEN_Y, colours.BLACK, self.last_gen_str
            )
        self.panel.draw_text(self.canvas, VALUE_FONT, GEN_X, GEN_Y, colour, text)
        self.last_gen_str = text

    def _draw_use(self, text: str, colour) -> None:
        if self.last_use_str == text:
            return
        self._erase_use()
        self.panel.draw_text(self.canvas, VALUE_FONT, USE_X, USE_Y, colour, text)
        self.last_use_str = text

    def _erase_use(self) -> None:
        if self.last_use_str is not None:
            self.panel.draw_text(
                self.canvas, VALUE_FONT, USE_X, USE_Y, colours.BLACK, self.last_use_str
            )
            self.last_use_str = None

    def _draw_today(self, text: str) -> None:
        if self.last_today_str == text:
            return
        self._erase_today()
        self.panel.draw_text(self.canvas, VALUE_FONT, TODAY_X, TODAY_Y, colours.WHITE, text)
        self.last_today_str = text

    def _erase_today(self) -> None:
        if self.last_today_str is not None:
            self.panel.draw_text(
                self.canvas, VALUE_FONT, TODAY_X, TODAY_Y, colours.BLACK, self.last_today_str
            )
            self.last_today_str = None