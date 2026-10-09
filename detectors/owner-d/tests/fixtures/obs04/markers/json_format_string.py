# Synthetic OBS-04 fixture: a JSON-shaped format string. Never executed.
import logging

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "message": "%(message)s"}'))
