"""Tests for the runner loop's process-mode entry points (issue #47).

The state-machine itself (`_evaluate` / `_compute_sleep`) is exercised
indirectly by the model/window tests; these tests cover the plumbing that
lets the loop run either as the in-process thread (web container / local
dev) or as a foreground process in a dedicated runner container:

- ``runner.run(stop_event)`` — the loop body, with a cancellable sleep so
  a signal handler can stop it promptly (no more infinite-only loop).
- ``runner.start()`` — gated by ``PRESENCE_RUN_RUNNER`` so the web
  container can opt out of spawning the thread.
- the ``run_runner`` management command — installs SIGTERM/SIGINT
  handlers that set the stop event and delegates to ``runner.run``.
"""
import signal
import threading

import pytest
from django.core.management import call_command

from presence import runner


class _OneShotEvent(threading.Event):
    """An Event whose first wait() sets it, so run() does exactly one tick.

    ``runner.run`` sleeps via ``stop_event.wait(...)`` between ticks; this
    subclass turns that first sleep into an immediate "stop requested",
    making a single loop iteration deterministic and thread-free.
    """

    def wait(self, timeout=None):  # noqa: ARG002 - signature must match
        self.set()
        return True


@pytest.mark.django_db(transaction=True)
def test_run_evaluates_enabled_rows_once(make_presence):
    # transaction=True: run() closes the DB connection after each tick,
    # which is incompatible with the atomic block the plain db fixture
    # wraps tests in.
    presence = make_presence()
    presence.save()
    assert presence.next_transition_at is None

    runner.run(_OneShotEvent())

    presence.refresh_from_db()
    assert presence.next_transition_at is not None


@pytest.mark.django_db(transaction=True)
def test_run_returns_promptly_when_stop_event_set():
    stop_event = threading.Event()
    thread = threading.Thread(
        target=runner.run, args=(stop_event,), daemon=True
    )
    thread.start()

    stop_event.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), (
        "run() must exit promptly once the stop event is set, not sleep "
        "out the full tick interval or loop forever"
    )


class _RecordingThread:
    """Stands in for threading.Thread to observe start() without a loop."""

    instances: list["_RecordingThread"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        _RecordingThread.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def _startable_runner(monkeypatch):
    """Make start()'s preconditions pass and capture any spawned thread."""
    monkeypatch.setattr(runner, "_started", False)
    monkeypatch.setattr(runner, "_should_start", lambda: True)
    _RecordingThread.instances = []
    monkeypatch.setattr(runner.threading, "Thread", _RecordingThread)
    return _RecordingThread


def test_start_spawns_thread_by_default(_startable_runner, monkeypatch):
    monkeypatch.delenv("PRESENCE_RUN_RUNNER", raising=False)

    runner.start()

    assert len(_startable_runner.instances) == 1
    assert _startable_runner.instances[0].started


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
def test_start_skips_thread_when_gated_off(_startable_runner, monkeypatch, raw):
    monkeypatch.setenv("PRESENCE_RUN_RUNNER", raw)

    runner.start()

    assert _startable_runner.instances == []
    # The gate must not latch _started: a later, correctly-gated start()
    # in the same process must still be possible.
    assert runner._started is False


def test_start_spawns_thread_when_gated_on_explicitly(
    _startable_runner, monkeypatch
):
    monkeypatch.setenv("PRESENCE_RUN_RUNNER", "true")

    runner.start()

    assert len(_startable_runner.instances) == 1


def test_run_runner_command_stops_via_signal_handler(monkeypatch):
    """The command wires SIGTERM/SIGINT to the stop event and runs the loop.

    The handlers are captured (not installed) and invoked directly: real
    signal delivery in a test is flaky and signal.signal() only works in
    the main thread anyway.
    """
    from presence.management.commands import run_runner as command_module

    registered: dict[int, object] = {}
    monkeypatch.setattr(
        command_module.signal,
        "signal",
        lambda signum, handler: registered.update({signum: handler}),
    )

    seen: dict[str, threading.Event] = {}
    monkeypatch.setattr(
        command_module.runner, "run", lambda stop_event: seen.update(
            {"stop_event": stop_event}
        )
    )

    call_command("run_runner")

    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered

    stop_event = seen["stop_event"]
    assert isinstance(stop_event, threading.Event)
    assert not stop_event.is_set()

    registered[signal.SIGTERM](signal.SIGTERM, None)
    assert stop_event.is_set()
