# Synthetic OBS-04 fixture: noqa and __main__ dev paths. Never executed.
import logging

logger = logging.getLogger(__name__)


def handle(item):
    logger.info("skipping %s", item)  # noqa: OBS-04
    logger.info(f"handled {item}")  # noqa: E501
    return item


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s %(message)s")
    name = input()
    logger.info("dev run for %s", name)
    handle(name)
