# Synthetic OBS-03 fixture: identity and loop-size boundaries. Never executed.
import logging

logger = logging.getLogger(__name__)


def sync(batch):
    for item in batch:
        logger.debug("start %s", item)
        logger.debug("end %s", item)


def sizes():
    for i in range(10):
        logger.debug("small constant loop %d", i)
    for i in range(11):
        logger.debug("larger constant loop %d", i)


def match(orders, rules):
    for order in orders:
        for rule in rules:
            if rule.matches(order):
                logger.debug("order %s matched %s", order, rule)
                break
