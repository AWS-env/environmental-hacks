"""PY-08: open()/connections not managed by a context manager."""
import ast

KEY = "PY-08"
DETECTOR_VERSION = "1.0.0"
NOQA = ("SIM115", "R1732")
REFS = ["https://pylint.readthedocs.io/en/v3.3.9/user_guide/messages/refactor/consider-using-with.html"]
RECOMMENDATION = "Open the resource in a with block so it is closed even when an exception occurs."
LIMITATION = ("Static pattern only: leaks are not observed at runtime. Handles that are closed explicitly, returned, "
              "stored on an object or passed to a constructor/container are treated as managed.")

RESOURCE_CALLS = {
    "open", "io.open", "sqlite3.connect", "psycopg2.connect", "pymysql.connect",
    "mysql.connector.connect",
}
_OWNER_METHODS = {"append", "add", "extend", "setdefault", "enter_context", "callback", "push"}


def _is_owner_callee(func) -> bool:
    """Calls that take ownership of a handle: constructors and container/stack methods."""
    if isinstance(func, ast.Attribute):
        return func.attr in _OWNER_METHODS
    name = getattr(func, "id", "").lstrip("_")
    return bool(name) and name[0].isupper()


def _managed_names(scope) -> set:
    """Names that are closed, used in `with`, or whose ownership leaves the scope."""
    names = set()
    for n in ast.walk(scope):
        if isinstance(n, ast.Call) and _is_owner_callee(n.func):
            for a in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(a, ast.Name):
                    names.add(a.id)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close" \
                and isinstance(n.func.value, ast.Name):
            names.add(n.func.value.id)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if isinstance(item.context_expr, ast.Name):
                    names.add(item.context_expr.id)
        elif isinstance(n, (ast.Return, ast.Yield, ast.YieldFrom)) and n.value is not None:
            # returning the handle itself (or a tuple/list holding it) transfers ownership;
            # `return f.read()` does not
            returned = n.value.elts if isinstance(n.value, (ast.Tuple, ast.List)) else [n.value]
            names.update(x.id for x in returned if isinstance(x, ast.Name))
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) \
                and any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in n.targets):
            names.add(n.value.id)
    return names


def run(ctx):
    out, cache = [], {}
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = ctx.dotted(node.func)
        if dotted not in RESOURCE_CALLS:
            continue
        fn = ctx.enclosing_function(node)
        if getattr(fn, "name", "") == "__enter__":
            continue
        parent = ctx.parent(node)
        if isinstance(parent, (ast.withitem, ast.Return, ast.Yield, ast.YieldFrom)):
            continue
        if isinstance(parent, ast.Call):
            callee = parent.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name in ("enter_context", "closing"):
                continue
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if any(isinstance(t, ast.Attribute) for t in targets):
                continue  # owned by an object, closed elsewhere
            scope = ctx.enclosing_scope(node)
            if id(scope) not in cache:
                cache[id(scope)] = _managed_names(scope)
            if {t.id for t in targets if isinstance(t, ast.Name)} & cache[id(scope)]:
                continue  # closed explicitly, used in a with block, or ownership transferred
        out.append(ctx.hit(
            node, f"{ctx.qualname(node)}:{dotted}",
            f"{dotted}() result is not managed by a context manager, so it can leak if an exception occurs.",
            "high"))
    return out
