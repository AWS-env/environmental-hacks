import io
import json
import unittest
import zipfile

from helpers import FIXTURES, SHA
from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.aws import common, profile_handler
from owner_c.langs import accepts
from shared.contracts.validation import validate
from test_aws import AwsTestCase

REAL = FIXTURES / "real"
HOT = (REAL / "hot.py").read_text()
SPEED = json.loads((REAL / "hot.speedscope.json").read_text())


class ProfileHandlerTests(AwsTestCase):
    def event(self, **extra):
        return {"repository_id": "github:o/r", "commit_sha": SHA, "source": {"files": [{"path": "hot.py", "content": HOT}]},
                "artifacts": {"speedscope": {"json": SPEED}}, **extra}

    def test_confirmed_finding_is_published_and_missing_artifacts_are_visible(self):
        out = profile_handler.lambda_handler(self.event())
        by_check = {r["check_id"]: r for r in out["results"]}
        expected = {k for k, m in ARTIFACT_CHECKS.items() if accepts(m, "hot.py")}  # JS checks need JS files
        self.assertEqual(set(by_check), expected)
        self.assertEqual((by_check["PY-01"]["status"], by_check["PY-01"]["findings"]), ("completed", 1))
        # no memray artifact was supplied: not "clean", explicitly unavailable
        for key in ("PY-05", "PY-11"):  # present once their PRs land
            if key in by_check:
                self.assertEqual(by_check[key]["status"], "unavailable")
        details = [json.loads(e["Detail"]) for e in self.events.entries]
        for result in details:
            validate(result)
        self.assertEqual([d["check_id"] for d in details if d["findings"]], ["PY-01"])

    def test_settings_override_flows_into_context(self):
        out = profile_handler.lambda_handler(self.event(settings={"min_time_share": 0.99}, dry_run=True))
        self.assertEqual({r["check_id"]: r for r in out["results"]}["PY-01"]["findings"], 0)

    def test_requires_artifacts_and_known_types(self):
        with self.assertRaises(ValueError):
            profile_handler.lambda_handler({**self.event(), "artifacts": {}})
        with self.assertRaisesRegex(ValueError, "unknown artifact"):
            profile_handler.lambda_handler({**self.event(), "artifacts": {"perf": {"json": {"x": 1}}}})

    def test_s3_artifact_and_source_pointers(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("repo-abc/hot.py", HOT)

        class FakeS3:
            def get_object(self, Bucket, Key):
                if Key.endswith(".zip"):
                    return {"Body": io.BytesIO(buf.getvalue()), "ContentLength": 1}
                return {"Body": io.BytesIO(json.dumps(SPEED).encode()), "ContentLength": 1}

        common._clients["s3"] = FakeS3()
        out = profile_handler.lambda_handler({
            "repository_id": "github:o/r", "commit_sha": SHA, "dry_run": True,
            "source": {"s3": {"bucket": "b", "key": "uploads/r.zip"}},
            "artifacts": {"speedscope": {"s3": {"bucket": "b", "key": "uploads/s.json"}}}})
        self.assertEqual({r["check_id"]: r for r in out["results"]}["PY-01"]["findings"], 1)


if __name__ == "__main__":
    unittest.main()
