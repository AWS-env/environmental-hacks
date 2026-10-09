# Negative cases for CODE-C3.7: every guard must suppress (synthetic fixtures)
import collections
from collections import deque


def iterate_slice_copy(items):
    for x in items[:]:
        items.remove(x)


def iterate_list_copy(items):
    for x in list(items):
        items.remove(x)


def iterate_tuple_and_sorted_copies(items):
    for x in tuple(items):
        items.remove(x)
    for x in sorted(items):
        items.remove(x)


def iterate_splat_copy(items):
    for x in [*items]:
        items.remove(x)


def iterate_dict_copies(d):
    for k in dict(d):
        del d[k]
    for k, v in list(d.items()):
        del d[k]


def build_new_collection(items):
    kept = [x for x in items if not x.stale]
    out = []
    for x in items:
        if keep(x):
            out.append(x)
    return kept, out


def deque_drain():
    q = deque(load())
    while q:
        handle(q.popleft())


def deque_pop_front_via_module():
    q = collections.deque(load())
    while q:
        q.insert(0, step(q.pop()))


def annotated_deque_param(q: deque):
    while q:
        q.insert(0, q.pop())


def pop_from_end(stack):
    while stack:
        handle(stack.pop())
    while stack:
        handle(stack.pop(-1))


def reverse_index_delete(a):
    for i in range(len(a) - 1, -1, -1):
        if a[i] < 0:
            del a[i]


def different_object_similar_name(self, items, other):
    for x in self.items:
        other.items.remove(x)
    for x in items:
        self.items.remove(x)


def remove_one_then_break(items):
    for x in items:
        if x.bad:
            items.remove(x)
            break


def remove_one_then_return(d):
    for k in d:
        if k.startswith("_"):
            del d[k]
            return k


def pop_front_once_not_in_loop(queue):
    return queue.pop(0)


def comprehension_does_not_mutate(items):
    return [x for x in items if x]


def mutation_in_nested_def_is_not_in_loop(items):
    for x in items:
        def drop():
            items.remove(x)
        register(drop)


def index_store_is_not_length_changing(a):
    for i, x in enumerate(a):
        a[i] = x * 2


def suppressed(items):
    for x in items:
        items.remove(x)  # noqa: CODE-C3.7
