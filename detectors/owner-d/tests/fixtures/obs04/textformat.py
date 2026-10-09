# Synthetic OBS-04 fixture: an explicit plain-text log format. Never executed.
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def sync(batch):
    logger.info("synced %d rows", len(batch))
