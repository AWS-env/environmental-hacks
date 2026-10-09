# Synthetic OBS-04 fixture: a test module. Never executed.
import logging

logger = logging.getLogger(__name__)


def test_charge():
    amount = 3
    logger.info(f"charging {amount}")
    assert amount == 3
