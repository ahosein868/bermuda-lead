"""Polite HTTP client: per-host rate limiting, retries, on-disk cache."""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class Fetcher:
    """Fetches URLs once, caching bodies on disk so re-runs cost nothing."""

    def __init__(self, cfg: dict) -> None:
        h = cfg["http"]
        self.user_agent: str = h["user_agent"]
        self.min_interval: float = 1.0 / float(h.get("requests_per_second", 1.0))
        self.timeout: float = float(h.get("timeout_seconds", 30))
        self.max_retries: int = int(h.get("max_retries", 3))
        self.cache_dir = Path(h.get("cache_dir", "data/cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_hit: dict[str, float] = {}
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=self.timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        self.stats = {"cache_hits": 0, "fetched": 0, "errors": 0}

    # -- cache ---------------------------------------------------------
    def _cache_file(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.html"

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # -- public --------------------------------------------------------
    def get(self, url: str, *, use_cache: bool = True) -> str | None:
        """Return the response body, or None if the URL could not be fetched."""
        cache_file = self._cache_file(url)
        if use_cache and cache_file.exists():
            self.stats["cache_hits"] += 1
            return cache_file.read_text(encoding="utf-8", errors="replace")

        backoff = 2.0
        for attempt in range(1, self.max_retries + 1):
            self._throttle(url)
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as exc:
                log.warning("fetch error %s (attempt %d/%d): %s", url, attempt, self.max_retries, exc)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 200:
                body = resp.text
                cache_file.write_text(body, encoding="utf-8")
                self.stats["fetched"] += 1
                return body
            if resp.status_code in (429, 500, 502, 503, 504):
                log.warning("status %s for %s, backing off", resp.status_code, url)
                time.sleep(backoff)
                backoff *= 2
                continue
            log.info("status %s for %s, giving up", resp.status_code, url)
            self.stats["errors"] += 1
            return None

        self.stats["errors"] += 1
        return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
