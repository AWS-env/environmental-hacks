# CODE-C1.6 positives: setup whose result is unused on some paths.
import requests


def overwritten_alloc(c):
    rows = []
    if c:
        rows = load_a()
    else:
        rows = load_b()
    return rows


def overwritten_call_with_exit(path, strict):
    data = parse(path)
    if strict:
        data = parse_strict(path)
    elif path.endswith(".json"):
        data = parse_json(path)
    else:
        raise ValueError("unsupported")
    return data


def setup_before_guard(path, enabled):
    fh = open(path)
    if not enabled:
        return None
    return fh.read()


def alloc_before_guards(items):
    seen = set()
    if items is None:
        return []
    if not items:
        return set()
    for x in items:
        seen.add(x)
    return seen


def call_in_loop_before_continue(records):
    out = []
    for r in records:
        parts = r.split(",")
        if r.startswith("#"):
            continue
        out.append(parts)
    return out


def session_before_guard(urls):
    session = requests.Session()
    if not urls:
        return []
    return [session.get(u) for u in urls]
