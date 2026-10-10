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
    with mock.patch.object(demo, "SPAN_SECONDS", 0):
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


def genai(doc):
    return doc["metadata"]["default"]


def tools(run_doc):
    return [c for c in run_doc["subsegments"] if genai(c)["gen_ai.operation.name"] == "execute_tool"]


class Llm10(unittest.TestCase):
    def test_spans_carry_otel_genai_attributes(self):
        _, _, _, xray = run(scenario="LLM-10", tool_calls=4)
        for agent in xray.documents:
            self.assertTrue(agent["name"].startswith("invoke_agent "))
            self.assertEqual(genai(agent)["gen_ai.operation.name"], "invoke_agent")
            self.assertEqual(agent["name"], f"invoke_agent {genai(agent)['gen_ai.agent.name']}")
            for child in agent["subsegments"]:
                attrs = genai(child)
                if attrs["gen_ai.operation.name"] == "chat":
                    self.assertEqual(child["name"], f"chat {attrs['gen_ai.request.model']}")
                    self.assertEqual(attrs["gen_ai.request.model"], demo.DEMO_MODEL)
                else:
                    self.assertEqual(attrs["gen_ai.operation.name"], "execute_tool")
                    self.assertEqual(child["name"], f"execute_tool {attrs['gen_ai.tool.name']}")
                    self.assertIsInstance(attrs["gen_ai.tool.call.arguments"], str)
                    json.loads(attrs["gen_ai.tool.call.arguments"])
            for doc in [agent, *agent["subsegments"]]:
                self.assertIs(doc["annotations"]["synthetic"], True)
                self.assertEqual(doc["annotations"]["check"], "LLM-10")
                self.assertRegex(doc["id"], r"^[0-9a-f]{16}$")
                self.assertLessEqual(doc["start_time"], doc["end_time"])

    def test_waste_repeats_identical_arguments_past_threshold_and_control_does_not(self):
        result, lines, _, xray = run(scenario="LLM-10")
        waste, control = xray.documents
        calls = tools(waste)
        self.assertEqual(len(calls), 12)
        self.assertGreater(len(calls), 3)  # LLM-10 reference max_identical_tool_calls
        self.assertEqual({genai(c)["gen_ai.tool.name"] for c in calls}, {"get_order_status"})
        self.assertEqual(len({genai(c)["gen_ai.tool.call.arguments"] for c in calls}), 1)
        self.assertEqual(waste["annotations"]["stop_reason"], "max_iterations")
        self.assertEqual(waste["annotations"]["llm_calls"], 12)
        clean = tools(control)
        self.assertEqual([genai(c)["gen_ai.tool.name"] for c in clean], ["get_order_status", "draft_reply"])
        self.assertEqual(control["annotations"]["stop_reason"], "answer_ready")
        self.assertEqual(control["annotations"]["llm_calls"], 3)
        self.assertEqual([(r["path"], r["tool_calls"]) for r in records(lines)], [("waste", 12), ("control", 2)])
        self.assertEqual(result["emitted"]["LLM-10"], {"xray": "sent", "waste_llm_calls": 12,
                                                       "waste_tool_calls": 12, "control_llm_calls": 3,
                                                       "control_tool_calls": 2})

    def test_path_selects_one_run_and_tool_calls_are_clamped(self):
        _, _, _, xray = run(scenario="LLM-10", path="control")
        self.assertEqual([d["annotations"]["path"] for d in xray.documents], ["control"])
        _, _, _, xray = run(scenario="LLM-10", path="waste", tool_calls=500)
        self.assertEqual(len(tools(xray.documents[0])), 25)
        with self.assertRaises(ValueError):
            run(scenario="LLM-10", path="sometimes")


try:  # owner_d.llm10 lands with PR #396; until then only the attribute shape above is asserted.
    from owner_d import llm10
except ImportError:
    llm10 = None


FUNCTION = "owner-d-telemetry-demo"


def batch_get_traces(invocations, path, scenario="LLM-10"):
    """Wrap the emitted documents the way BatchGetTraces returns a Lambda trace with independent subsegments."""
    traces = []
    for i in range(invocations):
        trace_id, lambda_id, function_id = f"1-6a000000-{i:024x}", f"a{i:015x}", f"f{i:015x}"
        daemon = demo.XRayDaemon(f"Root={trace_id};Parent={function_id};Sampled=1", None)
        _, _, _, xray = run(scenario=scenario, path=path)
        start, end = xray.documents[0]["start_time"] - 0.01, xray.documents[-1]["end_time"] + 0.01
        segments = [
            {"id": lambda_id, "name": FUNCTION, "trace_id": trace_id, "start_time": start, "end_time": end,
             "origin": "AWS::Lambda"},
            {"id": function_id, "name": FUNCTION, "trace_id": trace_id, "parent_id": lambda_id,
             "start_time": start, "end_time": end, "origin": "AWS::Lambda::Function"},
        ]
        segments += [json.loads(daemon.payload(doc).split(b"\n", 1)[1]) for doc in xray.documents]
        traces.append({"Id": trace_id, "Segments": [{"Id": s["id"], "Document": json.dumps(s)} for s in segments]})
    return traces


@unittest.skipIf(llm10 is None, "owner_d.llm10 not on this branch yet")
class Llm10Detector(unittest.TestCase):
    CONTEXT = {"min_traces": 10, "max_llm_iterations": 10, "max_identical_tool_calls": 3}

    def evaluate(self, traces):
        scope, sources = llm10.telemetry_sources(llm10.normalize_xray_traces(traces))
        return llm10.evaluate({
            "schema_version": "1.0", "kind": "input", "repository_id": "github:AWS-env/telemetry-demo",
            "scan_id": "scan-telemetry-demo", "commit_sha": "0" * 40, "check_id": "LLM-10",
            "detector_version": llm10.DETECTOR_VERSION, "context": self.CONTEXT, "scope": scope,
            "sources": sources})

    def test_ten_demo_invocations_normalize_to_one_entrypoint(self):
        normalized = llm10.normalize_xray_traces(batch_get_traces(10, "both"))
        self.assertEqual(normalized["skipped_traces"], [])
        data = normalized["entrypoints"][FUNCTION]
        self.assertEqual(data["traces_analyzed"], 10)
        worst = data["max_identical_tool_calls"]
        self.assertEqual((worst["agent"], worst["tool"], worst["calls"]), ("demo_unbounded_agent", "get_order_status", 12))
        self.assertEqual(data["max_llm_iterations"]["llm_calls"], 12)

    def test_waste_is_flagged_and_control_is_clean(self):
        waste = self.evaluate(batch_get_traces(10, "waste"))
        self.assertEqual(waste["coverage"]["evaluated_scope"], [f"entrypoint:{FUNCTION}"])
        self.assertEqual({f["identity"] for f in waste["findings"]}, {"identical-tool-calls", "iteration-budget"})
        self.assertIn("demo_unbounded_agent", json.dumps(waste["findings"]))
        control = self.evaluate(batch_get_traces(10, "control"))
        self.assertEqual(control["status"], "completed")
        self.assertEqual(control["coverage"]["evaluated_scope"], [f"entrypoint:{FUNCTION}"])
        self.assertEqual(control["findings"], [])

    def test_nine_invocations_are_below_min_traces(self):
        result = self.evaluate(batch_get_traces(9, "waste"))
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertIn("below the required min_traces 10", json.dumps(result["coverage"]["limitations"]))


class Llm05(unittest.TestCase):
    """Opt-in LLM-05 scenario: 2 chained `chat` calls with the same request (waste) vs a real chain (control)."""

    def test_waste_repeats_the_request_and_control_carries_the_first_output(self):
        result, lines, cloudwatch, xray = run(scenario="LLM-05")
        waste, control = xray.documents
        self.assertEqual(waste["name"], "invoke_agent demo_redundant_pipeline")
        self.assertEqual(control["name"], "invoke_agent demo_chained_pipeline")
        for agent in (waste, control):
            self.assertEqual([genai(c)["gen_ai.operation.name"] for c in agent["subsegments"]], ["chat", "chat"])
            self.assertEqual({genai(c)["gen_ai.request.model"] for c in agent["subsegments"]}, {demo.DEMO_MODEL})
            for doc in [agent, *agent["subsegments"]]:
                self.assertIs(doc["annotations"]["synthetic"], True)
                self.assertEqual(doc["annotations"]["check"], "LLM-05")
                self.assertRegex(doc["id"], r"^[0-9a-f]{16}$")
        digests = [[genai(c)["gen_ai.input.messages.hash"] for c in a["subsegments"]] for a in (waste, control)]
        self.assertEqual(len(set(digests[0])), 1)
        self.assertEqual(len(set(digests[1])), 2)
        self.assertEqual(digests[0][0], digests[1][0])  # same first step on both paths
        self.assertNotIn(demo.PIPELINE_QUESTION, json.dumps(xray.documents))  # digest only, no prompt content
        self.assertEqual(cloudwatch.calls, [])
        self.assertEqual([(r["path"], r["distinct_requests"]) for r in records(lines)], [("waste", 1), ("control", 2)])
        self.assertEqual(result["emitted"]["LLM-05"], {"xray": "sent", "waste_llm_calls": 2,
                                                       "waste_distinct_requests": 1, "control_llm_calls": 2,
                                                       "control_distinct_requests": 2})

    def test_opt_in_only_and_path_selection(self):
        self.assertNotIn("LLM-05", demo.SCENARIOS)
        self.assertEqual(run(scenario="llm05")[0]["scenarios"], ["LLM-05"])
        _, _, _, xray = run(scenario="LLM-05", path="control")
        self.assertEqual([d["annotations"]["path"] for d in xray.documents], ["control"])
        with self.assertRaises(ValueError):
            run(scenario="LLM-05", path="sometimes")


try:
    from owner_d import llm05
except ImportError:
    llm05 = None


@unittest.skipIf(llm05 is None, "owner_d.llm05 not on this branch")
class Llm05Detector(unittest.TestCase):
    def evaluate(self, traces):
        scope, sources = llm05.telemetry_sources(llm05.normalize_xray_traces(traces))
        payload = {
            "schema_version": "1.0", "kind": "input", "repository_id": "github:AWS-env/telemetry-demo",
            "scan_id": "scan-telemetry-demo", "commit_sha": "0" * 40, "check_id": "LLM-05",
            "detector_version": llm05.DETECTOR_VERSION, "context": dict(llm05.REFERENCE_SETTINGS), "scope": scope,
            "sources": sources}
        return llm05.evaluate(payload)

    def test_waste_is_flagged_and_control_is_clean(self):
        waste = self.evaluate(batch_get_traces(10, "waste", scenario="LLM-05"))
        self.assertEqual(waste["coverage"]["evaluated_scope"], [f"entrypoint:{FUNCTION}"])
        self.assertEqual([f["identity"] for f in waste["findings"]], ["consecutive-identical-calls"])
        self.assertIn("2 consecutive identical synthetic-demo-model calls", waste["findings"][0]["summary"])
        self.assertIn("by demo_redundant_pipeline", waste["findings"][0]["summary"])
        control = self.evaluate(batch_get_traces(10, "control", scenario="LLM-05"))
        self.assertEqual((control["status"], control["findings"]), ("completed", []))

    def test_llm10_demo_traffic_gives_llm05_a_limitation_not_a_clean_result(self):
        result = self.evaluate(batch_get_traces(10, "both"))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("recorded no comparable input", json.dumps(result["coverage"]["limitations"]))


class Llm17(unittest.TestCase):
    """Opt-in LLM-17 scenario: a fixed agent pool that idles with one burst a day (waste) vs a steady pool."""

    NOW = 1_791_633_600 + 1_234  # 2026-10-10T12:20:34Z, inside an hour

    def run_llm17(self, **event):
        with mock.patch.object(demo, "_now", return_value=self.NOW):
            return run(scenario="LLM-17", **event)

    def test_backfills_hourly_statistic_sets_for_both_paths(self):
        result, lines, cloudwatch, xray = self.run_llm17()
        (call,) = cloudwatch.calls
        self.assertEqual(call["Namespace"], "OwnerD/Demo")
        data = call["MetricData"]
        hours = demo.LLM17_DAYS * 24
        self.assertEqual(len(data), 2 * hours)
        end = self.NOW // 3600 * 3600
        for datum in data:
            self.assertEqual((datum["MetricName"], datum["Unit"]), (demo.LLM17_METRIC, "Percent"))
            self.assertIn({"Name": "synthetic", "Value": "true"}, datum["Dimensions"])
            self.assertIn({"Name": "check", "Value": "LLM-17"}, datum["Dimensions"])
            stamp = datum["Timestamp"].timestamp()
            self.assertTrue(end - 14 * 86400 < stamp < end and stamp % 3600 == 0)  # completed hours, < 2 weeks
            stats = datum["StatisticValues"]
            self.assertLessEqual(stats["Minimum"], stats["Sum"] / stats["SampleCount"])
            self.assertLessEqual(stats["Sum"] / stats["SampleCount"], stats["Maximum"])
            self.assertLessEqual(stats["Maximum"], 100)
        self.assertEqual(len({series_key(d) for d in data}), demo.LLM17_SERIES)
        self.assertEqual(xray.documents, [])
        self.assertEqual([(r["path"], r["hours"]) for r in records(lines)], [("waste", hours), ("control", hours)])
        self.assertEqual(result["emitted"]["LLM-17"], {"waste_datapoints": hours, "control_datapoints": hours,
                                                       "days": demo.LLM17_DAYS})

    def test_opt_in_only_and_path_selection(self):
        self.assertNotIn("LLM-17", demo.SCENARIOS)
        self.assertEqual(self.run_llm17()[0]["scenarios"], ["LLM-17"])
        with mock.patch.object(demo, "_now", return_value=self.NOW):
            self.assertEqual(run(scenario="llm17")[0]["scenarios"], ["LLM-17"])
        _, _, cloudwatch, _ = self.run_llm17(path="control")
        paths = {dict((d["Name"], d["Value"]) for d in datum["Dimensions"])["path"]
                 for datum in cloudwatch.calls[0]["MetricData"]}
        self.assertEqual(paths, {"control"})
        with self.assertRaises(ValueError):
            self.run_llm17(path="sometimes")

    def test_series_stay_bounded_and_values_repeat_across_runs(self):
        first = self.run_llm17()[2].calls[0]["MetricData"]
        again = self.run_llm17()[2].calls[0]["MetricData"]
        self.assertEqual(first, again)
        self.assertEqual(len({series_key(d) for d in first + again}), demo.LLM17_SERIES)


try:
    from owner_d import llm17
    from owner_d.aws import metrics as owner_d_metrics
except ImportError:
    llm17 = None


@unittest.skipIf(llm17 is None, "owner_d.llm17 not on this branch")
class Llm17Detector(unittest.TestCase):
    """The demo series, read back as GetMetricData would return them, through owner D's LLM-17."""

    def evaluate(self, path):
        with mock.patch.object(demo, "_now", return_value=Llm17.NOW):
            _, _, cloudwatch, _ = run(scenario="LLM-17", path=path)
        data = cloudwatch.calls[0]["MetricData"]
        stamps = [d["Timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ") for d in data]
        series = {"average": {"timestamps": stamps,
                              "values": [d["StatisticValues"]["Sum"] / d["StatisticValues"]["SampleCount"]
                                         for d in data]},
                  "maximum": {"timestamps": stamps, "values": [d["StatisticValues"]["Maximum"] for d in data]},
                  "complete": True, "messages": []}
        spec = {"type": "custom", "name": f"demo-agent-pool-{path}", "namespace": demo.NAMESPACE,
                "metric_name": demo.LLM17_METRIC, "dimensions": {"synthetic": "true", "check": "LLM-17", "path": path},
                "provisioned_capacity": demo.LLM17_WORKERS, "capacity_unit": "worker", "autoscaling": False}
        resource = owner_d_metrics.parse_capacity(spec)
        normalized = owner_d_metrics.normalize_capacity_metrics({
            "window": {"start": stamps[0], "end": stamps[-1]}, "period_seconds": 3600, "region": "ap-south-1",
            "resources": [resource], "series": {resource["id"]: series}})
        payload = {
            "schema_version": "1.0", "kind": "input", "repository_id": "github:AWS-env/telemetry-demo",
            "scan_id": "scan-telemetry-demo", "commit_sha": "0" * 40, "check_id": "LLM-17",
            "detector_version": llm17.DETECTOR_VERSION, "context": dict(llm17.REFERENCE_SETTINGS),
            "scope": normalized["scope"], "sources": normalized["sources"]}
        return llm17.evaluate(payload)

    def test_waste_is_flagged_and_control_is_clean(self):
        waste = self.evaluate("waste")
        self.assertEqual([f["identity"] for f in waste["findings"]], ["bursty-fixed-capacity"])
        self.assertEqual(waste["findings"][0]["confidence"], "high")
        self.assertIn("holds 4 worker of fixed capacity (no autoscaling)", waste["findings"][0]["summary"])
        control = self.evaluate("control")
        self.assertEqual((control["status"], control["findings"]), ("completed", []))


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
                mock.patch.object(demo, "SPAN_SECONDS", 0):
            result = demo.lambda_handler({"scenario": "all"}, None)
        client.assert_called_once_with("cloudwatch")
        self.assertEqual(len(cloudwatch.calls), 1)
        self.assertEqual(len(xray.documents), 2)
        self.assertGreater(stdout.call_count, 20)
        self.assertEqual(result["scenarios"], list(demo.SCENARIOS))


if __name__ == "__main__":
    unittest.main()
