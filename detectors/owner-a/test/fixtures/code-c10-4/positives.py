def local_augmented(rows):
    out = ""
    for row in rows:
        out += row.name
    return out


def local_self_concat(rows):
    s = ''
    for row in rows:
        s = s + row.name
    return s


def fstring_append(rows):
    text = f"header {len(rows)}\n"
    for row in rows:
        text += f"{row.id},{row.name}\n"
    return text


def while_loop(reader):
    buf = str(reader.first())
    while reader.more():
        buf += reader.next()
    return buf


def param_annotated(prefix: str, rows):
    for row in rows:
        prefix += row
    return prefix


def joined_start(rows):
    s = ", ".join(["a", "b"])
    for row in rows:
        s += row
    return s


class Builder:
    def __init__(self):
        self.buf = ""

    def add_all(self, rows):
        for row in rows:
            self.buf += row.text


def attribute_unknown_binding(obj, rows):
    for row in rows:
        obj.log += "line: {}\n".format(row)



def append_only_twin(rows, live):
    display = ""
    for step in rows:
        display += f"Step {step}\n"
    live.update(display)
