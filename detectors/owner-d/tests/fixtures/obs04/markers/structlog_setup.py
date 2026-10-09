# Synthetic OBS-04 fixture: structlog. Never executed.
import structlog

structlog.configure(processors=[structlog.processors.JSONRenderer()])
