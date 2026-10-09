"""Offline tests for the owner-d telemetry demo workload (issue #376). boto3 and X-Ray are stubbed."""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

HANDLER = Path(__file__).resolve().parents[1] / "examples" / "telemetry-demo" / "handler.py"
_spec = importlib.util.spec_from_file_location("owner_d_telemetry_demo", HANDLER)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


class FakeCloudWatch:
    def __init__(self):
        self.calls = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)


class FakeXRay:
    def __init__(self):
        self.documents = []

    def send(self, document):
        self.documents.append(document)
        return "sent"


def run(**event):
    lines, cloudwatch, xray = [], FakeCloudWatch(), FakeXRay()
    with mock.patch.object(demo, "TOOL_CALL_SECONDS", 0):
        result = demo.run(event, write=lines.append, cloudwatch=cloudwatch, xray=xray)
    return result, lines, cloudwatch, xray


def records(lines):
    return [json.loads(line) for line in lines if line.startswith("{")]


def series_key(datum):
    return datum["MetricName"], tuple(sorted((d["Name"], d["Value"]) for d in datum["Dimensions"]))


class ScenarioSelection(unittest.TestCase):
    def test_all_is_default_and_runs_every_scenario(self):
        result, _, cloudwatch, xray = run()
        self.assertEqual(result["scenarios"], list(demo.SCENARIOS))
        self.assertTrue(result["synthetic"])
        self.assertEqual(len(cloudwatch.calls), 1)
        self.assertEqual(len(xray.documents), 2)

    def test_aliases_select_one_scenario(self):
        for alias in ("OBS-11", "obs11", "obs-11", "obs_11"):
            result, _, cloudwatch, xray = run(scenario=alias)
            self.assertEqual(result["scenarios"], ["OBS-11"])
            self.assertEqual((cloudwatch.calls, xray.documents), ([], []))

    def test_unknown_scenario_and_bad_knob_are_rejected(self):
        with self.assertRaises(ValueError):
            run(scenario="OBS-99")
        with self.assertRaises(ValueError):
            run(scenario="OBS-11", repeat="20")

    def test_every_log_line_is_labeled_synthetic(self):
        _, lines, _, _ = run()
        for line in lines:
            if line.startswith("{"):
                record = json.loads(line)
                self.assertIs(record["synthetic"], True)
                self.assertIn(record["path"], ("waste", "control"))
            else:
                self.assertIn("synthetic=true", line)


class Obs11(unittest.TestCase):
    def test_retry_loop_repeats_identical_line_and_control_logs_once(self):
        result, lines, _, _ = run(scenario="OBS-11", repeat=7)
        waste = [line for line, r in zip(lines, records(lines)) if r["path"] == "waste"]
        control = [r for r in records(lines) if r["path"] == "control"]
        self.assertEqual(len(waste), 7)
        self.assertEqual(len(set(waste)), 1)
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0]["attempts"], 7)
        self.assertEqual(result["emitted"]["OBS-11"], {"waste_lines": 7, "control_lines": 1})

    def test_repeat_is_clamped(self):
        result, _, _, _ = run(scenario="OBS-11", repeat=10_000)
        self.assertEqual(result["emitted"]["OBS-11"]["waste_lines"], 50)


class Obs17(unittest.TestCase):
    def test_waste_line_carries_stack_trace_and_body_control_does_not(self):
        result, lines, _, _ = run(scenario="OBS-17")
        waste, control = records(lines)
        self.assertIn("Traceback (most recent call last)", waste["stack_trace"])
        self.assertIn("ValueError", waste["stack_trace"])
        self.assertEqual(len(waste["request_body"]["items"]), 40)
        self.assertNotIn("stack_trace", control)
        self.assertNotIn("request_body", control)
        self.assertEqual(control["error_type"], "RuntimeError")
        self.assertEqual(len(control["body_sha256"]), 16)
        emitted = result["emitted"]["OBS-17"]
        self.assertGreater(emitted["waste_bytes"], 10 * emitted["control_bytes"])


class Obs04(unittest.TestCase):
    def test_same_events_as_free_text_and_as_json(self):
        _, lines, _, _ = run(scenario="OBS-04")
        text = [line for line in lines if not line.startswith("{")]
        structured = records(lines)
        self.assertEqual(len(text), len(demo.EVENTS))
        self.assertEqual(len(structured), len(demo.EVENTS))
        for line in text:
            self.assertIn("check=OBS-04 path=waste", line)
            with self.assertRaises(json.JSONDecodeError):
                json.loads(line)
        self.assertEqual({r["path"] for r in structured}, {"control"})
        self.assertEqual([r["message"] for r in structured], [e[1] for e in demo.EVENTS])
        self.assertEqual(structured[1]["ms"], 2310)


class Obs06(unittest.TestCase):
    def test_metric_shape_and_dimensions(self):
        result, _, cloudwatch, _ = run(scenario="OBS-06", series=5)
        (call,) = cloudwatch.calls
        self.assertEqual(call["Namespace"], "OwnerD/Demo")
        data = call["MetricData"]
        waste = [d for d in data if {"Name": "path", "Value": "waste"} in d["Dimensions"]]
        control = [d for d in data if {"Name": "path", "Value": "control"} in d["Dimensions"]]
        self.assertEqual(len(waste), 5)
        self.assertEqual(len(control), len(demo.ENDPOINTS))
        for datum in data:
            self.assertEqual(datum["MetricName"], demo.METRIC_NAME)
            self.assertIn({"Name": "synthetic", "Value": "true"}, datum["Dimensions"])
            self.assertEqual(datum["Unit"], "Milliseconds")
        self.assertEqual(len({dict((d["Name"], d["Value"]) for d in w["Dimensions"])["request_id"] for w in waste}), 5)
        self.assertEqual(result["emitted"]["OBS-06"], {"waste_series": 5, "control_series": 2})

    def test_distinct_series_are_bounded_across_many_invocations(self):
        seen = set()
        for series in (1, 7, 40, 41, 10_000):
            for _ in range(3):
                _, _, cloudwatch, _ = run(scenario="OBS-06", series=series)
                for call in cloudwatch.calls:
                    self.assertLessEqual(len(call["MetricData"]), 1000)
                    seen.update(series_key(d) for d in call["MetricData"])
        self.assertEqual(len(seen), demo.MAX_METRIC_SERIES)
        self.assertLessEqual(demo.MAX_METRIC_SERIES, 50)


class Llm10(unittest.TestCase):
    def test_unbounded_loop_repeats_identical_tool_calls(self):
        result, lines, _, xray = run(scenario="LLM-10", tool_calls=9)
        waste, control = xray.documents
        self.assertEqual(waste["name"], "agent_loop")
        self.assertEqual(waste["annotations"]["path"], "waste")
        self.assertEqual(waste["annotations"]["stop_reason"], "max_iterations")
        calls = waste["subsegments"]
        self.assertEqual(len(calls), 9)
        self.assertEqual({c["name"] for c in calls}, {"tool:get_order_status"})
        self.assertEqual(len({c["annotations"]["args_hash"] for c in calls}), 1)
        self.assertEqual(len({c["id"] for c in calls}), 9)
        for doc in [waste, control, *calls, *control["subsegments"]]:
            self.assertIs(doc["annotations"]["synthetic"], True)
            self.assertEqual(doc["annotations"]["check"], "LLM-10")
            self.assertRegex(doc["id"], r"^[0-9a-f]{16}$")
            self.assertLessEqual(doc["start_time"], doc["end_time"])
        self.assertEqual(control["annotations"]["stop_reason"], "answer_ready")
        self.assertEqual(len(control["subsegments"]), 2)
        self.assertEqual(len({c["annotations"]["args_hash"] for c in control["subsegments"]}), 2)
        self.assertEqual([r["iterations"] for r in records(lines)], [9, 2])
        self.assertEqual(result["emitted"]["LLM-10"]["xray"], "sent")

    def test_tool_calls_clamped(self):
        _, _, _, xray = run(scenario="LLM-10", tool_calls=500)
        self.assertEqual(len(xray.documents[0]["subsegments"]), 25)


class XRayDaemon(unittest.TestCase):
    HEADER = "Root=1-5f84c7a1-0123456789abcdef01234567;Parent=0123456789abcdef;Sampled=1;Lineage=a:0"

    def test_payload_is_independent_subsegment_under_lambda_segment(self):
        daemon = demo.XRayDaemon(self.HEADER, ("127.0.0.1", 2000))
        header, body = daemon.payload({"id": "abcdef0123456789", "name": "agent_loop"}).split(b"\n", 1)
        self.assertEqual(json.loads(header), {"format": "json", "version": 1})
        doc = json.loads(body)
        self.assertEqual(doc["type"], "subsegment")
        self.assertEqual(doc["trace_id"], "1-5f84c7a1-0123456789abcdef01234567")
        self.assertEqual(doc["parent_id"], "0123456789abcdef")

    def test_unsampled_or_missing_trace_sends_nothing(self):
        with mock.patch.object(demo.socket, "socket") as sock:
            self.assertEqual(demo.XRayDaemon(self.HEADER.replace("Sampled=1", "Sampled=0"), None).send({}),
                             "not_sampled")
            self.assertEqual(demo.XRayDaemon(None, None).send({}), "not_sampled")
            sock.assert_not_called()

    def test_sampled_trace_sends_one_udp_datagram(self):
        env = {"_X_AMZN_TRACE_ID": self.HEADER, "AWS_XRAY_DAEMON_ADDRESS": "169.254.79.129:2000"}
        with mock.patch.dict(os.environ, env), mock.patch.object(demo.socket, "socket") as sock:
            self.assertEqual(demo.XRayDaemon.from_env().send({"id": "abcdef0123456789"}), "sent")
        sendto = sock.return_value.__enter__.return_value.sendto
        sendto.assert_called_once()
        self.assertEqual(sendto.call_args.args[1], ("169.254.79.129", 2000))

    def test_daemon_address_with_udp_and_tcp_parts(self):
        env = {"AWS_XRAY_DAEMON_ADDRESS": "tcp:10.0.0.1:2000 udp:10.0.0.2:2001"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(demo.XRayDaemon.from_env().address, ("10.0.0.2", 2001))


class LambdaHandler(unittest.TestCase):
    def test_handler_uses_boto3_and_daemon_lazily(self):
        cloudwatch, xray = FakeCloudWatch(), FakeXRay()
        with mock.patch.object(demo, "_boto3_client", return_value=cloudwatch) as client, \
                mock.patch.object(demo.XRayDaemon, "from_env", return_value=xray), \
                mock.patch.object(demo, "_stdout") as stdout, \
                mock.patch.object(demo, "TOOL_CALL_SECONDS", 0):
            result = demo.lambda_handler({"scenario": "all"}, None)
        client.assert_called_once_with("cloudwatch")
        self.assertEqual(len(cloudwatch.calls), 1)
        self.assertEqual(len(xray.documents), 2)
        self.assertGreater(stdout.call_count, 20)
        self.assertEqual(result["scenarios"], list(demo.SCENARIOS))


if __name__ == "__main__":
    unittest.main()
