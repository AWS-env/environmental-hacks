"""AWS Lambda `owner-d-artifact-parser`: client-CI artifacts uploaded through `POST /artifacts/presign` (#447)
-> Owner D artifact-mode checks -> findings-hub.

Trigger: an EventBridge rule on the default bus for S3 "Object Created" in owner-d-artifacts-<account>-<region>
under uploads/. The upload endpoint built the key from verified GitHub OIDC claims, so the key is the identity:

    uploads/<quote("github:<owner>/<repo>")>/<commit sha>/<run_id>-<run_attempt>/<name>

The object is read (at most MAX_OBJECT_BYTES), parsed as JSON and never executed. `name` picks the check:

    tst-12.json  {"framework": "pytest", "settings": {...optional TST-12 maxima},
                  "tests": [{"test_id": "tests/test_api.py::test_fetch", "duration_seconds": 14.2,
                             "sleep_seconds": 2.5, "network_call_count": 3, "fixture_bytes": 52428800,
                             "setup_seconds": 4.1, "framework": "optional per-test override"}]}

Every test becomes one `test:<test_id>` scope item with one `artifact` source (TST-12 artifact mode). Inputs are
chunked, evaluated, checked with shared.contracts validate_pair and published as detector.result.v1 events, like
the telemetry analyzers (common.py). Names with no route are ignored. An artifact that cannot be used (too
large, not JSON, wrong shape) is refused: logged, nothing published, and the invocation still succeeds, so a
client's bad upload never lands in the DLQ. AWS failures (GetObject, PutEvents) raise, so EventBridge retries
and then sends the event to the DLQ.

Direct invoke for replays: {"bucket": "...", "key": "uploads/...", "dry_run": true}.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

from owner_d.aws import common

ANALYZER = "owner-d-artifact-parser"
SOURCE = "owner-d.artifact-parser"
MAX_OBJECT_BYTES = 5 * 1024 * 1024
MAX_TESTS = 2000
KEY = re.compile(r"uploads/(?P<repo>[^/]+)/(?P<sha>[0-9a-f]{40})/(?P<run>[0-9]{1,20})-(?P<attempt>[0-9]{1,20})/"
                 r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,99})")
REPOSITORY_ID = re.compile(r"github:[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")


class Refused(ValueError):
    """The artifact cannot be used. Reported and logged, never retried."""


# ---- object identity and reading ---------------------------------------------------------------

def object_ref(event):
    """(bucket, key) from an EventBridge S3 event or a direct {"bucket", "key"} invoke."""
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    detail = event.get("detail") if event.get("source") == "aws.s3" else None
    if detail is not None:
        bucket, key = (detail.get("bucket") or {}).get("name"), (detail.get("object") or {}).get("key")
    else:
        bucket, key = event.get("bucket"), event.get("key")
    if not isinstance(bucket, str) or not isinstance(key, str) or not bucket or not key:
        raise ValueError("event needs an S3 bucket name and object key")
    allowed = os.environ.get("ARTIFACT_BUCKET")
    if allowed and bucket != allowed:
        raise ValueError(f"bucket {bucket!r} is not the artifact bucket")
    return bucket, key


def identity_from_key(key):
    match = KEY.fullmatch(key)
    if not match:
        raise Refused("key is not uploads/<repository>/<40-hex sha>/<run>-<attempt>/<name>")
    repository_id = urllib.parse.unquote(match["repo"])
    if not REPOSITORY_ID.fullmatch(repository_id):
        raise Refused("key repository is not github:<owner>/<repo>")
    return {"repository_id": repository_id, "commit_sha": match["sha"], "run": f"{match['run']}-{match['attempt']}",
            "scan_id": f"gha-{match['run']}-{match['attempt']}", "name": match["name"]}


def read_object(bucket, key):
    """The object's bytes, refusing anything larger than MAX_OBJECT_BYTES (the Range read is the hard cap)."""
    s3 = common.own_client("s3")
    try:
        response = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{MAX_OBJECT_BYTES}")
    except Exception as exc:  # botocore ClientError
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code == "NoSuchKey":
            raise Refused("artifact no longer exists") from None
        if code == "InvalidRange":  # a zero-byte object has no byte 0
            raise Refused("artifact is empty") from None
        raise
    body = response["Body"].read(MAX_OBJECT_BYTES + 1)
    if len(body) > MAX_OBJECT_BYTES:
        raise Refused(f"artifact is larger than {MAX_OBJECT_BYTES} bytes")
    return body


def load_json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("artifact is not UTF-8 JSON") from None


# ---- per-check builders ------------------------------------------------------------------------

def tst12_inputs(data, identity):
    """TST-12 artifact mode: (module, context, scope, sources, notes)."""
    from owner_d import tst12

    if not isinstance(data, dict) or not isinstance(data.get("tests"), list) or not data["tests"]:
        raise Refused('tst-12.json needs {"tests": [...]} with at least one test')
    framework = data.get("framework")
    overrides = data.get("settings") or {}
    if not isinstance(overrides, dict) or set(overrides) - set(tst12.SETTING_KEYS):
        raise Refused(f"settings may only contain {', '.join(tst12.SETTING_KEYS)}")
    context = dict(tst12.REFERENCE_SETTINGS) | overrides
    notes, scope, sources, seen = [], [], [], set()
    tests = data["tests"]
    if len(tests) > MAX_TESTS:
        notes.append(f"only the first {MAX_TESTS} of {len(tests)} tests in {identity['name']} were evaluated")
        tests = tests[:MAX_TESTS]
    for index, test in enumerate(tests):
        test_id = test.get("test_id") if isinstance(test, dict) else None
        if not isinstance(test_id, str) or not test_id.strip() or len(test_id) > 500:
            notes.append(f"tests[{index}] has no usable test_id (nonempty string, at most 500 characters); skipped")
            continue
        if test_id in seen:
            notes.append(f"duplicate test_id {test_id!r} in tests[{index}]; only the first entry was evaluated")
            continue
        seen.add(test_id)
        scope_id = f"test:{test_id}"
        sources.append({
            "source_id": f"artifact-{index}",
            "scope_id": scope_id,
            "kind": tst12.ARTIFACT_KIND,
            "locator": f"{identity['name']} from GitHub Actions run {identity['run']}: {test_id}",
            "data": {"framework": framework, **test} if framework is not None and "framework" not in test else test,
        })
        scope.append(scope_id)
    return tst12, context, scope, sources, notes


ROUTES = {"tst-12.json": tst12_inputs}


def llm16_inputs(data, identity):
    """LLM-16 artifact mode: raw `memray stats --json` output -> one `artifact:llm-16.json` scope (llm16.memray_inputs)."""
    from owner_d import llm16

    try:
        return (llm16, *llm16.memray_inputs(data, identity["name"], identity["run"]))
    except ValueError as exc:
        raise Refused(str(exc)) from None


ROUTES["llm-16.json"] = llm16_inputs


def llm19_inputs(data, identity):
    """LLM-19 artifact mode: {"settings": {...}, "servers": [...]}, shape and validation in owner_d/llm19.py."""
    from owner_d import llm19

    try:
        context, scope, sources, notes = llm19.artifact_inputs(data, identity["name"], identity["run"])
    except llm19.ArtifactRejected as exc:
        raise Refused(str(exc)) from None
    return llm19, context, scope, sources, notes


ROUTES["llm-19.json"] = llm19_inputs


# ---- handler -----------------------------------------------------------------------------------

def evaluate(identity, data, *, chunk=common.DEFAULT_SCOPE_PER_PAYLOAD):
    """Contract results for one artifact plus refused/errors lists (same rules as common.run_analyzer)."""
    module, context, scope, sources, notes = ROUTES[identity["name"]](data, identity)
    if not scope:
        raise Refused("no usable tests in the artifact: " + "; ".join(notes[:5]))
    validate_pair = common._validator()
    results, refused, errors = [], [], []
    for payload in common.build_inputs(repository_id=identity["repository_id"], commit_sha=identity["commit_sha"],
                                       scan_id=identity["scan_id"], module=module, context=context, scope=scope,
                                       sources=sources, chunk=chunk):
        try:
            result = common.add_limitations(module.evaluate(payload), notes)
        except Exception as exc:  # detector failure: never publish, report it
            errors.append({"check_id": module.CHECK_ID, "error": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            validate_pair(payload, result)
        except Exception as exc:  # contract failure: refuse to publish, report it
            refused.append({"check_id": module.CHECK_ID, "scope": len(payload["scope"]),
                            "status": result.get("status"),
                            "error": f"{type(exc).__name__}: {exc}"[:common.MAX_ERROR_CHARS]})
            continue
        results.append(result)
    return results, refused, errors


def lambda_handler(event, context=None):
    common.region()
    bucket, key = object_ref(event)
    summary = {"analyzer": ANALYZER, "key": key, "dry_run": bool(event.get("dry_run")), "published": 0}
    try:
        identity = identity_from_key(key)
        summary |= {"repository_id": identity["repository_id"], "commit_sha": identity["commit_sha"],
                    "scan_id": identity["scan_id"], "artifact": identity["name"]}
        if identity["name"] not in ROUTES:
            return common.log_summary(summary | {"outcome": "ignored", "reason": "no parser for this artifact name"})
        results, refused, errors = evaluate(identity, load_json(read_object(bucket, key)))
    except Refused as exc:
        return common.log_summary(summary | {"outcome": "refused", "reason": str(exc)})
    bus = os.environ.get("FINDINGS_BUS_NAME")
    if results and bus and not summary["dry_run"]:
        summary["published"] = common.publish_results(results, bus_name=bus, source=SOURCE)
    summary |= {"outcome": "evaluated", "contract_validated": True, "results": common.summarize(results),
                "refused": refused, "errors": errors}
    if summary["dry_run"]:
        summary["result_payloads"] = results
    common.log_summary(summary)
    if errors:
        raise RuntimeError(f"{len(errors)} result(s) failed evaluation and were not published: "
                           + "; ".join(e["error"] for e in errors))
    return summary
