"""Measured CI impact from public GitHub Actions history: exact counting, honest gaps, no network, no credentials."""
import http.client
import io
import os
import unittest
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scanner import ci_impact, source
from scanner.report import build_report

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
WORKFLOW = """name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        py: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest
  lint:
    name: push lint
    runs-on: ubuntu-latest
    steps:
      - run: ruff check .
"""
# Same workflow, but the lint job's name is computed: GitHub's job names can no longer be told apart exactly.
COMPUTED = WORKFLOW.replace("name: push lint", "name: ${{ github.event_name }} lint")
CALLED = """on:
  workflow_call:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Install dependencies
        run: pip install -r requirements.txt
"""


def run(rid, path=".github/workflows/ci.yml", created="2026-10-14T10:00:00Z", conclusion="success", attempt=1,
        status="completed", event="push"):
    return {"id": rid, "name": "CI", "path": path, "event": event, "status": status, "conclusion": conclusion,
            "run_attempt": attempt, "head_sha": "a" * 40, "created_at": created, "run_started_at": created,
            "updated_at": created, "html_url": f"https://github.com/o/r/actions/runs/{rid}"}


def job(name, start, end, created=None, labels=("ubuntu-latest",), group="GitHub Actions", attempt=1, steps=(),
        conclusion="success"):
    return {"id": hash((name, start, attempt)), "name": name, "run_attempt": attempt, "conclusion": conclusion,
            "labels": list(labels), "runner_group_name": group, "created_at": created or start, "started_at": start,
            "completed_at": end, "steps": [{"name": n, "number": i + 1, "conclusion": "success",
                                             "started_at": s, "completed_at": e} for i, (n, s, e) in enumerate(steps)]}


def t(minute, second=0):
    return f"2026-10-14T10:{minute:02d}:{second:02d}Z"


class FakeGitHub:
    """Answers the two endpoints collect() uses, counting requests and the headers it was sent."""

    def __init__(self, runs, jobs, total=None, remaining=60):
        self.runs, self.jobs, self.total, self.remaining, self.urls = runs, jobs, total, remaining, []

    def __call__(self, url):
        self.urls.append(url)
        self.remaining -= 1
        headers = {"X-RateLimit-Remaining": str(self.remaining)}
        if "/jobs?" in url:
            rid = url.split("/runs/")[1].split("/")[0]
            jobs = self.jobs.get(rid, [])
            return {"total_count": len(jobs), "jobs": jobs}, headers
        return {"total_count": self.total if self.total is not None else len(self.runs),
                "workflow_runs": self.runs}, headers


def history(runs, jobs, **kwargs):
    return ci_impact.collect("o", "r", fetch=FakeGitHub(runs, jobs, **kwargs), now=NOW)


class CollectTest(unittest.TestCase):
    def test_reads_public_history_without_credentials(self):
        seen = {}

        class Response:
            headers = {"X-RateLimit-Remaining": "59"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return b'{"total_count": 0, "workflow_runs": []}'

        def fake_urlopen(request, timeout):
            seen.update(request.header_items())
            return Response()

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-must-not-be-sent"}), \
                mock.patch.object(ci_impact, "urlopen", fake_urlopen):
            data = ci_impact.collect("o", "r", now=NOW)
        self.assertEqual(data["status"], "ok")
        self.assertNotIn("Authorization", seen)
        self.assertNotIn("secret-must-not-be-sent", str(seen))

    def test_rate_limit_and_api_failures_are_reported_not_guessed(self):
        def limited(url):
            raise ci_impact.Unavailable("the GitHub API rate limit for unauthenticated requests (60 per hour) is used up")

        data = ci_impact.collect("o", "r", fetch=limited, now=NOW)
        self.assertEqual(data["status"], "unavailable")
        summary = ci_impact.summarize(data)
        self.assertEqual(summary["status"], "unavailable")
        self.assertIn("rate limit", summary["reason"])
        self.assertNotIn("measured", summary)

    def test_job_requests_stay_inside_the_budget_and_cover_every_workflow_first(self):
        runs = [run(i, path=f".github/workflows/w{i % 3}.yml") for i in range(1, 31)]
        runs.append(run(99, conclusion=ci_impact.AWAITING))  # nothing ran: never worth a request
        fake = FakeGitHub(runs, {}, remaining=10)  # 10 left after the listing -> 10 - RATE_RESERVE job requests
        data = ci_impact.collect("o", "r", fetch=fake, now=NOW)
        job_urls = [u for u in fake.urls if "/jobs?" in u]
        self.assertEqual(len(job_urls), 9 - ci_impact.RATE_RESERVE)
        sampled = [int(u.split("/runs/")[1].split("/")[0]) for u in job_urls]
        self.assertEqual({runs[i - 1]["path"] for i in sampled[:3]}, {f".github/workflows/w{k}.yml" for k in range(3)})
        self.assertNotIn(99, sampled)
        self.assertEqual(data["requests"], 1 + len(job_urls))
        self.assertIn("created=%3E%3D2026-09-15T12%3A00%3A00Z", fake.urls[0])  # exactly 30 days, not from midnight

    def test_a_failure_mid_sample_keeps_what_was_measured(self):
        calls = []

        def flaky(url):
            calls.append(url)
            if len(calls) == 3:
                raise ci_impact.Unavailable("the GitHub API answered HTTP 502")
            if "/jobs?" in url:
                return {"total_count": 1, "jobs": [job("test", t(0), t(1))]}, {}
            return {"total_count": 3, "workflow_runs": [run(1), run(2), run(3)]}, {}

        data = ci_impact.collect("o", "r", fetch=flaky, now=NOW)
        self.assertEqual(list(data["jobs"]), ["1"])
        self.assertEqual(data["skipped"], {"3": "the GitHub API answered HTTP 502"})  # sampled 1, 3, 2: spread out
        self.assertEqual(len(calls), 3)  # stops instead of retrying into a broken API


class SummarizeTest(unittest.TestCase):
    def test_runner_time_counts_each_execution_once(self):
        # Attempt 2 re-ran "test" and re-listed "lint" with attempt 1's timestamps (started before created).
        jobs = {"1": [job("test", t(0), t(2)), job("lint", t(0), t(1)),
                      job("test", t(5), t(8), created=t(5), attempt=2),
                      job("lint", t(0), t(1), created=t(5), attempt=2)]}
        ci = ci_impact.summarize(history([run(1, attempt=2)], jobs))
        self.assertEqual(ci["status"], "measured")
        self.assertEqual(ci["measured"]["runner_seconds"], 120 + 60 + 180)
        self.assertEqual(ci["measured"]["jobs_not_counted"], {"carried over from an earlier attempt": 1})
        self.assertEqual(ci["workflows"][0]["retried_runs"], 1)

    def test_energy_is_the_documented_formula_and_a_range(self):
        ci = ci_impact.summarize(history([run(1)], {"1": [job("test", "2026-10-14T10:00:00Z", "2026-10-14T11:00:00Z")]}))
        f = ci_impact.F
        low, high = 4 * f["min_watts_per_vcpu"] * f["pue_low"], 4 * f["max_watts_per_vcpu"] * f["pue_high"]
        self.assertEqual(ci["measured"]["energy_wh"], [round(low, 4), round(high, 4)])  # 4 vCPU x 1 h
        self.assertEqual(ci["measured"]["co2e_g"], [round(low * f["grid_low"], 4), round(high * f["grid_high"], 4)])
        self.assertLess(ci["measured"]["energy_wh"][0], ci["measured"]["energy_wh"][1])
        self.assertIn("GitHub does not publish", ci["model"]["grid_assumption"])
        for spec in ci["model"]["factors"].values():
            self.assertTrue(spec["source"].startswith("https://"))

    def test_water_is_on_site_cooling_plus_power_plant_consumption(self):
        ci = ci_impact.summarize(history([run(1)], {"1": [job("test", "2026-10-14T10:00:00Z", "2026-10-14T11:00:00Z")]}))
        f, m = ci_impact.F, ci["measured"]
        it_low, it_high = 4 * f["min_watts_per_vcpu"], 4 * f["max_watts_per_vcpu"]  # Wh of IT energy, before PUE
        onsite = [it_low * f["wue_onsite"], it_high * f["wue_onsite"]]  # Wh x L/kWh = mL
        offsite = [it_low * f["pue_low"] * f["ewif_low"], it_high * f["pue_high"] * f["ewif_high"]]
        self.assertEqual(m["water_ml_onsite"], [round(x, 4) for x in onsite])
        self.assertEqual(m["water_ml_offsite"], [round(x, 4) for x in offsite])
        self.assertEqual(m["water_ml"], [round(onsite[0] + offsite[0], 4), round(onsite[1] + offsite[1], 4)])
        self.assertEqual(f["ewif_low"], round(0.34 * 3.785, 4))  # WRI's gal/kWh, converted as WRI recommends
        self.assertEqual(f["ewif_high"], round(2.51 * 3.785, 4))
        self.assertIn("consumption", ci["model"]["water_assumption"])
        wf = ci["workflows"][0]
        self.assertEqual(wf["estimated_water_ml"], m["water_ml"])  # one run in the window, measured
        self.assertEqual(ci["measured_runs"][0]["water_ml"], m["water_ml"])
        self.assertIsNone(ci_impact.water_ml(None))  # no energy, no water

    def test_factors_are_the_published_values(self):
        # A silent edit to a factor must fail here: each value is what its cited source states.
        self.assertEqual({k: ci_impact.F[k] for k in ci_impact.F}, {
            "min_watts_per_vcpu": 0.78, "max_watts_per_vcpu": 3.76,  # CCF, Azure averages
            "pue_low": 1.125, "pue_high": 1.185,  # CCF appendix / Microsoft fleet trailing 12 months
            "grid_low": 0.322167, "grid_high": 0.426254,  # CCF, Azure US regions (EPA eGRID)
            "wue_onsite": 0.34,  # Microsoft, Americas FY2025
            "ewif_low": 1.2869, "ewif_high": 9.5003,  # WRI 2020 Appendix 1: 0.34 and 2.51 gal/kWh x 3.785
        })

    def test_water_follows_the_energy_and_its_gaps(self):
        jobs = {"1": [job("linux", t(0), t(1))], "2": [job("mac", t(0), t(2), labels=["macos-latest"])]}
        runs = [run(1), run(2), run(3, path=".github/workflows/fork.yml", conclusion=ci_impact.AWAITING)]
        ci = ci_impact.summarize(history(runs, jobs))
        main = next(w for w in ci["workflows"] if w["path"] == ".github/workflows/ci.yml")
        self.assertIsNone(main["estimated_energy_wh"])  # one measured run had no energy: nothing is averaged
        self.assertIsNone(main["estimated_water_ml"])
        self.assertIsNone(main["estimated_co2e_g"])
        fork = next(w for w in ci["workflows"] if w["path"] == ".github/workflows/fork.yml")
        self.assertEqual(fork["estimated_water_ml"], [0.0, 0.0])  # nothing ran while it waited for approval
        self.assertIsNone(ci["estimate"]["water_ml"])
        self.assertEqual(set(ci_impact.impacts(None)), {"energy_wh", "co2e_g", "water_ml", "water_ml_onsite",
                                                        "water_ml_offsite"})
        whole = ci_impact.summarize(history([run(1)], {"1": [job("linux", t(0), t(1))]}))["estimate"]
        self.assertEqual(whole["water_ml"], [round(whole["water_ml_onsite"][i] + whole["water_ml_offsite"][i], 4)
                                             for i in (0, 1)])

    def test_runners_without_published_hardware_get_time_but_no_energy(self):
        jobs = {"1": [job("linux", t(0), t(1)), job("mac", t(0), t(2), labels=["macos-latest"]),
                      job("own", t(0), t(3), labels=["self-hosted", "linux"], group="Default"),
                      job("big", t(0), t(4), labels=["ubuntu-latest-16-cores"], group="Larger runners")]}
        ci = ci_impact.summarize(history([run(1)], jobs))
        m = ci["measured"]
        self.assertEqual(m["runner_seconds"], 60 + 120 + 180 + 240)
        self.assertEqual(m["energy_covered_seconds"], 60)
        self.assertEqual(m["seconds_by_runner"], {"ubuntu-latest-16-cores (unknown)": 240, "self-hosted (self-hosted)": 180,
                                                  "macos-latest (macos)": 120, "ubuntu-latest (hosted)": 60})
        self.assertIsNone(ci["workflows"][0]["energy_wh_per_run"])  # this workflow's runs are not fully covered
        self.assertIsNone(ci_impact.energy_wh(60, ci_impact.RUNNER_SPECS["macos-14"]))
        self.assertEqual(ci_impact.energy_wh(0, None), (0.0, 0.0))  # a skipped job used no runner

    def test_estimate_scales_per_workflow_means_by_executed_runs_only(self):
        runs = [run(1), run(2), run(3), run(4, conclusion=ci_impact.AWAITING),
                run(5, path=".github/workflows/other.yml"), run(6, status="in_progress", conclusion=None)]
        jobs = {"1": [job("test", t(0), t(1))], "2": [job("test", t(0), t(3))], "3": [job("test", t(0), t(2))]}
        data = history(runs, jobs)
        del data["jobs"]["5"]  # as if the request budget ran out before other.yml was reached
        ci = ci_impact.summarize(data)
        main = next(w for w in ci["workflows"] if w["path"] == ".github/workflows/ci.yml")
        self.assertEqual((main["runs"], main["completed_runs"], main["executed_runs"], main["awaiting_approval_runs"]),
                         (5, 4, 3, 1))
        self.assertEqual(main["mean_seconds_per_run"], 120.0)
        self.assertEqual(main["estimated_seconds"], 360)
        self.assertEqual(ci["estimate"]["runner_seconds"], 360)
        self.assertEqual(ci["estimate"]["runs_without_sample"], 1)  # other.yml was never measured: not estimated
        other = next(w for w in ci["workflows"] if w["path"] == ".github/workflows/other.yml")
        self.assertIsNone(other["estimated_seconds"])

    def test_a_truncated_listing_narrows_the_window_to_what_was_listed(self):
        runs = [run(1, created="2026-10-14T10:00:00Z"), run(2, created="2026-10-10T09:00:00Z"),
                run(3, created="2026-10-10T09:00:00Z")]
        ci = ci_impact.summarize(history(runs, {}, total=250))
        w = ci["window"]
        self.assertEqual((w["start"], w["start_exclusive"], w["runs_listed"], w["runs_total_since"], w["complete"]),
                         ("2026-10-10T09:00:00Z", True, 1, 250, False))
        self.assertEqual(ci["status"], "runs_only")  # nothing sampled: no runner time is claimed
        self.assertIsNone(ci["measured"]["energy_wh"])

    def test_no_runs_says_so(self):
        ci = ci_impact.summarize(history([], {}))
        self.assertEqual(ci["status"], "no_runs")
        self.assertIn("no GitHub Actions runs in the last 30 days", ci["reason"])


def spent_wh(spent):
    """The step's window energy, recomputed from its own figures: (low, high) Wh."""
    return tuple(spent["energy_wh_in_window"])


class FindingLinkTest(unittest.TestCase):
    def finding(self, check, line, path=".github/workflows/ci.yml"):
        return {"id": f"{check}-{line}", "check_id": check, "file": path, "line": line}

    def links(self, findings, measured, workflow=WORKFLOW):
        # Three runs in the window, sampled in spread order 1, 3, 2; the budget reaches only the measured ones.
        runs = [run(1), run(2), run(3)]
        jobs = {rid: listing for rid, listing in zip(("1", "3", "2"), measured)}
        data = history(runs, jobs, remaining=len(jobs) + ci_impact.RATE_RESERVE + 1)
        files = [(".github/workflows/ci.yml", workflow), (".github/workflows/called.yml", CALLED)]
        return ci_impact.summarize(data, findings, files)["finding_links"]

    def matrix_run(self, install_seconds):
        steps = lambda: [("Run actions/checkout@v4", t(0, 0), t(0, 5)),
                         ("Install dependencies", t(0, 5), t(0, 5 + install_seconds)),
                         ("Run tests", t(0, 5 + install_seconds), t(0, 59))]
        return [job("test (3.11)", t(0), t(1), steps=steps()), job("test (3.12)", t(0), t(1), steps=steps()),
                job("push lint", t(0), t(0, 30))]

    def test_a_cited_step_is_matched_exactly_and_its_window_time_shown(self):
        links = self.links([self.finding("CI-06", 12)], [self.matrix_run(20), self.matrix_run(30)])
        link = links["CI-06-12"]
        self.assertEqual(link["match"], "step")
        self.assertEqual(link["job"], {"name": "test", "measured_runs": 2, "executions": 4, "mean_seconds_per_run": 120.0})
        self.assertEqual(link["step"]["name"], "Install dependencies")
        self.assertEqual(link["step"]["mean_seconds_per_run"], 50.0)  # both matrix legs: (20+20 + 30+30) / 2 runs
        self.assertEqual(link["step_in_window"]["runs"], 3)
        self.assertEqual(link["step_in_window"]["measured_runs"], 2)
        self.assertEqual(link["step_in_window"]["seconds_in_window"], 150)
        spent = link["step_in_window"]
        for got, want in zip(spent["water_ml_in_window"], ci_impact.water_ml(spent_wh(spent))):
            self.assertAlmostEqual(got, want, places=3)  # recomputed from the rounded energy
        self.assertNotIn("save", str(link).lower())  # spent time, never a promised saving

    def test_checks_whose_cost_is_not_the_cited_step_get_no_window_time(self):
        # CI-08 cites the `clean` step (the cost is the build after it); CI-10's fix skips whole jobs on some events.
        for check in ("CI-08", "CI-10", "CI-16"):
            links = self.links([self.finding(check, 12)], [self.matrix_run(20)])
            self.assertEqual(links[f"{check}-12"]["match"], "step")
            self.assertIsNone(links[f"{check}-12"]["step_in_window"])

    def test_names_that_cannot_be_known_exactly_are_not_matched(self):
        links = self.links([self.finding("CI-11", 2), self.finding("CI-10", 10)], [self.matrix_run(20)])
        self.assertIsNone(links["CI-10-10"]["step_in_window"])
        computed = self.links([self.finding("CI-06", 19), self.finding("CI-06", 12)], [self.matrix_run(20)],
                              workflow=COMPUTED)
        self.assertEqual(computed["CI-06-19"]["match"], "workflow")  # its own name is an expression
        self.assertIn("computed", computed["CI-06-19"]["note"])
        # "test" could also match a computed sibling rendered as "test (...)": refuse rather than guess.
        self.assertEqual(computed["CI-06-12"]["match"], "workflow")
        self.assertIn("another job", computed["CI-06-12"]["note"])
        self.assertEqual(links["CI-11-2"]["match"], "workflow")  # a trigger line is not inside a job
        self.assertEqual(links["CI-10-10"]["match"], "step")  # `uses:` steps are named "Run <action>" by GitHub
        self.assertEqual(links["CI-10-10"]["step"]["name"], "Run actions/checkout@v4")

    def test_a_step_missing_from_a_measured_job_falls_back_to_the_job(self):
        broken = self.matrix_run(20)
        broken[0]["steps"] = [s for s in broken[0]["steps"] if s["name"] != "Install dependencies"]
        links = self.links([self.finding("CI-06", 12)], [broken])
        self.assertEqual(links["CI-06-12"]["match"], "job")
        self.assertIsNone(links["CI-06-12"]["step_in_window"])

    def test_findings_in_workflows_without_runs_say_so(self):
        links = self.links([self.finding("CI-06", 5, path=".github/workflows/release.yml")], [self.matrix_run(20)])
        self.assertEqual(links["CI-06-5"]["match"], "none")
        self.assertEqual(links["CI-06-5"]["note"], "this workflow has no runs in the window")


class EdgeCaseTest(unittest.TestCase):
    def fetch_error(self, error):
        with mock.patch.object(ci_impact, "urlopen", side_effect=error):
            with self.assertRaises(ci_impact.Unavailable) as caught:
                ci_impact.http_fetch("https://api.github.com/repos/o/r/actions/runs")
        return str(caught.exception)

    def http_error(self, code, headers=None):
        return HTTPError("u", code, "x", headers or {}, io.BytesIO(b""))

    def test_http_failures_map_to_plain_reasons(self):
        limited = self.fetch_error(self.http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1792046742"}))
        self.assertIn("60 unauthenticated requests per hour", limited)
        self.assertIn("resets", limited)
        self.assertIn("60 unauthenticated", self.fetch_error(self.http_error(429)))
        self.assertIn("HTTP 403", self.fetch_error(self.http_error(403)))  # a 403 that is not the rate limit
        self.assertIn("not found or not public", self.fetch_error(self.http_error(404)))
        self.assertIn("HTTP 502", self.fetch_error(self.http_error(502)))
        self.assertIn("could not be reached", self.fetch_error(http.client.IncompleteRead(b"")))
        with self.assertRaises(ci_impact.Unavailable):  # a JSON list where an object was expected
            ci_impact._get(lambda url: ([], {}), "u")

    def test_a_low_quota_is_named_as_the_reason(self):
        ci = ci_impact.summarize(history([run(1)], {"1": [job("test", t(0), t(1))]}, remaining=ci_impact.RATE_RESERVE + 1))
        self.assertEqual(ci["status"], "runs_only")
        self.assertIn("were left for this network address", ci["reason"])

    def test_long_job_listings_are_paged_and_oversized_runs_left_out(self):
        many = [job(f"j{i}", t(0), t(0, 1)) for i in range(150)]
        fake = FakeGitHub([run(1), run(2)], {})

        def paged(url):
            if "/jobs?" not in url:
                return fake(url)
            rid, page = url.split("/runs/")[1].split("/")[0], int(url.split("page=")[-1])
            listing = many if rid == "1" else [job(f"k{i}", t(0), t(0, 1)) for i in range(400)]
            return {"total_count": len(listing), "jobs": listing[(page - 1) * 100:page * 100]}, {}

        data = ci_impact.collect("o", "r", fetch=paged, now=NOW)
        self.assertEqual(len(data["jobs"]["1"]), 150)
        self.assertNotIn("2", data["jobs"])
        self.assertIn("more than 300 job records", data["skipped"]["2"])

    def test_a_listing_that_cannot_be_bounded_is_not_called_empty(self):
        burst = [run(i, created="2026-10-14T10:00:00Z") for i in range(1, 4)]
        ci = ci_impact.summarize(history(burst, {}, total=250))
        self.assertEqual(ci["status"], "unavailable")
        self.assertIn("250 runs", ci["reason"])

    def test_malformed_run_records_are_skipped_not_fatal(self):
        runs = [run(1), {"id": None}, "junk", {**run(2), "created_at": None}]
        data = history(runs, {"1": [job("test", t(0), t(1))]})
        self.assertEqual(data["malformed_runs"], 3)
        ci = ci_impact.summarize(data)
        self.assertEqual(ci["status"], "measured")
        self.assertTrue(any("ignored" in x for x in ci["limitations"]))

    def test_samples_spread_over_the_window(self):
        self.assertEqual(ci_impact._spread(list(range(7))), [0, 6, 3, 1, 4, 2, 5])
        self.assertEqual(ci_impact._spread([]), [])

    def test_called_workflows_say_where_their_time_is_counted(self):
        finding = {"id": "f", "check_id": "CI-06", "file": ".github/workflows/called.yml", "line": 8}
        ci = ci_impact.summarize(history([run(1)], {"1": [job("test", t(0), t(1))]}), [finding],
                                 [(".github/workflows/called.yml", CALLED)])
        self.assertEqual(ci["finding_links"]["f"]["match"], "none")
        self.assertIn("calling workflow", ci["finding_links"]["f"]["note"])

    def test_a_reusable_caller_named_like_the_job_is_not_mistaken_for_it(self):
        caller = WORKFLOW.replace("    name: push lint\n    runs-on: ubuntu-latest\n    steps:\n      - run: ruff check .\n",
                                  "    name: test (reusable)\n    uses: ./.github/workflows/called.yml\n")
        finding = {"id": "f", "check_id": "CI-06", "file": ".github/workflows/ci.yml", "line": 12}
        jobs = {"1": [job("test", t(0), t(1), steps=[("Install dependencies", t(0, 0), t(0, 30))]),
                      job("test (reusable) / build", t(0), t(5), steps=[("Install dependencies", t(0, 0), t(4, 0))])]}
        ci = ci_impact.summarize(history([run(1)], jobs), [finding], [(".github/workflows/ci.yml", caller)])
        link = ci["finding_links"]["f"]
        self.assertEqual(link["job"]["executions"], 1)
        self.assertEqual(link["step"]["mean_seconds_per_run"], 30.0)

    def test_all_malformed_records_are_named_as_such(self):
        ci = ci_impact.summarize(history([{"id": "x"}, {"id": None}], {}))
        self.assertEqual(ci["status"], "unavailable")
        self.assertIn("no id or creation time", ci["reason"])

    def test_duplicate_job_names_are_not_matched(self):
        dup = WORKFLOW.replace("name: push lint", "name: test")
        finding = {"id": "f", "check_id": "CI-06", "file": ".github/workflows/ci.yml", "line": 12}
        ci = ci_impact.summarize(history([run(1)], {"1": [job("test", t(0), t(1))]}), [finding],
                                 [(".github/workflows/ci.yml", dup)])
        self.assertEqual(ci["finding_links"]["f"]["match"], "workflow")
        self.assertIn("two jobs", ci["finding_links"]["f"]["note"])


class ReportWiringTest(unittest.TestCase):
    def scan(self, ci):
        with TemporaryDirectory() as tmp:
            Path(tmp, "app.py").write_text("x = 1\n")
            target = source.Target("github:o/r", Path(tmp), "b" * 40, "git", "https://github.com/o/r")
            return build_report(target, source.collect(Path(tmp), source.Limits()), [], scan_id="s1", now=NOW, ci=ci)

    def test_measured_history_fills_the_impact(self):
        data = history([run(1)], {"1": [job("test", t(0), t(1))]})
        impact = self.scan(lambda: data)["impact"]
        self.assertEqual((impact["status"], impact["ci"]["status"]), ("ci_measured", "measured"))
        self.assertEqual(impact["detector_measurements"], [])

    def test_without_history_the_impact_stays_not_quantified_with_a_reason(self):
        impact = self.scan(None)["impact"]
        self.assertEqual((impact["status"], impact["ci"]["status"]), ("not_quantified", "not_collected"))
        broken = self.scan(lambda: {"status": "ok"})["impact"]  # malformed history must not crash the scan
        self.assertEqual((broken["status"], broken["ci"]["status"]), ("not_quantified", "unavailable"))


if __name__ == "__main__":
    unittest.main()
