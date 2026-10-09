# Synthetic OBS-03 fixture: logging on every loop iteration. Never executed.
import logging

logger = logging.getLogger(__name__)
audit = logging.getLogger("audit")


def import_rows(rows, level):
    for row in rows:
        logger.debug("importing row %s", row.id)
        audit.info("row %s imported", row.id)
        logger.log(level, "row %s stored", row.id)
    return [logger.debug("validated %s", row) for row in rows]


def drain(queue):
    while queue:
        job = queue.pop()
        logger.info("processing job %s", job)


def score(matrix):
    for line in matrix:
        for cell in line:
            logger.debug("cell %s", cell)


def sync(items):
    for item in items:
        if not item.changed:
            logger.debug("unchanged %s", item)
            continue


def serve(sock):
    while True:
        request = sock.recv()
        logger.info("request %s", request)


class Consumer:
    def __init__(self):
        self.logger = logging.getLogger(type(self).__name__)

    async def consume(self, stream):
        async for message in stream:
            self.logger.debug("message %s", message)

    def handle(self, batch):
        for event in batch:
            self.logger.debug("event %s", event)
