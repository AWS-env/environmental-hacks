"""Registry of enabled static checks. Adding a check = one module + one line here."""
from owner_c.checks import py_02, py_03, py_04, py_07, py_08, py_09

STATIC_CHECKS = {c.KEY: c for c in (py_09, py_04, py_02, py_03, py_08, py_07,)}
