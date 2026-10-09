"""PY-11 end to end with the real memray capture (fixtures/real/mem.stats.json)."""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.normalize import normalize_all

REAL = FIXTURES / "real"
MEM = (REAL / "mem.py").read_text()
STATS = json.loads((REAL / "mem.stats.json").read_text())


class RealMemrayPy11Tests(unittest.TestCase):
    def test_deepcopy_confirmed_through_copy_internals(self):
        artifacts = normalize_all({"memray_stats": STATS}, [("mem.py", MEM)])
        _payload, result = run_check("PY-11", {"mem.py": MEM}, artifacts=artifacts)
        self.assertEqual([(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
                         [("<module>:copy.deepcopy", 4)])
        self.assertEqual(result["findings"][0]["evidence"][1]["field"], "allocated_bytes_deepcopy_internals")


if __name__ == "__main__":
    unittest.main()
