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

        def handle_stop_signal(signum, frame):
            logger.info(
                "presence runner received signal %s; stopping",
                signal.Signals(signum).name,
            )
            stop_event.set()

        signal.signal(signal.SIGTERM, handle_stop_signal)
        signal.signal(signal.SIGINT, handle_stop_signal)

        logger.info("presence runner loop starting")
        runner.run(stop_event)
        logger.info("presence runner loop stopped")
