# Synthetic OBS-02 fixture: repeated anchors in one function. Never executed.
import logging

logger = logging.getLogger(__name__)


def sync(batch):
    logger.debug(f"start {batch}")
    logger.debug(f"end {batch}")
