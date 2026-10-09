# Negative cases for CODE-C1.2: every guard must hold (synthetic fixtures)
import os

LIMIT = 10
LIMIT = 20  # module-level stores may be read by other modules: out of scope


TimeoutError = TimeoutError  # module body: re-exports the builtin as a module attribute


class Config:
    os = os  # class body: copies the module into the class namespace


def unbound_local():
    value = value  # UnboundLocalError: a bug, not a redundant statement
    return value


def swap(a, b):
    a, b = b, a
    return a, b


def update_from_self(x):
    x = x + 1
    x = x.strip()
    return x


def read_between(xs):
    total = 0
    log(total)
    total = sum(xs)
    return total


def conditional_overwrite(c):
    value = None
    if c:
        value = compute()
    return value


def loop_target_rebinds(items):
    last = None
    for last in items:
        pass
    return last


def global_store():
    global LIMIT
    LIMIT = 1
    LIMIT = 2


def captured_by_closure():
    state = 1

    def report():
        return state

    state = 2
    return report


def frame_introspection():
    x = 1
    debug(locals())
    x = 2
    return x


def exception_keeps_first_value():
    try:
        result = "fallback"
        result = fetch()
    except OSError:
        pass
    return result


def suppressed_exception_keeps_first_value():
    with suppress(KeyError):
        found = None
        found = table["k"]
    return found


def continue_skips_overwrite(items):
    for x in items:
        tag = "skip"
        if x is None:
            continue
        tag = label(x)
        use(tag)
    return tag


def release_before_reload(path):
    blob = load(path)
    use(blob)
    blob = None
    blob = load(path + ".next")
    return blob


def throwaway(items):
    _ = first(items)
    _ = second(items)


def annotation_only():
    count: int
    count = 0
    return count


def call_in_target():
    get().x = get().x
    cache[key()] = cache[key()]


def unreachable_overwrite():
    x = 1
    return x
    x = 2


def suppressed_store(xs):
    total = 0  # noqa: CODE-C1.2
    total = sum(xs)
    return total


def suppressed_self(x):
    x = x  # noqa: CODE-C1.2
    return x
