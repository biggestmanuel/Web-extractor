"""Rate limiter and cache tests."""

import time

import pytest

from ratelimit import TokenBucketLimiter, TTLCache


def test_allows_burst_up_to_capacity():
    limiter = TokenBucketLimiter(capacity=3, window=60)
    for _ in range(3):
        allowed, _, _ = limiter.check("1.2.3.4")
        assert allowed


def test_blocks_after_capacity_exhausted():
    limiter = TokenBucketLimiter(capacity=2, window=60)
    assert limiter.check("ip")[0]
    assert limiter.check("ip")[0]
    allowed, remaining, retry_after = limiter.check("ip")
    assert not allowed
    assert remaining == 0
    assert retry_after > 0


def test_keys_are_independent():
    limiter = TokenBucketLimiter(capacity=1, window=60)
    assert limiter.check("a")[0]
    assert not limiter.check("a")[0]
    assert limiter.check("b")[0]


def test_tokens_refill_over_time():
    limiter = TokenBucketLimiter(capacity=1, window=0.2)
    assert limiter.check("ip")[0]
    assert not limiter.check("ip")[0]
    time.sleep(0.25)
    assert limiter.check("ip")[0]


def test_retry_after_shrinks_as_bucket_refills():
    limiter = TokenBucketLimiter(capacity=1, window=1.0)
    limiter.check("ip")
    _, _, first = limiter.check("ip")
    time.sleep(0.4)
    _, _, second = limiter.check("ip")
    assert second < first


def test_stale_keys_are_evicted():
    limiter = TokenBucketLimiter(capacity=5, window=0.05)
    limiter.check("old")
    time.sleep(0.2)
    limiter.check("new")
    assert "old" not in limiter._tokens


def test_cache_round_trip_and_ttl():
    cache = TTLCache(max_entries=10, ttl=0.2)
    cache.set("url", {"a": 1})
    assert cache.get("url") == {"a": 1}
    time.sleep(0.3)
    assert cache.get("url") is None


def test_cache_evicts_least_recently_used():
    cache = TTLCache(max_entries=2, ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")          # refresh 'a'
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1


def test_cache_miss_returns_none():
    assert TTLCache().get("missing") is None


def test_concurrent_access_is_safe():
    import threading

    limiter = TokenBucketLimiter(capacity=50, window=60)
    allowed = []

    def worker():
        for _ in range(10):
            allowed.append(limiter.check("shared")[0])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(allowed) == 50


@pytest.mark.parametrize("capacity", [1, 5, 25])
def test_never_allows_more_than_capacity(capacity):
    limiter = TokenBucketLimiter(capacity=capacity, window=600)
    assert sum(limiter.check("ip")[0] for _ in range(capacity * 3)) == capacity
