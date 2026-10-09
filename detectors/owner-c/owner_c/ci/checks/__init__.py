"""Registry of static CI checks (workflow YAML only). Adding a check = one module + one line here."""
from owner_c.ci.checks import ci_06, ci_11

STATIC_CHECKS = {c.KEY: c for c in (ci_11, ci_06,)}
