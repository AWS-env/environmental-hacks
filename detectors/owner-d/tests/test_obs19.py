"""Behavioral tests for the OBS-19 continuous-profiling detector (issue #243).

OBS-19 is artifact-only: a client continuous-profiler export (`obs-19.json`) is normalized by
`obs19.artifact_inputs` and evaluated per `cpu-profile:`/`memory-profile:` scope item. Every result is
checked with `validate_pair`. Profiles here are synthetic.
"""

import contextlib
import copy
import io
import json
import sys
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli, obs19  # noqa: E402
from owner_d.aws import artifact_handler, common  # noqa: E402
from shared.contracts.validation import fingerprint as shared_fingerprint, validate_pair  # noqa: E402
from tests.aws_fakes import NOW, AwsTestCase, FakeTable, mock_env  # noqa: E402
from tests.test_aws_artifacts import BUCKET, PREFIX, FakeS3, s3_event  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "obs19"
REPO = "github:AWS-env/example"
SHA = "0123456789abcdef0123456789abcdef01234567"

# 3000 of 10000 samples: render's self share 0.3 > 0.1; everything else is at most 0.09.
HOT = "\n".join([
    "<module> (app.py:10);serve (app/server.py:88);handle (app/server.py:120);render (app/views.py:42) 2600",
    "<module> (app.py:10);serve (app/server.py:88);handle (app/server.py:120);render (app/views.py:47) 400",
    *[f"<module> (app.py:10);serve (app/server.py:88);f{i} (app/work.py:{i + 1}) 875" for i in range(8)],
])
# 20 functions with 5% each under one dispatcher: no hot spot, and the dispatcher is an entry frame.
SPREAD = "\n".join(f"main (app.py:1);dispatch (app.py:5);job{i} (app/jobs.py:{i + 1}) 100" for i in range(20))
# parse has 60% of samples through six callees with 10% each: an inclusive-only hot spot.
INCLUSIVE = "\n".join([
    *[f"main;worker;parse;tok_{c} 200" for c in "abcdef"],
    *[f"main;other;o{i} 100" for i in range(8)],
])


def stack_objects(text):
    """The same profile in the JSON stack-object form."""
    rows = []
    for line in text.splitlines():
        stack, count = line.rsplit(" ", 1)
        rows.append({"stack": stack.split(";"), "count": int(count)})
    return rows


def speedscope(text, *, rate=100, threads=1, pseudo_root=None):
    """The same profile as py-spy 0.4 `--format speedscope` writes it: shared frames, one sampled profile per
    thread listing every sample root-first with weight 1/rate seconds."""
    frames, index, samples = [], {}, []
    for line in text.splitlines():
        stack, count = line.rsplit(" ", 1)
        indices = []
        for part in ([pseudo_root] if pseudo_root else []) + stack.split(";"):
            if part not in index:
                index[part] = len(frames)
                name, _, location = part.partition(" (")
                file, _, lineno = location.rstrip(")").rpartition(":")
                if part == pseudo_root:  # py-spy writes pseudo frames with an empty file and line 0
                    frames.append({"name": part, "file": "", "line": 0})
                elif location:
                    frames.append({"name": name, "file": file, "line": int(lineno)})
                else:
                    frames.append({"name": part})
            indices.append(index[part])
        samples += [indices] * int(count)
    chunks = [samples[t::threads] for t in range(threads)]
    return {"$schema": "https://www.speedscope.app/file-format-schema.json", "activeProfileIndex": None,
            "name": "py-spy profile", "exporter": "py-spy@0.4.2", "shared": {"frames": frames},
            "profiles": [{"type": "sampled", "name": f'Thread 0x7F3A{t} "MainThread"', "unit": "seconds",
                          "startValue": 0.0, "endValue": len(chunk) / rate, "samples": chunk,
                          "weights": [1 / rate] * len(chunk)} for t, chunk in enumerate(chunks)]}


def run(artifact, *, context=None, keep=None):
    """artifact -> (input payload, validated result)."""
    _, reference, scope, sources, _ = obs19.artifact_inputs(artifact)
    payload = {"schema_version": "1.0", "kind": "input", "repository_id": REPO, "scan_id": "scan-1",
               "commit_sha": SHA, "check_id": obs19.CHECK_ID, "detector_version": obs19.DETECTOR_VERSION,
               "context": reference if context is None else context, "scope": scope, "sources": sources}
    if keep:
        keep(payload)
    result = obs19.evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


class HotSpotTests(unittest.TestCase):
    def test_obs19_01_self_hot_spot_cites_function_share_samples_and_location(self):
        _, result = run({"profiler": "py-spy", "cpu": HOT})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["cpu-profile:default"])
        finding = by_identity(result)["hot-spot:render (app/views.py)"]
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(finding["confidence"], "high")
        self.assertIn("render at app/views.py:42", finding["summary"])
        self.assertIn("30.0% of CPU samples in its own code (3000 of 10000", finding["summary"])
        total, record = finding["evidence"]
        self.assertEqual((total["field"], total["value"]), ("total_samples", 10000))
        self.assertEqual(record["field"], "cpu:render (app/views.py)")
        self.assertEqual((record["value"]["self_samples"], record["value"]["self_share"],
                          record["value"]["location"]), (3000, 0.3, "app/views.py:42"))
        self.assertEqual(finding["fingerprint"], shared_fingerprint(REPO, "OBS-19", "cpu-profile:default",
                                                                    "hot-spot:render (app/views.py)"))

    def test_obs19_02_spread_profile_is_clean_and_entry_frames_are_not_hot_spots(self):
        payload, result = run({"cpu": SPREAD})
        self.assertEqual(payload["sources"][0]["data"]["cpu:dispatch (app.py)"]["inclusive_share"], 1.0)
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        self.assertEqual(result["coverage"]["evaluated_scope"], ["cpu-profile:default"])

    def test_obs19_03_inclusive_hot_spot_is_the_most_specific_function(self):
        _, result = run({"cpu": INCLUSIVE}, context=obs19.REFERENCE_SETTINGS | {"min_total_samples": 1000})
        self.assertEqual(list(by_identity(result)), ["hot-spot:parse"])
        finding = result["findings"][0]
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("60.0% including its callees (1200 of 2000", finding["summary"])
        self.assertIn("parse at an unknown location", finding["summary"])  # bare frames carry no file:line

    def test_obs19_04_below_minimum_samples_is_not_evaluated(self):
        _, result = run({"cpu": "main;hot 600\nmain;cold 399"})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual((result["coverage"]["evaluated_scope"], result["findings"]), ([], []))
        self.assertIn("999 CPU samples is below min_total_samples 1000", result["coverage"]["limitations"][0])

    def test_obs19_06_threshold_boundary_is_strict(self):
        at = "\n".join(["main;hot 100", *[f"main;f{i} 100" for i in range(9)]])  # hot = exactly 0.1
        _, result = run({"cpu": at}, context=obs19.REFERENCE_SETTINGS | {"min_total_samples": 1})
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        _, result = run({"cpu": at.replace("main;hot 100", "main;hot 101")},
                        context=obs19.REFERENCE_SETTINGS | {"min_total_samples": 1})
        self.assertEqual(list(by_identity(result)), ["hot-spot:hot"])

    def test_identity_ignores_line_numbers(self):
        _, moved = run({"cpu": HOT.replace("views.py:42", "views.py:90")})
        _, original = run({"cpu": HOT})
        self.assertEqual([f["fingerprint"] for f in moved["findings"]],
                         [f["fingerprint"] for f in original["findings"]])
        self.assertIn("app/views.py:90", moved["findings"][0]["summary"])


class InputFormatTests(unittest.TestCase):
    def test_collapsed_text_and_stack_objects_give_the_same_result(self):
        _, text = run({"cpu": HOT})
        objects = stack_objects(HOT)
        objects[0]["stack"] = ";".join(objects[0]["stack"])  # a stack may also be one 'a;b;c' string
        payload, structured = run({"cpu": objects})
        self.assertEqual(payload["sources"][0]["data"]["input_format"], "stacks")
        def strip(result):
            return [(f["identity"], f["summary"], f["evidence"][1]["value"]) for f in result["findings"]]

        self.assertEqual(strip(structured), strip(text))

    def test_raw_collapsed_text_is_detected_by_content(self):
        self.assertEqual(obs19.load_artifact(HOT.encode()), HOT)
        self.assertEqual(obs19.load_artifact(json.dumps({"cpu": HOT}).encode()), {"cpu": HOT})
        self.assertEqual(obs19.load_artifact(json.dumps(HOT)), HOT)  # a JSON string holds collapsed text
        _, result = run(obs19.load_artifact(HOT.encode()))
        self.assertEqual(list(by_identity(result)), ["hot-spot:render (app/views.py)"])
        for body, reason in [(b"\xff\xfe", "UTF-8"), (b'{"cpu": ', "does not parse")]:
            with self.subTest(reason=reason), self.assertRaisesRegex(obs19.ArtifactError, reason):
                obs19.load_artifact(body)

    def test_frames_map_to_function_and_file_line(self):
        cases = {
            "render (app/views.py:42)": ("render (app/views.py)", "render", "app/views.py", 42),
            "render (app/views.py)": ("render (app/views.py)", "render", "app/views.py", None),
            "app/views.py:42 - render": ("render (app/views.py)", "render", "app/views.py", 42),
            "<lambda> (<frozen runpy>:88)": ("<lambda> (<frozen runpy>)", "<lambda>", "<frozen runpy>", 88),
            "main.handler": ("main.handler", "main.handler", None, None),
            "com/acme/Cart.total_[j]": ("com/acme/Cart.total", "com/acme/Cart.total", None, None),
            "do_syscall_64+0x5b": ("do_syscall_64", "do_syscall_64", None, None),
        }
        for frame, expected in cases.items():
            with self.subTest(frame=frame):
                self.assertEqual(obs19.parse_frame(frame), expected)
        long_key = obs19.parse_frame("x" * 500)[0]
        self.assertEqual(len(long_key), obs19.MAX_KEY_CHARS)
        self.assertNotEqual(long_key, obs19.parse_frame("x" * 499 + "y")[0])

    def test_pyspy_process_and_thread_pseudo_frames_are_not_entry_points(self):
        # py-spy 0.4 --threads: `thread (<0xID>)` on macOS or `thread (<tid>)`, plus `: <name>` when known.
        for thread in ("thread (0x7F3A)", "thread (0x7F3A1C2B3740): MainThread", "thread (12345): worker-1"):
            with self.subTest(thread=thread):
                text = "\n".join(f'process 7:"python app.py";{thread};{line}' for line in HOT.splitlines())
                payload, result = run({"cpu": text})
                self.assertEqual(list(by_identity(result)), ["hot-spot:render (app/views.py)"])
                self.assertEqual(payload["sources"][0]["data"]["cpu:<module> (app.py)"]["root_samples"], 10000)
                self.assertFalse(any("thread" in field for field in payload["sources"][0]["data"]))


class SpeedscopeTests(unittest.TestCase):
    """py-spy `--format speedscope` (speedscope.json, as owner C's CI uploads it) is accepted as CPU input."""

    @staticmethod
    def strip(result):
        return [(f["identity"], f["summary"], f["evidence"][1]["value"]) for f in result["findings"]]

    def test_speedscope_document_matches_the_collapsed_profile(self):
        _, collapsed = run({"profiler": "py-spy@0.4.2", "cpu": HOT})
        for threads in (1, 3):
            with self.subTest(threads=threads):
                payload, result = run(speedscope(HOT, threads=threads))  # the whole file is the document
                data = payload["sources"][0]["data"]
                self.assertEqual((data["input_format"], data["profiler"], data["total_samples"]),
                                 ("speedscope", "py-spy@0.4.2", 10000))
                self.assertEqual(payload["scope"], ["cpu-profile:default"])
                self.assertEqual(self.strip(result), self.strip(collapsed))

    def test_speedscope_thread_pseudo_frames_are_dropped(self):
        payload, result = run(speedscope(HOT, pseudo_root="thread (0x7F3A1C2B3740): MainThread"))
        self.assertEqual(list(by_identity(result)), ["hot-spot:render (app/views.py)"])
        self.assertFalse(any("thread" in field for field in payload["sources"][0]["data"]))

    def test_speedscope_inside_a_named_profile_with_memory(self):
        m = 1048576
        payload, result = run({"profiles": [{"name": "api", "cpu": speedscope(HOT),
                                             "memory": {"series": {"put (a.py:1)": [k * m for k in range(1, 7)]}}}]})
        self.assertEqual(payload["scope"], ["cpu-profile:api", "memory-profile:api"])
        self.assertEqual(sorted(by_identity(result)), ["hot-spot:render (app/views.py)", "leak-candidate:put (a.py)"])

    def test_integer_weights_count_as_samples_and_evented_profiles_are_skipped(self):
        document = speedscope(HOT)
        profile = document["profiles"][0]
        profile.update(unit="none", weights=[2] * len(profile["samples"]))
        document["profiles"].append({"type": "evented", "name": "x", "unit": "none", "events": []})
        _, _, _, sources, notes = obs19.artifact_inputs(document)
        self.assertEqual(sources[0]["data"]["total_samples"], 20000)
        self.assertIn("profiles[1] is not a sampled profile ('evented'); skipped", " ".join(notes))

    def test_unusable_speedscope_documents_are_not_evaluated(self):
        def edit(change):
            document = speedscope("main;work 1000")
            change(document)
            return document

        cases = [
            (edit(lambda d: d["profiles"][0].update(type="evented")), "has no sampled profiles"),
            (edit(lambda d: d["profiles"][0]["weights"].__setitem__(0, 0.015)), "not whole multiples of one sample"),
            (edit(lambda d: d["profiles"][0]["samples"].__setitem__(0, [9])), "not a list of shared.frames indices"),
            (edit(lambda d: d["profiles"][0]["weights"].pop()), "one nonnegative number per sample"),
            (edit(lambda d: d["shared"].__setitem__("frames", [])), "nonempty shared.frames"),
        ]
        for artifact, reason in cases:
            with self.subTest(reason=reason):
                payload, result = run(artifact)
                self.assertIn("normalization_error", payload["sources"][0]["data"])
                self.assertEqual(result["status"], "unavailable")
                self.assertIn(reason, " ".join(result["coverage"]["limitations"]))

    def test_memray_stats_are_refused_with_the_reason(self):
        stats = {"metadata": {}, "total_num_allocations": 10, "total_bytes_allocated": 100,
                 "top_allocations_by_size": [{"location": "f:a.py:1", "size": 100, "count": 1}]}
        with self.assertRaisesRegex(obs19.ArtifactError, "memray stats --json.*no time series"):
            obs19.artifact_inputs(stats)


class BoundedInputTests(unittest.TestCase):
    def test_function_count_is_capped(self):
        limit = obs19.MAX_FUNCTIONS
        payload, result = run({"cpu": "\n".join(f"main;f{i} 1" for i in range(limit - 1))},
                              context=obs19.REFERENCE_SETTINGS | {"min_total_samples": 1})
        self.assertEqual(payload["sources"][0]["data"]["function_count"], limit)  # main + limit - 1
        self.assertEqual(result["status"], "completed")
        payload, result = run({"cpu": "\n".join(f"main;f{i} 1" for i in range(limit))})
        self.assertIn("normalization_error", payload["sources"][0]["data"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn(f"more than {limit} distinct functions; not evaluated", result["coverage"]["limitations"][0])
        _, result = run({"memory": {"series": {f"f{i}": [1] * 5 for i in range(limit + 1)}}})
        self.assertIn(f"memory has more than {limit} distinct functions", result["coverage"]["limitations"][0])

    def test_line_numbers_do_not_count_as_functions(self):
        payload, _ = run({"cpu": "\n".join(f"main;work (a.py:{i}) 1" for i in range(2 * obs19.MAX_FUNCTIONS))})
        self.assertEqual(payload["sources"][0]["data"]["function_count"], 2)

    def test_counts_and_bytes_have_at_most_18_digits(self):
        top = 10 ** 18 - 1
        payload, _ = run({"memory": {"series": {"a": [top] * 5}}})
        self.assertEqual(payload["sources"][0]["data"]["memory:a"]["in_use_bytes"], [top] * 5)
        cases = [
            ({"memory": {"series": {"a": [10 ** 18] * 5}}}, "nonnegative integer bytes below 10^18"),
            ({"memory": {"series": {"a (x.py:1)": [top] * 5, "a (x.py:2)": [1] * 5}}}, "must stay below 10^18"),
            ({"memory": {"snapshots": [f"a {top}\nb;a 1"] * 5}}, "must stay below 10^18"),
            ({"memory": {"series": {"a": [1] * 5}, "timestamps": ["t" * 65] * 5}}, "at most 64 characters"),
            ({"cpu": [{"stack": ["a"], "count": 10 ** 18}]}, "below 10^18"),
            ({"cpu": f"a {top}\nb 1"}, "digits of total samples"),
            ({"cpu": "a 1" + "0" * 18}, "is not 'frame;frame;... count'"),
        ]
        for artifact, reason in cases:
            with self.subTest(reason=reason):
                payload, result = run(artifact)
                self.assertEqual(result["status"], "unavailable")
                self.assertIn(reason, result["coverage"]["limitations"][0])
        _, result = run({"cpu": HOT}, context=obs19.REFERENCE_SETTINGS | {"min_total_samples": 10 ** 4000})
        self.assertIn("min_total_samples must be a number below 10^18", result["coverage"]["limitations"][0])


class LeakTests(unittest.TestCase):
    MIB = 1048576

    def memory(self, **functions):
        return {"memory": {"series": {f"{name} (app/cache.py:{i + 1})": list(values)
                                      for i, (name, values) in enumerate(functions.items())}}}

    def test_monotonic_growth_is_a_leak_candidate(self):
        m = self.MIB
        _, result = run(self.memory(put=(1 * m, 2 * m, 3 * m, 4 * m, 5 * m, 6 * m),
                                    steady=(5 * m, 5 * m, 5 * m, 5 * m, 5 * m, 5 * m)))
        self.assertEqual(result["coverage"]["evaluated_scope"], ["memory-profile:default"])
        finding = by_identity(result)["leak-candidate:put (app/cache.py)"]
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(finding["confidence"], "medium")
        self.assertIn("grew from 1048576 to 6291456 bytes (+5242880) across 6 snapshots", finding["summary"])
        self.assertEqual([e["field"] for e in finding["evidence"]], ["memory:put (app/cache.py)", "snapshot_count"])
        self.assertEqual(finding["evidence"][0]["value"]["location"], "app/cache.py:1")

    def test_nearly_monotonic_growth_is_a_low_confidence_candidate(self):
        m = self.MIB
        _, result = run(self.memory(put=(1 * m, 2 * m, 3 * m, 2 * m, 4 * m, 5 * m)))  # 4 of 5 intervals grow
        self.assertEqual(result["findings"][0]["confidence"], "low")
        _, result = run(self.memory(put=(1 * m, 2 * m, 1 * m, 2 * m, 1 * m, 5 * m)))  # 3 of 5
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_one_step_then_flat_and_small_growth_are_not_leaks(self):
        m = self.MIB
        _, result = run(self.memory(warmup=(0, 0, 0, 0, 0, 50 * m), small=(1, 2, 3, 4, 5, 6)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_warm_up_then_plateau_is_not_a_leak(self):
        # Every interval grows, but after the first jump the growth is tens of KB: warm-up, not a leak.
        _, result = run(self.memory(warmup=(0, 40_000_000, 40_050_000, 40_060_000, 40_065_000)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        # A jump followed by a jittery plateau (4 of 5 intervals grow).
        _, result = run(self.memory(jitter=(0, 40_000_000, 40_100_000, 40_050_000, 40_200_000, 40_300_000)))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_steady_growth_after_warm_up_is_still_a_leak(self):
        m = self.MIB
        _, result = run(self.memory(steady=(10 * m, 12 * m, 14 * m, 16 * m, 18 * m, 20 * m),
                                    warm_then_leak=(0, 40 * m, 41 * m, 42 * m, 43 * m, 44 * m)))
        self.assertEqual({k: f["confidence"] for k, f in by_identity(result).items()},
                         {"leak-candidate:steady (app/cache.py)": "medium",
                          "leak-candidate:warm_then_leak (app/cache.py)": "medium"})
        self.assertIn("+3145728 over the last 3 intervals",
                      by_identity(result)["leak-candidate:warm_then_leak (app/cache.py)"]["summary"])

    def test_second_half_growth_threshold_is_pro_rated(self):
        m = self.MIB
        # 5 snapshots: the second half is the last 2 of 4 intervals and must grow by at least 2/4 MiB.
        _, result = run(self.memory(at=(0, 3 * m, 3 * m + 1, 3 * m + 2, 3 * m + 1 + m // 2)))
        self.assertEqual(list(by_identity(result)), ["leak-candidate:at (app/cache.py)"])
        _, result = run(self.memory(below=(0, 3 * m, 3 * m + 1, 3 * m + 2, 3 * m + m // 2)))
        self.assertEqual(result["findings"], [])

    def test_below_minimum_snapshots_is_not_evaluated(self):
        _, result = run(self.memory(put=(1, 2 * self.MIB, 3 * self.MIB, 4 * self.MIB)))
        self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
        self.assertIn("4 memory snapshots is below min_leak_snapshots 5", result["coverage"]["limitations"][0])

    def test_collapsed_snapshots_match_the_series_form(self):
        m = self.MIB
        snapshots = [f"main;handle;put (app/cache.py:1) {n * m}\nmain;other (app/x.py:2) 100" for n in range(1, 7)]
        payload, result = run({"memory": {"snapshots": snapshots}})
        self.assertEqual(payload["sources"][0]["data"]["memory:put (app/cache.py)"]["in_use_bytes"],
                         [n * m for n in range(1, 7)])
        self.assertEqual(list(by_identity(result)), ["leak-candidate:put (app/cache.py)"])

    def test_findings_per_scope_are_capped_with_a_limitation(self):
        m = self.MIB
        _, result = run({"memory": {"series": {f"f{i}": [k * m * (i + 1) for k in range(6)] for i in range(15)}}})
        self.assertEqual(len(result["findings"]), obs19.MAX_FINDINGS_PER_SCOPE)
        self.assertEqual(result["findings"][0]["identity"], "leak-candidate:f14")  # largest growth first
        self.assertIn("10 more memory findings not reported", " ".join(result["coverage"]["limitations"]))


class MalformedInputTests(unittest.TestCase):
    def assert_not_evaluated(self, result, reason):
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual((result["coverage"]["evaluated_scope"], result["findings"]), ([], []))
        self.assertIn(reason, " ".join(result["coverage"]["limitations"]))

    def test_obs19_05_malformed_profiles_are_limitations_never_clean(self):
        cases = [
            ({"cpu": "main;work notanumber"}, "cpu line 1 is not 'frame;frame;... count'"),
            ({"cpu": "main;;work 3"}, "cpu line 1 has an empty frame"),
            ({"cpu": "\n\n"}, "cpu has no stacks"),
            ({"cpu": [{"stack": ["main"], "count": True}]}, "count must be a nonnegative integer"),
            ({"cpu": [{"stack": [], "count": 1}]}, "stack must be a nonempty list"),
            ({"cpu": {"main": 1}}, "must be collapsed text or a nonempty list"),
            ({"cpu": HOT, "sample_type": "alloc_space"}, 'sample_type must be "cpu"'),
            ({"memory": {"series": {"a": [1, 2], "b": [1]}}}, "one value per snapshot"),
            ({"memory": {"series": {"a": [1, -2, 3, 4, 5]}}}, "nonnegative integer bytes"),
            ({"memory": {"series": {"a": [1] * 5}, "unit": "MiB"}}, 'memory.unit must be "bytes"'),
            ({"memory": {"series": {"a": [1] * 5}, "snapshots": []}}, 'exactly one of "series" or "snapshots"'),
            ({"memory": {"series": {"a": [1] * 61}}}, "1 to 60 snapshots"),
        ]
        for artifact, reason in cases:
            with self.subTest(reason=reason):
                payload, result = run(artifact)
                self.assertIn("normalization_error", payload["sources"][0]["data"])
                self.assert_not_evaluated(result, reason)

    def test_tampered_or_unnormalized_data_is_rejected(self):
        def tamper(edit):
            def keep(payload):
                edit(payload["sources"][0]["data"])
            return keep

        cases = [
            (lambda d: d["cpu:render (app/views.py)"].update(self_samples=2000, self_share=0.2),
             "self samples sum to 9000"),
            (lambda d: d["cpu:render (app/views.py)"].__setitem__("self_share", 0.9), "does not match its samples"),
            (lambda d: d["cpu:render (app/views.py)"].__setitem__("inclusive_samples", 1), "inconsistent"),
            (lambda d: d.__setitem__("function_count", 1), "function_count must equal"),
            (lambda d: d.__setitem__("extra", 1), "unknown fields: extra"),
            (lambda d: d.__setitem__("profile", "other"), "does not match the scope"),
            (lambda d: d.clear() or d.update(cpu=HOT), "missing fields"),
        ]
        for edit, reason in cases:
            with self.subTest(reason=reason):
                _, result = run({"cpu": HOT}, keep=tamper(edit))
                self.assert_not_evaluated(result, reason)

    def test_missing_artifact_settings_and_scope_are_unavailable(self):
        _, result = run({"cpu": HOT}, keep=lambda p: p.__setitem__("sources", []))
        self.assert_not_evaluated(result, "no continuous-profiler artifact supplied")
        _, result = run({"cpu": HOT}, context=obs19.REFERENCE_SETTINGS | {"max_self_cpu_share": 0})
        self.assert_not_evaluated(result, "max_self_cpu_share must be greater than 0")
        _, result = run({"cpu": HOT}, context={})
        self.assert_not_evaluated(result, "missing required context settings")
        _, result = run({"cpu": HOT}, keep=lambda p: p.update(scope=["file:app.py"], sources=[]))
        self.assert_not_evaluated(result, "unsupported scope")

    def test_one_bad_profile_makes_the_result_partial(self):
        _, result = run({"profiles": [{"name": "good", "cpu": HOT}, {"name": "bad", "cpu": "oops"}]})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["cpu-profile:good"])
        self.assertIn("cpu-profile:bad: profile could not be normalized", result["coverage"]["limitations"][0])

    def test_unusable_artifacts_raise(self):
        cases = [
            ([], "needs a JSON object"),
            ({"profiles": []}, "nonempty list"),
            ({"profiler": "py-spy"}, 'needs "profiles"'),
            ({"cpu": HOT, "settings": {"max_cpu": 1}}, "settings may only contain"),
            ({"cpu": HOT, "labels": {}}, "unknown top-level fields: labels"),
            ({"cpu": HOT, "profiler": ""}, '"profiler" must be'),
            ({"profiles": [{"name": "../x", "cpu": HOT}, {"cpu": HOT}, {"name": "a"}]}, "no usable profiles"),
        ]
        for artifact, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(obs19.ArtifactError, reason):
                obs19.artifact_inputs(artifact)

    def test_skipped_and_extra_profiles_are_noted(self):
        profiles = [{"name": "a", "cpu": HOT}, {"name": "a", "cpu": HOT}, {"name": "b", "cpu": HOT, "x": 1}]
        profiles += [{"name": f"p{i}", "cpu": HOT} for i in range(5)]
        _, _, scope, _, notes = obs19.artifact_inputs({"profiles": profiles})
        self.assertEqual(scope, ["cpu-profile:a", "cpu-profile:p0", "cpu-profile:p1"])
        text = " ".join(notes)
        for reason in ("only the first 5 of 8 profiles", "duplicate profile name 'a'", "unknown fields x"):
            self.assertIn(reason, text)

    def test_contract_errors_raise(self):
        payload, _ = run({"cpu": HOT})
        for field, value in [("check_id", "OBS-18"), ("detector_version", "0.9.0"), ("scope", [])]:
            with self.subTest(field=field), self.assertRaises(obs19.EvaluationError):
                obs19.evaluate(copy.deepcopy(payload) | {field: value})


class IntegrationTests(unittest.TestCase):
    def test_registered_artifact_only_and_skipped_by_repository_scans(self):
        from scanner.adapters.owner_d import RUNTIME_KINDS, discover

        self.assertIs(cli.DETECTORS["OBS-19"], obs19)
        self.assertIs(discover()["OBS-19"], obs19)
        self.assertIn(obs19.SUPPORTED_KIND, RUNTIME_KINDS)
        self.assertEqual(set(obs19.REFERENCE_SETTINGS), set(obs19.SETTING_KEYS))

    def test_fixture_matches_the_upload_example_and_runs_through_the_cli(self):
        artifact = json.loads((FIXTURES / "obs-19.json").read_text())
        expected = json.loads((FIXTURES / "obs19-01-positive-input.json").read_text())
        _, context, scope, sources, _ = obs19.artifact_inputs(artifact, name="obs-19.json", run="4242-1")
        self.assertEqual((context, scope, sources), (expected["context"], expected["scope"], expected["sources"]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([str(FIXTURES / "obs19-01-positive-input.json")]), 0)
        result = json.loads(out.getvalue())
        validate_pair(expected, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(sorted(by_identity(result)), ["hot-spot:handle (app/server.py)",
                                                       "hot-spot:render (app/views.py)",
                                                       "leak-candidate:cache_put (app/cache.py)"])


class ArtifactRouteTests(AwsTestCase):
    """obs-19.json through the live artifact parser's route (owner_d/aws/artifact_handler.py)."""

    def setUp(self):
        super().setUp()
        self._artifact_env = mock_env({"ARTIFACT_BUCKET": BUCKET})
        self.fakes["s3"] = FakeS3()

    def tearDown(self):
        self._artifact_env()
        super().tearDown()

    def run_key(self, body, name="obs-19.json", **event):
        self.fakes["s3"].objects[PREFIX + name] = body
        return artifact_handler.lambda_handler(s3_event(PREFIX + name) | event)

    def test_profile_artifact_is_evaluated_validated_and_published(self):
        from findings_hub import writer

        out = self.run_key((FIXTURES / "obs-19.json").read_bytes())
        self.assertEqual((out["outcome"], out["artifact"], out["published"], out["refused"], out["errors"]),
                         ("evaluated", "obs-19.json", 1, [], []))
        self.assertEqual(out["results"], [{"check_id": "OBS-19", "status": "completed", "scope": 2, "evaluated": 2,
                                           "findings": 3}])
        entry = self.fakes["events"].entries[0]
        result = json.loads(entry["Detail"])
        self.assertEqual((result["repository_id"], result["scan_id"]), ("github:AWS-env/example", "gha-4242-1"))
        self.assertEqual(result["scope"], ["cpu-profile:checkout-api", "memory-profile:checkout-api"])
        self.assertIn("obs-19.json from GitHub Actions run 4242-1: profile checkout-api (cpu)",
                      result["findings"][0]["evidence"][0]["locator"])
        self.assertNotIn("123456789012", entry["Detail"])
        stored = writer.ingest({"source": entry["Source"], "detail-type": entry["DetailType"], "detail": result,
                                "id": "evt-1"}, s3=None, table=FakeTable(), allowed_buckets=set(), now=NOW)
        self.assertEqual(stored["outcome"], "stored")

    def test_collapsed_text_in_json_and_settings_overrides(self):
        out = self.run_key(json.dumps(HOT).encode(), dry_run=True)
        self.assertEqual(out["results"][0]["findings"], 1)
        out = self.run_key(json.dumps({"cpu": HOT, "settings": {"max_self_cpu_share": 0.5}}).encode(), dry_run=True)
        self.assertEqual((out["results"][0]["findings"], out["published"]), (0, 0))

    def test_malformed_profile_is_published_as_unavailable_not_clean(self):
        out = self.run_key(json.dumps({"cpu": "not collapsed"}).encode(), dry_run=True)
        self.assertEqual(out["results"], [{"check_id": "OBS-19", "status": "unavailable", "scope": 1, "evaluated": 0,
                                           "findings": 0}])

    def test_unusable_artifacts_are_refused(self):
        for body, reason in [(HOT.encode(), "not UTF-8 JSON"), (b"[]", "needs a JSON object"),
                             (json.dumps({"profiles": [{"cpu": HOT}]}).encode(), "no usable profiles"),
                             (json.dumps({"cpu": HOT, "settings": {"x": 1}}).encode(), "settings may only contain")]:
            with self.subTest(reason=reason):
                out = self.run_key(body)
                self.assertEqual(out["outcome"], "refused")
                self.assertIn(reason, out["reason"])
        self.assertEqual(self.fakes["events"].entries, [])

    def test_oversized_numbers_are_bounded_and_fit_one_event(self):
        # ~1.4 MB of 4000-digit integers used to produce a 1.27 MB result, which made common.event_entries raise
        # (over MAX_DETAIL_BYTES) and sent the event to the DLQ instead of reporting it.
        big = 10 ** 3999
        profiles = [{"name": f"p{p}", "memory": {"series": {f"f{i}": [big * (k + 1) + i for k in range(14)]
                                                             for i in range(5)}}} for p in range(obs19.MAX_PROFILES)]
        body = json.dumps({"profiles": profiles}).encode()
        self.assertGreater(len(body), 1_400_000)
        out = self.run_key(body, dry_run=True)
        self.assertEqual((out["outcome"], out["results"][0]["status"]), ("evaluated", "unavailable"))
        self.assertIn("below 10^18", " ".join(out["result_payloads"][0]["coverage"]["limitations"]))
        for entry in common.event_entries(out["result_payloads"], bus_name="findings-hub",
                                          source=artifact_handler.SOURCE):
            self.assertLess(len(entry["Detail"].encode()), common.MAX_DETAIL_BYTES)

    def test_too_many_functions_are_reported_not_evaluated(self):
        body = json.dumps({"cpu": "\n".join(f"main;handler_{i} (app/m{i}.py:{i}) 1" for i in range(20_000))})
        out = self.run_key(body.encode(), dry_run=True)
        self.assertEqual(out["results"][0]["status"], "unavailable")
        self.assertIn("distinct functions", " ".join(out["result_payloads"][0]["coverage"]["limitations"]))

    def test_pyspy_speedscope_upload_is_evaluated(self):
        out = self.run_key(json.dumps(speedscope(HOT, threads=2)).encode(), dry_run=True)
        self.assertEqual(out["results"], [{"check_id": "OBS-19", "status": "completed", "scope": 1, "evaluated": 1,
                                           "findings": 1}])
        self.assertIn("(py-spy@0.4.2)", out["result_payloads"][0]["findings"][0]["summary"])

    def test_largest_accepted_artifact_fits_one_event(self):
        m = 1048576
        name = "handler_" + "x" * 300
        cpu = "\n".join(f"main;{name}{i} ({'d/' * 60}f{i}.py:{i}) 1000" for i in range(9))
        memory = {"series": {f"{name}{i} ({'d/' * 60}f{i}.py:9)": [(k + 1) * 10 ** 9 + i * m for k in range(60)]
                             for i in range(15)}}
        profiles = [{"name": f"{'p' * 90}{n}", "cpu": cpu, "memory": memory} for n in range(obs19.MAX_PROFILES)]
        out = self.run_key(json.dumps({"profiler": "p" * 100, "profiles": profiles}).encode(), dry_run=True)
        self.assertEqual([r["findings"] for r in out["results"]], [50])
        common.event_entries(out["result_payloads"], bus_name="findings-hub", source=artifact_handler.SOURCE)


if __name__ == "__main__":
    unittest.main()
