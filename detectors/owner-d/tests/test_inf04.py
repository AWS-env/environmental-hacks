"""Behavioral tests for the INF-04 detector (issue #171): static IaC proxy and idle-function telemetry."""

import contextlib
import copy
import datetime as dt
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

from tests.aws_fakes import NOW, ROLE, AwsTestCase, FakeCloudWatch, hourly  # noqa: E402

from owner_d import cli, inf04  # noqa: E402
from owner_d.aws import common, metrics, registry, telemetry_handler  # noqa: E402
from owner_d.inf04 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint  # noqa: E402
from shared.contracts.validation import (  # noqa: E402
    ContractError,
    fingerprint as shared_fingerprint,
    validate,
    validate_pair,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf04"
REPOSITORY_ID = "github:AWS-env/example"
ACCOUNT_ID = r"(?<!\d)\d{12}(?!\d)"


def static_source(name, content=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    return {"source_id": f"src:{name}", "scope_id": f"file:{name}", "kind": "static", "locator": name,
            "content": content}


def make_input(*names, sources=None, scope=None, context=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-inf04-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "iac"} if context is None else context,
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
TF_DIR = ("tf/main.tf", "tf/routes.tf", "tf/outputs.tf")


class Inf04StaticPositiveTests(unittest.TestCase):
    """INF04-01: billable components that nothing in the template/module uses are flagged with exact evidence."""

    # identity: (line_start, line_end, confidence, summary phrase)
    EXPECTED = {
        "template-positive.yaml": {
            "OrphanEip:unreferenced": (6, 7, "medium", "Elastic IP 'OrphanEip' is declared but nothing"),
            "IdleNat:unreferenced": (10, 11, "medium", "so no subnet sends traffic through it"),
            "PublicAlb:unreferenced": (15, 16, "medium", "no listener, Output or other resource references it"),
            "NamedNlb:unreferenced": (20, 21, "low", "explicit Name lets another stack look it up by name"),
            "DataVolume:unreferenced": (26, 27, "medium", "no VolumeAttachment"),
            "SpareEip:unreferenced": (31, 32, "low", "created only when its Condition holds"),
        },
        "cdk-synth.template.json": {
            "LogsVolume6E8A5F0C:unreferenced": (31, 32, "medium", "EBS volume 'LogsVolume6E8A5F0C'"),
        },
        "tf/main.tf": {
            "aws_eip.orphan:unreferenced": (11, 11, "medium", "it has no instance or network_interface"),
            "aws_ebs_volume.scratch:unreferenced": (32, 32, "low", "count/for_each may create no instances"),
            "aws_nat_gateway.spare:unreferenced": (38, 38, "medium", "NAT gateway aws_nat_gateway.spare"),
        },
    }

    def test_unreferenced_components_are_flagged_with_exact_lines(self):
        names = ["template-positive.yaml", "cdk-synth.template.json", *TF_DIR]
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
                evidence = finding["evidence"][0]
                self.assertEqual(evidence["line_start"], start, identity)
                self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]), identity)
                self.assertEqual(finding["confidence"], confidence, identity)
                self.assertIn(phrase, finding["summary"], identity)
                self.assertTrue(finding["references"])
        self.assertEqual(findings_for(result, "tf/routes.tf"), {})
        self.assertEqual(findings_for(result, "tf/outputs.tf"), {})

    def test_fingerprints_match_the_shared_reference_and_ignore_line_moves(self):
        _, result = run("template-positive.yaml")
        for finding in result["findings"]:
            self.assertEqual(finding["fingerprint"], shared_fingerprint(REPOSITORY_ID, CHECK_ID, finding["scope_id"],
                                                                        finding["identity"]))
        content = (FIXTURES / "template-positive.yaml").read_text()
        moved = content.replace("Resources:\n", "Resources:\n  Pad:\n    Type: AWS::SNS::Topic\n", 1)
        _, again = run_inline("template-positive.yaml", moved)
        self.assertEqual({f["fingerprint"] for f in result["findings"]}, {f["fingerprint"] for f in again["findings"]})

    def test_each_rule_on_its_own_and_the_recommendation_matches_the_component(self):
        cases = {
            "AWS::EC2::EIP": "Release the Elastic IP",
            "AWS::EC2::NatGateway": "Delete the NAT gateway",
            "AWS::ElasticLoadBalancingV2::LoadBalancer": "Delete the load balancer",
            "AWS::EC2::Volume": "Snapshot the volume",
        }
        for resource_type, recommendation in cases.items():
            with self.subTest(resource_type):
                _, result = run_inline("t.yaml", TEMPLATE + f"  Thing:\n    Type: {resource_type}\n")
                self.assertEqual([f["identity"] for f in result["findings"]], ["Thing:unreferenced"])
                self.assertIn(recommendation, result["findings"][0]["recommendation"])

    def test_a_reference_in_another_directory_does_not_count(self):
        main = 'resource "aws_eip" "spare" {\n  domain = "vpc"\n}\n'
        other = 'output "ip" {\n  value = aws_eip.spare.public_ip\n}\n'
        sources = [static_source("a/main.tf", main), static_source("b/outputs.tf", other)]
        _, result = run(sources=sources, scope=["file:a/main.tf", "file:b/outputs.tf"])
        self.assertEqual([f["identity"] for f in result["findings"]], ["aws_eip.spare:unreferenced"])
        sources = [static_source("a/main.tf", main), static_source("a/outputs.tf", other)]
        _, result = run(sources=sources, scope=["file:a/main.tf", "file:a/outputs.tf"])
        self.assertEqual(result["findings"], [])

    def test_terraform_file_judged_without_its_siblings_flags_what_they_reference(self):
        _, result = run("tf/main.tf")  # routes.tf and outputs.tf are not part of the payload
        self.assertIn("aws_nat_gateway.main:unreferenced", findings_for(result, "tf/main.tf"))
        self.assertIn("aws_lb.public:unreferenced", findings_for(result, "tf/main.tf"))


class Inf04StaticNegativeTests(unittest.TestCase):
    """INF04-02/03: associated, referenced, exported, suppressed and unresolved components are not flagged."""

    def test_used_components_are_clean_and_completed(self):
        _, result = run("template-negative.yaml", "template-exceptions.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["coverage"]["evaluated_scope"]), 2)

    def test_terraform_references_across_files_associations_count_zero_noqa_and_data_sources(self):
        _, result = run(*TF_DIR)
        flagged = set(findings_for(result, "tf/main.tf"))
        for clean in ("aws_eip.nat", "aws_nat_gateway.main", "aws_eip.web", "aws_lb.public",
                      "aws_ebs_volume.disabled", "aws_eip.standby"):
            self.assertNotIn(f"{clean}:unreferenced", flagged)

    def test_suppression_comment_above_the_resource(self):
        content = TEMPLATE + "  # noqa: INF-04\n  Spare:\n    Type: AWS::EC2::EIP\n"
        _, result = run_inline("t.yaml", content)
        self.assertEqual(result["findings"], [])
        _, result = run_inline("t.yaml", TEMPLATE + "  # noqa: INF-07\n  Spare:\n    Type: AWS::EC2::EIP\n")
        self.assertEqual(len(result["findings"]), 1)  # another check's code does not suppress INF-04

    def test_other_resource_types_and_cdk_vpc_shapes_are_not_judged(self):
        content = TEMPLATE + ("  Topic:\n    Type: AWS::SNS::Topic\n"
                              "  Group:\n    Type: AWS::ElasticLoadBalancingV2::TargetGroup\n"
                              "  Asg:\n    Type: AWS::AutoScaling::AutoScalingGroup\n    Properties:\n"
                              "      MinSize: '2'\n      MaxSize: '2'\n")
        _, result = run_inline("t.yaml", content)
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_references_through_sub_join_getatt_list_and_json_forms(self):
        forms = [
            "    Properties:\n      Value: !Sub '${Thing.PublicIp}'\n",
            "    Properties:\n      Value: !Join ['', [!Ref Thing]]\n",
            "    Properties:\n      Value: !GetAtt [Thing, PublicIp]\n",
            '    Properties:\n      Value: {"Fn::GetAtt": ["Thing", "PublicIp"]}\n',
            "    DependsOn: [Thing]\n",
        ]
        for form in forms:
            with self.subTest(form):
                content = (TEMPLATE + "  Thing:\n    Type: AWS::EC2::EIP\n"
                           "  User:\n    Type: AWS::SSM::Parameter\n" + form)
                _, result = run_inline("t.yaml", content)
                self.assertEqual(result["findings"], [])


class Inf04StaticMalformedTests(unittest.TestCase):
    """INF04-04: files the check cannot judge are limitations, never clean."""

    def assert_not_evaluated(self, name, content, phrase):
        _, result = run_inline(name, content)
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertTrue(any(phrase in note for note in result["coverage"]["limitations"]), result["coverage"])

    def test_broken_json_and_yaml(self):
        self.assert_not_evaluated("broken.json", '{"Resources": {', "could not be parsed (invalid JSON)")
        self.assert_not_evaluated("broken.yaml", TEMPLATE + "  A:\n\tType: AWS::EC2::EIP\n", "could not be parsed")

    def test_macro_transform_and_foreach_are_not_evaluated(self):
        self.assert_not_evaluated("macro.yaml", "Transform: MyMacro\n" + TEMPLATE + "  A:\n    Type: AWS::EC2::EIP\n",
                                  "macro transform MyMacro")
        self.assert_not_evaluated("each.yaml", TEMPLATE + "  Fn::ForEach::Ips:\n    - X\n    - [a]\n    - {}\n",
                                  "not evaluated")

    def test_non_template_yaml_and_unsupported_files(self):
        self.assert_not_evaluated("deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\n",
                                  "no CloudFormation Resources found")
        self.assert_not_evaluated("app.py", "print('hi')\n", "unsupported file type")

    def test_broken_terraform_makes_its_directory_not_evaluated(self):
        _, result = run("tf-broken/main.tf", "tf-broken/broken.tf")
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        notes = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tf-broken/broken.tf: could not be parsed", notes)
        self.assertIn("file:tf-broken/main.tf: not evaluated (tf-broken/broken.tf in the same Terraform", notes)

    def test_terraform_json_is_read_for_references_but_not_judged(self):
        main = 'resource "aws_eip" "spare" {\n  domain = "vpc"\n}\n'
        tfjson = json.dumps({"output": {"ip": {"value": "${aws_eip.spare.public_ip}"}}})
        sources = [static_source("m/main.tf", main), static_source("m/out.tf.json", tfjson)]
        _, result = run(sources=sources, scope=["file:m/main.tf", "file:m/out.tf.json"])
        self.assertEqual((result["status"], result["findings"]), ("partial", []))
        notes = result["coverage"]["limitations"]
        self.assertTrue(any("Terraform JSON is read only for references" in n for n in notes))

    def test_missing_duplicate_and_malformed_sources(self):
        payload = make_input("template-positive.yaml")
        payload["sources"] = []
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        payload = make_input("template-positive.yaml")
        payload["sources"].append(dict(payload["sources"][0], source_id="dup"))
        self.assertEqual(evaluate(payload)["status"], "unavailable")
        payload = make_input("template-positive.yaml")
        payload["sources"][0]["content"] = None
        self.assertEqual(evaluate(payload)["status"], "unavailable")

    def test_partial_when_one_file_fails(self):
        sources = [static_source("template-positive.yaml"), static_source("broken.json", "{")]
        _, result = run(sources=sources, scope=["file:template-positive.yaml", "file:broken.json"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["findings"]), 6)

    def test_parser_bug_is_not_clean(self):
        original = inf04._cfn_hits
        inf04._cfn_hits = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            _, result = run("template-positive.yaml")
        finally:
            inf04._cfn_hits = original
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("check failed (RuntimeError)" in n for n in result["coverage"]["limitations"]))

    def test_contract_errors(self):
        for mutate in (lambda p: p.update(detector_version="9.9.9"), lambda p: p.update(check_id="INF-07"),
                       lambda p: p.update(scope=[]), lambda p: p.update(kind="result"),
                       lambda p: p.update(sources=None), lambda p: p.pop("context")):
            payload = make_input("template-positive.yaml")
            mutate(payload)
            with self.assertRaises(EvaluationError):
                evaluate(payload)
        payload, result = run("template-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_cli_round_trip(self):
        fixture = FIXTURES / "inf04-01-positive-input.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([str(fixture)]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual((result["check_id"], result["status"], len(result["findings"])), (CHECK_ID, "completed", 6))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            self.assertEqual(cli.main([str(fixture), "-o", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text()), result)
        self.assertIs(cli.DETECTORS[CHECK_ID], inf04)


class Inf04ScannerTests(unittest.TestCase):
    """How a repository scan selects files: templates and .tf files are in scope; other YAML is declined."""

    def test_parse_selects_templates_and_terraform(self):
        from scanner.adapters.owner_d import select_by_parse

        files = [(name, (FIXTURES / name).read_text()) for name in ("template-positive.yaml", *TF_DIR)]
        files += [("deploy/k8s.yaml", "apiVersion: v1\nkind: Service\n"), ("app.py", "x = 1\n"),
                  ("bad.tf", "resource {\n")]
        selected, declined = select_by_parse(inf04, files)
        self.assertEqual([path for path, _ in selected], ["template-positive.yaml", *TF_DIR, "bad.tf"])
        self.assertEqual(sum(declined.values()), 1)
        self.assertNotEqual(getattr(inf04, "SUPPORTED_KIND", "static"), "telemetry")  # scanned, not "unavailable"
        self.assertFalse(hasattr(inf04, "REFERENCE_SETTINGS"))  # the static mode takes no settings


# -- telemetry mode ---------------------------------------------------------------------------

WINDOW_END = NOW.replace(minute=0, second=0, microsecond=0)
WINDOW_START = WINDOW_END - dt.timedelta(days=30)


def series(days_active, *, zeros_after=False, days=30):
    """Hourly Invocations Sum: datapoints only while active (Lambda publishes nothing when idle), unless
    zeros_after, which adds zero-valued datapoints for the idle hours."""
    stamps, _ = hourly(days, 0, end=WINDOW_END)
    active = int(days_active * 24)
    points = [(t, 3.0) for t in stamps[:active]]
    if zeros_after:
        points += [(t, 0.0) for t in stamps[active:]]
    return [t for t, _ in points], [v for _, v in points]


def raw(resources, data, *, start=WINDOW_START, end=WINDOW_END, complete=True, region="ap-south-1"):
    out = {"window": {"start": common.iso(start), "end": common.iso(end)}, "period_seconds": 3600, "region": region,
           "resources": [], "series": {}}
    for name in resources:
        resource = metrics.parse_resource({"type": "lambda", "name": name})
        out["resources"].append(resource)
        if name in data:
            stamps, values = data[name]
            out["series"][resource["id"]] = {"timestamps": stamps, "values": values, "complete": complete,
                                             "messages": []}
    return out


def telemetry_input(normalized, context=None):
    return {
        "schema_version": "1.0", "kind": "input", "repository_id": REPOSITORY_ID, "scan_id": "scan-inf04-t",
        "commit_sha": "d" * 40, "check_id": CHECK_ID, "detector_version": DETECTOR_VERSION,
        "context": {"min_idle_days": 14} if context is None else context,
        "scope": normalized["scope"], "sources": normalized["sources"],
    }


def evaluate_raw(raw_dict, context=None):
    normalized = inf04.normalize_invocation_metrics(raw_dict)
    payload = telemetry_input(normalized, context)
    result = evaluate(payload)
    validate_pair(payload, result)
    return normalized, result


class Inf04NormalizerTests(unittest.TestCase):
    def test_used_then_idle_function(self):
        out = inf04.normalize_invocation_metrics(raw(["orders"], {"orders": series(10)}))
        self.assertEqual(out["scope"], ["resource:lambda/orders"])
        data = out["sources"][0]["data"]
        self.assertEqual({k: data[k] for k in ("window_days", "idle_days", "datapoint_count", "total_invocations",
                                               "idle_period_datapoints", "metric", "resource_type")},
                         {"window_days": 30.0, "idle_days": 20.0, "datapoint_count": 240, "total_invocations": 720,
                          "idle_period_datapoints": 0, "metric": "invocations", "resource_type": "aws_lambda_function"})
        self.assertEqual(data["last_invoked_period"], common.iso(WINDOW_START + dt.timedelta(hours=239)))
        self.assertEqual(out["sources"][0]["locator"],
                         "cloudwatch://ap-south-1/AWS/Lambda/Invocations?FunctionName=orders&period=3600&stat=Sum")

    def test_all_zero_datapoints_and_busy_function(self):
        out = inf04.normalize_invocation_metrics(raw(["idle", "busy"], {"idle": series(0, zeros_after=True),
                                                                        "busy": series(30)}))
        idle, busy = (s["data"] for s in out["sources"])
        self.assertEqual((idle["total_invocations"], idle["idle_days"], idle["last_invoked_period"],
                          idle["idle_period_datapoints"]), (0, 30.0, None, 720))
        self.assertEqual((busy["idle_days"], busy["total_invocations"]), (0.0, 2160))

    def test_no_datapoints_and_incomplete_data_stay_in_scope_without_a_source(self):
        out = inf04.normalize_invocation_metrics(raw(["ghost", "cut"], {"ghost": ([], []), "cut": series(5)},
                                                     complete=False))
        self.assertEqual((out["scope"], out["sources"]), (["resource:lambda/ghost", "resource:lambda/cut"], []))
        out = inf04.normalize_invocation_metrics(raw(["ghost"], {"ghost": ([], [float("nan")] * 0)}))
        self.assertIn("Lambda publishes Invocations only when a function runs", out["limitations"][0])

    def test_arns_and_account_ids_are_dropped_and_non_lambda_resources_ignored(self):
        data = raw(["orders"], {"orders": series(3)})
        data["resources"][0]["dimensions"][0]["Value"] = "arn:aws:lambda:ap-south-1:123456789012:function:orders:live"
        data["resources"].append(metrics.parse_resource({"type": "ec2", "id": "i-0abc12345678def00"}))
        out = inf04.normalize_invocation_metrics(data)
        self.assertEqual(out["scope"], ["resource:lambda/orders"])
        self.assertNotRegex(json.dumps(out), ACCOUNT_ID)
        self.assertNotIn("arn:", json.dumps(out))
        self.assertEqual(inf04.function_name("arn:aws:lambda:ap-south-1:123456789012:function:a-b_c"), "a-b_c")
        self.assertIsNone(inf04.function_name("arn:aws:iam::123456789012:role/x"))
        self.assertIsNone(inf04.function_name(None))

    def test_nan_negative_and_missing_window_are_handled(self):
        stamps, values = series(30)
        values = [float("nan") if i % 2 else v for i, v in enumerate(values)]
        values[0] = -1.0
        out = inf04.normalize_invocation_metrics(raw(["f"], {"f": (stamps, values)}))
        self.assertEqual(out["sources"][0]["data"]["datapoint_count"], 359)
        broken = raw(["f"], {"f": series(30)})
        broken["window"] = None
        out = inf04.normalize_invocation_metrics(broken)
        self.assertEqual(out["sources"], [])
        self.assertIn("window is unknown", out["limitations"][0])


class Inf04TelemetryEvaluationTests(unittest.TestCase):
    def test_idle_function_is_flagged_with_cited_evidence(self):
        _, result = evaluate_raw(raw(["orders", "busy"], {"orders": series(10), "busy": series(30)}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["resource:lambda/orders", "resource:lambda/busy"])
        [finding] = result["findings"]
        self.assertEqual((finding["scope_id"], finding["identity"], finding["confidence"]),
                         ("resource:lambda/orders", "no-invocations", "low"))
        self.assertIn("no invocations in the last 20 days of the 30-day window", finding["summary"])
        self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, "resource:lambda/orders",
                                                             "no-invocations"))
        self.assertEqual({e["field"]: e["value"] for e in finding["evidence"]},
                         {"total_invocations": 720, "idle_days": 20.0, "window_days": 30.0, "datapoint_count": 240,
                          "last_invoked_period": common.iso(WINDOW_START + dt.timedelta(hours=239))})

    def test_zero_valued_datapoints_raise_confidence(self):
        _, result = evaluate_raw(raw(["idle"], {"idle": series(0, zeros_after=True)}))
        [finding] = result["findings"]
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("reported 0 invocations", finding["summary"])
        self.assertNotIn("last_invoked_period", {e["field"] for e in finding["evidence"]})

    def test_idle_boundary(self):
        _, result = evaluate_raw(raw(["f"], {"f": series(16)}))  # idle exactly 14 days
        self.assertEqual(len(result["findings"]), 1)
        _, result = evaluate_raw(raw(["f"], {"f": series(16.5)}))  # idle 13.5 days
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        _, result = evaluate_raw(raw(["f"], {"f": series(16.5)}), context={"min_idle_days": 7})
        self.assertEqual(len(result["findings"]), 1)

    def test_short_window_is_unavailable_never_clean(self):
        data = raw(["f"], {"f": series(0, zeros_after=True)}, start=WINDOW_END - dt.timedelta(days=7))
        _, result = evaluate_raw(data)
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertTrue(any("telemetry window 7 days is below the required 14 days" in n
                            for n in result["coverage"]["limitations"]))

    def test_function_without_datapoints_is_unavailable_never_clean(self):
        normalized, result = evaluate_raw(raw(["ghost", "busy"], {"busy": series(30)}))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["resource:lambda/busy"])
        self.assertTrue(any("resource:lambda/ghost: no telemetry source supplied" in n
                            for n in result["coverage"]["limitations"]))
        _, result = evaluate_raw(raw(["ghost"], {}))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))

    def test_settings_default_and_invalid_values(self):
        _, result = evaluate_raw(raw(["f"], {"f": series(10)}), context={})
        self.assertEqual(len(result["findings"]), 1)  # min_idle_days defaults to 14
        for bad in ("x", 0, -3, True, None):
            _, result = evaluate_raw(raw(["f"], {"f": series(10)}), context={"min_idle_days": bad})
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []), bad)

    def test_malformed_telemetry_data_is_not_evaluated(self):
        normalized = inf04.normalize_invocation_metrics(raw(["f"], {"f": series(10)}))
        mutations = [
            lambda d: d.pop("idle_days"), lambda d: d.update(metric="cpu_utilization"),
            lambda d: d.update(idle_days=40.0), lambda d: d.update(datapoint_count=0),
            lambda d: d.update(total_invocations="many"), lambda d: d.update(resource_id="lambda/other"),
            lambda d: d.update(idle_period_datapoints=999), lambda d: d.update(last_invoked_period=None),
            lambda d: d.update(window_days=float("inf")),
        ]
        for mutate in mutations:
            broken = copy.deepcopy(normalized)
            mutate(broken["sources"][0]["data"])
            payload = telemetry_input(broken)
            result = evaluate(payload)
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        broken = copy.deepcopy(normalized)
        broken["sources"][0]["data"] = "nope"
        self.assertEqual(evaluate(telemetry_input(broken))["status"], "unavailable")
        broken = copy.deepcopy(normalized)
        broken["sources"].append(dict(broken["sources"][0], source_id="dup"))
        self.assertEqual(evaluate(telemetry_input(broken))["status"], "unavailable")


class Inf04TelemetryRouteTests(AwsTestCase):
    """owner-d-telemetry-analyzer collects Invocations Sum for listed functions, normalizes and publishes."""

    def use(self, data):
        # FakeCloudWatch serves non-Average stats (here Sum) from the second series of each entry.
        self.fakes["cloudwatch"] = FakeCloudWatch({(name,): (([], []), points) for name, points in data.items()})
        common._clients.clear()
        return self.fakes["cloudwatch"]

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(self.base_event(checks=["INF-04"], **extra))

    def test_registry_wiring(self):
        [check] = [c for c in registry.CHECKS if c.check_id == CHECK_ID]
        module, normalize = registry.load(check)
        self.assertIs(module, inf04)
        self.assertIs(normalize, inf04.normalize_invocation_metrics)
        self.assertEqual(registry.settings(check, module, {}), {"min_idle_days": 14})
        self.assertIn("invocation_metrics", telemetry_handler.COLLECTORS)

    def test_idle_function_is_published_without_account_ids(self):
        cw = self.use({"orders": series(10), "busy": series(30)})
        out = self.run_event(role_arn=ROLE, resources=[
            {"type": "lambda", "name": "arn:aws:lambda:ap-south-1:123456789012:function:orders"},
            {"type": "lambda", "name": "busy"}, {"type": "ec2", "id": "i-0abc12345678def00"}])
        self.assertEqual(out["results"], [{"check_id": "INF-04", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 1}])
        [call] = [kw for name, kw in cw.calls if name == "get_metric_data"]
        queries = call["MetricDataQueries"]
        self.assertEqual([q["MetricStat"]["Metric"]["Dimensions"][0]["Value"] for q in queries], ["orders", "busy"])
        self.assertEqual({(q["MetricStat"]["Metric"]["Namespace"], q["MetricStat"]["Metric"]["MetricName"],
                           q["MetricStat"]["Stat"], q["MetricStat"]["Period"]) for q in queries},
                         {("AWS/Lambda", "Invocations", "Sum", 3600)})
        self.assertEqual(call["EndTime"] - call["StartTime"], dt.timedelta(days=30))
        [entry] = self.fakes["events"].entries
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertNotRegex(entry["Detail"], ACCOUNT_ID)
        self.assertNotIn("arn:", entry["Detail"])
        self.assertEqual(result["findings"][0]["scope_id"], "resource:lambda/orders")
        self.assertEqual(result["context"]["min_idle_days"], 14)
        self.assertEqual(result["context"]["collection"]["metric"], "AWS/Lambda Invocations")

    def test_without_listed_functions_nothing_is_read_or_published(self):
        cw = self.use({})
        out = self.run_event(discover={}, resources=[{"type": "ec2", "id": "i-0abc12345678def00"}])
        self.assertEqual((out["published"], out["results"]), (0, []))
        self.assertEqual(out["skipped"][0]["check_id"], "INF-04")
        self.assertIn("cannot discover idle functions", out["skipped"][0]["limitations"][0])
        self.assertEqual(cw.calls, [])

    def test_unlisted_function_without_datapoints_is_unavailable(self):
        self.use({})
        out = self.run_event(resources=[{"type": "lambda", "name": "ghost"}], dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertTrue(any("does not exist under this name" in n for n in result["coverage"]["limitations"]))

    def test_short_window_and_settings_override(self):
        self.use({"orders": series(0, zeros_after=True)})
        out = self.run_event(resources=[{"type": "lambda", "name": "orders"}], invocations={"lookback_days": 7},
                             dry_run=True)
        self.assertEqual(out["result_payloads"][0]["status"], "unavailable")
        out = self.run_event(resources=[{"type": "lambda", "name": "orders"}], invocations={"lookback_days": 7},
                             settings={"INF-04": {"min_idle_days": 5}}, dry_run=True)
        self.assertEqual(out["results"][0]["findings"], 1)

    def test_truncated_pages_are_not_evaluated(self):
        self.fakes["cloudwatch"] = FakeCloudWatch({("orders",): (([], []), series(10))}, data_pages=20)
        common._clients.clear()
        out = self.run_event(resources=[{"type": "lambda", "name": "orders"}], dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("incomplete Invocations data" in n for n in result["coverage"]["limitations"]))
        self.assertTrue(out["collection"]["INF-04"]["truncated"])

    def test_event_validation(self):
        self.use({})
        lam = [{"type": "lambda", "name": "orders"}]
        for event in ({"resources": lam, "invocations": {"lookback_days": 61}},
                      {"resources": lam, "invocations": {"period_seconds": 300}},
                      {"resources": lam, "invocations": {"period_seconds": 5400}},
                      {"resources": lam, "invocations": "30d"},
                      {"resources": [{"type": "lambda", "name": "bad name!"}]},
                      {"resources": "orders"}):
            with self.assertRaises(ValueError, msg=event):
                self.run_event(**event)

    def test_probe_collects_only(self):
        self.use({"orders": series(10)})
        out = telemetry_handler.lambda_handler({"probe": ["invocation_metrics"],
                                                "resources": [{"type": "lambda", "name": "orders"}]})
        self.assertEqual(out["probe"]["invocation_metrics"]["resources"], 1)
        self.assertEqual(self.fakes["events"].entries, [])


if __name__ == "__main__":
    unittest.main()
