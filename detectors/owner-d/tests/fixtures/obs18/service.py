# Synthetic OBS-18 fixture: a structlog service module. Never executed.
import structlog

log = structlog.get_logger()


def charge(user_id, request_id, amount):
    log.info("charge started", user_id=user_id, request_id=request_id)
    log.info("charge done", user_id=user_id, request_id=request_id, amount=amount)
    log.info("refund issued", usr_id=user_id)


def audit(r):
    log.info(
        "audit",
        extra={"field_01": r.a, "field_02": r.b, "field_03": r.c, "field_04": r.d, "field_05": r.e,
               "field_06": r.f, "field_07": r.g, "field_08": r.h, "field_09": r.i, "field_10": r.j,
               "field_11": r.k, "field_12": r.l, "field_13": r.m, "field_14": r.n, "field_15": r.o,
               "field_16": r.p, "field_17": r.q, "field_18": r.r, "field_19": r.s, "field_20": r.t},
        field_21=r.u,
    )
