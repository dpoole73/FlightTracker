"""
SolarService - background fetch for Enphase solar data, from two sources:

  - LIVE (get()): the local Envoy's /production.json over the LAN
    (https://<envoy-ip>/production.json, bearer token, self-signed cert),
    refreshed every cfg.solar_refresh_seconds - no cloud rate limit, so
    this can poll every few seconds. Powers SolarIdleTheme ("solar").
  - HISTORY (get_history()): the Enphase Enlighten cloud API's
    consumption_lifetime/energy_lifetime daily totals over
    cfg.solar_lookback_days, refreshed every
    cfg.solar_history_refresh_seconds (the local Envoy doesn't expose a
    day-by-day breakdown, only lifetime-to-date and last-7-days totals).
    Powers SolarHistoryIdleTheme ("solar_history").

LIVE and HISTORY use different auth entirely: the local Envoy token is a
long-lived, pre-generated JWT (no refresh dance needed - see
_fetch_live/cfg.solar_local_token), while HISTORY still uses the cloud
OAuth refresh-token flow (_ensure_bearer_token), since only the cloud API
has the daily-history endpoints.

The local Envoy uses a self-signed certificate, so LIVE requests disable
TLS verification (verify=False) - standard practice for this kind of
local-network integration (e.g. Home Assistant's own Envoy integration
does the same). InsecureRequestWarning is suppressed once at import time
so this doesn't spam the logs on every poll.

Enphase's cloud API rotates the refresh token on every use, so the
current refresh token is persisted to disk (SOLAR_TOKEN_CACHE_PATH)
rather than kept only in Config - otherwise a restart between refreshes
would strand you on a stale, already-used token. The cache lives next to
config.json (not a relative path in the repo), so it survives regardless
of the working directory the app is launched from.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta

import urllib3
from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

from setup.configuration import CONFIG_PATH, Config

# The local Envoy's cert is self-signed - suppress the per-request warning
# requests/urllib3 would otherwise log for every verify=False call.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"
API_BASE = "https://api.enphaseenergy.com/api/v4/systems"

SOLAR_TOKEN_CACHE_PATH = CONFIG_PATH.parent / "solar_token_cache.json"
SOLAR_INTRADAY_CACHE_PATH = CONFIG_PATH.parent / "solar_intraday_cache.json"

INTRADAY_BUCKETS = 48  # half-hourly for one day
INTRADAY_BUCKET_MINUTES = 24 * 60 // INTRADAY_BUCKETS  # 30

# How often the intraday cache is written to disk at most, outside of a
# bucket boundary or day rollover (which always save immediately). Keeps
# SD-card writes down to roughly once a minute rather than once per poll.
INTRADAY_SAVE_INTERVAL_SECONDS = 60

BEARER_TOKEN_TTL = timedelta(days=1)
BEARER_TOKEN_REFRESH_MARGIN = timedelta(minutes=10)

# Backoff after a failed refresh: starts at 1 minute, doubles each failure,
# capped at 1 hour. Mirrors TLEManager's backoff in utilities/tle_manager.py.
BACKOFF_MIN = 60.0
BACKOFF_MAX = 3600.0
# Enphase's free tier is rate-limited hard - a 429 backs off much further
# than a normal failure so we don't dig the hole deeper.
RATE_LIMIT_BACKOFF = 3600.0


def is_dns_error(exc: Exception) -> bool:
    cause = exc
    while cause:
        if isinstance(cause, socket.gaierror):
            return True
        cause = cause.__cause__
    return False


def _build_session() -> Session:
    session = Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=2,
        allowed_methods=["GET", "POST"],
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=2, pool_maxsize=2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _parse_production_json(payload: dict) -> tuple[float, float, float, float]:
    """
    Extract (production_w, consumption_w, production_today_wh,
    consumption_today_wh) from the local Envoy's /production.json.

    Confirmed shape (from a real local response):
        {"production": [
            {"type": "inverters", "wNow": 0, ...},
            {"type": "eim", "measurementType": "production",
             "wNow": 0.0, "whToday": 14660.189, ...},
        ],
        "consumption": [
            {"type": "eim", "measurementType": "total-consumption",
             "wNow": 691.253, "whToday": 17976.0, ...},
            {"type": "eim", "measurementType": "net-consumption", ...},
        ]}

    The "inverters" production entry reports per-panel/DC-side data and is
    not the one we want; the "eim" (whole-home CT meter) entry with
    measurementType "production" is the real AC production total.

    Consumption has two "eim" entries: "total-consumption" (gross home
    load, what the house is actually drawing) vs "net-consumption" (import
    from/export to the grid, can go negative). "total-consumption" is used
    here since "how much power is my house using" is what was asked for.

    whToday on each entry resets to ~0 at local midnight - that's what
    _update_intraday() relies on to build the half-hourly history below.

    Raises KeyError/StopIteration (caught by the caller like any other
    fetch failure) if either expected entry is missing, rather than
    silently returning a wrong number.
    """
    production_entry = next(
        p for p in payload["production"] if p.get("measurementType") == "production"
    )
    consumption_entry = next(
        c for c in payload["consumption"] if c.get("measurementType") == "total-consumption"
    )
    return (
        float(production_entry["wNow"]),
        float(consumption_entry["wNow"]),
        float(production_entry["whToday"]),
        float(consumption_entry["whToday"]),
    )


class _FetchState:
    """Timing/backoff bookkeeping shared by the live and history fetchers."""

    def __init__(self):
        self.fetched_at: float = 0.0
        self.backoff_seconds: float = 0.0
        self.next_attempt_at: float = 0.0

    def is_due(self, now: float, refresh_seconds: int) -> bool:
        return (now - self.fetched_at) >= refresh_seconds and now >= self.next_attempt_at

    def record_success(self) -> None:
        self.fetched_at = time.time()
        self.backoff_seconds = 0.0
        self.next_attempt_at = 0.0

    def record_failure(self, rate_limited: bool) -> float:
        self.backoff_seconds = (
            RATE_LIMIT_BACKOFF
            if rate_limited
            else BACKOFF_MIN
            if self.backoff_seconds <= 0.0
            else min(self.backoff_seconds * 2.0, BACKOFF_MAX)
        )
        self.next_attempt_at = time.time() + self.backoff_seconds
        return self.backoff_seconds


class SolarService:
    _instance: "SolarService | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "SolarService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance.start()
            return cls._instance

    def __init__(self):
        self.lock = threading.Lock()
        self.session = _build_session()

        self.bearer_token: str | None = None
        self.bearer_token_fetched_at: datetime = datetime.min
        self.refresh_token: str | None = None

        # Live reading (summary + latest_telemetry)
        self.current_power_w: float | None = None
        self.consumption_power_w: float | None = None
        self.energy_today_wh: float | None = None
        self.live_state = _FetchState()

        # History (consumption_lifetime + energy_lifetime)
        self.usage: list[float] = []
        self.production: list[float] = []
        self.history_state = _FetchState()

        # Intraday (half-hourly production/consumption, built locally from
        # whToday deltas - see _update_intraday()). Loaded from disk so a
        # restart partway through the day doesn't lose today's progress.
        self.intraday_date: str | None = None
        self.intraday_production_wh: list[float] = [0.0] * INTRADAY_BUCKETS
        self.intraday_consumption_wh: list[float] = [0.0] * INTRADAY_BUCKETS
        self.intraday_last_production_wh_today: float | None = None
        self.intraday_last_consumption_wh_today: float | None = None
        self.intraday_last_bucket_index: int | None = None
        self.intraday_last_saved_at: float = 0.0
        self._load_intraday_cache()

    def start(self) -> None:
        threading.Thread(
            target=self.run_loop, daemon=True, name="solar-service"
        ).start()

    def get(self) -> dict | None:
        """Non-blocking: last successfully fetched live reading, or None if
        nothing has been fetched yet (or auth isn't configured).
        consumption_power_w may be None even when a reading exists, if
        latest_telemetry's shape didn't match _parse_latest_telemetry()."""
        with self.lock:
            if self.current_power_w is None:
                return None
            return {
                "current_power_w": self.current_power_w,
                "consumption_power_w": self.consumption_power_w,
                "energy_today_wh": self.energy_today_wh,
            }

    def get_history(self) -> dict | None:
        """Non-blocking: last successfully fetched daily-totals history, or
        None if nothing has been fetched yet (or auth isn't configured)."""
        with self.lock:
            if not self.usage and not self.production:
                return None
            return {"usage": list(self.usage), "production": list(self.production)}

    def get_intraday(self) -> dict | None:
        """Non-blocking: today's half-hourly production/consumption so far
        (built locally from live whToday deltas, not an API call), or None
        before the first live reading of the day has come in."""
        with self.lock:
            if self.intraday_date is None:
                return None
            return {
                "production_wh": list(self.intraday_production_wh),
                "consumption_wh": list(self.intraday_consumption_wh),
            }

    def invalidate(self) -> None:
        with self.lock:
            self.live_state = _FetchState()
            self.history_state = _FetchState()

    # ------------------------------------------------------------------
    # Refresh-token cache (survives restarts; rotates on every use)
    # ------------------------------------------------------------------

    def _load_refresh_token(self, cfg) -> str:
        try:
            data = json.loads(SOLAR_TOKEN_CACHE_PATH.read_text())
            token = data.get("refresh_token")
            if token:
                return token
        except Exception:
            pass
        # First run: seed from config (the one-time value from the OAuth
        # authorization-code exchange, done once via Postman/similar).
        return cfg.solar_refresh_token

    def _save_refresh_token(self, token: str) -> None:
        try:
            SOLAR_TOKEN_CACHE_PATH.write_text(json.dumps({"refresh_token": token}))
        except Exception as exc:
            logger.warning("Solar token cache write failed: %s", exc)

    # ------------------------------------------------------------------
    # Intraday half-hourly buckets, built locally from whToday deltas.
    # ------------------------------------------------------------------

    def _load_intraday_cache(self) -> None:
        try:
            data = json.loads(SOLAR_INTRADAY_CACHE_PATH.read_text())
            # Only resume if the cache is from today - a stale cache from a
            # previous day (e.g. the Pi was off overnight) should start
            # fresh, not carry yesterday's bars into today's graph.
            if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                self.intraday_date = data["date"]
                self.intraday_production_wh = list(data["production_wh"])
                self.intraday_consumption_wh = list(data["consumption_wh"])
                self.intraday_last_production_wh_today = data.get("last_production_wh_today")
                self.intraday_last_consumption_wh_today = data.get("last_consumption_wh_today")
                self.intraday_last_bucket_index = data.get("last_bucket_index")
        except Exception:
            pass  # no cache yet, or unreadable/corrupt - start fresh

    def _save_intraday_cache_locked(self) -> None:
        """Caller must already hold self.lock."""
        try:
            SOLAR_INTRADAY_CACHE_PATH.write_text(
                json.dumps(
                    {
                        "date": self.intraday_date,
                        "production_wh": self.intraday_production_wh,
                        "consumption_wh": self.intraday_consumption_wh,
                        "last_production_wh_today": self.intraday_last_production_wh_today,
                        "last_consumption_wh_today": self.intraday_last_consumption_wh_today,
                        "last_bucket_index": self.intraday_last_bucket_index,
                    }
                )
            )
        except Exception as exc:
            logger.warning("Solar intraday cache write failed: %s", exc)

    def _update_intraday(self, production_today_wh: float, consumption_today_wh: float) -> None:
        """
        Accumulate today's half-hourly production/consumption from the
        cumulative whToday counters. Called after every successful live
        fetch - the delta since the last call is added to whichever
        half-hour bucket "now" falls into.

        A delta spanning a bucket boundary (e.g. a reading at 1:59:55 then
        the next at 2:00:05) is simply attributed entirely to the new
        bucket - with polling every few seconds this is negligible, and
        far simpler than splitting a delta proportionally across a
        boundary for a display that's only ever showing rounded Wh anyway.
        """
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        bucket_index = (now.hour * 60 + now.minute) // INTRADAY_BUCKET_MINUTES

        with self.lock:
            is_new_day = self.intraday_date != today_str
            bucket_changed = (not is_new_day) and (bucket_index != self.intraday_last_bucket_index)

            if is_new_day:
                # First reading ever, or the day has rolled over (whToday
                # itself resets to ~0 on the Envoy at local midnight too).
                self.intraday_date = today_str
                self.intraday_production_wh = [0.0] * INTRADAY_BUCKETS
                self.intraday_consumption_wh = [0.0] * INTRADAY_BUCKETS
            else:
                prod_delta = production_today_wh - self.intraday_last_production_wh_today
                cons_delta = consumption_today_wh - self.intraday_last_consumption_wh_today
                # A negative delta without a date change means the Envoy's
                # counter reset underneath us (e.g. it rebooted) - drop
                # this cycle's delta rather than corrupting a bucket with
                # a negative value.
                if prod_delta >= 0:
                    self.intraday_production_wh[bucket_index] += prod_delta
                if cons_delta >= 0:
                    self.intraday_consumption_wh[bucket_index] += cons_delta

            self.intraday_last_production_wh_today = production_today_wh
            self.intraday_last_consumption_wh_today = consumption_today_wh
            self.intraday_last_bucket_index = bucket_index

            due_to_save = (
                is_new_day
                or bucket_changed
                or (time.time() - self.intraday_last_saved_at) >= INTRADAY_SAVE_INTERVAL_SECONDS
            )
            if due_to_save:
                self._save_intraday_cache_locked()
                self.intraday_last_saved_at = time.time()

    def _ensure_bearer_token(self, cfg) -> None:
        with self.lock:
            token_fresh = (
                self.bearer_token is not None
                and datetime.now()
                < self.bearer_token_fetched_at + BEARER_TOKEN_TTL - BEARER_TOKEN_REFRESH_MARGIN
            )
        if token_fresh:
            return

        refresh_token = self._load_refresh_token(cfg)
        response = self.session.post(
            TOKEN_URL,
            auth=(cfg.solar_client_id, cfg.solar_client_secret),
            params={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=(5, 20),
        )
        if response.status_code == 429:
            raise RuntimeError("rate limited during token refresh")
        response.raise_for_status()

        payload = response.json()
        new_bearer = payload.get("access_token")
        new_refresh = payload.get("refresh_token")
        if not new_bearer or not new_refresh:
            raise RuntimeError("token refresh response missing expected fields")

        with self.lock:
            self.bearer_token = new_bearer
            self.bearer_token_fetched_at = datetime.now()
            self.refresh_token = new_refresh
        # Enphase rotates the refresh token on every use - persist the new
        # one immediately so a restart right after doesn't reuse a dead one.
        self._save_refresh_token(new_refresh)

    # ------------------------------------------------------------------
    # Background loop - checks both cadences independently each tick.
    # ------------------------------------------------------------------

    def run_loop(self) -> None:
        while True:
            cfg = Config.instance()
            now = time.time()

            if cfg.solar_local_host and cfg.solar_local_token:
                if self.live_state.is_due(now, cfg.solar_refresh_seconds):
                    self._fetch_live(cfg)

            if cfg.solar_client_id and cfg.solar_client_secret and cfg.solar_api_key:
                if self.history_state.is_due(now, cfg.solar_history_refresh_seconds):
                    self._fetch_history(cfg)

            # Live polling can now be as fast as a few seconds (local
            # network, no rate limit) - check often enough that the
            # configured interval is actually honored rather than only
            # checked once every 30s as before.
            time.sleep(1)

    # ------------------------------------------------------------------
    # Live fetch: local Envoy's /production.json over the LAN. No OAuth -
    # just a long-lived bearer token generated once (e.g. via Postman) -
    # and no rate limit, so this can poll as often as cfg.solar_refresh_seconds
    # allows.
    # ------------------------------------------------------------------

    def _fetch_live(self, cfg) -> None:
        try:
            response = self.session.get(
                f"https://{cfg.solar_local_host}/production.json",
                headers={"Authorization": f"Bearer {cfg.solar_local_token}"},
                timeout=(3, 10),  # local network - fail fast rather than hang
                verify=False,  # self-signed cert on the local Envoy
            )
            response.raise_for_status()
            production_w, consumption_w, today_wh, consumption_today_wh = _parse_production_json(
                response.json()
            )

        except (RequestException, ValueError, KeyError, StopIteration) as exc:
            with self.lock:
                backoff = self.live_state.record_failure(rate_limited=False)

            if is_dns_error(exc):
                logger.error(
                    "Solar local API: could not resolve %s - will retry",
                    cfg.solar_local_host,
                )
            else:
                logger.error(
                    "Solar local API fetch failed: %s - retrying in %.0fs", exc, backoff
                )
            return

        with self.lock:
            self.current_power_w = production_w
            self.consumption_power_w = consumption_w
            self.energy_today_wh = today_wh
            self.live_state.record_success()

        self._update_intraday(today_wh, consumption_today_wh)

        logger.info(
            "Solar live reading updated: %.0fW producing, %.0fW consuming, %.2fkWh today",
            production_w,
            consumption_w,
            today_wh / 1000.0,
        )

    # ------------------------------------------------------------------
    # History fetch: consumption_lifetime + energy_lifetime, daily totals
    # over the configured lookback window. Refreshed much less often than
    # live, since a day's total barely changes intraday except for "today".
    # ------------------------------------------------------------------

    def _fetch_history(self, cfg) -> None:
        try:
            self._ensure_bearer_token(cfg)

            lookback_days = cfg.solar_lookback_days
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            end_date = datetime.now().strftime("%Y-%m-%d")
            headers = {"Authorization": f"Bearer {self.bearer_token}"}
            params = {"key": cfg.solar_api_key, "start_date": start_date, "end_date": end_date}

            consumption_resp = self.session.get(
                f"{API_BASE}/{cfg.solar_system_id}/consumption_lifetime",
                headers=headers,
                params=params,
                timeout=(5, 20),
            )
            if consumption_resp.status_code == 429:
                raise RuntimeError("rate limited fetching consumption")
            consumption_resp.raise_for_status()
            usage = consumption_resp.json()["consumption"]

            production_resp = self.session.get(
                f"{API_BASE}/{cfg.solar_system_id}/energy_lifetime",
                headers=headers,
                params=params,
                timeout=(5, 20),
            )
            if production_resp.status_code == 429:
                raise RuntimeError("rate limited fetching production")
            production_resp.raise_for_status()
            production = production_resp.json()["production"]

        except (RequestException, ValueError, RuntimeError, KeyError) as exc:
            rate_limited = "rate limited" in str(exc)
            with self.lock:
                backoff = self.history_state.record_failure(rate_limited)

            if is_dns_error(exc):
                logger.error("Solar API (history): DNS failure resolving host - will retry")
            else:
                logger.error(
                    "Solar API (history) fetch failed: %s - retrying in %.0fs", exc, backoff
                )
            return

        with self.lock:
            self.usage = usage
            self.production = production
            self.history_state.record_success()

        logger.info("Solar history updated: %d day(s) of usage/production", len(usage))
