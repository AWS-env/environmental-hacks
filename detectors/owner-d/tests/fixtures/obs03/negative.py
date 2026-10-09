# Synthetic OBS-03 fixture: similar-looking calls that do not log per iteration. Never executed.
import logging
import time

logger = logging.getLogger(__name__)


def import_rows(rows, parser):
    logger.info("importing %d rows", len(rows))
    for row in rows:
        if row.broken:
            logger.warning("row %s is broken", row.id)
        try:
            row.save()
        except OSError:
            logger.debug("retry later for %s", row.id)
        parser.debug(row)
        print(row)
    else:
        logger.info("all rows visited")
    logger.info("imported %d rows", len(rows))


def callbacks(handlers):
    for handler in handlers:
        def on_done(result):
            logger.debug("handler finished with %s", result)
        handler.register(on_done)


def configure(options):
    for name in ("cache", "db", "queue"):
        logger.debug("configuring %s", name)
    for attempt in range(3):
        logger.info("probe %d", attempt)
    return [logger.debug("option %s", key) for key in ["a", "b"]]


def wait_ready(service):
    while not service.ready():
        logger.debug("waiting for %s", service)
        time.sleep(1)


def first_match(items, wanted):
    for item in items:
        if item == wanted:
            logger.info("found %s", item)
            return item
    for item in items:
        logger.info("only the first item %s", item)
        break
    return None
