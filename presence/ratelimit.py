"""Lightweight in-process rate limiting for auth endpoints.

Counters live in Django's default local-memory cache. That is coherent here
only because the deployment runs exactly one worker process (the single-runner
invariant — see CLAUDE.md and entrypoint.sh). Scaling out to multiple workers
would require a shared cache backend (e.g. Redis) before these limits remain
meaningful; until then per-process counting is correct.

The counter uses a fixed window: the first failure seeds the count with a TTL,
later failures increment it without extending the window, and the key expires
on its own. ``cache.add`` followed by ``cache.incr`` is not a single atomic
operation, so under concurrent requests the running total may be off by one;
that is acceptable for a throttle (it never under-counts to zero) and avoids
introducing a lock on the request path.
"""
from django.core.cache import cache


def client_ip(request) -> str:
    """Best-effort client IP for keying rate limits.

    Caddy is the sole front end and sets ``X-Forwarded-For``; the originating
    client is its first entry. Fall back to ``REMOTE_ADDR`` for direct
    (no-proxy) access, and to a constant when neither is present so the limiter
    still groups otherwise-unidentified callers together.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _cache_key(scope: str, subject: str) -> str:
    return f"ratelimit:{scope}:{subject}"


def is_blocked(scope: str, subject: str, *, limit: int) -> bool:
    """True once recorded failures for (scope, subject) have reached ``limit``."""
    return cache.get(_cache_key(scope, subject), 0) >= limit


def record_failure(scope: str, subject: str, *, window_seconds: int) -> int:
    """Count one failure in the current window; return the running total."""
    key = _cache_key(scope, subject)
    if cache.add(key, 1, timeout=window_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        # The key expired between the add and the incr; reseed the window.
        cache.set(key, 1, timeout=window_seconds)
        return 1


def clear(scope: str, subject: str) -> None:
    """Drop the failure counter, e.g. after a successful authentication."""
    cache.delete(_cache_key(scope, subject))
