"""CI-03: flaky jobs: an earlier attempt failed and a later attempt of the same job passed on the same commit."""
from owner_c.ci.history_checks.common import RUNS_DEFAULT, RUNS_SETTING, finding, is_count

KEY = "CI-03"
KIND = "history"
DETECTOR_VERSION = "1.0.0"
RUNS_FIELD = "runs_total"
SETTINGS = {"min_flaky_occurrences": (0, None), **RUNS_SETTING}
DEFAULTS = {"min_flaky_occurrences": 2, **RUNS_DEFAULT}
PREFIX = "fail_then_pass:"
REFS = [
    "https://docs.github.com/en/rest/actions/workflow-jobs",
    "https://docs.trunk.io/flaky-tests/detection/pass-on-retry-monitor",
]
RECOMMENDATION = (
    "Quarantine or fix the job: a failure that passes on retry with the same commit is non-deterministic "
    "(timing, shared state, network); retries only hide it and re-run the whole job.")
LIMITATION = (
    "Needs a client-collected GitHub Actions run history (normalized). Job level only: test-level flakiness needs "
    "test reports and is not claimed. Matrix legs count as separate jobs (a pass in another leg is not recovery). "
    "Infrastructure failures that pass on retry look the same and are not separated.")


def validate(data):
    if not is_count(data.get("runs_total")):
        return "runs_total must be a non-negative integer"
    for key, value in data.items():
        if key.startswith(PREFIX) and not is_count(value):
            return f"{key} must be a non-negative integer"
    return None


def evaluate(data, settings):
    threshold = settings["min_flaky_occurrences"]
    out = []
    for key in sorted(k for k in data if k.startswith(PREFIX)):
        count, job = data[key], key[len(PREFIX):]
        if count >= threshold:
            out.append(finding(
                f"job:{job}:fail-then-pass", key, count,
                f"Job '{job}' failed and then passed on the same commit for {count} commits (threshold "
                f"{threshold}), which points to a non-deterministic job.",
                "high" if count >= 3 * threshold else "medium"))
    return out
