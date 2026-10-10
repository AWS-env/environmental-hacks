"""LLM-16 runtime confirmation from client-CI memray artifacts (issue #461)."""

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

from owner_d import cli, llm16
from owner_d.llm16 import ARTIFACT_LIMITATION, ARTIFACT_REFERENCE_SETTINGS, LIMITATION, evaluate, fingerprint
from shared.contracts.validation import ContractError, validate_pair
from tests.test_llm16 import CONTEXT, REPOSITORY_ID, make_input, static_source

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm16"
STATS = json.loads((FIXTURES / "memray-stats.json").read_text())
ARTIFACT_CONTEXT = {**CONTEXT, **ARTIFACT_REFERENCE_SETTINGS}
NAME = "llm-16.json"


def frame_sources(scope_id, frames=None):
    frames = llm16.memray_frames(STATS)[0] if frames is None else frames
    return [{"source_id": f"memray-{i}", "scope_id": scope_id, "kind": "artifact",
             "locator": f"{NAME}: {frame.get('location', i)}", "data": frame} for i, frame in enumerate(frames)]


def run(payload):
    result = evaluate(payload)
    validate_pair(payload, result)
    return result


def confirm_input(frames=None, context=None):
    sources = [static_source("positive.py")] + frame_sources("file:positive.py", frames)
    return make_input(sources=sources, scope=["file:positive.py"],
                      context=dict(ARTIFACT_CONTEXT) if context is None else context)


def artifact_input(frames=None, context=None):
    scope_id = f"artifact:{NAME}"
    return make_input(sources=frame_sources(scope_id, frames), scope=[scope_id],
                      context=dict(ARTIFACT_REFERENCE_SETTINGS) if context is None else context)


def static_findings():
    return run(make_input("positive.py"))["findings"]


class MemrayNormalizationTests(unittest.TestCase):
    def test_stats_export_becomes_frame_records(self):
        frames, notes = llm16.memray_frames(STATS)
        self.assertEqual(notes, [])
        self.assertEqual(len(frames), 7)
        self.assertEqual(frames[0], {
            "profiler": "memray", "location": "invoke:/home/runner/work/example/example/positive.py:51",
            "function": "invoke", "file": "/home/runner/work/example/example/positive.py", "line": 51,
            "allocated_bytes": 8388608, "peak_memory": 41943040})

    def test_malformed_exports_and_entries(self):
        for bad in ([], {"metadata": {}}, {"top_allocations_by_size": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                llm16.memray_frames(bad)
        frames, notes = llm16.memray_frames({"top_allocations_by_size": [
            {"location": "no-line", "size": 1}, {"location": "f:a.py:1", "size": -1}, "x",
            {"location": "f:a.py:2", "size": True}, {"location": "f:a.py:3", "size": 7}]})
        self.assertEqual([f["line"] for f in frames], [3])
        self.assertNotIn("peak_memory", frames[0])
        self.assertEqual(len(notes), 4)


class Llm16ConfirmationTests(unittest.TestCase):
    """LLM16-A01: a memray frame on a static finding's call line above the threshold confirms it."""

    def test_large_allocation_on_the_call_line_confirms_the_static_finding(self):
        result = run(confirm_input())
        self.assertEqual(result["status"], "completed")
        before = {f["identity"]: f for f in static_findings()}
        after = {f["identity"]: f for f in result["findings"]}
        self.assertEqual(set(after), set(before))
        confirmed = after["invoke:bedrock.invoke_model"]
        self.assertEqual(confirmed["fingerprint"], before["invoke:bedrock.invoke_model"]["fingerprint"])
        self.assertEqual(confirmed["confidence"], "high")
        self.assertEqual(confirmed["evidence"][0], before["invoke:bedrock.invoke_model"]["evidence"][0])
        self.assertEqual([(e["kind"], e["field"], e["value"]) for e in confirmed["evidence"][1:]], [
            ("artifact", "location", "invoke:/home/runner/work/example/example/positive.py:51"),
            ("artifact", "allocated_bytes", 8388608), ("artifact", "peak_memory", 41943040)])
        self.assertIn("memray confirms it at runtime", confirmed["summary"])
        self.assertIn("8.0 MiB, more than 1,048,576 bytes", confirmed["summary"])
        self.assertIn(llm16.MEMRAY_REFERENCE, confirmed["references"])
        for identity, finding in after.items():  # every other finding is exactly the static one
            if identity != "invoke:bedrock.invoke_model":
                self.assertEqual(finding, before[identity])
        limitations = result["coverage"]["limitations"]
        self.assertIn(LIMITATION, limitations)
        self.assertIn(ARTIFACT_LIMITATION, limitations)
        self.assertIn("file:positive.py: memray confirmed 1 of 7 static finding(s); unconfirmed findings stay as "
                      "static evidence", limitations)
        self.assertIn("file:positive.py: 4 memray frame(s) are not in positive.py; ignored", limitations)

    def test_helper_confirmation_raises_low_to_medium(self):
        frame = {"profiler": "memray", "location": "draft_reply:app/positive.py:55", "function": "draft_reply",
                 "file": "app/positive.py", "line": 55, "allocated_bytes": 2 << 20}
        result = run(confirm_input([frame]))
        finding = {f["identity"]: f for f in result["findings"]}["draft_reply:anthropic.messages.create"]
        self.assertEqual(finding["confidence"], "medium")
        self.assertNotIn("peak_memory", [e.get("field") for e in finding["evidence"]])

    def test_below_threshold_leaves_static_findings_unchanged(self):
        """LLM16-A02: equality and smaller allocations do not confirm."""
        result = run(confirm_input(context={**CONTEXT, "max_buffered_response_bytes": 8388608}))
        self.assertEqual(result["findings"], static_findings())
        self.assertIn("file:positive.py: memray confirmed 0 of 7 static finding(s); unconfirmed findings stay as "
                      "static evidence", result["coverage"]["limitations"])

    def test_no_matching_frame_leaves_static_findings_unchanged(self):
        """LLM16-A03: frames on other lines never confirm, and never clear a static finding."""
        frames = [f for f in llm16.memray_frames(STATS)[0] if f["line"] != 51]
        result = run(confirm_input(frames))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], static_findings())

    def test_malformed_artifact_never_changes_the_static_result(self):
        """LLM16-A04: an unusable frame is reported; the static findings stay exactly as they are."""
        bad = [{"profiler": "py-spy", "location": "x"}, {"profiler": "memray", "location": "invoke:positive.py:51",
                                                         "function": "invoke", "file": "positive.py", "line": 51,
                                                         "allocated_bytes": "8388608"}]
        result = run(confirm_input(bad))
        self.assertEqual((result["status"], result["findings"]), ("completed", static_findings()))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("'memray-0' is not a usable memray frame (profiler must be 'memray')", limitations)
        self.assertIn("'memray-1' is not a usable memray frame (allocated_bytes must be a nonnegative integer)",
                      limitations)

    def test_missing_artifact_settings_make_only_confirmation_unavailable(self):
        result = run(confirm_input(context=dict(CONTEXT)))
        self.assertEqual((result["status"], result["findings"]), ("completed", static_findings()))
        self.assertIn("file:positive.py: runtime confirmation unavailable: missing or invalid context settings for "
                      "artifact mode: missing required context settings: max_buffered_response_bytes",
                      result["coverage"]["limitations"])

    def test_unparseable_static_file_stays_omitted(self):
        sources = [static_source("broken.py")] + frame_sources("file:broken.py")
        result = run(make_input(sources=sources, scope=["file:broken.py"], context=dict(ARTIFACT_CONTEXT)))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))


class Llm16ArtifactOnlyTests(unittest.TestCase):
    """LLM16-A05: without source, LLM SDK / HTTP response read frames above the threshold are flagged."""

    def test_sdk_response_reads_are_flagged(self):
        result = run(artifact_input())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"artifact:{NAME}"])
        findings = {f["identity"]: f for f in result["findings"]}
        self.assertEqual(list(findings), ["response-read:anthropic/_response.py:_parse",
                                          "response-read:httpx/_models.py:read"])
        sdk, http = findings.values()
        self.assertEqual((sdk["confidence"], http["confidence"]), ("medium", "low"))
        self.assertEqual(sdk["fingerprint"], fingerprint(REPOSITORY_ID, "LLM-16", f"artifact:{NAME}",
                                                         "response-read:anthropic/_response.py:_parse"))
        self.assertIn("Anthropic SDK _parse()", sdk["summary"])
        self.assertIn("(run peak 40.0 MiB)", sdk["summary"])
        self.assertIn("not only an LLM reply", http["summary"])
        self.assertEqual([e["field"] for e in sdk["evidence"]], ["location", "allocated_bytes", "peak_memory"])
        limitations = result["coverage"]["limitations"]
        self.assertEqual(limitations[-1], ARTIFACT_LIMITATION)
        self.assertNotIn(LIMITATION, limitations)  # static mode did not run
        self.assertTrue(any("4 memray frame(s) outside LLM SDK / HTTP response read functions were not judged" in item
                            for item in limitations))

    def test_identity_ignores_line_and_install_location(self):
        frames = llm16.memray_frames({"top_allocations_by_size": [
            {"location": "Response._parse:C:\\venv\\Lib\\site-packages\\anthropic\\_response.py:301", "size": 9 << 20}]})[0]
        result = run(artifact_input(frames))
        self.assertEqual([f["identity"] for f in result["findings"]], ["response-read:anthropic/_response.py:_parse"])

    def test_repeated_reader_gets_a_suffix(self):
        frame = llm16.memray_frames(STATS)[0][1]
        result = run(artifact_input([frame, dict(frame, line=300, location=frame["location"][:-3] + "300")]))
        self.assertEqual([f["identity"] for f in result["findings"]], [
            "response-read:anthropic/_response.py:_parse", "response-read:anthropic/_response.py:_parse#2"])

    def test_threshold_is_strict(self):
        result = run(artifact_input(context={"max_buffered_response_bytes": 6291456}))
        self.assertEqual([f["identity"] for f in result["findings"]], [])
        result = run(artifact_input(context={"max_buffered_response_bytes": 6291455}))
        self.assertEqual([f["identity"] for f in result["findings"]], ["response-read:anthropic/_response.py:_parse"])

    def test_missing_settings_or_frames_are_unavailable(self):
        for payload, reason in (
            (artifact_input(context={}), "Missing or invalid context settings: missing required context settings"),
            (artifact_input(context={"max_buffered_response_bytes": 0}), "must be a positive integer"),
            (artifact_input([{"profiler": "memray"}]), "no usable memray allocation frames"),
        ):
            with self.subTest(reason=reason):
                result = run(payload)
                self.assertEqual((result["status"], result["findings"], result["coverage"]["evaluated_scope"]),
                                 ("unavailable", [], []))
                self.assertTrue(any(reason in item for item in result["coverage"]["limitations"]))

    def test_mixed_payload_is_partial_when_one_mode_is_unavailable(self):
        sources = [static_source("positive.py")] + frame_sources(f"artifact:{NAME}")
        payload = make_input(sources=sources, scope=["file:positive.py", f"artifact:{NAME}"], context=dict(CONTEXT))
        result = run(payload)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.py"])
        self.assertEqual(result["findings"], static_findings())


class Llm16ArtifactContractTests(unittest.TestCase):
    def test_without_artifacts_the_static_result_is_unchanged(self):
        payload = make_input("positive.py", "negative.py", "broken.py")
        result = run(payload)
        self.assertEqual(result, llm16._evaluate_static(copy.deepcopy(payload)))
        self.assertNotIn(ARTIFACT_LIMITATION, result["coverage"]["limitations"])

    def test_invented_artifact_value_is_rejected(self):
        payload = confirm_input()
        tampered = copy.deepcopy(run(payload))
        finding = next(f for f in tampered["findings"] if len(f["evidence"]) > 1)
        finding["evidence"][2]["value"] = 1
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_header_is_checked_in_artifact_mode(self):
        payload = artifact_input()
        payload["detector_version"] = "1.0.0"
        with self.assertRaises(llm16.EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = confirm_input()
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_artifact_reference_settings_are_documented(self):
        readme = (DETECTOR_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("| `max_buffered_response_bytes` |", readme)
        self.assertIn(f"| `{ARTIFACT_REFERENCE_SETTINGS['max_buffered_response_bytes']}` |", readme)
        self.assertNotIn("max_buffered_response_bytes", llm16.REFERENCE_SETTINGS)  # repository scans stay static

    def test_committed_artifact_cli_fixture_matches_the_parser_output(self):
        committed = json.loads((FIXTURES / "llm16-a01-artifact-input.json").read_text())
        context, scope, sources, _ = llm16.memray_inputs(STATS, NAME, "4242-1")
        self.assertEqual(committed, make_input(sources=sources, scope=scope, context=context))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(FIXTURES / "llm16-a01-artifact-input.json"), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(committed, result)
        self.assertEqual(len(result["findings"]), 2)


if __name__ == "__main__":
    unittest.main()
