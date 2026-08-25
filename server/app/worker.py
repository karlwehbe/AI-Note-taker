"""Background worker stub. Job polling lands in a later phase."""

from __future__ import annotations

import logging
import signal
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
logger = logging.getLogger(__name__)

_running = True


def _handle_signal(signum: int, _frame: object) -> None:
    global _running
    logger.info("Received signal %s, shutting down", signum)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    logger.info("Worker started (stub — no job processing yet)")
    while _running:
        logger.info("heartbeat")
        time.sleep(30)
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()
