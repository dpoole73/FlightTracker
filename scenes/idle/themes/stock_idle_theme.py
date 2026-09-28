"""
StockIdleTheme - stock price idle screen, ported from the old standalone
StockPriceScene mixin.

Fetches via Alpha Vantage's GLOBAL_QUOTE endpoint on a background daemon
thread (StockService), same shape as WeatherService in theme_utilities.py,
so draw_content() only ever reads an already-fetched value and never
blocks the display loop on a network call.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from scenes.idle.idle_scene import BaseIdleScene
from setup import colours, fonts
from setup.configuration import Config

logger = logging.getLogger(__name__)

QUOTE_URL = "https://www.alphavantage.co/query"
HTTP_TIMEOUT = 10

# Backoff after a failed refresh: starts at 1 minute, doubles each failure,
# capped at 1 hour. Mirrors TLEManager's backoff in utilities/tle_manager.py.
BACKOFF_MIN = 60.0
BACKOFF_MAX = 3600.0

# Layout - same positions as the original StockPriceScene
SYMBOL_POSITION = (1, 10)
PRICE_POSITION = (1, 20)
CHANGE_POSITION = (1, 30)
STOCK_FONT = fonts.small


# ---------------------------------------------------------------------------
# StockService - background fetch + cache, shared by any StockIdleTheme
# instance (mirrors WeatherService's singleton pattern).
# ---------------------------------------------------------------------------


class StockService:
    _instance: "StockService | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "StockService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance.start()
            return cls._instance

    def __init__(self):
        self.lock = threading.Lock()
        self.symbol: str | None = None
        self.price: float | None = None
        self.change_percent: float | None = None
        self.fetched_at: float = 0.0
        self.backoff_seconds: float = 0.0
        self.next_attempt_at: float = 0.0

    def start(self) -> None:
        threading.Thread(
            target=self.run_loop, daemon=True, name="stock-service"
        ).start()

    def get(self) -> dict | None:
        """Non-blocking: returns the last successfully fetched quote, or
        None if nothing has been fetched yet (e.g. still on first fetch,
        or no API key configured)."""
        with self.lock:
            if self.price is None:
                return None
            return {
                "symbol": self.symbol,
                "price": self.price,
                "change_percent": self.change_percent,
            }

    def invalidate(self) -> None:
        """Force a refresh on next cycle (e.g. after the symbol changes
        in config)."""
        with self.lock:
            self.fetched_at = 0.0
            self.backoff_seconds = 0.0
            self.next_attempt_at = 0.0

    def run_loop(self) -> None:
        while True:
            cfg = Config.instance()
            now = time.time()

            with self.lock:
                symbol_changed = self.symbol is not None and self.symbol != cfg.stock_symbol
                age = now - self.fetched_at
                due = (
                    age >= cfg.stock_refresh_seconds or symbol_changed
                ) and now >= self.next_attempt_at

            if not cfg.stock_api_key:
                time.sleep(60)  # nothing to do without a key - check back periodically
                continue

            if due:
                self.do_fetch(cfg)

            time.sleep(5)  # check frequently; do_fetch itself respects refresh interval

    def do_fetch(self, cfg) -> None:
        try:
            response = requests.get(
                QUOTE_URL,
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": cfg.stock_symbol,
                    "apikey": cfg.stock_api_key,
                },
                timeout=HTTP_TIMEOUT,
            )
            data = response.json()
            quote = data["Global Quote"]
            price = float(quote["05. price"])
            change_percent = float(quote["10. change percent"].rstrip("%"))
        except Exception as exc:
            self.backoff_seconds = (
                BACKOFF_MIN
                if self.backoff_seconds <= 0.0
                else min(self.backoff_seconds * 2.0, BACKOFF_MAX)
            )
            self.next_attempt_at = time.time() + self.backoff_seconds
            logger.warning(
                "Stock quote fetch failed for %s: %s - retrying in %.0fs",
                cfg.stock_symbol,
                exc,
                self.backoff_seconds,
            )
            return

        with self.lock:
            self.symbol = cfg.stock_symbol
            self.price = price
            self.change_percent = change_percent
            self.fetched_at = time.time()
            self.backoff_seconds = 0.0
            self.next_attempt_at = 0.0

        logger.info(
            "Stock quote updated: %s $%.2f (%+.2f%%)", cfg.stock_symbol, price, change_percent
        )


# ---------------------------------------------------------------------------
# StockIdleTheme
# ---------------------------------------------------------------------------


class StockIdleTheme(BaseIdleScene):
    """Stock price idle layout: symbol, price, and change percent."""

    def theme_init(self) -> None:
        self.stock = StockService.instance()
        self.last_price_str: str | None = None
        self.last_change_str: str | None = None
        self.last_symbol: str | None = None

    def theme_reset(self) -> None:
        self.last_price_str = None
        self.last_change_str = None
        self.last_symbol = None

    def draw_content(self, count: int) -> None:
        cfg = Config.instance()
        quote = self.stock.get()

        if quote is None:
            self._draw_waiting_state(cfg)
            return

        colour = (
            colours.GREEN
            if quote["change_percent"] > 0
            else colours.RED
            if quote["change_percent"] < 0
            else colours.WHITE
        )

        self._draw_symbol(quote["symbol"], colour)
        self._draw_price(f"${quote['price']:.2f}", colour)
        self._draw_change(
            f"{'+' if quote['change_percent'] > 0 else ''}{quote['change_percent']:.2f}%",
            colour,
        )

    # ------------------------------------------------------------------
    # No-data state (no API key set, or first fetch still pending)
    # ------------------------------------------------------------------

    def _draw_waiting_state(self, cfg) -> None:
        text = "NO KEY" if not cfg.stock_api_key else "..."
        self._draw_symbol(cfg.stock_symbol, colours.WHITE)
        self._draw_price(text, colours.WHITE)
        self._erase_change()

    # ------------------------------------------------------------------
    # Erase-then-redraw helpers - avoids flicker by only touching pixels
    # that actually changed, matching ClassicIdleTheme's approach.
    # ------------------------------------------------------------------

    def _draw_symbol(self, symbol: str, colour) -> None:
        if self.last_symbol == symbol:
            return
        if self.last_symbol is not None:
            self.panel.draw_text(
                self.canvas,
                STOCK_FONT,
                SYMBOL_POSITION[0],
                SYMBOL_POSITION[1],
                colours.BLACK,
                self.last_symbol,
            )
        self.panel.draw_text(
            self.canvas, STOCK_FONT, SYMBOL_POSITION[0], SYMBOL_POSITION[1], colour, symbol
        )
        self.last_symbol = symbol

    def _draw_price(self, price_str: str, colour) -> None:
        if self.last_price_str == price_str:
            return
        if self.last_price_str is not None:
            self.panel.draw_text(
                self.canvas,
                STOCK_FONT,
                PRICE_POSITION[0],
                PRICE_POSITION[1],
                colours.BLACK,
                self.last_price_str,
            )
        self.panel.draw_text(
            self.canvas, STOCK_FONT, PRICE_POSITION[0], PRICE_POSITION[1], colour, price_str
        )
        self.last_price_str = price_str

    def _draw_change(self, change_str: str, colour) -> None:
        if self.last_change_str == change_str:
            return
        self._erase_change()
        self.panel.draw_text(
            self.canvas, STOCK_FONT, CHANGE_POSITION[0], CHANGE_POSITION[1], colour, change_str
        )
        self.last_change_str = change_str

    def _erase_change(self) -> None:
        if self.last_change_str is not None:
            self.panel.draw_text(
                self.canvas,
                STOCK_FONT,
                CHANGE_POSITION[0],
                CHANGE_POSITION[1],
                colours.BLACK,
                self.last_change_str,
            )
            self.last_change_str = None
