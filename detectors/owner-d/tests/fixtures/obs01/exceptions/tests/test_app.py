# Synthetic OBS-01 fixture: tests may enable DEBUG. Never executed.
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("app").setLevel(logging.DEBUG)
