# Synthetic OBS-02 fixture: similar-looking calls that defer formatting. Never executed.
import logging

logger = logging.getLogger(__name__)


def charge(order, user, parser):
    logger.debug("charging order %s for %s", order.id, user)
    logger.info("user %s charged", user.id)
    logger.debug(f"static message without placeholders")
    logger.debug("implicit " "constant concatenation")
    logger.debug("constant " + "concatenation")
    logger.warning(f"retrying order {order.id}")
    logger.error("failed: %s" % order.id)
    logger.exception(f"unexpected state for {order.id}")
    parser.debug(f"not a logger {order.id}")
    print(f"stdout is not logging {order.id}")
    return order
