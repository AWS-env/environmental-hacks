# CODE-C1.6 negatives: the setup is read, free, or unsafe to remove or move.
import time

CONFIG = load_config()
if not ENABLED:
    raise SystemExit(0)


def constant_init(c):
    result = None
    if c:
        result = a()
    else:
        result = b()
    return result


def no_else(c):
    items = []
    if c:
        items = load()
    return items


def arm_reads(c):
    buf = []
    if c:
        buf.append(1)
    else:
        buf = [2]
    return buf


def arm_falls_through(c, d):
    buf = []
    if c:
        buf = load()
    else:
        log(d)
    return buf


def condition_reads(c):
    cache = {}
    if cache or c:
        cache = load()
    else:
        cache = other()
    return cache


def used_before_guard(items):
    total = compute(items)
    log(total)
    if not items:
        return 0
    return total


def guard_condition_reads(items):
    data = load(items)
    if not data:
        return None
    return data


def guard_body_reads(items):
    data = load(items)
    if not items:
        return data
    return transform(data)


def guard_with_else(items):
    data = load(items)
    if not items:
        return None
    else:
        log(items)
    return data


def volatile_read(stream, skip):
    line = stream.readline()
    if skip:
        return None
    return line


def volatile_clock(skip):
    start = time.time()
    if skip:
        return 0
    return time.time() - start


def setup_input_rebound(items):
    view = make_view(items)
    items = None
    if not ready():
        return None
    return view


def walrus_guard(items):
    data = load(items)
    if (n := len(items)) == 0:
        return n
    return data


def inside_try(path, c):
    try:
        fh = open(path)
        if c:
            return None
        return fh.read()
    except OSError:
        return None


def inside_with(lock, c):
    with lock:
        buf = []
        if c:
            return None
        buf.append(1)
        return buf


def closure_capture(c):
    acc = []

    def push(v):
        acc.append(v)

    if c:
        return push
    acc.append(1)
    return acc


def frame_access(c):
    tmp = build()
    if c:
        return locals()
    return tmp


def global_name(c):
    global CACHE
    CACHE = {}
    if c:
        return None
    CACHE["k"] = 1


def loop_carried(rows):
    for r in rows:
        if r.fresh:
            cur = expensive(r)
            if r.skip:
                continue
            log(cur)
        use(cur)


def straight_line_overwrite_is_c12(c):
    rows = fetch()
    if c:
        return None
    rows = fetch_again()
    return rows


def never_read_is_c15(c):
    rows = fetch()
    if c:
        return None
    return 0


def generator_setup(c):
    item = yield
    if c:
        return
    use(item)


def self_update(c):
    total = []
    total = merge(total)
    if c:
        return None
    return total


def raise_guard_is_error_handling(width):
    lines = []
    if width <= 0:
        raise ValueError("width")
    lines.append(width)
    return lines


def mutator_setup(cache, filename):
    entry = cache.pop(filename, None)
    if not filename:
        return []
    return [entry]


def guard_reads_mutated_object(do, data):
    decompressed = do.decompress(data)
    if not do.eof:
        return None
    return decompressed


def mutator_passed_as_callable(self, kwds, names):
    result = self._make(map(kwds.pop, names, self))
    if kwds:
        return None
    return result


def impure_setup_before_other_calls(self, fut):
    fd = self.open_fd()
    self.remove_writer(fut)
    if fut.cancelled():
        return None
    return fd
