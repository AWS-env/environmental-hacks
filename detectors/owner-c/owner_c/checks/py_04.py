"""PY-04: string `+=` inside loops (quadratic copying)."""
import ast

from owner_c.common import is_str_like, scope_walk

KEY = "PY-04"
DETECTOR_VERSION = "1.0.0"
NOQA = ("PLR1713", "PERF")
REFS = [
    "https://docs.aws.amazon.com/amazonq/detector-library/python/string-concatenation/",
    "https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/consider-using-join.html",
]
RECOMMENDATION = "Append the parts to a list inside the loop and call ''.join(parts) once after it."
LIMITATION = ("Static pattern only: the loop's iteration count is unknown, so short loops may be harmless. "
              "Variable types are inferred from assignments in the same scope.")


def _str_names(scope) -> set:
    names = set()
    for n in scope_walk(scope):
        if isinstance(n, ast.Assign) and is_str_like(n.value):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and n.value is not None and is_str_like(n.value) \
                and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def run(ctx):
    out, cache = [], {}
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and ctx.in_loop(node)):
            continue
        target = node.target
        known_str = False
        if isinstance(target, ast.Name):
            scope = ctx.enclosing_scope(node)
            if id(scope) not in cache:
                cache[id(scope)] = _str_names(scope)
            known_str = target.id in cache[id(scope)]
        if is_str_like(node.value) or known_str:
            name = target.id if isinstance(target, ast.Name) else ast.unparse(target)
            out.append(ctx.hit(
                node, f"{ctx.qualname(node)}:{name}",
                f"String '{name}' is grown with += inside a loop, copying it on every iteration.",
                "medium"))
    return out
