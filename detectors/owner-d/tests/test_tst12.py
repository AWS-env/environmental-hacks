"""Behavioral tests for the TST-12 detector (issue #267).

Static mode (TST-12-01..08) evaluates Python test source; artifact mode (TST-12-A01..A07)
evaluates normalized CI test-run artifacts. Every result is checked with `validate_pair`.
"""

import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli  # noqa: E402
from owner_d.tst12 import (  # noqa: E402
    CHECK_ID,
    DETECTOR_VERSION,
    IDENTITY,
    STATIC_LIMITATION,
    EvaluationError,
    evaluate,
    fingerprint,
)
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tst12"
STATIC = FIXTURES / "static"
REPO = "github:AWS-env/example"
STATIC_CONTEXT = {"max_sleep_seconds": 0.1, "max_network_calls": 0, "max_fixture_bytes": 10485760}
ARTIFACT_CONTEXT = {
    "max_duration_seconds": 10,
    "max_sleep_seconds": 0.1,
    "max_network_calls": 0,
    "max_fixture_bytes": 10485760,
    "max_setup_seconds": 2,
}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def run_case(case):
    payload = load(f"{case}-input.json")
    return payload, evaluate(payload)


def static_source(fixture, locator, source_id=None):
    content = fixture if "\n" in fixture else (STATIC / fixture).read_text()
    return {
        "source_id": source_id or "static-" + locator.replace("/", "-").replace(".", "-"),
        "scope_id": f"file:{locator}",
        "kind": "static",
        "locator": locator,
        "content": content,
    }


def make_payload(sources, scope=None, context=None):
    scope = scope if scope is not None else list(dict.fromkeys(s["scope_id"] for s in sources))
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPO,
        "scan_id": "scan-tst12-static",
        "commit_sha": "2" * 40,
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": copy.deepcopy(context if context is not None else STATIC_CONTEXT),
        "scope": scope,
        "sources": sources,
    }


def run_static(sources, scope=None, context=None):
    payload = make_payload(sources, scope, context)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def line(source, text):
    """(line_start, exact line) for the single source line whose stripped text equals `text`."""
    matches = [(n, value) for n, value in enumerate(source["content"].splitlines(), 1) if value.strip() == text]
    assert len(matches) == 1, (text, matches)
    return matches[0]


def evidence_lines(finding):
    return [(e["line_start"], e["value"]) for e in finding["evidence"]]


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


class Tst12StaticPositiveTests(unittest.TestCase):
    """TST-12-01: constant sleeps, unmocked external calls and big fixtures in unit tests."""

    def setUp(self):
        self.payload, self.result = run_case("tst12-01-positive")
        validate_pair(self.payload, self.result)
        self.source = self.payload["sources"][0]
        self.findings = by_identity(self.result)

    def test_committed_input_matches_fixture_source(self):
        self.assertEqual(self.source["content"], (STATIC / "positive.py").read_text())

    def test_completed_with_one_finding_per_unit_and_signal(self):
        self.assertEqual(self.result["status"], "completed")
        self.assertEqual(self.result["coverage"]["evaluated_scope"], ["file:tests/test_client.py"])
        self.assertIn(STATIC_LIMITATION, self.result["coverage"]["limitations"])
        self.assertEqual(
            list(self.findings),
            [
                "large_payload:large-allocation",
                "test_fetch_package:network-call",
                "test_retry_waits_between_attempts:sleep",
                "TestUploader.test_upload:network-call",
                "TestUploader.test_async_backoff:sleep",
            ],
        )
        self.assertEqual(self.result["measurements"], [])

    def test_exact_evidence_lines(self):
        src = self.source
        expected = {
            "large_payload:large-allocation": [line(src, 'return b"\\0" * 64 * 1024 * 1024')],
            "test_fetch_package:network-call": [line(src, 'response = requests.get(f"{API}/requests/json", timeout=5)')],
            "test_retry_waits_between_attempts:sleep": [line(src, "time.sleep(RETRY_DELAY)"), line(src, "time.sleep(0.2)")],
            "TestUploader.test_upload:network-call": [line(src, "client.list_buckets()")],
            "TestUploader.test_async_backoff:sleep": [line(src, "await asyncio.sleep(0.5)")],
        }
        for identity, lines in expected.items():
            self.assertEqual(evidence_lines(self.findings[identity]), lines, identity)
            for evidence in self.findings[identity]["evidence"]:
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["locator"], "tests/test_client.py")

    def test_summaries_confidence_and_fingerprints(self):
        sleep = self.findings["test_retry_waits_between_attempts:sleep"]
        self.assertEqual(sleep["confidence"], "high")
        self.assertIn("time.sleep(1.5), time.sleep(0.2)", sleep["summary"])
        self.assertIn("at least 1.7s", sleep["summary"])
        self.assertIn("inside a loop", sleep["summary"])
        network = self.findings["test_fetch_package:network-call"]
        self.assertIn("requests.get() to api.pypi.org", network["summary"])
        self.assertEqual(network["confidence"], "medium")  # no conftest.py supplied
        aws = self.findings["TestUploader.test_upload:network-call"]
        self.assertIn("AWS SDK .list_buckets()", aws["summary"])
        allocation = self.findings["large_payload:large-allocation"]
        self.assertIn("67108864 bytes exceeds max_fixture_bytes 10485760", allocation["summary"])
        for identity, finding in self.findings.items():
            self.assertEqual(finding["fingerprint"], fingerprint(REPO, CHECK_ID, "file:tests/test_client.py", identity))
            self.assertTrue(finding["references"] and all(r.startswith("https://") for r in finding["references"]))


class Tst12StaticClientTests(unittest.TestCase):
    """TST-12-01 variants: other HTTP clients and socket APIs."""

    SOURCE = (
        "import socket\n"
        "from urllib.request import urlopen\n"
        "\n"
        "import aiohttp\n"
        "import httpx\n"
        "\n"
        "\n"
        "async def test_clients(app):\n"
        "    urlopen('https://pypi.org/simple')\n"
        "    with httpx.Client(base_url='https://api.pypi.org') as client:\n"
        "        client.get('/simple')\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get('https://files.pythonhosted.org/')\n"
        "    socket.create_connection(('pypi.org', 443))\n"
        "    async with httpx.AsyncClient(app=app, base_url='https://pypi.org') as local:\n"
        "        await local.get('/simple')\n"
    )

    def test_each_external_client_call_is_cited(self):
        source = static_source(self.SOURCE, "tests/test_clients.py")
        _, result = run_static([source])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(list(by_identity(result)), ["test_clients:network-call"])
        self.assertEqual(
            evidence_lines(result["findings"][0]),
            [line(source, "urlopen('https://pypi.org/simple')"),
             line(source, "client.get('/simple')"),
             line(source, "await session.get('https://files.pythonhosted.org/')"),
             line(source, "socket.create_connection(('pypi.org', 443))")],
        )

    def test_mock_transport_anywhere_in_module_suppresses_network(self):
        mocked = self.SOURCE + "\n\ndef test_transport(app):\n    httpx.Client(transport=httpx.MockTransport(app))\n"
        _, result = run_static([static_source(mocked, "tests/test_clients.py")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Tst12StaticNegativeTests(unittest.TestCase):
    def test_similar_negative_and_mocked_modules_are_clean(self):
        """TST-12-02: local/dynamic URLs, trivial or unknown sleeps, helpers, mocks."""
        _, result = run_static([
            static_source("negative.py", "tests/test_service.py"),
            static_source("mocked.py", "tests/test_mocked.py"),
        ])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_service.py", "file:tests/test_mocked.py"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_mocked_module_flags_once_the_mocks_are_removed(self):
        source = (STATIC / "mocked.py").read_text()
        unmocked = source.replace("import responses\n", "").replace("@responses.activate\n", "")
        unmocked = unmocked.replace('    responses.get("https://api.pypi.org/pypi/requests/json", json={})\n', "")
        unmocked = unmocked.replace('@mock.patch("time.sleep")\n', "")
        _, result = run_static([static_source(unmocked, "tests/test_mocked.py")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            sorted(by_identity(result)),
            ["test_fetch_package_is_mocked:network-call", "test_retry_with_patched_sleep:sleep"],
        )

    def test_legitimate_exceptions_are_not_flagged(self):
        """TST-12-03: integration/slow/network markers, live classes, skip-guards and noqa."""
        _, result = run_static([static_source("exceptions.py", "tests/test_exceptions.py")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_exception_markers_are_what_suppress_the_findings(self):
        source = (STATIC / "exceptions.py").read_text()
        stripped = source.replace("@pytest.mark.integration\n", "").replace("# noqa: TST-12 -", "#")
        _, result = run_static([static_source(stripped, "tests/test_exceptions.py")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            sorted(by_identity(result)),
            ["test_documented_wait:sleep", "test_pypi_round_trip:network-call", "test_pypi_round_trip:sleep"],
        )

    def test_integration_directory_is_excluded(self):
        _, result = run_static([static_source("positive.py", "tests/integration/test_client.py")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


class Tst12StaticIncompleteTests(unittest.TestCase):
    def test_missing_static_source_is_unavailable(self):
        """TST-12-04: a requested file with no supplied source is never clean."""
        _, result = run_static([], scope=["file:tests/test_client.py"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static test source or test artifact supplied" in item
                            for item in result["coverage"]["limitations"]))

    def test_missing_static_settings_make_scope_unavailable(self):
        context = dict(STATIC_CONTEXT)
        del context["max_fixture_bytes"]
        _, result = run_static([static_source("positive.py", "tests/test_client.py")], context=context)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("max_fixture_bytes" in item for item in result["coverage"]["limitations"]))

    def test_unparseable_unsupported_and_non_test_files_are_omitted(self):
        """TST-12-05: syntax error, non-Python and non-test files stay out of evaluated scope."""
        sources = [
            static_source("positive.py", "tests/test_client.py"),
            static_source("broken.py", "tests/test_broken.py"),
            static_source("test('x', () => {});\nsetTimeout(done, 5000);\n", "tests/client.test.js"),
            static_source("import time\n\ndef test_x():\n    time.sleep(5)\n", "src/app.py"),
        ]
        _, result = run_static(sources)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:tests/test_client.py"])
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:tests/test_client.py"})
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:tests/test_broken.py: could not be parsed (SyntaxError)", limitations)
        self.assertIn("file:tests/client.test.js: unsupported language", limitations)
        self.assertIn("file:src/app.py: not a test module", limitations)

    def test_multiple_static_sources_for_one_scope_are_unavailable(self):
        first = static_source("positive.py", "tests/test_client.py", "a")
        second = static_source("negative.py", "tests/test_client.py", "b")
        _, result = run_static([first, second])
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("multiple static sources" in item for item in result["coverage"]["limitations"]))


class Tst12StaticBoundaryTests(unittest.TestCase):
    """TST-12-06: equality is never flagged; identities ignore line numbers."""

    CONTEXT = {"max_sleep_seconds": 0.5, "max_network_calls": 1, "max_fixture_bytes": 1048576}

    def test_values_at_limit_are_not_flagged_and_just_over_are(self):
        source = static_source("boundary.py", "tests/test_boundary.py")
        _, result = run_static([source], context=self.CONTEXT)
        self.assertEqual(result["status"], "completed")
        findings = by_identity(result)
        self.assertEqual(
            list(findings),
            ["test_sleep_over_limit:sleep", "test_two_calls_over_limit:network-call", "test_payload_over_limit:large-allocation"],
        )
        self.assertEqual(
            evidence_lines(findings["test_sleep_over_limit:sleep"]),
            [(22, "    time.sleep(0.25)"), line(source, "time.sleep(0.3)")],
        )
        self.assertIn("at least 0.55s", findings["test_sleep_over_limit:sleep"]["summary"])
        self.assertEqual(
            evidence_lines(findings["test_two_calls_over_limit:network-call"]),
            [(31, '    requests.get("https://api.pypi.org/simple")'),
             line(source, 'requests.head("https://files.pythonhosted.org/")')],
        )
        self.assertEqual(
            evidence_lines(findings["test_payload_over_limit:large-allocation"]),
            [line(source, "assert bytearray(1048577)")],
        )

    def test_fingerprints_survive_line_movement(self):
        source = static_source("positive.py", "tests/test_client.py")
        moved = dict(source, content="\n\n# moved\n" + source["content"])
        _, before = run_static([source])
        _, after = run_static([moved])
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [e["line_start"] + 3 for f in before["findings"] for e in f["evidence"]],
            [e["line_start"] for f in after["findings"] for e in f["evidence"]],
        )

    def test_redefined_test_gets_an_ordinal_identity(self):
        text = "import time\n\ndef test_x():\n    time.sleep(1)\n\ndef test_x():\n    time.sleep(2)\n"
        _, result = run_static([static_source(text, "tests/test_dupe.py")])
        self.assertEqual([f["identity"] for f in result["findings"]], ["test_x:sleep", "test_x:sleep#2"])
        self.assertEqual(len({f["fingerprint"] for f in result["findings"]}), 2)


class Tst12CoexistenceTests(unittest.TestCase):
    """TST-12-07: static source plus optional artifacts in one payload."""

    def artifact(self, test_id, scope_id, **overrides):
        data = {
            "test_id": test_id,
            "framework": "pytest",
            "duration_seconds": 14.2,
            "sleep_seconds": 1.7,
            "network_call_count": 1,
            "fixture_bytes": 0,
            "setup_seconds": 0.1,
        }
        data.update(overrides)
        return {
            "source_id": "artifact-" + test_id.replace("/", "-").replace(":", "-"),
            "scope_id": scope_id,
            "kind": "artifact",
            "locator": f"pytest --durations artifact: {test_id}",
            "data": data,
        }

    def test_file_scope_cites_static_and_artifact_evidence(self):
        static = static_source("positive.py", "tests/test_client.py")
        artifact = self.artifact("tests/test_client.py::test_fetch_package", "file:tests/test_client.py")
        payload, result = run_static([static, artifact], context=ARTIFACT_CONTEXT)
        self.assertEqual(result["status"], "completed")
        findings = by_identity(result)
        artifact_identity = f"{IDENTITY}:tests/test_client.py::test_fetch_package"
        self.assertIn("test_fetch_package:network-call", findings)
        self.assertIn(artifact_identity, findings)
        self.assertEqual(
            [(e["kind"], e["field"], e["value"]) for e in findings[artifact_identity]["evidence"]],
            [("artifact", "duration_seconds", 14.2), ("artifact", "sleep_seconds", 1.7),
             ("artifact", "network_call_count", 1)],
        )
        self.assertEqual(
            findings[artifact_identity]["fingerprint"],
            fingerprint(REPO, CHECK_ID, "file:tests/test_client.py", artifact_identity),
        )

    def test_artifact_for_another_file_makes_the_scope_unavailable(self):
        static = static_source("positive.py", "tests/test_client.py")
        artifact = self.artifact("tests/test_other.py::test_x", "file:tests/test_client.py")
        _, result = run_static([static, artifact], context=ARTIFACT_CONTEXT)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("does not belong to tests/test_client.py" in item for item in result["coverage"]["limitations"]))

    def test_static_file_scope_and_artifact_test_scope_in_one_payload(self):
        static = static_source("negative.py", "tests/test_service.py")
        artifact = self.artifact("tests/test_api.py::test_fetch", "test:tests/test_api.py::test_fetch")
        _, result = run_static([static, artifact], context=ARTIFACT_CONTEXT)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([(f["scope_id"], f["identity"]) for f in result["findings"]],
                         [("test:tests/test_api.py::test_fetch", IDENTITY)])


class Tst12ConftestTests(unittest.TestCase):
    """TST-12-08: supplied ancestor conftest.py files can mock the network for a whole tree."""

    def test_ancestor_conftest_mocking_suppresses_network_findings(self):
        test = static_source("positive.py", "tests/test_client.py")
        conftest = static_source("conftest_mocking.py", "tests/conftest.py")
        _, result = run_static([test, conftest])
        self.assertEqual(result["status"], "completed")
        identities = [f["identity"] for f in result["findings"]]
        self.assertNotIn("test_fetch_package:network-call", identities)
        self.assertIn("test_retry_waits_between_attempts:sleep", identities)

    def test_sibling_conftest_does_not_apply(self):
        test = static_source("positive.py", "tests/unit/test_client.py")
        conftest = static_source("conftest_mocking.py", "tests/other/conftest.py")
        _, result = run_static([test, conftest])
        self.assertIn("test_fetch_package:network-call", by_identity(result))

    def test_supplied_non_mocking_conftest_raises_network_confidence(self):
        test = static_source("positive.py", "tests/test_client.py")
        conftest = static_source("import pytest\n\n\n@pytest.fixture\ndef token():\n    return 'x'\n", "conftest.py")
        _, result = run_static([test, conftest])
        self.assertEqual(by_identity(result)["test_fetch_package:network-call"]["confidence"], "high")


class Tst12ArtifactPositiveTests(unittest.TestCase):
    def test_positive_fixture_flags_heavy_test_work(self):
        """TST-12-A01."""
        payload, result = run_case("tst12-a01-positive")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertNotIn(STATIC_LIMITATION, result["coverage"]["limitations"])
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["scope_id"], payload["scope"][0])
        self.assertEqual(finding["identity"], IDENTITY)
        self.assertEqual(
            finding["fingerprint"],
            fingerprint(payload["repository_id"], CHECK_ID, payload["scope"][0], IDENTITY),
        )
        self.assertEqual(finding["confidence"], "high")
        self.assertIn("duration 14.2 exceeds 10", finding["summary"])
        self.assertIn("sleep 2.5 exceeds 0", finding["summary"])
        self.assertIn("network calls 3 exceeds 0", finding["summary"])
        self.assertTrue(finding["references"] and all(r.startswith("https://") for r in finding["references"]))
        self.assertEqual(
            {evidence["field"]: evidence["value"] for evidence in finding["evidence"]},
            {
                "duration_seconds": 14.2,
                "sleep_seconds": 2.5,
                "network_call_count": 3,
                "fixture_bytes": 52428800,
                "setup_seconds": 4.1,
            },
        )
        self.assertTrue(all(evidence["kind"] == "artifact" for evidence in finding["evidence"]))
        self.assertEqual(result["measurements"], [])


class Tst12ArtifactNegativeTests(unittest.TestCase):
    def test_negative_fixture_is_clean(self):
        """TST-12-A02."""
        payload, result = run_case("tst12-a02-negative")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["measurements"], [])

    def test_threshold_boundary_is_not_flagged(self):
        """TST-12-A03."""
        payload, result = run_case("tst12-a03-boundary")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], payload["scope"])
        self.assertEqual(result["findings"], [])


class Tst12ArtifactIncompleteTests(unittest.TestCase):
    def test_missing_artifact_is_unavailable_with_reason(self):
        """TST-12-A04."""
        payload, result = run_case("tst12-a04-missing-evidence")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static test source or test artifact supplied" in limitation
                            for limitation in result["coverage"]["limitations"]))

    def test_malformed_artifact_is_unavailable_with_explicit_reason(self):
        """TST-12-A05."""
        payload, result = run_case("tst12-a05-malformed")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("missing fields: sleep_seconds", limitations)
        self.assertIn("duration_seconds must be nonnegative", limitations)
        self.assertIn("network_call_count must be an integer", limitations)

    def test_partial_coverage_flags_only_evaluated_scope(self):
        """TST-12-A06."""
        payload, result = run_case("tst12-a06-partial")
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], [payload["scope"][0]])
        self.assertEqual([finding["scope_id"] for finding in result["findings"]], [payload["scope"][0]])
        self.assertTrue(any("no static test source or test artifact supplied" in limitation
                            for limitation in result["coverage"]["limitations"]))

    def test_missing_context_settings_make_result_unavailable(self):
        """TST-12-A07."""
        payload = load("tst12-a01-positive-input.json")
        del payload["context"]["max_network_calls"]
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("max_network_calls" in limitation for limitation in result["coverage"]["limitations"]))

    def test_artifact_mode_does_not_need_static_settings_only(self):
        payload = load("tst12-a01-positive-input.json")
        del payload["context"]["max_duration_seconds"]
        result = evaluate(payload)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("max_duration_seconds" in limitation for limitation in result["coverage"]["limitations"]))


class Tst12ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        self.assertEqual(
            fingerprint("github:AWS-env/example", CHECK_ID, "test:tests/test_api.py::test_fetch_users", IDENTITY),
            shared_fingerprint("github:AWS-env/example", CHECK_ID, "test:tests/test_api.py::test_fetch_users", IDENTITY),
        )

    def test_evaluation_is_deterministic(self):
        for case in ("tst12-01-positive", "tst12-a01-positive"):
            payload, first = run_case(case)
            self.assertEqual(first, evaluate(copy.deepcopy(payload)))

    def test_invented_artifact_evidence_is_rejected(self):
        payload, result = run_case("tst12-a01-positive")
        result["findings"][0]["evidence"][0]["value"] = 99
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run_case("tst12-01-positive")
        result["findings"][0]["evidence"][0]["line_start"] += 1
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_unsupported_detector_version_is_rejected(self):
        payload = load("tst12-01-positive-input.json")
        payload["detector_version"] = "1.0.0"
        with self.assertRaises(EvaluationError):
            evaluate(payload)


class Tst12CliTests(unittest.TestCase):
    def test_cli_writes_valid_tst12_results(self):
        for name in ("tst12-01-positive-input.json", "tst12-a01-positive-input.json"):
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "result.json"
                code = cli.main([str(FIXTURES / name), "-o", str(output)])
                self.assertEqual(code, 0)
                result = json.loads(output.read_text())
                validate_pair(load(name), result)
                self.assertTrue(result["findings"])

    def test_cli_rejects_unsupported_owner_d_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            payload = load("tst12-a01-positive-input.json")
            payload["check_id"] = "TST-11"
            bad.write_text(json.dumps(payload))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([str(bad)]), 1)


if __name__ == "__main__":
    unittest.main()
