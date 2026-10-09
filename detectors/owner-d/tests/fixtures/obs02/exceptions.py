# Synthetic OBS-02 fixture: legitimate exceptions. Never executed.
import logging

logger = logging.getLogger(__name__)


def charge(order):
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"expensive dump {order.to_dict()}")
    if logger.getEffectiveLevel() <= logging.DEBUG:
        logger.debug("dump: %s" % order.to_dict())
    logger.debug(f"accepted by the team {order.id}")  # noqa: G004
    return order


def not_guarded(order):
    if logger.isEnabledFor(logging.DEBUG):
        pass
    else:
        logger.debug(f"else branch is not guarded {order.id}")


def guarded_by_flag(order):
    debug_enabled = logger.isEnabledFor(logging.DEBUG)
    if debug_enabled:
        logger.debug(f"flag-guarded dump {order.to_dict()}")
