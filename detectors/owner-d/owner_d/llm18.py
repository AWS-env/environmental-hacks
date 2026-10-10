"""LLM-18: deploying agent infra far from its dependencies (static topology heuristic).

Detector semantics version 1.0.0. Reads, as text and project-wide, Python source, YAML/JSON
(CloudFormation/SAM, Kubernetes, compose, serverless.yml, samconfig, cdk.json, workflows),
samconfig.toml and Terraform, and flags an LLM, embedding or vector-store dependency pinned to a
Region other than the Region declared for the workload that calls it:

- dependency Region: a Bedrock / SageMaker Runtime / S3 Vectors client built with a literal
  `region_name` (also through `Config` or a `Session`), an SDK client with a literal Region
  (`AnthropicBedrock(aws_region=)`, LangChain/LlamaIndex Bedrock classes, `vertexai.init(location=)`,
  `AnthropicVertex(region=)`), an endpoint URL with a Region in it (bedrock-runtime, runtime.sagemaker,
  s3vectors, OpenSearch/AOSS next to vector wording, Vertex AI, Azure AI regional endpoints), a
  `BEDROCK_REGION`-style setting, or a Terraform `azurerm_cognitive_account` (OpenAI) location;
- workload Region: samconfig `region`, serverless.yml `provider.region`, Terraform `provider "aws"`
  `region` (not aliased), cdk.json context `region`, a CDK `Environment(region=)`, an
  `AWS_REGION`/`AWS_DEFAULT_REGION` value or a GitHub Actions `aws-region`. The declaration in the
  nearest parent directory of the dependency wins, else the payload's only declared Region.

Only literal Regions count: when either side is missing or ambiguous the file is not evaluated, never
guessed. Nothing is executed or resolved, and no latency or transfer is measured. LLM-18 is an OQ-8
judgement row: findings are candidates for reviewer confirmation.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
import types
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import miniyaml, static, textstatic
from .llmcalls import call_keywords, dict_items, resolve
from .miniyaml import Mapping, Scalar, Sequence
from .obs09 import _dev_path
from .obs10 import HclError, _mask, parse_hcl
from .obs10 import _string as hcl_string
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401
from .tst12 import is_test_path

CHECK_ID = "LLM-18"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-18", "LLM18")
FORMATS = (
    "Python (.py), YAML/JSON (.yaml/.yml/.json), TOML (.toml) and Terraform (.tf) files that pin an LLM, "
    "embedding or vector-store Region or declare a deployment Region"
)

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html",
    "https://docs.aws.amazon.com/general/latest/gr/bedrock.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/models-regions.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html",
    "https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html",
    "https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars-runtime.html",
    "https://aws.amazon.com/ec2/pricing/on-demand/",
)
RECOMMENDATION = (
    "Colocate the workload and the dependency: create the client without a hard-coded Region so it uses the "
    "runtime's AWS_REGION (or point it at the workload's Region), or deploy the workload in the dependency's "
    "Region. Before moving, confirm the model or feature is offered in that Region (a Bedrock cross-Region "
    "inference profile keeps requests inside one geography) and that data-residency rules allow it. If the "
    "Region is deliberate, mark the line with # noqa: LLM-18."
)
LIMITATION = (
    "Static topology heuristic only: LLM-18 proves that a file pins an LLM, embedding or vector-store dependency "
    "to a literal Region that differs from the literal deployment Region declared in the repository, not that "
    "the workload calls it often or how much latency or data transfer that adds, so no measurements are emitted. "
    "It is an OQ-8 judgement row: findings are candidates for reviewer confirmation (the model may not be "
    "offered in the workload's Region, or the Region may be required for residency or quota). Not evaluated: "
    "Regions from parameters, environment lookups, defaults, variables, SSM or AWS::Region; Regions set in "
    "Dockerfiles, .env files, other modules or at deploy time; Bedrock cross-Region inference profile routing; "
    "aliased Terraform providers; cross-cloud dependencies (Vertex AI, Azure) in the same geographic area as the "
    "workload; development/test files. A dependency file is not evaluated when no deployment Region is "
    "declared or the declarations name several Regions."
)

CLOUDS = {"aws": "AWS", "gcp": "Google Cloud", "azure": "Azure"}
AWS_REGION = re.compile(r"^(?:us|ca|mx|sa|eu|eusc|me|il|af|ap|cn)(?:-[a-z]+){1,2}-\d{1,2}$")
GCP_REGION = re.compile(r"^(?:us|northamerica|southamerica|europe|asia|australia|me|africa)-[a-z]+\d{1,2}$")
NA, SA, EU, ME, AF, IN, AS, OC = ("North America", "South America", "Europe", "Middle East", "Africa", "India",
                                  "Asia", "Oceania")
AWS_AREAS = {"us": NA, "ca": NA, "mx": NA, "sa": SA, "eu": EU, "eusc": EU, "me": ME, "il": ME, "af": AF,
             "ap": AS, "cn": AS}
GCP_AREAS = {"us": NA, "northamerica": NA, "southamerica": SA, "europe": EU, "me": ME, "africa": AF,
             "asia": AS, "australia": OC}
AZURE_AREAS = {name: area for area, names in (
    (NA, "eastus eastus2 westus westus2 westus3 centralus northcentralus southcentralus westcentralus "
         "canadacentral canadaeast mexicocentral"),
    (SA, "brazilsouth brazilsoutheast chilecentral"),
    (EU, "northeurope westeurope uksouth ukwest francecentral francesouth germanywestcentral germanynorth "
         "swedencentral switzerlandnorth switzerlandwest norwayeast norwaywest polandcentral italynorth "
         "spaincentral austriaeast"),
    (ME, "uaenorth uaecentral qatarcentral israelcentral"),
    (AF, "southafricanorth southafricawest"),
    (IN, "centralindia southindia westindia jioindiawest jioindiacentral"),
    (AS, "eastasia southeastasia japaneast japanwest koreacentral koreasouth indonesiacentral malaysiawest"),
    (OC, "australiaeast australiasoutheast australiacentral newzealandnorth"),
) for name in names.split()}

_R = r"(?P<region>[a-z]{2,4}(?:-[a-z]+){1,2}-\d{1,2})"
_AWS_DNS = r"\.(?:vpce\.)?amazonaws\.com\b"
# (pattern, cloud, service or None for the `service` group, needs vector wording in the file)
ENDPOINTS = (
    (re.compile(rf"\b(?P<service>bedrock-(?:runtime|agent-runtime|agentcore))(?:-fips)?\.{_R}{_AWS_DNS}"), "aws",
     None, False),
    (re.compile(rf"\bruntime(?:-fips)?\.sagemaker\.{_R}{_AWS_DNS}"), "aws", "sagemaker-runtime", False),
    (re.compile(rf"\bs3vectors\.{_R}\.api\.aws\b"), "aws", "s3vectors", False),
    (re.compile(rf"\b[a-z0-9-]+\.{_R}\.aoss{_AWS_DNS}"), "aws", "opensearch-serverless", True),
    (re.compile(rf"\b[a-z0-9-]+\.{_R}\.es{_AWS_DNS}"), "aws", "opensearch", True),
    (re.compile(r"\b(?P<region>[a-z]+-[a-z]+\d{1,2})-aiplatform\.googleapis\.com\b"), "gcp", "vertex-ai", False),
    (re.compile(r"\b(?P<region>[a-z0-9]+)\.api\.cognitive\.microsoft\.com\b"), "azure", "azure-ai", False),
)
VECTOR_WORDING = re.compile(r"vector|knn|embedding", re.I)

AWS_AI_SERVICES = frozenset({"bedrock-runtime", "bedrock-agent-runtime", "bedrock-agentcore", "sagemaker-runtime",
                             "s3vectors"})
SDK_ROOTS = frozenset({"anthropic", "langchain_aws", "langchain_community", "langchain_google_vertexai",
                       "llama_index"})
SDK_CLASSES = {  # class name -> (cloud, Region keyword, service)
    **dict.fromkeys(("AnthropicBedrock", "AsyncAnthropicBedrock"), ("aws", "aws_region", "bedrock-runtime")),
    **dict.fromkeys(("ChatBedrock", "ChatBedrockConverse", "BedrockLLM", "BedrockEmbeddings", "Bedrock",
                     "BedrockConverse", "BedrockEmbedding"), ("aws", "region_name", "bedrock-runtime")),
    "AmazonKnowledgeBasesRetriever": ("aws", "region_name", "bedrock-agent-runtime"),
    **dict.fromkeys(("AnthropicVertex", "AsyncAnthropicVertex"), ("gcp", "region", "vertex-ai")),
    **dict.fromkeys(("ChatVertexAI", "VertexAI", "VertexAIEmbeddings"), ("gcp", "location", "vertex-ai")),
}
SDK_CALLS = dict.fromkeys(("vertexai.init", "google.cloud.aiplatform.init", "google.genai.Client"),
                          ("gcp", "location", "vertex-ai"))

WORKLOAD_KEYS = ("AWS_REGION", "AWS_DEFAULT_REGION")
WORKFLOW_KEY = "aws-region"
DEPENDENCY_KEYS = {
    **dict.fromkeys(("BEDROCK_REGION", "BEDROCK_AWS_REGION", "AWS_BEDROCK_REGION"), ("aws", "bedrock-runtime")),
    **dict.fromkeys(("VERTEX_LOCATION", "VERTEX_AI_LOCATION", "VERTEXAI_LOCATION"), ("gcp", "vertex-ai")),
    **dict.fromkeys(("AZURE_OPENAI_LOCATION", "AZURE_OPENAI_REGION"), ("azure", "azure-openai")),
}
SETTING_HINT = re.compile(r"\b(?:%s)\b" % "|".join([*WORKLOAD_KEYS, WORKFLOW_KEY, *DEPENDENCY_KEYS]))
CDK_REGION_KEYS = {"region", "awsregion", "deployregion", "deploymentregion", "defaultregion"}
PY_HINT = re.compile(r"region|location|amazonaws\.com|googleapis\.com|microsoft\.com|api\.aws|"
                     r"bedrock-|sagemaker-runtime|s3vectors")
TF_HINT = re.compile(r'provider\s+"aws"|azurerm_cognitive_account')
AZURE_OPENAI_KINDS = {"OpenAI", "AIServices"}


@dataclass(frozen=True)
class Dependency:
    line: int
    end_line: int
    anchor: str
    cloud: str
    service: str
    region: str
    how: str


@dataclass(frozen=True)
class Declaration:
    locator: str
    home: str  # directory the declaration covers ("" = repository root)
    line: int
    region: str
    how: str


class Ctx:
    def __init__(self, locator, content, dependencies, declarations):
        self.locator = locator
        self.lines = content.splitlines()
        self.dependencies = dependencies
        self.declarations = declarations


def valid(cloud, region):
    if not isinstance(region, str):
        return False
    if cloud == "aws":
        return bool(AWS_REGION.match(region))
    if cloud == "gcp":
        return bool(GCP_REGION.match(region))
    return region in AZURE_AREAS


def area(cloud, region):
    if cloud == "aws":
        if region in ("ap-southeast-2", "ap-southeast-4", "ap-southeast-6"):
            return OC
        return IN if region.startswith("ap-south-") else AWS_AREAS.get(region.split("-")[0])
    if cloud == "gcp":
        return IN if region.startswith("asia-south") else GCP_AREAS.get(region.split("-")[0])
    return AZURE_AREAS.get(region)


def endpoints(text, vector):
    """(cloud, service, region, url) for every regional LLM/vector endpoint in `text`."""
    for pattern, cloud, service, needs_vector in ENDPOINTS:
        if needs_vector and not vector:
            continue
        for match in pattern.finditer(text):
            if valid(cloud, match.group("region")):
                yield cloud, service or match.group("service"), match.group("region"), match.group(0)


def _line_endpoints(lines, vector):
    return [Dependency(number, number, f"endpoint/{service}", cloud, service, region, f"The endpoint {url}")
            for number, text in enumerate(lines, 1) for cloud, service, region, url in endpoints(text, vector)]


def _home(locator):
    parts = locator.split("/")
    if parts[:2] == [".github", "workflows"]:
        return ""
    return "/".join(parts[:-1])


# -- Python ----------------------------------------------------------------------------------


def _literal(ctx, node):
    value = resolve(ctx, node) if node is not None else None
    return value.value.strip() if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _boto_region(ctx, call, keywords):
    """(region, how) of a boto3 client: region_name, else Config(region_name=), else its Session's region."""
    explicit = call.args[1] if len(call.args) > 1 else keywords.get("region_name")
    if explicit is not None:
        return _literal(ctx, explicit), "region_name"
    config = resolve(ctx, keywords["config"]) if "config" in keywords else None
    if isinstance(config, ast.Call) and (ctx.dotted(config.func) or "").endswith("Config"):
        region = _literal(ctx, (call_keywords(ctx, config) or {}).get("region_name"))
        if region:
            return region, "Config(region_name)"
    session = resolve(ctx, call.func.value)
    if isinstance(session, ast.Call) and (ctx.dotted(session.func) or "").endswith("Session"):
        return _literal(ctx, (call_keywords(ctx, session) or {}).get("region_name")), "Session(region_name)"
    return None, None


def _python_dependency(ctx, call):
    keywords = call_keywords(ctx, call)
    if keywords is None:
        return None
    dotted = ctx.dotted(call.func) or ""
    name = dotted.rsplit(".", 1)[-1]
    sdk = SDK_CALLS.get(dotted) or (SDK_CLASSES.get(name) if dotted.split(".")[0] in SDK_ROOTS else None)
    if sdk:
        cloud, keyword, service = sdk
        region = _literal(ctx, keywords.get(keyword))
        label = dotted if dotted in SDK_CALLS else name
        if not valid(cloud, region):
            return None
        return Dependency(call.lineno, call.end_lineno, f"{ctx.qualname(call)}:{label}({keyword})", cloud, service,
                          region, f"{label}({keyword}={region!r})")
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in ("client", "create_client")):
        return None
    service = _literal(ctx, call.args[0] if call.args else keywords.get("service_name"))
    if service not in AWS_AI_SERVICES:
        return None
    region, how = _boto_region(ctx, call, keywords)
    if not valid("aws", region):
        return None
    return Dependency(call.lineno, call.end_lineno, f"{ctx.qualname(call)}:{service}:client", "aws", service, region,
                      f"The client ({how}={region!r})")


def _cdk_declarations(ctx, locator, call):
    dotted = ctx.dotted(call.func) or ""
    keywords = call_keywords(ctx, call) or {}
    found = []
    if dotted.split(".")[0] == "aws_cdk" and dotted.endswith(".Environment"):
        found.append((_literal(ctx, keywords.get("region")), "CDK Environment(region=...)"))
    if "env" in keywords:
        env = dict_items(ctx, keywords["env"]) or {}
        found.append((_literal(ctx, env.get("region")), "CDK env={'region': ...}"))
    return [Declaration(locator, _home(locator), call.lineno, region, how) for region, how in found
            if valid("aws", region)]


def _python(locator, content):
    if not PY_HINT.search(content):
        raise Unsupported(locator)
    try:
        ctx = static.Ctx(locator, content)
    except (SyntaxError, ValueError):
        raise ParseError("invalid Python") from None
    vector = bool(VECTOR_WORDING.search(content))
    cdk = any(path.split(".")[0] == "aws_cdk" for path in ctx.aliases.values())
    dependencies, declarations = [], []
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call):
            found = _python_dependency(ctx, node)
            if found:
                dependencies.append(found)
            if cdk:
                declarations.extend(_cdk_declarations(ctx, locator, node))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and not isinstance(ctx.parent(node), ast.Expr)):  # docstrings and bare strings are not code
            dependencies.extend(
                Dependency(node.lineno, node.end_lineno, f"{ctx.qualname(node)}:endpoint/{service}", cloud, service,
                           region, f"The endpoint {url}")
                for cloud, service, region, url in endpoints(node.value, vector))
    return dependencies, declarations


# -- YAML / JSON -----------------------------------------------------------------------------


def _settings(node, workflow, found):
    """(key, value, line, end_line) of region settings: `KEY: value`, `{name: KEY, value: v}`, `- KEY=value`."""
    if isinstance(node, Mapping):
        name = node.get("name", node.get("Name"))
        value = node.get("value", node.get("Value"))
        if isinstance(name, Scalar) and isinstance(value, Scalar) and (name.value in WORKLOAD_KEYS
                                                                       or name.value in DEPENDENCY_KEYS):
            found.append((name.value, value.value, min(name.line, value.line), max(name.line, value.line)))
        for key, child in node.items.items():
            if isinstance(child, Scalar) and (key in WORKLOAD_KEYS or key in DEPENDENCY_KEYS
                                              or (workflow and key == WORKFLOW_KEY)):
                found.append((key, child.value, node.key_lines.get(key, child.line), child.line))
            _settings(child, workflow, found)
    elif isinstance(node, Sequence):
        for child in node.items:
            if isinstance(child, Scalar) and child.value and "=" in child.value:
                key, _, value = child.value.partition("=")
                if key.strip() in WORKLOAD_KEYS or key.strip() in DEPENDENCY_KEYS:
                    found.append((key.strip(), value.strip().strip("\"'"), child.line, child.line))
            _settings(child, workflow, found)
    return found


def _scalar(node, *path):
    for key in path:
        node = node.get(key) if isinstance(node, Mapping) else None
    return node if isinstance(node, Scalar) else None


def _special_declarations(name, document):
    """(Scalar, how) of samconfig / serverless.yml / cdk.json deployment Regions."""
    if not isinstance(document, Mapping):
        return []
    if name.startswith("samconfig."):
        return [(_scalar(document, env, cmd, "parameters", "region"), f"samconfig {env}.{cmd}.parameters.region")
                for env, cmds in document.items.items() if isinstance(cmds, Mapping) for cmd in cmds.items]
    if name.startswith("serverless."):
        provider = _scalar(document, "provider", "name")
        if provider is None or provider.value == "aws":
            return [(_scalar(document, "provider", "region"), "serverless.yml provider.region")]
    if name == "cdk.json":
        context = document.get("context")
        if isinstance(context, Mapping):
            return [(_scalar(context, key), f"cdk.json context.{key}") for key in context.items
                    if re.sub(r"[-_]", "", key.lower()) in CDK_REGION_KEYS]
    return []


def _structured(locator, content, json_file=False):
    name = PurePosixPath(locator.lower()).name
    special = name == "cdk.json" or name.startswith(("samconfig.", "serverless."))
    if not (special or SETTING_HINT.search(content) or any(endpoints(content, True))):
        raise Unsupported(locator)
    try:
        documents = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    lines = content.splitlines()
    workflow = locator.startswith(".github/workflows/")
    dependencies = _line_endpoints(lines if json_file else [miniyaml.strip_comment(line) for line in lines],
                                   bool(VECTOR_WORDING.search(content)))
    declarations = []
    for document in documents:
        for key, value, line, end in _settings(document, workflow, []):
            if key in DEPENDENCY_KEYS:
                cloud, service = DEPENDENCY_KEYS[key]
                if valid(cloud, value):
                    dependencies.append(Dependency(line, end, f"setting/{key}", cloud, service, value,
                                                   f"The setting {key}={value!r}"))
            elif valid("aws", value):
                declarations.append(Declaration(locator, _home(locator), line, value, key))
        for scalar, how in _special_declarations(name, document):
            if scalar is not None and valid("aws", scalar.value):
                declarations.append(Declaration(locator, _home(locator), scalar.line, scalar.value, how))
    return dependencies, declarations


# -- TOML / Terraform ------------------------------------------------------------------------

_TOML_TABLE = re.compile(r"^\s*\[([^\]]+)\]")
_TOML_REGION = re.compile(r"^\s*region\s*=")


def _toml(locator, content):
    sam = PurePosixPath(locator.lower()).name.startswith("samconfig")
    if not sam and not any(endpoints(content, True)):
        raise Unsupported(locator)
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise ParseError(f"invalid TOML: {error}") from None
    lines = content.splitlines()
    declarations = []
    if sam:
        table, where = None, {}
        for number, text in enumerate(lines, 1):
            header = _TOML_TABLE.match(text)
            if header:
                table = header.group(1).strip().replace('"', "")
            elif table and _TOML_REGION.match(text):
                where.setdefault(table, number)
        for env, cmds in data.items():
            for cmd, settings in (cmds.items() if isinstance(cmds, dict) else ()):
                params = settings.get("parameters") if isinstance(settings, dict) else None
                region = params.get("region") if isinstance(params, dict) else None
                if valid("aws", region):
                    table = f"{env}.{cmd}.parameters"
                    declarations.append(Declaration(locator, _home(locator), where.get(table, 1), region,
                                                    f"samconfig {table}.region"))
    return _line_endpoints([miniyaml.strip_comment(line) for line in lines],
                           bool(VECTOR_WORDING.search(content))), declarations


def _terraform(locator, content):
    if not (TF_HINT.search(content) or any(endpoints(content, True))):
        raise Unsupported(locator)
    try:
        blocks = parse_hcl(content)
    except HclError as error:
        raise ParseError(str(error)) from None
    lines = [raw[:len(_mask(raw))] for raw in content.splitlines()]  # comments cut, strings kept
    dependencies = _line_endpoints(lines, bool(VECTOR_WORDING.search(content)))
    declarations = []
    for block in blocks:
        if block.type == "provider" and block.labels[:1] == ("aws",) and "alias" not in block.attrs:
            raw, line = block.attrs.get("region", ("", 0))
            if valid("aws", hcl_string(raw)):
                declarations.append(Declaration(locator, _home(locator), line, hcl_string(raw),
                                                'Terraform provider "aws" region'))
        if block.type == "resource" and block.labels[:1] == ("azurerm_cognitive_account",) and len(block.labels) > 1:
            kind = hcl_string(block.attrs.get("kind", ("",))[0])
            raw, line = block.attrs.get("location", ("", 0))
            location = hcl_string(raw)
            if kind in AZURE_OPENAI_KINDS and valid("azure", location):
                dependencies.append(Dependency(
                    line, line, f"azurerm_cognitive_account.{block.labels[1]}:location", "azure", "azure-openai",
                    location, f'azurerm_cognitive_account "{block.labels[1]}" (kind = "{kind}")'))
    return dependencies, declarations


# -- parsing and project ---------------------------------------------------------------------


def _development(locator):
    marked = _dev_path(locator) or ("test module" if is_test_path(locator) else None)
    if not marked:
        return None
    return f"development/test path ({marked}); LLM-18 v1 judges deployed code and configuration only"


def parse(locator, content):
    lower = locator.lower()
    if lower.endswith(".py"):
        handler = _python
    elif lower.endswith((".yaml", ".yml")):
        handler = _structured
    elif lower.endswith(".json"):
        def handler(loc, text):
            return _structured(loc, text, json_file=True)
    elif lower.endswith(".toml"):
        handler = _toml
    elif lower.endswith(".tf"):
        handler = _terraform
    else:
        raise Unsupported(locator)
    development = _development(locator)
    try:
        dependencies, declarations = handler(locator, content)
    except ParseError:
        if development:
            raise NotEvaluated(development) from None
        raise
    if not dependencies and not declarations:
        raise Unsupported(locator)
    if development:
        raise NotEvaluated(development)
    return Ctx(locator, content, dependencies, declarations)


class Project:
    def __init__(self):
        self.declarations = []
        self.failed = 0

    @staticmethod
    def _unique(declarations, label):
        regions = sorted({d.region for d in declarations})
        if len(regions) == 1:
            return regions[0], declarations, None
        return None, declarations, f"{label} name several Regions ({', '.join(regions)})"

    def workload(self, locator):
        """(region, declarations, reason): the nearest parent directory's declarations, else the only Region."""
        parts = locator.split("/")[:-1]
        for depth in range(len(parts), -1, -1):
            home = "/".join(parts[:depth])
            here = [d for d in self.declarations if d.home == home]
            if here:
                return self._unique(here, f"the deployment Region declarations in {home or 'the repository root'}")
        if not self.declarations:
            return None, [], ("no deployment Region is declared in the payload (samconfig, serverless.yml, Terraform "
                              'provider "aws", cdk.json, CDK Environment, AWS_REGION/AWS_DEFAULT_REGION or a '
                              "workflow aws-region)")
        return self._unique(self.declarations, "the payload's deployment Region declarations")


def collect(payload):
    """Parse every evaluable source once: ({(locator, content): Ctx or exception}, Project)."""
    parsed, project = {}, Project()
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        return parsed, project
    scope = payload.get("scope") if isinstance(payload.get("scope"), list) else []
    for scope_id in scope:
        statics = [s for s in payload["sources"] if isinstance(s, dict) and s.get("scope_id") == scope_id
                   and s.get("kind") == textstatic.SUPPORTED_KIND]
        if len(statics) != 1:
            continue
        locator, content = statics[0].get("locator"), statics[0].get("content")
        if not isinstance(locator, str) or not isinstance(content, str):
            continue
        try:
            ctx = parse(locator, content)
        except Exception as error:  # reported per file by the textstatic runner
            parsed[(locator, content)] = error
            project.failed += isinstance(error, ParseError)
            continue
        parsed[(locator, content)] = ctx
        project.declarations.extend(ctx.declarations)
    return parsed, project


def _declared(declarations):
    shown = "; ".join(f"{d.locator} line {d.line}: {d.how}" for d in declarations[:2])
    return shown + (f"; and {len(declarations) - 2} more" if len(declarations) > 2 else "")


def run(ctx, project):
    region, declarations, _ = project.workload(ctx.locator)
    if region is None:
        return []
    hits = []
    for dep in ctx.dependencies:
        if dep.cloud == "aws":
            if dep.region == region:
                continue
            target, confidence = f"{dep.service} to {dep.region}", "medium"
            crossing = f"leaves {region} for {dep.region}: inter-Region data transfer and a longer round trip"
        else:
            theirs, ours = area(dep.cloud, dep.region), area("aws", region)
            if not theirs or not ours or theirs == ours:
                continue
            target, confidence = f"{dep.service} to {CLOUDS[dep.cloud]} {dep.region} ({theirs})", "low"
            crossing = f"leaves AWS {region} ({ours}) for another cloud in {theirs}"
        hits.append(TextHit(
            line=dep.line,
            end_line=dep.end_line,
            anchor=dep.anchor,
            summary=(
                f"{dep.how} pins {target}, but the workload is declared in {region} ({_declared(declarations)}). "
                f"Every call {crossing} (not measured). Candidate for reviewer confirmation: the model or feature "
                f"may not be offered closer to {region}, or the Region may be required for data residency or quota."
            ),
            confidence=confidence,
        ))
    return hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    parsed, project = collect(payload)

    def bound_parse(locator, content):
        ctx = parsed.get((locator, content))
        if ctx is None:
            ctx = parse(locator, content)
        if isinstance(ctx, Exception):
            raise ctx
        if ctx.dependencies:
            region, _, reason = project.workload(locator)
            if region is None:
                raise NotEvaluated(
                    f"pins {len(ctx.dependencies)} LLM/vector dependency Region(s), but {reason}; LLM-18 does not "
                    "guess the workload Region"
                )
        return ctx

    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES, FORMATS=FORMATS,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, parse=bound_parse,
        run=lambda ctx: module.run(ctx, project),
    )
    result = textstatic.evaluate_text(payload, check)
    if project.failed:
        result["coverage"]["limitations"].insert(
            -1, f"{project.failed} file(s) could not be parsed; deployment Region declarations in them, if any, "
                "were not considered")
    return result
