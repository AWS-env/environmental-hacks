"""OBS-04: unstructured logs that need query-time parsing.

Detector semantics version 1.0.0. Flags Python modules that write log lines with runtime
values embedded in free text (interpolated logging messages, and `print` in Lambda handler
modules) while no structured logging is visible anywhere in the scanned code. One finding per
module and kind. Python only; static only (sampling real log lines is not part of v1).
"""

from __future__ import annotations

import ast
import re
import sys
import types
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import static
from .logcalls import log_calls
from .obs02 import _concat_operands, eager_kind
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "OBS-04"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-04", "OBS04")

REFERENCES = (
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData-discoverable-fields.html",
    "https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CloudWatchLogs-Field-Indexing.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/python-logging.html",
    "https://docs.powertools.aws.dev/lambda/python/latest/core/logger/",
    "https://opentelemetry.io/docs/specs/otel/logs/data-model/",
    "https://dev.to/anderson_leite/rethinking-observability-costs-how-structured-logging-can-save-you-thousands-54bh",
)
RECOMMENDATION = (
    "Emit JSON log records with the values as separate fields, e.g. a constant message plus "
    "extra={\"order_id\": order_id} with a JSON formatter (python-json-logger), structlog, or the Powertools "
    "for AWS Lambda Logger, or set the Lambda log format to JSON. CloudWatch Logs Insights then discovers the "
    "fields (and can index them) instead of running `parse` over every scanned event. In Lambda, replace "
    "print() with a logger: print output stays plain text even with the JSON log format."
)
LIMITATION = (
    "Static pattern only: OBS-04 does not read real log events, log volume or Logs Insights queries, so it "
    "proves free-text log lines in code, not query cost. Logging configured outside Python (logging.conf, YAML "
    "dictConfig, SAM/Terraform LoggingConfig LogFormat JSON) is not visible, and JSON records whose message "
    "still embeds values are not flagged. print() is only evaluated in Lambda handler modules."
)

# Imports that make a project's log records structured (JSON/logfmt/OTel records).
STRUCTURED_MODULES = (
    "structlog", "pythonjsonlogger", "json_log_formatter", "ecs_logging", "logstash_formatter", "logfmter",
    "aws_lambda_powertools.logging", "opentelemetry.sdk._logs", "opentelemetry._logs",
)
STRUCTURED_NAMES = ("aws_lambda_powertools.Logger",)
STRUCTURED_TEXT = re.compile(r"json_?formatter|jsonlogger|pythonjsonlogger|ecs_logging|structlog", re.I)
JSON_FORMAT = re.compile(r"^\s*\{\s*[\"']|[\"']message[\"']\s*:|[\"']msg[\"']\s*:")
MESSAGE_FIELD = re.compile(r"%\(message\)s|\{message\}")
LOG_FORMAT_KEYS = {"logging_format", "log_format", "logformat"}
STDLIB_LOG_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra", "level", "msg"}
JSON_DUMPS = {"dumps", "dump"}

TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$")
# Installed or copied third-party code, and sample/doc code that is not deployed.
VENDOR_DIRS = {
    "site-packages", "dist-packages", "vendor", "vendored", "_vendor", "third_party", "thirdparty",
    "node_modules", ".venv", "venv", ".aws-sam", "cdk.out",
}
SAMPLE_DIRS = {"examples", "example", "samples", "sample", "docs", "doc"}
SCRIPT_DIRS = {"scripts", "script", "bin", "benchmarks"}
SCRIPT_FILES = {"setup.py", "manage.py", "noxfile.py", "fabfile.py", "conf.py"}
CLI_MODULES = {"argparse", "click", "typer", "fire", "docopt"}
LAMBDA_HANDLER_NAMES = {"lambda_handler", "handler"}
EVENT_PARAMS = {"event", "evt", "_event"}
CONTEXT_PARAMS = {"context", "ctx", "_context", "lambda_context"}


@dataclass(frozen=True)
class ProjectLogging:
    structured: tuple  # (locator, reason) pairs for structured-logging markers
    text_format: tuple  # locators that configure a text formatter outside `__main__`


# --- file classification -------------------------------------------------------------------

def _is_main_guard(node):
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def in_main_block(ctx, node):
    return any(_is_main_guard(ancestor) for ancestor in ctx.ancestors(node))


def is_lambda_module(tree):
    """A module-level function taking `(event, context)`, or `lambda_handler`/`handler(_, context)`."""
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in node.args.posonlyargs + node.args.args]
        if len(params) >= 2 and params[1] in CONTEXT_PARAMS and (
            node.name in LAMBDA_HANDLER_NAMES or params[0] in EVENT_PARAMS
        ):
            return True
    return False


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def is_vendored(locator):
    """Dependencies installed into the repo, including a Lambda layer's `python/` folder."""
    parts = PurePosixPath(locator).parts[:-1]
    if VENDOR_DIRS & set(parts):
        return True
    return any(
        part == "python" and ("layer" in prev.lower() or prev.lower() in ("dependencies", "deps"))
        for prev, part in zip(parts, parts[1:])
    )


def exempt_reason(locator, tree):
    """Why a module's log lines are not judged (vendored, samples, tests, terminal scripts/CLIs), or None."""
    path = PurePosixPath(locator)
    if is_vendored(locator):
        return "vendored"
    if SAMPLE_DIRS & set(path.parts[:-1]):
        return "sample"
    if TEST_PATH.search(locator):
        return "test"
    if is_lambda_module(tree):
        return None
    if path.name in SCRIPT_FILES or SCRIPT_DIRS & set(path.parts[:-1]):
        return "script"
    if any(name.split(".")[0] in CLI_MODULES for name in _imported_modules(tree)):
        return "cli"
    return None


# --- project-wide structured-logging markers -----------------------------------------------

def _str_constants(ctx):
    """String literals, excluding docstrings and other bare string statements."""
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and not isinstance(
            ctx.parent(node), ast.Expr
        ):
            yield node


def _calls_json_dumps(node):
    return any(
        isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in JSON_DUMPS
        for sub in ast.walk(node)
    )


def _is_nonempty(node):
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    return not (isinstance(node, ast.Constant) and node.value is None)


def structured_markers(ctx):
    """Reasons this module makes the project's log records structured, in source order."""
    reasons = []
    for name in _imported_modules(ctx.tree):
        if name in STRUCTURED_NAMES or any(name == m or name.startswith(m + ".") for m in STRUCTURED_MODULES):
            reasons.append(f"imports {name}")
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.ClassDef) and any(
            ast.unparse(base).endswith("Formatter") for base in node.bases
        ) and _calls_json_dumps(node):
            reasons.append(f"JSON formatter class {node.name}")
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in LOG_FORMAT_KEYS and "JSON" in ast.unparse(kw.value).upper():
                    reasons.append(f"Lambda JSON log format ({kw.arg}=)")
                elif kw.arg == "serialize" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    reasons.append("loguru serialize=True")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                        and key.value.lower() in LOG_FORMAT_KEYS and "JSON" in ast.unparse(value).upper()):
                    reasons.append(f"Lambda JSON log format ({key.value})")
    for const in _str_constants(ctx):
        if STRUCTURED_TEXT.search(const.value):
            reasons.append(f"names a structured formatter ({const.value[:60]!r})")
        elif MESSAGE_FIELD.search(const.value) and JSON_FORMAT.search(const.value):
            reasons.append("JSON-shaped log format string")
    for call in log_calls(ctx):
        for kw in call.node.keywords:
            if kw.arg == "extra" and _is_nonempty(kw.value):
                reasons.append(f"{call.receiver}.{call.method}(extra=...) fields")
            elif kw.arg is not None and kw.arg not in STDLIB_LOG_KWARGS:
                reasons.append(f"{call.receiver}.{call.method}({kw.arg}=...) structured fields")
    return list(dict.fromkeys(reasons))


def configures_text_format(ctx):
    """True if a plain-text formatter (`%(message)s`, not JSON-shaped) is set up outside `__main__`."""
    return any(
        MESSAGE_FIELD.search(const.value) and not JSON_FORMAT.search(const.value)
        and not in_main_block(ctx, const)
        for const in _str_constants(ctx)
    )


def project_logging(payload):
    """Collect markers from every parseable Python source of the requested scope."""
    structured, text_format = [], []
    scope = payload.get("scope") if isinstance(payload, dict) else None
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(scope, list) or not isinstance(sources, list):
        return ProjectLogging((), ())
    for source in sources:
        if not (isinstance(source, dict) and source.get("kind") == "static" and source.get("scope_id") in scope):
            continue
        locator, content = source.get("locator"), source.get("content")
        if not (isinstance(locator, str) and isinstance(content, str) and locator.endswith(".py")):
            continue
        try:
            ctx = static.Ctx(locator, content)
        except (SyntaxError, ValueError):
            continue  # the runner reports the parse failure; it contributes no markers
        exempt = exempt_reason(locator, ctx.tree)
        if exempt in ("vendored", "sample"):
            continue  # third-party and sample code configure neither the project's format nor its fields
        structured.extend((locator, reason) for reason in structured_markers(ctx))
        if exempt is None and configures_text_format(ctx):
            text_format.append(locator)
    return ProjectLogging(tuple(structured), tuple(text_format))


# --- per-module rule -----------------------------------------------------------------------

def _embedded_values(node):
    """The runtime values an eagerly built message interpolates."""
    if isinstance(node, ast.JoinedStr):
        return [part.value for part in node.values if isinstance(part, ast.FormattedValue)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        right = node.right
        values = right.elts if isinstance(right, ast.Tuple) else right.values if isinstance(right, ast.Dict) else [right]
        return _embedded_values(node.left) + list(values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):  # "...".format(...)
        return _embedded_values(node.func.value) + list(node.args) + [kw.value for kw in node.keywords]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        operands = _concat_operands(node)
        return [value for op in operands for value in (_embedded_values(op) if isinstance(op, ast.JoinedStr) else [op])]
    return []


def _is_json_dumps(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in JSON_DUMPS


def _is_free_text(value):
    """A runtime value rendered as text. A `json.dumps(...)` fragment is not: Logs Insights discovers the
    first embedded JSON fragment of a Lambda log event."""
    return not isinstance(value, ast.Constant) and not _is_json_dumps(value)


def _value_kind(message, args_after):
    kind = eager_kind(message)
    if kind is not None:
        values = _embedded_values(message)
    elif isinstance(message, ast.Constant) and isinstance(message.value, str):
        kind, values = "%-style arguments", args_after
    else:
        return None
    return kind if any(_is_free_text(value) for value in values) else None


def logging_kind(call):
    """How a logging call embeds runtime values in its message, or None."""
    if call.message is None:
        return None
    index = 2 if call.method == "log" else 1
    return _value_kind(call.message, call.node.args[index:])


def print_kind(node):
    """How a `print()` mixes text with runtime values, or None."""
    args = node.args
    if any(isinstance(a, ast.Starred) for a in args) or not args:
        return None
    if len(args) == 1:
        return _value_kind(args[0], [])
    has_text = any(
        (isinstance(a, ast.Constant) and isinstance(a.value, str)) or isinstance(a, ast.JoinedStr) for a in args
    )
    values = [value for a in args for value in (_embedded_values(a) if isinstance(a, ast.JoinedStr) else [a])]
    return "print arguments" if has_text and any(_is_free_text(v) for v in values) else None


def _is_print(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"


def _hit(kind, found, summary, confidence):
    first = found[0][0]
    kinds = sorted({k for _, k in found})
    return Hit(
        node=first,
        anchor=f"module:{kind}",
        summary=summary.format(count=len(found), kinds=", ".join(kinds)),
        confidence=confidence,
    )


def run(ctx, project=ProjectLogging((), ())):
    if exempt_reason(ctx.path, ctx.tree) is not None:
        return []
    lam = is_lambda_module(ctx.tree)

    def keep(node):
        return not in_main_block(ctx, node) and not static.is_noqa(NOQA, ctx.lines[node.lineno - 1])

    hits = []
    if not project.structured:
        found = sorted(
            ((c.node, k) for c in log_calls(ctx) if (k := logging_kind(c)) and keep(c.node)),
            key=lambda item: (item[0].lineno, item[0].col_offset),
        )
        if found:
            where = "a Lambda handler module (plain-text log format by default)" if lam else "this module"
            text = " A plain-text log format is configured in the project." if project.text_format else ""
            hits.append(_hit(
                "logging", found,
                "{count} logging call(s) in " + where + " embed runtime values in free-text messages "
                "({kinds}) and no structured logging (JSON formatter, structlog, Powertools Logger, extra= "
                "fields) is configured in the scanned code, so CloudWatch Logs Insights must `parse` each "
                "event to filter or aggregate on them." + text,
                "medium" if lam or project.text_format else "low",
            ))
    if lam:
        found = sorted(
            ((n, k) for n in ast.walk(ctx.tree) if _is_print(n) and (k := print_kind(n)) and keep(n)),
            key=lambda item: (item[0].lineno, item[0].col_offset),
        )
        if found:
            hits.append(_hit(
                "print", found,
                "{count} print() call(s) in a Lambda handler module write runtime values as plain text "
                "({kinds}); Lambda sends print output to CloudWatch Logs as plain text even with the JSON log "
                "format, so queries must `parse` it.",
                "medium",
            ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    project = project_logging(payload)
    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, run=lambda ctx: module.run(ctx, project),
    )
    result = static.evaluate_static(payload, check)
    if project.structured:
        locator, reason = project.structured[0]
        more = len({loc for loc, _ in project.structured}) - 1
        others = f" (and {more} other file(s))" if more else ""
        result["coverage"]["limitations"].insert(-1, (
            f"Structured logging is configured in {locator}{others}: {reason}; logging calls were not "
            "flagged in any file (Lambda print() is still evaluated)."
        ))
    return result
