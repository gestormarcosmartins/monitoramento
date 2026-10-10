import logging
import signal
import time

from .settings import get_settings

logger = logging.getLogger("central-monitor-worker")
running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker_started")

    while running:
        # The transactional outbox consumer is added with the first migration.
        time.sleep(5)

    logger.info("worker_stopped")


if __name__ == "__main__":
    main()

