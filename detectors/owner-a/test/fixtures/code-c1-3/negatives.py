# Negative cases for CODE-C1.3: control flow that changes what runs (synthetic fixtures)


def different_arms(x, items):
    if x > 0:
        items.append(x)
    else:
        items.append(-x)


def different_string_tokens(ok, log):
    if ok:
        log("x y")
    else:
        log("xy")


def no_else(x, items):
    # Without an else the arm differs from "do nothing".
    if x:
        items.append(x)


def identical_arms_without_else(a, b, out):
    # When neither a nor b holds nothing runs, so the conditions matter.
    if a:
        out.write(a)
    elif b:
        out.write(a)


def empty_first_arm_before_work(x):
    # The empty arm keeps the else from running.
    if x is None:
        pass
    else:
        x.close()


def empty_arm_before_elif(a, b, out):
    if a:
        pass
    elif b:
        out.write(b)


def only_empty_else(x, out):
    # `else: pass` evaluates nothing; it is style, not waste.
    if x:
        out.write(x)
    else:
        pass


def empty_else_after_work(a, b, out):
    if a:
        pass
    elif b:
        out.write(b)
    else:
        pass


def different_ternary(c, a, b):
    return a if c else b


def zero_cost_jumps(xs, work):
    # A trailing continue / bare return compiles to the same jump: not flagged.
    for x in xs:
        work(x)
        continue
    return


def constant_false_is_c1_1(run):
    if False:
        run()


def busy_wait_is_not_a_branch(poll):
    while not poll():
        pass


def suppressed(flag):
    if flag:  # noqa: CODE-C1.3
        pass
