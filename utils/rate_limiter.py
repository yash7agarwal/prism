"""Per-provider rate-limit throttle for LLM clients.

Why this exists:
The autonomous agent fires bursts of LLM calls (multiple work items in a
session, sometimes multiple sessions concurrently). Each provider's free
tier has tight RPM caps — Gemini 15 RPM, Groq 30 RPM — so a burst of 10
calls in 4s reliably lands 6+ in the same minute window and gets 429'd.
v0.15.4 added a Claude → Gemini → Groq cascade, but live UAT showed
Groq itself only succeeding on 26% of calls (5 OK / 19 total) under
burst load. The cascade can't save calls when all providers are throttled
at once.

This module gives each provider:
  1. a Semaphore that caps concurrent in-flight calls (so two agent
     threads can't both stampede the same provider in one tick), and
  2. a min-interval gate so calls are spaced at least N seconds apart
     (process-local; honest under-cap RPM).

Both gates are module-globals and acquire-block, so any thread calling
through `throttle("gemini")` will queue politely behind the others.
This trades latency (calls now take 4s+ to start instead of 0s) for
deterministic 0% 429 rate under expected load.

The numbers are conservative: actual provider caps are in parens.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


# (max_concurrent, min_seconds_between_calls)
# min_seconds is just under the inverse of the documented free-tier RPM:
#   gemini 15 RPM → 60/15 = 4s exactly. We use 4.5s to leave headroom.
#   groq   30 RPM → 60/30 = 2s exactly. We use 2.5s.
_LIMITS: dict[str, tuple[int, float]] = {
    "gemini": (2, 4.5),
    "groq":   (2, 2.5),
}

# Module-global state, one entry per provider.
_locks: dict[str, threading.Lock] = {}
_semaphores: dict[str, threading.Semaphore] = {}
_last_call_at: dict[str, float] = {}


def _state(provider: str):
    """Lazily initialize per-provider state. Thread-safe via the GIL on dict insert."""
    if provider not in _locks:
        max_concurrent, _ = _LIMITS.get(provider, (4, 0.0))
        _locks[provider] = threading.Lock()
        _semaphores[provider] = threading.Semaphore(max_concurrent)
        _last_call_at[provider] = 0.0
    return _locks[provider], _semaphores[provider]


@contextmanager
def throttle(provider: str):
    """Wrap an LLM HTTP call so it respects the per-provider rate limit.

    Usage:
        with throttle("gemini"):
            httpx.post(...)

    Blocks until both (a) the concurrency semaphore is acquired and
    (b) enough time has passed since the last call from any thread.
    """
    if provider not in _LIMITS:
        # Unknown provider → no-op, do not surprise callers.
        yield
        return

    lock, sem = _state(provider)
    _, min_interval = _LIMITS[provider]

    sem.acquire()
    try:
        # Sleep to honour min-interval. Hold the lock only across the
        # timestamp check + write so two callers can't both think
        # they're "first."
        with lock:
            now = time.monotonic()
            wait = (_last_call_at[provider] + min_interval) - now
            if wait > 0:
                # Don't log every micro-wait — it'd flood the logs. Only log
                # waits over 1s, which means we're meaningfully throttling.
                if wait > 1.0:
                    logger.info(f"[throttle:{provider}] waiting {wait:.1f}s for rate limit")
                time.sleep(wait)
            _last_call_at[provider] = time.monotonic()
        yield
    finally:
        sem.release()
