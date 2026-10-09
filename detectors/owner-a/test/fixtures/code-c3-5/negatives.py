# Negative cases for CODE-C3.5: every guard must hold (synthetic fixtures)


def already_breaks(items):
    found = False
    for x in items:
        if is_target(x):
            found = True
            break
    return found


def already_returns(items):
    for x in items:
        if is_target(x):
            return x
    return None


def for_else(items):
    for x in items:
        if is_target(x):
            break
    else:
        x = None
    return x


def accumulates_list(items):
    out = []
    for x in items:
        if keep(x):
            out.append(x)
    return out


def counts(items):
    n = 0
    for x in items:
        if keep(x):
            n += 1
    return n


def reset_in_else(items):
    found = False
    for x in items:
        if is_target(x):
            found = True
        else:
            found = False
    return found


def per_element_side_effect(items):
    found = False
    for x in items:
        audit(x)
        if is_target(x):
            found = True
    return found


def side_effect_on_match(items):
    found = False
    for x in items:
        if is_target(x):
            found = True
            notify(x)
    return found


def flag_read_in_condition(items):
    found = False
    for x in items:
        if not found and is_target(x):
            found = True
    return found


def value_reads_target(items):
    best = None
    for x in items:
        if better(x):
            best = merge(best, x)
    return best


def while_condition_is_exit(items):
    found = False
    i = 0
    while not found:
        if items[i]:
            found = True


def not_initialised_before(items):
    for x in items:
        if is_target(x):
            last = x


def volatile_match(items):
    stamp = None
    for x in items:
        if is_target(x):
            stamp = time()
    return stamp


def already_idiomatic(items):
    found = any(is_target(x) for x in items)
    first = next((x for x in items if is_target(x)), None)
    return found, first


def eager_any_is_c3_6(items):
    return any([is_target(x) for x in items])


def suppressed(items):
    found = False
    for x in items:  # noqa: CODE-C3.5
        if is_target(x):
            found = True
    return found
