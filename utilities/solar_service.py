"""
SolarService - background OAuth + fetch for Enphase Enlighten solar data.

Ported from the old utilities/solar.py's module-level grab_solar_data().
Same OAuth refresh-token flow, retry session, and DNS-error detection -
restructured as a singleton daemon thread (mirrors TLEManager/StockService)
so draw_content() never blocks on a network call, and moved to a class so
state isn't held in module globals.

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

        self.usage: list[float] = []
        self.production: list[float] = []
        self.fetched_at: float = 0.0

        self.backoff_seconds: float = 0.0
        self.next_attempt_at: float = 0.0

    def start(self) -> None:
        threading.Thread(
            target=self.run_loop, daemon=True, name="solar-service"
        ).start()

    def get(self) -> dict | None:
        """Non-blocking: last successfully fetched usage/production, or
        None if nothing has been fetched yet (or auth isn't configured)."""
        with self.lock:
            if not self.usage and not self.production:
                return None
            return {"usage": list(self.usage), "production": list(self.production)}

    def invalidate(self) -> None:
        with self.lock:
            self.fetched_at = 0.0
            self.backoff_seconds = 0.0
            self.next_attempt_at = 0.0

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
    # Background loop
    # ------------------------------------------------------------------

    def run_loop(self) -> None:
        while True:
            cfg = Config.instance()
            now = time.time()

            if not (cfg.solar_client_id and cfg.solar_client_secret and cfg.solar_api_key):
                time.sleep(60)  # nothing configured yet - check back periodically
                continue

            with self.lock:
                age = now - self.fetched_at
                due = age >= cfg.solar_refresh_seconds and now >= self.next_attempt_at

            if due:
                self.do_fetch(cfg)

            time.sleep(30)  # check periodically; do_fetch itself respects refresh interval

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

    def do_fetch(self, cfg) -> None:
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
                self.backoff_seconds = (
                    RATE_LIMIT_BACKOFF
                    if rate_limited
                    else BACKOFF_MIN
                    if self.backoff_seconds <= 0.0
                    else min(self.backoff_seconds * 2.0, BACKOFF_MAX)
                )
                self.next_attempt_at = time.time() + self.backoff_seconds

            if is_dns_error(exc):
                logger.error("Solar API: DNS failure resolving host - will retry")
            else:
                logger.error(
                    "Solar API fetch failed: %s - retrying in %.0fs",
                    exc,
                    self.backoff_seconds,
                )
            return

        with self.lock:
            self.usage = usage
            self.production = production
            self.fetched_at = time.time()
            self.backoff_seconds = 0.0
            self.next_attempt_at = 0.0

        logger.info(
            "Solar data updated: %d day(s) of usage/production", len(usage)
        )
