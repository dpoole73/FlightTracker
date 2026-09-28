"""
IdleScene - priority-0 fallback scene with pluggable, rotating themes.

Reads cfg.idle_theme_order (a list of enabled theme keys, in rotation
order) and cycles through the matching theme instances, holding each on
screen for cfg.idle_theme_rotation_seconds before advancing.  A single
enabled theme behaves exactly as before (no rotation).

BaseIdleScene provides the shared scene-protocol boilerplate (priority,
has_data, active, on_enter, reset, frame-throttled draw); each theme
implements draw_content() with its own layout - this is unchanged.

WeatherService (a singleton daemon thread in theme_utilities.py) is
started lazily and shared by all weather-based themes.  StockService
(scenes/idle/themes/stock_idle_theme.py) follows the same pattern for
the stock theme.
"""

from __future__ import annotations

from scenes.idle.themes.theme_utilities import WeatherService
from setup import frames
from setup.configuration import Config

PRIORITY = 0


class BaseIdleScene:
    """
    Base class for all idle screen themes.

    Provides the scene protocol expected by SceneManager:
        priority, poll(), has_data(), active(), on_enter(), reset(), draw()

    Subclasses implement:
        theme_init()        - set up theme-specific state (called from __init__)
        theme_reset()       - clear theme-specific state (called from reset())
        draw_content(count) - render one second's worth of content
    """

    priority = PRIORITY

    def __init__(self, canvas, panel):
        self.canvas = canvas
        self.panel = panel

        # Scene frame counter
        self.frame: int = 0

        # Shared weather service (singleton daemon thread)
        self.weather = WeatherService.instance()

        # Let the theme initialise its own state
        self.theme_init()

    # ------------------------------------------------------------------
    # Scene protocol
    # ------------------------------------------------------------------

    def poll(self) -> None:
        pass  # weather is handled by the background WeatherService thread

    def has_data(self) -> bool:
        return True  # always the guaranteed fallback

    def active(self) -> bool:
        return True  # never interrupted mid-draw

    def on_enter(self) -> None:
        """Called by SceneManager on scene transition. Clears canvas then resets."""
        self.panel.clear(self.canvas)
        self.reset()

    def reset(self) -> None:
        """Clear cached draw state so everything redraws on next tick."""
        self.frame = 0
        self.theme_reset()

    def draw(self) -> None:
        """Called every frame. Throttles to ~1 fps, then calls draw_content()."""
        self.frame += 1
        if self.frame % int(frames.PER_SECOND):
            return

        count = self.frame // int(frames.PER_SECOND)
        self.draw_content(count)

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    def theme_init(self) -> None:
        """Override to set up theme-specific state."""
        pass

    def theme_reset(self) -> None:
        """Override to clear theme-specific state."""
        pass

    def draw_content(self, count: int) -> None:
        """Override to render content. Called once per second."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Theme registry - maps config string to theme class.
# Imports are deferred to avoid circular dependencies (themes import
# BaseIdleScene from this module).
# ---------------------------------------------------------------------------


def _load_themes() -> dict:
    """Lazy-load theme classes to avoid circular imports."""
    from scenes.idle.themes.classic_idle_theme import ClassicIdleTheme
    from scenes.idle.themes.conditions_idle_theme import ConditionsIdleTheme
    from scenes.idle.themes.forecast_idle_theme import ForecastIdleTheme
    from scenes.idle.themes.solar_idle_theme import SolarIdleTheme
    from scenes.idle.themes.stock_idle_theme import StockIdleTheme

    return {
        "classic": ClassicIdleTheme,
        "forecast": ForecastIdleTheme,
        "conditions": ConditionsIdleTheme,
        "stock": StockIdleTheme,
        "solar": SolarIdleTheme,
    }


# ---------------------------------------------------------------------------
# RotatingIdleScene - the actual priority-0 scene registered with
# SceneManager.  Wraps one-or-more theme instances and advances through
# them on a timer.  Satisfies the same scene protocol as any other scene,
# so SceneManager doesn't need to know idle is now potentially plural.
# ---------------------------------------------------------------------------


class RotatingIdleScene:
    priority = PRIORITY

    def __init__(self, canvas, panel):
        self.canvas = canvas
        self.panel = panel

        self.themes: list = []
        self.index: int = 0
        self.elapsed_frames: int = 0
        self.frame_switch_threshold: int = int(
            Config.instance().idle_theme_rotation_seconds * frames.PER_SECOND
        )

        self._build_themes()

    def _build_themes(self) -> None:
        cfg = Config.instance()
        registry = _load_themes()

        order = cfg.idle_theme_order  # already validated/filtered by Config
        self.themes = [registry[name](self.canvas, self.panel) for name in order]

        if not self.themes:
            # Should be unreachable - Config.idle_theme_order always falls
            # back to at least ["classic"] - but never leave the display
            # with nothing to show.
            self.themes = [registry["classic"](self.canvas, self.panel)]

        self.index = 0
        self.elapsed_frames = 0

    # ------------------------------------------------------------------
    # Scene protocol
    # ------------------------------------------------------------------

    def poll(self) -> None:
        pass  # each theme's own services (weather/stock) poll in the background

    def has_data(self) -> bool:
        return True  # always the guaranteed fallback

    def active(self) -> bool:
        return True  # never interrupted mid-draw

    def on_enter(self) -> None:
        """
        Called by SceneManager whenever we arrive at idle from another
        scene (e.g. the last flight left). Resets rotation to the first
        enabled theme rather than resuming mid-rotation, so re-entering
        idle is always predictable.
        """
        self.panel.clear(self.canvas)
        self.reset()

    def reset(self) -> None:
        self.index = 0
        self.elapsed_frames = 0
        if self.themes:
            self.themes[self.index].on_enter()

    def draw(self) -> None:
        if not self.themes:
            return

        self.themes[self.index].draw()

        if len(self.themes) == 1:
            return  # nothing to rotate to

        self.elapsed_frames += 1
        if self.elapsed_frames >= self.frame_switch_threshold:
            self.elapsed_frames = 0
            self.index = (self.index + 1) % len(self.themes)
            self.themes[self.index].on_enter()


def IdleScene(canvas, panel):
    """
    Factory that returns the priority-0 idle scene.

    Kept as a plain function (rather than exposing RotatingIdleScene
    directly) so display/__init__.py's
    ``self.scene_manager.register(IdleScene(self.canvas, self.panel))``
    call needs no changes.
    """
    return RotatingIdleScene(canvas, panel)
