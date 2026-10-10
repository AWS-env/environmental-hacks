"""OBS-15: duplicate/overlapping observability tools ("Frankenstack") — static manifest proxy.

Detector semantics version 1.0.0. Flags one package or deployment unit that ships two or more
observability tools doing the same job:

- dependency manifests (`requirements*.txt`, `requirements/*.txt`, `pyproject.toml`, `Pipfile`,
  `package.json`, `go.mod`) that declare two or more distinct tools of one category
  (tracing/APM agents, error-tracking SDKs, metrics clients, log shippers). Python manifests in
  the same directory are read together as one package;
- Kubernetes pod templates and ECS task definitions that run two or more telemetry agent
  sidecars collecting the same signal (traces, metrics or logs).

Tools and categories come from the explicit tables below. OpenTelemetry bridges are not a second
stack: OTel API and per-library instrumentation packages are not counted (vendor tracers implement
the API), vendor OTel
distributions count as OpenTelemetry, and a Datadog Agent with OTLP ingestion enabled next to an
OTel collector is a bridge. Files are read as text and never installed, executed or resolved.
v1 proves "overlapping tools declared together", not ingest volume, licensing or cost, so no
measurements are emitted. The taxonomy's account-inventory half (OQ-7) is not implemented here.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import miniyaml
from .inf08 import POD_SPEC, _cfn_task_definitions, _ecs_task_definitions, _is_k8s, _k8s_objects
from .miniyaml import Mapping, Scalar, Sequence
from .static import (  # noqa: F401  (EvaluationError is re-exported for the CLI)
    IDENTITY_FIELDS,
    SCHEMA_VERSION,
    SUPPORTED_KIND,
    EvaluationError,
    _require,
    _unique_identities,
    fingerprint,
)
from .textstatic import NotEvaluated, ParseError, Unsupported

CHECK_ID = "OBS-15"
DETECTOR_VERSION = "1.0.0"
NOQA = ("OBS-15", "OBS15")
FORMATS = (
    "dependency manifests (requirements*.txt/.in, requirements/*.txt, pyproject.toml, Pipfile, package.json, "
    "go.mod) and Kubernetes/ECS manifests (.yaml/.yml/.json) that run telemetry agent sidecars"
)

REFERENCES = (
    "https://opentelemetry.io/docs/concepts/distributions/",
    "https://opentelemetry.io/ecosystem/vendors/",
    "https://docs.datadoghq.com/opentelemetry/",
    "https://aws-otel.github.io/docs/introduction",
    "https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/",
    "https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_firelens.html",
)
RECOMMENDATION = (
    "Keep one tool per job for this service. To move between vendors, instrument once with OpenTelemetry and send "
    "OTLP to the backend you keep (most APM vendors ingest OTLP), then remove the other agent. Mark a deliberate, "
    "time-boxed overlap (for example during a migration) with `# noqa: OBS-15`."
)
REC_SIDECARS = (
    "Run one telemetry agent per signal in this pod/task: route traces and metrics through a single collector or "
    "agent (an OpenTelemetry collector can export to most vendors, and the Datadog Agent can ingest OTLP), and keep "
    "one log router. Mark a deliberate overlap with `# noqa: OBS-15` on the image line."
)
LIMITATION = (
    "Static manifest scan only: OBS-15 v1 proves that one package or pod/task declares overlapping observability "
    "tools from a fixed vendor table, not that both are active, how much data each ingests or what they cost, so no "
    "measurements are emitted. Only Python manifests in the same directory are correlated; development/test "
    "dependencies (devDependencies, Pipfile dev-packages, optional extras, Poetry groups, requirements-dev/test/...), "
    "indirect Go modules, -r/-c includes, lockfiles, Maven/Gradle, runtime-attached agents (-javaagent, ddtrace-run "
    "without a dependency), Compose stacks, agents deployed as separate DaemonSets and the client account inventory "
    "(OQ-7) are not seen. Sidecar signals follow each agent's default configuration; configs outside the file are "
    "not visible."
)

TRACING, ERRORS, METRICS, LOGS = "tracing-apm", "error-tracking", "metrics", "log-shipping"
CATEGORIES = {
    TRACING: ("tracing/APM agents", "each one instruments the same requests and exports its own copy of the traces"),
    ERRORS: ("error-tracking SDKs", "each one captures and uploads the same exceptions"),
    METRICS: ("metrics clients", "each one emits the same application metrics to its own backend"),
    LOGS: ("log shippers", "each one ships the same log records to its own backend"),
}
OTEL = "OpenTelemetry"

# (category, tool, package names). A trailing `*` is a prefix. OpenTelemetry counts only with an SDK, distro,
# exporter or zero-code bundle: the API (opentelemetry-api, @opentelemetry/api, go.opentelemetry.io/otel) and
# per-library instrumentations (opentelemetry-instrumentation-*, @opentelemetry/instrumentation-*,
# go.opentelemetry.io/contrib/instrumentation/*) only call the API, which vendor tracers implement as a bridge.
PACKAGES = {
    "pypi": (
        (TRACING, "Datadog APM", ("ddtrace",)),
        (TRACING, "New Relic", ("newrelic",)),
        (TRACING, "Elastic APM", ("elastic-apm",)),
        (TRACING, "AWS X-Ray SDK", ("aws-xray-sdk",)),
        (TRACING, OTEL, ("opentelemetry-sdk", "opentelemetry-distro", "opentelemetry-exporter-*", "aws-opentelemetry-distro",
                         "splunk-opentelemetry", "elastic-opentelemetry", "azure-monitor-opentelemetry",
                         "azure-monitor-opentelemetry-exporter")),
        (TRACING, "AppDynamics", ("appdynamics",)),
        (TRACING, "Instana", ("instana",)),
        (TRACING, "Scout APM", ("scout-apm",)),
        (TRACING, "Honeycomb Beeline", ("honeycomb-beeline",)),
        (TRACING, "Dynatrace OneAgent SDK", ("oneagent-sdk",)),
        (TRACING, "Apache SkyWalking", ("apache-skywalking",)),
        (ERRORS, "Sentry", ("sentry-sdk", "raven")),
        (ERRORS, "Rollbar", ("rollbar",)),
        (ERRORS, "Bugsnag", ("bugsnag",)),
        (ERRORS, "Airbrake", ("pybrake",)),
        (ERRORS, "Honeybadger", ("honeybadger",)),
        (ERRORS, "Raygun", ("raygun4py",)),
        (METRICS, "Prometheus client", ("prometheus-client",)),
        (METRICS, "DogStatsD", ("datadog",)),
        (METRICS, "StatsD", ("statsd",)),
        (METRICS, "CloudWatch EMF", ("aws-embedded-metrics",)),
        (LOGS, "CloudWatch Logs handler", ("watchtower",)),
        (LOGS, "Logstash", ("python-logstash", "python-logstash-async")),
        (LOGS, "Logz.io", ("logzio-python-handler",)),
        (LOGS, "Google Cloud Logging", ("google-cloud-logging",)),
        (LOGS, "Splunk HEC", ("splunk-handler",)),
        (LOGS, "Grafana Loki", ("python-logging-loki", "loki-logger-handler")),
        (LOGS, "Better Stack", ("logtail-python",)),
    ),
    "npm": (
        (TRACING, "Datadog APM", ("dd-trace",)),
        (TRACING, "New Relic", ("newrelic", "@newrelic/*")),
        (TRACING, "Elastic APM", ("elastic-apm-node",)),
        (TRACING, "AWS X-Ray SDK", ("aws-xray-sdk", "aws-xray-sdk-core")),
        (TRACING, OTEL, ("@opentelemetry/sdk-node", "@opentelemetry/sdk-trace-node", "@opentelemetry/sdk-trace-base",
                         "@opentelemetry/sdk-trace-web", "@opentelemetry/auto-instrumentations-node",
                         "@opentelemetry/auto-instrumentations-web", "@opentelemetry/exporter-*",
                         "@aws/aws-distro-opentelemetry-node-autoinstrumentation", "@splunk/otel",
                         "@honeycombio/opentelemetry-node", "@elastic/opentelemetry-node",
                         "@azure/monitor-opentelemetry")),
        (TRACING, "AppDynamics", ("appdynamics",)),
        (TRACING, "Instana", ("@instana/collector", "@instana/aws-lambda", "@instana/aws-fargate")),
        (TRACING, "Dynatrace OneAgent", ("@dynatrace/oneagent", "@dynatrace/oneagent-sdk")),
        (ERRORS, "Sentry", ("@sentry/node", "@sentry/browser", "@sentry/react", "@sentry/nextjs", "@sentry/vue",
                            "@sentry/angular", "@sentry/aws-serverless", "@sentry/serverless", "@sentry/nestjs",
                            "@sentry/remix", "@sentry/sveltekit", "@sentry/bun", "@sentry/react-native", "raven",
                            "raven-js")),
        (ERRORS, "Rollbar", ("rollbar",)),
        (ERRORS, "Bugsnag", ("@bugsnag/js", "@bugsnag/node", "@bugsnag/browser", "@bugsnag/react-native",
                             "@bugsnag/expo")),
        (ERRORS, "Airbrake", ("@airbrake/node", "@airbrake/browser")),
        (ERRORS, "Honeybadger", ("@honeybadger-io/js", "@honeybadger-io/react")),
        (ERRORS, "Raygun", ("raygun", "raygun4js")),
        (ERRORS, "TrackJS", ("trackjs",)),
        (METRICS, "Prometheus client", ("prom-client",)),
        (METRICS, "StatsD", ("hot-shots", "node-statsd", "statsd-client", "lynx")),
        (METRICS, "CloudWatch EMF", ("aws-embedded-metrics",)),
        (METRICS, "Datadog metrics", ("datadog-metrics",)),
        (LOGS, "CloudWatch Logs transport", ("winston-cloudwatch",)),
        (LOGS, "Better Stack", ("@logtail/node", "@logtail/pino", "@logtail/winston")),
        (LOGS, "Elasticsearch transport", ("winston-elasticsearch",)),
        (LOGS, "Logz.io", ("logzio-nodejs",)),
        (LOGS, "Google Cloud Logging", ("@google-cloud/logging-winston", "@google-cloud/logging-bunyan")),
        (LOGS, "Datadog log transport", ("datadog-winston", "pino-datadog-transport")),
        (LOGS, "Grafana Loki", ("winston-loki", "pino-loki")),
        (LOGS, "Splunk HEC", ("splunk-logging",)),
    ),
    "go": (
        (TRACING, "Datadog APM", ("gopkg.in/datadog/dd-trace-go.v1", "github.com/datadog/dd-trace-go/*")),
        (TRACING, "New Relic", ("github.com/newrelic/go-agent", "github.com/newrelic/go-agent/*")),
        (TRACING, "Elastic APM", ("go.elastic.co/apm", "go.elastic.co/apm/*")),
        (TRACING, "AWS X-Ray SDK", ("github.com/aws/aws-xray-sdk-go", "github.com/aws/aws-xray-sdk-go/*")),
        (TRACING, OTEL, ("go.opentelemetry.io/otel/sdk", "go.opentelemetry.io/otel/sdk/*",
                         "go.opentelemetry.io/otel/exporters/*")),
        (TRACING, "Instana", ("github.com/instana/go-sensor",)),
        (ERRORS, "Sentry", ("github.com/getsentry/sentry-go", "github.com/getsentry/sentry-go/*")),
        (ERRORS, "Rollbar", ("github.com/rollbar/rollbar-go",)),
        (ERRORS, "Bugsnag", ("github.com/bugsnag/bugsnag-go", "github.com/bugsnag/bugsnag-go/*")),
        (ERRORS, "Airbrake", ("github.com/airbrake/gobrake", "github.com/airbrake/gobrake/*")),
        (ERRORS, "Honeybadger", ("github.com/honeybadger-io/honeybadger-go",)),
        (METRICS, "Prometheus client", ("github.com/prometheus/client_golang",)),
        (METRICS, "DogStatsD", ("github.com/datadog/datadog-go", "github.com/datadog/datadog-go/*")),
        (METRICS, "StatsD", ("github.com/cactus/go-statsd-client", "github.com/cactus/go-statsd-client/*",
                             "github.com/smira/go-statsd")),
        (METRICS, "CloudWatch EMF", ("github.com/aws/aws-embedded-metrics-golang",)),
    ),
}

TRACES, SIG_METRICS, SIG_LOGS = "traces", "metrics", "logs"
DATADOG_AGENT, COLLECTOR = "Datadog Agent", "OpenTelemetry Collector"
# (tool, image repository suffixes, signals collected with the default configuration). Collectors whose
# pipelines are defined in a separate config count for traces and metrics only: their logs pipelines are
# commonly fed by a log router in the same pod (fluentforward receiver), which is a bridge.
AGENTS = (
    (DATADOG_AGENT, ("datadog/agent", "datadoghq/agent", "datadog/docker-dd-agent"), (TRACES, SIG_METRICS)),
    (COLLECTOR, ("otel/opentelemetry-collector", "otel/opentelemetry-collector-contrib",
                 "otel/opentelemetry-collector-k8s", "opentelemetry-collector-releases/opentelemetry-collector",
                 "opentelemetry-collector-releases/opentelemetry-collector-contrib",
                 "opentelemetry-collector-releases/opentelemetry-collector-k8s", "amazon/aws-otel-collector",
                 "aws-observability/aws-otel-collector", "signalfx/splunk-otel-collector"), (TRACES, SIG_METRICS)),
    ("Grafana Alloy/Agent", ("grafana/alloy", "grafana/agent"), (TRACES, SIG_METRICS)),
    ("AWS X-Ray daemon", ("amazon/aws-xray-daemon", "xray/aws-xray-daemon"), (TRACES,)),
    ("CloudWatch agent", ("amazon/cloudwatch-agent", "cloudwatch-agent/cloudwatch-agent"), (SIG_METRICS,)),
    ("New Relic infrastructure agent", ("newrelic/infrastructure", "newrelic/infrastructure-bundle",
                                        "newrelic/infrastructure-k8s"), (SIG_METRICS,)),
    ("Fluent Bit", ("fluent/fluent-bit", "amazon/aws-for-fluent-bit", "aws-observability/aws-for-fluent-bit"),
     (SIG_LOGS,)),
    ("Fluentd", ("fluent/fluentd", "fluent/fluentd-kubernetes-daemonset"), (SIG_LOGS,)),
    ("Vector", ("timberio/vector",), (SIG_LOGS,)),
    ("Filebeat", ("beats/filebeat", "elastic/filebeat"), (SIG_LOGS,)),
    ("Promtail", ("grafana/promtail",), (SIG_LOGS,)),
    ("Elastic Agent", ("beats/elastic-agent", "elastic/elastic-agent"), (SIG_LOGS, SIG_METRICS)),
)
_AGENT_HINT = re.compile("|".join(re.escape(s) for _, suffixes, _ in AGENTS for s in suffixes), re.I)
DD_OTLP = re.compile(r"^DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_(GRPC|HTTP)_ENDPOINT$")
TRUE = {"true", "1", "yes"}

# Directory names (split on . _ -) that mark tests, examples or docs, and vendored code.
EXCLUDED_TOKENS = frozenset({
    "test", "tests", "testing", "testdata", "e2e", "example", "examples", "sample", "samples", "doc", "docs",
    "fixture", "fixtures", "mock", "mocks", "benchmark", "benchmarks",
})
EXCLUDED_DIRS = frozenset({"node_modules", "vendor", "third_party", "site-packages", ".venv", "venv", ".github"})
# requirements file-name tokens that mark development/test dependency sets.
DEV_REQUIREMENTS = frozenset({
    "dev", "develop", "development", "test", "tests", "testing", "lint", "docs", "doc", "ci", "typing", "mypy",
    "local", "e2e", "bench", "benchmark", "benchmarks", "debug",
})

# Manifest names correlated with the other manifests in their directory (see _group_key).
CANONICAL = frozenset({"requirements.txt", "requirements.in", "pyproject.toml", "pipfile", "package.json", "go.mod"})

_NOQA = re.compile(r"(?:#|//)\s*noqa\b\s*(?::\s*([A-Za-z0-9_, -]+))?", re.I)
_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_TOML_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
_TOML_KEY = re.compile(r"""^\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9._-]+))\s*=""")
_QUOTED = re.compile(r""""((?:[^"\\]|\\.)*)"|'([^']*)'""")


@dataclass(frozen=True)
class Declaration:
    category: str
    tool: str
    name: str  # as written
    line: int
    suppressed: bool


@dataclass
class Ctx:
    locator: str
    lines: list
    kind: str  # "manifest" | "deploy"
    ecosystem: str | None = None
    declarations: list = field(default_factory=list)
    docs: list = field(default_factory=list)


# -- helpers ------------------------------------------------------------------------------------


def _suppressed(lines, line):
    """`# noqa`, `# noqa: OBS-15` or `// noqa: OBS-15` on `line` or the comment lines directly above it."""
    def suppresses(text):
        for match in _NOQA.finditer(text):
            named = match.group(1)
            if named is None or {c.strip().upper() for c in re.split(r"[,\s]+", named)} & set(NOQA):
                return True
        return False

    if 1 <= line <= len(lines) and suppresses(lines[line - 1]):
        return True
    index = line - 2
    while index >= 0 and lines[index].lstrip().startswith(("#", "//")):
        if suppresses(lines[index]):
            return True
        index -= 1
    return False


def _normalize(ecosystem, name):
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def classify(ecosystem, name):
    """(category, tool) for a dependency name, or None when it is not in the table."""
    normalized = _normalize(ecosystem, name)
    for category, tool, names in PACKAGES[ecosystem]:
        for candidate in names:
            if normalized == candidate or (candidate.endswith("*") and normalized.startswith(candidate[:-1])):
                return category, tool
    return None


def _declare(ctx, name, line):
    found = classify(ctx.ecosystem, name)
    if found:
        ctx.declarations.append(Declaration(found[0], found[1], name, line, _suppressed(ctx.lines, line)))


def _not_evaluated(locator):
    parts = [part.lower() for part in re.split(r"[\\/]", locator)]
    for part in parts[:-1]:
        if part in EXCLUDED_DIRS:
            return f"path is vendored/tooling material ({part}), not this service's own manifest"
        marked = sorted(set(re.split(r"[._-]", part)) & EXCLUDED_TOKENS)
        if marked:
            return f"path marks test, example or docs material ({marked[0]}), not a deployed service"
    return None


def manifest_kind(locator):
    """(ecosystem, format) for a supported dependency manifest, else None."""
    path = PurePosixPath(locator.replace("\\", "/").lower())
    name, parent = path.name, path.parent.name
    if name == "package.json":
        return "npm", "package.json"
    if name == "go.mod":
        return "go", "go.mod"
    if name == "pyproject.toml":
        return "pypi", "pyproject"
    if name == "pipfile":
        return "pypi", "pipfile"
    if path.suffix in (".txt", ".in") and ("requirements" in path.stem or parent == "requirements"):
        return "pypi", "requirements"
    return None


# -- dependency manifests -------------------------------------------------------------------------


def _requirements(ctx):
    stem = PurePosixPath(ctx.locator.replace("\\", "/").lower()).stem
    marked = sorted(set(re.split(r"[._-]", stem)) & DEV_REQUIREMENTS)
    if marked:
        raise NotEvaluated(f"requirements file marks a development/test dependency set ({marked[0]})")
    for number, raw in enumerate(ctx.lines, 1):
        text = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip().rstrip("\\").strip()
        if not text or text.startswith("-") or "://" in text.split("@")[0]:
            continue
        match = _PEP508_NAME.match(text)
        if match:
            _declare(ctx, match.group(1), number)


def _close_brackets(text, depth):
    """Bracket depth after `text` (TOML arrays), ignoring quoted strings and comments."""
    stripped = _QUOTED.sub("", text).split("#", 1)[0]
    return depth + stripped.count("[") - stripped.count("]")


def _toml_tables(lines):
    """Yield (line number, current table, line) for every TOML line."""
    table = ""
    for number, line in enumerate(lines, 1):
        header = _TOML_HEADER.match(line)
        if header and not line.lstrip().startswith(("#",)):
            table = re.sub(r"\s*\.\s*", ".", header.group(1)).replace('"', "").replace("'", "")
            yield number, None, line
            continue
        yield number, table, line


def _locate_array_names(lines, table, key):
    """{normalized PEP 508 name: first line} for strings in the `key = [...]` array of `table`."""
    found, depth = {}, 0
    for number, current, line in _toml_tables(lines):
        if current != table:
            depth = 0
            continue
        if depth == 0:
            match = _TOML_KEY.match(line)
            if not match or next(g for g in match.groups() if g) != key:
                continue
            depth = _close_brackets(line.split("=", 1)[1], 0)
            values = _QUOTED.findall(line.split("=", 1)[1])
        else:
            depth = _close_brackets(line, depth)
            values = _QUOTED.findall(line)
        for double, single in values:
            name = _PEP508_NAME.match(double or single)
            if name:
                found.setdefault(_normalize("pypi", name.group(1)), number)
    return found


def _locate_table_keys(lines, table):
    found = {}
    for number, current, line in _toml_tables(lines):
        if current == table:
            match = _TOML_KEY.match(line)
            if match:
                found.setdefault(_normalize("pypi", next(g for g in match.groups() if g)), number)
    return found


def _toml(ctx, fmt):
    try:
        data = tomllib.loads("\n".join(ctx.lines))
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise ParseError(f"invalid TOML: {error}") from None
    wanted = []  # (name, locator table/key)
    if fmt == "pyproject":
        project = data.get("project")
        deps = project.get("dependencies") if isinstance(project, dict) else None
        for spec in deps if isinstance(deps, list) else []:
            match = _PEP508_NAME.match(spec) if isinstance(spec, str) else None
            if match:
                wanted.append((match.group(1), ("array", "project", "dependencies")))
        poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
        deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        for name, spec in (deps.items() if isinstance(deps, dict) else []):
            if name.lower() != "python" and not (isinstance(spec, dict) and spec.get("optional") is True):
                wanted.append((name, ("table", "tool.poetry.dependencies")))
    else:
        deps = data.get("packages")
        for name in (deps if isinstance(deps, dict) else {}):
            wanted.append((name, ("table", "packages")))
    located = {}
    for name, where in wanted:
        if classify("pypi", name) is None:
            continue
        if where not in located:
            located[where] = (_locate_array_names(ctx.lines, where[1], where[2]) if where[0] == "array"
                              else _locate_table_keys(ctx.lines, where[1]))
        line = located[where].get(_normalize("pypi", name))
        if line is None:
            raise ParseError(f"could not locate the declaration of {name!r} (dotted keys or inline tables)")
        _declare(ctx, name, line)


def _package_json(ctx):
    try:
        data = json.loads("\n".join(ctx.lines))
    except ValueError:
        raise ParseError("invalid JSON") from None
    if not isinstance(data, dict):
        raise ParseError("package.json is not a JSON object")
    comments = data.get("//")
    comments = comments if isinstance(comments, list) else [comments]
    if any(isinstance(c, str) and _suppressed(["# " + c], 1) for c in comments):
        return  # file-wide `"//": "noqa: OBS-15"` (package.json has no comment syntax)
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        return
    start = next((n for n, line in enumerate(ctx.lines, 1) if re.search(r'"dependencies"\s*:', line)), None)
    for name in deps:
        if classify("npm", name) is None:
            continue
        key = re.compile(re.escape(json.dumps(name)) + r"\s*:")
        line = next((n for n in range(start or 1, len(ctx.lines) + 1) if key.search(ctx.lines[n - 1])), None)
        if line is None:
            raise ParseError(f"could not locate the declaration of {name!r}")
        _declare(ctx, name, line)


def _go_mod(ctx):
    in_block, module = False, False
    for number, raw in enumerate(ctx.lines, 1):
        code, _, comment = raw.partition("//")
        words = code.split()
        if not words:
            continue
        if in_block:
            if words[0] == ")":
                in_block = False
                continue
            spec = words
        elif words[0] == "module":
            module = True
            continue
        elif words[0] == "require":
            if words[1:2] == ["("]:
                in_block = True
                continue
            spec = words[1:]
        else:
            continue
        if re.search(r"\bindirect\b", comment):
            continue
        if len(spec) != 2:
            raise ParseError(f"unexpected require directive on line {number}")
        _declare(ctx, spec[0].strip('"'), number)
    if not module or in_block:
        raise ParseError("no module directive" if not module else "unterminated require block")


def _manifest_hits(ctx, group):
    """Findings for one manifest: categories where the package (this file plus same-directory
    manifests of the same ecosystem) declares two or more distinct tools."""
    mine = [d for d in ctx.declarations if not d.suppressed]
    for category in CATEGORIES:
        own = [d for d in mine if d.category == category]
        if not own:
            continue
        tools = {d.tool for d in own}
        siblings = {}
        for other in group:
            for d in other.declarations:
                if other is not ctx and not d.suppressed and d.category == category and d.tool not in tools:
                    siblings.setdefault(d.tool, (d.name, other.locator))
        if len(tools) + len(siblings) < 2:
            continue
        label, effect = CATEGORIES[category]
        listed = ", ".join(f"{tool} ({', '.join(sorted({d.name for d in own if d.tool == tool}))})"
                           for tool in sorted(tools))
        summary = f"This package declares {len(tools) + len(siblings)} overlapping {label}: {listed}"
        if siblings:
            summary += "; plus " + ", ".join(f"{tool} ({name}) in sibling manifest {where}"
                                             for tool, (name, where) in sorted(siblings.items()))
        summary += f". {effect.capitalize()}, so the same data is collected, shipped and paid for more than once."
        own_overlap = len(tools) >= 2 and category in (TRACING, ERRORS)
        yield category, sorted({d.line for d in own}), summary, "medium" if own_overlap else "low", RECOMMENDATION


# -- sidecar agents -------------------------------------------------------------------------------


def _text(node):
    return node.value if isinstance(node, Scalar) else None


def _get(node, *path):
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.items.get(key)
    return node


def agent_for(image):
    """(tool, signals) for a telemetry agent image reference, else None."""
    ref = (image or "").strip().lower().split("@", 1)[0]
    head, _, last = ref.rpartition("/")
    repo = f"{head}/{last.split(':', 1)[0]}" if head else last.split(":", 1)[0]
    for tool, suffixes, signals in AGENTS:
        if any(repo == suffix or repo.endswith("/" + suffix) for suffix in suffixes):
            return tool, set(signals)
    return None


def _pod_units(docs):
    """(owner, [container Mapping], key function) for every pod template / task definition."""
    lower, upper = (lambda k: k), (lambda k: k[0].upper() + k[1:])
    for doc in _k8s_objects(docs):
        kind = _text(doc.get("kind"))
        if kind not in POD_SPEC:
            continue
        spec = _get(doc, *POD_SPEC[kind])
        containers = [c for c in getattr(_get(spec, "containers"), "items", []) if isinstance(c, Mapping)]
        # Native sidecars: init containers with restartPolicy Always keep running next to the app.
        containers += [c for c in getattr(_get(spec, "initContainers"), "items", [])
                       if isinstance(c, Mapping) and _text(c.get("restartPolicy")) == "Always"]
        namespace = _text(_get(doc, "metadata", "namespace"))
        name = _text(_get(doc, "metadata", "name")) or "unnamed"
        yield f"{kind}/{namespace + '/' if namespace else ''}{name}", containers, lower
    for doc in docs:
        if _is_k8s(doc):
            continue
        for family, task, style in (_ecs_task_definitions(doc) or []) + (_cfn_task_definitions(doc) or []):
            key = lower if style == "ecs" else upper
            containers = _get(task, key("containerDefinitions"))
            owner = f"task/{family}" if style == "ecs" else f"{family}"
            yield owner, [c for c in getattr(containers, "items", []) if isinstance(c, Mapping)], key


def _container_env(container, key):
    entries = _get(container, "env") if _get(container, "env") is not None else _get(container, key("environment"))
    env = {}
    for item in entries.items if isinstance(entries, Sequence) else ():
        name = _text(_get(item, key("name")))
        if name:
            env[name] = (_text(_get(item, key("value"))) or "").strip().lower()
    return env


def _sidecar_hits(ctx):
    for owner, containers, key in _pod_units(ctx.docs):
        agents = []  # (tool, signals, image line, otlp ingest)
        for container in containers:
            image = _get(container, key("image"))
            found = agent_for(_text(image))
            if not found:
                continue
            line = container.key_lines.get(key("image"), image.line)
            if _suppressed(ctx.lines, line) or _suppressed(ctx.lines, container.line):
                continue
            tool, signals = found
            env = _container_env(container, key)
            otlp = False
            if tool == DATADOG_AGENT:
                if env.get("DD_LOGS_ENABLED") in TRUE:
                    signals.add(SIG_LOGS)
                if env.get("DD_APM_ENABLED") in ("false", "0", "no"):
                    signals.discard(TRACES)
                otlp = any(DD_OTLP.match(name) for name in env)
            agents.append((tool, signals, line, otlp))
        bridged = any(tool == DATADOG_AGENT and otlp for tool, _, _, otlp in agents)
        overlap = {}
        for signal in (TRACES, SIG_METRICS, SIG_LOGS):
            tools = {tool for tool, signals, _, _ in agents if signal in signals}
            if bridged and {DATADOG_AGENT, COLLECTOR} <= tools:
                tools.discard(COLLECTOR)  # the collector forwards OTLP to the Datadog Agent
            if len(tools) >= 2:
                overlap[signal] = sorted(tools)
        if not overlap:
            continue
        involved = {tool for tools in overlap.values() for tool in tools}
        lines = sorted({line for tool, _, line, _ in agents if tool in involved})
        detail = "; ".join(f"{signal}: {', '.join(tools)}" for signal, tools in overlap.items())
        summary = (f"{owner} runs {len(involved)} telemetry agents that collect the same signal ({detail}). Each "
                   f"agent processes and ships its own copy, so the pod/task pays for duplicate CPU, memory and "
                   f"ingest.")
        yield f"{owner}:sidecar-agents", lines, summary, "low", REC_SIDECARS


# -- parse / evaluate ----------------------------------------------------------------------------


def parse(locator, content):
    """Context for one file. Raises Unsupported, ParseError or NotEvaluated (textstatic semantics)."""
    lower = locator.lower()
    kind = manifest_kind(locator)
    if kind is None:
        if not lower.endswith((".yaml", ".yml", ".json")) or not _AGENT_HINT.search(content):
            raise Unsupported(locator)
        if lower.endswith(".json") and not re.search(r'"[Cc]ontainerDefinitions"|"kind"', content):
            raise Unsupported(locator)
    reason = _not_evaluated(locator)
    if reason:
        raise NotEvaluated(reason)
    if kind is None:
        try:
            docs = miniyaml.load_all(content)
        except miniyaml.YamlError as error:
            raise ParseError(str(error)) from None
        ctx = Ctx(locator, content.splitlines(), "deploy", docs=docs)
        if not any(True for _ in _pod_units(docs)):
            raise NotEvaluated("no Kubernetes pod template or ECS task definition")
        return ctx
    ecosystem, fmt = kind
    ctx = Ctx(locator, content.splitlines(), "manifest", ecosystem=ecosystem)
    if fmt == "requirements":
        _requirements(ctx)
    elif fmt in ("pyproject", "pipfile"):
        _toml(ctx, fmt)
    elif fmt == "package.json":
        _package_json(ctx)
    else:
        _go_mod(ctx)
    return ctx


def _group_key(locator, ecosystem):
    """(directory, ecosystem) for manifests that describe the directory's package; a unique key otherwise.

    `requirements.<name>.txt` variants side by side often belong to different deployables (one per agent
    or Lambda), so only the canonical names and files under a `requirements/` directory are correlated."""
    path = PurePosixPath(locator.replace("\\", "/"))
    if path.name.lower() in CANONICAL or path.parent.name.lower() == "requirements":
        return (str(path.parent), ecosystem)
    return (locator,)


def _items(ctx, group, source):
    hits = _manifest_hits(ctx, group) if ctx.kind == "manifest" else _sidecar_hits(ctx)
    items = []
    for anchor, lines, summary, confidence, recommendation in hits:
        items.append({
            "anchor": anchor,
            "line": lines[0],
            "summary": summary,
            "confidence": confidence,
            "recommendation": recommendation,
            "evidence": [{
                "source_id": source["source_id"],
                "kind": SUPPORTED_KIND,
                "locator": source["locator"],
                "line_start": line,
                "value": ctx.lines[line - 1],
            } for line in lines],
        })
    items.sort(key=lambda item: item["line"])
    return _unique_identities(items)


def _parse_scope(scope_id, sources):
    """(source, ctx, None) or (None, kind-or-None, omitted reason)."""
    statics = [s for s in sources if isinstance(s, dict) and s.get("kind") == SUPPORTED_KIND]
    if not statics:
        return None, None, f"{scope_id}: no static source supplied for this scope item"
    if len(statics) > 1:
        return None, None, f"{scope_id}: multiple static sources supplied; evaluation requires exactly one"
    source = statics[0]
    locator, content = source.get("locator"), source.get("content")
    if not isinstance(locator, str) or not isinstance(content, str):
        return None, None, f"{scope_id}: static source needs a string locator and content"
    try:
        return source, parse(locator, content), None
    except Unsupported:
        reason = f"unsupported file type; {CHECK_ID} v{DETECTOR_VERSION} supports {FORMATS} only"
    except ParseError as error:
        reason = f"could not be parsed ({error}); not evaluated"
    except NotEvaluated as error:
        reason = f"not evaluated ({error})"
    except Exception as error:  # a parser bug must not become a clean claim for this file
        reason = f"could not be parsed ({type(error).__name__}); not evaluated"
    return None, locator, f"{scope_id}: {reason}"


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements "
        f"{DETECTOR_VERSION}",
    )
    for name in IDENTITY_FIELDS:
        _require(name in payload, f"input is missing required field {name}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")

    parsed, limitations, failed = {}, [], []
    for scope_id in scope:
        source, ctx, omitted = _parse_scope(
            scope_id, [s for s in sources if isinstance(s, dict) and s.get("scope_id") == scope_id])
        if omitted:
            limitations.append(omitted)
            if isinstance(ctx, str) and "could not be parsed" in omitted and manifest_kind(ctx):
                failed.append(ctx)
        else:
            parsed[scope_id] = (source, ctx)

    groups = {}
    for _, ctx in parsed.values():
        if ctx.kind == "manifest":
            groups.setdefault(_group_key(ctx.locator, ctx.ecosystem), []).append(ctx)
    for locator in failed:
        key = _group_key(locator, manifest_kind(locator)[0])
        if len(key) == 2 and key in groups:
            limitations.append(f"{locator}: overlap between it and the other {key[1]} manifests in {key[0]}/ "
                               f"is not judged because it could not be parsed")

    evaluated, findings = [], []
    for scope_id, (source, ctx) in parsed.items():
        try:
            group = groups.get(_group_key(ctx.locator, ctx.ecosystem), [ctx]) if ctx.kind == "manifest" else []
            items = _items(ctx, group, source)
        except Exception as error:  # a detector bug must not become a clean claim for this file
            limitations.append(f"{scope_id}: check failed ({type(error).__name__}); not evaluated")
            continue
        evaluated.append(scope_id)
        for item in items:
            findings.append({
                "fingerprint": fingerprint(payload["repository_id"], CHECK_ID, scope_id, item["identity"]),
                "scope_id": scope_id,
                "identity": item["identity"],
                "summary": item["summary"],
                "confidence": item["confidence"],
                "recommendation": item["recommendation"],
                "references": list(REFERENCES),
                "evidence": item["evidence"],
            })
    limitations.append(LIMITATION)
    evaluated = [scope_id for scope_id in scope if scope_id in evaluated]
    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result = {name: payload[name] for name in IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result
