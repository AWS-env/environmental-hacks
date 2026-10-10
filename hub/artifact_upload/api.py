"""AWS Lambda `owner-d-artifact-presign` behind `POST /artifacts/presign` (API Gateway HTTP API, payload 2.0).

The upload handshake from docs/ARCHITECTURE_FLOWS.md: the client's GitHub Actions job proves who it is with its
OIDC token, gets one short-lived presigned S3 POST per artifact, and uploads straight to the private bucket
owner-d-artifacts-<account>-ap-south-1. We hold no client credentials and never execute what is uploaded.
S3 sends "Object Created" events to EventBridge, so any owner's parser can subscribe to new uploads.

Request:  Authorization: Bearer <GitHub Actions OIDC token, audience = OIDC_AUDIENCE>
          {"artifacts": ["cpuprofile.json", "junit.xml"]}
Response: {"prefix": "uploads/github%3Ao%2Fr/<sha>/<run_id>-<attempt>/", "expires_in": 900, "max_bytes": N,
           "uploads": {"cpuprofile.json": {"key": "...", "url": "https://...", "fields": {...}}, ...}}
Upload:   multipart/form-data POST to `url` with every entry of `fields`, then the file as the last field `file`.

The token decides where uploads go: the key prefix comes from its `repository`, `sha`, `run_id` and
`run_attempt` claims, never from the request body. Each POST policy pins the exact key and a size range.
Tokens and presigned URLs are bearer credentials and are never logged.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
ALGORITHMS = ["RS256"]  # GitHub signs with RS256; anything else (including "none" and HS256) is refused
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "repository", "sha", "run_id", "run_attempt"]
LEEWAY_SECONDS = 60
EXPIRES_IN = 900
MAX_TOKEN_CHARS = 8192
MAX_BODY_BYTES = 8192
MAX_ARTIFACTS = 20
DEFAULT_REGION = "ap-south-1"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
REPOSITORY = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
SHA = re.compile(r"[0-9a-f]{40}")
DIGITS = re.compile(r"[0-9]{1,20}")

_jwks = None  # tests: object with get_signing_key_from_jwt(token) -> object with .key
_s3 = None  # tests: object with generate_presigned_post(...)


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def region() -> str:
    allowed = os.environ.get("ALLOWED_REGION", DEFAULT_REGION)
    current = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if current != allowed:
        raise RuntimeError(f"the artifact upload API runs only in {allowed}; this Region is {current!r}")
    return current


def _jwks_client():
    global _jwks
    if _jwks is None:
        import jwt

        _jwks = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=5)
    return _jwks


def _s3_client():
    global _s3
    if _s3 is None:
        import boto3  # provided by the Lambda runtime
        from botocore.config import Config

        # Regional virtual-hosted endpoint: the global host 307-redirects uploads outside us-east-1.
        _s3 = boto3.client("s3", region_name=region(),
                           config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return _s3


# ---- request parsing ---------------------------------------------------------------------------

def bearer_token(event) -> str:
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization") or ""
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HttpError(401, "send the GitHub Actions OIDC token as 'Authorization: Bearer <token>'")
    if len(token) > MAX_TOKEN_CHARS:
        raise HttpError(401, "token is too long")
    return token


def artifact_names(event) -> list[str]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except ValueError:
            raise HttpError(400, "body is not valid base64 UTF-8") from None
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise HttpError(400, f"body is larger than {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body)
    except ValueError:
        raise HttpError(400, 'body must be JSON: {"artifacts": ["name", ...]}') from None
    names = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_ARTIFACTS:
        raise HttpError(400, f'"artifacts" must be a list of 1-{MAX_ARTIFACTS} file names')
    for name in names:
        if not isinstance(name, str) or not NAME.fullmatch(name) or ".." in name:
            raise HttpError(400, "artifact names use letters, digits, '.', '_' or '-', start with a letter or "
                                 "digit, are at most 100 characters and contain no '..'")
    if len(set(names)) != len(names):
        raise HttpError(400, "artifact names must be unique")
    return names


# ---- identity ----------------------------------------------------------------------------------

def allowed_repositories() -> list[str]:
    return [p.strip().lower() for p in os.environ.get("ALLOWED_REPOSITORIES", "").split(",") if p.strip()]


def repository_allowed(repository: str, patterns: list[str]) -> bool:
    """`owner/repo` matches exactly; `owner/*` matches every repository of that owner (case-insensitive)."""
    repository = repository.lower()
    owner = repository.split("/", 1)[0]
    return any(p == repository or p == f"{owner}/*" for p in patterns)


def verified_claims(token: str) -> dict:
    """Signature (GitHub JWKS, RS256), issuer, audience, expiry and the claims the key prefix needs."""
    import jwt

    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=ALGORITHMS, audience=os.environ["OIDC_AUDIENCE"],
                            issuer=ISSUER, leeway=LEEWAY_SECONDS, options={"require": REQUIRED_CLAIMS})
    except jwt.PyJWKClientConnectionError:
        raise HttpError(503, "could not fetch GitHub's signing keys; retry shortly") from None
    except jwt.PyJWTError as exc:
        raise HttpError(401, f"token rejected ({type(exc).__name__})") from None
    repository, sha = claims.get("repository"), claims.get("sha")
    run_id, attempt = str(claims.get("run_id")), str(claims.get("run_attempt"))
    if not (isinstance(repository, str) and REPOSITORY.fullmatch(repository) and isinstance(sha, str)
            and SHA.fullmatch(sha) and DIGITS.fullmatch(run_id) and DIGITS.fullmatch(attempt)):
        raise HttpError(401, "token claims repository/sha/run_id/run_attempt are malformed")
    if not repository_allowed(repository, allowed_repositories()):
        raise HttpError(403, f"repository {repository} is not allowed to upload artifacts")
    return {"repository": repository, "sha": sha, "run_id": run_id, "run_attempt": attempt}


def prefix_for(identity: dict) -> str:
    repository_id = urllib.parse.quote(f"github:{identity['repository']}", safe="")
    return f"uploads/{repository_id}/{identity['sha']}/{identity['run_id']}-{identity['run_attempt']}/"


# ---- handler -----------------------------------------------------------------------------------

def presign(identity: dict, names: list[str]) -> dict:
    bucket = os.environ["ARTIFACT_BUCKET"]
    max_bytes = int(os.environ["MAX_ARTIFACT_MIB"]) * 1024 * 1024
    prefix = prefix_for(identity)
    uploads = {}
    for name in names:
        key = prefix + name
        # boto3 adds the bucket and exact-key conditions itself; the size range is ours.
        post = _s3_client().generate_presigned_post(
            Bucket=bucket, Key=key, Conditions=[["content-length-range", 1, max_bytes]], ExpiresIn=EXPIRES_IN)
        uploads[name] = {"key": key, "url": post["url"], "fields": post["fields"]}
    return {"prefix": prefix, "expires_in": EXPIRES_IN, "max_bytes": max_bytes, "uploads": uploads}


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body)}


def _log(**fields):
    print(json.dumps({"component": "owner-d-artifact-presign", **fields}, sort_keys=True))


def handler(event, context=None):
    region()
    try:
        token = bearer_token(event)
        names = artifact_names(event)
        identity = verified_claims(token)
        result = presign(identity, names)
    except HttpError as exc:
        _log(outcome="refused", status=exc.status, reason=exc.message)
        return _response(exc.status, {"error": exc.message})
    _log(outcome="presigned", status=200, repository=identity["repository"], sha=identity["sha"],
         run=f"{identity['run_id']}-{identity['run_attempt']}", artifacts=len(names))
    return _response(200, result)
