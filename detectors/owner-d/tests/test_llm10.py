"""Behavioral tests for the LLM-10 detector (issue #203), trace half.

Fixtures under fixtures/llm10 are SYNTHETIC BatchGetTraces responses shaped on the AWS X-Ray segment document
spec and real Lambda traces; they are not production telemetry.
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

from owner_d import cli, llm10
from owner_d.llm10 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint

from shared.contracts.validation import ContractError, validate_pair
from shared.contracts.validation import fingerprint as shared_fingerprint

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm10"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"min_traces": 3, "max_llm_iterations": 10, "max_identical_tool_calls": 3}


def traces(name):
    return json.loads((FIXTURES / name).read_text())["Traces"]


def normalized(*names, select=None):
    raw = [trace for name in names for trace in traces(name)]
    if select is not None:
        raw = [raw[i] for i in select]
    return llm10.normalize_xray_traces(raw)


def make_input(*names, select=None, sources=None, scope=None, context=None):
    if sources is None:
        scope_from, sources = llm10.telemetry_sources(normalized(*names, select=select))
        scope = scope_from if scope is None else scope
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-llm10-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(CONTEXT) if context is None else context,
        "scope": scope,
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


def entry(name, fixture, **kwargs):
    return normalized(fixture, **kwargs)["entrypoints"][name]


class Llm10NormalizerTests(unittest.TestCase):
    """The X-Ray normalizer counts GenAI calls per agent run from real BatchGetTraces shapes."""

    def test_metadata_annotation_and_independent_subsegment_styles(self):
        data = entry("support-agent", "positive.traces.json")
        by_trace = {t["trace_id"]: t for t in data["traces"]}
        otel, sdk, independent = (by_trace[t["Id"]] for t in traces("positive.traces.json"))
        # metadata.default with dotted keys (ADOT awsxrayexporter), nested under invoke_agent
        self.assertEqual(otel["max_run_iterations"], {"agent": "support_agent", "llm_calls": 7, "tool_calls": 6})
        self.assertEqual(otel["max_identical_tool_calls"]["calls"], 6)
        self.assertEqual(otel["max_agent_depth"], 1)
        # annotations gen_ai_* + structured arguments in a custom metadata namespace, no agent span
        self.assertEqual(sdk["max_run_iterations"], {"agent": "(entrypoint)", "llm_calls": 6, "tool_calls": 5})
        self.assertEqual(sdk["max_identical_tool_calls"]["calls"], 5)
        self.assertEqual(sdk["max_agent_depth"], 0)
        # every span sent as its own `type: subsegment` document
        self.assertEqual((independent["llm_calls"], independent["tool_calls"]), (12, 11))
        self.assertEqual(independent["max_identical_tool_calls"]["calls"], 1)
        self.assertEqual(data["traces_analyzed"], 3)
        self.assertEqual(data["max_identical_tool_calls"]["trace_id"], traces("positive.traces.json")[0]["Id"])
        self.assertEqual(data["max_llm_iterations"]["trace_id"], traces("positive.traces.json")[2]["Id"])

    def test_string_and_structured_arguments_hash_identically(self):
        def call(attrs):
            return {"id": "a" * 16, "name": "execute_tool t", "start_time": 1.0, "end_time": 1.1, **attrs}

        as_string = call({"metadata": {"default": {"gen_ai.tool.call.arguments": '{"b": 1, "a": [1, 2]}'}}})
        as_object = call({"annotations": {"gen_ai_tool_name": "t"}, "metadata": {"x": {"gen_ai.tool.call.arguments": {"a": [1, 2], "b": 1}}}})
        hashes = {llm10._arguments(llm10._attributes(n)["gen_ai.tool.call.arguments"])[0] for n in (as_string, as_object)}
        self.assertEqual(len(hashes), 1)

    def test_traces_without_genai_attributes_are_not_entrypoints(self):
        result = normalized("missing.traces.json")
        self.assertEqual(result["traces_received"], 3)
        self.assertEqual(result["traces_without_genai_calls"], 2)
        self.assertEqual(list(result["entrypoints"]), ["notes-agent"])
        notes = result["entrypoints"]["notes-agent"]
        self.assertEqual((notes["traces_with_genai_calls"], notes["traces_analyzed"]), (1, 0))
        self.assertEqual(notes["tool_calls_without_arguments"], 5)
        self.assertIsNone(notes["max_llm_iterations"])
        self.assertIsNone(notes["max_identical_tool_calls"])

    def test_malformed_traces_are_skipped_whole_with_reasons(self):
        result = normalized("malformed.traces.json")
        reasons = [item["reason"] for item in result["skipped_traces"]]
        self.assertEqual(len(reasons), 3)
        self.assertRegex(reasons[0], r"^segment [0-9a-f]{16}: Document is not a JSON object$")
        self.assertRegex(reasons[1], r"^segment [0-9a-f]{16} is in progress$")
        self.assertRegex(reasons[2], r"^subsegment [0-9a-f]{16} has no parent in the trace$")
        data = result["entrypoints"]["billing-agent"]
        self.assertEqual((data["traces_analyzed"], data["traces_skipped"]), (2, 3))
        self.assertEqual(data["skip_reasons"], sorted(reasons))

    def test_rejects_non_list_and_tolerates_junk_entries(self):
        with self.assertRaises(EvaluationError):
            llm10.normalize_xray_traces({"Traces": []})
        result = llm10.normalize_xray_traces(["junk", {"Id": "1-x"}, {"Id": "1-y", "Segments": [{"Id": "s", "Document": 5}]}])
        self.assertEqual([s["reason"] for s in result["skipped_traces"]], [
            "trace is not an object", "trace has no Id or Segments list", "segment s: Document is not a JSON object"])
        self.assertEqual(result["entrypoints"], {})


class Llm10PositiveTests(unittest.TestCase):
    """LLM10-01: identical repeated tool calls and a long agent run are flagged with exact evidence."""

    def test_two_findings_with_exact_evidence(self):
        payload, result = run("positive.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:support-agent"])
        self.assertEqual(result["measurements"], [])
        findings = by_identity(result)
        self.assertEqual(set(findings), {"identical-tool-calls", "iteration-budget"})
        data = payload["sources"][0]["data"]
        ids = [t["Id"] for t in traces("positive.traces.json")]

        identical = findings["identical-tool-calls"]
        self.assertEqual(identical["confidence"], "high")
        self.assertEqual(identical["summary"], (
            "2 of 3 analyzed traces of support-agent call the same tool with identical arguments more than 3 "
            f"times in one agent run; worst: lookup_order called 6 times with arguments hash "
            f"{data['max_identical_tool_calls']['arguments_hash']} by support_agent in trace {ids[0]} "
            "(0.72s of tool time)"))
        self.assertEqual([e["field"] for e in identical["evidence"]], ["traces_analyzed", "max_identical_tool_calls"])
        self.assertEqual(identical["evidence"][0]["value"], 3)
        self.assertEqual(identical["evidence"][1]["value"]["calls"], 6)
        self.assertEqual(identical["evidence"][1]["value"]["tool"], "lookup_order")

        iteration = findings["iteration-budget"]
        self.assertEqual(iteration["confidence"], "low")
        self.assertEqual(iteration["summary"], (
            "1 of 3 analyzed traces of support-agent run more than 10 model turns in one agent run; worst: 12 "
            f"model calls and 11 tool calls by support_agent in trace {ids[2]} (11.03s)"))
        self.assertEqual(iteration["evidence"][1], {
            "source_id": "xray:support-agent", "kind": "telemetry", "locator": "aws-xray:BatchGetTraces/support-agent",
            "field": "max_llm_iterations",
            "value": {"trace_id": ids[2], "agent": "support_agent", "llm_calls": 12, "tool_calls": 11,
                      "duration_seconds": 11.03},
        })
        self.assertTrue(all(f["references"] and f["recommendation"] for f in result["findings"]))


class Llm10NegativeTests(unittest.TestCase):
    """LLM10-02: the same tool with different arguments and runs within budget are clean."""

    def test_varied_arguments_are_clean(self):
        _, result = run("negative.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:research-agent"])
        self.assertEqual(result["findings"], [])
        data = entry("research-agent", "negative.traces.json")
        self.assertTrue(all(t["tool_calls"] == 8 for t in data["traces"]))
        self.assertEqual(data["max_identical_tool_calls"]["calls"], 2)
        self.assertEqual(data["max_llm_iterations"]["llm_calls"], 9)

    def test_negative_next_to_positive_only_flags_the_positive_entrypoint(self):
        _, result = run("positive.traces.json", "negative.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"entrypoint:support-agent"})


class Llm10ExceptionTests(unittest.TestCase):
    """LLM10-03: pagination, retries, layered instrumentation and separate agent runs are not flagged."""

    def setUp(self):
        self.data = entry("ops-agent", "exceptions.traces.json")
        self.by_trace = {t["trace_id"]: t for t in self.data["traces"]}
        self.pages, self.retries, self.orchestrator = (self.by_trace[t["Id"]] for t in traces("exceptions.traces.json"))

    def test_no_findings_and_exceptions_are_explained(self):
        _, result = run("exceptions.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        limitations = result["coverage"]["limitations"]
        self.assertIn("entrypoint:ops-agent: 6 failed or throttled calls excluded as retries", limitations)
        self.assertIn("entrypoint:ops-agent: 1 pagination series (same tool, changing cursor) not counted as identical calls",
                      limitations)

    def test_pagination_with_changing_cursors_is_not_identical(self):
        self.assertEqual(self.pages["paginated_tools"],
                         [{"agent": "ops_agent", "tool": "list_tickets", "calls": 6, "distinct_cursors": 6}])
        self.assertEqual(self.pages["max_identical_tool_calls"]["calls"], 1)

    def test_failed_calls_are_retries_and_layers_count_once(self):
        # 12 chat spans (3 throttled), each wrapping a Bedrock Runtime subsegment with the same gen_ai attributes;
        # search_kb called 5 times with identical arguments, 3 of them throttled.
        self.assertEqual(self.retries["llm_calls"], 9)
        self.assertEqual(self.retries["failed_calls"], 6)
        self.assertEqual(self.retries["max_identical_tool_calls"]["tool"], "search_kb")
        self.assertEqual(self.retries["max_identical_tool_calls"]["calls"], 2)

    def test_sub_agent_runs_are_counted_separately(self):
        self.assertEqual(self.orchestrator["agent_runs"], 3)
        self.assertEqual(self.orchestrator["max_agent_depth"], 2)
        self.assertEqual(self.orchestrator["llm_calls"], 13)  # above 10 for the trace, 6 per run
        self.assertEqual(self.orchestrator["max_run_iterations"]["llm_calls"], 6)
        self.assertEqual(self.orchestrator["max_identical_tool_calls"]["calls"], 3)  # 6 across the two runs

    def test_retries_still_count_when_they_succeed(self):
        # If the throttled flags are removed, the same trace has 5 identical calls and is flagged.
        raw = copy.deepcopy(traces("exceptions.traces.json"))
        for segment in raw[1]["Segments"]:
            segment["Document"] = segment["Document"].replace('"error":true,"throttle":true', '"error":false')
            segment["Document"] = segment["Document"].replace(',"error.type":"429"', "")
            segment["Document"] = segment["Document"].replace(',"error.type":"ThrottlingException"', "")
        data = llm10.normalize_xray_traces(raw)["entrypoints"]["ops-agent"]
        self.assertEqual(data["failed_calls"], 0)
        self.assertEqual(data["max_identical_tool_calls"]["calls"], 5)
        self.assertEqual(data["max_llm_iterations"]["llm_calls"], 12)


class Llm10IncompleteTests(unittest.TestCase):
    """LLM10-04/05: missing or malformed evidence is never reported clean."""

    def test_entrypoint_without_analyzable_traces_is_unavailable(self):
        _, result = run("missing.traces.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertIn(
            "entrypoint:notes-agent: 0 analyzable traces (with model turns or tool arguments) is below the required "
            "min_traces 3", result["coverage"]["limitations"])

    def test_missing_source_is_unavailable(self):
        _, result = run(sources=[], scope=["entrypoint:support-agent"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("entrypoint:support-agent: no telemetry source supplied; LLM-10 requires normalized X-Ray traces",
                      result["coverage"]["limitations"])

    def test_missing_or_invalid_settings_are_unavailable(self):
        for context, reason in (
            ({}, "missing required context settings: min_traces, max_llm_iterations, max_identical_tool_calls"),
            ({**CONTEXT, "min_traces": 0}, "context.min_traces must be a positive integer"),
            ({**CONTEXT, "max_llm_iterations": "10"}, "context.max_llm_iterations must be a positive integer"),
            ({**CONTEXT, "max_identical_tool_calls": True}, "context.max_identical_tool_calls must be a positive integer"),
            ({**CONTEXT, "max_identical_tool_calls": 2.5}, "context.max_identical_tool_calls must be a positive integer"),
        ):
            with self.subTest(context=context):
                _, result = run("positive.traces.json", context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["coverage"]["evaluated_scope"], [])
                self.assertEqual(result["coverage"]["limitations"],
                                 [f"Missing or invalid context settings: {reason}", LIMITATION])

    def test_skipped_traces_make_a_small_sample_insufficient(self):
        _, result = run("malformed.traces.json")
        self.assertEqual(result["status"], "unavailable")
        [reason] = [r for r in result["coverage"]["limitations"] if r.startswith("entrypoint:billing-agent")]
        self.assertIn("2 analyzable traces (with model turns or tool arguments) is below the required min_traces 3; "
                      "3 skipped: ", reason)
        self.assertIn("is in progress", reason)
        _, result = run("malformed.traces.json", context={**CONTEXT, "min_traces": 2})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("3 malformed or incomplete traces skipped" in r for r in result["coverage"]["limitations"]))

    def test_malformed_telemetry_next_to_valid_scope_is_partial(self):
        payload = make_input("positive.traces.json", "negative.traces.json")
        broken = payload["sources"][0]["data"]  # research-agent
        broken["traces"][0]["max_run_iterations"]["llm_calls"] = "nine"
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:support-agent"])
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"entrypoint:support-agent"})
        self.assertIn("entrypoint:research-agent: traces[0].max_run_iterations.llm_calls must be a nonnegative integer",
                      result["coverage"]["limitations"])

    def test_inconsistent_or_mismatched_data_is_omitted(self):
        cases = (
            (lambda d: d["max_identical_tool_calls"].update(calls=2), "max_identical_tool_calls does not match the largest per-trace value"),
            (lambda d: d.update(traces_analyzed=5), "traces_analyzed 5 does not match 3 trace summaries"),
            (lambda d: d.update(entrypoint="other"), "does not match entrypoint (expected 'entrypoint:other')"),
            (lambda d: d.pop("traces"), "missing fields: traces"),
            (lambda d: d.update(traces="x"), "traces must be a list of objects"),
        )
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                payload = make_input("positive.traces.json")
                mutate(payload["sources"][0]["data"])
                result = evaluate(payload)
                validate_pair(payload, result)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertTrue(any(reason in r for r in result["coverage"]["limitations"]), result["coverage"])

    def test_duplicate_sources_are_not_evaluated(self):
        payload = make_input("positive.traces.json")
        payload["sources"].append({**payload["sources"][0], "source_id": "xray:copy"})
        result = evaluate(payload)
        self.assertEqual(result["status"], "unavailable")


class Llm10BoundaryTests(unittest.TestCase):
    """LLM10-06: strict thresholds, the sample-size rule and stable fingerprints."""

    def test_thresholds_are_strict(self):
        ids = [t["Id"] for t in traces("boundary.traces.json")]
        context = {**CONTEXT, "min_traces": 1}
        # 3 identical calls and 10 model turns: clean
        _, result = run("boundary.traces.json", select=[0], context=context)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        # + 4 identical calls: only the identical-call rule
        _, result = run("boundary.traces.json", select=[0, 1], context=context)
        self.assertEqual(list(by_identity(result)), ["identical-tool-calls"])
        self.assertIn("1 of 2 analyzed traces", result["findings"][0]["summary"])
        self.assertIn("get_status called 4 times", result["findings"][0]["summary"])
        self.assertIn(ids[1], result["findings"][0]["summary"])
        # + 11 model turns: both rules
        _, result = run("boundary.traces.json", context=context)
        self.assertEqual(set(by_identity(result)), {"identical-tool-calls", "iteration-budget"})
        self.assertIn("worst: 11 model calls", by_identity(result)["iteration-budget"]["summary"])
        # raising the limits by one clears both
        _, result = run("boundary.traces.json",
                        context={**context, "max_identical_tool_calls": 4, "max_llm_iterations": 11})
        self.assertEqual(result["findings"], [])

    def test_min_traces_boundary(self):
        _, result = run("boundary.traces.json", context={**CONTEXT, "min_traces": 3})
        self.assertEqual(result["status"], "completed")
        _, result = run("boundary.traces.json", context={**CONTEXT, "min_traces": 4})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])

    def test_fingerprints_ignore_counts_and_trace_ids(self):
        context = {**CONTEXT, "min_traces": 1}
        _, small = run("boundary.traces.json", select=[1], context=context)
        _, full = run("boundary.traces.json", context=context)
        self.assertEqual(by_identity(small)["identical-tool-calls"]["fingerprint"],
                         by_identity(full)["identical-tool-calls"]["fingerprint"])
        self.assertNotEqual(by_identity(full)["identical-tool-calls"]["fingerprint"],
                            by_identity(full)["iteration-budget"]["fingerprint"])


class Llm10ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "entrypoint:support-agent", "identical-tool-calls")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_telemetry_evidence_is_rejected(self):
        payload, result = run("positive.traces.json")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][1]["value"] = {"calls": 99}
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.traces.json")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_and_normalization_are_deterministic(self):
        self.assertEqual(normalized("positive.traces.json"), normalized("positive.traces.json"))
        payload = make_input("positive.traces.json", "exceptions.traces.json")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "llm10-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.traces.json"))


class Llm10CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm10_result(self):
        input_path = FIXTURES / "llm10-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual({f["identity"] for f in result["findings"]}, {"identical-tool-calls", "iteration-budget"})


if __name__ == "__main__":
    unittest.main()
