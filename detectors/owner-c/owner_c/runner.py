"""Evaluate one contract v1 input payload for one Python check and return the result payload.

Pure function: it never imports or executes the analysed code (source is only parsed with `ast`)
and never calls AWS. Coverage is explicit: a file that cannot be parsed, or that lacks the
required artifact, is left out of `evaluated_scope` with a limitation, never reported as clean.
"""
from __future__ import annotations

from collections import Counter

from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.checks import STATIC_CHECKS
from owner_c.common import Ctx, is_noqa
from owner_c.contract import build_result, fingerprint


class EvaluationError(ValueError):
    """The payload cannot be evaluated as an owner C contract input."""


def check_module(check_id: str):
    module = STATIC_CHECKS.get(check_id) or ARTIFACT_CHECKS.get(check_id)
    if module is None:
        raise EvaluationError(f"unsupported check_id: {check_id!r}")
    return module


def read_settings(context, spec):
    """Validate the evaluation-affecting settings an artifact check needs. Returns (settings, error)."""
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


def _finding(payload, module, scope_id, anchor, summary, confidence, evidence):
    return {
        "fingerprint": fingerprint(payload["repository_id"], payload["check_id"], scope_id, anchor),
        "scope_id": scope_id, "identity": anchor, "summary": summary, "confidence": confidence,
        "recommendation": module.RECOMMENDATION, "references": list(module.REFS), "evidence": evidence,
    }


def _unique_anchors(items):
    """Disambiguate repeated anchors within a file: the 2nd occurrence becomes `anchor#2`, and so on."""
    seen = Counter()
    for item in items:
        seen[item["anchor"]] += 1
        item["identity"] = item["anchor"] if seen[item["anchor"]] == 1 else f"{item['anchor']}#{seen[item['anchor']]}"
    return items


def evaluate(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("kind") != "input":
        raise EvaluationError("payload must be a contract v1 input")
    module = check_module(payload.get("check_id"))
    if payload.get("detector_version") != module.DETECTOR_VERSION:
        raise EvaluationError(
            f"{module.KEY} detector supports version {module.DETECTOR_VERSION}, got {payload.get('detector_version')!r}")

    is_artifact = module.KEY in ARTIFACT_CHECKS
    settings = {}
    if is_artifact:
        settings, problem = read_settings(payload.get("context"), module.SETTINGS)
        if problem:
            return build_result(payload, "unavailable", [], [problem, module.LIMITATION], [])

    statics, artifacts = {}, {}
    for source in payload.get("sources", []):
        if source["kind"] == "static":
            statics[source["scope_id"]] = source
        elif source["kind"] == "artifact" and source["data"].get("profiler") == getattr(module, "PROFILER", None):
            artifacts[source["scope_id"]] = source

    evaluated, limitations, findings = [], [], []
    for scope_id in payload["scope"]:
        source = statics.get(scope_id)
        if not scope_id.startswith("file:") or source is None:
            limitations.append(f"{scope_id}: no Python source supplied for this scope item")
            continue
        path = source["locator"]
        try:
            ctx = Ctx(path, source["content"])
        except (SyntaxError, ValueError) as error:
            limitations.append(f"{scope_id}: could not be parsed ({type(error).__name__}); not evaluated")
            continue

        items = []  # dicts: anchor, line, summary, confidence, evidence
        try:
            if is_artifact:
                artifact = artifacts.get(scope_id)
                if artifact is None:
                    limitations.append(f"{scope_id}: no {module.PROFILER} artifact covers this file; not evaluated")
                    continue
                for cand in module.find(ctx):
                    conf = module.confirm(cand, artifact["data"], settings)
                    if conf is None:
                        continue
                    line, text = ctx.evidence_lines(cand.node)
                    items.append({
                        "anchor": cand.anchor, "line": cand.line, "summary": conf.summary,
                        "confidence": conf.confidence,
                        "evidence": [
                            _static_evidence(source, line, text),
                            {"source_id": artifact["source_id"], "kind": "artifact",
                             "locator": artifact["locator"], "field": conf.field, "value": conf.value},
                        ]})
            else:
                for hit in module.run(ctx):
                    line = hit.node.lineno
                    if line <= len(ctx.lines) and is_noqa(module.NOQA, ctx.lines[line - 1]):
                        continue
                    start, text = ctx.evidence_lines(hit.node)
                    items.append({"anchor": hit.anchor, "line": line, "summary": hit.summary,
                                  "confidence": hit.confidence, "evidence": [_static_evidence(source, start, text)]})
        except Exception as error:  # a detector bug must not become a clean claim for this file
            limitations.append(f"{scope_id}: check failed ({type(error).__name__}); not evaluated")
            continue

        evaluated.append(scope_id)
        items.sort(key=lambda i: i["line"])
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
