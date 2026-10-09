import json
import pathlib
import tempfile
import unittest

from helpers import FIXTURES, SHA
from test_cli import run_cli

REAL = FIXTURES / "real"


class CliArtifactTests(unittest.TestCase):
    def test_scan_without_artifacts_marks_artifact_checks_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "app.py").write_text("x = 1\n")
            _code, out, _err = run_cli("scan", tmp, "--commit", SHA)
        self.assertIn("PY-01: unavailable", out)  # no profiler artifact supplied

    def test_scan_with_real_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "hot.py").write_text((REAL / "hot.py").read_text())
            code, out, _err = run_cli("scan", tmp, "--commit", SHA, "--json",
                                      "--artifact", f"speedscope={REAL / 'hot.speedscope.json'}")
        self.assertEqual(code, 0)
        results = {r["check_id"]: r for r in json.loads(out)}
        self.assertEqual(len(results["PY-01"]["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
