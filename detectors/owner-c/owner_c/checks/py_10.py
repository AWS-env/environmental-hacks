"""PY-10: heavy modules (numpy/pandas/torch...) imported but never used."""
import ast
import os
import re

KEY = "PY-10"
DETECTOR_VERSION = "1.0.0"
NOQA = ("F401",)
REFS = ["https://docs.astral.sh/ruff/rules/unused-import/"]
RECOMMENDATION = "Remove the unused import, or import it lazily inside the function that needs it."
LIMITATION = ("Static pattern only: start-up time and memory cost are not measured. Imports under TYPE_CHECKING or "
              "try/except ImportError, __init__.py files and names listed in __all__ or type strings are not flagged.")

HEAVY_MODULES = {
    "numpy", "pandas", "torch", "tensorflow", "scipy", "sklearn", "matplotlib",
    "seaborn", "cv2", "transformers", "jax", "keras", "plotly",
}
# A string that looks like a type expression, e.g. cast("pd.DataFrame", x) or List["np.ndarray"].
_TYPE_STRING = re.compile(r"^[A-Za-z_][\w.\[\], |'\"]*$")


def _skipped_nodes(tree) -> set:
    """Imports under TYPE_CHECKING or try/except ImportError are not unconditional runtime cost."""
    skipped = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test):
            skipped.update(id(x) for b in n.body for x in ast.walk(b))
        elif isinstance(n, ast.Try):
            skipped.update(id(x) for b in n.body for x in ast.walk(b))
    return skipped


def _text_references(tree) -> str:
    """Names referenced only from string annotations, `cast("X")`-style strings or __all__."""
    refs = []
    for n in ast.walk(tree):
        anns = []
        if isinstance(n, ast.arg) and n.annotation is not None:
            anns.append(n.annotation)
        elif isinstance(n, ast.AnnAssign):
            anns.append(n.annotation)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns is not None:
            anns.append(n.returns)
        for a in anns:
            refs += [c.value for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _TYPE_STRING.match(n.value):
            refs.append(n.value)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets):
            refs += [c.value for c in ast.walk(n.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    return " ".join(refs)


def run(ctx):
    if os.path.basename(ctx.path) == "__init__.py":
        return []
    tree = ctx.tree
    skipped = _skipped_nodes(tree)
    own_package = ctx.path.split("/")[0]  # scanning the library itself, not a user of it
    imported = []  # (bound_name, module_root, node)
    for n in ast.walk(tree):
        if id(n) in skipped:
            continue
        if isinstance(n, ast.Import):
            for a in n.names:
                root = a.name.split(".")[0]
                if root in HEAVY_MODULES and root != own_package and a.asname != a.name:
                    imported.append((a.asname or root, root, n))
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            root = n.module.split(".")[0]
            if root in HEAVY_MODULES and root != own_package:
                for a in n.names:
                    if a.name != "*" and a.asname != a.name:
                        imported.append((a.asname or a.name, root, n))
    if not imported:
        return []

    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store)}
    blob = _text_references(tree)
    out = []
    for bound, root, node in imported:
        if bound in used or re.search(rf"\b{re.escape(bound)}\b", blob):
            continue
        out.append(ctx.hit(
            node, f"{ctx.qualname(node)}:import:{bound}",
            f"'{bound}' is imported from heavy module '{root}' but never used.", "high"))
    return out
