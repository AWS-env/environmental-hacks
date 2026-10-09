# Synthetic OBS-04 fixture: a service module with lazy %-style messages. Never executed.
import logging

log = logging.getLogger("billing")


class Billing:
    def charge(self, customer, amount):
        log.debug("charge start")
        log.info("Charging %s amount=%d", customer, amount)
        print(f"charged {customer}")  # not a Lambda module: print is not evaluated
        return amount
