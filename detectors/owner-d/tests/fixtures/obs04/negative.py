# Synthetic OBS-04 fixture: a Lambda handler whose lines carry no free-text values. Never executed.
import json
import logging

logger = logging.getLogger(__name__)
MESSAGE = "static message"


def handler(event, context):
    logger.info("Started")
    logger.info(MESSAGE)
    logger.info(event)
    logger.info("retries=%d", 3)
    logger.debug("%s", "constant")
    cache.info(f"cache {event['id']}")
    print(json.dumps({"order": event["id"], "level": "info"}))
    print("result:", json.dumps(event))
    print("finished")
    print(event)
    print(*event)
    return {"ok": True}
