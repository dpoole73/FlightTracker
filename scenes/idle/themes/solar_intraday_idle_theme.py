"""
SolarIntradayIdleTheme - today's production/consumption by half-hour,
drawn as a single mirrored bar graph: production bars grow upward from a
center line, consumption bars grow downward from the same center line.

Data comes from SolarService.get_intraday(), which builds this locally
from live whToday deltas (see solar_service.py's _update_intraday) -
there's no API that hands back ready-made hourly/half-hourly history, so
this is accumulated over the course of the day from the same live feed
SolarIdleTheme already polls.
"""

from __future__ import annotations

from scenes.idle.idle_scene import BaseIdleScene
from setup import colours, fonts, screen
from setup.configuration import Config
from utilities.solar_service import INTRADAY_BUCKETS, SolarService
from utilities.sun_times import is_daytime

CENTER_Y = screen.HEIGHT // 2
MAX_HALF_HEIGHT = max(2, CENTER_Y - 2)  # leave a small margin top and bottom

BAR_WIDTH = 1
GRAPH_WIDTH = INTRADAY_BUCKETS * BAR_WIDTH
START_X = max(0, (screen.WIDTH - GRAPH_WIDTH) // 2)

CENTER_LINE_COLOUR = colours.GREY
DISPLAY_EVERY_ELIGIBILITY_CHECKS = 4


class SolarIntradayIdleTheme(BaseIdleScene):
    """Mirrored half-hourly bar graph: generation up, usage down, from a center line."""

    default_display_mode = "eligible"

    def theme_init(self) -> None:
        self.solar = SolarService.instance()
        self.eligibility_checks = 0

    def theme_reset(self) -> None:
        pass  # full wipe every redraw - nothing to carry between frames

    def should_display(self) -> bool:
        self.eligibility_checks += 1
        if self.eligibility_checks % DISPLAY_EVERY_ELIGIBILITY_CHECKS:
            return False

        cfg = Config.instance()
        return is_daytime(cfg.observer_lat, cfg.observer_lng)

    def draw_content(self, count: int) -> None:
        # Full wipe each redraw (once/sec) - bars can only grow taller as
        # the day goes on, but wiping avoids any edge case with stale
        # pixels from a previous day's taller bars at the same bucket.
        self.panel.draw_square(self.canvas, 0, 0, screen.WIDTH, screen.HEIGHT, colours.BLACK)

        data = self.solar.get_intraday()
        if data is None:
            self._draw_waiting_state()
            return

        production = data["production_wh"]
        consumption = data["consumption_wh"]
        max_value = max([*production, *consumption, 1.0])  # avoid div-by-zero pre-dawn

        # Center reference line, so a bar of height 0 is still visually
        # anchored rather than just invisible.
        self.panel.draw_line(
            self.canvas, START_X, CENTER_Y, START_X + GRAPH_WIDTH - 1, CENTER_Y, CENTER_LINE_COLOUR
        )

        for i in range(INTRADAY_BUCKETS):
            x = START_X + i * BAR_WIDTH

            prod_height = int(round((production[i] / max_value) * MAX_HALF_HEIGHT))
            if prod_height > 0:
                self.panel.draw_line(
                    self.canvas, x, CENTER_Y - prod_height, x, CENTER_Y - 1, colours.GREEN
                )

            cons_height = int(round((consumption[i] / max_value) * MAX_HALF_HEIGHT))
            if cons_height > 0:
                self.panel.draw_line(
                    self.canvas, x, CENTER_Y + 1, x, CENTER_Y + cons_height, colours.RED
                )

    def _draw_waiting_state(self) -> None:
        self.panel.draw_text(
            self.canvas, fonts.extrasmall, 1, CENTER_Y, colours.WHITE, "..."
        )
