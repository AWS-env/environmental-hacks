import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from helpers import REPO, SHA
from owner_c import cli
from owner_c.connector import build_inputs


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_scan_directory_prints_every_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "app.py").write_text("def f(a=[]):\n    pass\n")
            code, out, _err = run_cli("scan", tmp, "--commit", SHA, "--repository-id", REPO)
        self.assertEqual(code, 0)
        self.assertIn("PY-09: completed (1/1 files, 1 findings)", out)
        self.assertIn("app.py:1 [high] f(a)", out)

    def test_scan_json_output_is_valid_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "app.py").write_text("def f(a=[]):\n    pass\n")
            code, out, _err = run_cli("scan", tmp, "--commit", SHA, "--json")
        self.assertEqual(code, 0)
        results = {r["check_id"]: r for r in json.loads(out)}
        self.assertEqual(len(results["PY-09"]["findings"]), 1)

    def test_evaluate_validates_and_writes_result(self):
        payload = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", checks=["PY-09"],
                               files=[("a.py", "def f(a=[]):\n    pass\n")])[0]
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = pathlib.Path(tmp, "in.json"), pathlib.Path(tmp, "out.json")
            src.write_text(json.dumps(payload))
            code, _out, _err = run_cli("evaluate", str(src), "-o", str(dst))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(dst.read_text())["status"], "completed")
            src.write_text(json.dumps({**payload, "check_id": "NOPE-1"}))
            code, _out, err = run_cli("evaluate", str(src))
            self.assertEqual(code, 1)
            self.assertIn("cannot evaluate", err)

    def test_evaluate_rejects_unreadable_input(self):
        code, _out, err = run_cli("evaluate", "does-not-exist.json")
        self.assertEqual(code, 2)
        self.assertIn("invalid input file", err)


if __name__ == "__main__":
    unittest.main()
