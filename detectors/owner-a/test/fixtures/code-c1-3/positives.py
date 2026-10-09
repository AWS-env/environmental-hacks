# Positive cases for CODE-C1.3: redundant control flow (synthetic fixtures)


def s1_identical_if_else(x, items):
    if x > 0:
        items.append(x)
    else:
        items.append(x)
    return items


def s1_identical_elif_chain(mode, out):
    if mode == "a":
        out.write(mode)  # comments do not make arms differ
    elif mode == "b":
        out.write(mode)
    else:
        out.write(mode)


def s1_identical_call_condition_in_loop(rows):
    for row in rows:
        if is_valid(row):
            emit(row)
        else:
            emit(row)


def s1_identical_ternary(cfg, default):
    return default if cfg.strict else default


def s2_empty_if(flag):
    if flag:
        pass
    return flag


def s2_empty_if_else(x):
    if x is None:
        ...
    else:
        pass
    return x


def s2_trailing_empty_elif(n, log):
    if n < 0:
        log("negative")
    elif n == 0:
        pass
    return n
