"""Real artifact: a memray stats capture recorded from fixtures/real/mem.py (in a Linux container)."""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.normalize import normalize_all

REAL = FIXTURES / "real"
MEM = (REAL / "mem.py").read_text()
STATS = json.loads((REAL / "mem.stats.json").read_text())


class MemrayTests(unittest.TestCase):
    def test_normalizes_allocation_sites(self):
        data = normalize_all({"memray_stats": STATS}, [("mem.py", MEM)])["memray"]["mem.py"]
        self.assertEqual(data["allocated_bytes_line_5"], 592115200)
        self.assertEqual(data["allocated_bytes_line_2"], 13693152)
        self.assertEqual(data["allocated_bytes_deepcopy_internals"], 526611840)

    def test_deepcopy_internals_only_for_files_that_use_deepcopy(self):
        out = normalize_all({"memray_stats": STATS}, [("mem.py", MEM), ("plain.py", "x = 1\n")])["memray"]
        self.assertNotIn("plain.py", out)

    def test_end_to_end_py05(self):
        artifacts = normalize_all({"memray_stats": STATS}, [("mem.py", MEM)])
        _payload, result = run_check("PY-05", {"mem.py": MEM}, artifacts=artifacts)
        self.assertEqual([(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
                         [("<module>:sum(listcomp)", 5)])
        self.assertEqual(result["findings"][0]["evidence"][1]["field"], "allocated_bytes_line_5")


if __name__ == "__main__":
    unittest.main()
