"""Happy-path tests for utils/rate_limiter.throttle.

The throttle is critical infrastructure: every LLM call in the autonomous
agent loop now goes through it. If the spacing logic regresses to "0
seconds between calls", we'd reintroduce the burst-429 problem that
v0.15.5 was specifically built to fix. These tests pin the contract.

Run:
    pytest tests/test_rate_limiter.py -v
"""
from __future__ import annotations

import threading
import time

import pytest

from utils import rate_limiter


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Each test gets fresh module state — otherwise the global timestamp
    dict carries waits between tests and the assertions get noisy."""
    monkeypatch.setattr(rate_limiter, "_locks", {})
    monkeypatch.setattr(rate_limiter, "_semaphores", {})
    monkeypatch.setattr(rate_limiter, "_last_call_at", {})
    yield


def test_first_call_does_not_wait():
    """The first call through a provider's throttle must not block."""
    t0 = time.monotonic()
    with rate_limiter.throttle("gemini"):
        pass
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"first call should be immediate, got {elapsed:.2f}s"


def test_subsequent_calls_respect_min_interval():
    """Calls 2 and 3 must be spaced at least min_interval apart."""
    _, min_interval = rate_limiter._LIMITS["gemini"]
    timestamps: list[float] = []
    t0 = time.monotonic()
    for _ in range(3):
        with rate_limiter.throttle("gemini"):
            timestamps.append(time.monotonic() - t0)
    assert timestamps[0] < 0.2
    # Allow a tiny scheduling jitter (50ms) below the configured interval.
    assert timestamps[1] - timestamps[0] >= min_interval - 0.05, (
        f"call gap was {timestamps[1] - timestamps[0]:.2f}s, want ≥{min_interval}s"
    )
    assert timestamps[2] - timestamps[1] >= min_interval - 0.05


def test_unknown_provider_is_noop():
    """Unknown provider names must pass through without blocking — no surprises
    if a future caller passes a string the module doesn't recognise."""
    t0 = time.monotonic()
    for _ in range(5):
        with rate_limiter.throttle("nonexistent"):
            pass
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"unknown provider should be no-op, took {elapsed:.2f}s"


def test_groq_uses_its_own_interval_independently_of_gemini():
    """The two providers' clocks must not share state — a Gemini call
    should not delay a Groq call or vice versa."""
    with rate_limiter.throttle("gemini"):
        pass
    t0 = time.monotonic()
    with rate_limiter.throttle("groq"):
        pass
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, (
        f"Groq call after a Gemini call should be immediate, got {elapsed:.2f}s "
        "— provider state is leaking"
    )


def test_concurrent_callers_serialize_through_min_interval():
    """Two threads firing simultaneously must each see the spacing — the
    Semaphore lets both into the gate but the lock+sleep enforces order.
    This is the test that pins the burst-429 fix."""
    _, min_interval = rate_limiter._LIMITS["groq"]
    completion_times: list[float] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def fire():
        with rate_limiter.throttle("groq"):
            with lock:
                completion_times.append(time.monotonic() - t0)

    threads = [threading.Thread(target=fire) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    completion_times.sort()
    # First completes quickly; subsequent ones spaced by min_interval.
    assert completion_times[0] < 0.2
    assert completion_times[1] - completion_times[0] >= min_interval - 0.1
    assert completion_times[2] - completion_times[1] >= min_interval - 0.1
