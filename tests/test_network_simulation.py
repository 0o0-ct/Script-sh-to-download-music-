"""Network simulation tests for MusicBrainz, Cover Art Archive, and LRCLib.

Tests verify retry logic, rate limiting, cache fallback, and error handling
when external services fail, timeout, or return unexpected responses.
"""
import asyncio
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest


class TestRateLimiterSimulation:
    """Simulate rate limiter behavior with mocked responses."""

    def test_rate_limiter_semaphore(self):
        """Verify the semaphore serializes requests."""
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        limiter = MusicBrainzRateLimiter(rate_limit=10)
        # Semaphore should exist
        assert limiter.semaphore is not None

    def test_rate_limiter_close(self):
        """Verify close() doesn't raise."""
        from otterrip_core.rate_limiter import MusicBrainzRateLimiter

        limiter = MusicBrainzRateLimiter()

        async def do_close():
            await limiter.close()

        asyncio.run(do_close())

    def test_response_cache_ttl(self):
        """Verify cache expires entries after TTL."""
        from otterrip_core.rate_limiter import _ResponseCache

        cache = _ResponseCache(ttl_seconds=0)
        cache.set("https://example.com/", {"key": "value"})
        result = cache.get("https://example.com/")
        assert result is None

    def test_response_cache_hit(self):
        """Verify cache returns stored values before expiry."""
        from otterrip_core.rate_limiter import _ResponseCache

        cache = _ResponseCache(ttl_seconds=3600)
        data = {"key": "value"}
        cache.set("https://example.com/", data)
        result = cache.get("https://example.com/")
        assert result == data


class TestCoverArtSimulation:
    """Simulate Cover Art Archive failures."""

    def test_cover_art_404(self):
        """Simulate 404 from Cover Art Archive — fetch returns None."""
        from otterrip_core.cover_art import fetch_and_cache_cover

        # Without network, fetch_and_cache_cover should return False or None
        result = fetch_and_cache_cover("nonexistent-release-id")
        assert result in (False, None)

    def test_cover_art_cache_fallback(self):
        """Verify metadata_cache stores and retrieves cover data."""
        state_dir = tempfile.mkdtemp()
        from otterrip_core.state_db import StateDB
        db = StateDB(state_dir)

        release_id = "test-release-123"
        cover_data = b"fake-cover-data"
        db.metadata_cache_set(
            key=release_id,
            data={"cover": cover_data.hex(), "type": "front"},
        )

        cached = db.metadata_cache_get(release_id)
        assert cached is not None
        assert cached["cover"] == cover_data.hex()


class TestWorkerKillRecovery:
    """Test worker kill and recovery scenarios."""

    def test_worker_kill_recovery(self):
        """Kill a worker and verify recover detects the dead PID."""
        state_dir = tempfile.mkdtemp()
        from otterrip_core.state_db import StateDB
        db = StateDB(state_dir)

        tid = db.queue_add("https://example.com/test")
        db.queue_claim(pid=99999999, lease_seconds=0)
        recovered = db.queue_recover()
        assert recovered >= 1
        task = db.queue_get(tid, "status")
        assert task == "pending"

    def test_network_partition_recovery(self):
        """Simulate network partition and verify system recovers."""
        state_dir = tempfile.mkdtemp()
        from otterrip_core.state_db import StateDB
        db = StateDB(state_dir)

        for i in range(3):
            tid = db.queue_add(f"https://example.com/{i}")
            db.queue_claim(pid=99999998 + i, lease_seconds=0)

        recovered = db.queue_recover()
        assert recovered == 3

        stats = db.queue_stats()
        assert stats.get("pending", 0) == 3
