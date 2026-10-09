import copy
from typing import Final, Sequence, Mapping


def never_mutated(x, acc=[]):
    return list(acc) + [x]


def reads_only(k, d={}):
    return d.get(k)


def none_default(x, acc=None):
    if acc is None:
        acc = []
    acc.append(x)
    return acc


def immutable_defaults(x, a=(), b=frozenset(), c=0, d="s", e=None):
    return x


def rebind_list(x, acc=[]):
    acc = list(acc)
    acc.append(x)
    return acc


def rebind_or(x, acc=[]):
    acc = acc or []
    acc.append(x)


def rebind_if_none(x, acc=[]):
    if acc is None:
        acc = []
    acc.append(x)


def rebind_copy(x, acc=[]):
    acc = copy.copy(acc)
    acc.append(x)


def rebind_then_item(k, v, store={}):
    store = dict(store)
    store[k] = v


def shadow_nested_param(x, acc=[]):
    def inner(acc):
        acc.append(x)
    return inner


def shadow_nested_local(x, acc=[]):
    def inner():
        acc = []
        acc.append(x)
    return inner


def shadow_lambda(x, acc=[]):
    return lambda acc: acc.append(x)


def annotated_final(x, acc: Final = []):
    acc.append(x)


def annotated_sequence(x, acc: Sequence[int] = []):
    acc.append(x)


def annotated_mapping(k, d: Mapping[str, int] = {}):
    d[k] = 1


def annotated_tuple(x, acc: tuple = ()):
    acc.append(x)


def noqa_def(x, acc=[]):  # noqa: CODE-C6.7
    acc.append(x)


def noqa_ruff(x, acc=[]):  # noqa: B006
    acc.append(x)


def noqa_blanket(x, acc=[]):  # noqa
    acc.append(x)


def noqa_mutation(x, acc=[]):
    acc.append(x)  # noqa: CODE-C6.7


def noqa_param_line(
    x,
    acc=[],  # noqa: B006
):
    acc.append(x)


def mutates_other_name(x, acc=[]):
    other = []
    other.append(x)
    return acc
