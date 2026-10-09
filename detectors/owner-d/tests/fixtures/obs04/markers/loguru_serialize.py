# Synthetic OBS-04 fixture: loguru with JSON output. Never executed.
import sys

from loguru import logger

logger.add(sys.stdout, serialize=True)
