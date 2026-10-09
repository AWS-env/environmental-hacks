# Synthetic OBS-04 fixture: a hand-written JSON formatter. Never executed.
import json
import logging


class JsonLines(logging.Formatter):
    def format(self, record):
        return json.dumps({"level": record.levelname, "message": record.getMessage()})
