# Positive cases for CODE-C3.5: missing loop early exit (synthetic fixtures)


def s1_boolean_flag(items):
    found = False
    for x in items:
        if is_target(x):
            found = True
    return found


def s1_sticky_constant(items):
    status = "ok"
    for x in items:
        if x.broken:
            status = "degraded"
    return status


def s2_match_store(items):
    result = None
    for x in items:
        if x.key == wanted:
            result = x
    log(result)


def s3_store_then_return(items):
    match = None
    for x in items:
        if x.key == wanted:
            match = x
    return match


def nested_inner_flag(groups):
    total = 0
    for g in groups:
        seen = False
        for x in g:
            if x is None:
                seen = True
        total += 0 if seen else 1
    return total
