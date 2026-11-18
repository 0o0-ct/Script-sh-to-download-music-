#!/usr/bin/env python3
"""Shared rate limiter for MusicBrainz HTTP requests.

MusicBrainz enforces ~1 request/second per IP on their public webservice.
This module provides a global async semaphore, response cache with TTL,
exponential backoff retries, and HTTP session pooling so that all callers
share the same rate limit instead of each worker racing independently.

Usage:
    limiter = MusicBrainzRateLimiter()
    async with limiter:
        async with limiter.session.get(url) as resp:
            data = await resp.json()

Or use the convenience method:
    data = await limiter.fetch_json(url, params, headers)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("otterrip_core.rate_limiter")

# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT: float = 1.0  # requests per second
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_BACKOFF_BASE: float = 1.0  # seconds
DEFAULT_CACHE_TTL: int = 300  # 5 minutes
DEFAULT_HTTP_TIMEOUT: float = 10.0


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

class _ResponseCache:
    """Simple in-memory LRU-style cache keyed by URL + params hash."""

    def __init__(self, ttl_seconds: int = DEFAULT_CACHE_TTL):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, value)

    def _key(self, url: str, params: dict[str, str] | None = None) -> str:
        raw = url
        if params:
            sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            raw = url + "?" + sorted_params
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get(self, url: str, params: dict[str, str] | None = None) -> Optional[Any]:
        key = self._key(url, params)
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if (time.time() - ts) > self.ttl:
            del self._store[key]
            return None
        return value

    def set(self, url: str, value: Any, params: dict[str, str] | None = None) -> None:
        key = self._key(url, params)
        self._store[key] = (time.time(), value)

    def clear(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class MusicBrainzRateLimiter:
    """Async rate limiter for MusicBrainz API calls.

    Provides:
    - Global semaphore (~1 req/s shared across all callers).
    - Response cache with configurable TTL.
    - Exponential backoff retries (max 3 attempts).
    - Shared aiohttp.ClientSession for connection pooling.

    The semaphore is module-level (singleton pattern) so all instances
    share the same rate limit.
    """

    # Module-level singleton semaphore — shared across all instances
    _shared_semaphore: Optional[asyncio.Semaphore] = None
    _semaphore_lock: Optional[asyncio.Lock] = None

    def __init__(
        self,
        rate_limit: float = DEFAULT_RATE_LIMIT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    ):
        self.rate_limit = rate_limit
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.http_timeout = http_timeout
        self.cache = _ResponseCache(cache_ttl)

        # Per-instance session (lazy init)
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_owned = True

    # -- singleton semaphore -------------------------------------------------

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        """Return the module-level shared semaphore, creating it if needed."""
        if cls._shared_semaphore is None:
            # Create in current event loop
            cls._shared_semaphore = asyncio.Semaphore(1)
        return cls._shared_semaphore

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._get_semaphore()

    # -- session pooling -----------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the shared HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.http_timeout)
            )
            self._session_owned = True
        return self._session

    async def close(self) -> None:
        """Close the HTTP session if owned."""
        if self._session_owned and self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # -- retry logic ---------------------------------------------------------

    async def _retry_request(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, str] | None,
        headers: dict[str, str],
    ) -> Optional[aiohttp.ClientResponse]:
        """Execute a GET request with exponential backoff retries."""
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.http_timeout),
                ) as resp:
                    # Retry on 503 (rate limit) or server errors
                    if resp.status == 503 and attempt < self.max_retries - 1:
                        wait = self.backoff_base * (2 ** attempt)
                        logger.warning(
                            "MusicBrainz 503 (rate limit), retrying in %.1fs (attempt %d/%d)",
                            wait, attempt + 1, self.max_retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    if resp.status >= 500 and attempt < self.max_retries - 1:
                        wait = self.backoff_base * (2 ** attempt)
                        logger.warning(
                            "MusicBrainz %d, retrying in %.1fs (attempt %d/%d)",
                            resp.status, wait, attempt + 1, self.max_retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return resp
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        "MusicBrainz request failed (%s), retrying in %.1fs (attempt %d/%d)",
                        type(exc).__name__, wait, attempt + 1, self.max_retries,
                    )
                    await asyncio.sleep(wait)
                continue

        logger.error(
            "MusicBrainz request failed after %d attempts: %s",
            self.max_retries, last_exc,
        )
        return None

    # -- main fetch method ---------------------------------------------------

    async def fetch_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Optional[dict]:
        """Fetch JSON from *url* with rate limiting, caching, and retries.

        Args:
            url: Request URL.
            params: Query parameters.
            headers: HTTP headers (User-Agent, etc.).
            use_cache: Whether to check/update the response cache.

        Returns:
            Parsed JSON dict, or ``None`` on failure.
        """
        # Check cache first
        if use_cache:
            cached = self.cache.get(url, params)
            if cached is not None:
                logger.debug("Cache hit for %s", url)
                return cached

        # Acquire rate limit semaphore
        sem = self.semaphore
        async with sem:
            session = await self._get_session()
            resp = await self._retry_request(session, url, params, headers or {})

            if resp is None:
                return None

            if resp.status != 200:
                logger.warning(
                    "MusicBrainz returned %d for %s", resp.status, url
                )
                return None

            try:
                data = await resp.json()
            except (ValueError, aiohttp.ContentTypeError) as exc:
                logger.warning("Failed to parse JSON from %s: %s", url, exc)
                return None

            # Update cache
            if use_cache:
                self.cache.set(url, data, params)

            # Rate limit: wait ~1/rate_limit seconds between requests
            wait_time = 1.0 / self.rate_limit
            await asyncio.sleep(wait_time)

            return data

    # -- context manager for raw access --------------------------------------

    async def acquire(self) -> aiohttp.ClientSession:
        """Acquire the rate limit semaphore and return the session.

        Use as::

            limiter = MusicBrainzRateLimiter()
            session = await limiter.acquire()
            try:
                async with session.get(url) as resp:
                    ...
            finally:
                await limiter.release()
        """
        await self.semaphore.acquire()
        return await self._get_session()

    async def release(self) -> None:
        """Release the rate limit semaphore and enforce rate delay."""
        sem = self.semaphore
        sem.release()
        wait_time = 1.0 / self.rate_limit
        await asyncio.sleep(wait_time)
