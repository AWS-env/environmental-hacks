import base64
import contextlib
import io
import json
import os
import time
import unittest
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from artifact_upload import api

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
AUDIENCE = "owner-d-artifact-upload"
SHA = "0123456789abcdef0123456789abcdef01234567"
ENV = {
    "AWS_REGION": "ap-south-1",
    "OIDC_AUDIENCE": AUDIENCE,
    "ALLOWED_REPOSITORIES": "AWS-env/*, client-org/service",
    "ARTIFACT_BUCKET": "owner-d-artifacts-123456789012-ap-south-1",
    "MAX_ARTIFACT_MIB": "25",
}


def claims(**overrides):
    now = int(time.time())
    base = {"iss": api.ISSUER, "aud": AUDIENCE, "iat": now, "nbf": now, "exp": now + 300,
            "repository": "AWS-env/environmental-hacks", "repository_owner": "AWS-env", "sha": SHA,
            "run_id": "123456", "run_attempt": "1", "sub": "repo:AWS-env/environmental-hacks:ref:refs/heads/main"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def token(key=KEY, algorithm="RS256", kid="k1", **overrides):
    return jwt.encode(claims(**overrides), key, algorithm=algorithm, headers={"kid": kid})


def event(tok=None, body=None, b64=False):
    body = json.dumps({"artifacts": ["cpuprofile.json"]}) if body is None else body
    headers = {"content-type": "application/json"}
    if tok is not None:
        headers["Authorization"] = f"Bearer {tok}"
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"headers": headers, "body": body, "isBase64Encoded": b64}


class FakeJwks:
    """GitHub's JWKS endpoint with one key, kid k1."""

    def __init__(self, error=None):
        self.error = error

    def get_signing_key_from_jwt(self, tok):
        if self.error:
            raise self.error
        if jwt.get_unverified_header(tok).get("kid") != "k1":
            raise jwt.PyJWKClientError("Unable to find a signing key that matches")
        return mock.Mock(key=KEY.public_key())


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_post(self, **kw):
        self.calls.append(kw)
        return {"url": f"https://{kw['Bucket']}.s3.ap-south-1.amazonaws.com/",
                "fields": {"key": kw["Key"], "policy": "SECRET-POLICY", "x-amz-signature": "SECRET-SIG"}}


class ArtifactUploadTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.s3 = FakeS3()
        api._jwks, api._s3 = FakeJwks(), self.s3
        self.addCleanup(setattr, api, "_jwks", None)
        self.addCleanup(setattr, api, "_s3", None)

    def call(self, evt):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            response = api.handler(evt)
        self.log = out.getvalue()
        return response["statusCode"], json.loads(response["body"]), response

    # ---- accepted --------------------------------------------------------------------------

    def test_valid_token_returns_one_presigned_post_per_artifact(self):
        tok = token()
        status, body, response = self.call(event(tok, json.dumps({"artifacts": ["cpuprofile.json", "junit.xml"]})))
        self.assertEqual(status, 200)
        prefix = f"uploads/github%3AAWS-env%2Fenvironmental-hacks/{SHA}/123456-1/"
        self.assertEqual(body["prefix"], prefix)
        self.assertEqual(body["expires_in"], 900)
        self.assertEqual(body["max_bytes"], 26214400)
        self.assertEqual(set(body["uploads"]), {"cpuprofile.json", "junit.xml"})
        self.assertEqual(body["uploads"]["junit.xml"]["key"], prefix + "junit.xml")
        self.assertEqual(response["headers"]["cache-control"], "no-store")
        for call in self.s3.calls:
            self.assertEqual(call["Bucket"], ENV["ARTIFACT_BUCKET"])
            self.assertTrue(call["Key"].startswith(prefix))
            self.assertEqual(call["Conditions"], [["content-length-range", 1, 26214400]])
            self.assertEqual(call["ExpiresIn"], 900)

    def test_token_claims_decide_the_prefix_not_the_body(self):
        body = json.dumps({"artifacts": ["a.json"], "repository": "victim/repo", "sha": "f" * 40})
        status, out, _ = self.call(event(token(run_attempt="2"), body))
        self.assertEqual(status, 200)
        self.assertEqual(out["uploads"]["a.json"]["key"],
                         f"uploads/github%3AAWS-env%2Fenvironmental-hacks/{SHA}/123456-2/a.json")

    def test_numeric_run_claims_are_accepted(self):
        status, _, _ = self.call(event(token(run_id=987, run_attempt=3)))
        self.assertEqual(status, 200)

    def test_base64_body(self):
        status, _, _ = self.call(event(token(), b64=True))
        self.assertEqual(status, 200)

    def test_exact_repository_allowlist_entry(self):
        status, _, _ = self.call(event(token(repository="client-org/service")))
        self.assertEqual(status, 200)

    def test_allowlist_is_case_insensitive(self):
        status, _, _ = self.call(event(token(repository="aws-ENV/other-repo")))
        self.assertEqual(status, 200)

    # ---- refused tokens ---------------------------------------------------------------------

    def assert_refused(self, evt, status):
        got, body, _ = self.call(evt)
        self.assertEqual(got, status, body)
        self.assertIn("error", body)
        self.assertEqual(self.s3.calls, [])

    def test_missing_or_non_bearer_authorization(self):
        self.assert_refused(event(None), 401)
        evt = event(None)
        evt["headers"]["authorization"] = "Basic dXNlcjpwYXNz"
        self.assert_refused(evt, 401)

    def test_expired_token(self):
        past = int(time.time()) - 3600
        self.assert_refused(event(token(iat=past - 300, nbf=past - 300, exp=past)), 401)

    def test_wrong_audience(self):
        self.assert_refused(event(token(aud="sts.amazonaws.com")), 401)

    def test_wrong_issuer(self):
        self.assert_refused(event(token(iss="https://evil.example.com")), 401)

    def test_signed_by_another_key(self):
        self.assert_refused(event(token(key=OTHER_KEY)), 401)

    def test_unknown_kid(self):
        self.assert_refused(event(token(kid="nope")), 401)

    def test_hs256_and_none_algorithms_are_refused(self):
        self.assert_refused(event(token(key="a-shared-secret-of-sufficient-length!", algorithm="HS256")), 401)
        unsigned = jwt.encode(claims(), None, algorithm="none", headers={"kid": "k1"})
        self.assert_refused(event(unsigned), 401)

    def test_missing_required_claim(self):
        self.assert_refused(event(token(run_id=None)), 401)

    def test_malformed_claims(self):
        self.assert_refused(event(token(sha="HEAD")), 401)
        self.assert_refused(event(token(repository="../../etc")), 401)
        self.assert_refused(event(token(run_attempt="1/../x")), 401)

    def test_repository_not_on_allowlist(self):
        self.assert_refused(event(token(repository="someone/else")), 403)
        self.assert_refused(event(token(repository="AWS-env-evil/repo")), 403)
        self.assert_refused(event(token(repository="client-org/other")), 403)

    def test_empty_allowlist_refuses_everyone(self):
        with mock.patch.dict(os.environ, {"ALLOWED_REPOSITORIES": ""}):
            self.assert_refused(event(token()), 403)

    def test_jwks_unreachable_is_503(self):
        api._jwks = FakeJwks(error=jwt.PyJWKClientConnectionError("timed out"))
        self.assert_refused(event(token()), 503)

    def test_oversized_token(self):
        self.assert_refused(event("x" * (api.MAX_TOKEN_CHARS + 1)), 401)

    # ---- refused bodies ---------------------------------------------------------------------

    def test_bad_bodies(self):
        tok = token()
        for body in ["not json", "[]", "{}", json.dumps({"artifacts": []}), json.dumps({"artifacts": "a.json"}),
                     json.dumps({"artifacts": [f"a{i}" for i in range(api.MAX_ARTIFACTS + 1)]}),
                     json.dumps({"artifacts": ["a.json", "a.json"]}), "x" * (api.MAX_BODY_BYTES + 1)]:
            with self.subTest(body=body[:40]):
                self.assert_refused(event(tok, body), 400)

    def test_bad_artifact_names(self):
        tok = token()
        for name in ["../x", "a/b", ".hidden", "a..b", "", "a" * 101, "sp ace", 7, None]:
            with self.subTest(name=name):
                self.assert_refused(event(tok, json.dumps({"artifacts": [name]})), 400)

    # ---- logging and Region ------------------------------------------------------------------

    def test_never_logs_token_or_presigned_fields(self):
        tok = token()
        self.call(event(tok))
        self.assertIn('"outcome": "presigned"', self.log)
        for secret in (tok, "SECRET-POLICY", "SECRET-SIG", "https://"):
            self.assertNotIn(secret, self.log)
        self.call(event(token(repository="someone/else")))
        self.assertIn('"status": 403', self.log)
        self.assertNotIn("eyJ", self.log)

    def test_refuses_other_regions(self):
        with mock.patch.dict(os.environ, {"AWS_REGION": "us-east-1"}):
            with self.assertRaises(RuntimeError):
                api.handler(event(token()))


class RealBotoPresignTest(unittest.TestCase):
    """The real boto3 signer (offline, fake credentials): regional URL and a policy pinned to key + size."""

    def setUp(self):
        try:
            import boto3  # noqa: F401
        except ImportError:
            self.skipTest("boto3 is not installed")
        env = dict(ENV, AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE",
                   AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        api._s3 = None
        self.addCleanup(setattr, api, "_s3", None)

    def test_policy_pins_key_and_size(self):
        identity = {"repository": "AWS-env/environmental-hacks", "sha": SHA, "run_id": "1", "run_attempt": "1"}
        upload = api.presign(identity, ["junit.xml"])["uploads"]["junit.xml"]
        self.assertEqual(upload["url"], f"https://{ENV['ARTIFACT_BUCKET']}.s3.ap-south-1.amazonaws.com/")
        policy = json.loads(base64.b64decode(upload["fields"]["policy"]))
        self.assertIn({"key": upload["key"]}, policy["conditions"])
        self.assertIn({"bucket": ENV["ARTIFACT_BUCKET"]}, policy["conditions"])
        self.assertIn(["content-length-range", 1, 26214400], policy["conditions"])


if __name__ == "__main__":
    unittest.main()
