"""Scan runner behaviour: bounded collection, deterministic reports, and failures never read as clean."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scanner import source
from scanner.adapters.owner_a import OwnerA
from scanner.adapters.owner_c import OwnerC
from scanner.adapters.owner_d import OwnerD
from scanner.core import CheckRun, evaluate_each, fingerprint_of, static_source
from scanner.report import build_report

SERVICE = """def add_item(item, bucket=[]):
    bucket.append(item)
    return bucket


def render(rows):
    out = ""
    for row in rows:
        out += str(row)
    return out
"""
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def write_repo(root: Path):
    (root / "app").mkdir()
    (root / "app/service.py").write_text(SERVICE)
    (root / "tests").mkdir()
    (root / "tests/test_service.py").write_text("def test_x(bucket=[]):\n    assert True\n")
    (root / "node_modules/pkg").mkdir(parents=True)
    (root / "node_modules/pkg/vendored.py").write_text("def f(a=[]):\n    pass\n")
    (root / "blob.bin").write_bytes(b"\x00\x01binary")
    (root / "big.py").write_text("x = 1\n" * 1000)
    (root / "README.md").write_text("# fixture\n")


class FakeAdapter:
    """Stands in for an owner adapter: `make(ctx)` returns its CheckRuns."""

    owner, name = "Z", "fake"

    def __init__(self, make):
        self.make = make

    def run(self, ctx):
        return self.make(ctx)


def service_payload(ctx):
    content = SERVICE
    return ctx.input("PY-09", "1.0.0", ctx.context(), [static_source("app/service.py", content)])


def result_for(payload, evidence_line, value):
    finding = {
        "fingerprint": fingerprint_of(payload["repository_id"], "PY-09", "file:app/service.py", "add_item(bucket)"),
        "scope_id": "file:app/service.py", "identity": "add_item(bucket)", "summary": "s", "confidence": "high",
        "recommendation": "r", "references": ["https://example.org/ref"],
        "evidence": [{"source_id": "src:app/service.py", "kind": "static", "locator": "app/service.py",
                      "line_start": evidence_line, "value": value}],
    }
    result = {k: payload[k] for k in ("schema_version", "repository_id", "scan_id", "commit_sha", "check_id",
                                      "detector_version", "context", "scope")}
    result.update(kind="result", status="completed", findings=[finding], measurements=[],
                  coverage={"evaluated_scope": ["file:app/service.py"], "limitations": []})
    return result


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_repo(self.root)
        self.limits = source.Limits(max_file_bytes=2000)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, adapters):
        with source.resolve(str(self.root)) as target:
            return build_report(target, source.collect(target.root, self.limits), adapters, scan_id="s1", now=NOW)

    def check(self, report, check_id):
        return next(c for c in report["checks"] if c["check_id"] == check_id)

    def test_fixture_repo_gives_bounded_deterministic_evidence_backed_report(self):
        report = self.scan([OwnerC(), OwnerD()])
        files = report["files"]
        self.assertEqual(files["collected"], 3)  # app/service.py, tests/test_service.py, README.md
        self.assertEqual(files["skipped"], {"binary": 1, "excluded_dir": 1, "too_large": 1})
        self.assertEqual(report["repository"]["commit_source"], "content-hash")
        self.assertRegex(report["repository"]["commit_sha"], r"^[0-9a-f]{40}$")

        py09 = [f for f in report["findings"] if f["check_id"] == "PY-09"]
        self.assertEqual([(f["file"], f["line"]) for f in py09], [("app/service.py", 1)])  # tests/ excluded
        self.assertEqual(py09[0]["evidence"][0]["value"], "def add_item(item, bucket=[]):")
        self.assertIn("app/service.py:1", py09[0]["agent_prompt"])
        self.assertIn("def add_item(item, bucket=[]):", py09[0]["agent_prompt"])
        self.assertEqual([(f["file"], f["line"]) for f in report["findings"] if f["check_id"] == "PY-04"],
                         [("app/service.py", 9)])
        self.assertEqual(self.check(report, "PY-09")["status"], "completed")

        # Runtime-evidence checks stay unavailable: owner C's artifact checks (from the detector)
        # and owner D's telemetry check (from the scanner, without calling it).
        self.assertEqual((self.check(report, "PY-01")["status"], self.check(report, "PY-01")["status_source"]),
                         ("unavailable", "detector"))
        self.assertEqual((self.check(report, "INF-01")["status"], self.check(report, "INF-01")["status_source"]),
                         ("unavailable", "scanner"))
        # Count-free on purpose: new checks keep landing. Runtime-only checks must be unavailable,
        # each with a stated reason, and the summary must agree with the per-check rows.
        unavailable = [c for c in report["checks"] if c["status"] == "unavailable"]
        self.assertLessEqual({"PY-01", "PY-05", "PY-11", "INF-01"}, {c["check_id"] for c in unavailable})
        self.assertTrue(all(c["reason"] or c["limitations"] for c in unavailable))
        self.assertEqual(report["summary"]["checks_by_status"]["unavailable"], len(unavailable))
        self.assertEqual(report["impact"]["status"], "not_quantified")

        again = self.scan([OwnerC(), OwnerD()])
        report.pop("timings"), again.pop("timings")
        self.assertEqual(report, again)

    def test_detector_crash_is_an_error_not_a_clean_result(self):
        def crash(_payload):
            raise RuntimeError("boom")

        report = self.scan([FakeAdapter(lambda ctx: evaluate_each("Z", "fake", [service_payload(ctx)], crash))])
        entry = self.check(report, "PY-09")
        self.assertEqual(entry["status"], "error")
        self.assertIn("detector raised RuntimeError: boom", entry["reason"])
        self.assertEqual((report["findings"], report["summary"]["checks_by_status"]["completed"]), ([], 0))

    def test_invalid_detector_output_is_rejected(self):
        def invented(ctx):
            payload = service_payload(ctx)
            return [CheckRun("PY-09", "Z", "fake", payload=payload,
                             result=result_for(payload, 3, "def add_item(item, bucket=[]):"))]

        report = self.scan([FakeAdapter(invented)])
        entry = self.check(report, "PY-09")
        self.assertEqual(entry["status"], "error")
        self.assertIn("invalid detector output rejected: Evidence does not match source lines", entry["reason"])
        self.assertEqual(report["findings"], [])

        def valid(ctx):  # the same result citing the real line is accepted
            payload = service_payload(ctx)
            return [CheckRun("PY-09", "Z", "fake", payload=payload,
                             result=result_for(payload, 1, "def add_item(item, bucket=[]):"))]

        self.assertEqual(len(self.scan([FakeAdapter(valid)])["findings"]), 1)

    def test_missing_toolchain_marks_adapter_unavailable(self):
        report = self.scan([OwnerA(entry=self.root / "missing" / "index.js")])
        (adapter,) = report["adapters"]
        self.assertEqual(adapter["status"], "unavailable")
        self.assertIn("owner A is not built", adapter["reason"])
        self.assertEqual(report["summary"]["adapters_unavailable_or_failed"], ["A"])


if __name__ == "__main__":
    unittest.main()
