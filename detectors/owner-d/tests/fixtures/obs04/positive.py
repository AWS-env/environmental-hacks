# Synthetic OBS-04 fixture: a Lambda handler that logs and prints free text. Never executed.
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    order_id = event["order_id"]
    logger.info("Done")
    logger.info(f"Processing order {order_id}")
    logger.info("Order %s for tenant %s", order_id, event["tenant"])
    logger.warning("Retrying " + order_id)
    logger.error("Charge failed: {}".format(event.get("reason")))
    print("finished")
    print(f"order={order_id} status=shipped")
    print("event:", event)
    return {"ok": True}
