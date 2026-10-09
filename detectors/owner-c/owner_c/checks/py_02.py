"""PY-02: list.pop(0) / list.insert(0, x) inside loops (O(n) per call)."""
import ast

KEY = "PY-02"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
REFS = ["https://www.pythonmastery.io/tips/deque-for-queues/"]
RECOMMENDATION = "Use collections.deque with popleft()/appendleft() when items are taken from the front."
LIMITATION = ("Static pattern only: list length and call frequency are unknown, so small lists may be fine; "
              "the receiver's type is not resolved.")


def run(ctx):
    out = []
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        attr = node.func.attr
        first = node.args[0]
        zero = isinstance(first, ast.Constant) and first.value == 0 and not isinstance(first.value, bool)
        # list.pop(i) takes one argument and list.insert(i, x) two; other arities are other APIs
        # (dict.pop(0, default), DataFrame.insert(0, col, value)).
        shape_ok = len(node.args) == (1 if attr == "pop" else 2) and not node.keywords
        if attr in ("pop", "insert") and zero and shape_ok and ctx.in_loop(node):
            receiver = ast.unparse(node.func.value)
            out.append(ctx.hit(
                node, f"{ctx.qualname(node)}:{receiver}.{attr}(0)",
                f"{attr}(0) inside a loop shifts every remaining item each time (O(n) per call).",
                "medium"))
    return out
