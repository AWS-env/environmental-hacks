"""Registry of enabled static checks. Adding a check = one module + one line here."""
from owner_c.checks import code_rt_02, code_rt_06, js_06, js_09, py_02, py_03, py_04, py_07, py_08, py_09, py_10

STATIC_CHECKS = {c.KEY: c for c in (py_09, py_04, py_02, py_03, py_08, py_07, py_10, code_rt_02, code_rt_06, js_09, js_06,)}
