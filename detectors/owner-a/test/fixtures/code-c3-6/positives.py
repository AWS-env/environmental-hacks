# Positive cases for CODE-C3.6: eager producer, subset consumer (synthetic fixtures)
import itertools


def s1_index_zero(xs):
    return [f(x) for x in xs if p(x)][0]


def s1_prefix_slice(xs):
    top = [score(x) for x in xs][:10]
    return top


def s1_list_map_prefix(xs, k):
    return list(map(score, xs))[:k]


def s1_next_iter(xs):
    return next(iter([parse(x) for x in xs]), None)


def s1_trivial_projection(xs):
    return [x.id for x in xs][0]


def s2_readlines_break(f):
    for line in f.readlines():
        if line.startswith("END"):
            break
        handle(line)


def s2_one_hop_return(lines):
    rows = [parse(l) for l in lines]
    for r in rows:
        if r.ok:
            return r
    return None


def s3_any(xs):
    return any([is_bad(x) for x in xs])


def s3_all_list_gen(xs):
    return all(list(check(x) for x in xs))


def s3_membership(key, keys):
    return key in [norm(k) for k in keys]


def s3_not_in_split(f, name):
    return name not in f.read().splitlines()
