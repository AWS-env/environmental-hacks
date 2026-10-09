"""Registry of enabled static checks. Adding a check = one module + one line here."""
from owner_c.checks import py_09

STATIC_CHECKS = {c.KEY: c for c in (py_09,)}
