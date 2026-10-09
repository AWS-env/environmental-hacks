# Synthetic OBS-01 fixture: Django-style production settings. Never executed.
import logging
import os

logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(10)

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler", "level": "DEBUG"}},
    "root": {"handlers": ["console"], "level": "DEBUG"},
    "loggers": {
        "django.db.backends": {"level": "DEBUG"},
        "app": {"level": "INFO"},
    },
}
