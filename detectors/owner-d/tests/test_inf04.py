"""Behavioral tests for the INF-04 detector (issue #171): static IaC proxy and CloudWatch telemetry mode."""

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

from tests.aws_fakes import NOW, ROLE, AwsTestCase, FakeTable  # noqa: E402

from findings_hub import writer  # noqa: E402
from owner_d import cli, inf04  # noqa: E402
from owner_d.aws import activity, common, registry, telemetry_handler  # noqa: E402
from owner_d.inf04 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint  # noqa: E402
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate, validate_pair  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf04"
REPOSITORY_ID = "github:AWS-env/example"
SETTINGS = {"min_window_days": 14, "max_activity": 0}


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


def identities(result):
    return [f["identity"] for f in result["findings"]]


TEMPLATE = 'AWSTemplateFormatVersion: "2010-09-09"\nResources:\n'
FUNCTION = ("  {name}:\n    Type: AWS::Lambda::Function\n    Properties:\n      Runtime: python3.12\n"
            "      Handler: app.handler\n      Role: arn:aws:iam::123456789012:role/fn\n")


class Inf04PositiveTests(unittest.TestCase):
    """INF04-01: unreferenced components and disabled-only triggers are flagged with exact evidence."""

    # identity: (line_start, line_end, confidence, summary phrase)
    EXPECTED = {
        "template-positive.yaml": {
            "LegacyExport:unreferenced": (14, 15, "low", "it has no FunctionName, so a caller outside the "
                                                         "template would have to look up its generated name"),
            "ReportBuilder:unreferenced": (40, 41, "low", "reached by its FunctionName 'report-builder' from outside"),
            "ResizeImages:unreferenced": (49, 50, "low", "SAM function 'ResizeImages' is referenced by nothing"),
            "OldJobsQueue:unreferenced": (55, 56, "low", "no producer, consumer, event source mapping"),
            "AlertsTopic:unreferenced": (59, 60, "low", "it has no subscriptions, and no publisher"),
            "NightlyCleanupRule:disabled-trigger": (75, 75, "low", "EventBridge rule 'NightlyCleanupRule' declares "
                                                                   "State DISABLED, and it is the only thing in this "
                                                                   "template that uses Lambda function "
                                                                   "'NightlyCleanup'"),
            "QuarterlyReportSchedule:disabled-trigger": (117, 117, "low", "uses Lambda function 'QuarterlyReport', "
                                                                          "which stays deployed"),
            "IngestMapping:disabled-trigger": (136, 136, "low", "declares Enabled: false, and it is the only thing in "
                                                                "this template that uses SQS queue 'IngestQueue' and "
                                                                "Lambda function 'IngestWorker', which stay deployed"),
            "WeeklyDigest:Events.Weekly:disabled": (148, 148, "low", "its event 'Weekly' is disabled"),
        },
        "cdk-synth.template.json": {
            "OldHandler1C2D3E4F:unreferenced": (13, 14, "low", "Lambda function 'OldHandler1C2D3E4F' is "
                                                                  "referenced by nothing"),
        },
    }

    def test_unused_components_are_flagged_with_exact_lines(self):
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
                    self.assertIn("# noqa: INF-04", finding["recommendation"])
                    self.assertTrue(finding["references"])

    def test_passive_references_do_not_count_as_use(self):
        """LegacyExport has a log group and an alarm on its metrics, which describe it but do not use it."""
        findings = findings_for(run("template-positive.yaml")[1], "template-positive.yaml")
        self.assertIn("LegacyExport:unreferenced", findings)

    def test_cdk_framework_function_is_not_flagged(self):
        findings = findings_for(run("cdk-synth.template.json")[1], "cdk-synth.template.json")
        self.assertNotIn("CustomS3AutoDeleteObjectsCustomResourceProviderHandler9D90184F:unreferenced", findings)
        self.assertNotIn("WorkerA1B2C3D4:unreferenced", findings)
        self.assertNotIn("WorkQueue7A8B9C0D:unreferenced", findings)


class Inf04NegativeTests(unittest.TestCase):
    """INF04-02: components used by triggers, producers, consumers, policies or outputs are clean."""

    def test_used_components_are_clean(self):
        _, result = run("template-negative.yaml")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-negative.yaml"])
        self.assertEqual(result["findings"], [])

    def test_removing_the_use_restores_the_finding(self):
        """Guards the negatives against passing for the wrong reason (e.g. a parse quirk)."""
        content = (FIXTURES / "template-negative.yaml").read_text()
        for old, new, identity in (
            ("      FunctionName: !Ref OrdersWorker\n  OrdersPausedMapping", "      FunctionName: !Ref TickFn\n"
             "  OrdersPausedMapping", "OrdersPausedMapping:disabled-trigger"),
            ("      State: ENABLED\n", "      State: DISABLED\n", "TickRule:disabled-trigger"),
            ("      State: !Ref ScheduleState\n", "      State: DISABLED\n", "OptionalSchedule:disabled-trigger"),
            ("      TargetFunctionArn: !GetAtt WebhookFn.Arn\n",
             "      TargetFunctionArn: arn:aws:lambda:ap-south-1:123456789012:function:other\n",
             "WebhookFn:unreferenced"),
            ("      FunctionName: !Ref LiveAlias\n", "      FunctionName: other-fn\n", "LiveFn:unreferenced"),
            ("    Value: !GetAtt ExportedFn.Arn\n", "    Value: none\n", "ExportedFn:unreferenced"),
            ("      Events:\n        Get:\n          Type: Api\n          Properties:\n            Path: /items\n"
             "            Method: get\n", "", "SamApiFn:unreferenced"),
            ("      Subscription:\n        - Protocol: email\n          Endpoint: ops@example.com\n",
             "      DisplayName: fanout\n", "FanoutTopic:unreferenced"),
            ("      AlarmActions: [!Ref AlarmTopic]\n", "      AlarmActions: []\n", "AlarmTopic:unreferenced"),
            ("        deadLetterTargetArn: !GetAtt OrdersDlq.Arn\n",
             "        deadLetterTargetArn: arn:aws:sqs:ap-south-1:123456789012:other\n", "OrdersDlq:unreferenced"),
            ("      ServiceToken: !GetAtt ProviderFn.Arn\n",
             "      ServiceToken: arn:aws:lambda:ap-south-1:123456789012:function:provider\n",
             "ProviderFn:unreferenced"),
            ('"${StepFn.Arn}"', '"arn:aws:lambda:ap-south-1:123456789012:function:step"', "StepFn:unreferenced"),
            ("      Target: !Sub arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${ApiFn.Arn}/"
             "invocations\n", "      Target: arn:aws:apigateway:ap-south-1:lambda:path/other\n", "ApiFn:unreferenced"),
        ):
            with self.subTest(identity=identity, old=old[:40]):
                self.assertIn(old, content)
                _, result = run_inline("template-negative.yaml", content.replace(old, new, 1))
                self.assertEqual(identities(result), [identity] if identity else [])

    def test_environment_variables_make_producers_visible(self):
        content = (FIXTURES / "template-negative.yaml").read_text()
        content = content.replace("          AUDIT_QUEUE_URL: !Ref AuditQueue\n", "")
        _, result = run_inline("template-negative.yaml", content)
        self.assertEqual(identities(result), ["AuditQueue:unreferenced"])

    def test_template_without_judged_types_is_clean(self):
        content = TEMPLATE + "  Svc:\n    Type: AWS::ECS::Service\n    Properties:\n      DesiredCount: 0\n"
        _, result = run_inline("app.yaml", content)
        self.assertEqual((result["status"], result["findings"]), ("completed", []))


class Inf04ExceptionTests(unittest.TestCase):
    """INF04-03: `# noqa: INF-04`, CDK framework functions and externally defined APIs."""

    def test_exceptions_and_noqa(self):
        _, result = run("template-exceptions.yaml", "external-definition.yaml")
        self.assertEqual(result["status"], "completed")
        got = {f["identity"]: f["confidence"] for f in result["findings"]}
        # `# noqa: E501` does not suppress; the external OpenAPI definition hides functions but not queues.
        self.assertEqual(got, {"OtherCodeQueue:unreferenced": "low", "DrStandbyQueue:unreferenced": "low",
                               "ScratchQueue:unreferenced": "low"})
        drill = findings_for(result, "template-exceptions.yaml")["DrStandbyQueue:unreferenced"]
        self.assertIn("(created only under a Condition)", drill["summary"])

    def test_removing_noqa_restores_findings(self):
        content = (FIXTURES / "template-exceptions.yaml").read_text()
        content = content.replace("  # noqa: INF-04\n", "").replace("  # noqa: INF-04 (re-enabled every November)", "")
        _, result = run_inline("template-exceptions.yaml", content)
        self.assertEqual(identities(result), ["BreakGlass:unreferenced", "SeasonalSaleRule:disabled-trigger",
                                              "OtherCodeQueue:unreferenced", "DrStandbyQueue:unreferenced"])

    def test_without_the_external_definition_the_function_is_judged(self):
        content = (FIXTURES / "external-definition.yaml").read_text().replace(
            "      DefinitionUri: openapi.yaml\n", "")
        _, result = run_inline("external-definition.yaml", content)
        self.assertEqual(identities(result), ["ListItems:unreferenced", "ScratchQueue:unreferenced"])


class Inf04IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF04-04: requested scope without a source is not evaluated."""
        _, result = run(sources=[], scope=["file:template.yaml"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static template or telemetry source" in item
                            for item in result["coverage"]["limitations"]))

    def test_malformed_unsupported_and_macro_files_make_result_partial(self):
        """INF04-05: invalid JSON, Terraform, Kubernetes manifests and macros are never reported clean."""
        _, result = run("template-positive.yaml", "broken.json", "main.tf", "deployment.yaml", "macro.yaml")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-positive.yaml"])
        self.assertTrue(all(f["scope_id"] == "file:template-positive.yaml" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.json: could not be parsed (invalid JSON)", limitations)
        self.assertIn("file:main.tf: unsupported file type; INF-04 v1.0.0 supports CloudFormation/SAM", limitations)
        self.assertIn("file:deployment.yaml: not evaluated (no CloudFormation Resources found)", limitations)
        self.assertIn("file:macro.yaml: not evaluated (template uses the macro transform CompanyDefaults", limitations)
        self.assertIn("Static IaC proxy only", limitations)
        self.assertNotIn("Telemetry mode", limitations)

    def test_unparseable_and_rewriting_templates_are_unavailable(self):
        for name, content, reason in (
            ("t.yaml", TEMPLATE + "  Q:\n\tType: AWS::SQS::Queue\n", "tab indentation"),
            ("t.yaml", TEMPLATE + "  Q: {Type: AWS::SQS::Queue\n", "could not be parsed"),
            ("t.json", '{"AWSTemplateFormatVersion": "2010-09-09"}', "no CloudFormation Resources found"),
            ("t.yaml", TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n    Properties:\n      Fn::Transform:\n"
                                  "        Name: AWS::Include\n", "Fn::Transform/AWS::Include or Fn::ForEach on line 6"),
            ("stack.ts", "new sqs.Queue(this, 'OldJobs');\n", "unsupported file type"),
        ):
            with self.subTest(name=name, reason=reason):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])


class Inf04BoundaryTests(unittest.TestCase):
    """INF04-06: reference forms, partial triggers, confidence tiers and stable identities."""

    def test_reference_forms_count_as_use(self):
        base = TEMPLATE + FUNCTION.format(name="Fn")
        for referrer in (
            "  P:\n    Type: AWS::Lambda::Permission\n    Properties:\n      FunctionName: {\"Ref\": Fn}\n",
            "  P:\n    Type: AWS::Lambda::Permission\n    Properties:\n      FunctionName: {\"Fn::GetAtt\": [Fn, Arn]}\n",
            "  P:\n    Type: AWS::Lambda::Permission\n    Properties:\n      FunctionName: !GetAtt [Fn, Arn]\n",
            "  P:\n    Type: AWS::IAM::Policy\n    Properties:\n      PolicyDocument:\n        Resource: !Sub \"${Fn.Arn}:*\"\n",
            "  P:\n    Type: AWS::CloudFormation::Stack\n    Properties:\n      Parameters:\n        Target: !Ref Fn\n",
        ):
            with self.subTest(referrer=referrer.splitlines()[-1]):
                _, result = run_inline("t.yaml", base + referrer)
                self.assertEqual(identities(result), [])
        _, result = run_inline("t.yaml", base + "Globals:\n  Function:\n    Environment:\n      Variables:\n"
                                                "        TARGET: !Ref Fn\n")
        self.assertEqual(identities(result), [])

    def test_event_invoke_config_is_passive_for_the_function_but_uses_its_destination(self):
        content = (TEMPLATE + FUNCTION.format(name="Fn") + "  Failures:\n    Type: AWS::SQS::Queue\n"
                   "  Cfg:\n    Type: AWS::Lambda::EventInvokeConfig\n    Properties:\n      FunctionName: !Ref Fn\n"
                   "      Qualifier: $LATEST\n      DestinationConfig:\n        OnFailure:\n"
                   "          Destination: !GetAtt Failures.Arn\n")
        _, result = run_inline("t.yaml", content)
        self.assertEqual(identities(result), ["Fn:unreferenced"])

    def test_disabled_trigger_next_to_an_active_one_is_clean(self):
        content = (TEMPLATE + "  Fn:\n    Type: AWS::Serverless::Function\n    Properties:\n      CodeUri: src/\n"
                   "      Events:\n        Off:\n          Type: Schedule\n          Properties:\n"
                   "            Schedule: rate(1 day)\n            State: DISABLED\n        On:\n"
                   "          Type: SQS\n          Properties:\n            Queue: arn:aws:sqs:ap-south-1:1:q\n")
        _, result = run_inline("t.yaml", content)
        self.assertEqual(identities(result), [])
        _, result = run_inline("t.yaml", content.split("        On:\n")[0])
        self.assertEqual(identities(result), ["Fn:Events.Off:disabled"])
        self.assertEqual(result["findings"][0]["evidence"][0]["value"], "            State: DISABLED")

    def test_one_finding_per_disabled_trigger(self):
        rule = ("  {name}:\n    Type: AWS::Events::Rule\n    Properties:\n      State: DISABLED\n      Targets:\n"
                "        - Arn: !GetAtt Fn.Arn\n          Id: t\n")
        content = TEMPLATE + FUNCTION.format(name="Fn") + rule.format(name="A") + rule.format(name="B")
        _, result = run_inline("t.yaml", content)
        self.assertEqual(identities(result), ["A:disabled-trigger", "B:disabled-trigger"])

    def test_unresolved_properties_and_names_set_by_intrinsics(self):
        _, result = run_inline("t.yaml", TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n    Properties: !If [C, {}, {}]\n")
        self.assertEqual(identities(result), [])
        _, result = run_inline("t.yaml", TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n    Properties:\n"
                                                    "      QueueName: !Sub ${AWS::StackName}-jobs\n")
        self.assertEqual([(f["identity"], f["confidence"]) for f in result["findings"]], [("Q:unreferenced", "low")])
        self.assertIn("reached by its QueueName '${AWS::StackName}-jobs'", result["findings"][0]["summary"])

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
        doc = TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n"
        _, result = run_inline("t.yaml", doc + "---\n" + doc)
        self.assertEqual(identities(result), ["Q:unreferenced", "Q:unreferenced#2"])

    def test_references_do_not_cross_documents(self):
        used = TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\nOutputs:\n  Url:\n    Value: !Ref Q\n"
        _, result = run_inline("t.yaml", used + "---\n" + TEMPLATE + "  Q:\n    Type: AWS::SQS::Queue\n")
        self.assertEqual(identities(result), ["Q:unreferenced"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 11)  # the second document


# ---- telemetry mode -------------------------------------------------------------------------------------


def telemetry_source(resource_id, metric, *, activity_value=0, datapoints=30, observed_days=30.0, window_days=30.0,
                     resource_type=None, statistic=None, scope_id=None):
    resource_type = resource_type or {"invocations": "aws_lambda_function", "request_count": "aws_lb",
                                      "database_connections": "aws_rds_db_instance"}.get(metric, "aws_thing")
    statistic = statistic or inf04.METRICS.get(metric, ("Sum",))[0]
    return {
        "source_id": f"cloudwatch:{resource_id}",
        "scope_id": scope_id or f"resource:{resource_id}",
        "kind": "telemetry",
        "locator": f"cloudwatch://ap-south-1/{resource_id}?period=86400&stat={statistic}",
        "data": {"resource_id": resource_id, "resource_type": resource_type, "metric": metric, "statistic": statistic,
                 "activity_value": activity_value, "datapoints": datapoints, "observed_days": observed_days,
                 "window_days": window_days, "period_seconds": 86400},
    }


def run_telemetry(*sources, context=None, scope=None):
    payload = make_input(sources=list(sources), scope=scope or [s["scope_id"] for s in sources],
                         context=dict(SETTINGS) if context is None else context)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


class Inf04TelemetryTests(unittest.TestCase):
    """INF04-07: no activity over the window is flagged; activity, short windows and gaps are not."""

    def test_idle_database_and_unused_function_and_load_balancer_are_flagged(self):
        _, result = run_telemetry(
            telemetry_source("rds/orders-db", "database_connections"),
            telemetry_source("lambda/legacy-export", "invocations", datapoints=0, observed_days=0),
            telemetry_source("alb/app/old-web/0123456789abcdef", "request_count", datapoints=0, observed_days=0),
        )
        self.assertEqual(result["status"], "completed")
        got = {f["scope_id"]: f for f in result["findings"]}
        self.assertEqual({k: f["confidence"] for k, f in got.items()}, {
            "resource:rds/orders-db": "medium",
            "resource:lambda/legacy-export": "low",
            "resource:alb/app/old-web/0123456789abcdef": "low",
        })
        rds = got["resource:rds/orders-db"]
        self.assertEqual(rds["identity"], "no-observed-use")
        self.assertIn("ran for 30 of the last 30 days with at most 0 peak database connections", rds["summary"])
        self.assertEqual({e["field"]: e["value"] for e in rds["evidence"]},
                         {"activity_value": 0, "datapoints": 30, "observed_days": 30.0, "window_days": 30.0})
        self.assertTrue(all(e["kind"] == "telemetry" for e in rds["evidence"]))
        self.assertIn("final snapshot", rds["recommendation"])
        self.assertIn("recorded no invocations in the 30-day window", got["resource:lambda/legacy-export"]["summary"])
        self.assertIn("or no longer exists under this name", got["resource:lambda/legacy-export"]["summary"])
        self.assertEqual(rds["fingerprint"],
                         fingerprint(REPOSITORY_ID, CHECK_ID, "resource:rds/orders-db", "no-observed-use"))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("Telemetry mode", limitations)
        self.assertNotIn("Static IaC proxy", limitations)

    def test_activity_is_clean(self):
        _, result = run_telemetry(
            telemetry_source("rds/orders-db", "database_connections", activity_value=3),
            telemetry_source("lambda/orders", "invocations", activity_value=1, datapoints=1),
            telemetry_source("alb/app/web/0123456789abcdef", "request_count", activity_value=12000),
        )
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_short_windows_and_missing_database_series_are_not_evaluated(self):
        _, result = run_telemetry(
            telemetry_source("lambda/new", "invocations", datapoints=0, observed_days=0, window_days=7.0),
            telemetry_source("rds/stopped", "database_connections", datapoints=0, observed_days=0),
            telemetry_source("rds/new-db", "database_connections", datapoints=10, observed_days=10.0),
            telemetry_source("rds/orders-db", "database_connections"),
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["resource:rds/orders-db"])
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("resource:lambda/new: telemetry window 7 days is below the required 14 days", limitations)
        self.assertIn("resource:rds/stopped: no peak database connections datapoints in the window; the resource was "
                      "stopped, deleted or not found", limitations)
        self.assertIn("resource:rds/new-db: datapoints cover only 10 days", limitations)

    def test_max_activity_boundary(self):
        for value, flagged in ((5, True), (5.5, False), (0, True)):
            with self.subTest(value=value):
                _, result = run_telemetry(
                    telemetry_source("lambda/orders", "invocations", activity_value=value, datapoints=3),
                    context={"min_window_days": 14, "max_activity": 5})
                self.assertEqual(len(result["findings"]), 1 if flagged else 0)
        _, result = run_telemetry(telemetry_source("lambda/orders", "invocations", activity_value=3, datapoints=3),
                                  context={"min_window_days": 14, "max_activity": 5})
        self.assertIn("recorded 3 invocations in 3 datapoints over 30 days (limit 5)", result["findings"][0]["summary"])
        _, result = run_telemetry(telemetry_source("lambda/x", "invocations", datapoints=0, observed_days=0,
                                                   window_days=14.0))
        self.assertEqual(len(result["findings"]), 1)  # the window equal to min_window_days is enough

    def test_settings_are_required_for_telemetry_only(self):
        for context, reason in (({}, "missing required context settings: min_window_days, max_activity"),
                                ({"min_window_days": 0, "max_activity": 0}, "context.min_window_days must be positive"),
                                ({"min_window_days": 14, "max_activity": -1},
                                 "context.max_activity must not be negative"),
                                ({"min_window_days": "14", "max_activity": 0},
                                 "context.min_window_days must be a number")):
            with self.subTest(context=context):
                sources = [telemetry_source("rds/orders-db", "database_connections"),
                           static_source("template-positive.yaml")]
                _, result = run_telemetry(*sources, context=context)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["file:template-positive.yaml"])
                self.assertIn(f"resource:rds/orders-db: missing or invalid context settings for telemetry mode: "
                              f"{reason}", result["coverage"]["limitations"])
                limitations = " ".join(result["coverage"]["limitations"])
                self.assertIn("Static IaC proxy", limitations)
                self.assertIn("Telemetry mode", limitations)

    def test_malformed_telemetry_is_not_evaluated(self):
        for source, reason in (
            (telemetry_source("ec2/i-1", "cpu_utilization"), "unsupported metric 'cpu_utilization'"),
            (telemetry_source("rds/db", "database_connections", statistic="Sum"),
             "database_connections must be read with the Maximum statistic"),
            (telemetry_source("lambda/f", "invocations", activity_value=3, datapoints=0),
             "activity_value and observed_days must be 0 when there are no datapoints"),
            (telemetry_source("lambda/f", "invocations", scope_id="resource:lambda/g"),
             "does not match resource_id"),
            (telemetry_source("lambda/f", "invocations", datapoints=True), "datapoints must be a nonnegative integer"),
            (telemetry_source("lambda/f", "invocations", activity_value=float("nan")),
             "activity_value must be a nonnegative number"),
        ):
            with self.subTest(reason=reason):
                payload = make_input(sources=[source], scope=[source["scope_id"]], context=dict(SETTINGS))
                result = evaluate(payload)
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(reason, result["coverage"]["limitations"][0])
        missing = telemetry_source("lambda/f", "invocations")
        del missing["data"]["window_days"]
        _, result = run_telemetry(missing)
        self.assertIn("missing fields: window_days", result["coverage"]["limitations"][0])

    def test_mixed_or_duplicate_sources_for_one_scope_are_not_evaluated(self):
        tele = telemetry_source("lambda/f", "invocations", datapoints=0, observed_days=0)
        static = dict(static_source("template-positive.yaml"), scope_id="resource:lambda/f")
        _, result = run_telemetry(tele, static, scope=["resource:lambda/f"])
        self.assertIn("static and telemetry sources supplied for one scope item", result["coverage"]["limitations"][0])
        _, result = run_telemetry(tele, dict(tele, source_id="cloudwatch:dup"), scope=["resource:lambda/f"])
        self.assertIn("multiple telemetry sources", result["coverage"]["limitations"][0])

    def test_invented_telemetry_evidence_is_rejected(self):
        payload, result = run_telemetry(telemetry_source("rds/orders-db", "database_connections"))
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = 7
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)


END = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)  # NOW (12:00 UTC) aligned down to a whole day


def daily(days, value, *, end=END):
    first = end - dt.timedelta(days=days)
    stamps = [first + dt.timedelta(days=i) for i in range(days)]
    return stamps, [value(i) if callable(value) else value for i in range(days)]


class ActivityCloudWatch:
    """ListMetrics pages + GetMetricData from {dimension value: (timestamps, values)}; read APIs only."""

    def __init__(self, data=None, list_pages=None, partial=(), data_pages=1):
        self.data, self.list_pages, self.partial, self.data_pages = data or {}, list_pages or [[]], set(partial), data_pages
        self.calls = []

    def list_metrics(self, **kw):
        self.calls.append(("list_metrics", kw))
        index = int(kw.get("NextToken", "0"))
        page = {"Metrics": [m for m in self.list_pages[index] if kw.get("Namespace") in (None, m["Namespace"])]}
        if index + 1 < len(self.list_pages):
            page["NextToken"] = str(index + 1)
        return page

    def get_metric_data(self, **kw):
        self.calls.append(("get_metric_data", kw))
        page = int(kw.get("NextToken", "0"))
        results = []
        for query in kw["MetricDataQueries"]:
            value = query["MetricStat"]["Metric"]["Dimensions"][0]["Value"]
            stamps, values = self.data.get(value, ([], []))
            size = -(-len(stamps) // self.data_pages) if stamps else 0
            chunk = slice(page * size, (page + 1) * size)
            last = page + 1 == self.data_pages
            results.append({"Id": query["Id"], "Timestamps": stamps[chunk], "Values": values[chunk],
                            "StatusCode": "PartialData" if value in self.partial or not last else "Complete"})
        out = {"MetricDataResults": results}
        if page + 1 < self.data_pages:
            out["NextToken"] = str(page + 1)
        return out


ACTIVITY = {"resources": [{"type": "lambda", "name": "legacy-export"}, {"type": "lambda", "name": "orders"},
                          {"type": "rds", "id": "orders-db"}, {"type": "rds", "id": "reports-db"},
                          {"type": "alb", "name": "app/web/0123456789abcdef"}]}
DATA = {
    "orders": daily(30, lambda i: 100 + i),
    "orders-db": daily(30, 0.0),
    "reports-db": daily(30, lambda i: 4.0 if i == 3 else 0.0),
    "app/web/0123456789abcdef": daily(30, 5000.0),
}


class Inf04TelemetryCollectionTests(AwsTestCase):
    """owner-d-telemetry-analyzer: CloudWatch activity -> INF-04 -> findings-hub, with stubbed clients."""

    def use(self, **kw):
        self.fakes["cloudwatch"] = ActivityCloudWatch(**kw)
        common._clients.clear()
        return self.fakes["cloudwatch"]

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(self.base_event(checks=["INF-04"], **extra))

    def test_unused_function_and_idle_database_are_flagged_end_to_end(self):
        cw = self.use(data=DATA)
        out = self.run_event(activity=ACTIVITY, dry_run=True)
        self.assertEqual(out["results"], [{"check_id": "INF-04", "status": "completed", "scope": 5, "evaluated": 5,
                                           "findings": 2}])
        result = out["result_payloads"][0]
        got = {f["scope_id"]: f["confidence"] for f in result["findings"]}
        self.assertEqual(got, {"resource:lambda/legacy-export": "low", "resource:rds/orders-db": "medium"})
        self.assertEqual(result["context"]["min_window_days"], 14)
        self.assertEqual(result["context"]["collection"],
                         {"source": "cloudwatch-getmetricdata", "period_seconds": 86400, "lookback_days": 30.0})
        [call] = [kw for name, kw in cw.calls if name == "get_metric_data"]
        self.assertEqual((call["StartTime"], call["EndTime"]), (END - dt.timedelta(days=30), END))
        stats = {q["MetricStat"]["Metric"]["MetricName"]: (q["MetricStat"]["Stat"], q["MetricStat"]["Period"])
                 for q in call["MetricDataQueries"]}
        self.assertEqual(stats, {"Invocations": ("Sum", 86400), "DatabaseConnections": ("Maximum", 86400),
                                 "RequestCount": ("Sum", 86400)})
        self.assertEqual([name for name, _ in cw.calls], ["get_metric_data"])  # no discovery requested

    def test_published_event_is_stored_by_the_hub_writer(self):
        self.use(data=DATA)
        out = self.run_event(activity={"resources": [{"type": "rds", "id": "orders-db"}]})
        self.assertEqual(out["published"], 1)
        entry = self.fakes["events"].entries[0]
        self.assertEqual((entry["Source"], entry["DetailType"]), ("owner-d.telemetry-analyzer", "detector.result.v1"))
        result = json.loads(entry["Detail"])
        validate(result)
        self.assertEqual(result["findings"][0]["evidence"][0]["locator"],
                         "cloudwatch://ap-south-1/AWS/RDS/DatabaseConnections?DBInstanceIdentifier=orders-db"
                         "&period=86400&stat=Maximum")
        self.assertNotRegex(entry["Detail"], r"\d{12}")  # no account ids in the published event
        summary = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                 "id": "evt-1"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual((summary["outcome"], summary["findings"]), ("stored", 1))

    def test_discovery_finds_running_databases_only(self):
        listed = [{"Namespace": "AWS/RDS", "MetricName": "DatabaseConnections",
                   "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": name}]} for name in ("orders-db", "b-db")]
        aggregate = {"Namespace": "AWS/RDS", "MetricName": "DatabaseConnections",
                     "Dimensions": [{"Name": "EngineName", "Value": "postgres"}]}
        cw = self.use(data=DATA, list_pages=[listed + [aggregate]])
        out = self.run_event(activity={"discover": {}}, dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual(result["scope"], ["resource:rds/b-db", "resource:rds/orders-db"])
        self.assertEqual([f["scope_id"] for f in result["findings"]], ["resource:rds/orders-db"])
        self.assertIn("resource:rds/b-db: no peak database connections datapoints", " ".join(
            result["coverage"]["limitations"]))
        list_call = cw.calls[0][1]
        self.assertEqual((list_call["Namespace"], list_call["RecentlyActive"]), ("AWS/RDS", "PT3H"))
        with self.assertRaisesRegex(ValueError, "cannot find idle lambda resources"):
            self.run_event(activity={"discover": {"types": ["lambda"]}})

    def test_discovery_is_bounded(self):
        listed = [{"Namespace": "AWS/RDS", "MetricName": "DatabaseConnections",
                   "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": f"db-{i}"}]} for i in range(4)]
        cw = self.use(list_pages=[listed[:2], listed[2:], []])
        resources, truncated, notes = activity.discover(cw, {"max_pages": 2, "max_resources": 3})
        self.assertEqual(([r["id"] for r in resources], truncated), (["rds/db-0", "rds/db-1", "rds/db-2"], True))
        self.assertIn("stopped after 2 ListMetrics pages", notes[0])
        self.assertIn("only the first 3 (by id) were read", notes[1])

    def test_without_an_activity_block_nothing_is_read(self):
        cw = self.use(data=DATA)
        out = self.run_event(discover={})
        self.assertEqual(out["skipped"][0]["check_id"], "INF-04")
        self.assertIn('no activity resources were requested (event "activity")', out["skipped"][0]["limitations"][0])
        self.assertEqual((cw.calls, out["published"]), ([], 0))

    def test_incomplete_series_are_not_evaluated(self):
        self.use(data=DATA, partial={"orders-db"})
        out = self.run_event(activity={"resources": [{"type": "rds", "id": "orders-db"},
                                                     {"type": "lambda", "name": "orders"}]}, dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual(result["coverage"]["evaluated_scope"], ["resource:lambda/orders"])
        self.assertIn("resource:rds/orders-db: CloudWatch returned incomplete DatabaseConnections data",
                      " ".join(result["coverage"]["limitations"]))

    def test_paged_series_are_merged(self):
        self.use(data=DATA, data_pages=3)
        out = self.run_event(activity={"resources": [{"type": "rds", "id": "orders-db"}]}, dry_run=True)
        evidence = {e["field"]: e["value"] for e in out["result_payloads"][0]["findings"][0]["evidence"]}
        self.assertEqual(evidence, {"activity_value": 0, "datapoints": 30, "observed_days": 30.0, "window_days": 30.0})

    def test_reads_go_through_the_read_only_role(self):
        self.use(data=DATA)
        self.run_event(activity={"resources": [{"type": "rds", "id": "orders-db"}]}, role_arn=ROLE, dry_run=True)
        self.assertIn(("cloudwatch", "ap-south-1", "ASIA1"), self.created)
        self.assertEqual(self.fakes["sts"].calls[0]["RoleArn"], ROLE)

    def test_event_validation(self):
        self.use(data=DATA)
        for bad, message in (
            ({"resources": [{"type": "ec2", "id": "i-1"}]}, "activity resource type must be one of"),
            ({"resources": [{"type": "alb", "name": "web"}]}, "alb activity resources need a valid 'name'"),
            ({"resources": [{"type": "rds", "name": "orders-db"}]}, "rds activity resources need a valid 'id'"),
            ({"resources": [{"type": "lambda", "name": "orders"}], "lookback_days": 91}, r"within \[0, 90"),
            ({}, "activity needs resources or discover"),
            ([], "activity must be an object"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.run_event(activity=bad)

    def test_probe_counts_without_publishing(self):
        self.use(data=DATA)
        out = telemetry_handler.lambda_handler({"probe": ["activity_metrics"], "activity": ACTIVITY})
        self.assertEqual(out["probe"]["activity_metrics"]["resources"], 5)
        self.assertEqual(out["probe"]["activity_metrics"]["with_datapoints"], 4)
        self.assertEqual((out["published"], self.fakes["events"].entries), (False, []))

    def test_registry_defaults_are_the_reference_settings(self):
        [check] = [c for c in registry.CHECKS if c.check_id == CHECK_ID]
        self.assertEqual((check.source, check.defaults), ("activity_metrics", inf04.REFERENCE_SETTINGS))
        self.assertIn("activity_metrics", telemetry_handler.COLLECTORS)


class Inf04ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:template-positive.yaml", "OldJobsQueue:unreferenced")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("template-positive.yaml")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "  Invented:"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("template-positive.yaml")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("template-positive.yaml", "cdk-synth.template.json", "template-exceptions.yaml")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixtures_match_source_fixtures(self):
        committed = json.loads((FIXTURES / "inf04-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("template-positive.yaml"))
        committed = json.loads((FIXTURES / "inf04-02-telemetry-input.json").read_text())
        self.assertEqual(committed, telemetry_cli_input())


def telemetry_cli_input():
    sources = [telemetry_source("rds/orders-db", "database_connections"),
               telemetry_source("lambda/legacy-export", "invocations", datapoints=0, observed_days=0),
               telemetry_source("lambda/orders", "invocations", activity_value=4521)]
    return make_input(sources=sources, scope=[s["scope_id"] for s in sources], context=dict(SETTINGS))


class Inf04CliTests(unittest.TestCase):
    def run_cli(self, name):
        input_path = FIXTURES / name
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        return result

    def test_cli_writes_valid_static_result(self):
        result = self.run_cli("inf04-01-positive-input.json")
        self.assertEqual((result["status"], len(result["findings"])), ("completed", 9))

    def test_cli_writes_valid_telemetry_result(self):
        result = self.run_cli("inf04-02-telemetry-input.json")
        self.assertEqual((result["status"], len(result["findings"])), ("completed", 2))


if __name__ == "__main__":
    unittest.main()
