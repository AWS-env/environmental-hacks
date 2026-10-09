# Synthetic OBS-04 fixture: boundaries. Never executed.
import logging

logger = logging.getLogger(__name__)


def handler(event):
    print(f"got {event}")
    logger.info("constant")
    logger.info("only %s", event["id"])
    logger.info("empty extra %s", event, extra={})
