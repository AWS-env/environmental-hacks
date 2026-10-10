"""Hostile or sloppy inputs must never hang the scan, crash it, or be reported as clean.

A workflow file or run history comes from the client's repository, so it is untrusted: parsing is bounded, a file
beyond the bounds is `unavailable` with a limitation, and every result still passes the shared contract validator.
"""
import json
import time
import unittest
from unittest import mock

from helpers_ci import run_ci_check
from owner_c.ci import yamlio
from owner_c.aws import common
from owner_c.ci.normalize import github_actions
from owner_c.ci.registry import CHECKS, kind
from shared.contracts.validation import validate

WF = ".github/workflows/ci.yml"
BASE = "name: CI\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm ci\n      - run: npm test\n"
STATIC = [k for k, m in CHECKS.items() if kind(m) == "static"]


def alias_bomb():
    names = "abcdefghi"
    rows = ["a: &a [x,x,x,x,x,x,x,x,x]"]
    for i in range(1, 9):
        rows.append(f"{names[i]}: &{names[i]} [{','.join('*' + names[i - 1] for _ in range(9))}]")
    return "name: bomb\non: push\nx-bomb:\n" + "".join("  " + r + "\n" for r in rows) + \
        "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n"


class HostileWorkflows(unittest.TestCase):
    def evaluate_all(self, text):
        started = time.time()
        results = [run_ci_check(key, {WF: text})[1] for key in STATIC]
        self.assertLess(time.time() - started, 15, "a hostile file must not stall the scan")
        return results

    def assert_not_evaluated(self, text, reason):
        for result in self.evaluate_all(text):
            self.assertEqual(result["status"], "unavailable", result["check_id"])
            self.assertEqual(result["coverage"]["evaluated_scope"], [])
            self.assertEqual(result["findings"], [])
            self.assertIn(reason, " ".join(result["coverage"]["limitations"]))

    def test_yaml_alias_bomb_is_rejected_not_expanded(self):
        self.assert_not_evaluated(alias_bomb(), "too many nodes")

    def test_a_very_long_line_is_not_evaluated(self):
        self.assert_not_evaluated(BASE + "      - run: " + "x" * (yamlio.MAX_LINE_CHARS + 1) + "\n", "longer than")

    def test_deeply_nested_flow_collections_are_not_evaluated(self):
        deep = BASE + "      - with: " + "[" * (yamlio.MAX_FLOW_DEPTH + 1) + "]" * (yamlio.MAX_FLOW_DEPTH + 1) + "\n"
        self.assert_not_evaluated(deep, "nested too deeply")

    def test_deep_indentation_and_many_lines_are_not_evaluated(self):
        self.assert_not_evaluated(BASE + " " * (yamlio.MAX_INDENT + 1) + "x: 1\n", "indentation")
        self.assert_not_evaluated(BASE + "# c\n" * (yamlio.MAX_LINES + 1), "lines")

    def test_crlf_and_bom_files_are_evaluated_and_valid(self):
        for text in (BASE.replace("\n", "\r\n"), "\ufeff" + BASE):
            results = {r["check_id"]: r for r in self.evaluate_all(text)}
            self.assertEqual(results["CI-11"]["status"], "completed")
            self.assertEqual(len(results["CI-11"]["findings"]), 1)

    def test_wrong_shapes_never_crash_and_never_claim_a_finding(self):
        for text in ("name: n\non: push\njobs:\n  t: [1, 2]\n",
                     "name: n\non: push\njobs:\n  t:\n    runs-on: x\n    steps: not-a-list\n",
                     "name: n\non: push\njobs:\n  t:\n    runs-on: x\n    strategy: {matrix: 5}\n    steps:\n      - run: 3\n      - uses: 7\n",
                     "on: [push]\njobs: {}\n", "", "- a\n- b\n"):
            for result in self.evaluate_all(text):
                self.assertEqual(result["findings"], [], text)

    def test_many_jobs_stay_fast(self):
        many = "name: m\non: push\njobs:\n" + "".join(
            f"  j{i}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm ci\n" for i in range(300))
        results = {r["check_id"]: r for r in self.evaluate_all(many)}
        self.assertTrue(all(r["status"] == "completed" for r in results.values()))
        if "CI-06" in results:  # present from the CI-06 PR onward
            self.assertEqual(len(results["CI-06"]["findings"]), 300)


class CommandAndPatternBounds(unittest.TestCase):
    def test_a_huge_continued_command_is_capped_and_stays_fast(self):
        """330 KB of `mvn clean \\` lines is one logical command; an unbounded quadratic pattern would run for minutes."""
        script = "".join("          mvn clean \\\n" for _ in range(25000))
        text = ("name: CI\non:\n  pull_request:\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n" + script + "          echo done\n")
        started = time.time()
        results = [run_ci_check(key, {WF: text})[1] for key in STATIC]
        self.assertLess(time.time() - started, 10)
        self.assertTrue(all(r["status"] == "completed" for r in results))

    def test_every_compiled_ci_regex_is_fast_on_capped_adversarial_input(self):
        import importlib
        import inspect
        import pkgutil
        import random
        import re

        import owner_c.ci as package
        from owner_c.ci.workflow import MAX_COMMAND_CHARS

        random.seed(7)
        seen, slow = set(), []
        for info in pkgutil.walk_packages(package.__path__, "owner_c.ci."):
            if info.name.endswith("__main__"):
                continue
            for _name, pattern in inspect.getmembers(importlib.import_module(info.name), lambda o: isinstance(o, re.Pattern)):
                if pattern.pattern in seen:
                    continue
                seen.add(pattern.pattern)
                words = re.findall(r"[A-Za-z_][A-Za-z_\-]{2,}", pattern.pattern)
                alphabet = sorted(set(re.sub(r"\\[a-zA-Z]|[\\^$.*+?()\[\]{}|]", "", pattern.pattern))) or ["a"]
                n = MAX_COMMAND_CHARS
                inputs = ["x" * n, " " * n + "x", (" ".join(words) + " ") * max(1, n // max(1, len(" ".join(words)) + 1)),
                          "".join(random.choice(alphabet) for _ in range(n)), "mvn clean " * (n // 10), "(" * (n // 2) + ")" * (n // 2)]
                for text in inputs:
                    started = time.perf_counter()
                    pattern.search(text)
                    if time.perf_counter() - started > 0.25:
                        slow.append(pattern.pattern[:60])
        self.assertGreater(len(seen), 5, "the probe must find the package's patterns (the first stacked state has about 9)")
        self.assertEqual(slow, [], "a CI regex is too slow on capped input")

    def test_a_job_name_of_a_million_spaces_does_not_stall_stage_matching(self):
        from owner_c.ci.normalize.github_actions import stage_name

        started = time.time()
        stage_name("a" + " " * 1_000_000)
        self.assertLess(time.time() - started, 1)


class ScopeAndDeclaredLimits(unittest.TestCase):
    def test_only_exact_workflow_paths_become_scope_items(self):
        from owner_c.ci.connector import build_inputs, select_workflows

        odd = [(".github/workflows/ci.yml", BASE), (".github/workflows/../x.yml", BASE), ("x/.github/workflows/a.yml", BASE),
               ("/.github/workflows/a.yml", BASE), (".github/workflows/a.yml.bak", BASE), (".github/workflows/sub/a.yml", BASE),
               ("github/workflows/a.yml", BASE), (".github/workflows/a.YML", BASE), (".github\\workflows\\w.yaml", BASE)]
        kept = [path for path, _ in select_workflows(odd)]
        self.assertEqual(sorted(kept), [".github/workflows/ci.yml", ".github/workflows/w.yaml"])
        payload = build_inputs(repository_id="github:o/r", commit_sha="a" * 40, scan_id="s", files=odd, checks=["CI-11"])[0]
        self.assertEqual(sorted(payload["scope"]), ["file:.github/workflows/ci.yml", "file:.github/workflows/w.yaml"])

    def test_every_payload_declares_what_is_not_scanned(self):
        from owner_c.ci.connector import build_inputs

        payload = build_inputs(repository_id="github:o/r", commit_sha="a" * 40, scan_id="s", files=[(WF, BASE)], checks=["CI-11"])[0]
        self.assertIn("composite actions (.github/actions/**/action.yml)", payload["context"]["not_scanned"])
        self.assertIn("CI providers other than GitHub Actions", payload["context"]["not_scanned"])

    def test_the_artifact_says_how_many_re_run_runs_had_no_attempt_detail(self):
        run = {"id": 1, "run_attempt": 3, "event": "pull_request", "head_sha": "a" * 40, "attempts": [], "jobs_unavailable": 502}
        plain = {"id": 2, "run_attempt": 1, "event": "pull_request", "head_sha": "b" * 40, "attempts": []}
        data = github_actions.normalize({"schema": github_actions.SCHEMA, "workflow_path": WF, "runs": [run, plain]})
        self.assertEqual(data["runs_without_detail"], 1)


class EvidenceSize(unittest.TestCase):
    def test_evidence_is_capped_by_characters_and_still_valid(self):
        long_entries = "".join(f"      - 'dir{i}/{'x' * 600}'\n" for i in range(8))
        text = ("name: n\non:\n  pull_request:\n    paths:\n" + long_entries +
                "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n")
        _payload, result = run_ci_check("CI-11", {WF: text})  # validate_pair checks the quoted lines
        self.assertEqual(len(result["findings"]), 1)
        quoted = result["findings"][0]["evidence"][0]["value"]
        self.assertLess(len(quoted), 2700)
        self.assertLess(len(quoted.splitlines()), 8)
        self.assertTrue(quoted.startswith("on:"))


class OversizeResults(unittest.TestCase):
    def setUp(self):
        class Events:
            calls = []

            def describe_event_bus(self, Name):
                return {}

            def put_events(self, Entries):
                self.calls.append(Entries)
                return {"FailedEntryCount": 0}

        self.events = Events()
        common._clients.clear()
        common._verified_buses.clear()
        common._clients["events"] = self.events

    def test_a_result_too_large_for_one_event_is_published_as_an_error_not_dropped(self):
        wf = "name: n\non:\n  pull_request:\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n"
        big = run_ci_check("CI-11", {f".github/workflows/w{i}.yml": wf for i in range(25)})[1]
        small = run_ci_check("CI-11", {WF: wf})[1]
        size = lambda r: len(json.dumps(r).encode("utf-8"))
        self.assertGreater(size(big), size(small))
        with mock.patch.object(common, "MAX_DETAIL_BYTES", (size(big) + size(small)) // 2):
            common.publish_results([big, small], "findings-hub")
        details = [json.loads(e["Detail"]) for call in self.events.calls for e in call]
        self.assertEqual([d["status"] for d in details], ["error", "completed"])
        self.assertEqual(details[0]["findings"], [])
        self.assertEqual(details[0]["scope"], big["scope"])
        self.assertIn("byte event limit", details[0]["coverage"]["limitations"][0])
        validate(details[0])  # an explicit error result is a valid contract v1 result


class HostileHistory(unittest.TestCase):
    def test_thousands_of_distinct_stages_in_one_run_do_not_stall_the_normalizer(self):
        jobs = [{"name": f"stage{i}", "conclusion": "success", "started_at": "2026-01-01T00:00:00Z",
                 "completed_at": "2026-01-01T00:01:00Z"} for i in range(5000)]
        run = {"id": 1, "run_attempt": 1, "event": "push", "head_sha": "a" * 40, "attempts": [{"run_attempt": 1, "jobs": jobs}]}
        started = time.time()
        data = github_actions.normalize({"schema": github_actions.SCHEMA, "workflow_path": WF, "runs": [run, dict(run, id=2)]})
        self.assertLess(time.time() - started, 5)
        self.assertFalse([k for k in data if k.startswith("chain_seconds:")])


if __name__ == "__main__":
    unittest.main()
