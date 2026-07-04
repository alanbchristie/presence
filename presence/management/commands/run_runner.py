"""Run the presence state-machine loop as a foreground process.

This is the entry point for the dedicated runner container (issue #47):
`entrypoint.sh` execs it when PRESENCE_SERVER=runner. It reuses
:func:`presence.runner.run` — the same loop the in-process dev thread
uses — and adds only lifecycle handling: SIGTERM/SIGINT set a stop event
so `docker stop` shuts the loop down cleanly instead of killing it
mid-tick.
"""
import logging
import signal
import threading

from django.core.management.base import BaseCommand

from presence import runner

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the presence state-machine loop in the foreground until "
        "SIGTERM/SIGINT (the dedicated runner container's entry point)."
    )

    def handle(self, *args, **options):
        stop_event = threading.Event()
        received_signals: list[int] = []

        # The handler only records and sets the event: anything more (I/O,
        # logging) is unsafe in a signal handler. Reporting happens after
        # run() returns.
        def handle_stop_signal(signum, frame):
            received_signals.append(signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, handle_stop_signal)
        signal.signal(signal.SIGINT, handle_stop_signal)

        # Lifecycle goes to stdout as well as the logger: with DEBUG off
        # Django's default logging drops INFO from non-django loggers, and
        # `docker compose logs runner` must still show a clean start/stop.
        self.stdout.write("presence runner loop starting")
        logger.info("presence runner loop starting")

        runner.run(stop_event)

        names = ", ".join(
            signal.Signals(signum).name for signum in received_signals
        )
        self.stdout.write(f"presence runner loop stopped ({names})")
        logger.info("presence runner loop stopped (%s)", names)
