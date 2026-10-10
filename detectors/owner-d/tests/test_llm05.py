"""Behavioral tests for the LLM-05 detector (issue #198): redundant chained calls, X-Ray trace analysis.

Fixtures under fixtures/llm05 and the traces built by `synthetic_trace` below are SYNTHETIC BatchGetTraces responses
shaped on the AWS X-Ray segment document spec, real Lambda traces and the OpenTelemetry GenAI attributes the ADOT
awsxrayexporter writes; they are not production telemetry.
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

from owner_d import cli, llm05, llm10
from owner_d.aws import registry
from owner_d.llm05 import CHECK_ID, DETECTOR_VERSION, LIMITATION, EvaluationError, evaluate, fingerprint

from shared.contracts.validation import ContractError, validate_pair
from shared.contracts.validation import fingerprint as shared_fingerprint

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm05"
REPOSITORY_ID = "github:AWS-env/example"
CONTEXT = {"min_traces": 3, "max_identical_consecutive_calls": 1, "min_chain_calls": 3, "min_repeat_share": 0.5}
MODEL = "us.anthropic.claude-3-5-haiku-20241022-v1:0"


def traces(name):
    return json.loads((FIXTURES / name).read_text())["Traces"]


def make_input(*names, raw=None, sources=None, scope=None, context=None):
    if sources is None:
        raw = [t for name in names for t in traces(name)] if raw is None else raw
        scope_from, sources = llm05.telemetry_sources(llm05.normalize_xray_traces(raw))
        scope = scope_from if scope is None else scope
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-llm05-001",
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


def entry(name, *fixtures, raw=None):
    raw = [t for f in fixtures for t in traces(f)] if raw is None else raw
    return llm05.normalize_xray_traces(raw)["entrypoints"][name]


def limitations(result):
    return "\n".join(result["coverage"]["limitations"])


# ---- SYNTHETIC trace builder for boundary cases -------------------------------------------------------------


def _message(text):
    return json.dumps([{"role": "user", "parts": [{"type": "text", "content": text}]}])


def synthetic_trace(n, requests, entrypoint="pipeline", agent="chain"):
    """One SYNTHETIC Lambda trace whose agent run sends one `chat` request per item of `requests` (a string is the
    user message; a dict is extra attributes merged into the span)."""
    trace_id = f"1-6ad1{n:04x}-{n:024x}"
    t0 = 1791700000.0 + n * 10
    calls = []
    for i, request in enumerate(requests):
        attrs = {"gen_ai.operation.name": "chat", "gen_ai.request.model": MODEL}
        if isinstance(request, str):
            attrs["gen_ai.input.messages"] = _message(request)
        else:
            attrs.update(request)
        calls.append({"id": f"{n:06x}{i:010x}", "name": f"chat {MODEL}", "start_time": t0 + 0.1 + i,
                      "end_time": t0 + 0.9 + i, "metadata": {"default": attrs}})
    end = t0 + len(requests) + 1
    run_span = {"id": f"a{n:015x}", "name": f"invoke_agent {agent}", "start_time": t0 + 0.05, "end_time": end - 0.05,
                "metadata": {"default": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent}},
                "subsegments": calls}
    function = {"id": f"f{n:015x}", "name": entrypoint, "start_time": t0, "end_time": end, "trace_id": trace_id,
                "origin": "AWS::Lambda::Function", "subsegments": [run_span]}
    return {"Id": trace_id, "Segments": [{"Id": function["id"], "Document": json.dumps(function)}]}


def boundary(requests_per_trace, context=None):
    raw = [synthetic_trace(i, requests) for i, requests in enumerate(requests_per_trace)]
    return run(raw=raw, context={**CONTEXT, **(context or {})})


def documents(trace):
    return [json.loads(s["Document"]) for s in trace["Segments"]]


def rewrite(trace, edit):
    """Copy of a fixture trace with `edit(node)` applied to every (sub)segment document node."""
    trace = copy.deepcopy(trace)
    for segment in trace["Segments"]:
        doc = json.loads(segment["Document"])
        stack = [doc]
        while stack:
            node = stack.pop()
            edit(node)
            stack.extend(node.get("subsegments", []))
        segment["Document"] = json.dumps(doc)
    return trace


class Llm05NormalizerTests(unittest.TestCase):
    """The normalizer builds on llm10.normalize_xray_traces and adds per-run request hashes."""

    def test_metadata_annotation_digest_and_independent_subsegment_styles(self):
        data = entry("research-agent", "positive.traces.json")
        ids = [t["Id"] for t in traces("positive.traces.json")]
        by_trace = {t["trace_id"]: t for t in data["traces"]}
        content, digest, independent = (by_trace[i] for i in ids)
        # metadata.default with a JSON string gen_ai.input.messages, under invoke_agent
        self.assertEqual(content["max_consecutive_identical"]["calls"], 3)
        self.assertEqual(content["max_consecutive_identical"]["input_basis"], "content")
        self.assertEqual(content["max_consecutive_identical"]["agent"], "researcher")
        # annotations (gen_ai_input_messages_hash) and no agent span: the entrypoint run
        self.assertEqual(digest["max_consecutive_identical"]["calls"], 2)
        self.assertEqual(digest["max_consecutive_identical"]["input_basis"], "digest")
        self.assertEqual(digest["max_consecutive_identical"]["agent"], "(entrypoint)")
        # every span sent as its own `type: subsegment` document; A, B, A, B has no consecutive repeat
        self.assertIsNone(independent["max_consecutive_identical"])
        self.assertEqual((independent["model_calls"], independent["comparable_pairs"]), (4, 3))
        self.assertEqual(data["repeated_chains"], [
            {"trace_id": ids[0], "agent": "researcher", "model_calls": 4, "comparable_calls": 4, "repeated_calls": 2},
            {"trace_id": ids[1], "agent": "(entrypoint)", "model_calls": 3, "comparable_calls": 3, "repeated_calls": 1},
            {"trace_id": ids[2], "agent": "planner", "model_calls": 4, "comparable_calls": 4, "repeated_calls": 2},
        ])
        self.assertEqual(data["max_consecutive_identical"]["trace_id"], ids[0])

    def test_builds_on_llm10_counts_and_skips(self):
        raw = traces("malformed.traces.json")
        base, ours = llm10.normalize_xray_traces(raw), llm05.normalize_xray_traces(raw)
        self.assertEqual(ours["skipped_traces"], base["skipped_traces"])
        self.assertEqual(ours["traces_received"], base["traces_received"])
        self.assertEqual(ours["entrypoints"]["billing-agent"]["skip_reasons"],
                         base["entrypoints"]["billing-agent"]["skip_reasons"])
        self.assertEqual(llm05.telemetry_sources(ours)[0], ["entrypoint:billing-agent"])

    def test_string_structured_and_legacy_indexed_inputs(self):
        def node(attrs):
            return {"id": "a" * 16, "name": "chat m", "start_time": 1.0, "end_time": 1.1, "metadata": {"x": attrs}}

        messages = [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]
        base = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}
        as_string = llm05._request(llm05._attributes(node({**base, "gen_ai.input.messages": json.dumps(messages)})))
        as_object = llm05._request(llm05._attributes(node({**base, "gen_ai.input.messages": messages})))
        self.assertEqual(as_string, as_object)
        legacy = llm05._request(llm05._attributes(node({**base, "gen_ai.prompt.0.role": "user",
                                                         "gen_ai.prompt.0.content": "hi"})))
        self.assertEqual(legacy[1], "content")
        other_model = llm05._request(llm05._attributes(node({**base, "gen_ai.request.model": "n",
                                                              "gen_ai.input.messages": messages})))
        self.assertNotEqual(other_model[2], as_string[2])
        self.assertEqual(llm05._request(llm05._attributes(node(base))), ("m", None, None))
        self.assertEqual(llm05._request(llm05._attributes(node({"gen_ai.input.messages": messages}))),
                         (None, None, None))

    def test_no_prompt_content_leaves_the_normalizer(self):
        text = json.dumps(llm05.normalize_xray_traces(traces("positive.traces.json") + traces("exceptions.traces.json")))
        for fragment in ("Summarise the open incidents", "Classify incident", "search_incidents returned",
                         "9f2c1d0e7b6a5f43", "Propose a mitigation"):
            self.assertNotIn(fragment, text)

    def test_traces_without_inputs_are_counted_not_analyzed(self):
        result = llm05.normalize_xray_traces(traces("missing.traces.json"))
        self.assertEqual((result["traces_received"], result["traces_without_model_calls"]), (4, 1))
        data = result["entrypoints"]["notes-agent"]
        self.assertEqual((data["traces_with_model_calls"], data["traces_analyzed"], data["traces_without_inputs"]),
                         (3, 0, 3))
        self.assertEqual((data["model_calls_without_input"], data["uncompared_pairs"]), (9, 6))
        self.assertEqual(data["traces"], [])

    def test_rejects_non_list_and_tolerates_junk_entries(self):
        with self.assertRaises(EvaluationError):
            llm05.normalize_xray_traces({"Traces": []})
        result = llm05.normalize_xray_traces(["junk", {"Id": "1-x"}, {"Id": "1-y", "Segments": [{"Id": "s", "Document": 5}]}])
        self.assertEqual(len(result["skipped_traces"]), 3)
        self.assertEqual(result["entrypoints"], {})


class Llm05PositiveTests(unittest.TestCase):
    """LLM05-01: consecutive identical requests and a re-asking chain are flagged with exact evidence."""

    def test_two_findings_with_exact_evidence(self):
        payload, result = run("positive.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:research-agent"])
        self.assertEqual(result["measurements"], [])
        findings = by_identity(result)
        self.assertEqual(set(findings), {"consecutive-identical-calls", "repeated-chain-requests"})
        source = payload["sources"][0]
        data = source["data"]
        ids = [t["Id"] for t in traces("positive.traces.json")]
        worst = data["max_consecutive_identical"]
        self.assertEqual((worst["trace_id"], worst["calls"], worst["seconds"], worst["model"]), (ids[0], 3, 2.4, MODEL))

        streak = findings["consecutive-identical-calls"]
        self.assertEqual(streak["confidence"], "high")
        self.assertEqual(streak["summary"], (
            "2 of 3 analyzed traces of research-agent send the same model an unchanged request 2 or more times in a "
            "row in one agent run, so earlier outputs are discarded and recomputed; worst: 3 consecutive identical "
            f"{MODEL} calls (request hash {worst['input_hash']}, from content) by researcher in trace {ids[0]} "
            "(2.4s of model time)"))
        self.assertEqual(streak["evidence"], [
            {"source_id": "xray:research-agent", "kind": "telemetry",
             "locator": "aws-xray:BatchGetTraces/research-agent", "field": "traces_analyzed", "value": 3},
            {"source_id": "xray:research-agent", "kind": "telemetry",
             "locator": "aws-xray:BatchGetTraces/research-agent", "field": "max_consecutive_identical", "value": worst},
        ])
        self.assertEqual(streak["fingerprint"], shared_fingerprint(
            REPOSITORY_ID, CHECK_ID, "entrypoint:research-agent", "consecutive-identical-calls"))

        chain = findings["repeated-chain-requests"]
        self.assertEqual(chain["confidence"], "medium")
        self.assertEqual(chain["summary"], (
            "2 of 3 analyzed traces of research-agent run a chain of at least 3 model calls in one agent run where at "
            f"least 50% of the calls repeat an earlier request; worst: 2 of 4 calls repeated by researcher in trace "
            f"{ids[0]}"))
        self.assertEqual([e["field"] for e in chain["evidence"]], ["traces_analyzed", "repeated_chains"])
        self.assertEqual(chain["evidence"][1]["value"], data["repeated_chains"])
        for finding in result["findings"]:
            self.assertIn("https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02-bp03.html",
                          finding["references"])
            self.assertTrue(any("client-inference.md" in r for r in finding["references"]))
        self.assertIn(LIMITATION, result["coverage"]["limitations"])

    def test_digest_only_trace_is_enough_for_the_consecutive_rule(self):
        raw = [traces("positive.traces.json")[1]]
        _, result = run(raw=raw, context={**CONTEXT, "min_traces": 1})
        finding = by_identity(result)["consecutive-identical-calls"]
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("2 consecutive identical", finding["summary"])
        self.assertIn("from digest", finding["summary"])
        self.assertNotIn("repeated-chain-requests", by_identity(result))  # 1 of 3 repeated is below 50%


class Llm05NegativeTests(unittest.TestCase):
    """LLM05-02: growing inputs, a second model on the same question and single calls are clean."""

    def test_distinct_requests_are_clean(self):
        payload, result = run("negative.traces.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:research-agent"])
        self.assertEqual(result["findings"], [])
        data = payload["sources"][0]["data"]
        self.assertEqual(data["traces_analyzed"], 3)
        self.assertIsNone(data["max_consecutive_identical"])
        self.assertEqual(data["repeated_chains"], [])
        self.assertEqual(result["coverage"]["limitations"], [LIMITATION])


class Llm05ExceptionTests(unittest.TestCase):
    """LLM05-03: retries, sampling, sub-agents, wrapped client spans and changed parameters are not flagged,
    and the same traces flag once the exception is removed."""

    def setUp(self):
        self.raw = traces("exceptions.traces.json")

    def test_exceptions_are_clean_with_notes(self):
        payload, result = run(raw=self.raw)
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        data = payload["sources"][0]["data"]
        self.assertEqual((data["traces_analyzed"], data["failed_model_calls"], data["retry_repeats_excluded"],
                          data["sampling_repeats_excluded"]), (5, 2, 1, 2))
        text = limitations(result)
        self.assertIn("2 failed or throttled model calls excluded, and 1 identical repeat after a failure excluded "
                      "as retries", text)
        self.assertIn("2 identical consecutive calls with gen_ai.request.temperature > 0 treated as intentional "
                      "sampling", text)
        by_trace = {t["trace_id"]: t for t in data["traces"]}
        wrapped = by_trace[self.raw[3]["Id"]]
        self.assertEqual(wrapped["model_calls"], 2)  # framework span + client span counted once
        subagents = by_trace[self.raw[2]["Id"]]
        self.assertEqual((subagents["agent_runs"], subagents["model_calls"]), (3, 4))

    def single(self, trace):
        return run(raw=[trace], context={**CONTEXT, "min_traces": 1})[1]

    def test_without_failure_flags_the_retry_trace_flags(self):
        def clear(node):
            for flag in ("throttle", "error", "fault"):
                node.pop(flag, None)
            node.get("metadata", {}).get("default", {}).pop("error.type", None)

        self.assertEqual(self.single(self.raw[0])["findings"], [])
        finding = by_identity(self.single(rewrite(self.raw[0], clear)))["consecutive-identical-calls"]
        self.assertIn("worst: 3 consecutive identical", finding["summary"])

    def test_without_temperature_the_sampling_trace_flags(self):
        def unset(node):
            node.get("metadata", {}).get("default", {}).pop("gen_ai.request.temperature", None)

        self.assertEqual(self.single(self.raw[1])["findings"], [])
        flagged = by_identity(self.single(rewrite(self.raw[1], unset)))
        self.assertEqual(set(flagged), {"consecutive-identical-calls", "repeated-chain-requests"})  # 2 of 3 repeat
        self.assertIn("worst: 3 consecutive identical", flagged["consecutive-identical-calls"]["summary"])

    def test_temperature_zero_is_not_sampling(self):
        def zero(node):
            attrs = node.get("metadata", {}).get("default", {})
            if "gen_ai.request.temperature" in attrs:
                attrs["gen_ai.request.temperature"] = 0
        self.assertEqual(len(self.single(rewrite(self.raw[1], zero))["findings"]), 2)

    def test_same_request_in_one_run_instead_of_two_sub_agents_flags(self):
        def flatten(node):
            kept = []
            for child in node.get("subsegments", []):
                if child["name"].startswith("invoke_agent worker_"):
                    kept.extend(child["subsegments"])
                else:
                    kept.append(child)
            if "subsegments" in node:
                node["subsegments"] = kept

        self.assertEqual(self.single(self.raw[2])["findings"], [])
        finding = by_identity(self.single(rewrite(self.raw[2], flatten)))["consecutive-identical-calls"]
        self.assertIn("by orchestrator", finding["summary"])

    def test_same_max_tokens_flags(self):
        def same(node):
            attrs = node.get("metadata", {}).get("default", {})
            if "gen_ai.request.max_tokens" in attrs:
                attrs["gen_ai.request.max_tokens"] = 1024

        self.assertEqual(self.single(self.raw[4])["findings"], [])
        self.assertEqual(len(self.single(rewrite(self.raw[4], same))["findings"]), 1)


class Llm05IncompleteTests(unittest.TestCase):
    """LLM05-04 / LLM05-05: missing inputs, sources or settings and malformed data never give a clean claim."""

    def test_missing_inputs_are_a_limitation_not_clean(self):
        payload, result = run("missing.traces.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual((result["coverage"]["evaluated_scope"], result["findings"]), ([], []))
        text = limitations(result)
        self.assertIn("entrypoint:notes-agent: 0 analyzable traces is below the required min_traces 3", text)
        self.assertIn("entrypoint:notes-agent: 3 traces with chained model calls recorded no comparable input "
                      "(gen_ai.input.messages is Opt-In", text)

    def test_demo_like_llm10_traffic_without_inputs_is_unavailable(self):
        llm10_fixture = json.loads((FIXTURES.parent / "llm10" / "positive.traces.json").read_text())["Traces"]
        _, result = run(raw=llm10_fixture, context={**CONTEXT, "min_traces": 1})
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("recorded no comparable input", limitations(result))

    def test_missing_inputs_next_to_enough_analyzed_traces_is_noted(self):
        _, result = run("positive.traces.json", "missing.traces.json")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:research-agent"])
        self.assertEqual(len(result["findings"]), 2)
        raw = traces("positive.traces.json") + traces("missing.traces.json")
        raw = [rewrite(t, lambda n: n.__setitem__("name", "research-agent") if n.get("name") == "notes-agent" else None)
               for t in raw]
        _, result = run(raw=raw)
        self.assertEqual(result["status"], "completed")
        self.assertIn("entrypoint:research-agent: 3 traces with chained model calls recorded no comparable input",
                      limitations(result))
        self.assertIn("6 consecutive call pairs without a recorded model or input could not be compared",
                      limitations(result))

    def test_no_source_and_bad_settings_are_unavailable(self):
        payload = make_input(sources=[], scope=["entrypoint:research-agent"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no telemetry source supplied", limitations(result))
        for context, reason in (
            ({}, "missing required context settings: min_traces, max_identical_consecutive_calls, min_chain_calls, "
                 "min_repeat_share"),
            ({**CONTEXT, "min_traces": 0}, "context.min_traces must be a positive integer"),
            ({**CONTEXT, "max_identical_consecutive_calls": True}, "max_identical_consecutive_calls must be a positive"),
            ({**CONTEXT, "min_chain_calls": 1}, "context.min_chain_calls must be an integer of at least 2"),
            ({**CONTEXT, "min_repeat_share": 0}, "context.min_repeat_share must be a number in (0, 1]"),
            ({**CONTEXT, "min_repeat_share": 1.5}, "context.min_repeat_share must be a number in (0, 1]"),
        ):
            payload, result = run("positive.traces.json", context=context)
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
            self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_malformed_traces_are_skipped_with_reasons(self):
        payload, result = run("malformed.traces.json", context={**CONTEXT, "min_traces": 2})
        data = payload["sources"][0]["data"]
        self.assertEqual((data["traces_analyzed"], data["traces_skipped"]), (2, 3))
        reasons = data["skip_reasons"]
        self.assertTrue(any(r.endswith("Document is not a JSON object") for r in reasons))
        self.assertTrue(any(r.endswith("is in progress") for r in reasons))
        self.assertTrue(any(r.endswith("has no parent in the trace") for r in reasons))
        self.assertIn("3 malformed or incomplete traces skipped", limitations(result))
        _, result = run("malformed.traces.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("2 analyzable traces is below the required min_traces 3", limitations(result))

    def test_bad_telemetry_next_to_a_valid_scope_is_partial(self):
        good = make_input("positive.traces.json")["sources"][0]
        tampered = [
            ("traces_analyzed", 99, "traces_analyzed 99 does not match 3 trace summaries"),
            ("max_consecutive_identical", None, "max_consecutive_identical does not match the largest per-trace streak"),
            ("repeated_chains", [{"trace_id": "1-nope", "agent": "a", "model_calls": 4, "comparable_calls": 4,
                                  "repeated_calls": 2}], "refers to trace 1-nope, which is not analyzed"),
            ("traces", "nope", "traces and repeated_chains must be lists"),
            ("entrypoint", "other", "does not match entrypoint"),
        ]
        for field, value, reason in tampered:
            bad = copy.deepcopy(good)
            bad["source_id"], bad["scope_id"] = "xray:broken", "entrypoint:broken"
            bad["data"][field] = value
            if field != "entrypoint":
                bad["data"]["entrypoint"] = "broken"
            payload = make_input(sources=[good, bad], scope=["entrypoint:research-agent", "entrypoint:broken"])
            result = evaluate(payload)
            validate_pair(payload, result)
            self.assertEqual(result["status"], "partial", field)
            self.assertEqual(result["coverage"]["evaluated_scope"], ["entrypoint:research-agent"])
            self.assertEqual({f["scope_id"] for f in result["findings"]}, {"entrypoint:research-agent"})
            self.assertIn(reason, limitations(result))

    def test_invalid_payloads_raise(self):
        payload = make_input("positive.traces.json")
        for key, value in (("check_id", "LLM-10"), ("detector_version", "0.9"), ("kind", "result"), ("scope", [])):
            with self.assertRaises(EvaluationError):
                evaluate({**payload, key: value})


class Llm05BoundaryTests(unittest.TestCase):
    """LLM05-06: exact thresholds and stable identity."""

    def test_consecutive_threshold(self):
        _, result = boundary([["a", "b"], ["a", "b"], ["c", "d"]])
        self.assertEqual(result["findings"], [])
        _, result = boundary([["a", "a"], ["a", "b"], ["c", "d"]])
        self.assertEqual([f["identity"] for f in result["findings"]], ["consecutive-identical-calls"])
        _, result = boundary([["a", "a"], ["a", "b"], ["c", "d"]], {"max_identical_consecutive_calls": 2})
        self.assertEqual(result["findings"], [])
        _, result = boundary([["a", "a", "a"], ["a", "b"], ["c", "d"]], {"max_identical_consecutive_calls": 2})
        self.assertEqual(by_identity(result)["consecutive-identical-calls"]["summary"][:72],
                         "1 of 3 analyzed traces of pipeline send the same model an unchanged requ")
        self.assertIn("3 or more times in a row", result["findings"][0]["summary"])

    def test_repeat_share_and_chain_length(self):
        chain = {"max_identical_consecutive_calls": 5}
        _, result = boundary([["a", "b", "a", "b"], ["x"], ["y"]], chain)
        self.assertEqual([f["identity"] for f in result["findings"]], ["repeated-chain-requests"])  # 2/4 = 0.5
        _, result = boundary([["a", "b", "c", "a", "b"], ["x"], ["y"]], chain)
        self.assertEqual(result["findings"], [])  # 2/5 = 0.4
        _, result = boundary([["a", "b", "a"], ["x"], ["y"]], {**chain, "min_repeat_share": 0.33})
        self.assertEqual(len(result["findings"]), 1)  # 1/3 >= 0.33 with 3 comparable calls
        _, result = boundary([["a", "b", "a"], ["x"], ["y"]], {**chain, "min_repeat_share": 0.33, "min_chain_calls": 4})
        self.assertEqual(result["findings"], [])

    def test_min_traces(self):
        payload, result = boundary([["a", "a"], ["b"], ["c"]], {"min_traces": 3})
        self.assertEqual((result["status"], len(result["findings"])), ("completed", 1))
        payload, result = boundary([["a", "a"], ["b"], ["c"]], {"min_traces": 4})
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("3 analyzable traces is below the required min_traces 4", limitations(result))

    def test_single_call_runs_are_clean_and_noted(self):
        _, result = boundary([["a"], ["a"], ["a"]])
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("no agent run had two comparable model calls", limitations(result))

    def test_partially_recorded_chain_counts_only_comparable_pairs(self):
        no_input = {"gen_ai.request.temperature": 0}
        _, result = boundary([["a", "b", no_input], ["b"], ["c"]])
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertIn("1 consecutive call pair without a recorded model or input", limitations(result))
        # a, ?, a: neither pair is comparable, so the trace is not analyzable rather than clean
        for unrecorded in (["a", no_input, "a"], [no_input, no_input]):
            _, result = boundary([unrecorded, ["b"], ["c"]])
            self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
            self.assertIn("1 trace with chained model calls recorded no comparable input", limitations(result))

    def test_fingerprint_ignores_counts_and_trace_ids(self):
        _, small = boundary([["a", "a"], ["b"], ["c"]])
        raw = [synthetic_trace(100 + i, r) for i, r in enumerate([["a"] * 5, ["a", "a"], ["q", "q"], ["z"]])]
        _, large = run(raw=raw)
        self.assertEqual(small["findings"][0]["fingerprint"], large["findings"][0]["fingerprint"])
        self.assertEqual(small["findings"][0]["fingerprint"],
                         fingerprint(REPOSITORY_ID, CHECK_ID, "entrypoint:pipeline", "consecutive-identical-calls"))
        self.assertNotEqual(small["findings"][0]["summary"], large["findings"][0]["summary"])


class Llm05ContractTests(unittest.TestCase):
    def test_reference_settings_match_registry_defaults(self):
        check = next(c for c in registry.CHECKS if c.check_id == CHECK_ID)
        self.assertEqual(check.defaults, llm05.REFERENCE_SETTINGS)
        self.assertEqual((check.source, check.adapter), ("traces", "xray_traces"))
        self.assertEqual(set(llm05.SETTING_KEYS), set(llm05.REFERENCE_SETTINGS))
        self.assertFalse(hasattr(llm05, "DEFAULT_SETTINGS"))  # settings stay required in the contract context

    def test_registry_adapter_builds_llm05_sources(self):
        out = registry.normalize(llm05.normalize_xray_traces, {"Traces": traces("positive.traces.json")},
                                 llm05.REFERENCE_SETTINGS, "xray_traces")
        self.assertEqual(out["scope"], ["entrypoint:research-agent"])
        self.assertEqual(out["sources"][0]["locator"], "aws-xray:BatchGetTraces/research-agent")
        self.assertIn("only 3 traces were read but min_traces is 10", out["limitations"][0])

    def test_tampered_evidence_is_rejected_by_the_contract(self):
        payload, result = run("positive.traces.json")
        result["findings"][0]["evidence"][1]["value"] = {"calls": 99}
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_committed_cli_fixture_is_in_sync(self):
        committed = json.loads((FIXTURES / "llm05-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.traces.json"))

    def test_cli_writes_valid_llm05_result(self):
        input_path = FIXTURES / "llm05-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual({f["identity"] for f in result["findings"]},
                         {"consecutive-identical-calls", "repeated-chain-requests"})


if __name__ == "__main__":
    unittest.main()
