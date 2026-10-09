# Synthetic OBS-18 fixture: threshold and tie-break boundaries. Never executed.
import structlog

log = structlog.get_logger()

TWENTY = dict(field_01=1, field_02=2, field_03=3, field_04=4, field_05=5, field_06=6, field_07=7)


def twenty(r):
    log.info("twenty", extra={"field_01": 1, "field_02": 2, "field_03": 3, "field_04": 4, "field_05": 5,
                              "field_06": 6, "field_07": 7, "field_08": 8, "field_09": 9, "field_10": 10,
                              "field_11": 11, "field_12": 12, "field_13": 13, "field_14": 14, "field_15": 15,
                              "field_16": 16, "field_17": 17, "field_18": 18, "field_19": 19, "field_20": 20})


def twenty_one(r):
    log.info("twenty one", extra={"field_01": 1, "field_02": 2, "field_03": 3, "field_04": 4, "field_05": 5,
                                  "field_06": 6, "field_07": 7, "field_08": 8, "field_09": 9, "field_10": 10,
                                  "field_11": 11, "field_12": 12, "field_13": 13, "field_14": 14, "field_15": 15,
                                  "field_16": 16, "field_17": 17, "field_18": 18, "field_19": 19},
             nested={"field_20": 20, "field_21": 21}, **TWENTY)


def traced(span):
    log.info("span start", trace_id=span.trace_id)
    log.info("span end", traceId=span.trace_id)
