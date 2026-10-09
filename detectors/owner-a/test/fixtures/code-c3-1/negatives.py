def legit_index_arithmetic(xs):
    # ok: the index itself is needed for arithmetic
    for i in range(len(xs)):
        print(xs[i + 1])


def parallel_index(xs, ys):
    # ok: indexing into a second list
    for i in range(len(xs)):
        print(xs[i], ys[i])


def window_slice(xs):
    # ok: slice window needs the index
    for i in range(len(xs)):
        print(xs[i:i + 2])


def counting(n):
    # ok: counting loop, non-len bound
    for i in range(n):
        print(i)


def already_enumerate(xs):
    # ok: already idiomatic
    for i, x in enumerate(xs):
        print(i, x)


def direct_iteration(xs):
    # ok: direct iteration
    for x in xs:
        print(x)


def non_index_while(xs):
    # ok: while with a non-index condition
    while xs:
        print(xs.pop())


def while_no_init(xs, i):
    # ok: no same-block `i = 0` init, manual indexing not established
    while i < len(xs):
        print(xs[i])
        i += 1


def while_no_increment(xs):
    # ok: no increment, manual indexing not established
    i = 0
    while i < len(xs):
        print(xs[i])


def mutate_index(xs):
    # ok: mutates the iterated collection (C3.7 owns it)
    for i in range(len(xs)):
        xs[i] = 0


def mutate_while(xs):
    # ok: mutates the iterated collection (C3.7 owns it)
    i = 0
    while i < len(xs):
        xs.pop()
        i += 1


def builtin_sum_index(xs):
    # ok: builtin reduction (C10 owns it)
    total = 0
    for i in range(len(xs)):
        total += xs[i]
    return total


def builtin_sum(xs):
    # ok: builtin reduction (C10 owns it)
    total = 0
    for x in xs:
        total += x
    return total


def string_concat(xs):
    # ok: string accumulation (C10.4 owns it)
    s = ""
    for x in xs:
        s += x
    return s


def s3_break_body(xs):
    # ok: multi-statement body is not a mechanical comprehension
    out = []
    for x in xs:
        if x is None:
            break
        out.append(x)
    return out


def s3_multi_statement(xs):
    # ok: extra statement besides the append
    out = []
    for x in xs:
        print(x)
        out.append(x)
    return out


def s3_if_else(xs):
    # ok: if/else changes structure
    out = []
    for x in xs:
        if x:
            out.append(x)
        else:
            print("empty")
    return out


def s3_await(xs):
    # ok: await blocks a mechanical comprehension rewrite
    out = []
    for x in xs:
        out.append(await g(x))
    return out


def s3_try(xs):
    # ok: try/except blocks a mechanical comprehension rewrite
    out = []
    for x in xs:
        try:
            out.append(f(x))
        except ValueError:
            pass
    return out


def s3_out_read(xs):
    # ok: accumulator read inside the loop
    out = []
    for x in xs:
        print(len(out))
        out.append(x)
    return out


def s3_stale_accumulator(xs, out):
    # ok: accumulator is not a fresh [] bound just before the loop
    for x in xs:
        out.append(x)
    return out


def s3_self_append(xs):
    # ok: appending to the iterated collection (C3.7 owns it)
    for x in xs:
        xs.append(x)


def dict_mutate(d):
    # ok: mutates the iterated dict (C3.7 owns it)
    for k in d:
        d[k] = 0


def dict_pop(d):
    # ok: mutates the iterated dict (C3.7 owns it)
    for k in list(d):
        d.pop(k)


def dict_builtin_sum(d):
    # ok: builtin reduction (C10 owns it)
    total = 0
    for k in d:
        total += d[k]
    return total


def already_items(d):
    # ok: already idiomatic
    for k, v in d.items():
        print(k, v)


def already_comprehension(xs):
    # ok: already a comprehension
    return [str(x) for x in xs]


def s1_noqa(xs):
    # ok: explicitly suppressed
    for i in range(len(xs)):  # noqa: CODE-C3.1
        print(xs[i])
