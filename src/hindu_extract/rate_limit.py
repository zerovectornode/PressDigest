"""Token-aware concurrency limiter for Gemini calls.

The binding real-world constraint on this pipeline is TPM (tokens/minute:
250K), not raw concurrency - a fixed "N workers" limit either wastes
headroom (small pages) or blows the TPM ceiling (large pages), since it
knows nothing about how many tokens each call actually costs. This limiter
enforces three independent ceilings before letting a call proceed:

1. max_concurrent - a coarse safety net on simultaneous in-flight calls.
2. requests_per_minute - a sliding 60s window on call starts (the 15 RPM
   cap).
3. tokens_per_minute - a sliding 60s window on token cost (the 250K TPM
   cap), reserved at an estimate when the call starts (the real cost isn't
   known until the response returns) and corrected to the actual usage via
   Reservation.finalize() once it is - so the window reflects reality
   quickly rather than the (necessarily approximate) pre-call estimate
   compounding over many calls.

Blocks (does not raise) until all three have headroom, polling every
0.5s - callers run this from a worker thread (e.g. a ThreadPoolExecutor),
never the main thread, so blocking here is the intended behaviour.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class TokenAwareLimiter:
    def __init__(
        self,
        max_concurrent: int,
        requests_per_minute: int,
        tokens_per_minute: int,
        estimated_tokens_per_request: int,
        window_seconds: float = 60.0,
        poll_seconds: float = 0.5,
    ) -> None:
        self._sem = threading.Semaphore(max_concurrent)
        self._rpm = requests_per_minute
        self._tpm = tokens_per_minute
        self._estimated = estimated_tokens_per_request
        self._window = window_seconds
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self._request_starts: deque[float] = deque()
        self._token_events: deque[list] = deque()  # each: [timestamp, token_count]

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._request_starts and self._request_starts[0] < cutoff:
            self._request_starts.popleft()
        while self._token_events and self._token_events[0][0] < cutoff:
            self._token_events.popleft()

    def _tokens_in_window(self) -> int:
        return sum(t for _, t in self._token_events)

    def reserve(self, estimated_tokens: int | None = None) -> "_Reservation":
        estimated = self._estimated if estimated_tokens is None else estimated_tokens
        self._sem.acquire()
        while True:
            with self._lock:
                now = time.time()
                self._prune(now)
                has_rpm_headroom = len(self._request_starts) < self._rpm
                has_tpm_headroom = self._tokens_in_window() + estimated <= self._tpm
                if has_rpm_headroom and has_tpm_headroom:
                    self._request_starts.append(now)
                    event = [now, estimated]
                    self._token_events.append(event)
                    return _Reservation(self, event)
            time.sleep(self._poll)

    def _release(self) -> None:
        self._sem.release()


class _Reservation:
    """Context manager returned by TokenAwareLimiter.reserve(). Releases the
    concurrency slot on exit regardless of success/failure; call
    finalize(actual_tokens) inside the `with` block once the real usage is
    known so the TPM window reflects it instead of the pre-call estimate."""

    def __init__(self, limiter: TokenAwareLimiter, event: list) -> None:
        self._limiter = limiter
        self._event = event

    def finalize(self, actual_tokens: int) -> None:
        with self._limiter._lock:
            self._event[1] = actual_tokens

    def __enter__(self) -> "_Reservation":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._limiter._release()
