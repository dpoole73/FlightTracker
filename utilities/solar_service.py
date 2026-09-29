"""
SolarService - background OAuth + fetch for Enphase Enlighten data.

Serves two independent idle themes with two independent fetch cadences,
sharing one bearer-token lifecycle:
  - LIVE (get()): summary's current_power/energy_today + latest_telemetry's
    live consumption, refreshed every cfg.solar_refresh_seconds. Powers
    SolarIdleTheme ("solar").
  - HISTORY (get_history()): consumption_lifetime/energy_lifetime daily
    totals over cfg.solar_lookback_days, refreshed every
    cfg.solar_history_refresh_seconds (much less often - daily totals
    don't need minute-by-minute polling). Powers SolarHistoryIdleTheme
    ("solar_history").

Both were originally one endpoint pair (the old utilities/solar.py's
grab_solar_data()); split because "live" and "history" are now two
separate idle themes, independently enabled/ordered like any other theme,
rather than one scene trying to show both.

latest_telemetry returns per-channel (per phase/leg) readings under
devices.meters, not a single total - _parse_latest_telemetry() sums the
"consumption" channels, skipping any non-reporting (null) channel.
Confirmed against a real API response; see that function's docstring.

Same OAuth refresh-token flow, retry session, and DNS-error detection as
the original - restructured as a singleton daemon thread (mirrors
TLEManager/StockService) so draw_content() never blocks on a network call.

Enphase rotates the refresh token on every use, so the current refresh
token is persisted to disk (SOLAR_TOKEN_CACHE_PATH) rather than kept only
in Config - otherwise a restart between refreshes would strand you on a
stale, already-used token. The cache lives next to config.json (not a
relative path in the repo), so it survives regardless of the working
directory the app is launched from.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta

from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

from setup.configuration import CONFIG_PATH, Config

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"
API_BASE = "https://api.enphaseenergy.com/api/v4/systems"

SOLAR_TOKEN_CACHE_PATH = CONFIG_PATH.parent / "solar_token_cache.json"

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


def _parse_latest_telemetry(payload: dict) -> float | None:
    """
    Extract live consumption power (Watts), summed across all reporting
    channels.

    Confirmed shape (from a real /latest_telemetry response):
        {"devices": {"meters": [
            {"name": "production", "channel": 1, "power": 843, ...},
            {"name": "production", "channel": 2, "power": 842, ...},
            {"name": "production", "channel": 3, "power": null, ...},
            {"name": "consumption", "channel": 1, "power": 123, ...},
            {"name": "consumption", "channel": 2, "power": 474, ...},
            {"name": "consumption", "channel": 3, "power": null, ...},
        ]}}

    Multi-channel because split-phase/3-phase systems report one reading
    per leg - total system power is the sum across channels, with null
    (non-reporting) channels excluded rather than treated as zero.

    Returns None only if the payload doesn't contain a "consumption"
    meter at all (unexpected account/hardware shape), so the caller can
    fall back gracefully and log the raw payload for a human to check.
    """
    meters = payload.get("devices", {}).get("meters")
    if isinstance(meters, list):
        consumption_meters = [m for m in meters if m.get("name") == "consumption"]
        if consumption_meters:
            readings = [m["power"] for m in consumption_meters if m.get("power") is not None]
            # Meters present but every channel currently null (e.g. between
            # reports) is a legitimate, known reading of 0W, not "unknown".
            return float(sum(readings)) if readings else 0.0

    return None


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

            if not (cfg.solar_client_id and cfg.solar_client_secret and cfg.solar_api_key):
                time.sleep(60)  # nothing configured yet - check back periodically
                continue

            if self.live_state.is_due(now, cfg.solar_refresh_seconds):
                self._fetch_live(cfg)

            if self.history_state.is_due(now, cfg.solar_history_refresh_seconds):
                self._fetch_history(cfg)

            time.sleep(30)  # check periodically; each fetch respects its own interval

    # ------------------------------------------------------------------
    # Live fetch: summary (current_power, energy_today) + latest_telemetry
    # (consumption_power, non-fatal if it fails or doesn't parse).
    # ------------------------------------------------------------------

    def _fetch_live(self, cfg) -> None:
        try:
            self._ensure_bearer_token(cfg)

            headers = {"Authorization": f"Bearer {self.bearer_token}"}
            params = {"key": cfg.solar_api_key}

            response = self.session.get(
                f"{API_BASE}/{cfg.solar_system_id}/summary",
                headers=headers,
                params=params,
                timeout=(5, 20),
            )
            if response.status_code == 429:
                raise RuntimeError("rate limited fetching summary")
            response.raise_for_status()
            payload = response.json()

            # Both are required fields on the summary endpoint - a KeyError
            # here is treated the same as any other fetch failure (caught
            # below), rather than silently displaying stale/wrong data.
            current_power_w = float(payload["current_power"])
            energy_today_wh = float(payload["energy_today"])

            # Live consumption isn't on the summary endpoint - fetch it
            # separately. Failure here is non-fatal: production/today's
            # totals are still valid and worth keeping either way.
            consumption_power_w = None
            try:
                telemetry_resp = self.session.get(
                    f"{API_BASE}/{cfg.solar_system_id}/latest_telemetry",
                    headers=headers,
                    params=params,
                    timeout=(5, 20),
                )
                if telemetry_resp.status_code == 429:
                    logger.warning("Solar API: rate limited fetching latest_telemetry")
                else:
                    telemetry_resp.raise_for_status()
                    consumption_power_w = _parse_latest_telemetry(telemetry_resp.json())
                    if consumption_power_w is None:
                        logger.warning(
                            "Solar API: latest_telemetry shape not recognised - "
                            "raw payload: %s",
                            telemetry_resp.text[:500],
                        )
            except (RequestException, ValueError) as exc:
                logger.warning("Solar API: latest_telemetry fetch failed: %s", exc)

        except (RequestException, ValueError, RuntimeError, KeyError) as exc:
            rate_limited = "rate limited" in str(exc)
            with self.lock:
                backoff = self.live_state.record_failure(rate_limited)

            if is_dns_error(exc):
                logger.error("Solar API (live): DNS failure resolving host - will retry")
            else:
                logger.error(
                    "Solar API (live) fetch failed: %s - retrying in %.0fs", exc, backoff
                )
            return

        with self.lock:
            self.current_power_w = current_power_w
            self.consumption_power_w = consumption_power_w
            self.energy_today_wh = energy_today_wh
            self.live_state.record_success()

        logger.info(
            "Solar live reading updated: %.0fW producing, %s consuming, %.2fkWh today",
            current_power_w,
            f"{consumption_power_w:.0f}W" if consumption_power_w is not None else "?",
            energy_today_wh / 1000.0,
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