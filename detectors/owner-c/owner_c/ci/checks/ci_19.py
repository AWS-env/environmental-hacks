"""CI-19: workflow artifacts kept for longer than needed (explicit long `retention-days`)."""
from owner_c.ci.workflow import Hit, is_expression

KEY = "CI-19"
DETECTOR_VERSION = "1.0.0"
SETTINGS = {"max_retention_days": (0, None)}
DEFAULTS = {"max_retention_days": 30}
REFS = [
    "https://github.com/actions/upload-artifact#retention-period",
    "https://docs.github.com/en/rest/actions/artifacts",
]
RECOMMENDATION = "Set `retention-days` on `actions/upload-artifact` to what the artifact is needed for (often 1-7 days)."
LIMITATION = (
    "Compliance or audit rules may require long retention (taxonomy: not wasteful when required). "
    "Organization policy sets the default and maximum retention (GitHub docs: default 90 days; public repositories 1-90, private up to 400), and a repository cannot exceed it. "
    "Static pattern only: artifact sizes and the repository's own retention setting are not read. An unset "
    "`retention-days` (the repository default, 90 days unless changed) is deliberately not reported: it is not 'forever' "
    "and organization policy may already shorten it (50 such hits on 5 real repositories were noise; decision D20). "
    "Only an explicit value above the limit is reported, once per job (the first upload step is quoted). IaC lifecycle rules, container registries "
    "and live storage inventory are not evaluated. Expression values are not flagged.")


def _days(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def run(wf, settings):
    hits = []
    for job in wf.jobs():
        long_kept = []  # (step, name, days): one finding per job, so a job with 20 uploads is not 20 findings
        for step in job.steps():
            if step.action != "actions/upload-artifact":
                continue
            inputs = step.inputs
            retention = inputs.get("retention-days")
            if is_expression(retention):
                continue
            name = inputs.get("name") if isinstance(inputs.get("name"), str) else "artifact"
            days = _days(retention)
            if days is not None and days > settings["max_retention_days"]:
                long_kept.append((step, name, days))
        if long_kept:
            step, name, days = long_kept[0]
            more = f" (and {len(long_kept) - 1} more upload step(s))" if len(long_kept) > 1 else ""
            hits.append(Hit(
                f"job:{job.id}:artifact:{name}:retention-long", step.span[0], step.span[1],
                f"Artifact '{name}' in job '{job.id}'{more} is kept for {days} days "
                f"(limit {settings['max_retention_days']}).", "medium"))
    return hits
