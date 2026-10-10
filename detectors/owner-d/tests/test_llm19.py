"""Behavioral tests for the LLM-19 detector (issue #212, OQ-10).

Static mode (LLM-19-S01..S10) reads vLLM/TGI serving configs and Python engine constructors; artifact mode
(LLM-19-A01..A09) reads a normalized inference-metrics summary. Every result is checked with `validate_pair`.
All inputs are synthetic.
"""

import contextlib
import copy
import io
import json
import sys
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli  # noqa: E402
from owner_d import llm19  # noqa: E402
from owner_d.llm19 import (  # noqa: E402
    CHECK_ID,
    DETECTOR_VERSION,
    LIMITATION,
    REFERENCE_SETTINGS,
    RUNTIME_UNAVAILABLE,
    EvaluationError,
    evaluate,
    fingerprint,
)
from scanner.adapters.owner_d import select_by_parse  # noqa: E402
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair  # noqa: E402

REPO = "github:AWS-env/example"

VLLM_DOCKERFILE = """\
FROM vllm/vllm-openai:v0.11.0
ENV HF_HOME=/models
CMD ["--model", "meta-llama/Llama-3.1-8B-Instruct", \\
     "--dtype", "float32", \\
     "--enforce-eager", \\
     "--no-enable-prefix-caching"]
"""

VLLM_COMPOSE = """\
services:
  llm:
    image: vllm/vllm-openai:v0.11.0
    command: --model Qwen/Qwen2.5-7B-Instruct --dtype=float --max-model-len 8192
    ports:
      - "8000:8000"
  web:
    image: example/web:1.0
"""

TGI_K8S = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tgi
spec:
  template:
    spec:
      containers:
        - name: tgi
          args:
            - --model-id
            - meta-llama/Llama-3.1-8B-Instruct
            - --cuda-graphs
            - "0"
          env:
            - name: CUDA_GRAPHS
              value: "0"
          image: ghcr.io/huggingface/text-generation-inference:3.0.1
"""

VLLM_PYTHON = """\
import torch
from vllm import LLM as Engine
from vllm.engine.arg_utils import AsyncEngineArgs

llm = Engine(
    model="meta-llama/Llama-3.1-8B-Instruct",
    dtype=torch.float32,
    enable_prefix_caching=False,
)
args = AsyncEngineArgs(model="x", enforce_eager=True)
"""

VLLM_CLEAN = """\
#!/usr/bin/env bash
# vllm serve x --enforce-eager   (old debugging command, commented out)
exec vllm serve meta-llama/Llama-3.1-8B-Instruct \\
  --dtype bfloat16 \\
  --enable-prefix-caching \\
  --enforce-eager=false \\
  --gpu-memory-utilization 0.9
"""

ARTIFACT = {
    "server_id": "llama-8b-prod",
    "engine": "vllm",
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "window_seconds": 3600,
    "requests": 12000,
    "preemptions": 840,
    "kv_cache_usage_max": 0.99,
    "prefix_cache_hit_rate": 0.02,
    "max_model_len": 131072,
    "max_request_tokens": 6000,
}
HEALTHY = dict(ARTIFACT, preemptions=12, kv_cache_usage_max=0.8, prefix_cache_hit_rate=0.6, max_model_len=16384)


def static_source(locator, content):
    return {"source_id": f"src:{locator}", "scope_id": f"file:{locator}", "kind": "static", "locator": locator,
            "content": content}


def artifact_source(data, scope_id=None, source_id="artifact-0"):
    server = data.get("server_id", "x") if isinstance(data, dict) else "x"
    return {"source_id": source_id, "scope_id": scope_id or f"inference:{server}", "kind": "artifact",
            "locator": f"llm-19.json from GitHub Actions run 1-1: {server}", "data": data}


def make_input(sources, scope=None, context=None):
    return {
        "schema_version": "1.0",
        "kind": "input",
        "repository_id": REPO,
        "scan_id": "scan-llm19-001",
        "commit_sha": "1919191919191919191919191919191919191919",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": dict(REFERENCE_SETTINGS) if context is None else context,
        "scope": scope if scope is not None else list(dict.fromkeys(s["scope_id"] for s in sources)),
        "sources": sources,
    }


def run(*sources, **kwargs):
    payload = make_input(list(sources), **kwargs)
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def identities(result):
    return sorted(finding["identity"] for finding in result["findings"])


def line_of(content, needle):
    return next(number for number, line in enumerate(content.splitlines(), 1) if needle in line)


class StaticPositiveTests(unittest.TestCase):
    def test_s01_vllm_dockerfile_flags_each_explicit_setting_with_exact_evidence(self):
        _, result = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(identities(result), ["vllm:eager-mode", "vllm:fp32-dtype", "vllm:prefix-caching-disabled"])
        by_id = {f["identity"]: f for f in result["findings"]}
        lines = VLLM_DOCKERFILE.splitlines()
        for identity, needle, confidence in [("vllm:fp32-dtype", '"--dtype"', "medium"),
                                             ("vllm:eager-mode", "--enforce-eager", "high"),
                                             ("vllm:prefix-caching-disabled", "--no-enable-prefix-caching", "high")]:
            with self.subTest(identity):
                finding = by_id[identity]
                number = line_of(VLLM_DOCKERFILE, needle)
                self.assertEqual(finding["confidence"], confidence)
                self.assertEqual(finding["evidence"], [{"source_id": "src:deploy/Dockerfile", "kind": "static",
                                                        "locator": "deploy/Dockerfile", "line_start": number,
                                                        "value": lines[number - 1]}])
                self.assertEqual(finding["recommendation"], llm19.RECOMMENDATIONS[identity.split(":")[1]])
                self.assertTrue(all(ref.startswith("https://") for ref in finding["references"]))
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        self.assertIn(RUNTIME_UNAVAILABLE, result["coverage"]["limitations"])

    def test_s02_compose_command_string_flags_fp32_alias(self):
        _, result = run(static_source("docker-compose.yml", VLLM_COMPOSE))
        self.assertEqual(identities(result), ["vllm:fp32-dtype"])
        self.assertIn("--dtype float", result["findings"][0]["summary"])
        self.assertEqual(result["findings"][0]["evidence"][0]["line_start"], 4)

    def test_s03_tgi_kubernetes_args_and_env_disable_cuda_graphs(self):
        _, result = run(static_source("k8s/tgi.yaml", TGI_K8S))
        self.assertEqual(identities(result), ["tgi:eager-mode", "tgi:eager-mode#2"])
        evidence = [f["evidence"][0] for f in result["findings"]]
        self.assertEqual([e["line_start"] for e in evidence], [13, 16])
        self.assertEqual(evidence[0]["value"], "            - --cuda-graphs\n            - \"0\"")  # flag + value lines
        self.assertEqual(evidence[1]["value"], "            - name: CUDA_GRAPHS\n              value: \"0\"")

    def test_s04_python_engine_constructors_with_aliases(self):
        _, result = run(static_source("serving/engine.py", VLLM_PYTHON))
        self.assertEqual(identities(result), ["vllm:eager-mode", "vllm:fp32-dtype", "vllm:prefix-caching-disabled"])
        by_id = {f["identity"]: f for f in result["findings"]}
        self.assertEqual(by_id["vllm:fp32-dtype"]["evidence"][0]["value"], "    dtype=torch.float32,")
        self.assertIn("LLM(dtype=torch.float32)", by_id["vllm:fp32-dtype"]["summary"])
        self.assertIn("AsyncEngineArgs(enforce_eager=True)", by_id["vllm:eager-mode"]["summary"])

    def test_s05_shell_and_env_forms(self):
        script = "python -m vllm.entrypoints.openai.api_server --model x --enforce_eager\n"
        dotenv = "MODEL_ID=x\nCUDA_GRAPHS=0\n"
        dockerfile = "FROM ghcr.io/huggingface/text-generation-inference:3.0\nENV CUDA_GRAPHS=0 MAX_TOTAL_TOKENS=4096\n"
        _, result = run(static_source("bin/serve.sh", script), static_source("deploy/Dockerfile", dockerfile))
        self.assertEqual([(f["scope_id"], f["identity"]) for f in result["findings"]],
                         [("file:bin/serve.sh", "vllm:eager-mode"), ("file:deploy/Dockerfile", "tgi:eager-mode")])
        with self.assertRaises(llm19.Unsupported):  # an env file alone does not launch a server
            llm19.parse(".env", dotenv)


class StaticNegativeTests(unittest.TestCase):
    def test_s06_clean_vllm_launch_completes_without_findings(self):
        _, result = run(static_source("deploy/serve.sh", VLLM_CLEAN))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:deploy/serve.sh"])

    def test_s07_runtime_values_and_other_engines_are_not_judged(self):
        content = ("services:\n"
                   "  vllm:\n    image: vllm/vllm-openai:latest\n    command: --model x --dtype ${DTYPE}\n"
                   "  " + "\n  ".join(f"pad{i}: {{}}" for i in range(8)) + "\n"
                   "  sglang:\n    image: lmsysorg/sglang:latest\n"
                   "    command: python -m sglang.launch_server --model x --dtype float32\n")
        _, result = run(static_source("compose.yaml", content))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_s08_noqa_suppresses_a_setting(self):
        content = VLLM_PYTHON.replace("enforce_eager=True)", "enforce_eager=True)  # noqa: LLM-19")
        _, result = run(static_source("serving/engine.py", content))
        self.assertNotIn("vllm:eager-mode", identities(result))

    def test_tgi_dtype_and_unrelated_values_are_not_flagged(self):
        content = ("FROM ghcr.io/huggingface/text-generation-inference:3.0\n"
                   'CMD ["--model-id", "x", "--dtype", "bfloat16", "--cuda-graphs", "1,2,4,8"]\n')
        _, result = run(static_source("Dockerfile", content))
        self.assertEqual(result["findings"], [])


class StaticCoverageTests(unittest.TestCase):
    def test_s09_files_without_self_hosted_inference_are_unsupported(self):
        for locator, content in [("Dockerfile", "FROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n"),
                                 ("app.py", "import anthropic\nclient = anthropic.Anthropic()\n"),
                                 ("llm.py", 'CMD = "vllm serve x --enforce-eager"\n'),
                                 ("README.md", "Run `vllm serve x --enforce-eager`.\n"),
                                 ("deploy.yaml", "# image: vllm/vllm-openai\nimage: example/web\n")]:
            with self.subTest(locator):
                with self.assertRaises(llm19.Unsupported):
                    llm19.parse(locator, content)
                _, result = run(static_source(locator, content))
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn("unsupported file type", result["coverage"]["limitations"][0])

    def test_s10_development_and_ci_paths_are_not_evaluated(self):
        for locator in ("dev/Dockerfile", "examples/serve.sh", "docker-compose.dev.yml", ".github/workflows/ci.yml",
                        "tests/serve.py"):
            content = VLLM_PYTHON if locator.endswith(".py") else VLLM_DOCKERFILE.replace("FROM ", "# x\nFROM ")
            if not locator.endswith((".py", "Dockerfile")):
                content = "vllm serve x --enforce-eager\n"
            with self.subTest(locator):
                with self.assertRaises(llm19.NotEvaluated):
                    llm19.parse(locator, content)
                _, result = run(static_source(locator, content))
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("development/test/example/CI", result["coverage"]["limitations"][0])

    def test_unparseable_python_is_not_reported_clean(self):
        _, result = run(static_source("serve.py", "from vllm import LLM\nLLM(model=\n"))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("could not be parsed (SyntaxError)", result["coverage"]["limitations"][0])

    def test_partial_when_one_file_is_out_of_scope(self):
        _, result = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE), static_source("notes.txt", "hello\n"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:deploy/Dockerfile"])

    def test_scanner_selects_only_self_hosting_files(self):
        files = [("deploy/Dockerfile", VLLM_DOCKERFILE), ("app.py", "print(1)\n"), ("README.md", "vllm serve x\n"),
                 ("examples/serve.sh", "vllm serve x\n"), ("k8s/tgi.yaml", TGI_K8S)]
        selected, declined = select_by_parse(llm19, files)
        self.assertEqual([path for path, _ in selected], ["deploy/Dockerfile", "k8s/tgi.yaml"])
        self.assertEqual(sum(declined.values()), 1)  # examples/serve.sh: declined with a reason, not clean
        selected, _ = select_by_parse(llm19, [("app.py", "print(1)\n"), ("Dockerfile", "FROM python:3.12\n")])
        self.assertEqual(selected, [])  # nothing self-hosted: the scanner reports not_applicable

    def test_identity_and_fingerprint_ignore_line_numbers(self):
        _, first = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE))
        _, moved = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE.replace("ENV HF_HOME=/models\n",
                                                                                 "ENV HF_HOME=/models\nENV A=1\n")))
        self.assertEqual([f["fingerprint"] for f in first["findings"]], [f["fingerprint"] for f in moved["findings"]])
        finding = first["findings"][0]
        self.assertEqual(finding["fingerprint"], fingerprint(REPO, CHECK_ID, finding["scope_id"], finding["identity"]))
        self.assertEqual(finding["fingerprint"],
                         shared_fingerprint(REPO, CHECK_ID, finding["scope_id"], finding["identity"]))


class ArtifactTests(unittest.TestCase):
    def test_a01_positive_flags_each_threshold_with_cited_fields(self):
        _, result = run(artifact_source(ARTIFACT))
        self.assertEqual(result["status"], "completed")
        by_id = {f["identity"]: f for f in result["findings"]}
        self.assertEqual(sorted(by_id), ["kv-cache-saturation", "low-prefix-cache-hit-rate", "oversized-context",
                                         "preemptions"])
        self.assertEqual({k: f["confidence"] for k, f in by_id.items()},
                         {"preemptions": "high", "kv-cache-saturation": "medium",
                          "low-prefix-cache-hit-rate": "low", "oversized-context": "medium"})
        self.assertEqual([(e["field"], e["value"]) for e in by_id["preemptions"]["evidence"]],
                         [("preemptions", 840), ("requests", 12000)])
        self.assertEqual([(e["field"], e["value"]) for e in by_id["oversized-context"]["evidence"]],
                         [("max_model_len", 131072), ("max_request_tokens", 6000)])
        self.assertIn("7.00%", by_id["preemptions"]["summary"])
        self.assertIn(llm19.ARTIFACT_LIMITATION, result["coverage"]["limitations"])
        self.assertNotIn(RUNTIME_UNAVAILABLE, result["coverage"]["limitations"])
        self.assertEqual(result["measurements"], [])

    def test_a02_healthy_server_has_no_findings(self):
        _, result = run(artifact_source(HEALTHY))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))

    def test_a03_thresholds_are_strict(self):
        boundary = dict(ARTIFACT, requests=1000, preemptions=10, kv_cache_usage_max=0.95, prefix_cache_hit_rate=0.05,
                        max_model_len=16000, max_request_tokens=4000)
        _, result = run(artifact_source(boundary))
        self.assertEqual((result["status"], result["findings"]), ("completed", []))
        over = dict(boundary, preemptions=11, kv_cache_usage_max=0.951, prefix_cache_hit_rate=0.049,
                    max_model_len=16001)
        _, result = run(artifact_source(over))
        self.assertEqual(len(result["findings"]), 4)

    def test_a04_optional_fields_absent_skip_their_rules(self):
        data = {k: v for k, v in ARTIFACT.items() if k not in ("prefix_cache_hit_rate", "max_model_len",
                                                               "max_request_tokens", "model")}
        _, result = run(artifact_source(data))
        self.assertEqual(identities(result), ["kv-cache-saturation", "preemptions"])
        _, result = run(artifact_source(dict(data, prefix_cache_hit_rate=None)))
        self.assertEqual(identities(result), ["kv-cache-saturation", "preemptions"])

    def test_a05_malformed_artifacts_are_not_reported_clean(self):
        cases = [
            ({"server_id": "x"}, "missing fields"),
            (dict(ARTIFACT, gpu="H100"), "unknown fields: gpu"),
            (dict(ARTIFACT, requests=True), "requests must be a nonnegative integer"),
            (dict(ARTIFACT, preemptions=-1), "preemptions must be a nonnegative integer"),
            (dict(ARTIFACT, kv_cache_usage_max=99), "kv_cache_usage_max must be a fraction"),
            (dict(ARTIFACT, kv_cache_usage_max=None), "kv_cache_usage_max must be a fraction"),
            (dict(ARTIFACT, prefix_cache_hit_rate="high"), "prefix_cache_hit_rate must be a fraction"),
            (dict(ARTIFACT, window_seconds=0), "window_seconds must be a positive number"),
            (dict(ARTIFACT, engine=""), "engine must be a nonempty string"),
            ({k: v for k, v in ARTIFACT.items() if k != "max_request_tokens"}, "supplied together"),
            (dict(ARTIFACT, max_request_tokens=200000), "cannot exceed max_model_len"),
            (dict(ARTIFACT, max_model_len=1.5), "max_model_len must be a positive integer"),
        ]
        for data, reason in cases:
            with self.subTest(reason):
                _, result = run(artifact_source(data, scope_id="inference:llama-8b-prod"))
                self.assertEqual((result["status"], result["findings"]), ("unavailable", []))
                self.assertIn(reason, result["coverage"]["limitations"][0])
        # The contract schema already rejects non-object data; the detector still refuses it on its own.
        result = evaluate(make_input([artifact_source("not an object", scope_id="inference:x")]))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("artifact data must be an object", result["coverage"]["limitations"][0])

    def test_a06_scope_must_match_server_id(self):
        _, result = run(artifact_source(ARTIFACT, scope_id="inference:other"))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("does not match server_id", result["coverage"]["limitations"][0])

    def test_a07_too_few_requests_is_not_evaluated(self):
        _, result = run(artifact_source(dict(ARTIFACT, requests=99, preemptions=50)))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("below min_requests 100", result["coverage"]["limitations"][0])

    def test_a08_missing_or_invalid_settings(self):
        for context, reason in [({}, "missing required context settings"),
                                (dict(REFERENCE_SETTINGS, max_kv_cache_usage=2),
                                 "max_kv_cache_usage must be a fraction"),
                                (dict(REFERENCE_SETTINGS, min_requests=0), "min_requests must be a positive integer"),
                                (dict(REFERENCE_SETTINGS, max_context_headroom_ratio=0.5), "at least 1")]:
            with self.subTest(reason):
                _, result = run(artifact_source(ARTIFACT), context=context)
                self.assertEqual(result["status"], "unavailable")
                self.assertIn(reason, result["coverage"]["limitations"][0])

    def test_a09_mixed_duplicate_and_missing_sources(self):
        mixed = [static_source("deploy/Dockerfile", VLLM_DOCKERFILE),
                 dict(artifact_source(ARTIFACT), scope_id="file:deploy/Dockerfile")]
        _, result = run(*mixed)
        self.assertIn("supplied together", result["coverage"]["limitations"][0])
        _, result = run(artifact_source(ARTIFACT), artifact_source(ARTIFACT, source_id="artifact-1"))
        self.assertIn("multiple inference-metrics artifacts", result["coverage"]["limitations"][0])
        _, result = run(scope=["inference:none"])
        self.assertIn("no static serving config or inference-metrics artifact", result["coverage"]["limitations"][0])
        self.assertEqual(result["status"], "unavailable")

    def test_static_and_artifact_scopes_in_one_payload(self):
        _, result = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE), artifact_source(HEALTHY))
        self.assertEqual(result["status"], "completed")
        self.assertEqual({f["scope_id"] for f in result["findings"]}, {"file:deploy/Dockerfile"})
        self.assertIn(LIMITATION, result["coverage"]["limitations"])
        self.assertIn(llm19.ARTIFACT_LIMITATION, result["coverage"]["limitations"])


class ArtifactInputsTests(unittest.TestCase):
    def test_builds_one_scope_per_server_with_reference_settings(self):
        context, scope, sources, notes = llm19.artifact_inputs(
            {"servers": [ARTIFACT, HEALTHY | {"server_id": "b"}, ARTIFACT, {"engine": "vllm"}]}, "llm-19.json", "7-1")
        self.assertEqual(context, REFERENCE_SETTINGS)
        self.assertEqual(scope, ["inference:llama-8b-prod", "inference:b"])
        self.assertEqual(sources[0]["locator"], "llm-19.json from GitHub Actions run 7-1: llama-8b-prod")
        self.assertEqual(len(notes), 2)
        self.assertIn("duplicate server_id", notes[0])
        self.assertIn("servers[3] has no usable server_id", notes[1])

    def test_settings_override_and_rejections(self):
        context, *_ = llm19.artifact_inputs({"servers": [ARTIFACT], "settings": {"min_requests": 10}}, "n", "1-1")
        self.assertEqual(context["min_requests"], 10)
        for data, reason in [([], "needs"), ({"servers": []}, "needs"),
                             ({"servers": [ARTIFACT], "extra": 1}, "unknown"),
                             ({"servers": [ARTIFACT], "settings": {"max_cpu": 1}}, "settings may only contain"),
                             ({"servers": [ARTIFACT], "settings": {"max_kv_cache_usage": 5}}, "invalid settings"),
                             ({"servers": [{"engine": "vllm"}]}, "no usable servers")]:
            with self.subTest(reason):
                with self.assertRaisesRegex(llm19.ArtifactRejected, reason):
                    llm19.artifact_inputs(data, "llm-19.json", "1-1")


class ContractTests(unittest.TestCase):
    def test_registered_with_reference_settings(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], llm19)
        self.assertEqual(REFERENCE_SETTINGS, {"min_requests": 100, "max_preemption_ratio": 0.01,
                                              "max_kv_cache_usage": 0.95, "min_prefix_cache_hit_rate": 0.05,
                                              "max_context_headroom_ratio": 4})
        self.assertFalse(hasattr(llm19, "SUPPORTED_KIND"))  # repository scans run the static mode

    def test_rejects_wrong_payloads(self):
        payload = make_input([static_source("Dockerfile", VLLM_DOCKERFILE)])
        for change in ({"check_id": "LLM-18"}, {"detector_version": "0.9.0"}, {"kind": "result"},
                       {"scope": []}, {"context": []}):
            with self.subTest(change):
                with self.assertRaises(EvaluationError):
                    evaluate(dict(copy.deepcopy(payload), **change))

    def test_invented_evidence_fails_validation(self):
        payload, result = run(static_source("deploy/Dockerfile", VLLM_DOCKERFILE))
        result["findings"][0]["evidence"][0]["value"] = "--enforce-eager"
        with self.assertRaises(ContractError):
            validate_pair(payload, result)

    def test_cli_round_trip_on_committed_fixture(self):
        path = Path(__file__).resolve().parent / "fixtures" / "llm19" / "llm19-01-static-and-artifact-input.json"
        payload = json.loads(path.read_text())
        self.assertEqual(payload["sources"], make_input([static_source("deploy/Dockerfile", VLLM_DOCKERFILE),
                                                         artifact_source(ARTIFACT)])["sources"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([str(path)]), 0)
        result = json.loads(out.getvalue())
        validate_pair(payload, result)
        self.assertEqual((result["status"], len(result["findings"])), ("completed", 7))

if __name__ == "__main__":
    unittest.main()
