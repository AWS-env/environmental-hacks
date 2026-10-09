# Positive cases for CODE-C1.2: redundant assignment (synthetic fixtures)


def s1_name(x):
    x = x
    return x


def s1_tuple(a, b):
    a, b = a, b
    return a + b


class Counter:
    def s1_attribute(self):
        self.n = self.n


def s1_subscript(row, i):
    row[i] = row[i]


def s2_literal_overwritten(xs):
    total = 0
    total = sum(xs)
    return total


def s2_call_overwritten_in_loop(paths):
    out = []
    for p in paths:
        data = load(p)
        data = load_fresh(p)
        out.append(data)
    return out


def s2_lookup_overwritten(cfg):
    mode = cfg.mode
    log("start", mode="fast")
    mode = "safe"
    return mode


def s2_annotated(items):
    names: list[str] = []
    names = [i.name for i in items]
    return names
