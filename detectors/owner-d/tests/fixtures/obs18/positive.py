# Synthetic OBS-18 fixture: a Lambda handler with drifting field names and object dumps. Never executed.
import json
from dataclasses import asdict

from aws_lambda_powertools import Logger

logger = Logger(service="orders")


def lambda_handler(event, context):
    logger.append_keys(requestId=context.aws_request_id)
    user = event["user"]
    logger.info("order received", extra={"userId": user["id"], "order_id": event["order_id"]})
    logger.info("user loaded", extra={"user": {"id": user["id"]}})
    order = load(event["order_id"])
    logger.info("order loaded", extra=vars(order))
    logger.debug("order state", **order.__dict__)
    print(json.dumps(vars(order)))
    return {"order": asdict(order)}


def load(order_id):
    return order_id
