"""TST-12: heavy fixtures, sleeps and real network calls in unit tests.

Detector semantics version 2.0.1. Two evidence modes, dispatched on source kind:

- static (primary): a Python test module (`file:<path>` scope, one `static` source) is
  parsed with `ast`; it is never imported or executed. Tests, fixtures and setup/teardown
  hooks are flagged for constant sleeps, unmocked calls to literal external URLs and
  constant-size large allocations. Works on any repository that has its test source.
- artifact (optional): a normalized CI test-run artifact (`test:<test_id>` scope, or
  alongside the static source in that test file's `file:<path>` scope) is compared with
  the configured duration/sleep/network/fixture/setup maxima.

Missing, unparseable or unsupported input is never reported as clean.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from urllib.parse import urlsplit

CHECK_ID = "TST-12"
DETECTOR_VERSION = "2.0.1"
IDENTITY = "heavy-test-work"  # artifact identity (one artifact per test: scope)
STATIC_KIND = "static"
ARTIFACT_KIND = "artifact"
NOQA = ("TST-12", "TST12")
EVIDENCE_MAX_LINES = 3

STATIC_SETTING_KEYS = ("max_sleep_seconds", "max_network_calls", "max_fixture_bytes")
SETTING_KEYS = (
    "max_duration_seconds",
    "max_sleep_seconds",
    "max_network_calls",
    "max_fixture_bytes",
    "max_setup_seconds",
)
ARTIFACT_SETTING_KEYS = SETTING_KEYS
# Reference values from "TST-12 > Context settings" in detectors/owner-d/README.md; repository
# scans pass them unless a setting is supplied explicitly.
REFERENCE_SETTINGS = {
    "max_sleep_seconds": 0.1,  # README TST-12: explicit sleep allowance per test
    "max_network_calls": 0,  # README TST-12: real network-call allowance per test
    "max_fixture_bytes": 10485760,  # README TST-12: largest fixture materialization allowance
    "max_duration_seconds": 10,  # README TST-12: longest unit-test duration (artifact only)
    "max_setup_seconds": 2,  # README TST-12: longest setup/fixture duration (artifact only)
}

REQUIRED_DATA_FIELDS = (
    "test_id",
    "framework",
    "duration_seconds",
    "sleep_seconds",
    "network_call_count",
    "fixture_bytes",
    "setup_seconds",
)

NUMERIC_DATA_FIELDS = (
    "duration_seconds",
    "sleep_seconds",
    "network_call_count",
    "fixture_bytes",
    "setup_seconds",
)
# Largest accepted integer in artifact data and context settings (signed 64-bit). A bigger JSON integer is
# malformed input: it is copied into every finding, so hundreds of digits would push a result past the
# one-event size limit, and past float range it cannot be compared at all.
MAX_INTEGER = 2**63 - 1

IDENTITY_FIELDS = (
    "schema_version",
    "repository_id",
    "scan_id",
    "commit_sha",
    "check_id",
    "detector_version",
    "context",
    "scope",
)

REFERENCES = (
    "https://github.com/AWS-env/environmental-hacks/issues/267",
    "https://arxiv.org/abs/2310.14548",
    "https://docs.pytest.org/en/stable/how-to/monkeypatch.html",
)

RECOMMENDATION = (
    "Replace real waits, network calls or oversized fixtures with fakes/mocks (a fake clock or event, "
    "responses/respx/requests-mock, moto/Stubber, a small generated payload) and keep heavyweight "
    "coverage in a separately marked integration or performance suite."
)

GENERAL_LIMITATION = (
    "TST-12 flags unit-test work, not measured energy; confirm intentionally slow integration, "
    "load or end-to-end tests before changing the suite."
)

STATIC_LIMITATION = (
    "TST-12 static mode proves the pattern in test source only: constant sleeps, calls to literal "
    "external URLs (or AWS SDK calls) with no visible mocking, and constant-size allocations inside "
    "tests, fixtures and setup/teardown hooks. It does not follow helpers in other modules, see mocks "
    "or network blockers configured outside the supplied files (conftest.py not in the payload, "
    "pytest plugins/ini options), resolve dynamic URLs, or know the size of fixture data files. "
    "Tests marked or located as integration/e2e/slow/network/live are excluded."
)


class EvaluationError(ValueError):
    """The payload cannot be evaluated as a TST-12 contract input."""


def _canonical(value):
    """Stable UTF-8 JSON encoding shared with the other contract implementations."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(repository_id, check_id, scope_id, identity):
    """Line numbers, observed values and commit IDs intentionally do not identify findings."""
    parts = [repository_id, check_id, scope_id, identity]
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _is_number(value):
    """A finite int or float. Never raises: an int beyond float range (JSON allows thousands of digits)
    makes math.isfinite raise OverflowError, so it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _in_range(value):
    """Artifact values and settings: ints are bounded so results stay small (one event per result)."""
    return not isinstance(value, int) or abs(value) <= MAX_INTEGER


def _fmt(value):
    if isinstance(value, int):
        return str(value)
    return f"{value:g}"


def _read_settings(context, keys):
    missing = [key for key in keys if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {}
    for key in keys:
        value = context[key]
        if not _in_range(value):
            return None, f"context.{key} is out of range (integers are limited to {MAX_INTEGER})"
        if not _is_number(value):
            return None, f"context.{key} must be a number"
        if value < 0:
            return None, f"context.{key} must be nonnegative"
        settings[key] = value
    return settings, None


# --------------------------------------------------------------------------- artifact mode


def _artifact_problems(data, test_id_check):
    if not isinstance(data, dict):
        return ["artifact data must be an object"]
    problems = []
    missing = [field for field in REQUIRED_DATA_FIELDS if field not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    for field in ("test_id", "framework"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            problems.append(f"{field} must be a nonempty string")
    numeric = {}
    for field in NUMERIC_DATA_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not _in_range(value):
            problems.append(f"{field} is out of range (integers are limited to {MAX_INTEGER})")
        elif not _is_number(value):
            problems.append(f"{field} must be a number")
        else:
            numeric[field] = value
    for field, value in numeric.items():
        if value < 0:
            problems.append(f"{field} must be nonnegative")
    for field in ("network_call_count", "fixture_bytes"):
        if field in numeric and not isinstance(numeric[field], int):
            problems.append(f"{field} must be an integer")
    test_id = data.get("test_id")
    if isinstance(test_id, str) and test_id.strip():
        problem = test_id_check(test_id)
        if problem:
            problems.append(problem)
    return problems


def _artifact_signals(data, settings):
    checks = (
        ("duration_seconds", "duration", "max_duration_seconds"),
        ("sleep_seconds", "sleep", "max_sleep_seconds"),
        ("network_call_count", "network calls", "max_network_calls"),
        ("fixture_bytes", "fixture bytes", "max_fixture_bytes"),
        ("setup_seconds", "setup", "max_setup_seconds"),
    )
    return [
        (field, label, data[field], settings[limit])
        for field, label, limit in checks
        if data[field] > settings[limit]
    ]


def _artifact_finding(source, settings, identity):
    """Return a finding body (without scope/fingerprint) or None if within all maxima."""
    data = source["data"]
    signals = _artifact_signals(data, settings)
    if not signals:
        return None
    cited_fields = ["duration_seconds"] + [field for field, _, _, _ in signals if field != "duration_seconds"]
    signal_text = ", ".join(f"{label} {_fmt(value)} exceeds {_fmt(limit)}" for _, label, value, limit in signals)
    crossed = {field for field, _, _, _ in signals}
    confidence = "high" if crossed & {"network_call_count", "sleep_seconds"} else "medium"
    return {
        "identity": identity,
        "summary": (
            f"{data['framework']} test {data['test_id']} does more work than the unit assertion likely needs: "
            f"{signal_text}"
        ),
        "confidence": confidence,
        "evidence": [
            {
                "source_id": source["source_id"],
                "kind": ARTIFACT_KIND,
                "locator": source["locator"],
                "field": field,
                "value": data[field],
            }
            for field in cited_fields
        ],
    }


# --------------------------------------------------------------------------- static mode

_NOQA_RE = re.compile(r"#\s*noqa(?::\s*([A-Za-z0-9, -]+))?", re.I)

# Path components (directories) and test-file stem tokens that denote non-unit suites.
_SUITE_DIRS = {
    "integration", "integrations_tests", "integration_tests", "integrationtests", "it", "e2e",
    "end_to_end", "endtoend", "functional", "functional_tests", "acceptance", "smoke", "live",
    "perf", "performance", "benchmark", "benchmarks", "load", "stress", "system", "system_tests",
    "slow", "network", "online", "remote", "external",
}
_SUITE_TOKENS = {
    "integration", "integ", "e2e", "endtoend", "functional", "acceptance", "smoke", "live",
    "benchmark", "benchmarks", "perf", "stress", "slow", "network", "internet", "online",
    "remote", "external", "vcr", "cassette", "cassettes", "credentials", "credential", "apikey",
}
_NAME_TOKENS = {"integration", "e2e", "endtoend", "functional", "acceptance", "smoke", "live", "benchmark",
                "stress", "slow"}
_SKIP_WORDS = {"skip", "skipif", "skipunless", "xfail", "requires", "needs", "only", "importorskip"}

# Any import of these means the module mocks/records HTTP or AWS traffic.
_MOCK_MODULES = {
    "responses", "requests_mock", "respx", "httpretty", "vcr", "pytest_recording", "betamax",
    "aioresponses", "aresponses", "pytest_httpx", "httmock", "pook", "mocket", "moto",
    "pytest_socket", "pytest_httpserver", "pytest_localserver", "pytest_httpbin", "localstack",
    "werkzeug", "http.server", "socketserver", "wsgiref", "aiohttp.test_utils",
    "pytest_aiohttp", "botocore.stub", "httpx_mock", "requests_testadapter",
}
_MOCK_FIXTURES = {
    "requests_mock", "httpx_mock", "respx_mock", "mocked_responses", "responses", "aioresponses",
    "aresponses", "httpretty", "vcr", "vcr_cassette", "vcr_config", "betamax_session", "httpserver",
    "httpbin", "httpbin_secure", "live_server", "aiohttp_client", "aiohttp_server", "unused_tcp_port",
    "socket_enabled", "socket_disabled", "s3", "moto", "aws", "mock_aws", "stubber",
}
_NETWORK_PATCH_HINTS = (
    "requests", "httpx", "urllib", "urlopen", "socket", "boto", "aiohttp", "http", "session",
    "transport", "adapter", "getaddrinfo", "connection",
)
_FAKE_CLOCK_HINTS = ("autojump_clock", "MockClock", "VirtualClock", "looptime", "time_machine.travel")
_FAKE_TRANSPORT_BASES = {"HTTPAdapter", "BaseAdapter", "BaseTransport", "AsyncBaseTransport",
                         "AioHTTPTestCase", "LiveServerTestCase"}
_TRANSPORT_OVERRIDES = {"transport", "app", "mounts", "connector"}

_SYNC_SLEEPS = {"time.sleep", "gevent.sleep", "eventlet.sleep"}
_ASYNC_SLEEPS = {"asyncio.sleep", "trio.sleep", "anyio.sleep", "curio.sleep"}
_TIMEOUT_SCOPES = {"move_on_after", "fail_after", "move_on_at", "fail_at", "timeout", "timeout_at",
                   "CancelScope", "wait_for"}

_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}
_FUNCTIONAL_HTTP = {"requests", "httpx"}
_SESSION_FACTORIES = {
    "requests.Session", "requests.session", "requests.sessions.Session", "httpx.Client",
    "httpx.AsyncClient", "aiohttp.ClientSession", "urllib3.PoolManager",
}
_URLOPEN = ("urllib.request.urlopen", "urllib.request.urlretrieve", "urllib2.urlopen", "urllib.urlopen")
_BOTO_FACTORIES = {"boto3.client", "boto3.resource"}
_BOTO_LOCAL_METHODS = {"generate_presigned_url", "generate_presigned_post", "get_paginator", "get_waiter",
                       "can_paginate", "close", "meta", "exceptions"}

_FIXTURE_DECORATORS = ("pytest.fixture", "pytest_asyncio.fixture", "fixture", "yield_fixture",
                       "pytest.yield_fixture")
_HOOK_NAMES = {
    "setUp", "tearDown", "setUpClass", "tearDownClass", "asyncSetUp", "asyncTearDown", "setUpModule",
    "tearDownModule", "setup_method", "teardown_method", "setup_class", "teardown_class",
    "setup_module", "teardown_module", "setup_function", "teardown_function",
}

_ITEMSIZE = {
    None: 8, "float": 8, "float64": 8, "f8": 8, "double": 8, "int": 8, "int64": 8, "i8": 8, "uint64": 8,
    "float32": 4, "f4": 4, "int32": 4, "i4": 4, "uint32": 4, "float16": 2, "int16": 2, "uint16": 2,
    "int8": 1, "uint8": 1, "u1": 1, "i1": 1, "bool": 1, "bool_": 1, "complex128": 16, "complex": 16,
}
_NUMPY_SHAPED = {"zeros", "ones", "empty", "full", "random.rand", "random.randn", "random.random",
                 "random.random_sample"}

_LOCAL_HOSTS = {"localhost", "0.0.0.0", "::1", "[::1]", "::", "testserver", "example.com", "example.org",
                "example.net", "www.example.com", "www.example.org", "www.example.net"}
_RESERVED_SUFFIXES = (".localhost", ".test", ".invalid", ".example", ".local", ".internal", ".lan")


def _tokens(text):
    """Lowercase word tokens; splits snake_case, camelCase, dots and punctuation."""
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text or "")
    return {word.lower() for word in words} | {part.lower() for part in re.split(r"[^A-Za-z0-9]+", text or "") if part}


def _is_noqa(line):
    match = _NOQA_RE.search(line)
    if not match:
        return False
    named = match.group(1)
    return named is None or any(code.upper() in named.upper() for code in NOQA)


def is_test_path(path):
    """Test module naming conventions recognised by pytest/unittest discovery."""
    pure = PurePosixPath(path)
    name = pure.name
    if not name.endswith(".py"):
        return False
    if name == "conftest.py" or name.startswith("test") or name.endswith("_test.py") or name.endswith("_tests.py"):
        return True
    return any(part in ("test", "tests", "testing", "unittests", "unit_tests") for part in pure.parts[:-1])


def non_unit_suite_path(path):
    """True if the path places the module in an integration/e2e/perf-style suite."""
    pure = PurePosixPath(path)
    if any(part.lower() in _SUITE_DIRS for part in pure.parts[:-1]):
        return True
    return bool(_tokens(pure.stem) & _NAME_TOKENS)


def _host_is_external(host):
    host = (host or "").strip().lower().rstrip(".")
    if not host or host in _LOCAL_HOSTS or "." not in host and ":" not in host:
        return False
    if host.endswith(_RESERVED_SUFFIXES) or host.startswith("127.") or host.startswith("[") or ":" in host:
        return False
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        first, second = (int(part) for part in host.split(".")[:2])
        if first in (10, 127, 0) or (first == 192 and second == 168) or (first == 172 and 16 <= second <= 31):
            return False
    return True


def _external_host(url):
    """The external host for an absolute http(s)/ws URL prefix, else None."""
    if not isinstance(url, str) or "://" not in url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https", "ws", "wss"):
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if not netloc or "{" in netloc:
        return None
    host = parts.hostname
    return host if _host_is_external(host) else None


@dataclass
class _Unit:
    """One test, fixture or setup/teardown hook, including everything nested inside it."""

    node: ast.AST
    qualname: str
    kind: str  # test | fixture | setup hook
    classes: tuple  # enclosing ClassDef nodes, outermost first


@dataclass(frozen=True)
class _Signal:
    category: str  # sleep | network-call | large-allocation
    node: ast.AST
    detail: str
    amount: float = 0
    in_loop: bool = False
    confidence: str = "high"


class _Module:
    """One parsed test module with import aliases, parent links and constant lookup."""

    def __init__(self, path, source):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.tree = ast.parse(source, filename=path)
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.parent = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node
        self.aliases = {}
        self.imported_modules = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imported_modules.add(alias.name)
                    if alias.asname:
                        self.aliases[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".")[0]
                        self.aliases[root] = root
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                self.imported_modules.add(node.module)
                for alias in node.names:
                    if alias.name != "*":
                        self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                        self.imported_modules.add(f"{node.module}.{alias.name}")
        self.module_constants = self._single_assignments(self.tree)

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _single_assignments(root):
        """Every name bound in `root`'s subtree -> its value if bound exactly once by `name = value`.

        Names bound more than once, or by loops/with/augmented assignment/unpacking, map to None
        (unknown), so they never fall through to an outer constant of the same name.
        """
        counts, values = Counter(), {}
        for node in ast.walk(root):
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(target, node.value) for target in node.targets]
            elif isinstance(node, ast.AnnAssign):
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr, ast.comprehension)):
                pairs = [(node.target, None)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                pairs = [(node.optional_vars, None)]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                counts[node.name] += 2
                values[node.name] = None
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    counts[name] += 2
                    values[name] = None
            for target, value in pairs:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        counts[name.id] += 1
                        values[name.id] = value if name is target else None
        return {name: (values[name] if counts[name] == 1 else None) for name in counts}

    def ancestors(self, node):
        node = self.parent.get(id(node))
        while node is not None:
            yield node
            node = self.parent.get(id(node))

    def dotted(self, node, shadowed=frozenset()):
        """Resolve `a.b.c` to a dotted import path; None unless the root name was imported."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id in shadowed or node.id not in self.aliases:
            return None
        parts.append(self.aliases[node.id])
        return ".".join(reversed(parts))

    @staticmethod
    def raw_dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Call):
            return _Module.raw_dotted(node.func)
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    def evidence(self, node):
        start = node.lineno
        end = min(getattr(node, "end_lineno", start) or start, start + EVIDENCE_MAX_LINES - 1)
        return start, "\n".join(self.lines[start - 1:end])

    # -- constant evaluation -------------------------------------------------------------

    def number(self, node, local, depth=0):
        if depth > 8 or node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value if _is_number(node.value) else None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = self.number(node.operand, local, depth + 1)
            return None if value is None else (-value if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow)):
            left, right = self.number(node.left, local, depth + 1), self.number(node.right, local, depth + 1)
            if left is None or right is None:
                return None
            try:
                if isinstance(node.op, ast.Pow):
                    if abs(right) > 64:
                        return None
                    value = left ** right
                else:
                    value = {
                        ast.Add: lambda: left + right, ast.Sub: lambda: left - right,
                        ast.Mult: lambda: left * right, ast.Div: lambda: left / right,
                        ast.FloorDiv: lambda: left // right,
                    }[type(node.op)]()
            except (ArithmeticError, OverflowError):
                return None
            return value if _is_number(value) else None
        if isinstance(node, ast.Name):
            value, scope = self._lookup(node.id, local)
            return self.number(value, scope, depth + 1) if value is not None else None
        return None

    def _lookup(self, name, local):
        """(value node or None, the namespace it was bound in)."""
        if name in local:
            return local[name], local
        return self.module_constants.get(name), {}

    def string_prefix(self, node, local, depth=0):
        """A statically known string, or the constant prefix of an f-string/concatenation."""
        if depth > 8 or node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            prefix = ""
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    prefix += part.value
                    continue
                inner = None
                if isinstance(part, ast.FormattedValue) and part.conversion == -1 and part.format_spec is None:
                    inner = self.string_prefix(part.value, local, depth + 1)
                if inner is None:
                    return prefix + "{"
                prefix += inner
                if inner.endswith("{"):
                    return prefix
            return prefix
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.string_prefix(node.left, local, depth + 1)
            if left is None:
                return None
            if left.endswith("{"):
                return left
            right = self.string_prefix(node.right, local, depth + 1)
            return left + (right if right is not None else "{")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            text = self.string_prefix(node.left, local, depth + 1)
            return None if text is None else text.split("%", 1)[0] + ("{" if "%" in text else "")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"):
            text = self.string_prefix(node.func.value, local, depth + 1)
            return None if text is None else text.split("{", 1)[0] + ("{" if "{" in text else "")
        if isinstance(node, ast.Name):
            value, scope = self._lookup(node.id, local)
            return self.string_prefix(value, scope, depth + 1) if value is not None else None
        return None

    # -- module-wide mocking signals (cached: conftest modules are shared across scopes) ------

    @cached_property
    def patch_targets(self):
        """Text of every patch/monkeypatch/setattr target in the module."""
        texts = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            name = self.raw_dotted(node.func) or ""
            last = name.split(".")[-1]
            if last in ("patch", "object", "setattr", "patch_object", "multiple", "dict") and (
                "patch" in name or "setattr" in name or "mock" in name.lower()
            ):
                texts.append(" ".join(ast.unparse(arg) for arg in node.args[:2]))
        return texts

    @cached_property
    def network_mocked(self):
        """The module imports an HTTP/AWS mocking or local-server library, or patches a network target."""
        if any(mod == m or mod.startswith(m + ".") for mod in self.imported_modules for m in _MOCK_MODULES):
            return True
        plugins = self.module_constants.get("pytest_plugins")
        if plugins is not None and any(m in ast.unparse(plugins) for m in _MOCK_MODULES):
            return True
        if any(hint in text.lower() for text in self.patch_targets for hint in _NETWORK_PATCH_HINTS):
            return True
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                last = (self.raw_dotted(node.func) or "").split(".")[-1]
                if last in ("mount", "install_opener", "MockTransport", "disable_socket", "Stubber"):
                    return True
            elif isinstance(node, ast.ClassDef):
                if any((self.raw_dotted(base) or "").split(".")[-1] in _FAKE_TRANSPORT_BASES for base in node.bases):
                    return True
        return False

    @cached_property
    def sleep_mocked(self):
        if any(hint in self.source for hint in _FAKE_CLOCK_HINTS):
            return True
        return any("sleep" in text.lower() or re.search(r"[\"'][\w.]*\btime[\"']", text)
                   for text in self.patch_targets)

    # -- structure -----------------------------------------------------------------------

    def decorator_name(self, decorator):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        return self.dotted(target) or self.raw_dotted(target) or ""

    def is_fixture(self, func):
        for decorator in func.decorator_list:
            name = self.decorator_name(decorator)
            if name in _FIXTURE_DECORATORS or name.endswith(".fixture"):
                return True
        return False

    @staticmethod
    def is_test_class(cls):
        names = [cls.name] + [_Module.raw_dotted(base) or "" for base in cls.bases]
        for index, name in enumerate(names):
            last = name.split(".")[-1]
            if index == 0 and last.startswith("Test"):
                return True
            if re.search(r"(Test|Tests|TestCase|TestBase|Mixin)$", last) or "TestCase" in last:
                return True
        return False

    def units(self):
        units = []
        is_conftest = PurePosixPath(self.path).name == "conftest.py"

        def visit(body, classes, prefix):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, classes + (node,), prefix + [node.name])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = ".".join(prefix + [node.name])
                    in_test_class = bool(classes) and all(self.is_test_class(cls) for cls in classes)
                    if self.is_fixture(node):
                        kind = "fixture"
                    elif node.name in _HOOK_NAMES and (not classes or in_test_class):
                        kind = "setup hook"
                    elif not is_conftest and node.name.startswith("test") and (not classes or in_test_class):
                        kind = "test"
                    else:
                        continue
                    units.append(_Unit(node, qualname, kind, classes))

        visit(self.tree.body, (), [])
        return units


class _StaticAnalysis:
    def __init__(self, module, settings, conftests):
        self.m = module
        self.settings = settings
        self.conftests = conftests  # supplied ancestor conftest _Module objects
        self.module_suite = non_unit_suite_path(module.path) or self._has_suite_marker(module.tree.body)
        self.network_mocked = module.network_mocked or any(c.network_mocked for c in conftests)
        self.sleep_mocked = module.sleep_mocked or any(c.sleep_mocked for c in conftests)

    def _has_suite_marker(self, body):
        for node in body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
                for sub in ast.walk(node.value):
                    if isinstance(sub, (ast.Attribute, ast.Call)) and self._suite_decorator(sub):
                        return True
        return False

    def _suite_decorator(self, decorator):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = self.m.dotted(target) or self.m.raw_dotted(target) or ""
        if not name:
            return False
        if ".mark." in f".{name}":
            marker = name.split(".mark.", 1)[-1] if ".mark." in name else name.split("mark.", 1)[-1]
            words = _tokens(marker)
            if words & _SUITE_TOKENS:
                return True
            check_args = bool(words & {"skipif", "skip", "xfail"} or marker.startswith("skip"))
        else:
            words = _tokens(name.split(".")[-1])
            if words & _SUITE_TOKENS:
                return True
            check_args = bool(words & _SKIP_WORDS)
        if check_args and isinstance(decorator, ast.Call):
            text = " ".join(ast.unparse(arg) for arg in list(decorator.args) + [k.value for k in decorator.keywords])
            return bool(_tokens(text) & _SUITE_TOKENS)
        return False

    def _skips_for_suite(self, func):
        """A body-level skip (pytest.skip / skipTest / SkipTest) that mentions network/integration."""
        for node in ast.walk(func):
            call = node.exc if isinstance(node, ast.Raise) else node
            if not isinstance(call, ast.Call):
                continue
            last = (self.m.raw_dotted(call.func) or "").split(".")[-1]
            if last not in ("skip", "skipTest", "SkipTest", "importorskip", "xfail"):
                continue
            text = " ".join(ast.unparse(arg) for arg in call.args)
            for ancestor in self.m.ancestors(node):
                if isinstance(ancestor, ast.If):
                    text += " " + ast.unparse(ancestor.test)
                if ancestor is func:
                    break
            if _tokens(text) & _SUITE_TOKENS:
                return True
        return False

    def excluded(self, unit):
        if self.module_suite:
            return True
        for cls in unit.classes:
            if _tokens(cls.name) & _NAME_TOKENS or any(self._suite_decorator(d) for d in cls.decorator_list):
                return True
            if any(_tokens((self.m.raw_dotted(base) or "").split(".")[-1]) & _NAME_TOKENS for base in cls.bases):
                return True
            if self._has_suite_marker(cls.body):
                return True
        func = unit.node
        if _tokens(func.name) & _NAME_TOKENS or any(self._suite_decorator(d) for d in func.decorator_list):
            return True
        if any(arg.arg in ("live_server", "httpbin", "httpserver") for arg in func.args.args):
            return True
        return self._skips_for_suite(func)

    # -- signal collection ---------------------------------------------------------------

    def _in_nested_def(self, node, unit):
        for ancestor in self.m.ancestors(node):
            if ancestor is unit.node:
                return False
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return True
        return False

    def _in_loop(self, node, unit):
        for ancestor in self.m.ancestors(node):
            if ancestor is unit.node:
                return False
            if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp,
                                     ast.DictComp, ast.GeneratorExp)):
                return True
        return False

    def _bounded_by_timeout(self, node, unit):
        """Inside a timeout/cancel scope, or a `raises(TimeoutError)` block: the wait is cut short."""
        for ancestor in self.m.ancestors(node):
            if ancestor is unit.node:
                return False
            if isinstance(ancestor, (ast.With, ast.AsyncWith)):
                for item in ancestor.items:
                    expr = item.context_expr
                    last = (self.m.raw_dotted(expr) or "").split(".")[-1]
                    if last in _TIMEOUT_SCOPES or "timeout" in last.lower():
                        return True
                    if last in ("raises", "assertRaises", "assertRaisesRegex") and isinstance(expr, ast.Call):
                        expected = " ".join(ast.unparse(arg) for arg in expr.args[:1])
                        if re.search(r"Timeout|Cancel", expected):
                            return True
        return False

    def _sleep(self, call, name, local, unit):
        if self.sleep_mocked or name not in _SYNC_SLEEPS | _ASYNC_SLEEPS:
            return None
        if name in _ASYNC_SLEEPS:
            parent = self.m.parent.get(id(call))
            runner = (isinstance(parent, ast.Call) and (self.m.raw_dotted(parent.func) or "").split(".")[-1]
                      in ("run_until_complete", "run", "run_sync"))
            if not isinstance(parent, ast.Await) and not runner:
                return None  # coroutine handed to wait_for/create_task etc.: may be cancelled early
        if self._bounded_by_timeout(call, unit):
            return None
        arg = call.args[0] if call.args else next(
            (k.value for k in call.keywords if k.arg in ("seconds", "secs", "delay")), None)
        seconds = self.m.number(arg, local)
        if seconds is None or seconds <= 0:
            return None
        return _Signal("sleep", call, f"{name}({_fmt(seconds)})", seconds, self._in_loop(call, unit))

    def _http_url_arg(self, call, method):
        index = 1 if method in ("request", "stream") else 0
        for keyword in call.keywords:
            if keyword.arg == "url":
                return keyword.value
        return call.args[index] if len(call.args) > index else None

    def _external(self, url_node, local, base_url=None):
        text = self.m.string_prefix(url_node, local)
        if text is None:
            return None
        if base_url and "://" not in text and not text.startswith("{"):
            text = base_url.rstrip("/") + "/" + text.lstrip("/")
        return _external_host(text)

    def _client_factories(self, unit, shadowed, local):
        """Names bound to HTTP sessions / AWS clients in this unit -> (kind, base_url or None)."""
        bound = {}
        for node in ast.walk(unit.node):
            pairs = []
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                pairs.append((node.targets[0].id, node.value))
            elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
                pairs.append((node.optional_vars.id, node.context_expr))
            for name, value in pairs:
                info = self._factory(value, shadowed, local)
                if info:
                    bound[name] = info
        return bound

    def _factory(self, value, shadowed, local):
        if not isinstance(value, ast.Call):
            return None
        name = self.m.dotted(value.func, shadowed) or ""
        keywords = {k.arg: k.value for k in value.keywords if k.arg}
        if name in _SESSION_FACTORIES:
            if _TRANSPORT_OVERRIDES & set(keywords):
                return None  # in-process transport / app: no socket traffic
            base = self.m.string_prefix(keywords.get("base_url"), local) if "base_url" in keywords else None
            return ("http", base)
        is_boto = name in _BOTO_FACTORIES or (
            isinstance(value.func, ast.Attribute) and value.func.attr in ("client", "resource")
            and isinstance(value.func.value, ast.Call)
            and (self.m.dotted(value.func.value.func, shadowed) or "") in ("boto3.Session", "boto3.session.Session"))
        if is_boto:
            endpoint = keywords.get("endpoint_url")
            if endpoint is not None and not _external_host(self.m.string_prefix(endpoint, local) or ""):
                return None  # local endpoint (localstack/moto server) or unknown endpoint
            return ("aws", None)
        return None

    def _network(self, call, name, local, unit, clients, shadowed):
        if self.network_mocked:
            return None
        func = call.func
        root, _, method = name.rpartition(".")
        if root in _FUNCTIONAL_HTTP and method in _HTTP_VERBS | {"request", "stream"}:
            host = self._external(self._http_url_arg(call, method), local)
            return _Signal("network-call", call, f"{name}() to {host}") if host else None
        if name.endswith(_URLOPEN):
            target = call.args[0] if call.args else None
            if isinstance(target, ast.Call) and (self.m.raw_dotted(target.func) or "").endswith("Request"):
                target = target.args[0] if target.args else None
            host = self._external(target, local)
            return _Signal("network-call", call, f"{name}() to {host}") if host else None
        if name == "socket.create_connection" and call.args and isinstance(call.args[0], ast.Tuple) and call.args[0].elts:
            host = self.m.string_prefix(call.args[0].elts[0], local)
            if host and "{" not in host and _host_is_external(host):
                return _Signal("network-call", call, f"socket.create_connection() to {host}")
            return None
        if not isinstance(func, ast.Attribute):
            return None
        receiver = func.value
        info = None
        if isinstance(receiver, ast.Name) and receiver.id in clients:
            info = clients[receiver.id]
        elif isinstance(receiver, ast.Call):
            info = self._factory(receiver, shadowed, local)
        if info is None:
            return None
        kind, base = info
        if kind == "http" and func.attr in _HTTP_VERBS | {"request", "stream", "ws_connect"}:
            host = self._external(self._http_url_arg(call, func.attr), local, base)
            return _Signal("network-call", call, f"HTTP client .{func.attr}() to {host}") if host else None
        if kind == "aws" and not func.attr.startswith("_") and func.attr not in _BOTO_LOCAL_METHODS:
            return _Signal("network-call", call, f"AWS SDK .{func.attr}() without a stub or local endpoint",
                           confidence="medium")
        return None

    def _allocation(self, call, name, local):
        limit = self.settings["max_fixture_bytes"]
        size, what = None, None
        if isinstance(call, ast.BinOp) and isinstance(call.op, ast.Mult):
            parent = self.m.parent.get(id(call))
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mult):
                return None  # evaluated once, at the outermost multiplication
            factors, stack = [], [call]
            while stack:
                node = stack.pop()
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                    stack.extend((node.left, node.right))
                else:
                    factors.append(node)
            blobs = [f for f in factors if isinstance(f, ast.Constant) and isinstance(f.value, (bytes, str)) and f.value]
            counts = [self.m.number(f, local) for f in factors if f not in blobs]
            if len(blobs) == 1 and counts and all(isinstance(c, int) and c >= 0 for c in counts):
                blob = blobs[0].value
                unit_bytes = len(blob) if isinstance(blob, bytes) else len(blob.encode("utf-8"))
                size, what = unit_bytes * math.prod(counts), "repeated literal"
        elif isinstance(call, ast.Call):
            if name in ("bytes", "bytearray", "os.urandom", "secrets.token_bytes", "random.randbytes") and call.args:
                if name in ("bytes", "bytearray") and (self.m.dotted(call.func) is not None):
                    return None  # shadowed builtin
                count = self.m.number(call.args[0], local)
                if isinstance(count, int):
                    size, what = count, f"{name}()"
            elif name.startswith("numpy.") and name[len("numpy."):] in _NUMPY_SHAPED and call.args:
                func = name[len("numpy."):]
                dims = call.args if func in ("random.rand", "random.randn") else [call.args[0]]
                if len(dims) == 1 and isinstance(dims[0], (ast.Tuple, ast.List)):
                    dims = dims[0].elts
                values = [self.m.number(dim, local) for dim in dims]
                dtype = next((k.value for k in call.keywords if k.arg == "dtype"), None)
                if dtype is None and func == "full" and len(call.args) > 2:
                    dtype = call.args[2]
                dtype_name = None if dtype is None else (
                    dtype.value if isinstance(dtype, ast.Constant) else (self.m.raw_dotted(dtype) or "?").split(".")[-1])
                itemsize = _ITEMSIZE.get(dtype_name)
                if itemsize and values and all(isinstance(v, int) and v >= 0 for v in values):
                    size, what = math.prod(values) * itemsize, f"{name}()"
        if size is None or size <= limit:
            return None
        return _Signal("large-allocation", call, f"{what} of {size} bytes", size, confidence="medium")

    def signals(self, unit):
        func = unit.node
        shadowed = set()
        for node in ast.walk(func):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                shadowed |= {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
                shadowed |= {a.arg for a in (args.vararg, args.kwarg) if a}
        local = _Module._single_assignments(func)
        local.update({name: None for name in shadowed})
        clients = self._client_factories(unit, shadowed, local)
        network_mock_fixture = any(arg.arg in _MOCK_FIXTURES for arg in func.args.args)
        found = []
        for node in ast.walk(func):
            if isinstance(node, ast.BinOp):
                signal = None if self._in_nested_def(node, unit) else self._allocation(node, "", local)
                if signal:
                    found.append(signal)
                continue
            if not isinstance(node, ast.Call) or self._in_nested_def(node, unit):
                continue
            name = self.m.dotted(node.func, shadowed)
            if name is None and isinstance(node.func, ast.Name) and node.func.id in ("bytes", "bytearray"):
                name = node.func.id
            signal = None
            if name:
                signal = self._sleep(node, name, local, unit) or self._allocation(node, name, local)
            if signal is None and not network_mock_fixture:
                signal = self._network(node, name or "", local, unit, clients, shadowed)
            if signal:
                found.append(signal)
        return [s for s in found if not _is_noqa(self.m.lines[s.node.lineno - 1])]


def _path_sleep(statements, amounts):
    """Most constant sleep along one execution path: branches take the max, loops count once."""

    def block(stmts):
        return sum(stmt(node) for node in stmts)

    def stmt(node):
        if isinstance(node, ast.If):
            return max(block(node.body), block(node.orelse))
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            # except-handlers are retry/error paths, not the normal run of the test
            return block(node.body) + block(node.orelse) + block(node.finalbody)
        if isinstance(node, ast.Match):
            return max([block(case.body) for case in node.cases] or [0])
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return block(node.body) + block(node.orelse)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return block(node.body)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return 0
        return sum(amounts.get(id(child), 0) for child in ast.walk(node))

    return block(statements)


def _static_items(module, settings, conftests, source):
    analysis = _StaticAnalysis(module, settings, conftests)
    items = []
    for unit in module.units():
        if _is_noqa(module.lines[unit.node.lineno - 1]) or analysis.excluded(unit):
            continue
        by_category = {}
        for signal in analysis.signals(unit):
            by_category.setdefault(signal.category, []).append(signal)
        for category, signals in by_category.items():
            if category == "network-call" and len(signals) <= settings["max_network_calls"]:
                continue
            total = 0
            if category == "sleep":
                total = round(_path_sleep(unit.node.body, {id(s.node): s.amount for s in signals}), 6)
                if total <= settings["max_sleep_seconds"]:
                    continue
            summary = _static_summary(unit, category, signals, settings, total)
            evidence, seen_lines = [], set()
            for signal in sorted(signals, key=lambda s: (s.node.lineno, s.node.col_offset)):
                start, text = module.evidence(signal.node)
                if start in seen_lines:
                    continue
                seen_lines.add(start)
                evidence.append({
                    "source_id": source["source_id"],
                    "kind": STATIC_KIND,
                    "locator": source["locator"],
                    "line_start": start,
                    "value": text,
                })
            confidence = "high" if all(s.confidence == "high" for s in signals) else "medium"
            if category == "network-call" and confidence == "high" and not conftests:
                confidence = "medium"  # an unsupplied conftest.py could still block or mock the network
            items.append({
                "anchor": f"{unit.qualname}:{category}",
                "line": min(s.node.lineno for s in signals),
                "summary": summary,
                "confidence": confidence,
                "evidence": evidence,
            })
    items.sort(key=lambda item: (item["line"], item["anchor"]))
    seen = Counter()
    for item in items:
        seen[item["anchor"]] += 1
        count = seen[item["anchor"]]
        item["identity"] = item["anchor"] if count == 1 else f"{item['anchor']}#{count}"
    return items


def _static_summary(unit, category, signals, settings, total=0):
    label = f"{unit.kind} {unit.qualname}"
    details = ", ".join(dict.fromkeys(s.detail for s in signals))
    if category == "sleep":
        loop = " (a call inside a loop repeats, so this is a lower bound)" if any(s.in_loop for s in signals) else ""
        return (
            f"{label} waits on a real clock: {details}; one execution path sleeps at least {_fmt(total)}s{loop}, which "
            f"exceeds max_sleep_seconds {_fmt(settings['max_sleep_seconds'])}; the wait is paid on every run of that path."
        )
    if category == "network-call":
        return (
            f"{label} makes {len(signals)} real network call(s) with no visible mocking: {details}; "
            f"exceeds max_network_calls {_fmt(settings['max_network_calls'])}."
        )
    largest = max(s.amount for s in signals)
    return (
        f"{label} materializes a large in-memory payload: {details}; {_fmt(int(largest))} bytes exceeds "
        f"max_fixture_bytes {_fmt(settings['max_fixture_bytes'])}."
    )


def _parse_static(source, cache=None):
    """Return (_Module, None) or (None, reason) for one static source (memoized per evaluation)."""
    key = id(source)
    if cache is not None and key in cache:
        return cache[key]
    outcome = _parse_static_uncached(source)
    if cache is not None:
        cache[key] = outcome
    return outcome


def _parse_static_uncached(source):
    locator, content = source.get("locator"), source.get("content")
    if not isinstance(locator, str) or not isinstance(content, str):
        return None, "static source needs a string locator and content"
    if not locator.endswith(".py"):
        return None, f"unsupported language; {CHECK_ID} v{DETECTOR_VERSION} static mode supports Python (.py) only"
    if not is_test_path(locator):
        return None, ("not a test module (expected test_*.py, *_test.py, conftest.py or a tests/ directory); "
                      "TST-12 evaluates test code only")
    try:
        return _Module(locator, content), None
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return None, f"could not be parsed ({type(error).__name__}); not evaluated"


# --------------------------------------------------------------------------- dispatch


@dataclass(frozen=True)
class _Outcome:
    evaluated: bool
    findings: tuple = ()
    omitted_reason: str | None = None


def _ancestor_conftests(path, sources, cache):
    """Parsed conftest.py modules supplied anywhere in the payload whose directory contains `path`."""
    parents = set(PurePosixPath(path).parents)
    found = []
    for source in sources:
        if not isinstance(source, dict) or source.get("kind") != STATIC_KIND:
            continue
        locator = source.get("locator")
        if not isinstance(locator, str) or PurePosixPath(locator).name != "conftest.py" or locator == path:
            continue
        if PurePosixPath(locator).parent in parents:
            module, _ = _parse_static(source, cache)
            if module is not None:
                found.append(module)
    return found


def _evaluate_scope(scope_id, sources, context, all_sources, cache):
    statics = [s for s in sources if s.get("kind") == STATIC_KIND]
    artifacts = [s for s in sources if s.get("kind") == ARTIFACT_KIND]
    if not statics and not artifacts:
        return _Outcome(False, omitted_reason=(
            f"{scope_id}: no static test source or test artifact supplied; TST-12 requires a Python test "
            "module (static) or normalized test-run artifact data"))
    if len(statics) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple static sources supplied; evaluation requires exactly one")
    if not statics and len(artifacts) > 1:
        return _Outcome(False, omitted_reason=f"{scope_id}: multiple test artifacts supplied; evaluation requires exactly one")

    findings = []
    if statics:
        source = statics[0]
        settings, reason = _read_settings(context, STATIC_SETTING_KEYS)
        if settings is None:
            return _Outcome(False, omitted_reason=f"{scope_id}: missing or invalid context settings for static mode: {reason}")
        module, reason = _parse_static(source, cache)
        if module is None:
            return _Outcome(False, omitted_reason=f"{scope_id}: {reason}")
        try:
            items = _static_items(module, settings, _ancestor_conftests(source["locator"], all_sources, cache), source)
        except Exception as error:  # a detector bug must not become a clean claim for this file
            return _Outcome(False, omitted_reason=f"{scope_id}: static check failed ({type(error).__name__}); not evaluated")
        findings.extend(items)

    if artifacts:
        settings, reason = _read_settings(context, ARTIFACT_SETTING_KEYS)
        if settings is None:
            return _Outcome(False, omitted_reason=f"{scope_id}: Missing or invalid context settings: {reason}")
        if statics:
            path = statics[0]["locator"]

            def check(test_id):
                if test_id.split("::", 1)[0] != path:
                    return f"artifact test_id {test_id!r} does not belong to {path}"
                return None
        else:
            def check(test_id):
                if scope_id != f"test:{test_id}":
                    return f"scope id {scope_id!r} does not match test_id (expected 'test:{test_id}')"
                return None
        seen_tests = set()
        for source in artifacts:
            problems = _artifact_problems(source.get("data"), check)
            test_id = source.get("data", {}).get("test_id") if isinstance(source.get("data"), dict) else None
            if not problems and test_id in seen_tests:
                problems = [f"duplicate artifact for {test_id}"]
            if problems:
                return _Outcome(False, omitted_reason=f"{scope_id}: " + "; ".join(problems))
            seen_tests.add(test_id)
            identity = f"{IDENTITY}:{test_id}" if statics else IDENTITY
            finding = _artifact_finding(source, settings, identity)
            if finding:
                findings.append(finding)
    return _Outcome(True, findings=tuple(findings))


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == "1.0", "unsupported schema_version; contract v1 is required")
    _require(
        payload.get("detector_version") == DETECTOR_VERSION,
        f"unsupported detector_version {payload.get('detector_version')!r}; this detector implements {DETECTOR_VERSION}",
    )
    for field in IDENTITY_FIELDS:
        _require(field in payload, f"input is missing required field {field}")
    scope = payload["scope"]
    sources = payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")
    sources = [s for s in sources if isinstance(s, dict)]

    evaluated, findings, limitations = [], [], []
    used_static = False
    cache = {}
    for scope_id in scope:
        scope_sources = [s for s in sources if s.get("scope_id") == scope_id]
        outcome = _evaluate_scope(scope_id, scope_sources, payload["context"], sources, cache)
        if not outcome.evaluated:
            limitations.append(outcome.omitted_reason)
            continue
        evaluated.append(scope_id)
        used_static = used_static or any(s.get("kind") == STATIC_KIND for s in scope_sources)
        for item in outcome.findings:
            findings.append({
                "fingerprint": fingerprint(payload["repository_id"], CHECK_ID, scope_id, item["identity"]),
                "scope_id": scope_id,
                "identity": item["identity"],
                "summary": item["summary"],
                "confidence": item["confidence"],
                "recommendation": RECOMMENDATION,
                "references": list(REFERENCES),
                "evidence": item["evidence"],
            })
    if used_static:
        limitations.append(STATIC_LIMITATION)
    limitations.append(GENERAL_LIMITATION)

    if not evaluated:
        status = "unavailable"
    elif len(evaluated) == len(scope):
        status = "completed"
    else:
        status = "partial"

    result = {field: payload[field] for field in IDENTITY_FIELDS}
    result.update(
        kind="result",
        status=status,
        coverage={"evaluated_scope": evaluated, "limitations": limitations},
        findings=findings if evaluated else [],
        measurements=[],
    )
    return result
