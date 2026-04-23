"""Logging configuration for Softarr.

Provides a single ``configure_logging()`` entry point used by both the
FastAPI application (`softarr.main`) and the standalone CLI
(`softarr.cli.init`) so both surfaces produce identical output.
"""

import logging

from softarr.core.config import settings


def configure_logging() -> logging.Logger:
    """Configure root logging according to ``LOG_FORMAT`` and ``DEBUG``.

    When ``LOG_FORMAT=json``, use python-json-logger for structured output
    suitable for Loki, Datadog, and similar log aggregators. When
    ``LOG_FORMAT=text`` (the default), use a human-readable format.

    Returns the project logger (``softarr``).
    """
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    if settings.LOG_FORMAT == "json":
        try:
            from pythonjsonlogger.json import JsonFormatter

            handler = logging.StreamHandler()
            handler.setFormatter(
                JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            logging.root.handlers = []
            logging.root.addHandler(handler)
            logging.root.setLevel(level)
        except ImportError:
            logging.basicConfig(
                level=level,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    return logging.getLogger("softarr")


# Historical alias -- older imports use ``setup_logging``. Kept so existing
# ``from softarr.core.logging import logger`` sites keep working.
def setup_logging() -> logging.Logger:
    return configure_logging()


logger = setup_logging()
