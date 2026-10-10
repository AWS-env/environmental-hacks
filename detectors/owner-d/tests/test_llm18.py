"""Behavioral tests for the LLM-18 detector (issue #211)."""

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

from owner_d import cli, llm18
from owner_d.llm18 import CHECK_ID, DETECTOR_VERSION, EvaluationError, evaluate, fingerprint
from shared.contracts.validation import ContractError, fingerprint as shared_fingerprint, validate_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm18"
REPOSITORY_ID = "github:AWS-env/example"
POSITIVE = ("infra/main.tf", "agent/app.py", "deploy/agent-deployment.yaml", "web/agent.ts")
TF_SETTING = "setting/BEDROCK_REGION@us-west-2"
TF_KB = "aws_opensearchserverless_collection.kb:provider/aws.west@us-west-2"
PY_HOST = "endpoint/abc123xyz.eu-west-1.aoss.amazonaws.com"
PY_CLIENT = "<module>:client/bedrock-runtime@us-west-2"
PY_SIGNER = "<module>:AWSV4SignerAuth@eu-west-1"
K8S_SETTING = "setting/EMBEDDING_REGION@ap-southeast-2"
TS_CLIENT = "client/BedrockRuntimeClient@eu-central-1"
WORKLOAD = ("infra/main.tf", 'provider "aws" {\n  region = "us-east-1"\n}\n')


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
        "scan_id": "scan-llm18-001",
        "commit_sha": "dddddddddddddddddddddddddddddddddddddddd",
        "check_id": CHECK_ID,
        "detector_version": DETECTOR_VERSION,
        "context": {"format": "iac+config+source"},
        "scope": scope if scope is not None else [f"file:{name}" for name in names],
        "sources": sources,
    }


def run(*names, extra=()):
    """Evaluate fixture files plus inline (name, content) sources."""
    sources = [static_source(name) for name in names] + [static_source(name, content) for name, content in extra]
    payload = make_input(sources=sources, scope=[source["scope_id"] for source in sources])
    result = evaluate(payload)
    validate_pair(payload, result)
    return payload, result


def identities(result):
    return [f["identity"] for f in result["findings"]]


def limitations(result):
    return " ".join(result["coverage"]["limitations"])


def judged(name, content, workload=WORKLOAD):
    """Identities flagged in one inline file next to a us-east-1 workload declaration."""
    _, result = run(extra=[workload, (name, content)])
    return identities(result)


class Llm18PositiveTests(unittest.TestCase):
    """LLM18-01: dependencies pinned to another Region than the declared workload Region are flagged."""

    EXPECTED = {
        TF_SETTING: ("infra/main.tf", 24, 24, "low"),
        TF_KB: ("infra/main.tf", 30, 34, "medium"),
        PY_HOST: ("agent/app.py", 9, 9, "medium"),
        PY_CLIENT: ("agent/app.py", 11, 11, "medium"),
        PY_SIGNER: ("agent/app.py", 13, 13, "medium"),
        K8S_SETTING: ("deploy/agent-deployment.yaml", 15, 16, "low"),
        TS_CLIENT: ("web/agent.ts", 7, 9, "medium"),
    }

    def test_findings_have_exact_evidence_and_coverage(self):
        _, result = run(*POSITIVE)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in POSITIVE])
        self.assertEqual(result["measurements"], [])
        self.assertEqual(sorted(identities(result)), sorted(self.EXPECTED))
        for finding in result["findings"]:
            name, start, end, confidence = self.EXPECTED[finding["identity"]]
            lines = (FIXTURES / name).read_text().splitlines()
            with self.subTest(identity=finding["identity"]):
                self.assertEqual(finding["scope_id"], f"file:{name}")
                self.assertEqual(finding["confidence"], confidence)
                self.assertEqual(finding["fingerprint"],
                                 fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", finding["identity"]))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["line_start"], start)
                self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                self.assertTrue(finding["references"])

    def test_summaries_name_both_regions_and_the_declaration(self):
        _, result = run(*POSITIVE)
        by_id = {f["identity"]: f for f in result["findings"]}
        client = by_id[PY_CLIENT]["summary"]
        self.assertIn("boto3 client 'bedrock-runtime' puts a Bedrock model/agent endpoint in us-west-2", client)
        self.assertIn("workload Region declared in the payload is us-east-1 (deploy/agent-deployment.yaml "
                      "(AWS_REGION), infra/main.tf (provider \"aws\"))", client)
        self.assertIn("Candidate for reviewer confirmation", client)
        self.assertIn("aws_opensearchserverless_collection.kb (provider aws.west)", by_id[TF_KB]["summary"])
        self.assertIn("vector store", by_id[TF_KB]["recommendation"])
        self.assertIn("model endpoint", by_id[TS_CLIENT]["recommendation"])
        self.assertIn("model endpoint", by_id[K8S_SETTING]["recommendation"])  # EMBEDDING_* is an LLM endpoint
        self.assertIn("LLM endpoint", by_id[K8S_SETTING]["summary"])

    def test_every_workload_declaration_form(self):
        dep = ("agent/llm.py", 'import boto3\nbedrock = boto3.client("bedrock-runtime", region_name="us-west-2")\n')
        for name, content in (
            ("serverless.yml", "service: agent\nprovider:\n  name: aws\n  region: us-east-1\n"),
            ("samconfig.toml", 'version = 0.1\n[default.deploy.parameters]\nregion = "us-east-1"\n'
                               '[prod.deploy.parameters]\nregion = "eu-west-1"\n'),
            ("samconfig.yaml", "version: 0.1\ndefault:\n  deploy:\n    parameters:\n      region: us-east-1\n"),
            ("cdk/app.py", "import aws_cdk as cdk\napp = cdk.App()\n"
                           "Agent(app, 'a', env=cdk.Environment(account='1', region='us-east-1'))\n"),
            ("cdk/app2.py", "import aws_cdk as cdk\napp = cdk.App()\nAgent(app, 'a', env={'region': 'us-east-1'})\n"),
            ("bin/app.ts", "import * as cdk from 'aws-cdk-lib';\nnew AgentStack(app, 'A', {\n"
                           "  env: { account: '1', region: 'us-east-1' },\n});\n"),
            (".env", "AWS_REGION=us-east-1\n"),
            ("Dockerfile", "FROM python:3.12\nENV AWS_DEFAULT_REGION=us-east-1\n"),
            ("task.json", '{"environment": [{"name": "AWS_REGION", "value": "us-east-1"}]}'),
            ("infra/vars.tf", 'variable "region" {\n  default = "eu-west-1"\n}\nlocals {\n  r = var.region\n}\n'
                              'provider "aws" {\n  region = local.r\n}\n'),
        ):
            with self.subTest(name=name):
                expected = ["<module>:client/bedrock-runtime@us-west-2"]
                _, result = run(extra=[(name, content), dep])
                self.assertEqual(result["status"], "completed", limitations(result))
                self.assertEqual(identities(result), expected)
        # terraform.tfvars overrides the variable default: two values, so the variable is unresolved
        _, result = run(extra=[("infra/vars.tf", 'variable "region" {\n  default = "us-west-2"\n}\n'
                                                 'provider "aws" {\n  region = var.region\n}\n'),
                               ("infra/prod.tfvars", 'region = "us-east-1"\n'), dep])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no workload Region is declared", limitations(result))

    def test_every_dependency_form(self):
        for name, content, expected in (
            ("a.py", 'import boto3\nc = boto3.client("sagemaker-runtime", "eu-west-1")\n',
             ["<module>:client/sagemaker-runtime@eu-west-1"]),
            ("a.py", 'import boto3\ns = boto3.Session(region_name="us-west-2")\n\n\ndef f():\n'
                     '    return s.client("bedrock-agent-runtime")\n', ["f:client/bedrock-agent-runtime@us-west-2"]),
            ("a.py", 'from langchain_aws import ChatBedrock\nREGION = "us-west-2"\nllm = ChatBedrock(region_name=REGION)\n',
             ["<module>:ChatBedrock@us-west-2"]),
            ("a.py", 'from anthropic import AnthropicBedrock\nc = AnthropicBedrock(aws_region="eu-central-1")\n',
             ["<module>:AnthropicBedrock@eu-central-1"]),
            ("a.py", 'from pinecone import ServerlessSpec\nspec = ServerlessSpec(cloud="aws", region="us-west-2")\n',
             ["<module>:ServerlessSpec@us-west-2"]),
            ("a.py", 'from requests_aws4auth import AWS4Auth\nauth = AWS4Auth(k, s, "eu-west-1", "es")\n',
             ["<module>:AWS4Auth@eu-west-1"]),
            ("a.py", 'import boto3\nc = boto3.client(\n    "bedrock-runtime",\n'
                     '    endpoint_url="https://bedrock-runtime.us-west-2.amazonaws.com",\n'
                     '    region_name="us-west-2",\n)\n', ["<module>:client/bedrock-runtime@us-west-2"]),
            ("a.py", 'URL = "https://runtime.sagemaker.eu-west-1.amazonaws.com/endpoints/x/invocations"\n',
             ["endpoint/runtime.sagemaker.eu-west-1.amazonaws.com"]),
            ("a.py", 'from langchain_postgres import PGVector\n'
                     'DSN = "postgresql://u@db.c1.eu-west-1.rds.amazonaws.com:5432/kb"\n',
             ["endpoint/db.c1.eu-west-1.rds.amazonaws.com"]),
            ("config.json", '{"bedrockRegion": "us-west-2"}', ["setting/bedrockRegion@us-west-2"]),
            ("app.env", "LLM_REGION=eu-west-1\n", ["setting/LLM_REGION@eu-west-1"]),
            ("kb.tf", 'variable "kb_region" {\n  default = "us-west-2"\n}\nresource "aws_lambda_function" "f" {\n'
                      '  environment {\n    variables = {\n      PINECONE_REGION = var.kb_region\n    }\n  }\n}\n',
             ["setting/PINECONE_REGION@us-west-2"]),
            ("kb.tf", 'provider "aws" {\n  alias  = "w"\n  region = "us-west-2"\n}\n'
                      'data "aws_bedrock_foundation_model" "m" {\n  provider = aws.w\n  model_id = "x"\n}\n',
             ["data.aws_bedrock_foundation_model.m:provider/aws.w@us-west-2"]),
            ("src/kb.ts", 'const c = new OpenSearchClient({ region: "eu-west-1" });\n',
             ["client/OpenSearchClient@eu-west-1"]),
        ):
            with self.subTest(content=content[:60]):
                self.assertEqual(judged(name, content), expected)


class Llm18NegativeTests(unittest.TestCase):
    """LLM18-02: same-Region, unrelated and unresolved Regions are clean."""

    def test_same_region_dependencies_are_clean(self):
        _, result = run(extra=[WORKLOAD, ("a.py", 'import boto3\nc = boto3.client("bedrock-runtime", '
                                                  'region_name="us-east-1")\n')])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:infra/main.tf", "file:a.py"])
        self.assertEqual(result["findings"], [])
        self.assertNotIn("were not flagged", limitations(result))  # same Region, not a failover

    def test_other_services_and_unresolved_regions_are_not_judged(self):
        for name, content in (
            ("a.py", 'import boto3\ns3 = boto3.client("s3", region_name="us-west-2")\n'),
            ("a.py", 'import os, boto3\nc = boto3.client("bedrock-runtime", region_name=os.getenv("R", "us-west-2"))\n'),
            ("a.py", 'import boto3\nr = "us-west-2"\nr = "eu-west-1"\nc = boto3.client("bedrock-runtime", region_name=r)\n'),
            ("a.py", 'import boto3\nc = boto3.client("bedrock-runtime", region_name=f"us-west-{n}")\n'),
            ("a.py", 'import boto3\nc = boto3.client("bedrock-runtime")\n'
                     'c.converse(modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0", messages=[])\n'),
            ("a.py", 'DSN = "postgresql://u@db.c1.eu-west-1.rds.amazonaws.com:5432/app"\n'),  # no pgvector
            ("a.py", '"""Calls bedrock-runtime.us-west-2.amazonaws.com."""\nimport boto3\n'),  # docstring
            ("a.py", 'from pinecone import ServerlessSpec\nspec = ServerlessSpec(cloud="gcp", region="us-west-2")\n'),
            ("a.env", "# BEDROCK_REGION=us-west-2\nREGION=us-west-2\nS3_REGION=us-west-2\n"),
            ("serverless.yml", "provider:\n  region: us-east-1\n  environment:\n"
                               "    BEDROCK_REGION: ${env:BEDROCK_REGION, 'us-west-2'}\n"),
            ("a.ts", "// const c = new BedrockRuntimeClient({ region: 'us-west-2' });\n"
                     "const s = new S3Client({ region: 'us-west-2' });\n"
                     "const n = new BedrockRuntimeClient({ credentials: { a: 1 }, region: 'us-west-2' });\n"),
            ("kb.tf", 'provider "aws" {\n  alias  = "w"\n  region = "us-west-2"\n}\n'
                      'resource "aws_s3_bucket" "b" {\n  provider = aws.w\n}\n'),
        ):
            with self.subTest(content=content[:60]):
                _, result = run(extra=[WORKLOAD, (name, content)])
                self.assertEqual(result["findings"], [])

    def test_cdk_app_pinning_only_its_global_stack_declares_no_workload(self):
        app = ("import * as cdk from 'aws-cdk-lib';\nnew WafStack(app, 'Waf', { env: { region: 'us-east-1' } });\n"
               "new AgentStack(app, 'Agent', { env: { account: a, region: process.env.CDK_DEFAULT_REGION } });\n")
        dep = ("cdk.json", '{"context": {"bedrockRegion": "us-west-2"}}')
        _, result = run(extra=[("bin/app.ts", app), dep])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no workload Region is declared", limitations(result))
        pinned = app.replace("process.env.CDK_DEFAULT_REGION", "'eu-west-1'")
        _, result = run(extra=[("bin/app.ts", pinned), dep])
        self.assertIn("several workload Regions", limitations(result))


class Llm18ExceptionTests(unittest.TestCase):
    """LLM18-03: failover clients, noqa, multi-Region payloads and development/example/CI paths."""

    def test_same_service_in_the_workload_region_is_treated_as_failover(self):
        content = ('import boto3\nprimary = boto3.client("bedrock-runtime", region_name="us-east-1")\n'
                   'fallback = boto3.client("bedrock-runtime", region_name="us-west-2")\n'
                   'auth_region = "eu-west-1"\nsearch = boto3.client("opensearchserverless", region_name=auth_region)\n')
        _, result = run(extra=[WORKLOAD, ("a.py", content)])
        self.assertEqual(identities(result), ["<module>:client/opensearchserverless@eu-west-1"])
        self.assertIn("1 cross-Region dependency setting(s) were not flagged: the same file also uses that service in "
                      "the workload Region us-east-1", limitations(result))

    def test_noqa_on_or_above_the_flagged_line(self):
        client = 'c = boto3.client("bedrock-runtime", region_name="us-west-2")'
        for marker, flagged in (("  # noqa: LLM-18", False), ("  # noqa: LLM18", False), ("  # noqa", False),
                                ("  # noqa: E501", True)):
            with self.subTest(marker=marker):
                self.assertEqual(bool(judged("a.py", f"import boto3\n{client}{marker}\n")), flagged)
        self.assertEqual(judged("a.env", "# model only offered there  # noqa: LLM-18\nBEDROCK_REGION=us-west-2\n"), [])
        ts = "const c = new BedrockRuntimeClient({ region: 'us-west-2' });"
        for prefix, flagged in (("// noqa: LLM-18 (model only offered there)\n", False), ("// noqa\n", False),
                                ("// noqa: E501\n", True), ("", True)):
            with self.subTest(prefix=prefix):
                self.assertEqual(bool(judged("a.ts", prefix + ts + "\n")), flagged)
        self.assertEqual(judged("a.ts", ts + " // noqa: LLM-18\n"), [])

    def test_several_or_no_workload_regions_are_unavailable(self):
        dep = ("a.py", 'import boto3\nc = boto3.client("bedrock-runtime", region_name="us-west-2")\n')
        _, result = run(extra=[WORKLOAD, (".env", "AWS_REGION=eu-west-1\n"), dep])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertIn("several workload Regions (eu-west-1 (.env (AWS_REGION)); us-east-1 (infra/main.tf (provider "
                      "\"aws\")))", limitations(result))
        _, result = run(extra=[dep])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no workload Region is declared in the payload", limitations(result))
        _, result = run(extra=[WORKLOAD])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("declares only the workload Region", limitations(result))

    def test_development_example_and_ci_paths_are_not_evaluated(self):
        dep = 'import boto3\nc = boto3.client("bedrock-runtime", region_name="us-west-2")\n'
        for name, content, reason in (
            ("tests/test_agent.py", dep, "development/test"),
            ("dev/agent.py", dep, "development/test"),
            ("examples/agent.py", dep, "example/template"),
            (".env.example", "BEDROCK_REGION=us-west-2\n", "example/template"),
            (".env.template", "BEDROCK_REGION=us-west-2\n", "example/template"),
            (".github/workflows/deploy.yml", "env:\n  AWS_REGION: us-west-2\n", "CI/CD pipeline"),
        ):
            with self.subTest(name=name):
                _, result = run(extra=[WORKLOAD, (name, content)])
                self.assertEqual(result["status"], "unavailable")  # the workload file alone has nothing to judge
                self.assertTrue(result["coverage"]["limitations"][1].startswith(f"file:{name}: not evaluated"))
                self.assertIn(reason, limitations(result))


class Llm18IncompleteTests(unittest.TestCase):
    def test_missing_source_is_unavailable_or_partial(self):
        """LLM18-04: requested scope without a static source is not evaluated."""
        payload = make_input(sources=[], scope=["file:infra/main.tf"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["coverage"]["evaluated_scope"], [])
        self.assertIn("no static source", limitations(result))
        payload = make_input(*POSITIVE, scope=[f"file:{name}" for name in POSITIVE] + ["file:x.tf"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["findings"]), len(Llm18PositiveTests.EXPECTED))

    def test_malformed_inputs_are_never_clean(self):
        """LLM18-05: broken Python, HCL or serverless YAML next to valid files gives partial with the reason."""
        for name, content, reason in (
            ("a.py", 'import boto3\nc = boto3.client("bedrock-runtime", region_name="us-west-2"\n', "invalid Python"),
            ("b.tf", 'provider "aws" {\n  region = "us-east-1"\n', "unbalanced"),
            ("serverless.yml", "provider:\n\tregion: us-east-1\n", "tab indentation"),
        ):
            with self.subTest(name=name):
                _, result = run(*POSITIVE, extra=[(name, content)])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(len(result["findings"]), len(Llm18PositiveTests.EXPECTED))
                self.assertIn(reason, limitations(result))

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        for name, content in (
            ("README.md", "BEDROCK_REGION=us-west-2\n"),
            ("main.tf", 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'),
            ("backend.tf", 'terraform {\n  backend "s3" {\n    region = "us-west-2"\n  }\n}\n'),
            ("app.py", 'import boto3\ns3 = boto3.client("s3", region_name="us-west-2")\n'),
            ("app.py", 'import boto3\nc = boto3.client("bedrock-runtime")\n'),
            ("k8s/deploy.yaml", "kind: Deployment\nmetadata:\n  labels:\n    zone: us-east-1a\n"),
            ("script.sh", "export AWS_REGION=us-east-1\n"),
        ):
            with self.subTest(name=name), self.assertRaises(llm18.Unsupported):
                llm18.parse(name, content)


class Llm18BoundaryTests(unittest.TestCase):
    """LLM18-06: Region syntax, aliases, normalization and stable fingerprints."""

    def test_region_literals_must_be_exact(self):
        for value in ("us-east-1a", "us-west", "useast1", "us-west-22"):
            with self.subTest(value=value):
                self.assertEqual(judged("a.env", f"BEDROCK_REGION={value}\n"), [])
        self.assertEqual(judged("a.env", "BEDROCK_REGION=US-WEST-2\n"), ["setting/BEDROCK_REGION@us-west-2"])
        self.assertEqual(judged("a.env", "BEDROCK_REGION=us-gov-west-1\n"), ["setting/BEDROCK_REGION@us-gov-west-1"])

    def test_aliased_provider_region_must_resolve(self):
        resource = 'resource "aws_opensearch_domain" "d" {\n  provider = aws.w\n}\n'
        for provider in ('provider "aws" {\n  alias  = "w"\n  region = var.missing\n}\n',
                         'provider "aws" {\n  alias  = "w"\n  region = "us-east-1"\n}\n'):
            with self.subTest(provider=provider):
                _, result = run(extra=[WORKLOAD, ("kb.tf", provider + resource),
                                       ("a.env", "LLM_REGION=eu-west-1\n")])
                self.assertEqual(identities(result), ["setting/LLM_REGION@eu-west-1"])
        _, result = run(extra=[WORKLOAD, ("kb.tf", 'provider "aws" {\n  alias  = "w"\n  region = var.missing\n}\n'
                                                   + resource), ("a.env", "LLM_REGION=eu-west-1\n")])
        self.assertIn("1 Terraform resource(s) on an aliased provider whose region is not a literal were not judged, "
                      "e.g. kb.tf: aws_opensearch_domain.d (provider aws.w)", limitations(result))

    def test_terraform_values_are_module_scoped(self):
        dep = ("agent/llm.py", 'import boto3\nc = boto3.client("bedrock-runtime", region_name="us-west-2")\n')
        provider = ("infra/providers.tf", 'provider "aws" {\n  region = var.region\n}\n')
        default = 'variable "region" {\n  default = "us-east-1"\n}\n'
        _, result = run(extra=[provider, ("infra/variables.tf", default), dep])
        self.assertEqual(identities(result), ["<module>:client/bedrock-runtime@us-west-2"])
        self.assertIn('us-east-1 (infra/providers.tf (provider "aws"))', result["findings"][0]["summary"])
        _, result = run(extra=[provider, ("other/variables.tf", default), dep])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no workload Region is declared", limitations(result))
        module = 'locals {{\n  region = "{}"\n}}\nprovider "aws" {{\n  region = local.region\n}}\n'
        _, result = run(extra=[("a/main.tf", module.format("us-east-1")), ("b/main.tf", module.format("eu-west-1")), dep])
        self.assertIn("several workload Regions (eu-west-1 (b/main.tf", limitations(result))

    def test_repeated_identities_are_numbered(self):
        content = "BEDROCK_REGION=us-west-2\nBEDROCK_REGION=us-west-2\n"
        self.assertEqual(judged("a.env", content),
                         ["setting/BEDROCK_REGION@us-west-2", "setting/BEDROCK_REGION@us-west-2#2"])

    def test_fingerprints_do_not_change_when_lines_move(self):
        _, before = run(*POSITIVE)
        shifted = [(name, "# moved\n\n\n" + (FIXTURES / name).read_text()) for name in ("infra/main.tf", "agent/app.py")]
        _, after = run("deploy/agent-deployment.yaml", "web/agent.ts", extra=shifted)
        key = lambda f: f["identity"]  # noqa: E731
        before_sorted, after_sorted = sorted(before["findings"], key=key), sorted(after["findings"], key=key)
        self.assertEqual([f["fingerprint"] for f in before_sorted], [f["fingerprint"] for f in after_sorted])
        for old, new in zip(before_sorted, after_sorted):
            shift = 3 if old["scope_id"] in ("file:infra/main.tf", "file:agent/app.py") else 0
            self.assertEqual(old["evidence"][0]["line_start"] + shift, new["evidence"][0]["line_start"])


class Llm18ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:agent/app.py", PY_CLIENT)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run(*POSITIVE)
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = 'BEDROCK_REGION = "invented"'
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("infra/main.tf")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input(*POSITIVE)
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "llm18-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(*POSITIVE))

    def test_registered_in_cli(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], llm18)


class Llm18CliTests(unittest.TestCase):
    def test_cli_writes_valid_llm18_result(self):
        input_path = FIXTURES / "llm18-01-positive-input.json"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main([str(input_path), "-o", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
        validate_pair(json.loads(input_path.read_text()), result)
        self.assertEqual(result["check_id"], CHECK_ID)
        self.assertEqual(sorted(identities(result)), sorted(Llm18PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
