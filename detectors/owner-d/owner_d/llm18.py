"""LLM-18: deploying agent infra far from its dependencies (static topology proxy).

Detector semantics version 1.0.0. Reads, as text and project-wide, IaC and configuration
(Terraform, serverless.yml, samconfig, CDK apps, env/Dockerfile/Kubernetes/compose/JSON
settings), Python and TypeScript/JavaScript sources, and flags an LLM or vector-store
dependency configured with an explicit AWS Region that differs from the single workload
Region declared in the payload:

- the workload Region: a default (non-aliased) Terraform `provider "aws"` region, serverless.yml
  `provider.region`, a samconfig `default` region, a CDK `env` region, or an `AWS_REGION` /
  `AWS_DEFAULT_REGION` / `CDK_DEFAULT_REGION` / `aws-region` setting;
- the dependency Region: a Bedrock/AgentCore, SageMaker, OpenSearch, Kendra or S3 Vectors client
  or SDK wrapper created with a literal region, a Pinecone `ServerlessSpec(cloud="aws")`, an
  OpenSearch request signer, a regional endpoint host, a `*_REGION` setting named after one of
  them (`BEDROCK_REGION`, `LLM_REGION`, ...), or a Terraform resource of such a service on an
  aliased provider with another region.

Only literal Regions count: both sides must be explicitly known and differ. Nothing is executed,
planned or resolved and no measurements are emitted. LLM-18 is an OQ-8 judgement row; findings
are candidates for reviewer confirmation.
"""

from __future__ import annotations

import ast
import re
import sys
import types
from dataclasses import dataclass, field

from . import miniyaml, static, textstatic
from .llmcalls import call_keywords, dict_items, resolve, static_text
from .miniyaml import Mapping, Scalar
from .obs09 import _dev_path
from .obs10 import HclError, parse_hcl
from .obs10 import _string as _hcl_string
from .textstatic import EvaluationError, NotEvaluated, ParseError, TextHit, Unsupported, fingerprint  # noqa: F401

CHECK_ID = "LLM-18"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-18", "LLM18")
FORMATS = (
    "Terraform (.tf/.tfvars), YAML/JSON/TOML config, .env files, Dockerfiles, Python (.py) and "
    "TypeScript/JavaScript sources that declare a literal AWS Region"
)

REFERENCES = (
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp03.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/models-regions.html",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html",
    "https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer",
)
REC_MODEL = (
    "Colocate the agent with its model endpoint: use the model in the workload Region when it is offered there "
    "(with Bedrock cross-region inference for availability if needed), or deploy the agent in the Region of the "
    "endpoint it calls most. Keep the cross-Region call only when the model or feature is not available in the "
    "workload Region or data residency requires it, and record that with `# noqa: LLM-18`."
)
REC_STORE = (
    "Colocate the agent with its vector store: create or move the index/collection into the workload Region, or "
    "deploy the agent next to the store. Keep the cross-Region store only when data residency or a feature that "
    "is not offered in the workload Region requires it, and record that with `# noqa: LLM-18`."
)
RECOMMENDATION = REC_MODEL
LIMITATION = (
    "Static topology scan only: LLM-18 compares literal Regions, not traffic, latency or transfer volume, so no "
    "measurements are emitted. It is an OQ-8 judgement row: findings are candidates for reviewer confirmation. "
    "Not visible: Regions set by variables, env lookups, CLI flags (`${opt:region}`), deploy pipelines or other "
    "repositories; whether the model or feature is offered in the workload Region; data-residency requirements; "
    "multi-Region deployments (several declared workload Regions are not judged); Azure OpenAI, Vertex AI and other "
    "non-AWS Regions; Pinecone/pgvector hosts that do not name an AWS Region. Bedrock cross-region inference "
    "profiles route from the configured Region by design and are not flagged. Development/test and example files "
    "are not judged."
)

_R = (r"(?:us|eu|ap|sa|ca|me|af|il|mx)(?:-gov|-iso[bef]?)?-"
      r"(?:north|south|east|west|central|northeast|southeast|northwest|southwest)-\d")
REGION = re.compile(rf"(?<![A-Za-z0-9-]){_R}(?![A-Za-z0-9-])", re.I)
FULL = re.compile(rf"^{_R}$")

SERVICES = {
    "bedrock": "bedrock", "bedrock-runtime": "bedrock", "bedrock-agent": "bedrock",
    "bedrock-agent-runtime": "bedrock", "bedrock-agentcore": "bedrock", "bedrock-agentcore-control": "bedrock",
    "sagemaker-runtime": "sagemaker", "sagemaker": "sagemaker",
    "opensearch": "opensearch", "opensearchserverless": "opensearch", "es": "opensearch",
    "kendra": "kendra", "s3vectors": "s3vectors",
}
LABELS = {
    "bedrock": "Bedrock model/agent endpoint", "sagemaker": "SageMaker endpoint", "llm": "LLM endpoint",
    "opensearch": "OpenSearch vector store", "kendra": "Kendra index", "s3vectors": "S3 Vectors index",
    "pinecone": "Pinecone index", "pgvector": "pgvector database", "vector": "vector store",
}
STORES = {"opensearch", "kendra", "s3vectors", "pinecone", "pgvector", "vector"}
# SDK wrappers whose class name names the service (ChatBedrockConverse, BedrockEmbeddings, AnthropicBedrock, ...).
_CTOR = re.compile(r"(Bedrock|KnowledgeBases|AgentCore)|(SageMaker|Sagemaker)|(OpenSearch)|(Kendra)|(S3Vectors)")
_CTOR_KINDS = ("bedrock", "sagemaker", "opensearch", "kendra", "s3vectors")
REGION_KWARGS = ("region_name", "region", "aws_region")
SIGNERS = {"AWSV4SignerAuth": 1, "AWSV4SignerAsyncAuth": 1, "Urllib3AWSV4SignerAuth": 1, "AWS4Auth": 2}
# Setting names: the workload's own Region, and dependency Regions by name token (first match wins).
WORKLOAD_SETTINGS = {"aws_region", "aws_default_region", "cdk_default_region"}
SETTING_KINDS = (("bedrock", "bedrock"), ("agentcore", "bedrock"), ("sagemaker", "sagemaker"),
                 ("opensearch", "opensearch"), ("aoss", "opensearch"), ("kendra", "kendra"),
                 ("s3vectors", "s3vectors"), ("pinecone", "pinecone"), ("pgvector", "pgvector"),
                 ("vector", "vector"), ("embed", "llm"), ("llm", "llm"), ("model", "llm"), ("inference", "llm"))
TF_DEP = re.compile(r"^aws_(bedrock|opensearch|sagemaker_endpoint|kendra|s3vectors)")
TF_KINDS = {"bedrock": "bedrock", "opensearch": "opensearch", "sagemaker_endpoint": "sagemaker",
            "kendra": "kendra", "s3vectors": "s3vectors"}
SKIP_NAMES = {"example", "examples", "sample", "samples"}
CI_DIRS = {".github", ".gitlab", ".circleci", ".buildkite"}
CI_FILES = {".gitlab-ci.yml", "bitbucket-pipelines.yml", "azure-pipelines.yml", "jenkinsfile"}
ENV_TEMPLATES = {"template", "dist", "tpl"}  # .env.template, .env.dist

_KEY = r"[A-Za-z][A-Za-z0-9_.-]*?region(?:[_-]?name)?"
_SETTING = re.compile(rf"""(?<![A-Za-z0-9_.-])["']?({_KEY})["']?\s*[:=]\s*["'`]?({_R})(?![A-Za-z0-9-])""", re.I)
_K8S_ENV = re.compile(rf"""name:\s*["']?({_KEY})["']?[ \t]*\n[ \t]*value:\s*["']?({_R})(?![A-Za-z0-9-])""", re.I)
_JSON_ENV = re.compile(rf""""name"\s*:\s*"({_KEY})"\s*,\s*"value"\s*:\s*"({_R})\"""", re.I)
_HOST = re.compile(rf"(?<![A-Za-z0-9.-])((?:[a-z0-9-]+\.)+?)({_R})\.((?:es|aoss|rds)\.)?amazonaws\.com(?![A-Za-z0-9-])")
_TS_NEW = re.compile(r"new\s+([A-Za-z_$][\w$]*)\s*\(\s*\{([^{}]*)\}")
_TS_REGION = re.compile(rf"""\bregion\s*:\s*["'`]({_R})["'`]""")
_CDK_ENV = re.compile(r"""\benv\s*:\s*\{([^{}]*)\}""")
_TF_SETTING_REF = re.compile(rf"""(?<![A-Za-z0-9_.-])["']?({_KEY})["']?\s*=\s*((?:var|local)\.[A-Za-z_][\w-]*)\s*$""", re.I)
_TFVAR = re.compile(rf"""^\s*([A-Za-z_]\w*)\s*=\s*"({_R})"\s*$""")
_TF_REF = re.compile(r"^(var|local)\.([A-Za-z_][\w-]*)$")
_TOML_TABLE = re.compile(r"^\s*\[([^\]]+)\]")
_TOML_REGION = re.compile(rf"""^\s*region\s*=\s*["']({_R})["']""")
_PY_HINT = re.compile(r"bedrock|sagemaker|opensearch|aoss|kendra|pinecone|ServerlessSpec|s3vectors|amazonaws\.com|"
                      r"aws_cdk|region|AWS4Auth|AWSV4Signer", re.I)
_TF_PROVIDER = re.compile(r'^\s*provider\s*(?:"aws"\s*\{|=\s*aws\.)', re.M)  # Regions may come from variables.tf
_TF_HINT = re.compile(r'provider\s+"aws"|"aws_(bedrock|opensearch|sagemaker_endpoint|kendra|s3vectors)|^\s*variable\s|'
                      r"^\s*locals\s", re.M)
_SLASH_NOQA = re.compile(r"//\s*noqa\b\s*(?::\s*([A-Za-z0-9_, -]+))?", re.I)
TEXT_EXTS = (".yaml", ".yml", ".json", ".toml", ".tfvars")
JS_EXTS = (".ts", ".tsx", ".js", ".mjs", ".cjs")


@dataclass(frozen=True)
class Dep:
    """A dependency configured with an explicit Region."""

    line: int
    end_line: int
    kind: str
    region: str
    anchor: str
    what: str
    confidence: str


@dataclass
class Ctx:
    locator: str
    lines: list
    deps: list = field(default_factory=list)
    workloads: list = field(default_factory=list)  # (region, where)
    tf_workloads: list = field(default_factory=list)  # (raw HCL value, where)
    tf_aliases: list = field(default_factory=list)  # (alias, raw HCL value)
    tf_values: list = field(default_factory=list)  # ((var|local, name), raw HCL value)
    tf_uses: list = field(default_factory=list)  # (line, end_line, address, kind, alias)
    tf_settings: list = field(default_factory=list)  # (line, setting name, `var.x`/`local.x`)
    slash_comments: bool = False

    @property
    def dir(self):
        return "/".join(re.split(r"[\\/]", self.locator)[:-1])

    @property
    def found(self):
        return bool(self.deps or self.workloads or self.tf_workloads or self.tf_aliases or self.tf_values
                    or self.tf_uses or self.tf_settings)


def _line_of(content, offset):
    return content.count("\n", 0, offset) + 1


def _setting_kind(key):
    lower = key.lower()
    if lower in WORKLOAD_SETTINGS:
        return "workload"
    for token, kind in SETTING_KINDS:
        if token in lower:
            return kind
    return None


def _host_kind(prefix, suffix, pgvector):
    if suffix in ("es.", "aoss."):
        return "opensearch"
    if suffix == "rds.":
        return "pgvector" if pgvector else None
    label = prefix.rstrip(".").rsplit(".", 1)[-1]
    if label.startswith("bedrock"):
        return "bedrock"
    if "sagemaker" in label:
        return "sagemaker"
    if label.startswith("kendra"):
        return "kendra"
    if label.startswith("s3vectors"):
        return "s3vectors"
    return None


def _hosts(ctx, text, line, end_line, pgvector):
    for match in _HOST.finditer(text):
        kind = _host_kind(match.group(1), match.group(3), pgvector)
        if kind:
            host = match.group(0)
            ctx.deps.append(Dep(line, end_line, kind, match.group(2), f"endpoint/{host}", f"Endpoint {host}",
                                "medium"))


def _setting(ctx, key, region, line, end_line=None):
    kind, region = _setting_kind(key), region.lower()
    if kind == "workload":
        ctx.workloads.append((region, f"{ctx.locator} ({key})"))
    elif kind:
        ctx.deps.append(Dep(line, end_line or line, kind, region, f"setting/{key}@{region}", f"Setting {key}", "low"))


def _scan_text(ctx, content, yaml_like):
    """Settings and endpoint hosts in a config/IaC/JS file (comment lines blanked)."""
    lines = []
    for raw in content.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith(("#", "//", ";")):
            lines.append("")
        else:
            lines.append(miniyaml.strip_comment(raw) if yaml_like else raw)
    clean = "\n".join(lines)
    pgvector = "pgvector" in content.lower()
    for number, line in enumerate(lines, 1):
        for match in _SETTING.finditer(line):
            _setting(ctx, match.group(1), match.group(2), number)
        _hosts(ctx, line, number, number, pgvector)
    for pattern in (_K8S_ENV, _JSON_ENV):
        for match in pattern.finditer(clean):
            _setting(ctx, match.group(1), match.group(2), _line_of(clean, match.start()),
                     _line_of(clean, match.start(2)))
    return clean


# -- Python ----------------------------------------------------------------------------------


def _py_region(ctx, node):
    if node is None:
        return None
    text, done = static_text(ctx, node)
    return text if done and FULL.match(text) else None


def _py_string(ctx, node):
    if node is None:
        return None
    text, done = static_text(ctx, node)
    return text if done else None


def _arg(node, index):
    return node.args[index] if len(node.args) > index else None


def _session_region(ctx, receiver):
    session = resolve(ctx, receiver)
    if isinstance(session, ast.Call):
        name = session.func.attr if isinstance(session.func, ast.Attribute) else getattr(session.func, "id", "")
        if name == "Session":
            keywords = call_keywords(ctx, session) or {}
            return _py_region(ctx, keywords.get("region_name"))
    return None


def _py_call(tree, ctx, node, cdk_envs):
    """Record a dependency client created by one call (`tree` is the parsed file). In a CDK app (`cdk_envs` is a
    list), also record each stack environment's Region (None when it is not a literal)."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if not name:
        return
    keywords = call_keywords(tree, node)
    if keywords is None:
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    qual = tree.qualname(node)
    py_string = lambda value: _py_string(tree, value)  # noqa: E731
    py_region = lambda value: _py_region(tree, value)  # noqa: E731
    span = (node.lineno, node.end_lineno or node.lineno)

    def dep(kind, region, anchor, what):
        ctx.deps.append(Dep(*span, kind, region, anchor, what, "medium"))

    if name in ("client", "resource", "create_client"):
        service = py_string(keywords.get("service_name", _arg(node, 0)))
        if service in SERVICES:
            region = py_region(keywords.get("region_name", _arg(node, 1)))
            if region is None and isinstance(func, ast.Attribute):
                region = _session_region(tree, func.value)
            if region:
                dep(SERVICES[service], region, f"{qual}:client/{service}@{region}", f"boto3 client {service!r}")
    elif name == "ServerlessSpec":
        region = py_region(keywords.get("region"))
        if region and py_string(keywords.get("cloud")) == "aws":
            dep("pinecone", region, f"{qual}:ServerlessSpec@{region}", "Pinecone ServerlessSpec(cloud='aws')")
    elif name in SIGNERS:
        index = SIGNERS[name]
        service = py_string(keywords.get("service", _arg(node, index + 1)))
        region = py_region(keywords.get("region", _arg(node, index)))
        if region and service in ("es", "aoss"):
            dep("opensearch", region, f"{qual}:{name}@{region}", f"OpenSearch request signer {name}")
    elif name == "Environment" and cdk_envs is not None:
        cdk_envs.append(py_region(keywords.get("region")))
    elif name[:1].isupper() and (match := _CTOR.search(name)):
        region = next((r for kw in REGION_KWARGS if (r := py_region(keywords.get(kw)))), None)
        if region:
            kind = _CTOR_KINDS[match.lastindex - 1]
            dep(kind, region, f"{qual}:{name}@{region}", f"{name}(...)")
    if cdk_envs is not None and "env" in keywords:
        env = resolve(tree, keywords["env"])
        if not (isinstance(env, ast.Call) and getattr(env.func, "attr", getattr(env.func, "id", "")) == "Environment"):
            items = dict_items(tree, env) if env is not None else None
            cdk_envs.append(py_region(items.get("region")) if items else None)


def _parse_python(locator, content):
    try:
        tree = static.Ctx(locator, content)
    except (SyntaxError, ValueError):
        raise ParseError("invalid Python") from None
    ctx = Ctx(locator, tree.lines)
    cdk_envs = [] if any(path.split(".")[0] == "aws_cdk" for path in tree.aliases.values()) else None
    docstrings = {id(n.value) for n in ast.walk(tree.tree)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    pgvector = "pgvector" in content.lower()
    for node in ast.walk(tree.tree):
        if isinstance(node, ast.Call):
            _py_call(tree, ctx, node, cdk_envs)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            _hosts(ctx, node.value, node.lineno, node.end_lineno or node.lineno, pgvector)
    _cdk_workload(ctx, cdk_envs)
    return ctx


def _cdk_workload(ctx, regions):
    """A CDK app declares workload Regions only when every stack environment pins a literal Region (an app that
    pins only its us-east-1 WAF/CloudFront stack and leaves the rest to the CLI declares none)."""
    if regions and None not in regions:
        ctx.workloads.extend((region, f"{ctx.locator} (CDK env region)") for region in dict.fromkeys(regions))


# -- Terraform -------------------------------------------------------------------------------


def _regionish(raw):
    """A literal Region or a `var.x`/`local.x` reference (the only HCL values Regions are resolved through)."""
    value = _hcl_string(raw)
    return bool(FULL.match(value)) if value is not None else bool(_TF_REF.match(raw or ""))


def _parse_terraform(locator, content):
    ctx = Ctx(locator, content.splitlines())
    clean = _scan_text(ctx, content, yaml_like=True)
    for number, line in enumerate(clean.splitlines(), 1):  # `BEDROCK_REGION = var.bedrock_region`
        match = _TF_SETTING_REF.search(line)
        kind = _setting_kind(match.group(1)) if match else None
        if kind == "workload":
            ctx.tf_workloads.append((match.group(2), f"{locator} ({match.group(1)})"))
        elif kind:
            ctx.tf_settings.append((number, match.group(1), match.group(2)))
    if not _TF_HINT.search(content) and not ctx.found:
        raise Unsupported(locator)
    try:
        blocks = parse_hcl(content)
    except HclError as error:
        raise ParseError(str(error)) from None
    for block in blocks:
        labels = block.labels
        if block.type == "provider" and labels[:1] == ("aws",):
            alias = _hcl_string(block.attrs.get("alias", ("",))[0])
            raw = block.attrs.get("region", (None,))[0]
            if raw and alias:
                ctx.tf_aliases.append((alias, raw))
            elif raw:
                ctx.tf_workloads.append((raw, f'{locator} (provider "aws")'))
        elif block.type == "variable" and labels and _regionish(block.attrs.get("default", ("",))[0]):
            ctx.tf_values.append((("var", labels[0]), block.attrs["default"][0]))
        elif block.type == "locals":
            ctx.tf_values.extend((("local", key), raw) for key, (raw, _) in block.attrs.items() if _regionish(raw))
        elif block.type in ("resource", "data") and len(labels) >= 2 and (match := TF_DEP.match(labels[0])):
            provider = re.fullmatch(r"aws\.([A-Za-z_][\w-]*)", block.attrs.get("provider", ("",))[0])
            if provider:
                address = ("data." if block.type == "data" else "") + f"{labels[0]}.{labels[1]}"
                kind = TF_KINDS[match.group(1)]
                ctx.tf_uses.append((block.line, block.end_line, address, kind, provider.group(1)))
    return ctx


# -- parsing ---------------------------------------------------------------------------------


def _not_evaluated(locator):
    marked = _dev_path(locator)
    if marked:
        raise NotEvaluated(f"path marks a development/test file ({marked}); LLM-18 v1 evaluates deployed config only")
    parts = [p.lower() for p in re.split(r"[\\/]", locator)]
    if CI_DIRS & set(parts[:-1]) or parts[-1] in CI_FILES or parts[-1].startswith("buildspec"):
        raise NotEvaluated("CI/CD pipeline configuration; its Region is where the pipeline runs, not the workload")
    tokens = set(re.split(r"[._-]", parts[-1])) | set(parts[:-1])
    skipped = sorted(tokens & SKIP_NAMES)
    if parts[-1].startswith(".env."):
        skipped += sorted(tokens & ENV_TEMPLATES)
    if skipped:
        raise NotEvaluated(f"path marks an example/template file ({skipped[0]}); its Regions are placeholders")


def _kind_of_file(locator):
    lower = locator.lower()
    name = re.split(r"[\\/]", lower)[-1]
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(".tf"):
        return "terraform"
    if lower.endswith(JS_EXTS):
        return "js"
    if lower.endswith(TEXT_EXTS) or name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return "text"
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "text"
    return None


def _yaml_workloads(ctx, content, name):
    if not name.startswith(("serverless.", "samconfig.")) or not name.endswith((".yml", ".yaml")):
        return
    try:
        documents = miniyaml.load_all(content)
    except miniyaml.YamlError as error:
        raise ParseError(str(error)) from None
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        if name.startswith("serverless."):
            provider = document.get("provider")
            region = provider.get("region") if isinstance(provider, Mapping) else None
            if isinstance(region, Scalar) and region.value and FULL.match(region.value):
                ctx.workloads.append((region.value, f"{ctx.locator} (provider.region)"))
            continue
        default = document.get("default")
        for command in default.items.values() if isinstance(default, Mapping) else ():
            parameters = command.get("parameters") if isinstance(command, Mapping) else None
            region = parameters.get("region") if isinstance(parameters, Mapping) else None
            if isinstance(region, Scalar) and region.value and FULL.match(region.value):
                ctx.workloads.append((region.value, f"{ctx.locator} (samconfig default region)"))


def parse(locator, content):
    if not REGION.search(content) and not (locator.lower().endswith(".tf") and _TF_PROVIDER.search(content)):
        raise Unsupported(locator)
    kind = _kind_of_file(locator)
    name = re.split(r"[\\/]", locator.lower())[-1]
    if kind == "python":
        if not _PY_HINT.search(content):
            raise Unsupported(locator)
        _not_evaluated(locator)
        ctx = _parse_python(locator, content)
    elif kind == "terraform":
        ctx = _parse_terraform(locator, content)
        if ctx.found:
            _not_evaluated(locator)
    elif kind in ("text", "js"):
        ctx = Ctx(locator, content.splitlines())
        clean = _scan_text(ctx, content, yaml_like=kind == "text" and not name.endswith(".json"))
        if kind == "js":
            ctx.slash_comments = True
            for match in _TS_NEW.finditer(clean):
                ctor, region = match.group(1), _TS_REGION.search(match.group(2))
                found = _CTOR.search(ctor)
                if found and region:
                    start = _line_of(clean, match.start())
                    end = _line_of(clean, match.start(2) + region.start())
                    ctx.deps.append(Dep(start, end, _CTOR_KINDS[found.lastindex - 1], region.group(1),
                                        f"client/{ctor}@{region.group(1)}", f"new {ctor}(...)", "medium"))
            if "aws-cdk-lib" in content or "@aws-cdk" in content:
                envs = [_TS_REGION.search(m.group(1)) for m in _CDK_ENV.finditer(clean)]
                _cdk_workload(ctx, [m.group(1) if m else None for m in envs])
        elif name.endswith(".tfvars"):
            for line in clean.splitlines():
                match = _TFVAR.match(line)
                if match:
                    ctx.tf_values.append((("var", match.group(1)), f'"{match.group(2)}"'))
        elif name.startswith("samconfig.") and name.endswith(".toml"):
            table = ""
            for line in clean.splitlines():
                header = _TOML_TABLE.match(line)
                table = header.group(1).strip() if header else table
                match = _TOML_REGION.match(line)
                if match and table.startswith("default."):
                    ctx.workloads.append((match.group(1), f"{locator} (samconfig default region)"))
        if ctx.found or name.startswith(("serverless.", "samconfig.")):
            _not_evaluated(locator)
        _yaml_workloads(ctx, content, name)
    else:
        raise Unsupported(locator)
    if not ctx.found:
        raise Unsupported(locator)
    return ctx


# -- project ---------------------------------------------------------------------------------


class Project:
    def __init__(self):
        self.ctxs = []
        self.workloads = []  # (region, where)
        self.tf_workloads, self.tf_aliases, self.tf_values = [], {}, {}
        self.region, self.reason, self.where = None, None, ""
        self.unresolved = []
        self.failover = 0

    def add(self, ctx):
        """Terraform variables, locals and provider aliases are module-scoped: keyed by the file's directory."""
        self.ctxs.append(ctx)
        self.workloads.extend(ctx.workloads)
        self.tf_workloads.extend((raw, where, ctx.dir) for raw, where in ctx.tf_workloads)
        for alias, raw in ctx.tf_aliases:
            self.tf_aliases.setdefault((ctx.dir, alias), set()).add(raw)
        for key, raw in ctx.tf_values:
            self.tf_values.setdefault((ctx.dir, *key), set()).add(raw)

    def tf_region(self, raw, module, depth=0):
        """Literal Region of an HCL value: a string, or a `var.x`/`local.x` of the module with exactly one literal
        value (a variable default and a different tfvars value make it unresolved); else None."""
        value = _hcl_string(raw)
        if value is not None:
            return value if FULL.match(value) else None
        ref = _TF_REF.match(raw or "")
        values = self.tf_values.get((module, ref.group(1), ref.group(2)), set()) if ref else set()
        if len(values) != 1 or depth > 3:
            return None
        return self.tf_region(next(iter(values)), module, depth + 1)

    def alias_region(self, alias, module):
        regions = {self.tf_region(raw, module) for raw in self.tf_aliases.get((module, alias), set())}
        return regions.pop() if len(regions) == 1 else None

    def finalize(self):
        declared = list(self.workloads)
        declared += [(region, where) for raw, where, module in self.tf_workloads
                     if (region := self.tf_region(raw, module))]
        regions = {}
        for region, where in declared:
            regions.setdefault(region, []).append(where)
        if len(regions) == 1:
            self.region, wheres = next(iter(regions.items()))
            self.where = ", ".join(dict.fromkeys(wheres))
        elif not regions:
            self.reason = ("no workload Region is declared in the payload (a Terraform provider \"aws\" region, "
                           "serverless.yml provider.region, samconfig default region, CDK env region or "
                           "AWS_REGION/AWS_DEFAULT_REGION literal), so dependency Regions cannot be compared")
        else:
            listed = "; ".join(f"{region} ({', '.join(dict.fromkeys(w))})" for region, w in sorted(regions.items()))
            self.reason = (f"the payload declares several workload Regions ({listed}); LLM-18 v1 compares against a "
                           "single declared workload Region")
        if self.region:
            self.failover = sum(len(self.judge(ctx)[1]) for ctx in self.ctxs)
        self.unresolved = [f"{ctx.locator}: {address} (provider aws.{alias})" for ctx in self.ctxs
                           for _, _, address, _, alias in ctx.tf_uses if not self.alias_region(alias, ctx.dir)]

    def deps(self, ctx):
        found = list(ctx.deps)
        for line, key, raw in ctx.tf_settings:
            region = self.tf_region(raw, ctx.dir)
            if region:
                found.append(Dep(line, line, _setting_kind(key), region, f"setting/{key}@{region}",
                                 f"Setting {key} ({raw})", "low"))
        for line, end_line, address, kind, alias in ctx.tf_uses:
            region = self.alias_region(alias, ctx.dir)
            if region:
                found.append(Dep(line, end_line, kind, region, f"{address}:provider/aws.{alias}@{region}",
                                 f"{address} (provider aws.{alias})", "medium"))
        return found

    def judge(self, ctx):
        """(dependencies in another Region, those not flagged because the same file also uses that service in
        the workload Region: a failover/fallback)."""
        found = self.deps(ctx)
        clients = [d for d in found if not d.anchor.startswith("endpoint/")]
        deps = clients + [  # an endpoint_url inside a client call of the same service and Region is that client
            d for d in found if d.anchor.startswith("endpoint/") and not any(
                c.kind == d.kind and c.region == d.region and c.line <= d.line <= c.end_line for c in clients)]
        local = {dep.kind for dep in deps if dep.region == self.region}
        far = [dep for dep in deps if dep.region != self.region]
        return [d for d in far if d.kind not in local], [d for d in far if d.kind in local]


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
            continue
        parsed[(locator, content)] = ctx
        project.add(ctx)
    project.finalize()
    return parsed, project


def _slash_suppressed(ctx, line):
    def named(text):
        match = _SLASH_NOQA.search(text)
        return bool(match) and (match.group(1) is None or bool(set(re.split(r"[,\s]+", match.group(1).upper()))
                                                              & set(NOQA)))
    if named(ctx.lines[line - 1]):
        return True
    index = line - 2
    while index >= 0 and ctx.lines[index].lstrip().startswith("//"):
        if named(ctx.lines[index]):
            return True
        index -= 1
    return False


def run(ctx, project):
    hits = []
    for dep in project.judge(ctx)[0]:
        if ctx.slash_comments and _slash_suppressed(ctx, dep.line):
            continue
        label = LABELS[dep.kind]
        hits.append(TextHit(
            line=dep.line,
            end_line=dep.end_line,
            anchor=dep.anchor,
            summary=(
                f"{dep.what} puts a {label} in {dep.region}, but the workload Region declared in the payload is "
                f"{project.region} ({project.where}). Every call or query then crosses Regions, adding inter-Region "
                f"latency and data-transfer cost on each agent step. Candidate for reviewer confirmation: keep it only "
                f"if the {label} is not offered in {project.region} or data residency requires {dep.region}."
            ),
            confidence=dep.confidence,
            recommendation=REC_STORE if dep.kind in STORES else REC_MODEL,
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
        if project.region is None:
            raise NotEvaluated(project.reason)
        if not project.deps(ctx) and not any(project.deps(other) for other in project.ctxs):
            raise NotEvaluated(
                "declares only the workload Region, and the payload has no LLM or vector-store dependency with a "
                "literal Region to compare"
            )
        return ctx

    module = sys.modules[__name__]
    check = types.SimpleNamespace(
        CHECK_ID=CHECK_ID, DETECTOR_VERSION=DETECTOR_VERSION, NOQA=NOQA, REFERENCES=REFERENCES, FORMATS=FORMATS,
        RECOMMENDATION=RECOMMENDATION, LIMITATION=LIMITATION, parse=bound_parse,
        run=lambda ctx: module.run(ctx, project),
    )
    result = textstatic.evaluate_text(payload, check)
    if project.unresolved and project.region:
        result["coverage"]["limitations"].insert(
            -1, f"{len(project.unresolved)} Terraform resource(s) on an aliased provider whose region is not a literal "
                f"were not judged, e.g. {project.unresolved[0]}")
    if project.failover:
        result["coverage"]["limitations"].insert(
            -1, f"{project.failover} cross-Region dependency setting(s) were not flagged: the same file also uses that "
                f"service in the workload Region {project.region} (treated as a failover/fallback)")
    return result
