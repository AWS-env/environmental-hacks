# Synthetic OBS-18 fixture: one spelling per field. Never executed.
import structlog

log = structlog.get_logger()


def charge(user):
    log.info("charge", user_id=user.id)
