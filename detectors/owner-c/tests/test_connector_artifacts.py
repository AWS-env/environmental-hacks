import unittest

from helpers import REPO, SHA
from owner_c.connector import build_inputs


class ArtifactInputTests(unittest.TestCase):
    def build(self, **kw):
        return build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", files=[("a.py", "x = 1\n")], **kw)

    def test_artifact_checks_carry_their_thresholds(self):
        payloads = {p["check_id"]: p for p in self.build()}
        self.assertEqual(payloads["PY-01"]["context"]["min_time_share"], 0.05)
        self.assertNotIn("min_time_share", payloads["PY-09"]["context"])
        if "PY-05" in payloads:
            self.assertEqual(payloads["PY-05"]["context"]["min_alloc_bytes"], 10 * 1024 * 1024)

    def test_artifacts_only_attach_to_files_in_scope_and_to_their_profiler(self):
        artifacts = {"py-spy": {"a.py": {"profiler": "py-spy"}, "gone.py": {"profiler": "py-spy"}},
                     "memray": {"a.py": {"profiler": "memray"}}}
        payloads = {p["check_id"]: p for p in self.build(artifacts=artifacts)}
        kinds = lambda key: sorted(s["source_id"] for s in payloads[key]["sources"] if s["kind"] == "artifact")
        self.assertEqual(kinds("PY-01"), ["py-spy:a.py"])
        self.assertEqual(kinds("PY-09"), [])
        if "PY-05" in payloads:
            self.assertEqual(kinds("PY-05"), ["memray:a.py"])


if __name__ == "__main__":
    unittest.main()
