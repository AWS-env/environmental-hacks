"""CI is part of the shared owner-C system (decision D15): static scan, upload handshake, parser, scanner.

Everything here goes through the SHARED entry points (`owner_c.aws.handler`, `profile_handler`, `presign_handler`,
the normalizer registry and the unified scanner adapter) with fake AWS clients; there are no CI-specific Lambdas.
"""
import io
import json
import unittest
import zipfile
from unittest import mock

from helpers import SHA
from helpers_ci import FIXTURES
from owner_c.aws import common, handler, presign_handler, profile_handler
from owner_c.ci import collector
from owner_c.ci.checks import STATIC_CHECKS as CI_STATIC
from owner_c.ci.history_checks import HISTORY_CHECKS as CI_HISTORY
from owner_c.ci.normalize.github_actions import BUNDLE_SCHEMA
from owner_c.normalize import NORMALIZERS, normalize_all
from owner_c.normalize import ci_history
from shared.contracts.validation import validate
from test_aws import AwsTestCase

NO_CANCEL = "name: CI\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n"
BAD_PY = "def f(a=[]):\n    pass\n"
LAB_WF = "name: Lab\non:\n  workflow_dispatch:\njobs:\n  ok:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
LAB = json.loads((FIXTURES / "real_ci" / "lab__lab.yml.json").read_text(encoding="utf-8"))
BUNDLE = {"schema": BUNDLE_SCHEMA, "repository": "o/r", "workflows": [LAB]}
HISTORY_ALL = {"CI-01", "CI-02", "CI-03", "CI-05", "CI-12", "CI-18"}


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr("r-abc/" + path, text)
    return buf.getvalue()


class StaticScanRunsCi(AwsTestCase):
    def event(self, **extra):
        files = [{"path": "app.py", "content": BAD_PY}, {"path": ".github/workflows/ci.yml", "content": NO_CANCEL}]
        return {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": files}, **extra}

    def test_one_scan_publishes_python_and_ci_results_from_one_source(self):
        out = handler.lambda_handler(self.event())
        details = [json.loads(e["Detail"]) for e in self.events.entries]
        for entry in self.events.entries:
            self.assertEqual((entry["Source"], entry["DetailType"]), ("owner-c.detectors", "detector.result.v1"))
        for result in details:
            validate(result)
        by_check = {d["check_id"]: d for d in details}
        self.assertEqual(len(by_check["PY-09"]["findings"]), 1)
        self.assertEqual(len(by_check["CI-11"]["findings"]), 1)  # pull_request workflow with no concurrency
        self.assertTrue(CI_STATIC.keys() <= by_check.keys(), "every static CI check reports for a repo with a workflow")
        self.assertEqual({r["scan_id"] for r in out["results"]} if False else {out["scan_id"]}, {details[0]["scan_id"]})

    def test_a_repository_without_workflows_gets_no_ci_results(self):
        out = handler.lambda_handler(self.event(source={"files": [{"path": "app.py", "content": BAD_PY}]}, dry_run=True))
        self.assertFalse([r for r in out["results"] if r["check_id"].startswith("CI-")])

    def test_ci_settings_can_be_overridden_per_scan(self):
        if "CI-19" not in CI_STATIC:
            self.skipTest("needs the CI-19 check")
        wf = ("name: CI\non:\n  push:\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
              "      - uses: actions/upload-artifact@v4\n        with:\n          name: d\n          path: d\n          retention-days: 20\n")
        files = {"files": [{"path": ".github/workflows/ci.yml", "content": wf}]}
        default = handler.lambda_handler(self.event(source=files, dry_run=True))
        strict = handler.lambda_handler(self.event(source=files, dry_run=True, settings={"max_retention_days": 10}))
        self.assertEqual({r["check_id"]: r for r in default["results"]}["CI-19"]["findings"], 0)
        self.assertEqual({r["check_id"]: r for r in strict["results"]}["CI-19"]["findings"], 1)


class UploadHandshakeCarriesCiHistory(AwsTestCase):
    def test_the_registry_makes_ci_history_uploadable_without_changing_presign(self):
        self.assertIn("ci_history", NORMALIZERS)
        self.assertIn("ci_history", presign_handler.UPLOADABLE)
        signed = []

        class FakeS3:
            def generate_presigned_url(s3, op, Params, ExpiresIn, HttpMethod):
                signed.append(Params["Key"])
                return "https://example.invalid/" + Params["Key"]

        common._clients["s3:regional"] = FakeS3()
        with mock.patch.dict("os.environ", {"ARTIFACT_BUCKET": "owner-c-artifacts"}):
            out = presign_handler.lambda_handler({"repository_id": "github:o/r", "commit_sha": SHA, "artifacts": ["ci_history"]})
        self.assertEqual(sorted(out["urls"]), ["ci_history", "manifest", "repo"])
        self.assertTrue(out["urls"]["ci_history"]["key"].endswith("/ci_history.json"))

    @unittest.skipUnless(HISTORY_ALL <= set(CI_HISTORY), "needs every history check registered")
    def test_manifest_upload_runs_the_ci_history_checks_and_nothing_else(self):
        prefix = f"uploads/github%3Ao%2Fr/{SHA}/"
        objects = {prefix + "repo.zip": _zip({".github/workflows/lab.yml": LAB_WF, "app.py": BAD_PY}),
                   prefix + "ci_history.json": json.dumps(BUNDLE).encode(),
                   prefix + "manifest.json": json.dumps({"artifacts": ["ci_history"]}).encode()}

        class FakeS3:
            def get_object(s3, Bucket, Key):
                return {"Body": io.BytesIO(objects[Key]), "ContentLength": len(objects[Key])}

        common._clients["s3"] = FakeS3()
        s3_event = {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"},
                                 "object": {"key": (prefix + "manifest.json").replace("%", "%25")}}}]}
        out = profile_handler.lambda_handler(s3_event)
        by_check = {r["check_id"]: r for r in out["results"]}
        self.assertEqual(set(by_check), HISTORY_ALL, "no Python/JS profile check runs for a history-only upload")
        self.assertEqual([by_check[c]["findings"] for c in ("CI-01", "CI-02", "CI-03", "CI-05")], [1, 1, 2, 1])
        # the lab has no scheduled runs, so CI-12 has too few runs to judge (unavailable, never clean); CI-18 has history
        self.assertEqual((by_check["CI-12"]["status"], by_check["CI-18"]["status"]), ("unavailable", "completed"))
        for entry in self.events.entries:
            validate(json.loads(entry["Detail"]))
            self.assertEqual(entry["Source"], "owner-c.detectors")

    def test_a_bundle_collected_for_another_repository_is_rejected(self):
        event = {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": []},
                 "artifacts": {"ci_history": {"json": dict(BUNDLE, repository="someone/else")}}}
        with self.assertRaisesRegex(ValueError, "different repository"):
            profile_handler.lambda_handler(event)
        event["artifacts"]["ci_history"]["json"] = dict(BUNDLE, repository="o/r")
        profile_handler.lambda_handler(dict(event, dry_run=True))  # the matching repository is accepted
        event["artifacts"]["ci_history"]["json"] = dict(LAB, repository="someone/else")  # a single document is checked too
        with self.assertRaisesRegex(ValueError, "different repository"):
            profile_handler.lambda_handler(event)
        event["artifacts"]["ci_history"]["json"] = {k: v for k, v in dict(BUNDLE).items() if k != "repository"}  # a bundle must name it
        with self.assertRaisesRegex(ValueError, "different repository"):
            profile_handler.lambda_handler(event)

    def test_a_malformed_history_bundle_fails_loudly(self):
        event = {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": []},
                 "artifacts": {"ci_history": {"json": {"schema": BUNDLE_SCHEMA, "workflows": "not a list"}}}}
        with self.assertRaises(ValueError):
            profile_handler.lambda_handler(event)


class CiHistoryNormalizer(unittest.TestCase):
    def test_a_bundle_and_a_single_document_both_normalize_by_workflow_path(self):
        for raw in (BUNDLE, LAB):
            out = normalize_all({"ci_history": raw}, [])
            self.assertEqual(list(out), ["github-actions"])
            self.assertEqual(list(out["github-actions"]), [".github/workflows/lab.yml"])
            self.assertEqual(out["github-actions"][".github/workflows/lab.yml"]["runs_total"], 9)

    def test_other_documents_and_oversized_bundles_are_rejected(self):
        for bad in ({"not": "history"}, {"schema": BUNDLE_SCHEMA, "workflows": [{"schema": "other"}]},
                    {"schema": BUNDLE_SCHEMA, "workflows": [LAB] * 201}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                ci_history.normalize(bad)

    def test_the_collector_bundle_is_what_the_normalizer_accepts(self):
        self.assertEqual(collector.BUNDLE_SCHEMA, BUNDLE_SCHEMA)

        def fetch(url):
            if "/runs?" in url:
                return {"workflow_runs": [{"id": 1, "run_attempt": 1, "event": "push", "head_sha": "a" * 40, "conclusion": "success"}]}
            return {"jobs": []}

        bundle = collector.collect_bundle(fetch, "o/r", ["ci.yml", "release.yml"])
        self.assertEqual([w["workflow_path"] for w in bundle["workflows"]],
                         [".github/workflows/ci.yml", ".github/workflows/release.yml"])
        out = ci_history.normalize(bundle)
        self.assertEqual(sorted(out), [".github/workflows/ci.yml", ".github/workflows/release.yml"])


class UnifiedScannerAdapter(unittest.TestCase):
    def _run(self, files):
        from scanner.adapters.owner_c import OwnerC
        from scanner.core import ScanContext

        ctx = ScanContext(repository_id="github:o/r", commit_sha=SHA, scan_id="s1", files=files, collection={})
        return {run.check_id: run for run in OwnerC().run(ctx)}

    def test_workflow_checks_run_and_history_checks_are_unavailable_not_clean(self):
        runs = self._run([("app.py", BAD_PY), (".github/workflows/ci.yml", NO_CANCEL)])
        self.assertEqual(runs["CI-11"].result["status"], "completed")
        self.assertEqual(len(runs["CI-11"].result["findings"]), 1)
        for check_id in CI_HISTORY:
            self.assertIsNotNone(runs[check_id].unavailable, check_id)
            self.assertIsNone(runs[check_id].result)

    def test_a_repository_without_workflows_marks_the_static_ci_checks_not_applicable(self):
        runs = self._run([("app.py", BAD_PY)])
        for check_id in CI_STATIC:
            self.assertIsNotNone(runs[check_id].not_applicable, check_id)


if __name__ == "__main__":
    unittest.main()
