# Synthetic OBS-18 fixture: EMF payloads, __main__, and noqa. Never executed.
import json

import structlog

log = structlog.get_logger()


def handler(event, context):
    log.info("start", user_id=event["user"], request_id=context.aws_request_id)
    print(json.dumps({"_aws": {"Timestamp": 0, "CloudWatchMetrics": []}, "userId": event["user"], "Latency": 3}))
    log.info("legacy", userId=event["user"])  # noqa: OBS-18
    log.info("upstream", reqId=event["req"])  # noqa: E501
    return {}


def snapshot(order):
    log.info("snapshot", **vars(order))  # noqa: OBS-18


def dump(order):
    log.info("dump", extra=vars(order))  # noqa: E501


if __name__ == "__main__":
    log.info("local run", UserID=1, extra=vars(object()))
