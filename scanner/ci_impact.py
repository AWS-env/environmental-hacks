"""Measured CI workload of a public GitHub repository, from its public GitHub Actions history.

Read-only and unauthenticated: no token is read or sent (not even GITHUB_TOKEN), nothing is executed. The GitHub REST
API allows 60 unauthenticated requests per hour per IP, so a scan makes one runs listing plus a bounded number of
job listings and stops early when the quota runs low; whatever it could not fetch is reported, never guessed.

Three tiers, kept apart in the report:
- measured: run counts and job/step runner time (`completed_at - started_at`), exactly as GitHub reports them;
- modelled: energy (always a low-high range) from runner vCPUs and published coefficients, CO2e from that energy
  under an explicitly stated grid assumption. Runners whose hardware is not published get no energy at all;
- estimated: a measured mean scaled by an exact run count (per workflow, or per cited step for CI findings). These
  describe what was spent, never what a fix would save.
"""
from __future__ import annotations

import http.client
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.github.com"
WINDOW_DAYS = 30
RUNS_PAGE = 100
JOBS_PAGE = 100
MAX_JOB_PAGES = 3        # a run with more than 300 job records (all attempts) is left out of the sample
MAX_JOB_REQUESTS = 12    # job listing requests per scan (each is one of the 60 unauthenticated requests per hour)
RATE_RESERVE = 5         # leave this many requests for the IP's other scans
HTTP_TIMEOUT = 20
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "environmental-hacks-scanner"}
HOSTED_GROUP = "GitHub Actions"  # runner_group_name of standard GitHub-hosted runners

RUNNERS_SOURCE = "https://docs.github.com/en/actions/reference/runners/github-hosted-runners"
CCF_SOURCE = "https://www.cloudcarbonfootprint.org/docs/methodology/"
# Standard GitHub-hosted runners for public repositories (RUNNERS_SOURCE, "Standard GitHub-hosted runners for public
# repositories"). Windows and Ubuntu run in Azure; macOS runs in GitHub's own macOS cloud, which publishes no power
# data, so macOS time is measured but never converted to energy.
_AZURE = {"provider": "azure"}
_MACOS = {"provider": "github-macos-cloud"}
RUNNER_SPECS = {
    "ubuntu-slim": {"vcpus": 1, "memory_gb": 5, "os": "Linux", "arch": "x64", **_AZURE},
    **{label: {"vcpus": 4, "memory_gb": 16, "os": "Linux", "arch": "x64", **_AZURE}
       for label in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04", "ubuntu-26.04")},
    **{label: {"vcpus": 4, "memory_gb": 16, "os": "Windows", "arch": "x64", **_AZURE}
       for label in ("windows-latest", "windows-2025", "windows-2025-vs2026", "windows-2022")},
    **{label: {"vcpus": 4, "memory_gb": 16, "os": "Linux", "arch": "arm64", **_AZURE}
       for label in ("ubuntu-24.04-arm", "ubuntu-22.04-arm", "ubuntu-26.04-arm")},
    **{label: {"vcpus": 4, "memory_gb": 16, "os": "Windows", "arch": "arm64", **_AZURE}
       for label in ("windows-11-arm", "windows-11-vs2026-arm")},
    **{label: {"vcpus": 4, "memory_gb": 14, "os": "macOS", "arch": "Intel", **_MACOS}
       for label in ("macos-15-intel", "macos-26-intel")},
    **{label: {"vcpus": 3, "memory_gb": 7, "os": "macOS", "arch": "arm64", **_MACOS}
       for label in ("macos-latest", "macos-14", "macos-15", "macos-26", "xcode-27")},
}

# Cloud Carbon Footprint methodology (CCF_SOURCE). The low end of every range uses the low factor, the high end the
# high factor, so the true value for the stated assumptions lies inside the range.
FACTORS = {
    "min_watts_per_vcpu": {"value": 0.78, "unit": "W", "source": CCF_SOURCE,
                           "note": "CCF Azure average minimum watts per vCPU (0% CPU utilisation), used when the "
                                   "processor micro-architecture is unknown"},
    "max_watts_per_vcpu": {"value": 3.76, "unit": "W", "source": CCF_SOURCE,
                           "note": "CCF Azure average maximum watts per vCPU (100% CPU utilisation)"},
    "pue_low": {"value": 1.125, "unit": "ratio", "source": CCF_SOURCE,
                "note": "Azure PUE in CCF's appendix: Microsoft's latest datacenter designs"},
    "pue_high": {"value": 1.185, "unit": "ratio", "source": CCF_SOURCE,
                 "note": "Azure PUE CCF applies: Microsoft's fleet-wide trailing 12-month average"},
    "grid_low": {"value": 0.322167, "unit": "kgCO2e/kWh", "source": CCF_SOURCE,
                 "note": "lowest CCF factor among Azure US regions (West US, West US 2/3, West Central US; EPA eGRID)"},
    "grid_high": {"value": 0.426254, "unit": "kgCO2e/kWh", "source": CCF_SOURCE,
                  "note": "highest CCF factor among Azure US regions (Central US; EPA eGRID)"},
}
F = {key: spec["value"] for key, spec in FACTORS.items()}
GRID_ASSUMPTION = ("GitHub does not publish which Azure regions run hosted runners. CO2e assumes Azure US regions "
                   "and uses the lowest and highest CCF factors among them; other regions would differ.")
ENERGY_FORMULA = ("energy (Wh) = runner vCPUs x runner hours x watts per vCPU x PUE; low end: "
                  f"{F['min_watts_per_vcpu']} W x PUE {F['pue_low']}, high end: {F['max_watts_per_vcpu']} W x "
                  f"PUE {F['pue_high']}")
CO2E_FORMULA = f"CO2e (g) = energy (kWh) x {F['grid_low']}-{F['grid_high']} kgCO2e/kWh x 1000"
EXCLUSIONS = [
    "CPU utilisation of CI jobs is not published, so energy spans idle (0%) to full (100%) CPU.",
    "CCF's Azure average watts per vCPU (from x86 servers) are applied to every Azure-hosted runner, including the "
    "arm64 ones, because CCF publishes no separate figure for them.",
    "No separate memory term: CCF adds one only for instance types with more memory per CPU than its SPECpower "
    "baseline, and GitHub's runners are not in CCF's instance lists.",
    "Embodied hardware emissions, storage, network transfer and water are not included.",
    "macOS, self-hosted and larger or custom runners: runner time is measured, energy is not estimated.",
    "Queue time before a job starts is excluded; only time a runner spent on the job counts.",
    "Findings are matched to jobs and steps by name using the workflow file at the scanned commit; measured runs "
    "may come from other commits.",
]

TIME = "%Y-%m-%dT%H:%M:%SZ"
# Checks whose cited step is the work the fix targets, so the step's time is worth showing next to the finding. This
# is what the step spent, not a saving: CI-08 cites a `clean` step (the cost is in the build after it) and CI-10's fix
# skips whole jobs on some events, so neither is listed.
STEP_TARGETS = {
    "CI-06": "Caching dependencies shortens dependency installs like this one (and any other install steps in the "
             "job); how much depends on cache hits.",
    "CI-07": "Layer caching shortens this build when layers are unchanged; how much depends on what changed.",
}
EXPRESSION = re.compile(r"\$\{\{")
AWAITING = "action_required"  # a run waiting for a maintainer to approve it (e.g. a first-time fork PR): no job ran


class Unavailable(Exception):
    """CI history could not be read; the message is safe to show."""


def _parse(ts):
    try:
        return datetime.strptime(ts, TIME).replace(tzinfo=timezone.utc) if ts else None
    except (TypeError, ValueError):
        return None


def _seconds(start, end):
    a, b = _parse(start), _parse(end)
    return max(0, int((b - a).total_seconds())) if a and b else None


def _reset_note(headers):
    value = {k.lower(): v for k, v in (headers or {}).items()}.get("x-ratelimit-reset")
    if value is None or not str(value).isdigit():
        return ""
    return f", resets {datetime.fromtimestamp(int(value), timezone.utc).strftime('%H:%M UTC')}"


def http_fetch(url):
    """One unauthenticated GET: (JSON body, response headers). Raises Unavailable with a user-safe reason."""
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=HTTP_TIMEOUT) as response:
            return json.load(response), dict(response.headers)
    except HTTPError as error:
        headers = {k.lower(): v for k, v in (error.headers or {}).items()}
        if error.code == 429 or (error.code == 403 and headers.get("x-ratelimit-remaining") == "0"):
            raise Unavailable("the GitHub API's limit of 60 unauthenticated requests per hour for this network address "
                              f"is used up{_reset_note(headers)}") from None
        if error.code in (404, 410, 451):
            raise Unavailable("GitHub returned no Actions history for this repository (not found or not public)") from None
        raise Unavailable(f"the GitHub API answered HTTP {error.code}") from None
    except (URLError, OSError, ValueError, http.client.HTTPException) as error:
        raise Unavailable(f"the GitHub API could not be reached ({type(error).__name__})") from None


def _get(fetch, url):
    """fetch() with the body's shape checked: anything but a JSON object is unusable, not a crash."""
    body, headers = fetch(url)
    if not isinstance(body, dict):
        raise Unavailable("the GitHub API returned an unexpected response")
    return body, headers


def _remaining(headers):
    value = {k.lower(): v for k, v in (headers or {}).items()}.get("x-ratelimit-remaining")
    return int(value) if value is not None and str(value).isdigit() else None


def _workflow_path(run):
    return str(run.get("path") or "").split("@", 1)[0]


RUN_FIELDS = ("id", "name", "event", "status", "conclusion", "run_attempt", "head_sha", "created_at",
              "run_started_at", "updated_at", "html_url")


def _trim_run(run):
    if not isinstance(run, dict) or not isinstance(run.get("id"), int) or not _parse(run.get("created_at")):
        return None
    return {**{k: run.get(k) for k in RUN_FIELDS}, "path": _workflow_path(run)}


def _trim_job(job):
    steps = [{"name": s.get("name"), "number": s.get("number"), "conclusion": s.get("conclusion"),
              "seconds": _seconds(s.get("started_at"), s.get("completed_at"))}
             for s in job.get("steps") or [] if isinstance(s, dict)]
    return {"id": job.get("id"), "name": job.get("name"), "run_attempt": job.get("run_attempt"),
            "conclusion": job.get("conclusion"), "labels": [str(x) for x in job.get("labels") or []],
            "runner_group_name": job.get("runner_group_name"), "created_at": job.get("created_at"),
            "started_at": job.get("started_at"), "completed_at": job.get("completed_at"), "steps": steps}


def _spread(queue):
    """Indexes 0, n-1, n/2, n/4, 3n/4, ...: a few picks cover the window instead of only its newest runs."""
    n, order, seen, parts = len(queue), [], set(), 1
    for i in ((0, n - 1) if n else ()):
        if i not in seen:
            seen.add(i)
            order.append(i)
    while len(order) < n:
        parts *= 2
        for k in range(1, parts, 2):
            i = k * (n - 1) // parts
            if i not in seen:
                seen.add(i)
                order.append(i)
        if parts > 2 * n:
            order += [i for i in range(n) if i not in seen]
            break
    return [queue[i] for i in order]


def _sample_order(runs):
    """Completed runs that ran jobs, round-robin across workflows (busiest workflow first), each workflow's picks
    spread over the window: the budget measures as many workflows, and as much of the window, as it can."""
    grouped = defaultdict(list)
    for run in runs:
        if run["status"] == "completed" and run["conclusion"] != AWAITING:
            grouped[run["path"]].append(run)
    queues = [_spread(q) for q in sorted(grouped.values(), key=lambda q: (-len(q), q[0]["path"]))]
    order, depth = [], 0
    while any(len(q) > depth for q in queues):
        order += [q[depth] for q in queues if len(q) > depth]
        depth += 1
    return order


def collect(owner, repo, fetch=None, now=None):
    """Fetch the history the summary needs. Never raises: failures become `{"status": "unavailable", ...}`."""
    fetch = fetch or http_fetch
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    since = (now - timedelta(days=WINDOW_DAYS)).strftime(TIME)
    base = f"{API}/repos/{owner}/{repo}/actions/runs"
    history = {"repository": f"{owner}/{repo}", "collected_at": now.strftime(TIME), "since": since,
               "source": base, "requests": 0, "runs_total": 0, "runs": [], "jobs": {}, "skipped": {},
               "malformed_runs": 0, "budget_note": None}
    try:
        body, headers = _get(fetch, f"{base}?per_page={RUNS_PAGE}&exclude_pull_requests=true&created={quote('>=' + since)}")
    except Unavailable as error:
        return {**history, "status": "unavailable", "reason": str(error)}
    history["requests"] = 1
    listed = body.get("workflow_runs") if isinstance(body.get("workflow_runs"), list) else []
    runs = [r for r in (_trim_run(run) for run in listed) if r]
    history["malformed_runs"], history["listed"] = len(listed) - len(runs), len(listed)
    total = body.get("total_count")
    history["runs_total"] = total if isinstance(total, int) and total >= len(listed) else len(listed)
    history["complete"] = history["runs_total"] <= len(listed)
    if not history["complete"] and runs:
        # The listing is newest first and cut at 100. Runs sharing the oldest listed instant may continue on the next
        # page, so the window starts just after it and holds exactly the runs created since.
        oldest = min(run["created_at"] for run in runs)
        runs = [run for run in runs if run["created_at"] > oldest]
        history["window_start"], history["start_exclusive"] = oldest, True
    else:
        history["window_start"], history["start_exclusive"] = since, False
    history["runs"] = runs
    remaining = _remaining(headers)
    budget = MAX_JOB_REQUESTS if remaining is None else min(MAX_JOB_REQUESTS, remaining - RATE_RESERVE)
    history["rate_limit_remaining"] = remaining
    if budget <= 0:
        history["budget_note"] = (f"only {remaining} of the GitHub API's 60 unauthenticated requests per hour were left "
                                  f"for this network address{_reset_note(headers)}, so job timings were not fetched")
    for run in _sample_order(runs):
        if budget <= 0:
            break
        jobs, page, total, listed = [], 1, None, 0
        try:
            while budget > 0 and page <= MAX_JOB_PAGES:
                body, headers = _get(fetch, f"{base}/{run['id']}/jobs?filter=all&per_page={JOBS_PAGE}&page={page}")
                budget -= 1
                history["requests"] += 1
                total = body.get("total_count") if isinstance(body.get("total_count"), int) else 0
                raw = body.get("jobs") if isinstance(body.get("jobs"), list) else []
                listed += len(raw)  # malformed records still count as listed, so paging ends where GitHub's does
                jobs += [_trim_job(job) for job in raw if isinstance(job, dict)]
                if listed >= total or not raw:
                    break
                page += 1
        except Unavailable as error:
            history["skipped"][str(run["id"])] = str(error)
            break  # the quota or the API is gone: do not keep trying
        if total is not None and listed >= total:
            history["jobs"][str(run["id"])] = jobs
        else:
            history["skipped"][str(run["id"])] = (f"run has more than {MAX_JOB_PAGES * JOBS_PAGE} job records"
                                                  if page > MAX_JOB_PAGES else "request budget ran out mid-run")
    history["status"] = "ok"
    return history


# ---------- pure computation (no network) ----------

def runner_of(job):
    """(label, spec or None, kind): kind is hosted / macos / self-hosted / unknown."""
    labels = job.get("labels") or []
    if any(str(label).lower() == "self-hosted" for label in labels):
        return (labels[0] if labels else "self-hosted"), None, "self-hosted"
    if job.get("runner_group_name") == HOSTED_GROUP and len(labels) == 1 and labels[0] in RUNNER_SPECS:
        spec = RUNNER_SPECS[labels[0]]
        return labels[0], spec, "hosted" if spec["provider"] == "azure" else "macos"
    return (", ".join(map(str, labels)) or "unknown"), None, "unknown"


def energy_wh(seconds, spec):
    """(low, high) Wh for `seconds` on a runner, or None when its hardware has no published coefficients."""
    if seconds == 0:
        return (0.0, 0.0)  # e.g. a job skipped by its `if:`: GitHub assigns it no runner, and it used none
    if not spec or spec["provider"] != "azure":
        return None
    vcpu_hours = spec["vcpus"] * seconds / 3600
    return (vcpu_hours * F["min_watts_per_vcpu"] * F["pue_low"], vcpu_hours * F["max_watts_per_vcpu"] * F["pue_high"])


def co2e_g(wh):
    return None if wh is None else (wh[0] / 1000 * F["grid_low"] * 1000, wh[1] / 1000 * F["grid_high"] * 1000)


def _add(a, b):
    return b if a is None else a if b is None else (a[0] + b[0], a[1] + b[1])


def _scale(r, k):
    return None if r is None else (r[0] * k, r[1] * k)


def _r(r, digits=4):
    return None if r is None else [round(r[0], digits), round(r[1], digits)]


def _mean_range(ranges):
    return (statistics.fmean(r[0] for r in ranges), statistics.fmean(r[1] for r in ranges))


def counted(job):
    """A job's runner time counts once: GitHub re-lists jobs carried over from an earlier attempt with that attempt's
    timestamps (started_at before the new record's created_at); those were already counted in their own attempt."""
    started, created = _parse(job.get("started_at")), _parse(job.get("created_at"))
    if _seconds(job.get("started_at"), job.get("completed_at")) is None:
        return False, "no start or end time"
    if started and created and started < created:
        return False, "carried over from an earlier attempt"
    return True, None


def summarize(history, findings=(), files=()):
    """The report's `impact.ci` block from `collect()` output, findings and the scanned files."""
    if history.get("status") != "ok":
        return {"status": "unavailable", "reason": history.get("reason") or "CI history was not collected",
                "source": history.get("source"), "collected_at": history.get("collected_at")}
    runs, total = history["runs"], history["runs_total"]
    window = {"start": history["window_start"], "start_exclusive": history["start_exclusive"],
              "end": history["collected_at"], "days": WINDOW_DAYS, "runs_listed": len(runs),
              "runs_total_since": total, "complete": history["complete"]}
    base = {"source": history["source"], "authenticated": False, "collected_at": history["collected_at"],
            "requests": history["requests"], "window": window, "model": model_block()}
    if not runs:
        if history.get("listed") and history.get("malformed_runs") == history["listed"]:
            return {**base, "status": "unavailable",
                    "reason": "GitHub's run records had no id or creation time, so no run could be counted"}
        if total:
            return {**base, "status": "unavailable",
                    "reason": (f"GitHub lists {total} runs in the last {WINDOW_DAYS} days, but the newest page could not "
                               "be bounded to an exact window, so nothing is counted")}
        return {**base, "status": "no_runs",
                "reason": f"no GitHub Actions runs in the last {WINDOW_DAYS} days, so there is no CI workload to measure"}

    by_run = {str(run["id"]): run for run in runs}
    sampled = {rid: jobs for rid, jobs in history["jobs"].items() if rid in by_run}
    measured_runs, per_workflow = [], defaultdict(lambda: {"seconds": [], "wh": []})
    totals = {"seconds": 0, "energy_seconds": 0, "wh": None, "jobs": 0, "by_runner": Counter(), "excluded": Counter()}
    for rid, jobs in sampled.items():
        run = by_run[rid]
        run_seconds, run_wh, run_energy_known, run_jobs = 0, (0.0, 0.0), True, []
        for job in jobs:
            ok, why = counted(job)
            label, spec, kind = runner_of(job)
            secs = _seconds(job.get("started_at"), job.get("completed_at")) or 0
            entry = {"name": job.get("name"), "attempt": job.get("run_attempt"), "conclusion": job.get("conclusion"),
                     "runner": label, "runner_kind": kind, "seconds": secs if ok else 0, "counted": ok}
            if not ok:
                entry["not_counted"] = why
                totals["excluded"][why] += 1
            else:
                wh = energy_wh(secs, spec)
                run_seconds += secs
                if wh is None:
                    run_energy_known = False
                else:
                    run_wh = _add(run_wh, wh)
                    totals["energy_seconds"] += secs
                    totals["wh"] = _add(totals["wh"], wh)
                totals["seconds"] += secs
                totals["jobs"] += 1
                if secs:
                    totals["by_runner"][f"{label} ({kind})"] += secs
                entry["energy_wh"] = _r(wh)
            run_jobs.append(entry)
        run_wh = run_wh if run_energy_known else None
        per_workflow[run["path"]]["seconds"].append(run_seconds)
        per_workflow[run["path"]]["wh"].append(run_wh)
        measured_runs.append({"id": run["id"], "workflow": run["name"], "path": run["path"], "event": run["event"],
                              "conclusion": run["conclusion"], "attempts": run["run_attempt"],
                              "created_at": run["created_at"], "url": run["html_url"], "runner_seconds": run_seconds,
                              "energy_wh": _r(run_wh), "jobs": run_jobs})

    workflows, est_seconds, est_wh, est_runs, unsampled_runs, energy_complete = [], 0, None, 0, 0, True
    names = {}
    for run in runs:
        names.setdefault(run["path"], run["name"])
    for path in sorted({run["path"] for run in runs}, key=lambda p: (-sum(r["path"] == p for r in runs), p)):
        group = [run for run in runs if run["path"] == path]
        completed = [run for run in group if run["status"] == "completed"]
        executed = [run for run in completed if run["conclusion"] != AWAITING]
        stats = per_workflow.get(path)
        row = {"path": path, "name": names[path], "runs": len(group), "completed_runs": len(completed),
               "executed_runs": len(executed), "awaiting_approval_runs": len(completed) - len(executed),
               "by_conclusion": dict(Counter(str(r["conclusion"] or r["status"]) for r in group).most_common()),
               "by_event": dict(Counter(str(r["event"]) for r in group).most_common()),
               "retried_runs": sum(int(r["run_attempt"] or 1) > 1 for r in group),
               "measured_runs": 0, "measured_seconds": 0, "mean_seconds_per_run": None,
               "energy_wh_per_run": None, "estimated_seconds": None, "estimated_energy_wh": None}
        if stats:
            mean = statistics.fmean(stats["seconds"])
            # Energy is only averaged when every measured run of this workflow ran on runners with published hardware.
            wh_mean = None if any(w is None for w in stats["wh"]) else _mean_range(stats["wh"])
            row.update(measured_runs=len(stats["seconds"]), measured_seconds=sum(stats["seconds"]),
                       mean_seconds_per_run=round(mean, 1), energy_wh_per_run=_r(wh_mean),
                       estimated_seconds=round(mean * len(executed)),
                       estimated_energy_wh=_r(_scale(wh_mean, len(executed))))
            est_seconds += mean * len(executed)
            est_runs += len(executed)
            if wh_mean is None:
                energy_complete = False
            else:
                est_wh = _add(est_wh, _scale(wh_mean, len(executed)))
        elif not executed:  # every run waited for approval: nothing ran, which is known without measuring
            row.update(estimated_seconds=0, estimated_energy_wh=[0.0, 0.0])
        else:
            unsampled_runs += len(executed)
        workflows.append(row)

    executed_total = sum(w["executed_runs"] for w in workflows)
    measured = {"runs": len(measured_runs), "jobs": totals["jobs"], "runner_seconds": totals["seconds"],
                "energy_covered_seconds": totals["energy_seconds"], "energy_wh": _r(totals["wh"]),
                "co2e_g": _r(co2e_g(totals["wh"])),
                "seconds_by_runner": dict(totals["by_runner"].most_common()),
                "jobs_not_counted": dict(totals["excluded"]),
                "span": [min((r["created_at"] for r in measured_runs), default=None),
                         max((r["created_at"] for r in measured_runs), default=None)]}
    estimate = {"runner_seconds": round(est_seconds), "runs": est_runs, "runs_total": executed_total,
                "runs_without_sample": unsampled_runs, "measured_runs": len(measured_runs),
                "energy_wh": _r(est_wh), "co2e_g": _r(co2e_g(est_wh)),
                "energy_complete": est_wh is not None and energy_complete,
                "method": ("per workflow: mean measured runner time per run x that workflow's completed runs in the "
                           "window (runs still waiting for approval ran no jobs and count as zero); workflows with no "
                           "measured run are not estimated")}
    limitations = [f"Job timings were fetched for {len(measured_runs)} of {executed_total} completed runs "
                   f"(unauthenticated budget: at most {MAX_JOB_REQUESTS} job requests per scan); per-workflow figures "
                   "rest on the number of measured runs shown next to them."]
    if not history["complete"]:
        limitations.append(f"{total} runs in the last {WINDOW_DAYS} days; the newest {len(runs)} created after "
                           f"{history['window_start']} are listed and the window starts there.")
    if history.get("malformed_runs"):
        limitations.append(f"{history['malformed_runs']} run record(s) without an id or creation time were ignored.")
    if history["skipped"]:
        reasons = Counter(history["skipped"].values())
        limitations += [f"{count} run(s) not measured: {reason}." for reason, count in reasons.items()]
    if history.get("budget_note"):
        limitations.append(f"{history['budget_note'][0].upper()}{history['budget_note'][1:]}.")

    if totals["jobs"]:
        status, reason = "measured", None
    elif history.get("budget_note") and not sampled:
        status, reason = "runs_only", history["budget_note"]
    elif sampled:
        status, reason = "runs_only", "the measured runs ran no jobs (skipped or failed before starting), so no runner time was spent in them"
    else:
        status, reason = "runs_only", next(iter(history["skipped"].values()), "no job timings could be fetched")
    return {**base, "status": status, "reason": reason, "workflows": workflows, "measured": measured,
            "estimate": estimate, "measured_runs": measured_runs,
            "finding_links": link_findings(findings, files, history, by_run, workflows),
            "limitations": limitations}


def model_block():
    return {"energy_formula": ENERGY_FORMULA, "co2e_formula": CO2E_FORMULA, "grid_assumption": GRID_ASSUMPTION,
            "factors": FACTORS, "runner_specs_source": RUNNERS_SOURCE, "exclusions": EXCLUSIONS,
            "runner_specs": {label: dict(spec) for label, spec in RUNNER_SPECS.items()}}


# ---------- findings -> measured steps ----------

def _expected_job_name(job):
    """The name GitHub shows for a YAML job (before any matrix suffix), or None when it cannot be known exactly."""
    if job.is_reusable_call:
        return None  # GitHub names these "<caller> / <called job>"
    name = job.data.get("name")
    if name is None:
        return job.id
    return name if isinstance(name, str) and not EXPRESSION.search(name) else None


def _matches(api_name, expected):
    """GitHub's name for a run of this job: exactly the name, or the name plus a matrix suffix. A " / " marks a job of a
    called reusable workflow ("<caller> / <job>"), which never belongs to a plain job, whatever its caller is named."""
    api_name = api_name or ""
    if " / " in api_name and " / " not in expected:
        return False
    return api_name == expected or api_name.startswith(f"{expected} (")


def _expected_step_name(step):
    name = step.data.get("name")
    if name is not None:
        return name if isinstance(name, str) and not EXPRESSION.search(name) else None
    uses = step.data.get("uses")
    return f"Run {uses}" if isinstance(uses, str) and not EXPRESSION.search(uses) else None


def _load_workflow(path, text):
    try:
        from scanner.core import REPO_ROOT, import_path
        import_path(REPO_ROOT / "detectors" / "owner-c")
        from owner_c.ci.workflow import Workflow
        return Workflow(path, text)
    except Exception:  # unparseable YAML or no owner C parser: no exact match, nothing claimed
        return None


def _within(span, line):
    return bool(span) and span[0] <= line <= span[1]


def _no_runs_note(workflow):
    try:
        called = workflow is not None and "workflow_call" in workflow.triggers()
    except Exception:
        called = False
    if called:
        return ("this workflow runs when another workflow calls it; GitHub lists that time under the calling "
                "workflow, so it is not attributed here")
    return "this workflow has no runs in the window"


def link_findings(findings, files, history, by_run, workflows):
    """finding id -> the measured context of the workflow, job and step the finding cites (exact matches only)."""
    texts = dict(files)
    rows = {w["path"]: w for w in workflows}
    links, parsed = {}, {}
    for finding in findings:
        path, line = finding.get("file"), finding.get("line")
        if not str(finding.get("check_id", "")).startswith("CI-") or not path:
            continue
        if path not in parsed:
            parsed[path] = _load_workflow(path, texts[path]) if path in texts else None
        workflow = parsed[path]
        if path not in rows:
            links[finding["id"]] = {"workflow": path, "runs": 0, "executed_runs": 0, "measured_runs": 0,
                                    "mean_seconds_per_run": None, "match": "none", "job": None, "step": None,
                                    "step_in_window": None, "note": _no_runs_note(workflow)}
            continue
        row = rows[path]
        link = {"workflow": path, "runs": row["runs"], "executed_runs": row["executed_runs"],
                "measured_runs": row["measured_runs"], "mean_seconds_per_run": row["mean_seconds_per_run"],
                "match": "workflow", "job": None, "step": None, "step_in_window": None, "note": None}
        links[finding["id"]] = link
        if not row["executed_runs"]:
            link["note"] = "every run of this workflow in the window was waiting for a maintainer's approval, so no job ran"
            continue
        if not row["measured_runs"]:
            link["note"] = "none of this workflow's runs were in the measured sample"
            continue
        if workflow is None or not line:
            link["note"] = "the workflow file could not be read at the cited line, so only the workflow is linked"
            continue
        yaml_jobs = [job for job in workflow.jobs() if _within(job.span, line)]
        if len(yaml_jobs) != 1:
            link["note"] = "the cited line is not inside one job, so only the workflow is linked"
            continue
        job = yaml_jobs[0]
        expected = _expected_job_name(job)
        if expected is None:
            link["note"] = f"job '{job.id}' has a computed or reusable-workflow name, so GitHub's job name cannot be matched exactly"
            continue
        siblings = [j for j in workflow.jobs() if j.id != job.id]
        if any(_expected_job_name(j) is None and not j.is_reusable_call for j in siblings):
            # A computed name such as "test (${{ matrix.os }})" could render as "test (...)" and pass for this job.
            link["note"] = "another job in this workflow has a computed name, so GitHub's job names cannot be told apart exactly"
            continue
        others = [n for n in (_expected_job_name(j) for j in siblings) if n]
        if expected in others:
            link["note"] = f"two jobs in this workflow are named '{expected}', so the job cannot be matched exactly"
            continue
        step = next((s for s in job.steps() if _within(s.span, line)), None)
        step_name = _expected_step_name(step) if step else None
        if step_name and sum(_expected_step_name(s) == step_name for s in job.steps()) != 1:
            step_name = None
        job_secs, step_secs, step_wh, executions, step_exact, energy_known = [], [], [], 0, True, True
        for rid in row_runs(history, path, by_run):
            j_total, s_total, s_wh = 0, 0, (0.0, 0.0)
            for api_job in history["jobs"][rid]:
                name = api_job.get("name")
                # Another YAML job whose name also matches (e.g. "test" and a literal "test (lint)") makes it ambiguous.
                if not counted(api_job)[0] or not _matches(name, expected) or any(_matches(name, o) for o in others):
                    continue
                if api_job.get("conclusion") == "skipped" and not api_job["steps"]:
                    continue  # a job skipped by its `if:` ran nothing
                executions += 1
                j_total += _seconds(api_job["started_at"], api_job["completed_at"]) or 0
                if not step_name:
                    continue
                hits = [s for s in api_job["steps"] if s.get("name") == step_name]
                secs = hits[0]["seconds"] if len(hits) == 1 else None
                if secs is None and len(hits) == 1 and hits[0].get("conclusion") == "skipped":
                    secs = 0
                if secs is None:
                    step_exact = False
                    continue
                s_total += secs
                wh = energy_wh(secs, runner_of(api_job)[1])
                if wh is None:
                    energy_known = False
                else:
                    s_wh = _add(s_wh, wh)
            job_secs.append(j_total)
            step_secs.append(s_total)
            step_wh.append(s_wh)
        if not executions:
            link["note"] = f"no job named '{expected}' ran in the measured runs of this workflow"
            continue
        link["match"] = "job"
        link["job"] = {"name": expected, "measured_runs": len(job_secs), "executions": executions,
                       "mean_seconds_per_run": round(statistics.fmean(job_secs), 1)}
        if not step_name:
            link["note"] = ("the cited line is not in a step with a fixed name or `uses`, so only the job is matched"
                            if step else "the cited line is not inside a step, so only the job is matched")
            continue
        if not step_exact:
            link["note"] = f"step '{step_name}' was not found exactly once with timings in every measured job, so only the job is matched"
            continue
        mean = statistics.fmean(step_secs)
        wh_mean = _mean_range(step_wh) if energy_known else None
        link["match"] = "step"
        link["step"] = {"name": step_name, "measured_runs": len(step_secs), "mean_seconds_per_run": round(mean, 1),
                        "energy_wh_per_run": _r(wh_mean)}
        if finding["check_id"] in STEP_TARGETS:
            runs_n = row["executed_runs"]
            link["step_in_window"] = {"targets": STEP_TARGETS[finding["check_id"]], "seconds_per_run": round(mean, 1),
                                      "measured_runs": len(step_secs), "runs": runs_n,
                                      "seconds_in_window": round(mean * runs_n),
                                      "energy_wh_in_window": _r(_scale(wh_mean, runs_n)),
                                      "co2e_g_in_window": _r(co2e_g(_scale(wh_mean, runs_n)))}
    return links


def row_runs(history, path, by_run):
    return [rid for rid in history["jobs"] if rid in by_run and by_run[rid]["path"] == path]
