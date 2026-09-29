"""
SolarHistoryIdleTheme - usage vs. generation bar graph over the lookback
window (cfg.solar_lookback_days). Separate theme from the live-power
SolarIdleTheme, so each can be independently enabled/ordered via
idle_theme_order, and each pulls from its own SolarService method
(get_history() here vs. get() for live).
"""

from __future__ import annotations

from scenes.idle.idle_scene import BaseIdleScene
from setup import colours, fonts, screen
from setup.configuration import Config
from utilities.solar_service import SolarService

SOLAR_FONT = fonts.regular

LABEL_Y = 9
BAR_TOP_Y = 11
BOTTOM_MARGIN = 1
MAX_BAR_HEIGHT = max(4, screen.HEIGHT - BAR_TOP_Y - BOTTOM_MARGIN)

USE_X = 1
GEN_X = screen.WIDTH // 2

BAR_WIDTH_PER_POINT = 2
MAX_POINTS = (screen.WIDTH // 2) // BAR_WIDTH_PER_POINT


class SolarHistoryIdleTheme(BaseIdleScene):
    """Usage (red) vs. generation (green) bar graph over the lookback window."""

    def theme_init(self) -> None:
        self.solar = SolarService.instance()
        self.labels_drawn = False

    def theme_reset(self) -> None:
        self.labels_drawn = False

    def draw_content(self, count: int) -> None:
        cfg = Config.instance()
        data = self.solar.get_history()

        # Full wipe each redraw (once/sec) - simplest way to avoid stale
        # bars when the point count changes between fetches, and cheap
        # enough at this refresh rate.
        self.panel.draw_square(self.canvas, 0, 0, screen.WIDTH, screen.HEIGHT, colours.BLACK)
        self.labels_drawn = False

        if data is None:
            self._draw_waiting_state(cfg)
            return

        usage = data["usage"][-MAX_POINTS:]
        production = data["production"][-MAX_POINTS:]
        max_value = max([*usage, *production, 1])  # avoid div-by-zero on all-zero data

        self._draw_labels()
        self._draw_bars(USE_X, usage, max_value, colours.RED)
        self._draw_bars(GEN_X, production, max_value, colours.GREEN)

    # ------------------------------------------------------------------

    def _draw_waiting_state(self, cfg) -> None:
        self._draw_labels()
        text = (
            "NO AUTH"
            if not (cfg.solar_client_id and cfg.solar_client_secret and cfg.solar_api_key)
            else "..."
        )
        self.panel.draw_text(self.canvas, SOLAR_FONT, USE_X, BAR_TOP_Y + 8, colours.WHITE, text)

    def _draw_labels(self) -> None:
        if self.labels_drawn:
            return
        self.panel.draw_text(self.canvas, SOLAR_FONT, USE_X, LABEL_Y, colours.RED, "Used")
        self.panel.draw_text(self.canvas, SOLAR_FONT, GEN_X, LABEL_Y, colours.GREEN, "Gen")
        self.labels_drawn = True

    def _draw_bars(self, x0: int, values: list[float], max_value: float, colour) -> None:
        for i, value in enumerate(values):
            height = (value / max_value) * MAX_BAR_HEIGHT
            top_y = int(round(BAR_TOP_Y + (MAX_BAR_HEIGHT - height)))
            bottom_y = BAR_TOP_Y + MAX_BAR_HEIGHT
            x = x0 + BAR_WIDTH_PER_POINT * i
            # Two-pixel-wide bar, matching the original's paired DrawLine calls.
            self.panel.draw_line(self.canvas, x, top_y, x, bottom_y, colour)
            self.panel.draw_line(self.canvas, x + 1, top_y, x + 1, bottom_y, colour)