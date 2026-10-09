"""CODE-RT.2: thread/executor hop awaited immediately around trivial work (Python asyncio)."""
import ast

KEY = "CODE-RT.2"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
REFS = [
    "https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread",
    "https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor",
    "https://docs.python.org/3/library/asyncio-dev.html",
]
RECOMMENDATION = ("Call trivial work directly (no hop). Only offload genuinely blocking calls, and await a coroutine "
                  "function directly instead of passing it to a thread.")
LIMITATION = ("Static pattern only, low confidence by design: 'trivial' means a lambda without calls, a cheap builtin "
              "(len, str, int, ...) or a coroutine function defined in the same file. Unknown functions are never "
              "flagged, and the Node worker_threads/child_process variant of this row is not analysed.")

TRIVIAL_BUILTINS = {"len", "str", "int", "float", "bool", "abs", "min", "max", "repr", "id", "type", "isinstance",
                    "bytes", "tuple"}


def _offloaded(ctx, call):
    """(wrapper name, callable node) for an asyncio.to_thread / run_in_executor call, else None."""
    dotted = ctx.dotted(call.func)
    if dotted == "asyncio.to_thread" and call.args:
        return "to_thread", call.args[0]
    if isinstance(call.func, ast.Attribute) and call.func.attr == "run_in_executor" and len(call.args) >= 2:
        return "run_in_executor", call.args[1]
    return None


def run(ctx):
    coroutine_functions = {n.name for n in ast.walk(ctx.tree) if isinstance(n, ast.AsyncFunctionDef)}
    out = []
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Call) and isinstance(ctx.parent(node), ast.Await)):
            continue
        found = _offloaded(ctx, node)
        if found is None:
            continue
        wrapper, target = found
        qual = ctx.qualname(node)
        if isinstance(target, ast.Lambda) and not any(isinstance(n, ast.Call) for n in ast.walk(target.body)):
            out.append(ctx.hit(node, f"{qual}:{wrapper}(lambda)",
                               f"{wrapper} wraps a trivial lambda and is awaited immediately.", "low"))
        elif isinstance(target, ast.Name) and target.id in TRIVIAL_BUILTINS and target.id not in ctx.aliases:
            out.append(ctx.hit(node, f"{qual}:{wrapper}({target.id})",
                               f"{wrapper} wraps the cheap builtin {target.id}() and is awaited immediately.", "low"))
        elif isinstance(target, ast.Name) and target.id in coroutine_functions:
            out.append(ctx.hit(node, f"{qual}:{wrapper}({target.id})",
                               f"{wrapper} is given the coroutine function {target.id}, which a worker thread cannot await.",
                               "medium"))
    return out
