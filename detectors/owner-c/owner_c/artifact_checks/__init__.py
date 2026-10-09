"""Registry of enabled artifact-confirmed checks. Adding a check = one module + one line here."""
from owner_c.artifact_checks import py_01, py_05

ARTIFACT_CHECKS = {c.KEY: c for c in (py_01, py_05,)}
