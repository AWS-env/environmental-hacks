def numeric_int(rows):
    total = 0
    for row in rows:
        total += row.count
    return total


def numeric_float_expr(rows):
    total = 0.0
    for row in rows:
        total = total + 1.5
    return total


def list_concat(rows):
    acc = []
    for row in rows:
        acc += [row]
    return acc


def list_expr_into_unknown(rows):
    acc = []
    for row in rows:
        acc = acc + [row]
    return acc


def bytes_concat(chunks):
    data = b""
    for chunk in chunks:
        data += b"x"
    return data


def tuple_concat(rows):
    acc = ()
    for row in rows:
        acc += (row,)
    return acc


def at_most_once(rows):
    s = ""
    for row in rows:
        s += row
        break
    return s


def returns_after(rows):
    s = ""
    for row in rows:
        s += row
        return s


def small_literal_list():
    s = ""
    for part in ["a", "b", "c", "d", "e", "f", "g", "h"]:
        s += part
    return s


def small_range():
    s = ""
    for i in range(8):
        s += "x"
    return s


def uses_join(rows):
    parts = []
    for row in rows:
        parts.append(row.name)
    return "".join(parts)


def unknown_binding(base, rows):
    s = base
    for row in rows:
        s += row
    return s


def unannotated_param(s, rows):
    for row in rows:
        s += row
    return s


def reset_each_iteration(rows):
    out = []
    for row in rows:
        s = ""
        s += row.a
        out.append(s)
    return out


def noqa_on_statement(rows):
    s = ""
    for row in rows:
        s += row  # noqa: CODE-C10.4
    return s


def noqa_on_header(rows):
    s = ""
    for row in rows:  # noqa
        s += row
    return s


def other_noqa_code_is_not_suppressing_but_unrelated_name(rows):
    n = 0
    for row in rows:
        n += 1  # noqa: F401
    return n


def attribute_numeric(rows):
    class C:
        pass

    c = C()
    c.total = 0
    for row in rows:
        c.total += row.size
    return c


def nested_function_not_crossed(rows):
    s = ""

    def inner(more):
        for m in more:
            pass

    for row in rows:
        inner(row)
    return s


def read_via_call_argument(rows, live):
    display = ""
    for step in rows:
        display += f"Step {step}\n"
        live.update(display, refresh=True)
    return display


def read_via_len(rows):
    s = ""
    for r in rows:
        s += r
        if len(s) > 80:
            s = s[-10:]
    return s


def read_via_comparison(rows):
    s = ""
    for r in rows:
        s += r
        if s == "stop":
            break
    return s


def read_via_fstring(rows, out):
    s = ""
    for r in rows:
        s += r
        out.append(f"{s}!")
    return s


class Reader:
    def __init__(self):
        self.buf = ""

    def go(self, rows, sink):
        for r in rows:
            self.buf += r
            sink(self.buf)