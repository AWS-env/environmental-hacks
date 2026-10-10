"""Registry of CI checks that read normalized run history. Adding a check = one module + one line here."""
from owner_c.ci.history_checks import ci_01, ci_02, ci_03

HISTORY_CHECKS = {c.KEY: c for c in (ci_01, ci_02, ci_03,)}
