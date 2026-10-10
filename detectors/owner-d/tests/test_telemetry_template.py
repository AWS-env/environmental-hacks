"""Static checks of cdk/owner-d/telemetry.yaml, mainly the optional analyzer schedules (issue #467).

Parses the template offline (PyYAML with the CloudFormation short-form tags) and feeds each schedule's
input through the analyzers' own event parsing. No AWS calls. cfn-lint runs on the template in CI.
"""

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

try:
    import yaml
except ImportError:  # CI installs PyYAML (detectors/owner-c/requirements.txt) before these tests
    yaml = None

from owner_d.aws import common, log_handler, trace_handler

TEMPLATE = Path(__file__).resolve().parents[3] / "cdk" / "owner-d" / "telemetry.yaml"
SCHEDULES = {
    "TelemetryAnalyzerSchedule": "TelemetryAnalyzerFunction",
    "LogAnalyzerSchedule": "LogAnalyzerFunction",
    "TraceAnalyzerSchedule": "TraceAnalyzerFunction",
}
ROLE_ARN = "arn:aws:iam::123456789012:role/owner-d-telemetry-readonly"
FAKE_EXTERNAL_ID = "test-only-external-id-0000"
SAMPLE_SHA = "0123456789abcdef0123456789abcdef01234567"


class Tag:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def __eq__(self, other):
        return isinstance(other, Tag) and (self.name, self.value) == (other.name, other.value)

    def __repr__(self):
        return f"!{self.name} {self.value!r}"


def _load():
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return Tag(suffix, value)

    Loader.add_multi_constructor("!", construct)
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)


def render_input(text, *, commit_sha, scheduled_time="2026-10-10T01:00:00Z"):
    """What Scheduler would send: !Sub values filled in, then the scheduled-time context attribute."""
    def sub(match):
        name = match.group(1)
        if name == "AnalyzerScheduleCommitSha":
            return commit_sha
        if name == "ReadOnlyRole.Arn":
            return ROLE_ARN
        raise AssertionError(f"unexpected substitution ${{{name}}} in schedule input")

    rendered = re.sub(r"\$\{([^}]+)\}", sub, text)
    return json.loads(rendered.replace("<aws.scheduler.scheduled-time>", scheduled_time))


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TelemetryTemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = _load()
        cls.params = cls.template["Parameters"]
        cls.resources = cls.template["Resources"]

    def test_schedule_parameters_default_to_disabled_daily(self):
        state = self.params["AnalyzerScheduleState"]
        self.assertEqual(state["Default"], "DISABLED")
        self.assertEqual(sorted(state["AllowedValues"]), ["DISABLED", "ENABLED"])
        self.assertEqual(self.params["AnalyzerScheduleExpression"]["Default"], "rate(1 day)")
        sha = self.params["AnalyzerScheduleCommitSha"]
        self.assertNotIn("Default", sha)  # required: a baked-in commit would go stale
        self.assertRegex(SAMPLE_SHA, re.compile("^" + sha["AllowedPattern"] + "$"))

    def test_existing_guards_are_unchanged(self):
        self.assertEqual(self.params["LogQueryPattern"]["Default"], "/aws/lambda/owner-d-*")
        self.assertEqual(self.params["ReservedConcurrency"]["Default"], 0)
        assertion = self.template["Rules"]["ProjectRegionOnly"]["Assertions"][0]["Assert"]
        self.assertEqual(assertion, Tag("Equals", [Tag("Ref", "AWS::Region"), "ap-south-1"]))
        self.assertTrue(self.params["ReadOnlyExternalId"]["NoEcho"])

    def test_one_schedule_per_analyzer(self):
        schedules = {k: v for k, v in self.resources.items() if v["Type"] == "AWS::Scheduler::Schedule"}
        self.assertEqual(set(schedules), set(SCHEDULES))
        for name, function in SCHEDULES.items():
            props = schedules[name]["Properties"]
            with self.subTest(name):
                self.assertTrue(props["Name"].startswith("owner-d-"))
                self.assertEqual(props["State"], Tag("Ref", "AnalyzerScheduleState"))
                self.assertEqual(props["ScheduleExpression"], Tag("Ref", "AnalyzerScheduleExpression"))
                self.assertIn(props["FlexibleTimeWindow"]["Mode"], ("OFF", "FLEXIBLE"))
                target = props["Target"]
                self.assertEqual(target["Arn"], Tag("GetAtt", f"{function}.Arn"))
                self.assertEqual(target["RoleArn"], Tag("GetAtt", "AnalyzerScheduleRole.Arn"))
                self.assertEqual(target["RetryPolicy"]["MaximumRetryAttempts"], 0)
                self.assertEqual(target["DeadLetterConfig"]["Arn"], Tag("GetAtt", "TelemetryDlq.Arn"))

    def test_scheduler_role_is_least_privilege(self):
        role = self.resources["AnalyzerScheduleRole"]["Properties"]
        self.assertTrue(role["RoleName"].startswith("owner-d-"))
        self.assertIn({"Key": "owner", "Value": "D"}, role["Tags"])
        self.assertNotIn("ManagedPolicyArns", role)
        trust = role["AssumeRolePolicyDocument"]["Statement"]
        self.assertEqual([s["Principal"] for s in trust], [{"Service": "scheduler.amazonaws.com"}])
        statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
        granted = {}
        for statement in statements:
            self.assertEqual(statement["Effect"], "Allow")
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            resources = statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
            for action in actions:
                granted.setdefault(action, []).extend(resources)
        self.assertEqual(set(granted), {"lambda:InvokeFunction", "sqs:SendMessage"})
        self.assertCountEqual(granted["lambda:InvokeFunction"],
                              [Tag("GetAtt", f"{f}.Arn") for f in SCHEDULES.values()])
        self.assertEqual(granted["sqs:SendMessage"], [Tag("GetAtt", "TelemetryDlq.Arn")])

    def test_inputs_parse_and_never_carry_the_external_id(self):
        sha = SAMPLE_SHA
        env = {"READONLY_ROLE_ARN": ROLE_ARN, "READONLY_EXTERNAL_ID": FAKE_EXTERNAL_ID}
        for name in SCHEDULES:
            raw = self.resources[name]["Properties"]["Target"]["Input"]
            with self.subTest(name):
                self.assertEqual(raw.name, "Sub")
                self.assertNotIn("ExternalId", raw.value)
                self.assertNotIn("external_id", raw.value)
                event = render_input(raw.value, commit_sha=sha)
                self.assertEqual(event["role_arn"], ROLE_ARN)
                repository_id, commit_sha, scan_id = common.read_identity(event)
                self.assertEqual(repository_id, "github:AWS-env/environmental-hacks")
                self.assertEqual(commit_sha, sha)
                self.assertEqual(scan_id, "owner-d-scheduled-2026-10-10T01:00:00Z")
                with mock.patch.dict(os.environ, env):
                    readers = common.readers_for(event)
                self.assertEqual(readers.external_id, FAKE_EXTERNAL_ID)  # injected by the handler, not the input

    def test_inputs_use_bounded_default_windows(self):
        sha = SAMPLE_SHA

        def event(name):
            return render_input(self.resources[name]["Properties"]["Target"]["Input"].value, commit_sha=sha)

        self.assertEqual(event("TelemetryAnalyzerSchedule")["discover"], {})
        logs = event("LogAnalyzerSchedule")["logs"]
        self.assertEqual(logs, {"lookback_hours": log_handler.DEFAULT_LOOKBACK_HOURS,
                                "limit": log_handler.DEFAULT_ROW_LIMIT,
                                "timeout_seconds": log_handler.DEFAULT_QUERY_TIMEOUT})
        self.assertNotIn("log_groups", logs)  # only LOG_GROUP_ALLOWLIST (LogQueryPattern) groups are queried
        xray = event("TraceAnalyzerSchedule")["xray"]
        self.assertEqual(xray, {"lookback_minutes": trace_handler.DEFAULT_LOOKBACK_MINUTES,
                                "max_traces": trace_handler.DEFAULT_MAX_TRACES,
                                "max_pages": trace_handler.DEFAULT_PAGES})

    def test_no_reserved_concurrency_by_default(self):
        functions = [v["Properties"] for v in self.resources.values() if v["Type"] == "AWS::Lambda::Function"]
        self.assertEqual(len(functions), 3)
        for props in functions:
            self.assertEqual(props["ReservedConcurrentExecutions"],
                             Tag("If", ["Reserve", Tag("Ref", "ReservedConcurrency"), Tag("Ref", "AWS::NoValue")]))


if __name__ == "__main__":
    unittest.main()
