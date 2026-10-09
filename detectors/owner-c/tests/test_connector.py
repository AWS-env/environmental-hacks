import unittest

from helpers import REPO, SHA
from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.checks import STATIC_CHECKS
from owner_c.common import MAX_FILE_BYTES
from owner_c.langs import accepts
from owner_c.connector import build_inputs, select_files


class SelectFilesTests(unittest.TestCase):
    FILES = [
        ("app.py", "x = 1\n"), ("README.md", "# hi"), ("tests/test_app.py", "x = 1\n"), ("conftest.py", ""),
        ("node_modules/pkg/x.py", "x = 1\n"), ("venv/lib/y.py", "x = 1\n"), ("big.py", "#" * (MAX_FILE_BYTES + 1)),
        ("win\\path.py", "x = 1\n"),
    ]

    def test_default_selection(self):
        # extension filtering is per check (see test_each_check_only_receives_files_it_accepts)
        self.assertEqual([p for p, _ in select_files(self.FILES)], ["app.py", "README.md", "win/path.py"])

    def test_each_check_only_receives_files_it_accepts(self):
        payload = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", checks=["PY-09"],
                               files=[("app.py", "x = 1\n"), ("README.md", "# hi"), ("web/app.js", "let a;")])[0]
        self.assertEqual(payload["scope"], ["file:app.py"])

    def test_include_tests(self):
        paths = [p for p, _ in select_files(self.FILES, include_tests=True)]
        self.assertIn("tests/test_app.py", paths)
        self.assertIn("conftest.py", paths)
        self.assertNotIn("node_modules/pkg/x.py", paths)


class BuildInputsTests(unittest.TestCase):
    def build(self, **kw):
        return build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", files=[("a.py", "x = 1\n")], **kw)

    def test_one_payload_per_enabled_check(self):
        keys = [p["check_id"] for p in self.build()]
        expected = [k for k, m in list(STATIC_CHECKS.items()) + list(ARTIFACT_CHECKS.items()) if accepts(m, "a.py")]
        self.assertEqual(keys, expected)  # checks for other languages get no payload without their files

    def test_exclusions_are_declared_in_context(self):
        ctx = self.build()[0]["context"]
        self.assertTrue(ctx["exclude_tests"])
        self.assertEqual(ctx["max_file_bytes"], MAX_FILE_BYTES)
        self.assertIn("node_modules", ctx["excluded_dirs"])

    def test_no_files_means_no_payloads(self):
        self.assertEqual(build_inputs(repository_id=REPO, commit_sha=SHA, scan_id="s", files=[("README.md", "x")]), [])


if __name__ == "__main__":
    unittest.main()
