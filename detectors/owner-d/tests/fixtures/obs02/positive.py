# Synthetic OBS-02 fixture: eager log message construction. Never executed.
import logging

from loguru import logger as trace_log

logger = logging.getLogger(__name__)
audit = logging.getLogger("audit")


def charge(order, user):
    logger.debug(f"charging order {order.id} for {user}")
    logger.debug("order payload: %s" % order.to_dict())
    logger.info("user {} charged".format(user.id))
    audit.debug("total=" + str(order.total))
    logging.debug(f"module-level call for {order.id}")
    logger.log(logging.DEBUG, f"explicit level for {order.id}")
    trace_log.trace(f"loguru trace {order}")
    return order


class Worker:
    def __init__(self):
        self.logger = logging.getLogger(type(self).__name__)

    def handle(self, items):
        for item in items:
            self.logger.debug(f"handling {item}")
