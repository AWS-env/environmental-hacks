"""Behavioral tests for the INF-06 detector (issue #173)."""

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

from owner_d import cli, inf06
from owner_d.inf06 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from owner_d.textstatic import NotEvaluated, Unsupported
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf06"
REPOSITORY_ID = "github:AWS-env/example"
POSITIVE = ("sam-globals.yaml", "cdk-synth.template.json", "serverless.yml")
IDENTITY = "lambda-functions:uniform-sizing"


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
        "scan_id": "scan-inf06-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "iac"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, extra=()):
    """Evaluate fixture files plus inline (name, content) sources."""
    sources = [static_source(name) for name in names] + [static_source(name, content) for name, content in extra]
    payload = make_input(sources=sources, scope=[source["scope_id"] for source in sources])
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def inline(content, name="template.yaml"):
    return run(extra=[(name, content)])[1]


def limitations(result):
    return " ".join(result["coverage"]["limitations"])


SAM_HEAD = 'AWSTemplateFormatVersion: "2010-09-09"\nTransform: AWS::Serverless-2016-10-31\n'


def sam_function(name, event_type, sizing="", events_extra=""):
    """A SAM function with one event of `event_type` (or none) and optional sizing lines."""
    body = "".join(f"      {line}\n" for line in sizing.splitlines())
    events = ""
    if event_type:
        events = f"      Events:\n        Trigger:\n          Type: {event_type}\n" + events_extra
    return f"  {name}:\n    Type: AWS::Serverless::Function\n    Properties:\n      Handler: h.handler\n" + body + events


def sam(functions, globals_="MemorySize: 1024\nTimeout: 30\n", params=""):
    head = SAM_HEAD + params
    if globals_:
        head += "Globals:\n  Function:\n" + "".join(f"    {line}\n" for line in globals_.splitlines())
    return head + "Resources:\n" + "".join(functions)


THREE = [sam_function("ApiFn", "Api"), sam_function("QueueFn", "SQS"), sam_function("JobFn", "Schedule")]


class Inf06PositiveTests(unittest.TestCase):
    """INF06-01: three workload kinds with identical declared sizing are flagged once per template."""

    EXPECTED = {
        "sam-globals.yaml": (7, "    MemorySize: 1024\n    Timeout: 30\n    Architectures: [arm64]"),
        "cdk-synth.template.json": (8, '        "MemorySize": 512,\n        "Timeout": 60'),
        "serverless.yml": (6, "  memorySize: 2048\n  timeout: 900"),
    }

    def test_findings_have_exact_evidence_and_coverage(self):
        payload, result = run(*POSITIVE)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["measurements"], [])
        self.assertEqual(len(result["findings"]), 3)
        for finding in result["findings"]:
            name = finding["scope_id"][len("file:"):]
            line, value = self.EXPECTED[name]
            self.assertEqual(finding["identity"], IDENTITY)
            self.assertEqual(finding["confidence"], "low")
            evidence = finding["evidence"][0]
            self.assertEqual((evidence["locator"], evidence["line_start"], evidence["value"]), (name, line, value))
            self.assertEqual(finding["recommendation"], inf06.RECOMMENDATION)

    def test_summaries_name_the_kinds_the_sizing_and_the_claim_boundary(self):
        _, result = run(*POSITIVE)
        summaries = {f["scope_id"]: f["summary"] for f in result["findings"]}
        sam_summary = summaries["file:sam-globals.yaml"]
        self.assertIn("MemorySize 1024, Timeout 30, Architectures arm64", sam_summary)
        self.assertIn("SAM Globals.Function", sam_summary)
        self.assertIn("API request/response: ApiFunction", sam_summary)
        self.assertIn("asynchronous event/queue consumer: QueueConsumer", sam_summary)
        self.assertIn("scheduled job: NightlyReport", sam_summary)
        self.assertNotIn("SeedData", sam_summary)  # no trigger: not a counted workload
        self.assertIn("candidate for reviewer confirmation", sam_summary)
        self.assertIn("does not show that any function is mis-sized", sam_summary)
        cdk = summaries["file:cdk-synth.template.json"]
        self.assertIn("declared function by function", cdk)
        self.assertIn("x86_64 (platform default)", cdk)
        self.assertIn("event/queue consumer: StreamWorker1A2B3C4D", cdk)  # through the alias
        self.assertIn("Serverless provider", summaries["file:serverless.yml"])
        self.assertIn("scheduled job: purge", summaries["file:serverless.yml"])

    def test_same_parameter_and_same_variable_count_as_uniform(self):
        params = "Parameters:\n  Mem:\n    Type: Number\n    Default: 512\n"
        functions = [sam_function("ApiFn", "HttpApi", "MemorySize: !Ref Mem"),
                     sam_function("QueueFn", "DynamoDB", "MemorySize: !Ref Mem"),
                     sam_function("JobFn", "ScheduleV2", "MemorySize:\n  Ref: Mem")]
        result = inline(sam(functions, globals_="", params=params))
        self.assertEqual(len(result["findings"]), 1)
        self.assertIn("MemorySize parameter 'Mem'", result["findings"][0]["summary"])
        self.assertIn("declared function by function", result["findings"][0]["summary"])
        service = ("provider:\n  name: aws\n  memorySize: ${self:custom.memory}\nfunctions:\n"
                   "  a:\n    handler: a.h\n    url: true\n  b:\n    handler: b.h\n    events:\n      - s3: uploads\n"
                   "  c:\n    handler: c.h\n    events:\n      - schedule: rate(1 hour)\n")
        result = inline(service, "serverless.yaml")
        self.assertEqual(len(result["findings"]), 1)
        self.assertIn("${self:custom.memory}", result["findings"][0]["summary"])

    def test_many_functions_are_listed_with_a_count(self):
        functions = [sam_function(f"Api{i}", "Api") for i in range(6)] + THREE[1:]
        result = inline(sam(functions))
        self.assertIn("8 Lambda functions", result["findings"][0]["summary"])
        self.assertIn("Api0, Api1, Api2, Api3 +2 more", result["findings"][0]["summary"])


class Inf06NegativeTests(unittest.TestCase):
    """INF06-02: per-workload sizing, missing kinds and pure platform defaults are clean."""

    def test_workloads_sized_per_kind_are_clean(self):
        _, result = run("template-negative.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_two_kinds_are_not_enough(self):
        functions = [sam_function("ApiFn", "Api"), sam_function("ApiFn2", "HttpApi"), sam_function("QueueFn", "SQS")]
        self.assertEqual(inline(sam(functions))["findings"], [])
        functions = [sam_function("QueueFn", "SQS"), sam_function("TopicFn", "SNS"), sam_function("JobFn", "Schedule")]
        self.assertEqual(inline(sam(functions))["findings"], [])

    def test_pure_platform_defaults_are_not_judged(self):
        result = inline(sam(THREE, globals_="Runtime: python3.12\n"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_one_differing_dimension_is_clean(self):
        functions = [sam_function("ApiFn", "Api"), sam_function("QueueFn", "SQS"),
                     sam_function("JobFn", "Schedule", "Timeout: 900")]
        self.assertEqual(inline(sam(functions))["findings"], [])
        functions = [sam_function("ApiFn", "Api"), sam_function("QueueFn", "SQS"),
                     sam_function("JobFn", "Schedule", "Architectures: [arm64]")]
        self.assertEqual(inline(sam(functions))["findings"], [])

    def test_non_lambda_compute_is_not_judged(self):
        template = ('AWSTemplateFormatVersion: "2010-09-09"\nResources:\n'
                    "  A:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m7g.large\n"
                    "  B:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m7g.large\n")
        self.assertEqual(inline(template)["findings"], [])


class Inf06ExceptionTests(unittest.TestCase):
    """INF06-03: noqa, mixed-trigger functions and unresolved sizing are not flagged."""

    def test_noqa_above_the_shared_block_or_on_the_cited_line(self):
        text = sam(THREE).replace("Globals:\n  Function:\n", "Globals:\n  # noqa: INF-06 one tier by design\n  Function:\n")
        self.assertEqual(inline(text)["findings"], [])
        text = sam(THREE).replace("    MemorySize: 1024\n", "    MemorySize: 1024  # noqa: INF06\n")
        self.assertEqual(inline(text)["findings"], [])
        text = sam(THREE).replace("    MemorySize: 1024\n", "    MemorySize: 1024  # noqa: INF-07\n")
        self.assertEqual(len(inline(text)["findings"]), 1)

    def test_function_with_triggers_of_two_kinds_is_not_counted(self):
        warmed = sam_function("ApiFn", "Api") + "        Warmer:\n          Type: Schedule\n"
        functions = [warmed, sam_function("QueueFn", "SQS"), sam_function("JobFn", "Schedule")]
        self.assertEqual(inline(sam(functions))["findings"], [])  # no single-kind API handler left

    def test_unresolved_sizing_blocks_the_judgement(self):
        functions = [sam_function("ApiFn", "Api", "MemorySize: !If [IsProd, 2048, 1024]"), *THREE[1:]]
        self.assertEqual(inline(sam(functions))["findings"], [])
        params = "Parameters:\n  Mem:\n    Type: Number\n    Default: 1024\n"
        functions = [sam_function("ApiFn", "Api", "MemorySize: !Ref Mem"), *THREE[1:]]
        self.assertEqual(inline(sam(functions, params=params))["findings"], [])  # parameter vs literal 1024
        for value in ("!FindInMap [Sizes, Default, Mem]", "!Sub '${Mem}'", "!If [IsProd, 1024, 1024]"):
            with self.subTest(value=value):  # identical but unresolved intrinsics are not treated as uniform
                result = inline(sam(THREE, globals_=f"MemorySize: {value}\nTimeout: 30\n"))
                self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_intrinsic_properties_block_the_judgement(self):
        functions = [THREE[0], THREE[1], "  JobFn:\n    Type: AWS::Lambda::Function\n    Properties:\n"
                     "      Fn::If: [IsProd, {MemorySize: 1024}, {MemorySize: 128}]\n",
                     "  JobRule:\n    Type: AWS::Events::Rule\n    Properties:\n      ScheduleExpression: rate(1 day)\n"
                     "      Targets:\n        - Arn: !GetAtt JobFn.Arn\n          Id: t\n"]
        self.assertEqual(inline(sam(functions))["findings"], [])


class Inf06IncompleteTests(unittest.TestCase):
    """INF06-04/05: missing evidence and malformed or unsupported input are never reported clean."""

    def test_missing_source_is_partial_or_unavailable(self):
        payload = make_input("sam-globals.yaml")
        payload["scope"].append("file:missing.yaml")
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertIn("file:missing.yaml: no static source supplied", limitations(result))
        payload = make_input(sources=[], scope=["file:missing.yaml"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))

    def test_malformed_inputs_are_never_clean(self):
        cases = {
            "broken.yaml": "could not be parsed",
            "broken.json": "could not be parsed (invalid JSON)",
            "serverless-broken.yml": "could not be parsed",
            "macro.yaml": "macro transform",
            "serverless.yml": "no Serverless Framework service",
            "deployment.yaml": "no CloudFormation Resources found",
            "app.py": "unsupported file type",
        }
        contents = {  # inline, so a repository scan of this repo does not pick up extra broken files
            "broken.yaml": SAM_HEAD + "Resources:\n  ApiFn:\n    Type: AWS::Serverless::Function\n    Properties: [x\n",
            "broken.json": '{"Resources": {',
            "serverless-broken.yml": "provider:\n  name: aws\nfunctions: {a: [\n",
            "macro.yaml": "Transform: AWS::SomeMacro\nResources:\n  A:\n    Type: AWS::Lambda::Function\n",
            "serverless.yml": "provider:\n  name: google\nfunctions:\n  a:\n    handler: a.h\n",
            "deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n",
            "app.py": "print('hi')\n",
        }
        for name, reason in cases.items():
            with self.subTest(name=name):
                _, result = run(extra=[(name, contents[name])])
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, limitations(result))

    def test_parse_declines_unrelated_files_for_the_scanner(self):
        with self.assertRaises(Unsupported):
            inf06.parse("src/app.py", "x = 1\n")
        with self.assertRaises(NotEvaluated):
            inf06.parse("k8s/deploy.yaml", "apiVersion: v1\nkind: ConfigMap\n")
        with self.assertRaises(NotEvaluated):
            inf06.parse("serverless.yml", "service: x\nprovider:\n  name: aws\nfunctions: ${file(fns.yml)}\n")


class Inf06BoundaryTests(unittest.TestCase):
    """INF06-06: the minimum workload count, shared-versus-own evidence and identity stability."""

    def test_exactly_one_function_per_kind_is_the_minimum(self):
        self.assertEqual(len(inline(sam(THREE))["findings"]), 1)
        self.assertEqual(inline(sam(THREE[:2]))["findings"], [])

    def test_untriggered_functions_do_not_count_or_block(self):
        functions = [*THREE, sam_function("Seeder", None, "MemorySize: 128\nTimeout: 3")]
        self.assertEqual(len(inline(sam(functions))["findings"]), 1)

    def test_mixed_shared_and_own_sizing_cites_the_first_own_declaration(self):
        functions = [sam_function("ApiFn", "Api", "MemorySize: 1024"),
                     sam_function("QueueFn", "SQS", "MemorySize: 1024\nTimeout: 30"),
                     sam_function("JobFn", "Schedule", "MemorySize: 1024")]
        result = inline(sam(functions, globals_="Timeout: 30\n"))
        finding = result["findings"][0]
        self.assertEqual(finding["evidence"][0]["value"], "      MemorySize: 1024")
        self.assertEqual(finding["evidence"][0]["line_start"], 11)  # ApiFn, the first counted function
        self.assertIn("partly from the shared defaults block", finding["summary"])
        functions[0] = sam_function("ApiFn", "Api")  # now takes the default 128 MB: no longer uniform
        self.assertEqual(inline(sam(functions, globals_="Timeout: 30\n"))["findings"], [])

    def test_plain_cloudformation_triggers_are_recognised(self):
        template = ('AWSTemplateFormatVersion: "2010-09-09"\nResources:\n'
                    + "".join(f"  {n}:\n    Type: AWS::Lambda::Function\n    Properties:\n      MemorySize: 256\n"
                              for n in ("UrlFn", "TopicFn", "CronFn"))
                    + "  Url:\n    Type: AWS::Lambda::Url\n    Properties:\n      TargetFunctionArn: !Ref UrlFn\n"
                    "      AuthType: NONE\n"
                    "  Sub:\n    Type: AWS::SNS::Subscription\n    Properties:\n      Protocol: lambda\n"
                    "      Endpoint: !Sub ${TopicFn.Arn}\n"
                    "  Cron:\n    Type: AWS::Scheduler::Schedule\n    Properties:\n      ScheduleExpression: rate(1 day)\n"
                    "      Target:\n        Arn: !GetAtt CronFn.Arn\n")
        summary = inline(template)["findings"][0]["summary"]
        self.assertIn("API request/response: UrlFn", summary)
        self.assertIn("event/queue consumer: TopicFn", summary)
        self.assertIn("scheduled job: CronFn", summary)
        events_rule_pattern = template.replace("Type: AWS::Scheduler::Schedule", "Type: AWS::Events::Rule").replace(
            "      ScheduleExpression: rate(1 day)\n      Target:\n        Arn: !GetAtt CronFn.Arn\n",
            "      EventPattern: {source: [aws.ec2]}\n      Targets:\n        - Arn: !GetAtt CronFn.Arn\n")
        self.assertEqual(inline(events_rule_pattern)["findings"], [])  # an EventBridge pattern is an event, not a job

    def test_fingerprints_do_not_change_when_lines_move_or_values_change(self):
        _, before = run("sam-globals.yaml")
        content = "# leading comment\n\n" + (FIXTURES / "sam-globals.yaml").read_text().replace("1024", "2048")
        after = inline(content, "sam-globals.yaml")
        self.assertEqual(before["findings"][0]["fingerprint"], after["findings"][0]["fingerprint"])
        self.assertEqual(after["findings"][0]["evidence"][0]["line_start"], 9)


class Inf06ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:sam-globals.yaml", IDENTITY)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run(*POSITIVE)
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "    MemorySize: 9999"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("sam-globals.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input(*POSITIVE)
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "inf06-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(*POSITIVE))

    def test_registered_in_cli_without_required_settings(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], inf06)
        self.assertIsNone(getattr(inf06, "REFERENCE_SETTINGS", None))


class Inf06CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf06_result(self):
        input_path = FIXTURES / "inf06-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 3)


if __name__ == "__main__":
    unittest.main()
