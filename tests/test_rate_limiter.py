#!/usr/bin/env python3
"""Tests for rate_limiter.py — MusicBrainzRateLimiter and _ResponseCache."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp


class TestResponseCache(unittest.TestCase):
    """Tests for the _ResponseCache internal class."""

    def setUp(self):
        from otterrip_core.rate_limiter import _ResponseCache
        self.cache = _ResponseCache(ttl_seconds=1)

    def test_get_miss(self):
        result = self.cache.get("http://example.com")
        self.assertIsNone(result)

    def test_set_and_get(self):
        self.cache.set("http://example.com", {"key": "value"})
        result = self.cache.get("http://example.com")
        self.assertEqual(result, {"key": "value"})

    def test_get_with_params(self):
        self.cache.set(
            "http://example.com",
            {"data": 1},
            params={"a": "1", "b": "2"},
        )
        result = self.cache.get("http://example.com", params={"a": "1", "b": "2"})
        self.assertEqual(result, {"data": 1})

    def test_different_params_different_keys(self):
        self.cache.set("http://example.com", {"a": 1}, params={"q": "a"})
        self.cache.set("http://example.com", {"b": 2}, params={"q": "b"})
        self.assertEqual(
            self.cache.get("http://example.com", params={"q": "a"}), {"a": 1}
        )
        self.assertEqual(
            self.cache.get("http://example.com", params={"q": "b"}), {"b": 2}
        )

    def test_ttl_expiration(self):
        self.cache.set("http://example.com", {"data": 1})
        self.assertIsNotNone(self.cache.get("http://example.com"))
        # Wait for TTL to expire
        time.sleep(1.1)
        self.assertIsNone(self.cache.get("http://example.com"))

    def test_clear(self):
        self.cache.set("http://a.com", 1)
        self.cache.set("http://b.com", 2)
        self.cache.set("http://c.com", 3)
        count = self.cache.clear()
        self.assertEqual(count, 3)
        self.assertIsNone(self.cache.get("http://a.com"))

    def test_key_deterministic(self):
        from otterrip_core.rate_limiter import _ResponseCache
        c = _ResponseCache()
        k1 = c._key("http://example.com", {"a": "1"})
        k2 = c._key("http://example.com", {"a": "1"})
        self.assertEqual(k1, k2)

    def test_key_sorts_params(self):
        from otterrip_core.rate_limiter import _ResponseCache
        c = _ResponseCache()
        k1 = c._key("http://example.com", {"b": "2", "a": "1"})
        k2 = c._key("http://example.com", {"a": "1", "b": "2"})
        self.assertEqual(k1, k2)


class TestRateLimiterInit(unittest.TestCase):
    """Tests for MusicBrainzRateLimiter initialization."""

    def test_default_values(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter
        limiter = MusicBrainzRateLimiter()
        self.assertEqual(limiter.rate_limit, 1.0)
        self.assertEqual(limiter.max_retries, 3)
        self.assertEqual(limiter.backoff_base, 1.0)
        self.assertEqual(limiter.http_timeout, 10.0)

    def test_custom_values(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter
        limiter = MusicBrainzRateLimiter(
            rate_limit=2.0,
            max_retries=5,
            backoff_base=0.5,
            cache_ttl=600,
            http_timeout=30.0,
        )
        self.assertEqual(limiter.rate_limit, 2.0)
        self.assertEqual(limiter.max_retries, 5)
        self.assertEqual(limiter.backoff_base, 0.5)
        self.assertEqual(limiter.cache.ttl, 600)
        self.assertEqual(limiter.http_timeout, 30.0)

    def test_semaphore_is_shared(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter
        # Reset singleton for test
        MusicBrainzRateLimiter._shared_semaphore = None
        l1 = MusicBrainzRateLimiter()
        l2 = MusicBrainzRateLimiter()
        self.assertIs(l1.semaphore, l2.semaphore)

    def test_cache_instance(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter, _ResponseCache
        limiter = MusicBrainzRateLimiter()
        self.assertIsInstance(limiter.cache, _ResponseCache)


class TestRateLimiterAsync(unittest.TestCase):
    """Async tests for MusicBrainzRateLimiter."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def setUp(self):
        # Reset singleton semaphore before each test
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter
        MusicBrainzRateLimiter._shared_semaphore = None

    @patch("aiohttp.ClientSession")
    async def test_fetch_json_uses_cache(self, mock_session_cls):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        # Mock response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"test": "data"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False
        mock_session_cls.return_value = mock_session

        limiter = MusicBrainzRateLimiter()
        # First call should hit the mock
        result = await limiter.fetch_json(
            "http://example.com",
            params={"q": "test"},
            headers={"User-Agent": "Test"},
        )
        self.assertEqual(result, {"test": "data"})

        # Second call should come from cache (no additional network call)
        # Replace session to verify it's not called again
        limiter._session = None
        result2 = await limiter.fetch_json(
            "http://example.com",
            params={"q": "test"},
            headers={"User-Agent": "Test"},
        )
        self.assertEqual(result2, {"test": "data"})

    @patch("aiohttp.ClientSession")
    async def test_fetch_json_cache_disabled(self, mock_session_cls):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        call_count = 0

        async def make_resp():
            nonlocal call_count
            call_count += 1
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"call": call_count})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = AsyncMock()
        mock_session.closed = False

        async def get_side_effect(*args, **kwargs):
            return await make_resp()

        mock_session.get = MagicMock(side_effect=get_side_effect)
        mock_session_cls.return_value = mock_session

        limiter = MusicBrainzRateLimiter()
        r1 = await limiter.fetch_json("http://example.com", use_cache=False)
        r2 = await limiter.fetch_json("http://example.com", use_cache=False)
        self.assertEqual(r1, {"call": 1})
        self.assertEqual(r2, {"call": 2})

    async def test_semaphore_serializes_requests(self):
        """Verify that the semaphore limits concurrent access."""
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        limiter = MusicBrainzRateLimiter()
        sem = limiter.semaphore

        # Semaphore should allow only 1 at a time
        acquired = False

        async def worker():
            nonlocal acquired
            await sem.acquire()
            try:
                acquired = True
                await asyncio.sleep(0.05)
                return True
            finally:
                sem.release()

        # Start two workers
        t1 = asyncio.create_task(worker())
        t2 = asyncio.create_task(worker())
        await asyncio.sleep(0.01)  # Let first one start
        # Both should complete
        r1, r2 = await asyncio.gather(t1, t2)
        self.assertTrue(r1)
        self.assertTrue(r2)

    async def test_close_session(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        limiter = MusicBrainzRateLimiter()
        session = await limiter._get_session()
        self.assertFalse(session.closed)
        await limiter.close()
        # Session should be closed
        self.assertTrue(session.closed)

    async def test_context_manager(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        async with MusicBrainzRateLimiter() as limiter:
            session = await limiter._get_session()
            self.assertFalse(session.closed)
        # After context exit, session should be closed
        self.assertTrue(session.closed)

    async def test_acquire_release(self):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        limiter = MusicBrainzRateLimiter()
        session = await limiter.acquire()
        self.assertIsNotNone(session)
        # Release with short sleep override
        limiter.rate_limit = 100.0  # 0.01s delay
        await limiter.release()

    @patch("aiohttp.ClientSession")
    async def test_retry_on_503(self, mock_session_cls):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        attempts = 0

        async def make_resp():
            nonlocal attempts
            attempts += 1
            mock_resp = AsyncMock()
            if attempts < 2:
                mock_resp.status = 503
            else:
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={"ok": True})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = AsyncMock()
        mock_session.closed = False

        async def get_side_effect(*args, **kwargs):
            return await make_resp()

        mock_session.get = MagicMock(side_effect=get_side_effect)
        mock_session_cls.return_value = mock_session

        limiter = MusicBrainzRateLimiter(backoff_base=0.01)
        result = await limiter.fetch_json("http://example.com")
        self.assertEqual(result, {"ok": True})
        self.assertGreaterEqual(attempts, 2)

    @patch("aiohttp.ClientSession")
    async def test_non_200_returns_none(self, mock_session_cls):
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False
        mock_session_cls.return_value = mock_session

        limiter = MusicBrainzRateLimiter()
        result = await limiter.fetch_json("http://example.com")
        self.assertIsNone(result)


class TestRateLimiterConstants(unittest.TestCase):
    """Test that default constants are reasonable."""

    def test_default_rate_limit(self):
        from otterrip_core.rate_limiter import DEFAULT_RATE_LIMIT
        self.assertEqual(DEFAULT_RATE_LIMIT, 1.0)

    def test_default_max_retries(self):
        from otterrip_core.rate_limiter import DEFAULT_MAX_RETRIES
        self.assertEqual(DEFAULT_MAX_RETRIES, 3)

    def test_default_cache_ttl(self):
        from otterrip_core.rate_limiter import DEFAULT_CACHE_TTL
        self.assertEqual(DEFAULT_CACHE_TTL, 300)


if __name__ == "__main__":
    unittest.main()
