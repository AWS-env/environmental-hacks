"""Behavioral tests for the INF-07 detector (issue #174)."""

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
from owner_d.inf07 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf07"
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
        "scan_id": "scan-inf07-001",
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


def identities(result):
    return [f["identity"] for f in result["findings"]]


TEMPLATE = 'AWSTemplateFormatVersion: "2010-09-09"\nResources:\n'


def instance(instance_type, name="Web"):
    return TEMPLATE + f"  {name}:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: {instance_type}\n"


class Inf07PositiveTests(unittest.TestCase):
    """INF07-01: older / x86 families with a more efficient equivalent are flagged with exact evidence."""

    # identity: (line_start, line_end, confidence, summary phrase)
    EXPECTED = {
        "template-positive.yaml": {
            "Bastion:InstanceType": (7, 7, "low", "t2.micro (Default of parameter 'BastionType'"),
            "WebServer:InstanceType": (13, 13, "medium", "m4.large, an older-generation m4 family (newer "
                                                         "generation: m7i, or m7g on AWS Graviton)"),
            "WorkerTemplate:LaunchTemplateData.InstanceType": (23, 23, "medium", "c4.xlarge, an older-generation"),
            "WorkerGroup:MixedInstancesPolicy.LaunchTemplate.Overrides": (36, 36, "low", "m5.large, an x86 m5 "
                                                                          "family with an AWS Graviton (Arm) "
                                                                          "equivalent (m7g)"),
            "Nodes:InstanceTypes": (43, 43, "low", "equivalent (t4g)"),
            "OrdersDb:DBInstanceClass": (48, 48, "medium", "db.r6i, or db.r7g on AWS Graviton"),
            "ReportsDb:DBInstanceClass": (53, 53, "low", "equivalent (db.m7g)"),
            "SessionCache:CacheNodeType": (59, 59, "low", "cache.t3, or cache.t4g on AWS Graviton"),
            "SearchDomain:ClusterConfig.InstanceType": (64, 64, "medium", "m4.large.search, an older-generation"),
            "ResizeFunction:Architectures": (66, 67, "medium", "declares no Architectures, so it runs on the "
                                                               "x86_64 default"),
            "ApiFunction:Architectures": (80, 81, "low", "declares Architectures [x86_64], although the "
                                                         "nodejs20.x runtime"),
        },
        "cdk-synth.template.json": {
            "Handler886CB40B:Architectures": (15, 16, "medium", "x86_64 default"),
            "BuildHost3F1D3A2B:InstanceType": (51, 51, "low", "equivalent (t4g)"),
        },
        "sam-globals.yaml": {
            "IngestFunction:Architectures": (7, 7, "low", "[x86_64] in SAM Globals"),
        },
    }

    def test_older_and_x86_families_are_flagged_with_exact_lines(self):
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

    def test_recommendation_matches_the_kind_of_finding(self):
        findings = findings_for(run("template-positive.yaml")[1], "template-positive.yaml")
        self.assertIn("newer generation named in the finding", findings["WebServer:InstanceType"]["recommendation"])
        self.assertIn("Graviton equivalent named in the finding",
                      findings["ReportsDb:DBInstanceClass"]["recommendation"])
        self.assertIn("Architectures: [arm64]", findings["ApiFunction:Architectures"]["recommendation"])

    def test_cdk_framework_function_is_not_flagged(self):
        findings = findings_for(run("cdk-synth.template.json")[1], "cdk-synth.template.json")
        self.assertNotIn("CustomS3AutoDeleteObjectsCustomResourceProviderHandler9D90184F:Architectures", findings)


class Inf07NegativeTests(unittest.TestCase):
    """INF07-02: current/Graviton families, unresolved intrinsics and unjudged families are clean."""

    def test_current_families_and_intrinsics_are_clean(self):
        _, result = run("template-negative.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-negative.yaml"])
        self.assertEqual(result["findings"], [])

    def test_changing_the_declaration_restores_the_finding(self):
        """Guards the negatives against passing for the wrong reason (e.g. a parse quirk)."""
        content = (FIXTURES / "template-negative.yaml").read_text()
        for old, new, identity in (
            ("      InstanceType: m7g.large\n", "      InstanceType: m5.large\n", "WebServer:InstanceType"),
            ("    Default: m7g.large\n", "    Default: c4.large\n", "FromParameter:InstanceType"),
            ("      DBInstanceClass: db.r7g.large\n", "      DBInstanceClass: db.r5.large\n", "Db:DBInstanceClass"),
            ("        DedicatedMasterEnabled: false\n", "        DedicatedMasterEnabled: true\n",
             "Search:ClusterConfig.DedicatedMasterType"),
            ("      Architectures: [arm64]\n", "      Architectures: [x86_64]\n", "ArmFunction:Architectures"),
            ("    Default: arm64\n", "    Default: x86_64\n", None),  # Architectures Refs are not resolved
            ("    Architectures:\n      - arm64\n", "    MemorySize: 256\n", "GlobalsArmFunction:Architectures"),
        ):
            with self.subTest(old=old):
                self.assertIn(old, content)
                _, result = run_inline("template-negative.yaml", content.replace(old, new, 1))
                self.assertEqual(identities(result), [identity] if identity else [])

    def test_template_without_compute_resources_is_clean(self):
        content = TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n"
        _, result = run_inline("app.yaml", content)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Inf07ExceptionTests(unittest.TestCase):
    """INF07-03: `# noqa: INF-07`, runtimes/engines without an arm64 build and CDK framework functions."""

    def test_exceptions_and_noqa(self):
        _, result = run("template-exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["OtherCode:InstanceType"])

    def test_removing_noqa_restores_findings(self):
        content = (FIXTURES / "template-exceptions.yaml").read_text()
        content = content.replace("  # noqa: INF-07\n", "").replace("  # noqa: INF-07 (x86-only monitoring agent)", "")
        _, result = run_inline("template-exceptions.yaml", content)
        self.assertEqual(identities(result),
                         ["LicensedAppliance:InstanceType", "LegacyAgent:InstanceType", "OtherCode:InstanceType"])

    def test_graviton_engine_and_runtime_boundaries(self):
        for content, expected in (
            (TEMPLATE + "  D:\n    Type: AWS::RDS::DBInstance\n    Properties:\n      Engine: oracle-ee\n"
                        "      DBInstanceClass: db.m4.large\n", ["D:DBInstanceClass"]),
            (TEMPLATE + "  D:\n    Type: AWS::RDS::DBInstance\n    Properties:\n      Engine: MariaDB\n"
                        "      DBInstanceClass: db.t3.micro\n", ["D:DBInstanceClass"]),
            (TEMPLATE + "  D:\n    Type: AWS::RDS::DBInstance\n    Properties:\n      Engine: !Ref Engine\n"
                        "      DBInstanceClass: db.t3.micro\n", []),
            (TEMPLATE + "  F:\n    Type: AWS::Lambda::Function\n    Properties:\n      Runtime: java8.al2\n",
             ["F:Architectures"]),
            (TEMPLATE + "  F:\n    Type: AWS::Lambda::Function\n    Properties:\n      Runtime: nodejs10.x\n", []),
        ):
            with self.subTest(content=content.splitlines()[-1]):
                _, result = run_inline("t.yaml", content)
                self.assertEqual(identities(result), expected)


class Inf07IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF07-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:template.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_unsupported_and_macro_files_make_result_partial(self):
        """INF07-05: invalid JSON, Terraform, Kubernetes manifests and macros are never reported clean."""
        _, result = run("template-positive.yaml", "broken.json", "main.tf", "deployment.yaml", "macro.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-positive.yaml"])
        self.assertTrue(all(f["scope_id"] == "file:template-positive.yaml" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.json: could not be parsed (invalid JSON)", limitations)
        self.assertIn("file:main.tf: unsupported file type; INF-07 v1.0.0 supports CloudFormation/SAM", limitations)
        self.assertIn("file:deployment.yaml: not evaluated (no CloudFormation Resources found)", limitations)
        self.assertIn("file:macro.yaml: not evaluated (template uses the macro transform CompanyDefaults", limitations)
        self.assertIn("blocked on OQ-7", limitations)

    def test_unparseable_and_rewriting_templates_are_unavailable(self):
        for name, content, reason in (
            ("t.yaml", TEMPLATE + "  W:\n\tType: AWS::EC2::Instance\n", "tab indentation"),
            ("t.yaml", TEMPLATE + "  W: {Type: AWS::EC2::Instance\n", "could not be parsed"),
            ("t.json", '{"AWSTemplateFormatVersion": "2010-09-09"}', "no CloudFormation Resources found"),
            ("t.yaml", TEMPLATE + "  W:\n    Type: AWS::EC2::Instance\n    Properties:\n      Fn::Transform:\n"
                                  "        Name: AWS::Include\n", "Fn::Transform/AWS::Include or Fn::ForEach on line 6"),
            ("t.yaml", "Transform: AWS::LanguageExtensions\nResources:\n  Fn::ForEach::Hosts:\n    - Name\n"
                       "    - [a, b]\n    - Host${Name}: {Type: AWS::EC2::Instance}\n", "Fn::ForEach on line 3"),
            ("stack.ts", "new ec2.Instance(this, 'W', {instanceType: new ec2.InstanceType('m4.large')});\n",
             "unsupported file type"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])


class Inf07BoundaryTests(unittest.TestCase):
    """INF07-06: confidence tiers, engine boundaries, list evidence and stable identities."""

    def test_boundary_outcomes_and_evidence(self):
        _, result = run("boundary.yaml")
        lines = (FIXTURES / "boundary.yaml").read_text().splitlines()
        got = {f["identity"]: f for f in result["findings"]}
        self.assertEqual({identity: f["confidence"] for identity, f in got.items()}, {
            "FreeTierHost:InstanceType": "low",
            "SqlServerDb:DBInstanceClass": "medium",
            "ImageFunction:Architectures": "low",
            "CustomRuntimeFunction:Architectures": "low",
            "BatchCompute:ComputeResources.InstanceTypes": "medium",
            "SearchMasters:ClusterConfig.DedicatedMasterType": "low",
        })
        # SQL Server has no Graviton classes: only the x86 successor is suggested.
        self.assertIn("(newer generation: db.m6i)", got["SqlServerDb:DBInstanceClass"]["summary"])
        self.assertIn("container image can be rebuilt for arm64", got["ImageFunction:Architectures"]["summary"])
        # One finding per list; evidence is the first flagged item (`optimal` and `c6g` are skipped).
        batch = got["BatchCompute:ComputeResources.InstanceTypes"]
        self.assertEqual((batch["evidence"][0]["line_start"], batch["evidence"][0]["value"]), (36, lines[35]))
        self.assertIn("c4, an older-generation c4 family", batch["summary"])
        self.assertIn("r5.2xlarge, an x86 r5 family", batch["summary"])
        self.assertNotIn("optimal", batch["summary"])

    def test_instance_type_boundaries(self):
        for instance_type, expected in (
            ("m4.large", "medium"), ("c3.8xlarge", "medium"), ("t1.micro", "low"), ("t3a.nano", "low"),
            ("c5n.18xlarge", "low"), ("r5ad.metal", "low"), ("m4", None), ("m6i.large", None),
            ("m7g.medium", None), ("t4g.micro", None), ("r5b.large", None), ("db.m4.large", None),
            ('"m4.large"', "medium"), ("~", None),
        ):
            with self.subTest(instance_type=instance_type):
                _, result = run_inline("t.yaml", instance(instance_type))
                self.assertEqual(result["status"], "completed")
                self.assertEqual([f["confidence"] for f in result["findings"]], [expected] if expected else [])

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
        _, result = run_inline("t.yaml", instance("m4.large") + "---\n" + instance("c4.large"))
        self.assertEqual(identities(result), ["Web:InstanceType", "Web:InstanceType#2"])


class Inf07ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:template-positive.yaml", "WebServer:InstanceType")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("template-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "      InstanceType: m1.small"
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
        committed = json.loads((FIXTURES / "inf07-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("template-positive.yaml"))


class Inf07CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf07_result(self):
        input_path = FIXTURES / "inf07-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["findings"]), 11)


if __name__ == "__main__":
    unittest.main()
