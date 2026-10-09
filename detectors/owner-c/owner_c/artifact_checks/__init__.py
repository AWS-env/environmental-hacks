"""Registry of enabled artifact-confirmed checks. Adding a check = one module + one line here."""
from owner_c.artifact_checks import (fe_11, js_01, js_02, js_03, js_04, js_05, js_07, js_08, py_01, py_05, py_11)

ARTIFACT_CHECKS = {c.KEY: c for c in (py_01, py_05, py_11, js_01, js_02, js_03, js_04, js_05, js_07, js_08, fe_11,)}
