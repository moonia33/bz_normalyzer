"""Mandagus HTTP gamintojų/mažmenininkų svetainėms: User-Agent, pauzė, 429 backoff."""

from __future__ import annotations

import logging
import time

import requests

USER_AGENT = "Mozilla/5.0 (compatible; bz-normalyzer/1.0)"
MIN_DELAY = 1.0
MAX_RETRIES = 4

logger = logging.getLogger(__name__)


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get(
    s: requests.Session, url: str, delay: float = MIN_DELAY, sleep=time.sleep, **kwargs
) -> requests.Response | None:
    """GET su pauze po kiekvienos užklausos; 429/5xx -> eksponentinis backoff.

    Tinklo klaida ar nepavykęs bandymas grąžina None (skreperis tęsia kitu puslapiu)."""
    delay = max(delay, MIN_DELAY)
    wait = delay * 5
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = s.get(url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            logger.warning("%s: %s", url, exc)
            response = None
        sleep(delay)
        if response is not None and response.status_code not in (
            429,
            500,
            502,
            503,
            504,
        ):
            return response
        if attempt == MAX_RETRIES:
            break
        retry_after = (
            response.headers.get("Retry-After") if response is not None else None
        )
        sleep(float(retry_after) if retry_after and retry_after.isdigit() else wait)
        wait *= 2
    logger.warning("%s: nepavyko po %s bandymų", url, MAX_RETRIES + 1)
    return None
