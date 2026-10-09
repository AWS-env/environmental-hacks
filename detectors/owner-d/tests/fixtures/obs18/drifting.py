# Synthetic OBS-18 fixture: drifting keys and a dump, judged only outside exempt paths. Never executed.
import structlog

log = structlog.get_logger()


def record(user, order):
    log.info("legacy", userId=user.id)
    log.info("order", **vars(order))
