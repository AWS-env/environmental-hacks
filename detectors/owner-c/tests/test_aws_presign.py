"""Presign handshake and the S3-manifest trigger, with fake AWS clients."""
import io
import json
import unittest
import zipfile
from unittest import mock

from helpers import SHA
from owner_c.aws import common, presign_handler, profile_handler
from test_aws import AwsTestCase


class PresignTests(AwsTestCase):
    def setUp(self):
        super().setUp()
        self.signed = []

        class FakeS3:
            def generate_presigned_url(s3, op, Params, ExpiresIn, HttpMethod):
                self.signed.append((op, Params["Bucket"], Params["Key"], ExpiresIn, HttpMethod))
                return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?sig=x"

        common._clients["s3:regional"] = FakeS3()  # presigning must use the regional endpoint; the default client signs the global host
        self.env = mock.patch.dict("os.environ", {"ARTIFACT_BUCKET": "owner-c-artifacts"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_issues_short_lived_put_urls_under_a_quoted_prefix(self):
        artifact = presign_handler.UPLOADABLE[0]  # whichever artifact types are registered
        out = presign_handler.lambda_handler({"repository_id": "github:o/r", "commit_sha": SHA, "artifacts": [artifact]})
        self.assertEqual(out["prefix"], f"uploads/github%3Ao%2Fr/{SHA}/")
        self.assertEqual(sorted(out["urls"]), sorted([artifact, "manifest", "repo"]))
        self.assertTrue(out["urls"]["manifest"]["key"].endswith("/manifest.json"))
        self.assertTrue(all(call[3] == 900 and call[4] == "PUT" for call in self.signed))

    def test_rejects_unknown_or_missing_artifact_names(self):
        for artifacts in ([], ["xray"], ["perf"]):
            with self.subTest(artifacts=artifacts), self.assertRaises(ValueError):
                presign_handler.lambda_handler({"repository_id": "r", "commit_sha": SHA, "artifacts": artifacts})


class ManifestTriggerTests(AwsTestCase):
    def test_manifest_upload_runs_the_parser_over_the_uploaded_objects(self):
        repo = io.BytesIO()
        with zipfile.ZipFile(repo, "w") as zf:
            zf.writestr("r-abc/hot.py", "def slow(items, wanted):\n    for p in wanted:\n        if p in items:\n            pass\n")
        speedscope = json.loads((__import__("helpers").FIXTURES / "real" / "hot.speedscope.json").read_text())
        prefix = f"uploads/github%3Ao%2Fr/{SHA}/"
        objects = {prefix + "repo.zip": repo.getvalue(), prefix + "speedscope.json": json.dumps(speedscope).encode(),
                   prefix + "manifest.json": json.dumps({"artifacts": ["speedscope"]}).encode()}

        class FakeS3:
            def get_object(s3, Bucket, Key):
                body = objects[Key]
                return {"Body": io.BytesIO(body), "ContentLength": len(body)}

        common._clients["s3"] = FakeS3()
        s3_event = {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"},
                                                                  "object": {"key": (prefix + "manifest.json").replace("%", "%25")}}}]}
        out = profile_handler.lambda_handler(s3_event)
        self.assertTrue(out["published"])
        detail = json.loads(self.events.entries[0]["Detail"])
        self.assertEqual((detail["repository_id"], detail["commit_sha"]), ("github:o/r", SHA))
        self.assertNotIn("JS-01", {r["check_id"] for r in out["results"]})  # X-Ray evidence comes from the reader, not uploads

    def test_manifest_that_disagrees_with_its_prefix_is_rejected(self):
        prefix = f"uploads/github%3Ao%2Fr/{SHA}/"

        class FakeS3:
            def get_object(s3, Bucket, Key):
                body = json.dumps({"repository_id": "github:evil/other", "artifacts": []}).encode()
                return {"Body": io.BytesIO(body), "ContentLength": len(body)}

        common._clients["s3"] = FakeS3()
        event = {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": (prefix + "manifest.json").replace("%", "%25")}}}]}
        with self.assertRaisesRegex(ValueError, "does not match"):
            profile_handler.lambda_handler(event)


if __name__ == "__main__":
    unittest.main()
