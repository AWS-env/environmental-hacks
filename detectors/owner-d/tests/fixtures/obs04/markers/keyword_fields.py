# Synthetic OBS-04 fixture: structlog-style keyword fields on a wrapped logger. Never executed.
from app.logs import get_logger

log = get_logger()


def charge(amount):
    log.info("charged", amount=amount)
