"""Offline tests for the token-aware concurrency limiter (rate_limit.py).
No network access - these only exercise the sliding-window/semaphore logic
itself, driven by real wall-clock time via threads."""
from __future__ import annotations

import threading
import time

from hindu_extract.rate_limit import TokenAwareLimiter


def test_max_concurrent_caps_simultaneous_reservations():
    limiter = TokenAwareLimiter(
        max_concurrent=2,
        requests_per_minute=1000,
        tokens_per_minute=1_000_000,
        estimated_tokens_per_request=10,
    )
    in_flight = []
    max_seen = []
    lock = threading.Lock()

    def worker():
        with limiter.reserve():
            with lock:
                in_flight.append(1)
                max_seen.append(len(in_flight))
            time.sleep(0.2)
            with lock:
                in_flight.pop()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max(max_seen) <= 2


def test_requests_per_minute_blocks_until_window_has_headroom():
    limiter = TokenAwareLimiter(
        max_concurrent=10,
        requests_per_minute=2,
        tokens_per_minute=1_000_000,
        estimated_tokens_per_request=10,
        window_seconds=0.5,
        poll_seconds=0.05,
    )
    start = time.monotonic()
    for _ in range(4):
        with limiter.reserve():
            pass
    elapsed = time.monotonic() - start
    # 4 requests at rpm=2 within a 0.5s window must span at least one full
    # window past the first 2 - i.e. it cannot finish near-instantly.
    assert elapsed >= 0.4


def test_tokens_per_minute_blocks_until_estimated_cost_fits():
    limiter = TokenAwareLimiter(
        max_concurrent=10,
        requests_per_minute=1000,
        tokens_per_minute=100,
        estimated_tokens_per_request=60,
        window_seconds=0.5,
        poll_seconds=0.05,
    )
    start = time.monotonic()
    # First reservation (60) fits under 100; second (60 more = 120) does not
    # until the window clears.
    with limiter.reserve():
        pass
    with limiter.reserve():
        pass
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4


def test_finalize_corrects_token_window_to_actual_usage():
    limiter = TokenAwareLimiter(
        max_concurrent=10,
        requests_per_minute=1000,
        tokens_per_minute=100,
        estimated_tokens_per_request=90,
    )
    with limiter.reserve() as res:
        res.finalize(5)  # actual usage far lower than the estimate
    # A second call whose estimate (90) would have exceeded 100 combined
    # with the original 90-token estimate must now fit, since the first
    # reservation was corrected down to 5.
    acquired = []

    def worker():
        with limiter.reserve():
            acquired.append(1)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=1.0)
    assert acquired == [1]


def test_reservation_releases_concurrency_slot_even_if_body_raises():
    limiter = TokenAwareLimiter(
        max_concurrent=1,
        requests_per_minute=1000,
        tokens_per_minute=1_000_000,
        estimated_tokens_per_request=10,
    )
    try:
        with limiter.reserve():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    acquired = []

    def worker():
        with limiter.reserve():
            acquired.append(1)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=1.0)
    assert acquired == [1]
