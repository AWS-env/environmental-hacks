# Synthetic OBS-04 fixture: values passed as extra= fields. Never executed.
import logging

logger = logging.getLogger(__name__)


def charge(amount):
    logger.info("charged", extra={"amount": amount})
