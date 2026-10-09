# Synthetic OBS-18 fixture: consistent, lean structured logging and look-alikes. Never executed.
import json
import logging
from dataclasses import asdict

import structlog

log = structlog.get_logger()
logger = logging.getLogger(__name__)


def handle(user, req, timer, fields, client, cache, order):
    log.info("started", user_id=user.id, request_id=req.id, duration_ms=timer.ms)
    log.info("exercise", user_id=user.id, uid=req.exercise_uid)
    log.info("slow", user_id=user.id, duration_s=timer.seconds)
    log.info("named", user_id=user.id, user_name=user.name, user=user.login)
    log.info("failed", exc_info=True, stack_info=False, stacklevel=2)
    logger.info("dynamic fields", extra=fields)
    logger.info("order", extra={"order": order.as_payload(), "user_id": user.id})
    logger.info(json.dumps({"event": "done", "user_id": user.id}))
    payload = {"userId": user.id, "uid": user.id, **vars(order)}
    client.send(payload)
    client.info("sent", userId=user.id, reqId=req.id, extra=vars(order))
    cache.bind(userId=user.id)
    log.info("typed record", product=order.model_dump(), item=asdict(order), row=order._asdict())
    logger.info(
        "twenty",
        extra={"field_01": 1, "field_02": 2, "field_03": 3, "field_04": 4, "field_05": 5,
               "field_06": 6, "field_07": 7, "field_08": 8, "field_09": 9, "field_10": 10,
               "field_11": 11, "field_12": 12, "field_13": 13, "field_14": 14, "field_15": 15,
               "field_16": 16, "field_17": 17, "field_18": 18, "field_19": 19, "field_20": 20},
    )
