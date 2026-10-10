from typing import List, Sequence

BLOCKED = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]


def for_loop(rows):
    allowed = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    out = []
    for row in rows:
        if row not in allowed:
            out.append(row)
    return out


def while_loop(queue):
    seen = list(queue)
    while queue:
        item = queue.pop()
        if item in seen:
            continue


def comprehension(rows, text):
    words = text.split()
    return [r for r in rows if r in words]


def generator_element(rows):
    names = [r.name for r in rows]
    return any(x in names for x in rows)


def annotated_param(rows, known: List[str]):
    return [r for r in rows if r in known]


def annotated_sequence(rows, known: Sequence[int]):
    hits = 0
    for r in rows:
        if r in known:
            hits += 1
    return hits


def module_level_name(rows):
    for r in rows:
        if r in BLOCKED:
            return_value = r


def sorted_binding(rows, src):
    ordered = sorted(src)
    for r in rows:
        if r in ordered:
            print(r)


class Registry:
    def __init__(self):
        self.ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    def count(self, rows):
        n = 0
        for r in rows:
            if r in self.ids:
                n += 1
        return n


def twice_same_scope(rows):
    names = [r.name for r in rows]
    for r in rows:
        if r.a in names:
            pass
    for r in rows:
        if r.b in names:
            pass


def nine_literal_loop(rows):
    names = list(rows)
    for p in ["a", "b", "c", "d", "e", "f", "g", "h", "i"]:
        if p in names:
            print(p)


def splat_iterable(rows, extra):
    names = list(rows)
    for p in [*extra, 1]:
        if p in names:
            print(p)


def range_nine(rows):
    names = list(rows)
    for i in range(9):
        if i in names:
            print(i)


def dict_nine_items(rows):
    names = list(rows)
    table = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9}
    return {k: v for k, v in table.items() if k in names}


def small_name_but_mutated(rows):
    names = list(rows)
    queue = [1, 2]
    queue.append(3)
    for q in queue:
        if q in names:
            print(q)


def small_inner_large_outer(rows, cols):
    names = list(rows)
    for r in rows:
        for c in (1, 2, 3):
            if c in names:
                print(r)


def derived_from_other_name(src_include, big):
    include = list(src_include)
    data = {"text/plain": 1, "text/html": 2}
    data = {k: v for (k, v) in big.items() if k in include}
    return {k: v for (k, v) in data.items() if k in include}


def derived_two_for_clauses(src_include, big):
    include = list(src_include)
    data = {"text/plain": 1, "text/html": 2}
    data = {k: v for k in big for v in data.values()}
    return {k: v for (k, v) in data.items() if k in include}
