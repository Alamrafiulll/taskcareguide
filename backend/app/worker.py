import logging
import signal
from threading import Event

from app.config import get_settings
from app.logging_config import configure_logging
from app.services.worker import DocumentWorker


def main() -> None:
    configure_logging(get_settings().log_level)
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    DocumentWorker().run_forever(stop)


if __name__ == "__main__":
    main()
