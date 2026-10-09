def s1_indexing(xs):
    for i in range(len(xs)):
        print(xs[i])


def s1_control_flow_ok(xs):
    # S1 stays valid with control flow: only the header changes.
    for i in range(len(xs)):
        if xs[i] is None:
            continue
        print(xs[i])


def s2_manual_while(xs):
    i = 0
    while i < len(xs):
        print(xs[i])
        i += 1


def s3_append(xs):
    out = []
    for x in xs:
        out.append(str(x))
    return out


def s3_gated(xs):
    out = []
    for x in xs:
        if x:
            out.append(str(x))
    return out


def s3_annotated(xs):
    out: list = []
    for x in xs:
        out.append(x)
    return out


def s4_key_lookup(d):
    for k in d:
        print(k, d[k])


def s4_keys_call(d):
    for k in d.keys():
        print(k, d[k])


def nested(xss):
    for i in range(len(xss)):
        for j in range(len(xss[i])):
            print(xss[i][j])
