# Negative cases for CODE-C3.3: every guard must hold (synthetic fixtures)
import re
import sqlite3
from collections import Counter, defaultdict
from io import StringIO

import requests


def hoisted_compile(lines, rule):
    rx = re.compile(rule)
    for line in lines:
        if rx.search(line):
            handle(line)


def hoisted_session_wraps_loop(urls):
    with requests.Session() as s:
        for url in urls:
            s.get(url)


def with_open_wraps_loop(path, keys):
    with open(path) as f:
        for key in keys:
            lookup(f, key)


def loop_var_argument(rules, text):
    for rule in rules:
        rx = re.compile(rule)
        rx.search(text)


def per_database_connection(db_paths):
    # Each iteration targets a different database: per-iteration connect is required.
    for db in db_paths:
        conn = sqlite3.connect(db)
        migrate(conn)
        conn.close()


def body_assigned_argument(items):
    for item in items:
        path = item.path
        with open(path) as f:
            consume(f)


def write_mode_open(rows, path):
    for row in rows:
        with open(path, "a") as f:
            f.write(row)


def write_mode_keyword(rows, path):
    for row in rows:
        with open(path, mode="w") as f:
            f.write(row)


def scratch_containers(rows):
    for row in rows:
        out = []
        seen = set()
        counts = Counter()
        groups = defaultdict(list)
        buf = StringIO()
        out.append(row)
        seen.add(row)
        counts[row] += 1
        groups[row].append(row)
        buf.write(row)


def scratch_capwords(rows, cfg):
    for row in rows:
        acc = Accumulator(cfg)
        acc.add(row)
        flush(acc)


def exception_construction(rows, msg):
    for row in rows:
        if not row:
            raise ValueError(msg)


def unbound_capwords(rows, cfg):
    for row in rows:
        emit(Record(cfg), row)


def capwords_function(rows, cfg):
    for row in rows:
        r = MakeRecord(cfg)
        emit(r, row)


def MakeRecord(cfg):
    return cfg


def comprehension_shadowing(rules):
    return [re.compile(r) for r in rules]


def runs_at_most_once(rows, rule):
    for row in rows:
        rx = re.compile(rule)
        return rx.match(row)


def suppressed(lines, rule):
    for line in lines:
        rx = re.compile(rule)  # noqa: CODE-C3.3
        rx.search(line)


def bare_request_is_c9_1(urls):
    for url in urls:
        requests.get(url)
