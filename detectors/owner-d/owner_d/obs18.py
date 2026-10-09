"""OBS-18: inconsistent log field names and over-structured log payloads.

Detector semantics version 1.0.0. Reads the literal field names that Python code writes to
structured logs (log-call keywords, `extra=`, logger `bind`/`append_keys`, `json.dumps({...})`
log payloads) and flags: one field concept written under more spellings than
`context.max_field_spellings` across the scanned project (case/separator drift and a short
synonym list); single calls writing more literal fields than `context.max_fields_per_event`;
and whole objects (`vars(x)`, `x.__dict__`, `locals()`) dumped as fields. Python only;
static only.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace

from . import static
from .logcalls import _is_logger, log_call, logger_variables
from .obs04 import STDLIB_LOG_KWARGS, exempt_reason, in_main_block, is_lambda_module
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "OBS-18"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-18", "OBS18")
SETTING_KEYS = ("max_field_spellings", "max_fields_per_event")

# Calls that attach fields to every later record of a logger (Powertools, structlog, loguru).
BIND_METHODS = {"append_keys", "thread_safe_append_keys", "append_context_keys", "bind", "new"}
BIND_FUNCTIONS = ("bind_contextvars",)
# Normalised spellings of one concept. Only abbreviations or plain synonyms, never other units.
SYNONYMS = {
    "user_id": ("usr_id", "userid"),  # not `uid`: often a generic unique id
    "request_id": ("req_id", "reqid", "requestid"),
    "correlation_id": ("corr_id", "correlationid"),
    "session_id": ("sess_id", "sessionid"),
    "customer_id": ("cust_id", "customerid"),
    "account_id": ("acct_id", "acc_id"),
    "status_code": ("http_status", "http_status_code", "http_code", "statuscode"),
    "error_message": ("error_msg", "err_msg", "errmsg", "err_message"),
    "duration_ms": ("elapsed_ms", "latency_ms", "took_ms"),
    "duration_s": ("elapsed_s", "latency_s", "duration_seconds", "elapsed_seconds", "latency_seconds"),
    "duration": ("elapsed", "elapsed_time", "latency"),
}
CONCEPTS = {alias: concept for concept, aliases in SYNONYMS.items() for alias in aliases}
SNAKE = re.compile(r"[a-z0-9]+(_[a-z0-9]+)*")
DUMP_FUNCTIONS = ("vars", "locals")
EMF_KEY = "_aws"

REFERENCES = (
    "https://opentelemetry.io/docs/specs/semconv/general/naming/",
    "https://opentelemetry.io/docs/specs/otel/logs/data-model/",
    "https://opentelemetry.io/docs/specs/otel/common/",
    "https://www.elastic.co/guide/en/ecs/current/ecs-guidelines.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData-discoverable-fields.html",
    "https://docs.aws.amazon.com/powertools/python/latest/core/logger/",
    "https://dev.to/anderson_leite/rethinking-observability-costs-how-structured-logging-can-save-you-thousands-54bh",
)
RECOMMENDATION = (
    "Pick one name per field and use it everywhere: lower snake_case or dotted names as in the "
    "OpenTelemetry semantic conventions / Elastic Common Schema (user.id, http.response.status_code, "
    "event.duration), without abbreviations. Log the few fields queries filter or aggregate on, not whole "
    "objects: map vars()/__dict__ dumps to named fields, and keep per-event fields well below the "
    "200 fields CloudWatch Logs Insights discovers per JSON event."
)
LIMITATION = (
    "Static pattern only: OBS-18 reads literal field names in Python logging code, not real log events, "
    "formatters or Logs Insights queries. Not counted: keys built at runtime, non-literal extra=, fields "
    "added by formatters/processors, log configuration outside Python and other languages. Drift is judged "
    "over the whole payload, so two services in one repository with different conventions are reported. "
    "Field counts are per call and are lower bounds (** spreads count 0); persistent append_keys/bind "
    "fields are not added to later calls."
)


@dataclass(frozen=True)
class Key:
    spelling: str  # dotted for nested dict literals, e.g. "user.id"
    node: ast.AST  # the key constant or keyword; its line is the evidence


@dataclass(frozen=True)
class Site:
    node: ast.Call
    label: str  # e.g. "logger.info", "logger.append_keys", "print"
    binds: bool  # adds the fields to every later record
    keys: tuple
    dumps: tuple  # (node, inside json.dumps) pairs


@dataclass(frozen=True)
class Drift:
    reference: str
    spellings: dict  # spelling -> (uses, files)


def normalise(spelling):
    """`userId`, `user-id`, `User.ID` -> `user_id`."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spelling)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


def concept_of(spelling):
    norm = normalise(spelling)
    return CONCEPTS.get(norm, norm)


# --- field sites ---------------------------------------------------------------------------

def _is_dump(node):
    """An object's whole, schema-less attribute dict: `vars(x)`, `locals()` or `x.__dict__`.

    Typed records (`asdict(x)`, `x._asdict()`, `x.model_dump()`) have declared fields and are not dumps.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in DUMP_FUNCTIONS


def _is_json_dumps(ctx, node):
    return isinstance(node, ast.Call) and (ctx.dotted(node.func) or "").endswith("json.dumps") and len(node.args) >= 1


def _is_emf(node):
    return isinstance(node, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == EMF_KEY for k in node.keys
    )


def _is_dict_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" and not node.args


class _Collector:
    def __init__(self, ctx):
        self.ctx, self.keys, self.dumps = ctx, [], []

    def value(self, node, name, key_node, in_json):
        """A field value: nested dict literals become dotted leaves."""
        if isinstance(node, ast.Dict) or _is_dict_call(node):
            self.payload(node, in_json, prefix=name + ".")
            return
        self.keys.append(Key(name, key_node))
        if _is_dump(node):
            self.dumps.append((node, in_json))

    def payload(self, node, in_json, prefix=""):
        """A field set (also a `**` spread): a dict literal, dict(k=...), or a whole-object dump."""
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    self.payload(value, in_json, prefix)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.value(value, prefix + key.value, key, in_json)
        elif _is_dict_call(node):
            self.keywords(node.keywords, in_json, prefix=prefix)
        elif _is_dump(node):
            self.dumps.append((node, in_json))

    def keywords(self, keywords, in_json, prefix="", skip=frozenset()):
        for kw in keywords:
            if kw.arg is None:
                self.payload(kw.value, in_json, prefix)
            elif kw.arg not in skip:
                self.value(kw.value, prefix + kw.arg, kw, in_json)

    def json_payloads(self, args):
        for arg in args:
            if _is_json_dumps(self.ctx, arg) and not _is_emf(arg.args[0]):
                self.payload(arg.args[0], in_json=True)


def _site(ctx, node, known, lam):
    collector = _Collector(ctx)
    found = log_call(ctx, node, known)
    if found is not None:
        label, binds = f"{found.receiver}.{found.method}", False
        collector.keywords(node.keywords, False, skip=STDLIB_LOG_KWARGS)
        for kw in node.keywords:
            if kw.arg == "extra":
                collector.payload(kw.value, False)
        collector.json_payloads(node.args + [kw.value for kw in node.keywords if kw.arg == "msg"])
    elif (isinstance(node.func, ast.Attribute) and node.func.attr in BIND_METHODS
          and _is_logger(ctx, node.func.value, known)):
        label, binds = f"{ast.unparse(node.func.value)}.{node.func.attr}", True
        collector.keywords(node.keywords, False)
    elif (ctx.dotted(node.func) or "").rsplit(".", 1)[-1] in BIND_FUNCTIONS:
        label, binds = ast.unparse(node.func), True
        collector.keywords(node.keywords, False)
    elif lam and isinstance(node.func, ast.Name) and node.func.id == "print":
        label, binds = "print", False
        collector.json_payloads(node.args)
    else:
        return None
    if not (collector.keys or collector.dumps):
        return None
    return Site(node, label, binds, tuple(collector.keys), tuple(collector.dumps))


def field_sites(ctx):
    """Structured log calls of a module that is judged, in source order."""
    if exempt_reason(ctx.path, ctx.tree) is not None:
        return []
    known = logger_variables(ctx)
    lam = is_lambda_module(ctx.tree)
    sites = []
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call) and not in_main_block(ctx, node):
            site = _site(ctx, node, known, lam)
            if site is not None:
                sites.append(site)
    sites.sort(key=lambda s: (s.node.lineno, s.node.col_offset))
    return sites


def drift_keys(ctx, sites):
    """Keys that count toward drift: not on a `# noqa` line."""
    return [
        key for site in sites for key in site.keys
        if not static.is_noqa(NOQA, ctx.lines[key.node.lineno - 1])
    ]


# --- project-wide spellings ----------------------------------------------------------------

def project_drift(payload, settings):
    """concept -> Drift for every concept written under too many spellings in the payload."""
    uses = defaultdict(lambda: defaultdict(lambda: [0, set()]))
    scope = payload.get("scope") if isinstance(payload, dict) else None
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(scope, list) or not isinstance(sources, list):
        return {}
    for source in sources:
        if not (isinstance(source, dict) and source.get("kind") == "static" and source.get("scope_id") in scope):
            continue
        locator, content = source.get("locator"), source.get("content")
        if not (isinstance(locator, str) and isinstance(content, str) and locator.endswith(".py")):
            continue
        try:
            ctx = static.Ctx(locator, content)
        except (SyntaxError, ValueError):
            continue  # the runner reports the parse failure; it contributes no spellings
        for key in drift_keys(ctx, field_sites(ctx)):
            concept = concept_of(key.spelling)
            if not concept:
                continue  # punctuation-only keys name no concept
            entry = uses[concept][key.spelling]
            entry[0] += 1
            entry[1].add(locator)
    drift = {}
    for concept, spellings in uses.items():
        if len(spellings) <= settings["max_field_spellings"]:
            continue
        reference = min(spellings, key=lambda s: (-spellings[s][0], not SNAKE.fullmatch(s), s))
        drift[concept] = Drift(reference, {s: (n, len(files)) for s, (n, files) in spellings.items()})
    return drift


# --- per-module rule -----------------------------------------------------------------------

def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _drift_summary(concept, drift, local):
    ordered = sorted(drift.spellings, key=lambda s: (s != drift.reference, -drift.spellings[s][0], s))
    listed = ", ".join(
        f"`{s}` ({_plural(drift.spellings[s][0], 'use')} in {_plural(drift.spellings[s][1], 'file')})"
        for s in ordered
    )
    return (
        f"The log field `{concept}` is written under {len(drift.spellings)} spellings in the scanned code: "
        f"{listed}. This file uses {', '.join(f'`{s}`' for s in local)} instead of `{drift.reference}`; "
        "CloudWatch Logs Insights and other backends index each spelling as a separate field, so filters, "
        "stats and field indexes on one name miss the events logged under the others."
    )


def _drift_hits(ctx, sites, drift):
    found = defaultdict(list)
    for key in drift_keys(ctx, sites):
        concept = concept_of(key.spelling)
        if concept in drift and key.spelling != drift[concept].reference:
            found[concept].append(key)
    hits = []
    for concept, keys in found.items():
        local = list(dict.fromkeys(k.spelling for k in keys))
        reference = normalise(drift[concept].reference)
        hits.append(Hit(
            node=keys[0].node,
            anchor=f"drift:{concept}",
            summary=_drift_summary(concept, drift[concept], local),
            confidence="medium" if any(normalise(s) == reference for s in local) else "low",
        ))
    return hits


def _wide_hit(ctx, site, limit):
    count = len(site.keys)
    if count <= limit:
        return None
    effect = f"adds {count} fields to every later log record" if site.binds else (
        f"writes {count} structured fields in one log event"
    )
    return Hit(
        node=site.node,
        anchor=f"wide:{ctx.qualname(site.node)}:{site.label}",
        summary=(
            f"{site.label}() {effect} (more than {limit}; literal fields only, so a lower "
            "bound). Every field is stored and scanned with each event; CloudWatch Logs Insights discovers at "
            "most 200 fields per JSON event and OpenTelemetry SDKs keep 128 attributes per record by default."
        ),
        confidence="medium",
    )


def _dump_hit(ctx, site):
    if not site.dumps:
        return None
    shown = ", ".join(dict.fromkeys(ast.unparse(node) for node, _ in site.dumps))
    in_json = all(flag for _, flag in site.dumps)
    return Hit(
        node=site.node,
        anchor=f"dump:{ctx.qualname(site.node)}:{site.label}",
        summary=(
            f"{site.label}() logs whole objects as structured fields ({shown}); the number and names of the "
            "fields follow the objects' attributes rather than a log schema, so every attribute is stored, "
            "indexed and scanned with each event and new attributes become new fields."
        ),
        confidence="low" if in_json else "medium",
    )


def run(ctx, settings, drift=None):
    if settings is None:
        return []
    sites = field_sites(ctx)
    hits = _drift_hits(ctx, sites, drift or {})
    for site in sites:
        hits.extend(h for h in (_wide_hit(ctx, site, settings["max_fields_per_event"]), _dump_hit(ctx, site)) if h)
    return hits


def _read_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    for key in SETTING_KEYS:
        value = context[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None, f"context.{key} must be a positive integer"
    return {key: context[key] for key in SETTING_KEYS}, None


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    context = payload.get("context") if isinstance(payload, dict) else None
    settings, reason = _read_settings(context)
    drift = project_drift(payload, settings) if settings is not None else {}
    module = sys.modules[__name__]
    check = SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, run=lambda ctx: module.run(ctx, settings, drift),
    )
    result = static.evaluate_static(payload, check)
    if settings is None:
        result.update(
            status="unavailable",
            coverage={
                "evaluated_scope": [],
                "limitations": [f"Missing or invalid context settings: {reason}", LIMITATION],
            },
            findings=[],
            measurements=[],
        )
    return result
