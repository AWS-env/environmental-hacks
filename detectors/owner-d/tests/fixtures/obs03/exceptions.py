# Synthetic OBS-03 fixture: legitimate exceptions. Never executed.
import logging

logger = logging.getLogger(__name__)


def guarded(rows):
    debug_enabled = logger.isEnabledFor(logging.DEBUG)
    for i, row in enumerate(rows):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("row %s", row)
        if debug_enabled:
            logger.debug("row again %s", row)
        if i % 1000 == 0:
            logger.info("progress %d/%d", i, len(rows))
        logger.debug("accepted by the team %s", row)  # noqa: OBS-03


class Exporter:
    def export(self, rows):
        for row in rows:
            if self.verbose:
                logger.info("exporting %s", row)


def not_guarded(rows):
    for row in rows:
        if logger.isEnabledFor(logging.DEBUG):
            pass
        else:
            logger.debug("else branch is not guarded %s", row)
