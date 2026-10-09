# Synthetic OBS-04 fixture: dictConfig with python-json-logger. Never executed.
import logging.config

logging.config.dictConfig({
    "version": 1,
    "formatters": {"json": {"()": "pythonjsonlogger.jsonlogger.JsonFormatter"}},
    "handlers": {"out": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["out"], "level": "INFO"},
})
