"""Validate detector payloads and conservatively compare successive results."""

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(Path(__file__).with_name("detector.schema.json").read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
CHECK_IDS = {check["key"] for check in json.loads((ROOT / "docs/taxonomy/checks.json").read_text())}
IDENTITY_FIELDS = ("schema_version", "repository_id", "scan_id", "commit_sha", "check_id", "detector_version", "context", "scope")
METRIC_UNITS = {
    "energy": "kWh", "emissions": "kgCO2e", "cpu_time": "seconds",
    "data_transfer": "bytes", "requests": "count", "tokens": "count", "log_volume": "bytes",
}


class ContractError(ValueError):
    """A payload cannot safely enter the shared findings pipeline."""


def canonical(value):
    """Stable UTF-8 JSON encoding shared with other language implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def _finite(value):
    if isinstance(value, float):
        _require(math.isfinite(value), "Numbers must be finite")
    elif isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


def validate(payload):
    """Validate shape plus invariants JSON Schema cannot express."""
    _finite(payload)
    errors = list(VALIDATOR.iter_errors(payload))
    if errors:
        raise ContractError("Schema validation failed: " + errors[0].message)
    _require(payload["check_id"] in CHECK_IDS, "Unknown taxonomy check_id")
    scope = set(payload["scope"])
    if payload["kind"] == "input":
        sources = payload["sources"]
        _require(len({s["source_id"] for s in sources}) == len(sources), "Duplicate source_id")
        _require(all(s["scope_id"] in scope for s in sources), "Source outside requested scope")
        return

    evaluated = set(payload["coverage"]["evaluated_scope"])
    status = payload["status"]
    _require(evaluated <= scope, "Evaluated scope exceeds requested scope")
    if status == "completed":
        _require(evaluated == scope, "Completed result must evaluate all requested scope")
    elif status == "partial":
        _require(bool(evaluated) and evaluated < scope, "Partial result needs a proper nonempty subset of scope")
    else:
        _require(not evaluated and not payload["findings"] and not payload["measurements"],
                 "Unavailable/error results cannot contain evaluated scope, findings or measurements")
    if status != "completed":
        _require(bool(payload["coverage"]["limitations"]), "Incomplete result must explain why")

    seen = set()
    for finding in payload["findings"]:
        _require(finding["scope_id"] in evaluated, "Finding outside evaluated scope")
        expected = fingerprint(payload["repository_id"], payload["check_id"], finding["scope_id"], finding["identity"])
        _require(finding["fingerprint"] == expected, "Incorrect finding fingerprint")
        _require(expected not in seen, "Duplicate finding fingerprint")
        seen.add(expected)
    measurements = set()
    for measurement in payload["measurements"]:
        _require(METRIC_UNITS[measurement["metric"]] == measurement["unit"], "Metric/unit mismatch")
        try:
            start, end = (datetime.fromisoformat(measurement["window"][key].replace("Z", "+00:00")) for key in ("start", "end"))
        except ValueError as error:
            raise ContractError("Invalid measurement timestamp") from error
        _require(start.tzinfo is not None and end.tzinfo is not None, "Measurement timestamps need time zones")
        _require(start < end, "Measurement window must have start before end")
        key = (measurement["metric"], measurement["allocation_key"], start, end)
        _require(key not in measurements, "Duplicate measurement allocation")
        measurements.add(key)


def validate_pair(detector_input, result):
    """Reject invented citations and results that do not belong to the supplied input."""
    validate(detector_input)
    validate(result)
    _require(detector_input["kind"] == "input" and result["kind"] == "result", "Expected an input/result pair")
    _require(all(canonical(detector_input[key]) == canonical(result[key]) for key in IDENTITY_FIELDS),
             "Input/result identity or context mismatch")
    sources = {source["source_id"]: source for source in detector_input["sources"]}
    _require(set(result["coverage"]["evaluated_scope"]) <= {source["scope_id"] for source in sources.values()},
             "Evaluated scope has no supplied evidence source")
    for finding in result["findings"]:
        for evidence in finding["evidence"]:
            source = sources.get(evidence["source_id"])
            _require(source is not None, "Evidence references an unknown source")
            _require(source["scope_id"] == finding["scope_id"], "Evidence belongs to a different scope")
            _require(source["locator"] == evidence["locator"] and source["kind"] == evidence["kind"], "Evidence locator/kind mismatch")
            if source["kind"] == "static":
                lines = source["content"].splitlines()
                quote = evidence["value"].splitlines()
                start = evidence["line_start"] - 1
                _require(bool(quote) and lines[start:start + len(quote)] == quote, "Evidence does not match source lines")
            else:
                field = evidence["field"]
                _require(field in source["data"] and canonical(source["data"][field]) == canonical(evidence["value"]),
                         "Evidence does not match supplied data field")
    for measurement in result["measurements"]:
        for source_id in measurement["provenance"]["source_ids"]:
            _require(source_id in sources, "Measurement references an unknown source")
            _require(sources[source_id]["scope_id"] in result["coverage"]["evaluated_scope"],
                     "Measurement source was not evaluated")


def compare(previous, current):
    """Compare validated results; absence only means no longer detected under comparable coverage."""
    validate(previous)
    validate(current)
    _require(previous["kind"] == current["kind"] == "result", "Comparison requires results")
    _require(previous["repository_id"] == current["repository_id"] and previous["check_id"] == current["check_id"],
             "Comparison requires the same repository and check")
    compatible = all(canonical(previous[key]) == canonical(current[key])
                     for key in ("schema_version", "detector_version", "context"))
    complete = previous["status"] == current["status"] == "completed"
    same_scope = set(previous["scope"]) == set(current["scope"])
    before = {item["fingerprint"] for item in previous["findings"]}
    after = {item["fingerprint"] for item in current["findings"]}
    comparable = compatible and complete and same_scope
    return {
        "comparable": comparable,
        "reason": "Same detector, context and completed scope" if comparable else "Incomplete coverage or changed detector/context/scope",
        "persisting": sorted(before & after) if compatible else [],
        "new": sorted(after - before) if comparable else [],
        "no_longer_detected": sorted(before - after) if comparable else [],
        "unknown": sorted(before - after) if compatible and not comparable else sorted(before) if not compatible else [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--input", type=Path, help="Also verify result evidence against its input")
    args = parser.parse_args()
    try:
        payload = json.loads(args.payload.read_text())
        if args.input:
            validate_pair(json.loads(args.input.read_text()), payload)
        else:
            validate(payload)
    except (ContractError, OSError, ValueError) as error:
        parser.exit(1, f"Invalid detector payload: {error}\n")
    print("Valid detector payload" + (" and evidence" if args.input else ""))


if __name__ == "__main__":
    main()
