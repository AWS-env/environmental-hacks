# Negative cases for CODE-C6.6: every guard must suppress (synthetic fixtures)
import contextlib
import socket
import subprocess
import sys
import sqlite3
from contextlib import closing, ExitStack

LOG = open("module-level.log", "a")
STREAM = sys.stdout


def with_statement(path):
    with open(path) as f:
        return f.read()


def with_closing(host):
    with closing(socket.create_connection((host, 80))) as s:
        s.sendall(b"ping")


def with_exit_stack(path):
    with ExitStack() as stack:
        f = stack.enter_context(open(path))
        return f.read()


def with_contextlib_exit_stack(path):
    with contextlib.ExitStack() as stack:
        f = stack.enter_context(open(path))
        return f.read()


def closed_in_finally(path):
    f = open(path)
    try:
        return f.read()
    finally:
        f.close()


def popen_waited_in_finally(cmd):
    p = subprocess.Popen(cmd)
    try:
        p.stdin.write(b"x")
    finally:
        p.kill()
        p.wait()


def with_named_later(path):
    f = open(path)
    with f:
        return f.read()


def escapes_return(path):
    f = open(path)
    return f


def escapes_return_tuple(path):
    f = open(path)
    return f, path


def escapes_yield(path):
    f = open(path)
    yield f


def escapes_attribute(self, path):
    self.f = open(path)


def escapes_attribute_via_name(self, path):
    f = open(path)
    self.f = f


def escapes_container(handles, path):
    f = open(path)
    handles.append(f)


def escapes_dict(registry, path):
    conn = sqlite3.connect(path)
    registry["db"] = conn


def escapes_argument(path):
    import json

    f = open(path)
    return json.load(f)


def escapes_closure(path):
    f = open(path)

    def reader():
        return f.read()

    return reader


def stdio_is_not_a_handle():
    out = sys.stdout
    inp = sys.stdin
    out.write(inp.read())


def noqa_blanket(path):
    f = open(path)  # noqa
    return f.read()


def noqa_code(path):
    f = open(path)  # noqa: CODE-C6.6
    return f.read()


def noqa_sim115(path):
    f = open(path)  # noqa: SIM115
    return f.read()


def noqa_other_code_still_fires_elsewhere(path):
    with open(path) as f:  # noqa: SIM115
        return f.read()


def chained_with_is_fine(path):
    with open(path) as f:
        return f.read()


def not_a_handle_callee(path, other):
    a = other.open(path)
    b = os_open(path)
    return a.read() + b.read()


def chained_close_only(path):
    open(path).close()


class Holder:
    handle = open("class-level.txt")
