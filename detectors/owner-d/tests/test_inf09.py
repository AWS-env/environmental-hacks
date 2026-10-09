"""Behavioral tests for the INF-09 detector (issue #176)."""

import contextlib
import copy
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

from owner_d import cli
from owner_d.inf09 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inf09"
REPOSITORY_ID = "github:AWS-env/example"


def static_source(name, content=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    return {
        "source_id": f"src:{name}",
        "scope_id": f"file:{name}",
        "kind": "static",
        "locator": name,
        "content": content,
    }


def make_input(*names, sources=None, scope=None):
    sources = [static_source(name) for name in names] if sources is None else sources
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPOSITORY_ID,
        "scan_id": "scan-inf09-001",
        "commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "dockerfile"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, **kwargs):
    payload = make_input(*names, **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def run_inline(name, content):
    return run(sources=[static_source(name, content)], scope=[f"file:{name}"])


def by_identity(result):
    return {finding["identity"]: finding for finding in result["findings"]}


class Inf09PositiveTests(unittest.TestCase):
    """INF09-01: avoidable layer content in shipped stages is flagged with exact evidence."""

    EXPECTED = {
        "positive.Dockerfile": {
            "base:full-base-image": (7, "medium", "full Debian variant"),
            "base:apt-lists-kept": (8, "medium", "/var/lib/apt/lists"),
            "base:apt-install-recommends": (9, "medium", "--no-install-recommends"),
            "base:build-toolchain": (10, "medium", "build-essential"),
            "base:pip-cache-kept": (12, "medium", "--no-cache-dir"),
            "base:pip-cache-kept#2": (13, "medium", "--no-cache-dir"),
        },
        "node.Dockerfile": {
            "stage-0:apk-cache-kept": (4, "medium", "--no-cache"),
            "stage-0:build-toolchain": (4, "medium", "g++, make"),
            "stage-0:node-dev-dependencies": (6, "low", "devDependencies"),
        },
        "go.Dockerfile": {
            "stage-0:toolchain-base-image": (2, "medium", "Go toolchain"),
        },
        "ubi.Containerfile": {
            "stage-0:dnf-cache-kept": (3, "medium", "dnf clean all"),
        },
    }

    def test_shipped_stage_waste_is_flagged_with_exact_lines(self):
        names = list(self.EXPECTED)
        _, result = run(*names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in names])
        self.assertEqual(result["measurements"], [])
        for name, expected in self.EXPECTED.items():
            scope_id = f"file:{name}"
            findings = by_identity({"findings": [f for f in result["findings"] if f["scope_id"] == scope_id]})
            self.assertEqual(set(findings), set(expected), name)
            lines = (FIXTURES / name).read_text().splitlines()
            for identity, (line, confidence, phrase) in expected.items():
                finding = findings[identity]
                with self.subTest(name=name, identity=identity):
                    self.assertEqual(finding["confidence"], confidence)
                    self.assertIn(phrase, finding["summary"] + " " + finding["recommendation"])
                    self.assertEqual(finding["fingerprint"], fingerprint(REPOSITORY_ID, CHECK_ID, scope_id, identity))
                    [evidence] = finding["evidence"]
                    self.assertEqual(evidence["kind"], "static")
                    self.assertEqual(evidence["locator"], name)
                    self.assertEqual(evidence["line_start"], line)
                    self.assertEqual(evidence["value"], lines[line - 1])
                    self.assertTrue(finding["references"])

    def test_builder_stage_is_not_flagged(self):
        """The `tools` stage installs git and runs the Go toolchain but is only used via COPY --from."""
        _, result = run("positive.Dockerfile")
        self.assertFalse(any(f["identity"].startswith("tools:") for f in result["findings"]))


class Inf09NegativeTests(unittest.TestCase):
    """INF09-02: the same steps done the lean way, and lean base tags, are clean."""

    def test_lean_dockerfile_is_clean(self):
        _, result = run("negative.Dockerfile")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:negative.Dockerfile"])
        self.assertEqual(result["findings"], [])

    def test_lean_or_unknown_base_images_are_clean(self):
        for base in (
            "python:3.12-slim-bookworm",
            "node:20-alpine",
            "docker.io/library/python:3.12-slim@sha256:abc",
            "gcr.io/distroless/python3",
            "ruby:3.3-slim",
            "${BASE_IMAGE}",
            "golang:1.22",  # toolchain image without a build step: the toolchain is the purpose
            "nginx:1.27",
        ):
            with self.subTest(base=base):
                _, result = run_inline("Dockerfile", f"ARG BASE_IMAGE\nFROM {base}\nCMD [\"app\"]\n")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["findings"], [])

    def test_full_tags_are_flagged(self):
        for base in ("python", "python:3", "python:3.12.4-bookworm", "node:lts", "ruby:3.3-bullseye",
                     "public.ecr.aws/docker/library/node:20"):
            with self.subTest(base=base):
                _, result = run_inline("Dockerfile", f"FROM {base}\nCMD [\"app\"]\n")
                self.assertEqual([f["identity"] for f in result["findings"]], ["stage-0:full-base-image"])
                self.assertEqual(result["findings"][0]["evidence"][0]["value"], f"FROM {base}")


class Inf09ExceptionTests(unittest.TestCase):
    """INF09-03: suppression comments, env/config opt-outs, cache mounts and dev images."""

    def test_suppressions_and_opt_outs_are_respected(self):
        _, result = run("exceptions.Dockerfile")
        self.assertEqual(result["status"], "completed")
        # `# hadolint ignore=DL3008` names a different rule, so DL3009 on line 9 still applies.
        self.assertEqual([f["identity"] for f in result["findings"]], ["stage-0:apt-lists-kept"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 9)

    def test_development_and_test_images_are_not_evaluated(self):
        cases = {
            "Dockerfile.dev": "FROM python:3.12\nRUN pip install flask\n",
            "docker/test/Dockerfile": "FROM python:3.12\nRUN pip install pytest\n",
            ".devcontainer/Dockerfile": "FROM python:3.12\n",
            "Dockerfile": "FROM python:3.12-slim AS app\nFROM app AS dev-envs\nRUN apt-get update\n",
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                _, result = run_inline(name, content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn("development/test", result["coverage"]["limitations"][0])


class Inf09IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_with_reason(self):
        """INF09-04: requested scope without a static source is not evaluated."""
        _, result = run(sources=[], scope=["file:service/Dockerfile"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertEqual(result["findings"], [])
        self.assertTrue(any("no static source" in item for item in result["coverage"]["limitations"]))

    def test_malformed_and_unsupported_files_make_result_partial(self):
        """INF09-05: parse failures and non-Dockerfiles are omitted, never reported clean."""
        _, result = run("positive.Dockerfile", "broken.Dockerfile", "app.py")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:positive.Dockerfile"])
        self.assertTrue(all(f["scope_id"] == "file:positive.Dockerfile" for f in result["findings"]))
        limitations = " ".join(result["coverage"]["limitations"])
        self.assertIn("file:broken.Dockerfile: could not be parsed (unknown instruction on line 4)", limitations)
        self.assertIn("file:app.py: unsupported file type", limitations)

    def test_unparseable_variants_are_unavailable(self):
        for content, reason in (
            ("# only a comment\n", "no FROM instruction"),
            ("RUN echo hi\nFROM alpine\n", "RUN before the first FROM"),
            ("FROM alpine\nRUN <<EOF\napk add curl\n", "unterminated heredoc"),
        ):
            with self.subTest(reason=reason):
                _, result = run_inline("Dockerfile", content)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["findings"], [])
                self.assertIn(reason, result["coverage"]["limitations"][0])


class Inf09BoundaryTests(unittest.TestCase):
    """INF09-06: identities, line movement and Dockerfile syntax boundaries."""

    def test_repeated_rule_in_one_stage_gets_distinct_identities(self):
        _, result = run("boundary.Dockerfile")
        self.assertEqual(
            [f["identity"] for f in result["findings"]],
            ["base:full-base-image", "base:pip-cache-kept", "base:pip-cache-kept#2"],
        )

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run("boundary.Dockerfile")
        shifted = "# moved\n\n\n" + (FIXTURES / "boundary.Dockerfile").read_text()
        _, after = run_inline("boundary.Dockerfile", shifted)
        self.assertEqual([f["fingerprint"] for f in before["findings"]], [f["fingerprint"] for f in after["findings"]])
        self.assertEqual(
            [f["evidence"][0]["line_start"] + 3 for f in before["findings"]],
            [f["evidence"][0]["line_start"] for f in after["findings"]],
        )

    def test_cleanup_must_be_in_the_same_run(self):
        content = "FROM debian:bookworm-slim\nRUN apt-get update\nRUN rm -rf /var/lib/apt/lists/*\n"
        _, result = run_inline("Dockerfile", content)
        self.assertEqual([f["identity"] for f in result["findings"]], ["stage-0:apt-lists-kept"])
        self.assertEqual(result["findings"][0]["evidence"][0]["value"], "RUN apt-get update")

    def test_opt_out_in_another_run_does_not_cover_this_run(self):
        content = (
            "FROM python:3.12-slim\n"
            "RUN pip install flask\n"
            "RUN pip install --no-cache-dir gunicorn\n"
            "RUN pip config set global.no-cache-dir false\n"
        )
        _, result = run_inline("Dockerfile", content)
        self.assertEqual([f["identity"] for f in result["findings"]], ["stage-0:pip-cache-kept"])
        self.assertEqual(result["findings"][0]["evidence"][0]["value"], "RUN pip install flask")

    def test_heredoc_escape_directive_and_comments_in_continuations(self):
        content = (
            "# escape=`\n"
            "FROM debian:bookworm-slim\n"
            "RUN apt-get update `\n"
            "# a comment inside the continuation\n"
            "    && apt-get install -y --no-install-recommends curl\n"
            "RUN <<EOF\n"
            "pip install requests\n"
            "EOF\n"
        )
        _, result = run_inline("Dockerfile", content)
        findings = by_identity(result)
        self.assertEqual(set(findings), {"stage-0:apt-lists-kept", "stage-0:pip-cache-kept"})
        self.assertEqual(findings["stage-0:apt-lists-kept"]["evidence"][0]["value"], "RUN apt-get update `")
        self.assertEqual(findings["stage-0:pip-cache-kept"]["evidence"][0]["value"], "pip install requests")

    def test_exec_form_and_env_prefixes_are_understood(self):
        content = (
            "FROM python:3.12-slim\n"
            "RUN [\"pip\", \"install\", \"flask\"]\n"
            "RUN DEBIAN_FRONTEND=noninteractive apt-get -y install --no-install-recommends tini\n"
        )
        _, result = run_inline("Dockerfile", content)
        self.assertEqual([f["identity"] for f in result["findings"]], ["stage-0:pip-cache-kept"])


class Inf09ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:positive.Dockerfile", "base:pip-cache-kept")
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run("positive.Dockerfile")
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = "FROM python:invented"
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("positive.Dockerfile")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input("positive.Dockerfile", "node.Dockerfile", "negative.Dockerfile")
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "inf09-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input("positive.Dockerfile"))


class Inf09CliTests(unittest.TestCase):
    def test_cli_writes_valid_inf09_result(self):
        input_path = FIXTURES / "inf09-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(len(result["findings"]), 6)


if __name__ == "__main__":
    unittest.main()
