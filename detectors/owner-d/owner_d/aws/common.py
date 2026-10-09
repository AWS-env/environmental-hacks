"""Shared plumbing for the Owner D telemetry Lambdas.

- Region guard: the project deploys and reads in ap-south-1 only (ALLOWED_REGION).
- Read-only clients: the execution role, or a short STS AssumeRole session into a read-only role.
- Bounded pagination and time windows, so every AWS read has a fixed upper cost.
- Contract v1 inputs/results and publishing detector.result.v1 events in the shape the findings-hub
  writer accepts (hub/findings_hub/writer.py): source starting with "owner-", Detail = one contract
  result. Every input/result pair passes shared.contracts validate_pair before it is published; invalid
  results are refused and reported. The writer re-validates every event and keeps rejected ones in its DLQ.

boto3 comes from the Lambda runtime. jsonschema (for shared.contracts) is bundled into the Lambda zip by
scripts/build-owner-d-telemetry.sh.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import math
import os
import re
import time
import uuid

DETAIL_TYPE = "detector.result.v1"  # hub writer: validate(detail), evidence = "unverified"
DEFAULT_REGION = "ap-south-1"
SESSION_NAME = "owner-d-telemetry-reader"
SESSION_SECONDS = 900  # STS minimum; one invocation never needs longer
SESSION_REFRESH_SECONDS = 120
PUT_EVENTS_BATCH = 10  # EventBridge PutEvents limit per call
MAX_DETAIL_BYTES = 240_000  # EventBridge entry limit is 256 KB; the hub stores results up to 300 KB inline
DEFAULT_SCOPE_PER_PAYLOAD = 50
MAX_SCOPE_PER_PAYLOAD = 200
MAX_ERROR_CHARS = 300  # refused-result reports in the response and the log line
ROLE_ARN = re.compile(r"arn:aws(?:-[a-z]+)*:iam::\d{12}:role/[\w+=,.@/-]{1,512}")
COMMIT_SHA = re.compile(r"[a-f0-9]{40}")

_factory = None  # tests: callable(service, region, credentials) -> client
_clients = {}
_sessions = {}
_verified_buses = set()
_sleep = time.sleep
_monotonic = time.monotonic
_now = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731  (tests freeze time)


# ---- region, clients and the read-only role ---------------------------------------------------

def region() -> str:
    """The Lambda's Region, refusing anything but the project Region."""
    allowed = os.environ.get("ALLOWED_REGION", DEFAULT_REGION)
    current = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if current != allowed:
        raise RuntimeError(f"telemetry analyzers run only in {allowed}; this Region is {current!r}")
    return current


def _new_client(service, region_name, credentials=None):
    if _factory is not None:
        return _factory(service, region_name, credentials)
    import boto3  # provided by the Lambda runtime
    from botocore.config import Config

    kwargs = {"region_name": region_name,
              "config": Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30)}
    if credentials:
        kwargs.update(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
                      aws_session_token=credentials["SessionToken"])
    return boto3.client(service, **kwargs)


def readers_for(event: dict) -> "Readers":
    """Readers for an event. An `external_id` in the event wins. Otherwise, when the event targets this
    stack's own read-only role (READONLY_ROLE_ARN), the template-provided READONLY_EXTERNAL_ID is used.
    It is never sent to any other role, so another project's CloudTrail never sees it."""
    role_arn, external_id = event.get("role_arn"), event.get("external_id")
    if external_id is None and role_arn is not None and role_arn == os.environ.get("READONLY_ROLE_ARN"):
        external_id = os.environ.get("READONLY_EXTERNAL_ID") or None
    return Readers(role_arn, external_id)


def own_client(service):
    """Client using the analyzer's execution role (hub publishing, STS)."""
    key = (service, None)
    if key not in _clients:
        _clients[key] = _new_client(service, region())
    return _clients[key]


def _expiry(credentials):
    value = credentials["Expiration"]
    if isinstance(value, str):
        value = parse_time(value)
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


class Readers:
    """Read-only telemetry clients.

    Without role_arn the execution role reads its own project. With role_arn the analyzer assumes that
    role (session `owner-d-telemetry-reader`, 15 minutes) and every read goes through the role, which
    is how a client's read-only role is used. Credentials are cached per role and refreshed before expiry.
    """

    def __init__(self, role_arn=None, external_id=None):
        if role_arn is not None and (not isinstance(role_arn, str) or not ROLE_ARN.fullmatch(role_arn)):
            raise ValueError("role_arn must be an IAM role ARN")
        if external_id is not None and (not isinstance(external_id, str) or not 2 <= len(external_id) <= 1224):
            raise ValueError("external_id must be a string of 2-1224 characters")
        self.role_arn, self.external_id = role_arn, external_id

    @property
    def assumed(self):
        return self.role_arn is not None

    def _credentials(self):
        key = (self.role_arn, self.external_id)
        cached = _sessions.get(key)
        if cached and (_expiry(cached) - _now()).total_seconds() > SESSION_REFRESH_SECONDS:
            return cached
        params = {"RoleArn": self.role_arn, "RoleSessionName": SESSION_NAME, "DurationSeconds": SESSION_SECONDS}
        if self.external_id:
            params["ExternalId"] = self.external_id
        credentials = own_client("sts").assume_role(**params)["Credentials"]
        _sessions[key] = credentials
        return credentials

    def client(self, service):
        if not self.assumed:
            return own_client(service)
        credentials = self._credentials()
        key = (service, self.role_arn, credentials["AccessKeyId"])
        if key not in _clients:
            _clients[key] = _new_client(service, region(), credentials)
        return _clients[key]


# ---- bounds -----------------------------------------------------------------------------------

def bounded_int(value, default, low, high, name):
    """An integer setting within [low, high]; out-of-range values are refused, not clamped."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ValueError(f"{name} must be an integer")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be within [{low}, {high}]")
    return value


def bounded_number(value, default, low, high, name):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be within [{low}, {high}]")
    return value


def paginate(call, *, items_key, max_pages, token_in="NextToken", token_out="NextToken", **params):
    """Call a paginated read API at most max_pages times. Returns (items, truncated)."""
    items, token = [], None
    for _ in range(max_pages):
        kwargs = dict(params)
        if token:
            kwargs[token_in] = token
        page = call(**kwargs)
        items.extend(page.get(items_key) or [])
        token = page.get(token_out)
        if not token:
            return items, False
    return items, True


def parse_time(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        moment = value
    elif isinstance(value, str):
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"expected an ISO 8601 timestamp, got {value!r}")
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_window(cfg, *, lookback_key, default, maximum, unit):
    """(start, end): explicit start/end win over a lookback; the span is capped at `maximum`."""
    end = parse_time(cfg["end"]) if cfg.get("end") else _now()
    if cfg.get("start"):
        start = parse_time(cfg["start"])
    else:
        lookback = bounded_number(cfg.get(lookback_key), default, 0, maximum / unit, lookback_key)
        start = end - lookback * unit
    if not start < end:
        raise ValueError("window start must be before its end")
    if end - start > maximum:
        raise ValueError(f"window is longer than the {maximum} bound")
    return start, end


class Deadline:
    """Time left in this invocation, minus a reserve for evaluation and publishing."""

    def __init__(self, context=None, reserve_seconds=15):
        self._context, self._reserve = context, reserve_seconds

    def remaining(self):
        getter = getattr(self._context, "get_remaining_time_in_millis", None)
        return math.inf if getter is None else getter() / 1000 - self._reserve


# ---- contract payloads ------------------------------------------------------------------------

def read_identity(event: dict):
    repository_id, commit_sha = event.get("repository_id"), event.get("commit_sha", "")
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise ValueError("event needs repository_id")
    if not isinstance(commit_sha, str) or not COMMIT_SHA.fullmatch(commit_sha):
        raise ValueError("event needs a full 40-character lowercase commit_sha")
    scan_id = event.get("scan_id") or str(uuid.uuid4())
    if not isinstance(scan_id, str):
        raise ValueError("scan_id must be a string")
    return repository_id, commit_sha, scan_id


def build_inputs(*, repository_id, commit_sha, scan_id, module, context, scope, sources, chunk):
    """Contract v1 inputs, `chunk` scope items each, so every result fits one EventBridge entry."""
    for i in range(0, len(scope), chunk):
        part = scope[i:i + chunk]
        wanted = set(part)
        yield {
            "schema_version": "1.0",
            "kind": "input",
            "repository_id": repository_id,
            "scan_id": scan_id,
            "commit_sha": commit_sha,
            "check_id": module.CHECK_ID,
            "detector_version": module.DETECTOR_VERSION,
            "context": context,
            "scope": part,
            "sources": [s for s in sources if s.get("scope_id") in wanted],
        }


def _validator():
    """shared.contracts validate_pair. The Lambda zip bundles jsonschema, so a missing package is a broken
    build: fail closed instead of publishing unvalidated results."""
    try:
        from shared.contracts.validation import validate_pair
    except ImportError as exc:
        raise RuntimeError("shared.contracts validation is unavailable (jsonschema missing from the build); "
                           "refusing to evaluate or publish") from exc
    return validate_pair


def add_limitations(result, notes):
    existing = result["coverage"]["limitations"]
    for note in notes:
        if note and note not in existing:
            existing.append(note)
    return result


# ---- findings-hub publishing ------------------------------------------------------------------

def ensure_bus(bus_name):
    """PutEvents to a missing bus succeeds silently (events are dropped), so check that it exists."""
    if bus_name in _verified_buses:
        return
    try:
        own_client("events").describe_event_bus(Name=bus_name)
    except Exception as exc:  # botocore ClientError
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ResourceNotFoundException":
            raise RuntimeError(f"event bus '{bus_name}' does not exist") from exc
        raise
    _verified_buses.add(bus_name)


def event_entries(results, *, bus_name, source):
    entries = []
    for result in results:
        detail = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(detail.encode("utf-8")) > MAX_DETAIL_BYTES:
            raise RuntimeError(f"{result['check_id']} result is too large for one event; lower scope_per_payload")
        entries.append({"Source": source, "DetailType": DETAIL_TYPE, "EventBusName": bus_name, "Detail": detail})
    return entries


def publish_results(results, *, bus_name, source):
    """One event per contract result. Raises if the bus is missing or any entry is rejected.
    An accepted PutEvents does not prove persistence: confirm with `findings_hub.readback`."""
    entries = event_entries(results, bus_name=bus_name, source=source)
    ensure_bus(bus_name)
    events = own_client("events")
    for i in range(0, len(entries), PUT_EVENTS_BATCH):
        response = events.put_events(Entries=entries[i:i + PUT_EVENTS_BATCH])
        if response.get("FailedEntryCount"):
            raise RuntimeError(f"PutEvents failed for {response['FailedEntryCount']} entries")
    return len(entries)


def summarize(results):
    return [{"check_id": r["check_id"], "status": r["status"], "scope": len(r["scope"]),
             "evaluated": len(r["coverage"]["evaluated_scope"]), "findings": len(r["findings"])} for r in results]


def log_summary(summary: dict) -> dict:
    """One JSON line per invocation in CloudWatch Logs. Never includes role ARNs or raw telemetry."""
    print(json.dumps({k: v for k, v in summary.items() if k != "result_payloads"}, default=str))
    return summary


# ---- the analyzer loop ------------------------------------------------------------------------

def _call_collector(collector, event, readers, module, deadline):
    return collector(event, readers, module=module, deadline=deadline)


def run_probe(event, context, *, analyzer, collectors):
    """Collect-only smoke test of the read path (IAM, role, Region): returns counts, publishes nothing."""
    region()
    readers = readers_for(event)
    deadline = Deadline(context)
    requested = event["probe"]
    if not isinstance(requested, list) or not requested or not set(requested) <= set(collectors):
        raise ValueError(f"probe must list sources handled by {analyzer}: {sorted(collectors)}")
    report = {}
    for source in requested:
        raw = _call_collector(collectors[source], event, readers, None, deadline)
        report[source] = raw.get("counts", {}) | {"truncated": raw.get("truncated", False),
                                                  "limitations": raw.get("limitations", [])}
    return log_summary({"analyzer": analyzer, "probe": report, "assumed_role": readers.assumed, "published": False})


def run_analyzer(event, context, *, analyzer, source, collectors):
    """Collect -> normalize (registry) -> evaluate -> validate -> publish, for every registered check
    whose source this analyzer collects. Collection errors raise before anything is published.

    Every input/result pair goes through validate_pair before publishing. A pair that fails is refused:
    it is not published and is listed under "refused" in the response, while valid results are still
    published. A detector that raises is listed under "errors" and makes the invocation fail after the
    valid results are published."""
    from owner_d.aws import registry

    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    if event.get("probe"):
        return run_probe(event, context, analyzer=analyzer, collectors=collectors)
    region()
    repository_id, commit_sha, scan_id = read_identity(event)
    checks = registry.select(collectors, event.get("checks"))
    if not checks:
        raise ValueError(f"no checks are registered for {analyzer} yet (see owner_d/aws/registry.py)")
    validate_pair = _validator()
    readers = readers_for(event)
    deadline = Deadline(context)
    chunk = bounded_int(event.get("scope_per_payload"), DEFAULT_SCOPE_PER_PAYLOAD, 1, MAX_SCOPE_PER_PAYLOAD,
                        "scope_per_payload")
    raw_cache, collection, results, skipped, errors, refused = {}, {}, [], [], [], []
    for check in checks:
        module, normalize = registry.load(check)
        key = (check.source, *registry.collector_key(module))
        if key not in raw_cache:
            raw_cache[key] = _call_collector(collectors[check.source], event, readers, module, deadline)
        raw = raw_cache[key]
        collection[check.check_id] = raw.get("counts", {}) | {"source": check.source,
                                                              "truncated": raw.get("truncated", False)}
        settings = registry.settings(check, module, event)
        normalized = registry.normalize(normalize, raw, settings, check.adapter)
        if not normalized["scope"]:
            skipped.append({"check_id": check.check_id, "reason": "no scope collected",
                            "limitations": raw.get("limitations", []) + normalized["limitations"]})
            continue
        payload_context = dict(settings)
        if raw.get("collection"):
            payload_context["collection"] = raw["collection"]
        notes = raw.get("limitations", []) + normalized["limitations"]
        for payload in build_inputs(repository_id=repository_id, commit_sha=commit_sha, scan_id=scan_id,
                                    module=module, context=payload_context, scope=normalized["scope"],
                                    sources=normalized["sources"], chunk=chunk):
            try:
                result = add_limitations(module.evaluate(payload), notes)
            except Exception as exc:  # detector failure: never publish, report it
                errors.append({"check_id": check.check_id, "error": f"{type(exc).__name__}: {exc}"})
                continue
            try:
                validate_pair(payload, result)
            except Exception as exc:  # contract failure: refuse to publish, report it
                # schema messages can quote payload values, so keep the report short
                refused.append({"check_id": check.check_id, "scope": len(payload["scope"]),
                                "status": result.get("status"),
                                "error": f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]})
                continue
            results.append(result)

    bus = os.environ.get("FINDINGS_BUS_NAME")
    dry_run = bool(event.get("dry_run"))
    published = 0
    if results and bus and not dry_run:
        published = publish_results(results, bus_name=bus, source=source)
    summary = {"analyzer": analyzer, "scan_id": scan_id, "dry_run": dry_run, "published": published,
               "assumed_role": readers.assumed, "contract_validated": True,
               "results": summarize(results), "collection": collection, "skipped": skipped, "refused": refused,
               "errors": errors}
    if dry_run:
        summary["result_payloads"] = results
    log_summary(summary)
    if errors:
        raise RuntimeError(f"{len(errors)} result(s) failed evaluation and were not published: "
                           + "; ".join(e["error"] for e in errors))
    return summary


def accepts_settings(fn):
    try:
        return "settings" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
