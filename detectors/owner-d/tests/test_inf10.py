"""Behavioral tests for the INF-10 detector (issue #177)."""

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
from owner_d.inf10 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf10"
REPOSITORY_ID = "github:AWS-env/example"


def static_source(name, content=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{name}",
        "kind": "static",
        "locator": name,
        "content": content,
    }


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-inf10-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "iac"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_inline(name, content):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])


def findings_for(result, name):
    return {f["identity"]: f for f in result["findings"] if f["scope_id"] == f"file:{name}"}


TEMPLATE = 'AWSTemplateFormatVersion: "2010-09-09"\nResources:\n'


class Inf10PositiveTests(unittest.TestCase):
    """INF10-01: storage resources without lifecycle/retention are flagged with exact evidence."""

    # identity: (line_start, line_end, confidence, summary phrase)
    EXPECTED = {
        "template-positive.yaml": {
            "UploadsBucket:s3-lifecycle": (8, 10, "low", "declares no LifecycleConfiguration"),
            "ArchiveBucket:s3-lifecycle": (13, 14, "low", "2 lifecycle rule(s), all Disabled"),
            "StagingBucket:s3-lifecycle": (26, 27, "low", "only has lifecycle rules that abort incomplete multipart"),
            "ReportsBucket:s3-lifecycle": (35, 36, "low", "noncurrent versions, but versioning is not enabled"),
            "DataBucket:s3-noncurrent-versions": (47, 48, "low", "none that expires or transitions noncurrent"),
            "AppLogGroup:log-retention": (54, 55, "medium", "sets no RetentionInDays"),
            "ImageRepository:ecr-lifecycle": (58, 59, "medium", "declares no LifecyclePolicy"),
        },
        "cdk-synth.template.json": {
            "ArtifactsBucket7410C9EF:s3-lifecycle": (3, 4, "low", "versioning is enabled"),
            "AuditLogs0C3E9A77:log-retention": (27, 28, "medium", "never expire"),
        },
    }

    def test_storage_without_lifecycle_is_flagged_with_exact_lines(self):
        names = list(self.EXPECTED)
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["measurements"], [])
        for name, expected in self.EXPECTED.items():
            findings = findings_for(result, name)
            self.assertEqual(set(findings), set(expected), name)
            lines = (FIXTURES / name).read_text().splitlines()
            for identity, (start, end, confidence, phrase) in expected.items():
                finding = findings[identity]
                with self.subTest(name=name, identity=identity):
                    self.assertEqual(finding["confidence"], confidence)
                    self.assertIn(phrase, finding["summary"])
                    self.assertEqual(
                        finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity)
                    )
                    [evidence] = finding["evidence"]
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["line_start"], start)
                    self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                    self.assertTrue(finding["references"])

    def test_versioned_bucket_without_lifecycle_is_one_finding(self):
        findings = findings_for(run("cdk-synth.template.json")[1], "cdk-synth.template.json")
        self.assertNotIn("ArtifactsBucket7410C9EF:s3-noncurrent-versions", findings)


class Inf10NegativeTests(unittest.TestCase):
    """INF10-02: declared lifecycle/retention (literal or intrinsic) and unrelated resources are clean."""

    def test_declared_lifecycle_and_retention_are_clean(self):
        _, result = run("template-negative.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-negative.yaml"])
        self.assertEqual(result["findings"], [])

    def test_removing_the_declaration_restores_the_finding(self):
        """Guards the negatives against passing for the wrong reason (e.g. a parse quirk)."""
        content = (FIXTURES / "template-negative.yaml").read_text()
        for old, new, identity in (
            ("      RetentionInDays: !Ref LogRetentionDays\n", "      LogGroupClass: STANDARD\n",
             "AppLogGroup:log-retention"),
            ("      LogGroupClass: DELIVERY\n", "      LogGroupClass: STANDARD\n", "VendedLogGroup:log-retention"),
            ("            NoncurrentVersionExpiration:\n              NoncurrentDays: 30\n", "",
             "VersionedBucket:s3-noncurrent-versions"),
            ("          - !If [IsProd, {Id: expire, Status: Enabled, ExpirationInDays: 30}, !Ref AWS::NoValue]\n",
             "          - {Id: expire, Status: Disabled, ExpirationInDays: 30}\n", "ConditionalRuleBucket:s3-lifecycle"),
        ):
            with self.subTest(identity=identity):
                self.assertIn(old, content)
                _, result = run_inline("template-negative.yaml", content.replace(old, new, 1))
                self.assertEqual([f["identity"] for f in result["findings"]], [identity])

    def test_template_without_storage_resources_is_clean(self):
        content = TEMPLATE + "  Fn:\n    Type: AWS::Lambda::Function\n    Properties: {Runtime: python3.12}\n"
        _, result = run_inline("app.yaml", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Inf10ExceptionTests(unittest.TestCase):
    """INF10-03: Object Lock buckets and `# noqa: INF-10` are not flagged; other noqa codes are."""

    def test_object_lock_and_noqa(self):
        _, result = run("template-exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([f["identity"] for f in result["findings"]], ["OtherBucket:s3-lifecycle"])

    def test_object_lock_false_is_still_flagged(self):
        content = TEMPLATE + "  B:\n    Type: AWS::S3::Bucket\n    Properties:\n      ObjectLockEnabled: false\n"
        _, result = run_inline("t.yaml", content)
        self.assertEqual([f["identity"] for f in result["findings"]], ["B:s3-lifecycle"])


class Inf10IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF10-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:template.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_unsupported_and_macro_files_make_result_partial(self):
        """INF10-05: invalid JSON, Terraform, non-templates and macros are never reported clean."""
        _, result = run("template-positive.yaml", "broken.json", "main.tf", "workflow.yml", "macro.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-positive.yaml"])
        self.assertTrue(all(f["scope_id"] == "file:template-positive.yaml" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.json: could not be parsed (invalid JSON)", limitations)
        self.assertIn("file:main.tf: unsupported file type; INF-10 v1.0.0 supports CloudFormation/SAM", limitations)
        self.assertIn("file:workflow.yml: not evaluated (no CloudFormation Resources found)", limitations)
        self.assertIn("file:macro.yaml: not evaluated (template uses the macro transform CompanyDefaults", limitations)
        self.assertIn("blocked on OQ-7", limitations)

    def test_unparseable_and_rewriting_templates_are_unavailable(self):
        for name, content, reason in (
            ("t.yaml", TEMPLATE + "  B:\n\tType: AWS::S3::Bucket\n", "tab indentation"),
            ("t.yaml", TEMPLATE + "  B: {Type: AWS::S3::Bucket\n", "could not be parsed"),
            ("t.json", '{"AWSTemplateFormatVersion": "2010-09-09"}', "no CloudFormation Resources found"),
            ("t.yaml", TEMPLATE + "  B:\n    Type: AWS::S3::Bucket\n    Properties:\n      Fn::Transform:\n"
                                  "        Name: AWS::Include\n", "Fn::Transform/AWS::Include or Fn::ForEach on line 6"),
            ("t.yaml", TEMPLATE + "  B:\n    Type: AWS::S3::Bucket\n    Properties: !Transform {Name: AWS::Include}\n",
             "Fn::Transform/AWS::Include or Fn::ForEach on line 5"),
            ("t.yaml", "Transform: AWS::LanguageExtensions\nResources:\n  Fn::ForEach::Buckets:\n    - Name\n"
                       "    - [a, b]\n    - Bucket${Name}: {Type: AWS::S3::Bucket}\n", "Fn::ForEach on line 3"),
            ("kustomization.yaml", "resources:\n  - deploy.yaml\n", "no CloudFormation Resources found"),
            ("bucket.tf.json", '{"resource": {"aws_s3_bucket": {"b": {}}}}', "no CloudFormation Resources found"),
            ("stack.ts", "new s3.Bucket(this, 'B');\n", "unsupported file type"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_sam_and_language_extension_transforms_are_evaluated(self):
        for transform in ("AWS::Serverless-2016-10-31", "[AWS::LanguageExtensions, AWS::Serverless-2016-10-31]"):
            with self.subTest(transform=transform):
                content = f"Transform: {transform}\nResources:\n  L:\n    Type: AWS::Logs::LogGroup\n"
                _, result = run_inline("t.yaml", content)
                self.assertEqual(result["status"], "completed")
                self.assertEqual([f["identity"] for f in result["findings"]], ["L:log-retention"])

    def test_dot_template_files_in_both_syntaxes(self):
        for content in ('{"Resources": {"L": {"Type": "AWS::Logs::LogGroup"}}}',
                        "Resources:\n  L:\n    Type: AWS::Logs::LogGroup\n"):
            with self.subTest(content=content[:12]):
                _, result = run_inline("stack.template", content)
                self.assertEqual([f["identity"] for f in result["findings"]], ["L:log-retention"])


class Inf10BoundaryTests(unittest.TestCase):
    """INF10-06: rule boundaries, evidence spans and stable identities."""

    def test_boundary_outcomes_and_evidence(self):
        _, result = run("boundary.yaml")
        lines = (FIXTURES / "boundary.yaml").read_text().splitlines()
        got = {f["identity"]: f for f in result["findings"]}
        # OneEnabledBucket (one enabled rule) and SuspendedBucket (noncurrent rule, versions exist) are clean.
        self.assertEqual(set(got), {
            "EmptyRetention:log-retention",
            "MarkerOnlyBucket:s3-lifecycle",
            "LateTypeRepository:ecr-lifecycle",
            "PolicyWithoutText:ecr-lifecycle",
        })
        self.assertIn("versioning is enabled", got["MarkerOnlyBucket:s3-lifecycle"]["summary"])
        self.assertIn("without LifecyclePolicyText", got["PolicyWithoutText:ecr-lifecycle"]["summary"])
        # The Type line is 8 lines below the logical ID, so only the logical-ID line is cited.
        late = got["LateTypeRepository:ecr-lifecycle"]["evidence"][0]
        self.assertEqual((late["line_start"], late["value"]), (31, lines[30]))

    def test_one_line_json_resource_cites_one_line(self):
        _, result = run_inline("t.json", '{"Resources": {"L": {"Type": "AWS::Logs::LogGroup"}}}')
        [finding] = result["findings"]
        self.assertEqual(finding["evidence"][0]["value"], '{"Resources": {"L": {"Type": "AWS::Logs::LogGroup"}}}')

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("template-positive.yaml")
        shifted = "# moved\n\n\n" + (FIXTURES / "template-positive.yaml").read_text()
        _, after = run_inline("template-positive.yaml", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )

    def test_repeated_logical_ids_across_documents_get_suffixes(self):
        doc = "Resources:\n  L:\n    Type: AWS::Logs::LogGroup\n"
        _, result = run_inline("t.yaml", doc + "---\n" + doc)
        self.assertEqual([f["identity"] for f in result["findings"]], ["L:log-retention", "L:log-retention#2"])


class Inf10ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:template-positive.yaml", "UploadsBucket:s3-lifecycle")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("template-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "  InventedBucket:"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("template-positive.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("template-positive.yaml", "cdk-synth.template.json", "boundary.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "inf10-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("template-positive.yaml"))


class Inf10CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf10_result(self):
        input_path = FIXTURES / "inf10-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["findings"]), 7)


if __name__ == "__main__":
    unittest.main()
