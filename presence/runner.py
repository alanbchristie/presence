"""Background driver that flips Presence rows between on/off.

A single daemon thread is started once per process from PresenceConfig.ready().
This is fine for `runserver` / a single-worker dev setup; under multi-worker
gunicorn each worker would spawn its own thread and race on the same rows.
"""
from __future__ import annotations

import logging
import os
import random
import sys
import threading
import time as time_module
from datetime import timedelta

from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

_MAX_SLEEP_SECONDS = 30.0
_MIN_SLEEP_SECONDS = 0.5

_started = False
_start_lock = threading.Lock()


def start() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        if not _should_start():
            return
        _started = True
        thread = threading.Thread(target=_loop, name="presence-runner", daemon=True)
        thread.start()
        logger.info("presence runner thread started")


def _should_start() -> bool:
    argv = sys.argv
    if len(argv) < 2:
        return False
    command = argv[1]
    long_running = {"runserver", "runserver_plus"}
    if command not in long_running:
        return False
    # Under runserver, the autoreloader's parent process also runs ready();
    # we only want the child (RUN_MAIN=true) or a --noreload invocation
    # to actually spawn the thread.
    if command == "runserver" and "--noreload" not in argv:
        if os.environ.get("RUN_MAIN") != "true":
            return False
    return True


def _loop() -> None:
    from .models import Presence

    while True:
        try:
            now = timezone.now()
            rows = list(Presence.objects.filter(enabled=True))
            for row in rows:
                _evaluate(row, now)
            sleep_for = _compute_sleep(rows, now)
        except Exception:
            logger.exception("presence runner loop iteration failed")
            sleep_for = _MAX_SLEEP_SECONDS
        finally:
            connection.close()
        time_module.sleep(sleep_for)


def _evaluate(row, now) -> None:
    State = row.State
    in_window = row.is_in_window(now)
    changed: list[str] = []

    if not in_window:
        if row.current_state == State.ON:
            row.current_state = State.OFF
            row.state_since = now
            changed.extend(["current_state", "state_since"])
        # The first ON transition of each window should land a random off-period
        # AFTER the window opens, not at the open boundary itself. Compute the
        # delayed target once (don't re-randomize on every tick).
        window_open = row.next_window_open(now)
        needs_target = (
            row.next_transition_at is None
            or row.next_transition_at <= now
            or row.next_transition_at <= window_open
        )
        if needs_target:
            delay = _pick_duration(row.min_off_duration, row.max_off_duration)
            target = min(window_open + delay, row.window_close_after(window_open))
            if row.next_transition_at != target:
                row.next_transition_at = target
                changed.append("next_transition_at")
    else:
        window_close = row.window_close_after(now)
        if row.current_state == State.OFF:
            if row.next_transition_at is None:
                # Mid-window with no scheduled flip (e.g. just enabled): start
                # with an off-delay rather than flipping ON immediately.
                delay = _pick_duration(row.min_off_duration, row.max_off_duration)
                row.next_transition_at = min(now + delay, window_close)
                changed.append("next_transition_at")
            elif now >= row.next_transition_at:
                duration = _pick_duration(row.min_on_duration, row.max_on_duration)
                row.current_state = State.ON
                row.state_since = now
                row.next_transition_at = min(now + duration, window_close)
                changed.extend(["current_state", "state_since", "next_transition_at"])
        else:  # ON
            if row.next_transition_at is None or now >= row.next_transition_at:
                duration = _pick_duration(row.min_off_duration, row.max_off_duration)
                row.current_state = State.OFF
                row.state_since = now
                row.next_transition_at = min(now + duration, window_close)
                changed.extend(["current_state", "state_since", "next_transition_at"])

    if changed:
        row.save(update_fields=list(set(changed)) + ["updated_at"])


def _pick_duration(low: timedelta, high: timedelta) -> timedelta:
    seconds = random.uniform(low.total_seconds(), high.total_seconds())
    return timedelta(seconds=seconds)


def _compute_sleep(rows, now) -> float:
    next_times = [r.next_transition_at for r in rows if r.next_transition_at is not None]
    if not next_times:
        return _MAX_SLEEP_SECONDS
    delta = (min(next_times) - now).total_seconds()
    return max(_MIN_SLEEP_SECONDS, min(_MAX_SLEEP_SECONDS, delta))
