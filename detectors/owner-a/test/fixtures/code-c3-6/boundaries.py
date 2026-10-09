# Boundary rows from the C3.6 plan: each shape is owned by a sibling check, not C3.6.
import heapq


def c6_3_full_consumer(xs):
    return sum([cost(x) for x in xs])


def c7_4_work_before_guard(xs):
    for x in xs:
        y = expensive(x)
        if not cheap(x):
            continue
        use(y)


def c3_5_missing_exit(xs):
    found = False
    for x in xs:
        if p(x):
            found = True
    return found


def c3_1_append_accumulation(xs):
    out = []
    for x in xs:
        if p(x):
            out.append(f(x))
    return out


def c10_2_sorted_prefix(xs):
    return sorted(xs)[0], sorted(xs)[:3]


def c5_1_stored_list_membership(v, stored):
    return v in stored


def c3_7_copy_to_mutate(items, d):
    for x in items[:]:
        items.remove(x)
    for k in list(d):
        del d[k]


def c9_2_over_fetch(cursor):
    rows = cursor.fetchall()
    return rows[0]
