# Negative cases for CODE-C3.6: every guard must suppress (synthetic fixtures)
import itertools
import random


def in_loop_filter_is_filter_first(items):
    for x in items:
        if pred(x):
            process(x)


def full_consumer_sum(xs):
    return sum([cost(x) for x in xs])


def full_consumer_len(xs):
    return len([x for x in xs if p(x)])


def full_loop_no_exit(xs):
    for r in [parse(x) for x in xs]:
        handle(r)


def name_read_twice(lines):
    rows = [parse(l) for l in lines]
    first = rows[0]
    return first, len(rows)


def name_returned(lines):
    rows = [parse(l) for l in lines]
    return rows


def name_reassigned(lines):
    rows = [parse(l) for l in lines]
    rows = rows[:3]
    return rows


def last_element(xs):
    return [f(x) for x in xs][-1]


def suffix_slice(xs, k):
    return [f(x) for x in xs][-k:]


def strided_slice(xs):
    return [f(x) for x in xs][::2]


def variable_index(xs, i):
    return [f(x) for x in xs][i]


def offset_slice(xs):
    return [f(x) for x in xs][2:5]


def full_copy_slice(xs):
    return [f(x) for x in xs][:]


def break_in_inner_loop(groups):
    for g in [load(x) for x in groups]:
        for item in g:
            if item.bad:
                break
        handle(g)


def raise_caught_locally(xs):
    for r in [parse(x) for x in xs]:
        try:
            raise ValueError(r)
        except ValueError:
            pass


def copy_to_mutate(items):
    for x in [i for i in items if i.stale]:
        items.remove(x)
        if not items:
            break


def volatile_element(xs):
    return [random.choice(x) for x in xs][0]


def mutating_element(xs, out):
    return any([out.append(x) for x in xs])


def logging_element(xs, log):
    return any([log.info(x) for x in xs])


def print_mapped(xs):
    return list(map(print, xs))[0]


def small_literal_source(a, b, c):
    return any([check(v) for v in (a, b, c)])


def small_range_source():
    return [probe(i) for i in range(4)][0]


def already_lazy_generator(xs):
    return any(is_bad(x) for x in xs)


def already_lazy_next(xs):
    return next((f(x) for x in xs if p(x)), None)


def already_lazy_islice(xs):
    return list(itertools.islice((score(x) for x in xs), 10))


def already_lazy_map(xs):
    for r in map(parse, xs):
        if r.ok:
            return r


def iterate_file_object(f):
    for line in f:
        if line.startswith("END"):
            break


def whitespace_split_is_not_lines(f):
    return "x" in f.read().split()


def module_level_one_hop_is_not_followed():
    pass


ROWS = [parse(l) for l in LINES]
for r in ROWS:
    if r.ok:
        break


def read_inside_repeating_loop(lines):
    rows = [parse(l) for l in lines]
    while True:
        for r in rows:
            if r.ok:
                return r


def suppressed(xs):
    return [f(x) for x in xs][0]  # noqa: CODE-C3.6
