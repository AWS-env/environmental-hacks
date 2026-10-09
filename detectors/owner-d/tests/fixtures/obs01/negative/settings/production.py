# Synthetic OBS-01 fixture: production settings with near misses only. Never executed.
import argparse
import logging
import os

DEBUG = False
TRACE_LOG_LEVEL = 5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
VERBOSE_LEVEL = logging.DEBUG if os.getenv("VERBOSE") else logging.INFO
parser = argparse.ArgumentParser()
parser.debug("not a logger")

if os.getenv("VERBOSE"):
    logger.setLevel(logging.DEBUG)

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler", "level": "DEBUG"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "debug_flags": {"sql": "DEBUG"},
}
