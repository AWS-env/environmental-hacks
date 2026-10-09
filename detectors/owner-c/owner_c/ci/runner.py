"""Evaluate one contract v1 input payload for one CI check and return the result payload.

Pure function: workflow YAML is only parsed (never executed) and AWS is never called. Coverage is explicit: a
workflow that cannot be parsed, is not a workflow, or lacks the history a check needs is left out of
`evaluated_scope` with a limitation, never reported as clean.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from owner_c.ci.registry import CHECKS, PROFILER, kind
from owner_c.ci.workflow import NotAWorkflow, Workflow
from owner_c.ci.yamlio import WorkflowParseError
from owner_c.contract import build_result, fingerprint


@lru_cache(maxsize=32)  # at most ~32 MB of source held per invocation (1 MB file cap)
def _parse(path: str, content: str) -> Workflow:
    """A workflow is read-only for the checks, so one parse serves every check of the same file."""
    return Workflow(path, content)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an owner C CI contract input."""


def read_settings(context, spec):
    """Validate the evaluation-affecting settings a check needs. Returns (settings, error)."""
    if not isinstance(context, dict):
        return None, "context must be an object"
    settings = {}
    for name, (low, high) in spec.items():
        if name not in context:
            return None, f"missing required context setting: {name}"
        value = context[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"context.{name} must be a number"
        if value <= low or (high is not None and value > high):
            bound = f"> {low}" if high is None else f"in ({low}, {high}]"
            return None, f"context.{name} must be {bound}"
        settings[name] = value
    return settings, None


def _static_evidence(source, line_start, value):
    return {"source_id": source["source_id"], "kind": "static", "locator": source["locator"],
            "line_start": line_start, "value": value}


def _artifact_evidence(source, field, value):
    return {"source_id": source["source_id"], "kind": "artifact", "locator": source["locator"],
            "field": field, "value": value}


def _finding(payload, module, scope_id, identity, summary, confidence, evidence):
    return {
        "fingerprint": fingerprint(payload["repository_id"], payload["check_id"], scope_id, identity),
        "scope_id": scope_id, "identity": identity, "summary": summary, "confidence": confidence,
        "recommendation": module.RECOMMENDATION, "references": list(module.REFS), "evidence": evidence,
    }


def _unique_anchors(items):
    """Disambiguate repeated anchors within a file: the 2nd occurrence becomes `anchor#2`, and so on."""
    seen = Counter()
    for item in items:
        seen[item["anchor"]] += 1
        item["identity"] = item["anchor"] if seen[item["anchor"]] == 1 else f"{item['anchor']}#{seen[item['anchor']]}"
    return items


def _history_gate(module, data, settings):
    """Validate normalized history data and its sample size. Returns a limitation text or None."""
    problem = module.validate(data)
    if problem:
        return f"malformed artifact data: {problem}; not evaluated"
    runs = data[module.RUNS_FIELD]
    if runs < settings["min_runs"]:
        return f"too few runs recorded ({runs} < min_runs {settings['min_runs']}); not evaluated"
    return None


def evaluate(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("kind") != "input":
        raise EvaluationError("payload must be a contract v1 input")
    module = CHECKS.get(payload.get("check_id"))
    if module is None:
        raise EvaluationError(f"unsupported check_id: {payload.get('check_id')!r}")
    if payload.get("detector_version") != module.DETECTOR_VERSION:
        raise EvaluationError(
            f"{module.KEY} detector supports version {module.DETECTOR_VERSION}, got {payload.get('detector_version')!r}")

    style = kind(module)
    settings, problem = read_settings(payload.get("context"), module.SETTINGS)
    if problem:
        return build_result(payload, "unavailable", [], [problem, module.LIMITATION], [])

    statics, artifacts = {}, {}
    for source in payload.get("sources", []):
        if source["kind"] == "static":
            statics[source["scope_id"]] = source
        elif source["kind"] == "artifact" and source["data"].get("profiler") == PROFILER:
            artifacts[source["scope_id"]] = source

    evaluated, limitations, findings = [], [], []
    for scope_id in payload["scope"]:
        if not scope_id.startswith("file:"):
            limitations.append(f"{scope_id}: unsupported scope item; not evaluated")
            continue
        items = []  # dicts: anchor, line, summary, confidence, evidence
        try:
            if style == "history":
                artifact = artifacts.get(scope_id)
                if artifact is None:
                    limitations.append(f"{scope_id}: no {PROFILER} artifact covers this file; not evaluated")
                    continue
                gate = _history_gate(module, artifact["data"], settings)
                if gate:
                    limitations.append(f"{scope_id}: {gate}")
                    continue
                for hit in module.evaluate(artifact["data"], settings):
                    items.append({"anchor": hit["anchor"], "line": 0, "summary": hit["summary"],
                                  "confidence": hit["confidence"],
                                  "evidence": [_artifact_evidence(artifact, hit["field"], hit["value"])]})
            else:
                source = statics.get(scope_id)
                if source is None:
                    limitations.append(f"{scope_id}: no workflow source supplied for this scope item; not evaluated")
                    continue
                try:
                    wf = _parse(source["locator"], source["content"])
                except WorkflowParseError as error:
                    limitations.append(f"{scope_id}: could not be parsed ({error}); not evaluated")
                    continue
                except NotAWorkflow:
                    limitations.append(f"{scope_id}: not a GitHub Actions workflow (no jobs mapping); not evaluated")
                    continue
                if style == "static":
                    for hit in module.run(wf, settings):
                        line, text = wf.quote(hit.start, hit.end)
                        items.append({"anchor": hit.anchor, "line": line, "summary": hit.summary,
                                      "confidence": hit.confidence, "evidence": [_static_evidence(source, line, text)]})
                else:  # confirmed: a static candidate that run history confirms
                    artifact = artifacts.get(scope_id)
                    if artifact is None:
                        limitations.append(f"{scope_id}: no {PROFILER} artifact covers this file; not evaluated")
                        continue
                    gate = _history_gate(module, artifact["data"], settings)
                    if gate:
                        limitations.append(f"{scope_id}: {gate}")
                        continue
                    for cand in module.find(wf):
                        hit = module.confirm(cand, artifact["data"], settings)
                        if hit is None:
                            continue
                        line, text = wf.quote(cand.start, cand.end)
                        items.append({"anchor": hit["anchor"], "line": line, "summary": hit["summary"],
                                      "confidence": hit["confidence"],
                                      "evidence": [_static_evidence(source, line, text),
                                                   _artifact_evidence(artifact, hit["field"], hit["value"])]})
        except Exception as error:  # a detector bug must not become a clean claim for this file
            limitations.append(f"{scope_id}: check failed ({type(error).__name__}); not evaluated")
            continue

        evaluated.append(scope_id)
        items.sort(key=lambda i: (i["line"], i["anchor"]))
        for item in _unique_anchors(items):
            findings.append(_finding(payload, module, scope_id, item["identity"], item["summary"],
                                     item["confidence"], item["evidence"]))

    if len(evaluated) == len(payload["scope"]):
        status = "completed"
    elif evaluated:
        status = "partial"
    else:
        status = "unavailable"
    return build_result(payload, status, evaluated, limitations + [module.LIMITATION], findings)
