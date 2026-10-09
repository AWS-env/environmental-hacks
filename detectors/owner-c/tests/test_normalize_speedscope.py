"""Real artifact: a py-spy capture recorded from fixtures/real/hot.py."""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.normalize import normalize_all

REAL = FIXTURES / "real"
HOT = (REAL / "hot.py").read_text()
SPEED = json.loads((REAL / "hot.speedscope.json").read_text())


class SpeedscopeTests(unittest.TestCase):
    def test_normalizes_line_time_shares(self):
        data = normalize_all({"speedscope": SPEED}, [("hot.py", HOT)])["py-spy"]["hot.py"]
        self.assertEqual(data["profiler"], "py-spy")
        self.assertGreater(data["sampled_seconds"], 3)
        self.assertGreater(data["time_share_line_5"], 0.5)  # `if p in items` dominates

    def test_paths_must_match_on_a_path_boundary(self):
        out = normalize_all({"speedscope": SPEED}, [("other/hot.py", HOT), ("hot.py", HOT), ("shot.py", HOT)])
        self.assertEqual(sorted(out["py-spy"]), ["hot.py"])

    def test_end_to_end_py01(self):
        artifacts = normalize_all({"speedscope": SPEED}, [("hot.py", HOT)])
        _payload, result = run_check("PY-01", {"hot.py": HOT}, artifacts=artifacts)
        finding = result["findings"][0]
        self.assertEqual((result["status"], finding["identity"], finding["confidence"]),
                         ("completed", "slow_membership:in:items", "high"))
        self.assertEqual([e["kind"] for e in finding["evidence"]], ["static", "artifact"])
        self.assertEqual(finding["evidence"][0]["line_start"], 5)
        self.assertEqual(finding["evidence"][1]["field"], "time_share_line_5")


class NormalizeAllTests(unittest.TestCase):
    def test_unknown_artifact_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown artifact"):
            normalize_all({"perf_data": {}}, [("a.py", "")])

    def test_empty_artifact_is_ignored(self):
        self.assertEqual(normalize_all({"speedscope": {}}, [("a.py", "")]), {})


if __name__ == "__main__":
    unittest.main()
