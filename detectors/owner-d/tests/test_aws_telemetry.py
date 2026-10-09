"""owner-d-telemetry-analyzer: CloudWatch metrics -> INF-01 -> findings-hub, with stubbed boto3 clients."""

import datetime as dt
import json
import unittest
from unittest import mock

from tests.aws_fakes import NOW, ROLE, AwsTestCase, ClientError, FakeCloudWatch, FakeEvents, FakeSts, FakeTable, hourly

from findings_hub import writer
from owner_d.aws import common, metrics, registry, telemetry_handler
from shared.contracts.validation import validate, validate_pair

IDLE = (hourly(15, 4.0), hourly(15, lambda i: 20.0 + (i % 5)))  # avg 4%, peak 24%
BUSY_PEAKS = (hourly(15, 6.0), hourly(15, lambda i: 95.0 if i == 100 else 30.0))


class SummaryTests(unittest.TestCase):
    def test_average_peak_window_and_samples(self):
        (stamps, avg), (_, peak) = IDLE
        out = metrics.summarize_cpu({"timestamps": stamps, "values": avg}, {"timestamps": stamps, "values": peak}, 3600)
        self.assertEqual(out, {"average_utilization": 0.04, "peak_utilization": 0.24, "window_days": 15.0,
                               "sample_count": 360, "capped": False})

    def test_no_datapoints_is_none_and_nan_is_ignored(self):
        self.assertIsNone(metrics.summarize_cpu({"timestamps": [], "values": []}, {}, 3600))
        stamps, _ = hourly(1, 0)
        out = metrics.summarize_cpu({"timestamps": stamps, "values": [float("nan")] * 23 + [50.0]}, {}, 3600)
        self.assertEqual((out["sample_count"], out["average_utilization"], out["peak_utilization"]), (1, 0.5, 0.5))

    def test_ecs_over_reservation_is_capped(self):
        stamps, _ = hourly(1, 0)
        out = metrics.summarize_cpu({"timestamps": stamps, "values": [150.0] * 24},
                                    {"timestamps": stamps, "values": [180.0] * 24}, 3600)
        self.assertEqual((out["average_utilization"], out["peak_utilization"], out["capped"]), (1.0, 1.0, True))

    def test_resource_specs(self):
        self.assertEqual(metrics.parse_resource({"type": "ecs", "cluster": "web", "service": "api"})["id"], "ecs/web/api")
        ec2 = metrics.parse_resource({"type": "ec2", "id": "i-0abc12345678def00", "provisioned_capacity": 8})
        self.assertEqual((ec2["provisioned_capacity"], ec2["capacity_unit"]), (8, "vcpu"))
        for bad in ({"type": "ec2", "id": "web-1"}, {"type": "rds", "id": "x"}, {"type": "lambda"},
                    {"type": "ec2", "id": "i-0abc12345678def00", "provisioned_capacity": 0}):
            with self.assertRaises(ValueError):
                metrics.parse_resource(bad)


class TelemetryHandlerTests(AwsTestCase):
    def use(self, data=None, **kw):
        self.fakes["cloudwatch"] = FakeCloudWatch(data=data, **kw)
        common._clients.clear()  # drop any cached client so the new fake is used
        return self.fakes["cloudwatch"]

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(self.base_event(**extra))

    def published(self):
        return [json.loads(e["Detail"]) for e in self.fakes["events"].entries]

    def test_idle_instance_is_flagged_and_published_in_hub_shape(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00", "provisioned_capacity": 2}])
        self.assertEqual(out["results"], [{"check_id": "INF-01", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        self.assertEqual((out["published"], out["contract_validated"], out["assumed_role"]), (1, True, False))
        entry = self.fakes["events"].entries[0]
        self.assertEqual((entry["Source"], entry["DetailType"], entry["EventBusName"]),
                         ("owner-d.telemetry-analyzer", "detector.result.v1", "findings-hub"))
        result = json.loads(entry["Detail"])
        validate(result)
        evidence = {e["field"]: e["value"] for e in result["findings"][0]["evidence"]}
        self.assertEqual(evidence, {"average_utilization": 0.04, "peak_utilization": 0.24, "provisioned_capacity": 2,
                                    "window_days": 15.0, "sample_count": 360})
        self.assertEqual(result["findings"][0]["scope_id"], "resource:ec2/i-0abc12345678def00")
        self.assertEqual(result["context"]["collection"],
                         {"source": "cloudwatch-getmetricdata", "period_seconds": 3600, "lookback_days": 15.0})
        self.assertNotRegex(entry["Detail"], r"\d{12}")  # no account ids in the published event

        # The hub writer accepts and stores the event exactly as it would from EventBridge.
        table = FakeTable()
        summary = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                 "id": "evt-1"}, s3=None, table=table, allowed_buckets=set(), now=NOW)
        self.assertEqual((summary["outcome"], summary["evidence"], summary["findings"]), ("stored", "unverified", 1))

    def test_dry_run_publishes_nothing_and_returns_valid_pairs(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True)
        self.assertEqual((out["published"], self.fakes["events"].entries), (0, []))
        self.assertEqual(len(out["result_payloads"]), 1)
        self.assertNotIn(("events", "ap-south-1", None), self.created)  # no events client at all

    def test_payloads_validate_against_inputs(self):
        self.use({("i-0abc12345678def00",): IDLE, ("web", "api"): BUSY_PEAKS})
        raw = metrics.collect_cpu_metrics(
            self.base_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"},
                                       {"type": "ecs", "cluster": "web", "service": "api"}]),
            common.Readers())
        normalized = registry.normalize(metrics.normalize_cpu_metrics, raw, registry.INF01_DEFAULTS)
        from owner_d import inf01
        payload = next(common.build_inputs(repository_id="r", commit_sha="0" * 40, scan_id="s", module=inf01,
                                           context=dict(registry.INF01_DEFAULTS), chunk=50,
                                           scope=normalized["scope"], sources=normalized["sources"]))
        result = inf01.evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual([f["scope_id"] for f in result["findings"]], ["resource:ec2/i-0abc12345678def00"])
        self.assertTrue(any("driven by real peaks" in note for note in result["coverage"]["limitations"]))

    def test_short_window_is_not_evaluated(self):
        self.use({("i-0abc12345678def00",): (hourly(3, 2.0), hourly(3, 5.0))})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertTrue(any("3 days is below the required 14 days" in n for n in result["coverage"]["limitations"]))

    def test_too_few_samples_is_not_evaluated(self):
        self.use({("i-0abc12345678def00",): (hourly(15, 2.0, period=86400), hourly(15, 5.0, period=86400))})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}],
                             window={"period_seconds": 86400}, dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("15 samples is below the required 100" in n for n in result["coverage"]["limitations"]))

    def test_lambda_and_missing_data_stay_unevaluated_with_reasons(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"},
                                        {"type": "ec2", "id": "i-0fff12345678def00"},
                                        {"type": "lambda", "name": "orders"}], dry_run=True)
        result = out["result_payloads"][0]
        self.assertEqual((result["status"], result["coverage"]["evaluated_scope"]),
                         ("partial", ["resource:ec2/i-0abc12345678def00"]))
        notes = " ".join(result["coverage"]["limitations"])
        self.assertIn("resource:lambda/orders: AWS/Lambda publishes no CPU utilization metric", notes)
        self.assertIn("resource:ec2/i-0fff12345678def00: no AWS/EC2 CPUUtilization datapoints", notes)
        gmd = [kw for name, kw in self.fakes["cloudwatch"].calls if name == "get_metric_data"]
        self.assertEqual(len(gmd[0]["MetricDataQueries"]), 4)  # Lambda is not queried for CPU

    def test_discovery_uses_exact_dimension_sets_and_is_bounded(self):
        ec2 = [{"Namespace": "AWS/EC2", "MetricName": "CPUUtilization",
                "Dimensions": [{"Name": "InstanceId", "Value": f"i-0abc1234567800{i:02d}"}]} for i in range(4)]
        aggregate = {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization",
                     "Dimensions": [{"Name": "InstanceId", "Value": "i-0abc12345678ffff"},
                                    {"Name": "InstanceType", "Value": "m5.large"}]}
        cw = self.use({}, list_pages=[ec2[:2] + [aggregate], ec2[2:], []])
        resources, truncated, notes = metrics.discover(cw, {"types": ["ec2"], "max_pages": 2})
        self.assertEqual([r["id"] for r in resources], [f"ec2/i-0abc1234567800{i:02d}" for i in range(4)])
        self.assertTrue(truncated)
        self.assertIn("stopped after 2 ListMetrics pages", notes[0])
        self.assertEqual(sum(1 for name, _ in cw.calls if name == "list_metrics"), 2)
        self.assertEqual(cw.calls[0][1]["RecentlyActive"], "PT3H")
        resources, truncated, notes = metrics.discover(self.use({}, list_pages=[ec2]), {"types": ["ec2"], "max_resources": 3})
        self.assertEqual((len(resources), truncated), (3, True))

    def test_discover_with_defaults_runs_end_to_end_and_empty_discovery_publishes_nothing(self):
        ec2 = [{"Namespace": "AWS/EC2", "MetricName": "CPUUtilization",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-0abc12345678def00"}]}]
        self.use({("i-0abc12345678def00",): IDLE}, list_pages=[ec2])
        out = self.run_event(discover={}, checks=["INF-01"])  # OBS-06 also reads ListMetrics; keep it out here
        self.assertEqual((out["published"], out["results"][0]["findings"]), (1, 1))
        self.fakes["events"].entries.clear()
        self.use({}, list_pages=[[]])
        out = self.run_event(discover=True, checks=["INF-01"])
        self.assertEqual((out["published"], out["skipped"][0]["check_id"]), (0, "INF-01"))
        self.assertEqual(self.fakes["events"].entries, [])

    def test_get_metric_data_pagination_is_merged_and_bounded(self):
        cw = self.use({("i-0abc12345678def00",): IDLE}, data_pages=3)
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True)
        self.assertEqual(out["result_payloads"][0]["findings"][0]["evidence"][4]["value"], 360)
        self.assertEqual(sum(1 for name, _ in cw.calls if name == "get_metric_data"), 3)
        cw = self.use({("i-0abc12345678def00",): IDLE}, data_pages=3)
        series = metrics.fetch_cpu_series(cw, [metrics.parse_resource({"type": "ec2", "id": "i-0abc12345678def00"})],
                                          NOW - dt.timedelta(days=15), NOW, 3600, max_pages=2)
        self.assertFalse(series["ec2/i-0abc12345678def00"]["complete"])

    def test_scope_is_chunked_so_each_event_stays_small(self):
        ids = [f"i-0abc1234567800{i:02d}" for i in range(5)]
        self.use({(i,): IDLE for i in ids})
        out = self.run_event(resources=[{"type": "ec2", "id": i} for i in ids], scope_per_payload=2)
        self.assertEqual([r["scope"] for r in out["results"]], [2, 2, 1])
        self.assertEqual(len(self.fakes["events"].entries), 3)
        self.assertEqual({r["scan_id"] for r in self.published()}, {"scan-1"})

    def test_assume_role_reads_through_the_role_but_publishes_as_itself(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], role_arn=ROLE,
                             external_id="ext-123")
        self.assertTrue(out["assumed_role"])
        self.assertEqual(self.fakes["sts"].calls, [{"RoleArn": ROLE, "RoleSessionName": "owner-d-telemetry-reader",
                                                    "DurationSeconds": 900, "ExternalId": "ext-123"}])
        self.assertIn(("cloudwatch", "ap-south-1", "ASIA1"), self.created)
        self.assertIn(("events", "ap-south-1", None), self.created)
        self.assertNotIn(("cloudwatch", "ap-south-1", None), self.created)

    def test_assumed_credentials_are_cached_then_refreshed_near_expiry(self):
        readers = common.Readers(ROLE)
        readers.client("cloudwatch")
        readers.client("cloudwatch")
        self.assertEqual(len(self.fakes["sts"].calls), 1)
        common._now = lambda: NOW + dt.timedelta(minutes=14)
        readers.client("cloudwatch")
        self.assertEqual(len(self.fakes["sts"].calls), 2)
        self.assertIn(("cloudwatch", "ap-south-1", "ASIA2"), self.created)

    def test_bad_role_or_region_is_refused(self):
        for bad in ("owner-d-telemetry-readonly", "arn:aws:iam::123:role/x", 42):
            with self.assertRaises(ValueError):
                common.Readers(bad)
        with mock.patch.dict("os.environ", {"AWS_REGION": "us-east-1"}):
            with self.assertRaises(RuntimeError):
                self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}])

    def test_event_validation(self):
        for event in ({"resources": []}, {"resources": [{"type": "ec2", "id": "i-0abc12345678def00"}],
                                          "window": {"lookback_days": 31}},
                      {"resources": [{"type": "ec2", "id": "i-0abc12345678def00"}], "window": {"period_seconds": 90}},
                      {"resources": [{"type": "ec2", "id": "i-0abc12345678def00"}], "checks": ["OBS-07"]},
                      {"resources": [{"type": "ec2", "id": "i-0abc12345678def00"}], "scope_per_payload": 0}):
            with self.assertRaises(ValueError, msg=event):
                self.run_event(**event)
        with self.assertRaises(ValueError):
            telemetry_handler.lambda_handler({"commit_sha": "abc", "resources": []})

    def test_missing_bus_raises(self):
        self.fakes["events"] = FakeEvents(bus_exists=False)
        self.use({("i-0abc12345678def00",): IDLE})
        with self.assertRaises(RuntimeError):
            self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}])

    def test_oversized_result_is_refused(self):
        result = {"check_id": "INF-01", "padding": "x" * common.MAX_DETAIL_BYTES}
        with self.assertRaises(RuntimeError):
            common.event_entries([result], bus_name="findings-hub", source="owner-d.telemetry-analyzer")

    def test_settings_override_and_invalid_settings_are_reported_not_clean(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True,
                             settings={"INF-01": {"average_utilization_threshold": 0.03}})
        self.assertEqual(out["results"][0]["findings"], 0)
        out = self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True,
                             settings={"INF-01": {"min_window_days": "x"}})
        self.assertEqual(out["results"][0]["status"], "unavailable")

    def test_probe_counts_without_publishing(self):
        self.use({("i-0abc12345678def00",): IDLE})
        out = telemetry_handler.lambda_handler({"probe": ["cpu_metrics"], "role_arn": ROLE,
                                                "resources": [{"type": "ec2", "id": "i-0abc12345678def00"}]})
        self.assertEqual(out["probe"]["cpu_metrics"]["resources"], 1)
        self.assertEqual(self.fakes["events"].entries, [])

    def test_list_metrics_collector_keeps_raw_pages_and_marks_truncation(self):
        page = [{"Namespace": "OwnerD/Demo", "MetricName": "Latency", "OwningAccounts": ["123456789012"],
                 "Dimensions": [{"Name": "request_id", "Value": "r1"}]}]
        self.use({}, list_pages=[page, page, page])
        raw = metrics.collect_list_metrics(self.base_event(list_metrics={"namespace": "OwnerD/Demo", "max_pages": 2}),
                                           common.Readers())
        self.assertTrue(raw["truncated"])
        self.assertEqual((len(raw["pages"]), "NextToken" in raw["pages"][-1]), (2, True))
        self.assertNotIn("OwningAccounts", raw["pages"][0]["Metrics"][0])
        self.assertNotIn("RecentlyActive", self.fakes["cloudwatch"].calls[0][1])

    def test_detector_failure_is_reported_and_nothing_invalid_is_published(self):
        self.use({("i-0abc12345678def00",): IDLE})
        with mock.patch("owner_d.inf01.evaluate", side_effect=ValueError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}])
        self.assertEqual(self.fakes["events"].entries, [])


class ValidateBeforePublishTests(AwsTestCase):
    """validate_pair runs on every input/result pair before PutEvents; invalid pairs are refused and reported."""

    IDS = ("i-0abc12345678def00", "i-0abc12345678def01")

    def setUp(self):
        super().setUp()
        self.fakes["cloudwatch"] = FakeCloudWatch({(i,): IDLE for i in self.IDS})

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(
            self.base_event(resources=[{"type": "ec2", "id": i} for i in self.IDS], scope_per_payload=1, **extra))

    def test_valid_pairs_are_validated_then_published(self):
        from shared.contracts import validation
        with mock.patch.object(validation, "validate_pair", wraps=validation.validate_pair) as spy:
            out = self.run_event()
        self.assertEqual(spy.call_count, 2)
        for (payload, result), entry in zip((c.args for c in spy.call_args_list), self.fakes["events"].entries):
            self.assertEqual((payload["kind"], result["kind"]), ("input", "result"))
            self.assertEqual(json.loads(entry["Detail"]), result)
        self.assertEqual((out["published"], out["contract_validated"], out["refused"], out["errors"]),
                         (2, True, [], []))

    def test_invalid_result_is_refused_reported_and_the_valid_one_still_published(self):
        from owner_d import inf01
        real = inf01.evaluate

        def tampered(payload):
            result = real(payload)
            if payload["scope"] == ["resource:ec2/i-0abc12345678def01"]:
                result["findings"][0]["fingerprint"] = "0" * 64  # not derived from the finding identity
            return result

        with mock.patch.object(inf01, "evaluate", side_effect=tampered):
            out = self.run_event()
        self.assertEqual(out["published"], 1)
        self.assertEqual(out["refused"], [{"check_id": "INF-01", "scope": 1, "status": "completed",
                                           "error": "ContractError: Incorrect finding fingerprint"}])
        self.assertEqual(out["errors"], [])
        published = [json.loads(e["Detail"]) for e in self.fakes["events"].entries]
        self.assertEqual([r["scope"] for r in published], [["resource:ec2/i-0abc12345678def00"]])

    def test_result_that_does_not_match_its_input_is_refused_in_dry_run_too(self):
        from owner_d import inf01
        real = inf01.evaluate
        with mock.patch.object(inf01, "evaluate", side_effect=lambda p: {**real(p), "scan_id": "other-scan"}):
            out = self.run_event(dry_run=True)
        self.assertEqual((out["published"], out["result_payloads"], len(out["refused"])), (0, [], 2))
        self.assertIn("identity or context mismatch", out["refused"][0]["error"])
        self.assertEqual(self.fakes["events"].entries, [])

    def test_refused_report_is_bounded(self):
        from owner_d import inf01
        real = inf01.evaluate

        def huge(payload):
            result = real(payload)
            result["coverage"]["limitations"].append(12345)  # schema error quoting payload values
            result["findings"][0]["evidence"][0]["value"] = "x" * 5000
            return result

        with mock.patch.object(inf01, "evaluate", side_effect=huge):
            out = self.run_event()
        self.assertEqual(out["published"], 0)
        self.assertTrue(all(len(r["error"]) <= common.MAX_ERROR_CHARS for r in out["refused"]))

    def test_missing_jsonschema_fails_closed_before_reading(self):
        with mock.patch.dict("sys.modules", {"shared.contracts.validation": None}):
            with self.assertRaisesRegex(RuntimeError, "refusing to evaluate or publish"):
                self.run_event()
        self.assertEqual((self.fakes["cloudwatch"].calls, self.fakes["events"].entries), ([], []))


class ExternalIdTests(AwsTestCase):
    """READONLY_EXTERNAL_ID (set by the template) is sent only when assuming READONLY_ROLE_ARN."""

    OTHER = "arn:aws:iam::210987654321:role/client-telemetry-readonly"

    def setUp(self):
        super().setUp()
        self.fakes["cloudwatch"] = FakeCloudWatch({("i-0abc12345678def00",): IDLE})
        self.env = mock.patch.dict("os.environ", {"READONLY_ROLE_ARN": ROLE,
                                                  "READONLY_EXTERNAL_ID": "env-external-id-0123456789"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_event(self, **extra):
        return telemetry_handler.lambda_handler(
            self.base_event(resources=[{"type": "ec2", "id": "i-0abc12345678def00"}], dry_run=True, **extra))

    def test_stack_role_uses_the_env_external_id(self):
        out = self.run_event(role_arn=ROLE)
        self.assertEqual(self.fakes["sts"].calls[0]["ExternalId"], "env-external-id-0123456789")
        self.assertNotIn("env-external-id", json.dumps(out))

    def test_event_external_id_wins(self):
        self.run_event(role_arn=ROLE, external_id="event-external-id")
        self.assertEqual(self.fakes["sts"].calls[0]["ExternalId"], "event-external-id")

    def test_env_external_id_is_never_sent_to_another_role(self):
        self.run_event(role_arn=self.OTHER)
        self.assertNotIn("ExternalId", self.fakes["sts"].calls[0])

    def test_probe_uses_the_env_external_id(self):
        telemetry_handler.lambda_handler({"probe": ["cpu_metrics"], "role_arn": ROLE,
                                          "resources": [{"type": "ec2", "id": "i-0abc12345678def00"}]})
        self.assertEqual(self.fakes["sts"].calls[0]["ExternalId"], "env-external-id-0123456789")


class StsFailureTests(AwsTestCase):
    def test_access_denied_on_assume_role_raises_before_publishing(self):
        class Denied(FakeSts):
            def assume_role(self, **kw):
                raise ClientError("AccessDenied")

        self.fakes["sts"] = Denied()
        with self.assertRaises(ClientError):
            telemetry_handler.lambda_handler(self.base_event(role_arn=ROLE,
                                                             resources=[{"type": "ec2", "id": "i-0abc12345678def00"}]))
        self.assertEqual(self.fakes["events"].entries, [])


if __name__ == "__main__":
    unittest.main()
