import collections
from collections import defaultdict


def list_literal(x, acc=[]):
    acc.append(x)
    return acc


def dict_literal(k, v, store={}):
    store.setdefault(k, v)
    return store


def set_literal(x, bag=set()):
    bag.add(x)


def set_brace(x, bag={1}):
    bag.discard(x)


def comprehension(x, acc=[i for i in range(3)]):
    acc.extend([x])


def dict_comp(x, d={i: i for i in range(2)}):
    d.update({x: x})


def list_call(x, acc=list()):
    acc.insert(0, x)


def defaultdict_call(x, d=defaultdict(list)):
    d[x].append(1)
    d.clear()


def deque_call(x, q=collections.deque()):
    q.appendleft(x)


def bytearray_call(x, b=bytearray()):
    b.extend(x)


def plus_equals(x, acc=[]):
    acc += [x]
    return acc


def item_assign(k, v, store={}):
    store[k] = v


def delete_item(k, store={"a": 1}):
    del store[k]


async def async_fn(x, acc=[]):
    acc.append(x)


def branch_only(x, flag, acc=[]):
    if flag:
        acc.append(x)
    return acc


def typed(x, acc: list = []):
    acc.sort()


class Service:
    def method(self, x, acc=[]):
        acc.append(x)


def lambda_default():
    return lambda x, acc=[]: acc.append(x)


def memo_name(n, cache={}):
    cache[n] = n * 2
    return cache[n]


def closure_mutation(x, acc=[]):
    def inner():
        acc.append(x)
    inner()
