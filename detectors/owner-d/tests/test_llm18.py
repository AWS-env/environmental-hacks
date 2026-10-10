"""Behavioral tests for the LLM-18 detector (issue #211). Fixtures are synthetic."""

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
POSITIVE = ("agent/samconfig.toml", "agent/app.py", "agent/template.yaml", "rag/main.tf", "rag/retriever.py")

SAM = '[default.deploy.parameters]\nstack_name = "agent"\nregion = "{}"\n'
CLIENT = 'import boto3\n\nclient = boto3.client("bedrock-runtime", region_name="{}")\n'
CLIENT_ID = "<module>:bedrock-runtime:client"


def static_source(name, content=None):
    if content is None:
        content = (FIXTURES / name).read_text()
    return {"source_id": f"src:{name}", "scope_id": f"file:{name}", "kind": "static", "locator": name,
            "content": content}


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
        "context": {"format": "python+iac"},
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


def workload(region="eu-west-1"):
    return ("samconfig.toml", SAM.format(region))


class Llm18PositiveTests(unittest.TestCase):
    """LLM18-01: dependencies pinned to another Region than the declared workload are flagged."""

    EXPECTED = {
        ("agent/app.py", "<module>:bedrock-runtime:client"): (14, 14, "medium"),
        ("agent/app.py", "<module>:AnthropicBedrock(aws_region)"): (18, 18, "medium"),
        ("agent/app.py", "<module>:bedrock-runtime:client#2"): (19, 22, "medium"),
        ("agent/app.py", "knowledge_base:bedrock-agent-runtime:client"): (27, 27, "medium"),
        ("agent/template.yaml", "setting/BEDROCK_REGION"): (13, 13, "medium"),
        ("agent/template.yaml", "endpoint/bedrock-runtime"): (16, 16, "medium"),
        ("rag/main.tf", "azurerm_cognitive_account.openai:location"): (13, 13, "low"),
        ("rag/retriever.py", "<module>:vertexai.init(location)"): (6, 6, "low"),
    }

    def test_findings_have_exact_evidence_and_coverage(self):
        _, result = run(*POSITIVE)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["evaluated_scope"], [f"file:{name}" for name in POSITIVE])
        self.assertEqual(result["measurements"], [])
        found = {(f["scope_id"][len("file:"):], f["identity"]): f for f in result["findings"]}
        self.assertEqual(sorted(found), sorted(self.EXPECTED))
        for (name, identity), finding in found.items():
            start, end, confidence = self.EXPECTED[(name, identity)]
            lines = (FIXTURES / name).read_text().splitlines()
            with self.subTest(identity=identity):
                self.assertEqual(finding["confidence"], confidence)
                self.assertEqual(finding["fingerprint"],
                                 fingerprint(REPOSITORY_ID, CHECK_ID, f"file:{name}", identity))
                [evidence] = finding["evidence"]
                self.assertEqual(evidence["kind"], "static")
                self.assertEqual(evidence["line_start"], start)
                self.assertEqual(evidence["value"], "\n".join(lines[start - 1:end]))
                self.assertTrue(finding["references"])
                self.assertIn("Candidate for reviewer confirmation", finding["summary"])
                self.assertIn("# noqa: LLM-18", finding["recommendation"])

    def test_summaries_name_both_regions_and_the_declaration(self):
        _, result = run(*POSITIVE)
        by_id = {f["identity"]: f["summary"] for f in result["findings"]}
        first = by_id["<module>:bedrock-runtime:client"]
        self.assertIn("region_name='us-east-1') pins bedrock-runtime to us-east-1", first)
        self.assertIn("declared in ap-south-1 (agent/samconfig.toml line 6: samconfig "
                      "default.deploy.parameters.region)", first)
        self.assertIn("not measured", first)
        self.assertIn("Config(region_name)='eu-west-1'", by_id["<module>:bedrock-runtime:client#2"])
        self.assertIn("Session(region_name)='us-east-1'", by_id["knowledge_base:bedrock-agent-runtime:client"])
        azure = by_id["azurerm_cognitive_account.openai:location"]
        self.assertIn("Azure eastus (North America)", azure)
        self.assertIn('declared in eu-central-1 (rag/main.tf line 3: Terraform provider "aws" region)', azure)
        self.assertIn("Google Cloud us-central1 (North America)", by_id["<module>:vertexai.init(location)"])

    def test_other_dependency_forms(self):
        cases = (
            ("app.py", 'URL = "https://runtime.sagemaker.us-east-1.amazonaws.com/endpoints/x/invocations"\n',
             "<module>:endpoint/sagemaker-runtime"),
            ("app.py", 'URL = "https://s3vectors.us-east-1.api.aws"\n', "<module>:endpoint/s3vectors"),
            ("app.py", 'URL = "https://vpce-1.bedrock-runtime-fips.us-east-1.vpce.amazonaws.com"\n',
             "<module>:endpoint/bedrock-runtime"),
            ("app.py", 'import boto3\nc = boto3.client("bedrock-runtime", "us-east-1")\n', CLIENT_ID),
            ("app.py", 'import boto3\nKW = {"region_name": "us-east-1"}\nc = boto3.client("sagemaker-runtime", **KW)\n',
             "<module>:sagemaker-runtime:client"),
            ("app.py", 'from langchain_aws import ChatBedrock\nm = ChatBedrock(region_name="us-east-1")\n',
             "<module>:ChatBedrock(region_name)"),
            ("app.py", "from llama_index.embeddings.bedrock import BedrockEmbedding\n"
                       'e = BedrockEmbedding(region_name="us-east-1")\n', "<module>:BedrockEmbedding(region_name)"),
            ("app.py", 'from anthropic import AnthropicVertex\nc = AnthropicVertex(region="us-east5")\n',
             "<module>:AnthropicVertex(region)"),
            ("app.py", 'import opensearchpy\nHOST = "search-kb.us-east-1.es.amazonaws.com"  # knn index\n',
             "<module>:endpoint/opensearch"),
            ("deploy.yaml", "spec:\n  containers:\n    - env:\n        - name: BEDROCK_REGION\n"
                            "          value: us-east-1\n", "setting/BEDROCK_REGION"),
            ("compose.yaml", "services:\n  agent:\n    environment:\n      - BEDROCK_REGION=us-east-1\n",
             "setting/BEDROCK_REGION"),
            ("task.json", '{\n  "containerDefinitions": [{\n    "environment": [\n'
                          '      {"name": "VERTEX_LOCATION", "value": "us-central1"}\n    ]\n  }]\n}\n',
             "setting/VERTEX_LOCATION"),
            ("models.toml", '[bedrock]\nendpoint = "https://bedrock-runtime.us-east-1.amazonaws.com"\n',
             "endpoint/bedrock-runtime"),
            ("main.tf", 'locals {\n  url = "https://westus.api.cognitive.microsoft.com"\n}\n', "endpoint/azure-ai"),
        )
        for name, content, identity in cases:
            with self.subTest(name=name, identity=identity):
                _, result = run(extra=[workload(), (name, content)])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(identities(result), [identity])
                self.assertEqual(result["findings"][0]["scope_id"], f"file:{name}")

    def test_workload_region_forms(self):
        cases = (
            ("serverless.yml", "service: agent\nprovider:\n  name: aws\n  region: eu-west-1\n",
             "serverless.yml provider.region"),
            ("samconfig.yaml", "version: 0.1\nprod:\n  deploy:\n    parameters:\n      region: eu-west-1\n",
             "samconfig prod.deploy.parameters.region"),
            ("cdk.json", '{\n  "app": "python3 app.py",\n  "context": {\n    "region": "eu-west-1"\n  }\n}\n',
             "cdk.json context.region"),
            ("stack.py", "import aws_cdk as cdk\nenv = cdk.Environment(account='1', region='eu-west-1')\n",
             "CDK Environment(region=...)"),
            ("stack.py", "import aws_cdk as cdk\nfrom stacks import AgentStack\n"
                         "AgentStack(cdk.App(), 'Agent', env={'account': '1', 'region': 'eu-west-1'})\n",
             "CDK env={'region': ...}"),
            (".github/workflows/deploy.yml", "jobs:\n  deploy:\n    steps:\n      - uses: aws-actions/configure-aws-"
                                             "credentials@v4\n        with:\n          aws-region: eu-west-1\n",
             "aws-region"),
            ("k8s.yaml", "env:\n  - name: AWS_REGION\n    value: eu-west-1\n", "AWS_REGION"),
            ("main.tf", 'provider "aws" {\n  region = "eu-west-1" # Ireland\n}\n', 'Terraform provider "aws" region'),
        )
        for name, content, how in cases:
            with self.subTest(name=name):
                _, result = run(extra=[(name, content), ("app.py", CLIENT.format("us-east-1"))])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(identities(result), [CLIENT_ID])
                self.assertIn(f"declared in eu-west-1 ({name} line ", result["findings"][0]["summary"])
                self.assertIn(how, result["findings"][0]["summary"])


class Llm18NegativeTests(unittest.TestCase):
    """LLM18-02: colocated, unpinned or out-of-scope dependencies are clean."""

    def test_same_region_is_clean(self):
        _, result = run(extra=[workload("us-east-1"), ("app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_regions_not_written_literally_are_not_dependencies(self):
        content = CLIENT.format("eu-west-1") + (
            "import os\n"
            'a = boto3.client("bedrock-runtime")\n'
            'b = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"))\n'
            "def make(region):\n"
            '    return boto3.client("bedrock-runtime", region_name=region)\n'
            'URL = f"https://bedrock-runtime.{os.environ[\'R\']}.amazonaws.com"\n'
            'c = boto3.client("bedrock-runtime", **settings())\n'
            'd = boto3.client("bedrock", region_name="us-east-1")\n'
            'e = boto3.client("s3", region_name="us-east-1")\n'
            '"""https://bedrock-runtime.us-east-1.amazonaws.com in a bare string"""\n'
            "# https://bedrock-runtime.us-east-1.amazonaws.com in a comment\n"
            'HOST = "search-logs.us-east-1.es.amazonaws.com"\n'
        )
        _, result = run(extra=[workload(), ("app.py", content)])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        yaml = ("Resources:\n  F:\n    Properties:\n      Environment:\n        Variables:\n"
                "          BEDROCK_REGION: !Ref AWS::Region\n          VERTEX_LOCATION: global\n"
                "          MODEL: https://bedrock-runtime.eu-west-1.amazonaws.com\n")
        _, result = run(extra=[workload(), ("template.yaml", yaml)])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])

    def test_cross_cloud_dependencies_in_the_same_area_are_not_flagged(self):
        content = ('import vertexai\nvertexai.init(location="us-east4")\n'
                   'AZURE = "https://eastus.api.cognitive.microsoft.com"\n')
        _, result = run(extra=[workload("us-east-1"), ("app.py", content)])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])
        _, result = run(extra=[workload("ap-south-1"), ("app.py", content)])
        self.assertEqual(len(result["findings"]), 2)
        self.assertTrue(all(f["confidence"] == "low" for f in result["findings"]))

    def test_aliased_provider_and_non_aws_serverless_do_not_declare_the_workload(self):
        for name, content in (("main.tf", 'provider "aws" {\n  alias  = "use1"\n  region = "us-east-1"\n}\n'),
                              ("serverless.yml", "provider:\n  name: azure\n  region: eu-west-1\n")):
            with self.subTest(name=name):
                _, result = run(extra=[(name, content), ("app.py", CLIENT.format("us-east-1"))])
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("unsupported file type", limitations(result))
                self.assertIn("no deployment Region is declared", limitations(result))


class Llm18ExceptionTests(unittest.TestCase):
    """LLM18-03: deliberate pins and development files."""

    def test_noqa_on_or_above_the_flagged_line(self):
        for marker, flagged in (("# noqa: LLM-18\n", False), ("# noqa: LLM18\n", False), ("# noqa\n", False),
                                ("# noqa: E501\n", True)):
            with self.subTest(marker=marker):
                content = CLIENT.format("us-east-1").replace("client =", marker + "client =")
                _, result = run(extra=[workload(), ("app.py", content)])
                self.assertEqual(bool(result["findings"]), flagged)
        _, result = run(*POSITIVE)
        self.assertNotIn("us-east-2", " ".join(f["summary"] for f in result["findings"]))

    def test_development_paths_are_not_evaluated_and_do_not_declare_the_workload(self):
        for name in ("tests/app.py", "dev/app.py", "test_agent.py", "local/template.yaml"):
            content = CLIENT.format("us-east-1") if name.endswith(".py") else "env:\n  BEDROCK_REGION: us-east-1\n"
            with self.subTest(name=name):
                _, result = run(extra=[workload(), (name, content)])
                self.assertEqual(result["status"], "partial")
                self.assertIn("development/test path", limitations(result))
        _, result = run(extra=[("dev/samconfig.toml", SAM.format("eu-west-1")), ("app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no deployment Region is declared", limitations(result))

    def test_development_files_without_signals_stay_silent(self):
        for name, content in (("tests/test_x.py", "def test():\n    assert region\n"),
                              ("tests/broken.py", "def broken(:\n    region\n")):
            with self.subTest(name=name), self.assertRaises(llm18.Unsupported if "test_x" in name
                                                            else llm18.NotEvaluated):
                llm18.parse(name, content)


class Llm18MissingEvidenceTests(unittest.TestCase):
    """LLM18-04: without one explicit workload Region the dependency file is not evaluated."""

    def test_no_workload_region_is_unavailable(self):
        _, result = run(extra=[("app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertIn("no deployment Region is declared in the payload", limitations(result))
        self.assertIn("does not guess the workload Region", limitations(result))

    def test_several_regions_are_ambiguous_unless_a_parent_directory_decides(self):
        decls = [("a/samconfig.toml", SAM.format("eu-west-1")), ("b/samconfig.toml", SAM.format("us-west-2"))]
        _, result = run(extra=decls + [("src/app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evaluated_scope"], ["file:a/samconfig.toml", "file:b/samconfig.toml"])
        self.assertIn("name several Regions (eu-west-1, us-west-2)", limitations(result))
        _, result = run(extra=decls + [("b/handler/app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "completed")
        self.assertIn("declared in us-west-2 (b/samconfig.toml", result["findings"][0]["summary"])
        environments = SAM.format("eu-west-1") + '[prod.deploy.parameters]\nregion = "us-west-2"\n'
        _, result = run(extra=[("samconfig.toml", environments), ("app.py", CLIENT.format("us-east-1"))])
        self.assertEqual(result["status"], "partial")
        self.assertIn("the repository root name several Regions", limitations(result))

    def test_missing_source_is_unavailable_or_partial(self):
        payload = make_input(sources=[], scope=["file:agent/app.py"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no static source", limitations(result))
        payload = make_input(*POSITIVE, scope=[f"file:{name}" for name in POSITIVE] + ["file:x.tf"])
        result = evaluate(payload)
        validate_pair(payload, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["findings"]), len(Llm18PositiveTests.EXPECTED))


class Llm18MalformedTests(unittest.TestCase):
    """LLM18-05: unparseable files are never reported clean."""

    def test_malformed_inputs_are_partial_with_the_reason(self):
        for name, content, reason in (
            ("app.py", 'import boto3\nc = boto3.client("bedrock-runtime", region_name="us-east-1"\n', "invalid Python"),
            ("template.yaml", "Variables:\n\tBEDROCK_REGION: us-east-1\n", "tab"),
            ("svc/samconfig.toml", '[default.deploy.parameters\nregion = "eu-west-1"\n', "invalid TOML"),
            ("main.tf", 'provider "aws" {\n  region = "eu-west-1"\n', "unbalanced"),
            ("cdk.json", '{"context": {"region": "eu-west-1"}\n', "could not be parsed"),
        ):
            with self.subTest(name=name):
                _, result = run(extra=[workload(), (name, content)])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["coverage"]["evaluated_scope"], ["file:samconfig.toml"])
                self.assertIn(reason, limitations(result))
                self.assertIn("1 file(s) could not be parsed", limitations(result))

    def test_unrelated_files_are_declined_cheaply_for_the_scanner(self):
        for name, content in (
            ("README.md", "boto3.client('bedrock-runtime', region_name='us-east-1')\n"),
            ("main.tf", 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'),
            ("values.yaml", "region: us-east-1\nreplicas: 2\n"),
            ("broken.yaml", "{{ .Values.region }}: [\n"),
            ("pyproject.toml", '[project]\nname = "x"\n'),
            ("util.py", "def add(a, b):\n    return a + b\n"),
            ("geo.py", "def region_of(city):\n    return city.region\n"),
        ):
            with self.subTest(name=name), self.assertRaises(llm18.Unsupported):
                llm18.parse(name, content)


class Llm18BoundaryTests(unittest.TestCase):
    """LLM18-06: Region syntax, identities and stable fingerprints."""

    def test_only_well_formed_regions_count(self):
        for region in ("us-east-1x", "useast1", "US-EAST-1", "global", "us-east", ""):
            with self.subTest(region=region), self.assertRaises(llm18.Unsupported):
                llm18.parse("app.py", CLIENT.format(region))
        for region in ("us-gov-west-1", "eusc-de-east-1", "ap-southeast-7"):
            with self.subTest(region=region):
                self.assertEqual(len(llm18.parse("app.py", CLIENT.format(region)).dependencies), 1)

    def test_fingerprints_ignore_line_moves_and_the_pinned_region(self):
        _, before = run(*POSITIVE)
        shifted = [(name, "# moved\n\n\n" + (FIXTURES / name).read_text()) for name in ("agent/app.py", "rag/main.tf")]
        _, after = run("agent/samconfig.toml", "agent/template.yaml", "rag/retriever.py", extra=shifted)
        key = lambda f: (f["scope_id"], f["identity"])  # noqa: E731
        self.assertEqual([f["fingerprint"] for f in sorted(before["findings"], key=key)],
                         [f["fingerprint"] for f in sorted(after["findings"], key=key)])
        moved = {f["identity"]: f["evidence"][0]["line_start"] for f in after["findings"]}
        self.assertEqual(moved["<module>:bedrock-runtime:client"], 17)
        _, east = run(extra=[workload(), ("app.py", CLIENT.format("us-east-1"))])
        _, west = run(extra=[workload(), ("app.py", CLIENT.format("us-west-2"))])
        self.assertEqual(east["findings"][0]["fingerprint"], west["findings"][0]["fingerprint"])


class Llm18ContractTests(unittest.TestCase):
    def test_fingerprint_matches_shared_contract_implementation(self):
        args = (REPOSITORY_ID, CHECK_ID, "file:agent/app.py", CLIENT_ID)
        self.assertEqual(fingerprint(*args), shared_fingerprint(*args))

    def test_invented_static_evidence_is_rejected(self):
        payload, result = run(*POSITIVE)
        tampered = copy.deepcopy(result)
        tampered["findings"][0]["evidence"][0]["value"] = 'runtime = boto3.client("bedrock-runtime")'
        with self.assertRaises(ContractError):
            validate_pair(payload, tampered)

    def test_unsupported_detector_version_is_rejected(self):
        payload = make_input("agent/app.py")
        payload["detector_version"] = "9.9.9"
        with self.assertRaises(EvaluationError):
            evaluate(payload)

    def test_evaluation_is_deterministic(self):
        payload = make_input(*POSITIVE)
        self.assertEqual(evaluate(payload), evaluate(copy.deepcopy(payload)))

    def test_committed_cli_fixture_matches_source_fixture(self):
        committed = json.loads((FIXTURES / "llm18-01-positive-input.json").read_text())
        self.assertEqual(committed, make_input(*POSITIVE))

    def test_registered_in_cli_without_settings(self):
        self.assertIs(cli.DETECTORS[CHECK_ID], llm18)
        self.assertFalse(hasattr(llm18, "REFERENCE_SETTINGS"))


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
        self.assertEqual(len(result["findings"]), len(Llm18PositiveTests.EXPECTED))


if __name__ == "__main__":
    unittest.main()
