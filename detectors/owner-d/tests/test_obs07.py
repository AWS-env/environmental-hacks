"""Behavioral tests for the OBS-07 detector (issue #231).

`recorded-describe-log-groups.json` is a real DescribeLogGroups response from the project
(account ID replaced with 123456789012). Every `synthetic-*.json` fixture is synthetic.
"""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli
from owner_d.obs07 import (
    CHECK_ID,
    DETECTOR_VERSION,
    EvaluationError,
    NormalizationError,
    evaluate,
    fingerprint,
    normalize_describe_log_groups,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs07"
REPOSITORY_ID = "aws:ap-south-1:project"
GiB = 1024**3
CONTEXT = {
    "max_hot_retention_days": 365,
    "min_stored_bytes": GiB,
    "compliance_tag_keys": ["compliance", "data-retention", "legal-hold"],
    "exempt_log_group_prefixes": ["aws-controltower/"],
}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def normalized_fixture(name):
    fixture = load(name)
    return normalize_describe_log_groups(fixture["pages"], fixture.get("tags"))


def source(name, data):
    return {
        "source_id": f"cwl:{name}",
        "scope_id": f"resource:{name}",
        "kind": "telemetry",
        "locator": data.get("log_group_arn") or f"cloudwatch-logs:{name}",
        "data": data,
    }


def make_input(groups, context=None, sources=None, scope=None):
    if sources is None:
        sources = [source(name, data) for name, data in groups.items()]
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-obs07-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": copy.deepcopy(CONTEXT) if context is None else context,
        "scope": scope if scope is not None else [f"resource:{name}" for name in groups],
        "sources": sources,
    }


def run(groups, **kwargs):
    payload = make_input(groups, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_scope(result):
    return {finding["scope_id"]: finding for finding in result["findings"]}


def evidence_map(finding):
    return {item["field"]: item["value"] for item in finding["evidence"]}


class Obs07NormalizerTests(unittest.TestCase):
    def test_recorded_response_normalizes_every_group(self):
        raw = load("recorded-describe-log-groups.json")
        groups = normalize_describe_log_groups([raw])
        self.assertEqual(len(groups), 20)
        first = groups["/aws/lambda/owner-a-presign"]
        self.assertEqual(first["retention_in_days"], 7)
        self.assertEqual(first["stored_bytes"], 0)
        self.assertEqual(first["log_group_class"], "STANDARD")
        self.assertEqual(first["creation_time"], "2026-10-09T21:34:49.261Z")
        self.assertEqual(
            first["log_group_arn"], "arn:aws:logs:ap-south-1:123456789012:log-group:/aws/lambda/owner-a-presign"
        )
        self.assertIsNone(first["tags"])
        self.assertIsNone(first["data_protection_status"])

    def test_absent_retention_means_never_expire_and_tags_are_attached(self):
        groups = normalized_fixture("synthetic-positive.json")
        self.assertEqual(list(groups), ["/aws/lambda/orders-api", "/aws/ecs/payments-worker"])
        orders = groups["/aws/lambda/orders-api"]
        self.assertIsNone(orders["retention_in_days"])
        self.assertEqual(orders["stored_bytes"], 50 * GiB)
        self.assertEqual(orders["data_protection_status"], "ACTIVATED")
        self.assertEqual(orders["tags"], {"team": "orders", "env": "production"})
        self.assertIsNone(groups["/aws/ecs/payments-worker"]["tags"])

    def test_tags_can_be_keyed_by_arn_and_arn_falls_back_to_trimmed_arn(self):
        page = {"logGroups": [{"logGroupName": "/x", "arn": "arn:aws:logs:ap-south-1:123456789012:log-group:/x:*",
                               "storedBytes": 1}]}
        arn = "arn:aws:logs:ap-south-1:123456789012:log-group:/x"
        groups = normalize_describe_log_groups([page], {arn: {"k": "v"}})
        self.assertEqual(groups["/x"]["log_group_arn"], arn)
        self.assertEqual(groups["/x"]["tags"], {"k": "v"})
        self.assertIsNone(groups["/x"]["log_group_class"])

    def test_malformed_responses_raise(self):
        cases = {
            "single response": {"logGroups": []},
            "empty": [],
            "no logGroups": [{"nextToken": "t"}],
            "logGroups not list": [{"logGroups": {}}],
            "group not object": [{"logGroups": ["x"]}],
            "no name": [{"logGroups": [{"storedBytes": 1}]}],
            "duplicate": [{"logGroups": [{"logGroupName": "/a"}]}, {"logGroups": [{"logGroupName": "/a"}]}],
        }
        for label, pages in cases.items():
            with self.subTest(label), self.assertRaises(NormalizationError):
                normalize_describe_log_groups(pages)
        with self.assertRaises(NormalizationError):
            normalize_describe_log_groups([{"logGroups": [{"logGroupName": "/a"}]}], {"/a": {"k": 1}})
        self.assertTrue(issubclass(NormalizationError, ValueError))


class Obs07PositiveTests(unittest.TestCase):
    """OBS07-01: never-expire and over-horizon groups with real stored bytes are flagged."""

    def test_flags_never_expire_and_long_retention_groups(self):
        payload, result = run(normalized_fixture("synthetic-positive.json"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["measurements"], [])
        findings = by_scope(result)
        self.assertEqual(set(findings), {"resource:/aws/lambda/orders-api", "resource:/aws/ecs/payments-worker"})

        orders = findings["resource:/aws/lambda/orders-api"]
        self.assertEqual(orders["confidence"], "medium")
        self.assertEqual(orders["identity"], "hot-log-retention")
        self.assertEqual(
            evidence_map(orders),
            {
                "retention_in_days": None,
                "stored_bytes": 50 * GiB,
                "log_group_class": "STANDARD",
                "tags": {"team": "orders", "env": "production"},
            },
        )
        self.assertIn("forever", orders["summary"])
        self.assertIn("50.0 GiB", orders["summary"])
        self.assertIn("no compliance tag", orders["summary"])
        self.assertEqual(
            orders["evidence"][0]["locator"], "arn:aws:logs:ap-south-1:123456789012:log-group:/aws/lambda/orders-api"
        )

        payments = findings["resource:/aws/ecs/payments-worker"]
        self.assertEqual(payments["confidence"], "low")
        self.assertEqual(evidence_map(payments)["retention_in_days"], 3653)
        self.assertEqual(evidence_map(payments)["log_group_class"], "INFREQUENT_ACCESS")
        self.assertIsNone(evidence_map(payments)["tags"])
        self.assertIn("3653 days", payments["summary"])
        self.assertIn("cannot be ruled out", payments["summary"])
        self.assertIn("ingestion cost only", payments["recommendation"])
        for finding in result["findings"]:
            self.assertEqual(
                finding["fingerprint"],
                shared_fingerprint(REPOSITORY_ID, CHECK_ID, finding["scope_id"], "hot-log-retention"),
            )
            self.assertTrue(all(ref.startswith("https://") for ref in finding["references"]))

    def test_committed_cli_fixture_matches_normalized_positive(self):
        committed = json.loads((FIXTURES / "obs07-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(normalized_fixture("synthetic-positive.json")))


class Obs07NegativeTests(unittest.TestCase):
    """OBS07-02: short retention, Delivery class and the real project response are clean."""

    def test_recorded_project_response_is_clean_and_completed(self):
        groups = normalize_describe_log_groups([load("recorded-describe-log-groups.json")])
        _, result = run(groups)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 20)
        self.assertEqual(len(result["coverage"]["limitations"]), 1)

    def test_large_groups_with_short_retention_are_not_flagged(self):
        _, result = run(normalized_fixture("synthetic-negative.json"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(
            result["coverage"]["evaluated_scope"],
            ["resource:/aws/lambda/search-indexer", "resource:/aws/ecs/batch-report", "resource:/aws/vendedlogs/lambda-delivery"],
        )

    def test_delivery_class_with_fixed_two_day_retention_is_clean(self):
        groups = normalized_fixture("synthetic-negative.json")
        groups["/aws/vendedlogs/lambda-delivery"]["retention_in_days"] = 2
        _, result = run(groups)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Obs07ExceptionTests(unittest.TestCase):
    """OBS07-03: compliance tags, exempt prefixes and tiny groups are evaluated but not flagged."""

    def test_exceptions_are_evaluated_with_notes(self):
        _, result = run(normalized_fixture("synthetic-exceptions.json"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        limitations = result["coverage"]["limitations"]
        self.assertIn(
            "resource:/aws/lambda/card-ledger: retention never expire with 50.0 GiB stored is exempt by the "
            "compliance tag 'Compliance'",
            limitations,
        )
        self.assertIn(
            "resource:aws-controltower/CloudTrailLogs: retention never expire with 80.0 GiB stored is exempt by "
            "the configured prefix 'aws-controltower/'",
            limitations,
        )
        self.assertIn(
            "resource:/aws/lambda/cron-heartbeat: retention never expire but only 10.0 MiB stored, below the "
            "1.0 GiB minimum; not flagged yet",
            limitations,
        )

    def test_exceptions_follow_context_settings(self):
        context = dict(CONTEXT, compliance_tag_keys=[], exempt_log_group_prefixes=[], min_stored_bytes=0)
        _, result = run(normalized_fixture("synthetic-exceptions.json"), context=context)
        self.assertEqual(len(result["findings"]), 3)
        self.assertEqual({f["confidence"] for f in result["findings"]}, {"medium"})


class Obs07IncompleteTests(unittest.TestCase):
    """OBS07-04 / OBS07-05: missing or malformed evidence never becomes a clean claim."""

    def test_missing_source_gives_partial(self):
        groups = normalized_fixture("synthetic-positive.json")
        scope = [f"resource:{name}" for name in groups] + ["resource:/aws/lambda/missing"]
        _, result = run(groups, scope=scope)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("resource:/aws/lambda/missing", result["coverage"]["evaluated_scope"])
        self.assertIn(
            "resource:/aws/lambda/missing: no telemetry source supplied; OBS-07 requires normalized log group data",
            result["coverage"]["limitations"],
        )

    def test_only_missing_sources_gives_unavailable(self):
        _, result = run({}, sources=[], scope=["resource:/a"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])

    def test_missing_or_invalid_settings_give_unavailable(self):
        groups = normalized_fixture("synthetic-positive.json")
        cases = {
            "absent": ({k: v for k, v in CONTEXT.items() if k != "min_stored_bytes"},
                       "missing required context settings: min_stored_bytes"),
            "string": (dict(CONTEXT, max_hot_retention_days="365"), "context.max_hot_retention_days must be a number"),
            "bool": (dict(CONTEXT, min_stored_bytes=True), "context.min_stored_bytes must be a number"),
            "zero days": (dict(CONTEXT, max_hot_retention_days=0), "context.max_hot_retention_days must be positive"),
            "negative bytes": (dict(CONTEXT, min_stored_bytes=-1), "context.min_stored_bytes must not be negative"),
            "not a list": (dict(CONTEXT, compliance_tag_keys="compliance"),
                           "context.compliance_tag_keys must be a list of nonempty strings (may be empty)"),
            "empty prefix": (dict(CONTEXT, exempt_log_group_prefixes=[""]),
                             "context.exempt_log_group_prefixes must be a list of nonempty strings (may be empty)"),
        }
        for label, (context, reason) in cases.items():
            with self.subTest(label):
                _, result = run(groups, context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertEqual(result["coverage"]["limitations"][0], f"Missing or invalid context settings: {reason}")

    def test_malformed_data_is_omitted_with_reason(self):
        base = normalized_fixture("synthetic-positive.json")["/aws/lambda/orders-api"]
        cases = {
            "string retention": ({"retention_in_days": "never"}, "retention_in_days must be an integer or null"),
            "unsupported retention": ({"retention_in_days": 42}, "retention_in_days 42 is not a CloudWatch Logs retention value"),
            "stored bytes absent": ({"stored_bytes": None}, "stored_bytes was not reported"),
            "negative bytes": ({"stored_bytes": -5}, "stored_bytes must be a nonnegative integer"),
            "bad class": ({"log_group_class": "COLD"}, "unsupported log_group_class 'COLD'"),
            "bad tags": ({"tags": ["compliance"]}, "tags must be an object of strings or null"),
            "wrong type": ({"resource_type": "aws_s3_bucket"}, "unsupported resource_type 'aws_s3_bucket'"),
            "scope mismatch": ({"resource_id": "/aws/lambda/other"}, "does not match resource_id"),
        }
        good = normalized_fixture("synthetic-positive.json")["/aws/ecs/payments-worker"]
        for label, (patch, reason) in cases.items():
            with self.subTest(label):
                data = dict(base, **patch)
                sources = [source("/aws/lambda/orders-api", data), source("/aws/ecs/payments-worker", good)]
                sources[0]["scope_id"] = "resource:/aws/lambda/orders-api"
                _, result = run({}, sources=sources, scope=["resource:/aws/lambda/orders-api", "resource:/aws/ecs/payments-worker"])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["resource:/aws/ecs/payments-worker"])
                self.assertEqual([f["scope_id"] for f in result["findings"]], ["resource:/aws/ecs/payments-worker"])
                self.assertIn(reason, result["coverage"]["limitations"][0])

        missing_field = {k: v for k, v in base.items() if k != "tags"}
        _, result = run({"/aws/lambda/orders-api": missing_field})
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("missing fields: tags", result["coverage"]["limitations"][0])

    def test_multiple_sources_for_one_scope_are_not_evaluated(self):
        data = normalized_fixture("synthetic-positive.json")["/aws/lambda/orders-api"]
        first = source("/aws/lambda/orders-api", data)
        second = dict(first, source_id="cwl:dup")
        _, result = run({}, sources=[first, second], scope=["resource:/aws/lambda/orders-api"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("multiple telemetry sources", result["coverage"]["limitations"][0])

    def test_wrong_check_or_version_raises(self):
        payload = make_input(normalized_fixture("synthetic-positive.json"))
        for field, value in (("check_id", "OBS-01"), ("detector_version", "9.9.9"), ("kind", "result")):
            with self.subTest(field), self.assertRaises(EvaluationError):
                evaluate(dict(payload, **{field: value}))


class Obs07BoundaryTests(unittest.TestCase):
    """OBS07-06: strict `>` on retention days, `>=` on stored bytes, value-independent fingerprints."""

    def test_thresholds(self):
        _, result = run(normalized_fixture("synthetic-boundary.json"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            sorted(by_scope(result)), ["resource:/app/bytes-at-min", "resource:/app/retention-400"]
        )
        self.assertIn("400 days, beyond the 365-day hot-retention horizon", by_scope(result)["resource:/app/retention-400"]["summary"])
        self.assertTrue(any(note.startswith("resource:/app/bytes-below-min:") for note in result["coverage"]["limitations"]))

    def test_fingerprint_is_stable_when_values_change(self):
        groups = normalized_fixture("synthetic-positive.json")
        _, before = run(groups)
        changed = copy.deepcopy(groups)
        changed["/aws/lambda/orders-api"]["stored_bytes"] = 90 * GiB
        changed["/aws/lambda/orders-api"]["retention_in_days"] = 3653
        _, after = run(changed)
        self.assertEqual(
            by_scope(before)["resource:/aws/lambda/orders-api"]["fingerprint"],
            by_scope(after)["resource:/aws/lambda/orders-api"]["fingerprint"],
        )
        self.assertEqual(
            fingerprint(REPOSITORY_ID, CHECK_ID, "resource:/x", "hot-log-retention"),
            shared_fingerprint(REPOSITORY_ID, CHECK_ID, "resource:/x", "hot-log-retention"),
        )

    def test_tampered_evidence_is_rejected_by_contract(self):
        payload, result = run(normalized_fixture("synthetic-positive.json"))
        result["findings"][0]["evidence"][1]["value"] = 1
        with self.assertRaises(ContractError):
            validate_pair(payload, result)


class Obs07CliTests(unittest.TestCase):
    def test_cli_writes_valid_obs07_result(self):
        input_path = FIXTURES / "obs07-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["findings"]), 2)


if __name__ == "__main__":
    unittest.main()
