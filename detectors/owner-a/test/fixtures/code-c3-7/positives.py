# Positive cases for CODE-C3.7: inefficient array mutation (synthetic fixtures)
from collections import deque


def s1_remove_while_iterating(items):
    for x in items:
        if x.stale:
            items.remove(x)


def s1_del_dict_key(d):
    for k in d:
        if d[k] is None:
            del d[k]


def s1_pop_dict_items(cache):
    for key, value in cache.items():
        if value.expired:
            cache.pop(key)


class Registry:
    def prune(self):
        for h in self.handlers:
            if h.dead:
                self.handlers.remove(h)


def s2_pop_front_drain(jobs):
    queue = list(jobs)
    while queue:
        job = queue.pop(0)
        run(job)


def s2_insert_front(xs):
    out = []
    for x in xs:
        out.insert(0, x)
    return out


def s2_del_front_unknown_type(buf):
    while len(buf) > 10:
        del buf[0]


def s3_worklist_append(todo):
    for node in todo:
        if node.children:
            todo.append(node.children[0])


def s4_slice_store(a):
    for x in a:
        if x < 0:
            a[:] = [y for y in a if y >= 0]
