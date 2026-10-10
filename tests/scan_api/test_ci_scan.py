"""scripts/ci_scan.py (the scan workflow's client) with a scripted fake scan-api: no network."""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_scan", ROOT / "scripts" / "ci_scan.py")
ci_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scan)

API = "https://api.example.test"
REPO = "https://github.com/AWS-env/environmental-hacks"
SCAN_ID = "11111111-2222-3333-4444-555555555555"
REPORT = {
    "repository": {"commit_sha": "a" * 40},
    "summary": {"checks_total": 2, "checks_by_status": {"completed": 1, "unavailable": 1, "error": 0},
                "findings_total": 3, "findings_by_confidence": {"high": 2, "medium": 1, "low": 0},
                "adapters_unavailable_or_failed": ["A"]},
    "checks": [{"owner": "C", "check_id": "CODE-C6.7", "status": "completed", "finding_count": 3},
               {"owner": "A", "check_id": "CODE-C1|2", "status": "unavailable", "finding_count": 0}],
}


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


class CiScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.report, self.summary, self.clock = self.tmp / "report.json", self.tmp / "summary.md", FakeClock()

    def run_scan(self, responses, api_url=API, repo_url=REPO, **kwargs):
        """Feed `responses` (status, body) or exceptions in order to request_json; return (code, stdout, calls)."""
        calls, queue = [], list(responses)

        def fake_request(method, url, body=None):
            calls.append((method, url, body))
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        out = io.StringIO()
        with mock.patch.object(ci_scan, "request_json", fake_request), redirect_stdout(out):
            code = ci_scan.run(api_url, repo_url, ci_scan.DEFAULT_HUB_URL, self.report, self.summary,
                               timeout=kwargs.get("timeout", 60), interval=10,
                               expected_commit=kwargs.get("expected_commit"),
                               sleep=self.clock.sleep, clock=self.clock)
        self.assertEqual(queue, [], "unused fake responses")
        return code, out.getvalue(), calls

    def test_no_api_url_skips_with_notice(self):
        code, out, calls = self.run_scan([], api_url="")
        self.assertEqual((code, calls), (0, []))
        self.assertIn("::notice title=scan-api::SCAN_API_URL is not set", out)
        self.assertFalse(self.report.exists())
        self.assertFalse(self.summary.exists())

    def test_done_writes_report_and_summary(self):
        code, out, calls = self.run_scan([
            (202, {"scan_id": SCAN_ID, "status": "queued"}),
            (200, {"status": "queued"}),
            (503, {"error": "Service Unavailable"}),
            (200, {"status": "running"}),
            (200, {"status": "done", "report": REPORT}),
        ], repo_url=REPO + ".git/")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ("POST", f"{API}/scans", {"repo_url": REPO}))
        self.assertEqual(calls[1][:2], ("GET", f"{API}/scans/{SCAN_ID}"))
        self.assertEqual(json.loads(self.report.read_text()), REPORT)
        summary = self.summary.read_text()
        self.assertIn("**Status:** done", summary)
        self.assertIn(f"{ci_scan.DEFAULT_HUB_URL}/repos/AWS-env/environmental-hacks/scans/{SCAN_ID}", summary)
        self.assertIn("| C | CODE-C6.7 | completed | 3 |", summary)
        self.assertIn("| A | CODE-C1\\|2 | unavailable | 0 |", summary)
        self.assertIn("Findings: 3 (high 2, medium 1, low 0)", summary)
        self.assertIn("Owners unavailable or failed: A", summary)
        self.assertNotIn("::warning", out)

    def test_post_retries_429_and_network_errors_with_backoff(self):
        code, _, calls = self.run_scan([
            (429, {"message": "Too Many Requests"}),
            URLError("connection reset"),
            (202, {"scan_id": SCAN_ID}),
            (200, {"status": "done", "report": REPORT}),
        ])
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], ["POST", "POST", "POST", "GET"])
        self.assertEqual(self.clock.sleeps[:2], [2, 4])

    def test_post_gives_up_with_warning_not_failure(self):
        code, out, calls = self.run_scan([(429, {})] * ci_scan.POST_ATTEMPTS)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), ci_scan.POST_ATTEMPTS)
        self.assertIn("::warning title=scan-api::could not start a scan", out)
        self.assertIn("**Status:** error", self.summary.read_text())
        self.assertFalse(self.report.exists())

    def test_reused_scan_200_is_polled_and_reported(self):
        code, out, calls = self.run_scan([
            (200, {"scan_id": SCAN_ID, "status": "running", "reused": True}),
            (200, {"status": "running"}),
            (200, {"status": "done", "report": REPORT}),
        ])
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], ["POST", "GET", "GET"])
        self.assertIn(f"scan {SCAN_ID} reused for {REPO}", out)
        summary = self.summary.read_text()
        self.assertIn("**Status:** done", summary)
        self.assertIn(f"**Scan ID:** `{SCAN_ID}` (reused:", summary)
        self.assertEqual(json.loads(self.report.read_text()), REPORT)

    def test_new_scan_is_not_marked_reused(self):
        self.run_scan([(202, {"scan_id": SCAN_ID}), (200, {"status": "done", "report": REPORT})])
        self.assertNotIn("reused", self.summary.read_text())

    def test_daily_cap_429_is_skipped_without_retry(self):
        code, out, calls = self.run_scan([
            (429, {"error": "daily scan limit reached; try again after 00:00 UTC"})])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertIn("::warning title=scan-api::scan skipped: daily scan limit reached", out)
        summary = self.summary.read_text()
        self.assertIn("**Status:** skipped", summary)
        self.assertIn("try again after 00:00 UTC", summary)
        self.assertFalse(self.report.exists())

    def test_throttling_429_is_retried_then_daily_cap_stops(self):
        code, _, calls = self.run_scan([
            (429, {"message": "Too Many Requests"}),
            (429, {"error": "daily scan limit reached; try again after 00:00 UTC"}),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.clock.sleeps, [2])
        self.assertIn("**Status:** skipped", self.summary.read_text())

    def test_report_for_another_commit_is_noted(self):
        pushed = "b" * 40
        self.run_scan([(200, {"scan_id": SCAN_ID, "status": "done", "reused": True}),
                       (200, {"status": "done", "report": REPORT})], expected_commit=pushed)
        self.assertIn(f"the report is for commit `{'a' * 40}`, not the pushed commit `{pushed}`",
                      self.summary.read_text())

    def test_report_for_the_pushed_commit_has_no_note(self):
        self.run_scan([(202, {"scan_id": SCAN_ID}), (200, {"status": "done", "report": REPORT})],
                      expected_commit="a" * 40)
        self.assertNotIn("not the pushed commit", self.summary.read_text())

    def test_main_compares_github_sha_only_for_this_repository(self):
        env = {"GITHUB_REPOSITORY": "AWS-env/environmental-hacks", "GITHUB_SHA": "c" * 40}
        with mock.patch.dict(ci_scan.os.environ, env), mock.patch.object(ci_scan, "run", return_value=0) as fake_run:
            ci_scan.main(["--api-url", API, "--repo-url", "https://github.com/aws-env/Environmental-Hacks"])
            ci_scan.main(["--api-url", API, "--repo-url", "https://github.com/pallets/itsdangerous"])
        self.assertEqual([c.kwargs["expected_commit"] for c in fake_run.call_args_list], ["c" * 40, None])

    def test_post_client_error_is_a_config_error(self):
        with self.assertRaises(ci_scan.ConfigError):
            self.run_scan([(400, {"error": "body must be JSON"})])

    def test_invalid_repo_url_is_a_config_error(self):
        with self.assertRaises(ci_scan.ConfigError):
            self.run_scan([], repo_url="https://gitlab.com/a/b")

    def test_scan_error_is_a_warning(self):
        code, out, _ = self.run_scan([(202, {"scan_id": SCAN_ID}),
                                      (200, {"status": "error", "error": "archive too large\nover 50 MB"})])
        self.assertEqual(code, 0)
        self.assertIn("::warning title=scan-api::scan " + SCAN_ID + " failed: archive too large%0Aover 50 MB", out)
        summary = self.summary.read_text()
        self.assertIn("**Status:** error", summary)
        self.assertIn(f"/repos/AWS-env/environmental-hacks/scans/{SCAN_ID}", summary)
        self.assertFalse(self.report.exists())

    def test_poll_deadline_is_a_warning(self):
        code, out, calls = self.run_scan([(202, {"scan_id": SCAN_ID})] + [(200, {"status": "running"})] * 7,
                                         timeout=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 8)  # 1 POST + polls at t=0..60 s
        self.assertIn("did not finish within 60 s (last status: running)", out)

    def test_large_report_is_fetched_from_report_url(self):
        body = io.BytesIO(json.dumps(REPORT).encode())
        with mock.patch.object(ci_scan, "urlopen", return_value=body) as fake_urlopen:
            code, _, _ = self.run_scan([(202, {"scan_id": SCAN_ID}),
                                        (200, {"status": "done", "report": None,
                                               "report_url": "https://bucket.s3.test/report.json?X-Amz-Signature=x"})])
        self.assertEqual(code, 0)
        self.assertEqual(fake_urlopen.call_args.args[0].full_url, "https://bucket.s3.test/report.json?X-Amz-Signature=x")
        self.assertEqual(json.loads(self.report.read_text()), REPORT)

    def test_main_returns_1_on_config_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ci_scan.main(["--api-url", API, "--repo-url", "not-a-url", "--report", str(self.report)])
        self.assertEqual(code, 1)
        self.assertIn("::error title=scan-api::repo_url must be", out.getvalue())


if __name__ == "__main__":
    unittest.main()
