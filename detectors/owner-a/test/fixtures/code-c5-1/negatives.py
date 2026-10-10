from typing import Set
from somewhere import imported_list


def set_binding(rows):
    allowed = {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"}
    return [r for r in rows if r in allowed]


def frozenset_binding(rows, src):
    allowed = frozenset(src)
    return [r for r in rows if r in allowed]


def dict_binding(rows, src):
    lookup = {k: 1 for k in src}
    return [r for r in rows if r in lookup]


def dict_keys_binding(rows, lookup):
    keys = lookup.keys()
    return [r for r in rows if r in keys]


def range_binding(rows):
    window = range(100)
    return [r for r in rows if r in window]


def str_binding(rows):
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return [r for r in rows if r in alphabet]


def set_annotated(rows, known: Set[str]):
    return [r for r in rows if r in known]


def unknown_imported(rows):
    return [r for r in rows if r in imported_list]


def unknown_param(rows, known):
    return [r for r in rows if r in known]


def inline_literals(rows, a, b):
    out = []
    for r in rows:
        if r in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
            out.append(r)
        if r in (a, b):
            out.append(r)
    return out


def small_literal(rows):
    small = ["a", "b", "c", "d", "e", "f", "g", "h"]
    return [r for r in rows if r in small]


def mutated_append(rows):
    seen = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    for r in rows:
        if r not in seen:
            seen.append(r)


def mutated_extend(rows):
    seen = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    for r in rows:
        if r in seen:
            continue
        seen.extend([r])


def mutated_insert_remove_pop_clear(rows):
    seen = list(rows)
    for r in rows:
        if r in seen:
            seen.remove(r)
    for r in rows:
        if r in seen:
            seen.pop()
    for r in rows:
        if r in seen:
            seen.clear()
    for r in rows:
        if r in seen:
            seen.insert(0, r)


def mutated_augmented(rows):
    seen = list(rows)
    for r in rows:
        if r in seen:
            seen += [r]


def mutated_item_assign_del(rows):
    seen = list(rows)
    for r in rows:
        if r in seen:
            seen[0] = r
    for r in rows:
        if r in seen:
            del seen[0]


def rebound_in_loop(rows):
    seen = list(rows)
    for r in rows:
        if r in seen:
            seen = [r]


def runs_once(rows):
    names = list(rows)
    for r in rows:
        if r in names:
            print(r)
        break


def returns_immediately(rows):
    names = list(rows)
    while rows:
        found = rows in names
        return found


def outside_loop(x):
    names = list(x)
    return x in names


def iterable_evaluated_once(rows, src):
    names = list(src)
    return [r for r in (names if src in names else []) if r]


def mixed_bindings(rows, flag):
    names = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    if flag:
        names = set(names)
    return [r for r in rows if r in names]


def suppressed_header(rows):
    names = list(rows)
    for r in rows:  # noqa: CODE-C5.1
        if r in names:
            print(r)


def suppressed_line(rows):
    names = list(rows)
    for r in rows:
        if r in names:  # noqa
            print(r)


def suppressed_specific(rows):
    names = list(rows)
    return [r for r in rows if r in names]  # noqa: CODE-C5.1


def not_a_tracked_name(rows, cfg):
    names = list(rows)
    for r in rows:
        if r in cfg.names:
            print(r)
        if r in names[1:]:
            print(r)
        if r in other.names:
            print(r)


def small_literal_loop(names_src):
    names = list(names_src)
    for prefix in ["/path-a", "/path-b", "/path-c"]:
        assert prefix not in names


def small_tuple_set_loops(src):
    names = list(src)
    for p in ("a", "b"):
        if p in names:
            print(p)
    for p in {"a", "b", "c"}:
        if p in names:
            print(p)


def small_dict_items_comprehension(include_src):
    include = list(include_src)
    data = {"text/plain": 1, "text/html": 2}
    return {k: v for (k, v) in data.items() if k in include}


def small_dict_keys_values(src):
    names = list(src)
    data = {"a": 1, "b": 2}
    for k in data.keys():
        if k in names:
            print(k)
    return [v for v in data.values() if v in names]


def small_bound_name(src):
    names = list(src)
    kinds = ["x", "y", "z"]
    for k in kinds:
        if k in names:
            print(k)


def small_range(src):
    names = list(src)
    for i in range(5):
        if i in names:
            print(i)
    for i in range(2, 6):
        if i in names:
            print(i)
    return [i for i in range(8) if i in names]


def exactly_eight_literal(src):
    names = list(src)
    for p in [1, 2, 3, 4, 5, 6, 7, 8]:
        if p in names:
            print(p)


def small_nested_both(src):
    names = list(src)
    for a in (1, 2):
        for b in (3, 4):
            if b in names:
                print(a)


def jupyter_shape(src_include, src_exclude, a, b):
    include = list(src_include)
    exclude = list(src_exclude)
    data = {"text/plain": a, "text/html": b}
    data = {k: v for (k, v) in data.items() if k in include}
    data = {k: v for (k, v) in data.items() if k not in exclude}
    return data


def derived_list_from_small(src):
    names = list(src)
    kinds = ["x", "y"]
    kinds = [k for k in kinds if k]
    return [k for k in kinds if k in names]
