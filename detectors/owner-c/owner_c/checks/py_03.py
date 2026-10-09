"""PY-03: pandas iterrows() / row-wise apply(axis=1)."""
import ast

KEY = "PY-03"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
REFS = ["https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.iterrows.html"]
RECOMMENDATION = "Use vectorized column operations, or itertuples() when a Python loop is unavoidable."
LIMITATION = ("Static pattern only: frame size is unknown, so small frames may be fine. iterrows() is matched by "
              "name; apply(axis=1) is matched only in files that import pandas.")


def run(ctx):
    out = []
    imports_pandas = any(v == "pandas" or v.startswith("pandas.") for v in ctx.aliases.values())
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        receiver = ast.unparse(node.func.value)
        if attr == "iterrows" and not node.args:
            out.append(ctx.hit(
                node, f"{ctx.qualname(node)}:{receiver}.iterrows",
                "iterrows() builds a Series per row and loses dtypes.", "medium"))
        elif attr == "apply" and imports_pandas:
            for kw in node.keywords:
                if kw.arg == "axis" and isinstance(kw.value, ast.Constant) and kw.value.value in (1, "columns"):
                    out.append(ctx.hit(
                        node, f"{ctx.qualname(node)}:{receiver}.apply(axis=1)",
                        "DataFrame.apply(axis=1) runs a Python function for every row.", "medium"))
    return out
