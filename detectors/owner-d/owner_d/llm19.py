"""LLM-19: inference-engine inefficiencies (KV-cache growth, attention, quantization) in self-hosted serving.

Detector semantics version 1.1.0. Only relevant when the repository hosts models, so it looks at
self-hosted vLLM and Hugging Face TGI servers only. Two evidence modes, dispatched on source kind
(like TST-12):

- static (primary, repository scans): `file:<path>` with one `static` source. Launch commands and
  serving configs (`vllm serve`, `python -m vllm.entrypoints...`, the `vllm/vllm-openai`, TGI and
  SageMaker TGI image references, `text-generation-launcher`) in Dockerfiles, shell scripts, compose/Kubernetes/ECS YAML or
  JSON, Terraform/HCL, TOML, .env/Procfile/Makefile files, and Python that builds a vLLM `LLM`/
  `EngineArgs`/`AsyncEngineArgs`. Only explicit settings are flagged: prefix caching disabled
  (vLLM), an fp32 serving dtype (vLLM) and eager mode / CUDA graphs disabled (vLLM
  `--enforce-eager`, TGI `--cuda-graphs 0` / `CUDA_GRAPHS=0`). Each flag belongs to the launcher of
  its enclosing service/container/stage block, or to the file's only launcher. Text is read, never executed.
  Files that do not launch vLLM/TGI are out of scope (`Unsupported`), so a repository that hosts
  no model reports the check not applicable.
- artifact (optional): `inference:<server_id>` with one `artifact` source, a normalized
  inference-metrics summary (for example from a vLLM `/metrics` scrape) uploaded as `llm-19.json`
  through the artifact upload endpoint. Compared with the configured preemption, KV-cache,
  prefix-cache and context-headroom thresholds.

Without an artifact, runtime evidence is reported unavailable in the limitations, never clean.
Missing, unparseable or unsupported input is never reported as clean.
"""

from __future__ import annotations

import ast
import math
import re
import sys
from dataclasses import dataclass, field

from . import dockerfile, textstatic
from .static import IDENTITY_FIELDS, SCHEMA_VERSION, EvaluationError, _require, fingerprint  # noqa: F401
from .textstatic import NotEvaluated, ParseError, TextHit, Unsupported  # noqa: F401  (scanner reads these names)

CHECK_ID = "LLM-19"
DETECTOR_VERSION = "1.1.0"
NOQA = ("LLM-19", "LLM19")
STATIC_KIND = "static"
ARTIFACT_KIND = "artifact"
ARTIFACT_NAME = "llm-19.json"  # upload name routed by owner_d/aws/artifact_handler.py
SCOPE_PREFIX = "inference:"
MAX_SERVERS = 200

ARTIFACT_SETTING_KEYS = (
    "min_requests",
    "max_preemption_ratio",
    "max_kv_cache_usage",
    "min_prefix_cache_hit_rate",
    "max_context_headroom_ratio",
)
# Reference values from "LLM-19 > Context settings" in detectors/owner-d/README.md. Static mode needs
# none of them; repository scans pass them anyway, and the artifact parser uses them unless the
# artifact's `settings` override them.
REFERENCE_SETTINGS = {
    "min_requests": 100,  # README LLM-19: fewer completed requests is too small a sample
    "max_preemption_ratio": 0.01,  # README LLM-19: preemptions per completed request
    "max_kv_cache_usage": 0.95,  # README LLM-19: peak KV-cache usage (fraction)
    "min_prefix_cache_hit_rate": 0.05,  # README LLM-19: prefix-cache hit rate floor
    "max_context_headroom_ratio": 4,  # README LLM-19: max_model_len / largest observed request
}

FORMATS = (
    "vLLM/TGI launch commands and serving configs (Dockerfile, *.sh, *.yaml/*.yml, *.json, *.tf/*.hcl, *.toml, "
    ".env, Procfile, Makefile) and Python files that import vllm"
)
REFERENCES = (
    "https://docs.vllm.ai/en/latest/configuration/engine_args.html",
    "https://docs.vllm.ai/en/latest/configuration/optimization.html",
    "https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html",
    "https://docs.vllm.ai/en/latest/usage/metrics.html",
    "https://huggingface.co/docs/text-generation-inference/reference/launcher",
    "https://huggingface.co/docs/text-generation-inference/conceptual/chunking",
)
RECOMMENDATION = (
    "Serve with the engine defaults unless a measured reason requires otherwise: keep prefix caching and CUDA "
    "graphs on, serve in bfloat16/float16 (or a quantized checkpoint), and size the KV cache and context length "
    "from observed traffic."
)
RECOMMENDATIONS = {
    "prefix-caching-disabled": (
        "Remove --no-enable-prefix-caching / enable_prefix_caching=False. vLLM's automatic prefix caching reuses "
        "the KV cache of shared prompt prefixes (system prompts, chat history, documents) and in general does not "
        "reduce performance when prefixes are not shared."
    ),
    "fp32-dtype": (
        "Serve in bfloat16 or float16 (--dtype auto picks the checkpoint's half-precision dtype), or use a quantized "
        "checkpoint/--quantization. fp32 doubles weight and KV-cache memory and runs matmuls far slower than half "
        "precision on GPUs with tensor cores."
    ),
    "eager-mode": (
        "Drop --enforce-eager / enforce_eager=True (vLLM) or CUDA_GRAPHS=0 / --cuda-graphs 0 (TGI) in production. "
        "Eager mode skips CUDA-graph capture for faster startup at the cost of steady-state decode performance; "
        "keep it for development or debugging only."
    ),
    "preemptions": (
        "Give the KV cache more room or admit less work per step: raise gpu_memory_utilization, lower max_num_seqs "
        "or max_num_batched_tokens, use an fp8 KV cache (--kv-cache-dtype fp8) or a quantized model, or add "
        "tensor/pipeline parallelism. Preempted requests are recomputed, which wastes GPU time."
    ),
    "kv-cache-saturation": (
        "The KV cache runs full, so requests queue or get preempted: compress it (--kv-cache-dtype fp8), lower "
        "max_model_len / --max-total-tokens to what traffic needs, quantize the weights to free memory, or scale out."
    ),
    "low-prefix-cache-hit-rate": (
        "Almost no prompt tokens are served from the prefix cache. If prefix caching is disabled on this server, "
        "enable it; otherwise put stable content (system prompt, tool definitions, documents) first and variable "
        "content last, and route a conversation to the same replica (sticky sessions) so its cached prefix is found."
    ),
    "oversized-context": (
        "Lower max_model_len (vLLM) or --max-total-tokens (TGI) towards the largest request the server actually "
        "sees, with headroom. TGI documents that a larger value makes each request take more memory and batching "
        "less effective."
    ),
}
LIMITATION = (
    "LLM-19 static mode proves explicit serving settings only: vLLM --no-enable-prefix-caching / "
    "enable_prefix_caching=False, an fp32 --dtype / dtype, and --enforce-eager / enforce_eager=True, and TGI "
    "--cuda-graphs 0 / CUDA_GRAPHS=0, in launch commands and serving configs, and in vLLM LLM/EngineArgs/"
    "AsyncEngineArgs constructors with constant keyword arguments in Python. A flag is attributed to the launcher "
    "of its enclosing block (compose service, Kubernetes/ECS container, Dockerfile stage, logical shell line), or "
    "to the file's only launcher; a flag in a block shared by several engines is not judged. Defaults, settings "
    "built at runtime ($VARS, templating, **kwargs), vLLM --config YAML files, SageMaker LMI (djl-inference) "
    "OPTION_* settings, other engines (SGLang, llama.cpp, TensorRT-LLM, Triton) and managed APIs are not judged; "
    "model size, attention kernels and missing quantization are not flagged. Development, test, example and CI "
    "paths are not evaluated."
)
RUNTIME_UNAVAILABLE = (
    "LLM-19 runtime evidence is unavailable: no inference-metrics artifact (llm-19.json: KV-cache usage, "
    "preemptions, prefix-cache hit rate) was supplied, so KV-cache growth and preemption waste are not "
    "evaluated, not clean."
)
ARTIFACT_LIMITATION = (
    "LLM-19 artifact mode compares a client-supplied, normalized inference-metrics summary with thresholds; the "
    "values are not re-measured. Findings describe engine behaviour over the reported window, not measured "
    "energy or cost."
)

# ---- static mode --------------------------------------------------------------------------------

TEXT_SUFFIXES = (".sh", ".bash", ".yaml", ".yml", ".json", ".tf", ".hcl", ".toml", ".env", ".service", ".mk")
TEXT_NAMES = {"procfile", "makefile", "justfile", ".env", "gnumakefile"}
# Path parts and file-name tokens that mark configs outside production serving.
NON_PRODUCTION = {
    "dev", "devel", "develop", "development", "debug", "local", "test", "tests", "testing", "e2e", "example",
    "examples", "sample", "samples", "demo", "demos", "fixture", "fixtures", "devcontainer", "github",
    "workflows", "ci", "notebooks", "benchmark", "benchmarks",
}
# Image references `[registry[:port]/]namespace/.../name[:tag|@digest]` that start a token (after whitespace, a
# quote, `=`, `:`, `,`, `[` or a `${...}` interpolation). Path segments cannot be empty or contain `:` other than a
# registry port, so URLs such as http://vllm-openai:8000/v1 (an API client) or
# https://github.com/huggingface/text-generation-inference never match. A bare `vllm-openai` counts only as the
# value of an `image` key.
_IMAGE_START = r"(?:^|(?<=[\s\"'=:,\[}]))"
_SEGMENT = r"(?:[\w.-]+(?::\d+)?/|(?<=})/)"
_IMAGE_END = r"(?=[:@\s\"',\]]|$)"
ENGINE_MARKERS = (
    ("vllm", re.compile(
        r"\bvllm\s+serve\b|\bvllm\.entrypoints\."
        rf"|{_IMAGE_START}{_SEGMENT}+vllm-openai{_IMAGE_END}"
        rf"|\bimage[\"']?\s*[:=]\s*[\"']?vllm-openai{_IMAGE_END}")),
    ("tgi", re.compile(
        r"\btext-generation-launcher\b"
        rf"|{_IMAGE_START}{_SEGMENT}*huggingface/text-generation-inference{_IMAGE_END}"
        rf"|{_IMAGE_START}{_SEGMENT}+huggingface-pytorch-tgi-inference{_IMAGE_END}")),  # SageMaker TGI DLC
    ("other", re.compile(
        r"\bsglang\b|\bllama-server\b|\blmdeploy\b|\btrtllm-serve\b|\btritonserver\b|\baphrodite\b|\bollama\b",
        re.I)),
)
ENGINES = {"vllm": "vLLM", "tgi": "TGI"}
_COMMENT = re.compile(r"(?:^|(?<=\s))#.*$")
_TOKEN = re.compile(r"[^\s,\[\]{}()]+")
_TGI_CUDA_GRAPHS_ENV = re.compile(r"\bCUDA_GRAPHS\b\s*[=:]\s*[\"']?0[\"']?(?![\w,.])")
_ENV_NAME = re.compile(r"^\s*-?\s*name\s*:\s*[\"']?CUDA_GRAPHS[\"']?\s*$")
_ENV_VALUE_ZERO = re.compile(r"^\s*value\s*:\s*[\"']?0[\"']?\s*$")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+vllm[\s.]|import\s+vllm\b)", re.M)
VLLM_CONSTRUCTORS = {"LLM", "EngineArgs", "AsyncEngineArgs"}
FP32_DTYPES = {"float32", "float"}
TRUE_VALUES = {"true", "1", "yes", "on"}


def _non_production(locator):
    parts = [part.lower() for part in re.split(r"[\\/]", locator) if part]
    tokens = set(re.split(r"[._-]", parts[-1])) | {part.lstrip(".") for part in parts[:-1]}
    marked = sorted(tokens & NON_PRODUCTION)
    return f"path marks a development/test/example/CI config ({marked[0]})" if marked else None


def _is_text_config(locator):
    name = locator.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (dockerfile.is_dockerfile(locator) or name in TEXT_NAMES or name.endswith(TEXT_SUFFIXES)
            or name.startswith(".env."))


@dataclass
class Context:
    locator: str
    lines: list
    kind: str  # "text" | "python"
    code: list = field(default_factory=list)  # text: lines without comments
    markers: list = field(default_factory=list)  # text: [(line, column, engine)]
    tree: ast.AST | None = None
    parents: list = field(default_factory=list)  # text: enclosing block line of each line (0 = whole file)
    inherits: dict = field(default_factory=dict)  # Dockerfile: stage line -> line of the stage it is built FROM
    engines: dict = field(default_factory=dict)  # text: line -> engines of the markers inside that block


def _markers(code):
    found = []
    for number, line in enumerate(code, 1):
        for engine, pattern in ENGINE_MARKERS:
            for match in pattern.finditer(line):
                found.append((number, match.start(), engine))
    return found


_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.I)
_TOML_TABLE = re.compile(r"^\[")


def _heads(code):
    """1-based line of the first line of each logical line (`\\` continuations join the line above)."""
    heads = [0]
    for number, line in enumerate(code, 1):
        joined = number > 1 and code[number - 2].rstrip().endswith("\\")
        heads.append(heads[number - 1] if joined else number)
    return heads


def _dockerfile_blocks(code, heads):
    """Each instruction belongs to its build stage (`FROM` line); a stage built FROM an earlier stage inherits it."""
    parents, inherits, stages, stage = [0] * (len(code) + 1), {}, {}, 0
    for number, line in enumerate(code, 1):
        if heads[number] != number:
            parents[number] = heads[number]
            continue
        match = _FROM.match(line)
        if match:
            stage = number
            base = stages.get(match.group(1).lower())
            if base:
                inherits[number] = base
            if match.group(2):
                stages[match.group(2).lower()] = number
            continue
        parents[number] = stage
    return parents, inherits


def _indent_blocks(code, heads, toml):
    """Indentation tree (YAML, pretty-printed JSON/HCL, shell, Makefile, ...): a line's block is the nearest less
    indented line above it. A YAML list item (`- `) nests under a key at its own column; a TOML `[table]` header
    holds the keys below it; `---` starts a new YAML document."""
    parents, stack = [0] * (len(code) + 1), []
    for number, line in enumerate(code, 1):
        if heads[number] != number:
            parents[number] = heads[number]
            continue
        expanded = line.expandtabs(8)
        text = expanded.strip()
        if not text:
            continue
        if text == "---":
            stack = []
            continue
        indent = len(expanded) - len(expanded.lstrip())
        if text == "-" or text.startswith("- "):
            indent += 0.5
        elif toml and indent == 0 and _TOML_TABLE.match(text):
            indent = -1
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parents[number] = stack[-1][1] if stack else 0
        stack.append((indent, number))
    return parents


def _block_engines(markers, parents):
    """line -> set of engines whose markers sit on that line or in the block it opens; 0 is the whole file."""
    engines = {}
    for number, _, engine in markers:
        line = number
        while True:
            engines.setdefault(line, set()).add(engine)
            if line == 0:
                break
            line = parents[line]
    return engines


def parse(locator, content):
    """Context for a file that launches vLLM/TGI; `Unsupported` for anything else (out of scope)."""
    if locator.endswith(".py"):
        if not _PY_IMPORT.search(content):
            raise Unsupported(locator)
        reason = _non_production(locator)
        if reason:
            raise NotEvaluated(reason + "; LLM-19 v1 evaluates production serving configs only")
        try:
            tree = ast.parse(content, filename=locator)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
            raise ParseError(type(error).__name__) from None
        return Context(locator, content.splitlines(), "python", tree=tree)
    if not _is_text_config(locator):
        raise Unsupported(locator)
    lines = content.splitlines()
    code = [_COMMENT.sub("", line) for line in lines]
    markers = _markers(code)
    if not any(engine in ENGINES for _, _, engine in markers):
        raise Unsupported(locator)  # no self-hosted vLLM/TGI server launched here
    reason = _non_production(locator)
    if reason:
        raise NotEvaluated(reason + "; LLM-19 v1 evaluates production serving configs only")
    heads = _heads(code)
    inherits = {}
    if dockerfile.is_dockerfile(locator):
        parents, inherits = _dockerfile_blocks(code, heads)
    else:
        parents = _indent_blocks(code, heads, locator.lower().endswith(".toml"))
    return Context(locator, lines, "text", code=code, markers=markers, parents=parents, inherits=inherits,
                   engines=_block_engines(markers, parents))


def _engine_at(ctx, line, column=0):
    """The engine a setting on `line` belongs to: the launcher of the innermost enclosing block (compose service,
    Kubernetes/ECS container, Dockerfile stage, logical shell line, ...) that holds a launch marker. A file-level
    match counts only when the file has one launcher; a block with launchers of several engines is ambiguous
    (None), except on the setting's own line, where the closest marker before it wins."""
    node, seen = line, set()
    while node not in seen:
        seen.add(node)
        engines = ctx.engines.get(node, set())
        if len(engines) == 1:
            return next(iter(engines))
        if len(engines) > 1:
            own = sorted((col, engine) for number, col, engine in ctx.markers if number == line)
            if node == line and own:
                before = [engine for col, engine in own if col <= column]
                return before[-1] if before else own[0][1]
            return None
        if node == 0:
            return None
        base = ctx.inherits.get(node)
        while base and base not in seen and not ctx.engines.get(base):
            seen.add(base)
            base = ctx.inherits.get(base)
        node = base if base and ctx.engines.get(base) else ctx.parents[node]
    return None


def _tokens(ctx):
    tokens = []
    for number, line in enumerate(ctx.code, 1):
        text = line.replace('"', " ").replace("'", " ").rstrip().rstrip("\\")
        tokens.extend((match.group(0), number, match.start()) for match in _TOKEN.finditer(text)
                      if match.group(0) != "-")
    return tokens


def _flag_value(tokens, index, inline):
    """(value, line) of a flag: `--flag=value`, or the next token on the same or one of the next two lines."""
    flag_line = tokens[index][1]
    if inline is not None:
        return inline, flag_line
    if index + 1 < len(tokens):
        value, line, _ = tokens[index + 1]
        if line - flag_line <= 2 and not value.startswith("-") and not value.endswith(":"):
            return value, line
    return None, flag_line


def _hit(rule, engine, line, end_line, summary, confidence, block_line=None):
    return TextHit(line=line, end_line=end_line, block_line=block_line, anchor=f"{engine}:{rule}",
                   summary=summary, confidence=confidence, recommendation=RECOMMENDATIONS[rule])


def _summary(rule, engine, setting):
    name = ENGINES[engine]
    if rule == "prefix-caching-disabled":
        return (f"{name} server launched with {setting}: automatic prefix caching is off, so the KV cache of "
                f"shared prompt prefixes (system prompts, chat history) is recomputed on every request.")
    if rule == "fp32-dtype":
        return (f"{name} server launched with {setting}: model weights and KV cache are served in 32-bit floats, "
                f"twice the memory of bfloat16/float16 and much slower matrix math on tensor-core GPUs.")
    return (f"{name} server launched with {setting}: CUDA graphs are disabled (eager mode), so every decode step "
            f"pays the kernel-launch overhead that graph capture removes; this trades steady-state decode "
            f"performance for faster startup.")


def _text_hits(ctx):
    hits = []
    tokens = _tokens(ctx)
    for index, (token, line, column) in enumerate(tokens):
        if not token.startswith("--") or len(token) < 3:
            continue
        engine = _engine_at(ctx, line, column)
        if engine not in ENGINES:
            continue
        raw, _, inline = token[2:].partition("=")
        name = raw.lower().replace("_", "-")
        inline = inline or None
        if engine == "vllm" and name == "no-enable-prefix-caching":
            hits.append(_hit("prefix-caching-disabled", engine, line, line,
                             _summary("prefix-caching-disabled", engine, "--no-enable-prefix-caching"), "high"))
        elif engine == "vllm" and name == "enforce-eager" and (inline is None or inline.lower() in TRUE_VALUES):
            hits.append(_hit("eager-mode", engine, line, line, _summary("eager-mode", engine, "--enforce-eager"),
                             "high"))
        elif engine == "vllm" and name == "dtype":
            value, end = _flag_value(tokens, index, inline)
            if value is not None and value.lower() in FP32_DTYPES:
                hits.append(_hit("fp32-dtype", engine, line, end, _summary("fp32-dtype", engine, f"--dtype {value}"),
                                 "medium"))
        elif engine == "tgi" and name == "cuda-graphs":
            value, end = _flag_value(tokens, index, inline)
            if value == "0":
                hits.append(_hit("eager-mode", engine, line, end, _summary("eager-mode", "tgi", "--cuda-graphs 0"),
                                 "high"))
    for number, line in enumerate(ctx.code, 1):
        env = _TGI_CUDA_GRAPHS_ENV.search(line)
        if env and _engine_at(ctx, number, env.start()) == "tgi":
            hits.append(_hit("eager-mode", "tgi", number, number, _summary("eager-mode", "tgi", "CUDA_GRAPHS=0"),
                             "high"))
        elif (_ENV_NAME.match(line) and number < len(ctx.code) and _ENV_VALUE_ZERO.match(ctx.code[number])
              and _engine_at(ctx, number) == "tgi"):
            hits.append(_hit("eager-mode", "tgi", number, number + 1,
                             _summary("eager-mode", "tgi", "CUDA_GRAPHS=0"), "high"))
    # One finding per (launcher, rule, line): `CUDA_GRAPHS=0 text-generation-launcher --cuda-graphs 0` is one
    # setting stated twice, not two findings.
    unique = {}
    for hit in hits:
        unique.setdefault((hit.anchor, hit.line), hit)
    return list(unique.values())


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _vllm_names(tree):
    """Local name -> fully qualified name for everything imported from vllm."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "vllm" or alias.name.startswith("vllm."):
                    names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else "vllm"
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "vllm" or node.module.startswith("vllm."):
                for alias in node.names:
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _python_hits(ctx):
    names = _vllm_names(ctx.tree)
    hits = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted is None:
            continue
        head, _, rest = dotted.partition(".")
        qualified = names.get(head)
        if qualified is None:
            continue
        full = f"{qualified}.{rest}" if rest else qualified
        if full.rsplit(".", 1)[-1] not in VLLM_CONSTRUCTORS or not full.startswith("vllm"):
            continue
        constructor = full.rsplit(".", 1)[-1]
        for keyword in node.keywords:
            value = keyword.value
            rule = setting = None
            if keyword.arg == "enable_prefix_caching" and isinstance(value, ast.Constant) and value.value is False:
                rule, setting, confidence = "prefix-caching-disabled", "enable_prefix_caching=False", "high"
            elif keyword.arg == "enforce_eager" and isinstance(value, ast.Constant) and value.value is True:
                rule, setting, confidence = "eager-mode", "enforce_eager=True", "high"
            elif keyword.arg == "dtype":
                text = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
                attribute = _dotted(value) or ""
                if (text or "").lower() in FP32_DTYPES:
                    rule, setting, confidence = "fp32-dtype", f"dtype={text!r}", "medium"
                elif attribute.startswith("torch.") and attribute.split(".", 1)[1] in FP32_DTYPES:
                    rule, setting, confidence = "fp32-dtype", f"dtype={attribute}", "medium"
            if rule is None:
                continue
            summary = _summary(rule, "vllm", f"{constructor}({setting})").replace("server launched", "engine built")
            hits.append(_hit(rule, "vllm", keyword.lineno, keyword.end_lineno or keyword.lineno, summary,
                             confidence, block_line=node.lineno))
    return hits


def run(ctx):
    hits = _python_hits(ctx) if ctx.kind == "python" else _text_hits(ctx)
    return sorted(hits, key=lambda hit: (hit.line, hit.anchor))


# ---- artifact mode ------------------------------------------------------------------------------

REQUIRED_FIELDS = ("server_id", "engine", "window_seconds", "requests", "preemptions", "kv_cache_usage_max")
OPTIONAL_FIELDS = ("model", "prefix_cache_hit_rate", "max_model_len", "max_request_tokens")
FRACTIONS = ("kv_cache_usage_max", "prefix_cache_hit_rate")
COUNTS = ("requests", "preemptions")


# Largest magnitude accepted for any number. JSON integers are unbounded in Python, and a 400-digit one overflows
# float conversion (math.isfinite, division); bounding before any float math keeps a hostile or broken upload a
# per-server `unavailable` (or an ArtifactRejected for `settings`), never an exception that fails the whole upload.
MAX_MAGNITUDE = 10**15
_MAX_TEXT = "1e15"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and -MAX_MAGNITUDE <= value <= MAX_MAGNITUDE


def _is_number(value):
    if _is_int(value):
        return True
    return isinstance(value, float) and math.isfinite(value) and -MAX_MAGNITUDE <= value <= MAX_MAGNITUDE


def _fmt(value):
    return str(value) if isinstance(value, int) else f"{value:g}"


def artifact_problems(data):
    """Reasons a normalized inference-metrics object is unusable (empty list when valid)."""
    if not isinstance(data, dict):
        return ["artifact data must be an object"]
    problems = []
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    unknown = sorted(set(data) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        problems.append("unknown fields: " + ", ".join(unknown))
    for name in ("server_id", "engine", "model"):
        if name in data and (not isinstance(data[name], str) or not data[name].strip() or len(data[name]) > 200):
            problems.append(f"{name} must be a nonempty string of at most 200 characters")
    if "window_seconds" in data and (not _is_number(data["window_seconds"]) or data["window_seconds"] <= 0):
        problems.append(f"window_seconds must be a positive number of at most {_MAX_TEXT}")
    for name in COUNTS:
        if name in data and (not _is_int(data[name]) or data[name] < 0):
            problems.append(f"{name} must be a nonnegative integer of at most {_MAX_TEXT}")
    for name in FRACTIONS:
        if name in data and data[name] is not None and (not _is_number(data[name]) or not 0 <= data[name] <= 1):
            problems.append(f"{name} must be a fraction between 0 and 1")
    if data.get("kv_cache_usage_max", 0) is None:
        problems.append("kv_cache_usage_max must be a fraction between 0 and 1")
    lengths = [name for name in ("max_model_len", "max_request_tokens") if data.get(name) is not None]
    for name in lengths:
        if not _is_int(data[name]) or data[name] < 1:
            problems.append(f"{name} must be a positive integer of at most {_MAX_TEXT}")
    if len(lengths) == 1:
        problems.append("max_model_len and max_request_tokens must be supplied together")
    elif (len(lengths) == 2 and _is_int(data["max_model_len"]) and _is_int(data["max_request_tokens"])
          and data["max_request_tokens"] > data["max_model_len"]):
        problems.append("max_request_tokens cannot exceed max_model_len")
    return problems


def read_artifact_settings(context):
    if not isinstance(context, dict):
        return None, "context must be an object"
    missing = [key for key in ARTIFACT_SETTING_KEYS if key not in context]
    if missing:
        return None, "missing required context settings: " + ", ".join(missing)
    settings = {key: context[key] for key in ARTIFACT_SETTING_KEYS}
    if not _is_int(settings["min_requests"]) or settings["min_requests"] < 1:
        return None, f"context.min_requests must be a positive integer of at most {_MAX_TEXT}"
    for key in ("max_preemption_ratio", "max_kv_cache_usage", "min_prefix_cache_hit_rate"):
        if not _is_number(settings[key]) or not 0 <= settings[key] <= 1:
            return None, f"context.{key} must be a fraction between 0 and 1"
    if not _is_number(settings["max_context_headroom_ratio"]) or settings["max_context_headroom_ratio"] < 1:
        return None, f"context.max_context_headroom_ratio must be a number of at least 1 and at most {_MAX_TEXT}"
    return settings, None


def _evidence(source, *names):
    return [{"source_id": source["source_id"], "kind": ARTIFACT_KIND, "locator": source["locator"],
             "field": name, "value": source["data"][name]} for name in names]


def artifact_items(source, settings):
    """Finding bodies (identity, summary, confidence, recommendation, evidence) for one valid artifact."""
    data = source["data"]
    label = f"{data['engine']} server {data['server_id']}"
    window = f"over {_fmt(data['window_seconds'])}s"
    items = []
    requests, preemptions = data["requests"], data["preemptions"]
    ratio = preemptions / requests
    if ratio > settings["max_preemption_ratio"]:
        items.append(("preemptions", "high", ["preemptions", "requests"],
                      f"{label} preempted requests {preemptions} times for {requests} completed requests {window} "
                      f"({ratio:.2%}, above {settings['max_preemption_ratio']:.2%}): the KV cache ran out of space and "
                      f"preempted requests were recomputed."))
    usage = data["kv_cache_usage_max"]
    if usage > settings["max_kv_cache_usage"]:
        items.append(("kv-cache-saturation", "medium", ["kv_cache_usage_max"],
                      f"{label} reached {usage:.0%} KV-cache usage {window} "
                      f"(above {settings['max_kv_cache_usage']:.0%}): "
                      f"KV-cache growth limits batching and leads to queueing or preemption."))
    hit_rate = data.get("prefix_cache_hit_rate")
    if hit_rate is not None and hit_rate < settings["min_prefix_cache_hit_rate"]:
        reuse = (f"served no prompt tokens from a prefix cache {window} (prefix caching is disabled or no prefix "
                 f"was reused)" if hit_rate == 0 else
                 f"reused only {hit_rate:.1%} of prompt tokens from its prefix cache {window}")
        items.append(("low-prefix-cache-hit-rate", "low", ["prefix_cache_hit_rate"],
                      f"{label} {reuse} (below {settings['min_prefix_cache_hit_rate']:.1%}): almost every prefill "
                      f"is computed from scratch."))
    if data.get("max_model_len") is not None:
        limit, largest = data["max_model_len"], data["max_request_tokens"]
        if limit > settings["max_context_headroom_ratio"] * largest:
            items.append(("oversized-context", "medium", ["max_model_len", "max_request_tokens"],
                          f"{label} allows {limit:,}-token sequences but its largest request {window} used "
                          f"{largest:,} tokens ({limit / largest:.1f}x, above "
                          f"{_fmt(settings['max_context_headroom_ratio'])}x): the context budget is far above "
                          f"what traffic needs."))
    return [{"identity": identity, "confidence": confidence, "summary": summary,
             "recommendation": RECOMMENDATIONS[identity], "evidence": _evidence(source, *fields)}
            for identity, confidence, fields, summary in items]


def _evaluate_artifact(scope_id, source, context):
    settings, reason = read_artifact_settings(context)
    if settings is None:
        return None, f"{scope_id}: missing or invalid context settings for artifact mode: {reason}"
    data = source.get("data")
    problems = artifact_problems(data)
    if not problems and scope_id != f"{SCOPE_PREFIX}{data['server_id']}":
        expected = f"{SCOPE_PREFIX}{data['server_id']}"
        problems.append(f"scope id {scope_id!r} does not match server_id (expected '{expected}')")
    if not isinstance(source.get("locator"), str) or not source.get("locator"):
        problems.append("artifact source needs a locator")
    if problems:
        return None, f"{scope_id}: " + "; ".join(problems)
    if data["requests"] < settings["min_requests"]:
        return None, (f"{scope_id}: {data['requests']} completed requests is below min_requests "
                      f"{settings['min_requests']}; too few to judge, not evaluated")
    try:
        return artifact_items(source, settings), None
    except (ArithmeticError, ValueError) as error:  # bounded above; a slip must stay per-server, not fail the upload
        return None, f"{scope_id}: metrics could not be evaluated ({type(error).__name__}); not evaluated"


# ---- dispatch -----------------------------------------------------------------------------------

def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    _require(isinstance(payload, dict), "input payload must be an object")
    _require(payload.get("kind") == "input", "expected a contract input payload")
    _require(payload.get("check_id") == CHECK_ID, f"detector only evaluates {CHECK_ID}")
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version; contract v1 is required")
    _require(payload.get("detector_version") == DETECTOR_VERSION,
             f"unsupported detector_version {payload.get('detector_version')!r}; "
             f"this detector implements {DETECTOR_VERSION}")
    for name in IDENTITY_FIELDS:
        _require(name in payload, f"input is missing required field {name}")
    scope, sources = payload["scope"], payload.get("sources")
    _require(isinstance(scope, list) and bool(scope), "scope must be a nonempty list")
    _require(isinstance(sources, list), "sources must be a list")
    _require(isinstance(payload["context"], dict), "context must be an object")
    sources = [source for source in sources if isinstance(source, dict)]
    module = sys.modules[__name__]

    evaluated, findings, limitations = [], [], []
    used_static = used_artifact = False
    for scope_id in scope:
        scoped = [source for source in sources if source.get("scope_id") == scope_id]
        statics = [source for source in scoped if source.get("kind") == STATIC_KIND]
        artifacts = [source for source in scoped if source.get("kind") == ARTIFACT_KIND]
        if statics and artifacts:
            items, omitted = None, (f"{scope_id}: static and artifact sources supplied together; static configs use "
                                    f"file:<path> scopes and inference metrics use {SCOPE_PREFIX}<server_id> scopes")
        elif statics:
            items, omitted = textstatic._evaluate_file(scope_id, scoped, module)
            used_static = True
        elif len(artifacts) == 1:
            items, omitted = _evaluate_artifact(scope_id, artifacts[0], payload["context"])
            used_artifact = True
        elif artifacts:
            items, omitted = None, (f"{scope_id}: multiple inference-metrics artifacts supplied; "
                                    f"evaluation requires exactly one")
        else:
            items, omitted = None, (f"{scope_id}: no static serving config or inference-metrics artifact supplied; "
                                    f"LLM-19 requires one")
        if items is None:
            limitations.append(omitted)
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
    if used_static:
        limitations.append(LIMITATION)
    if used_artifact:
        limitations.append(ARTIFACT_LIMITATION)
    else:
        limitations.append(RUNTIME_UNAVAILABLE)

    status = "unavailable" if not evaluated else "completed" if len(evaluated) == len(scope) else "partial"
    result = {name: payload[name] for name in IDENTITY_FIELDS}
    result.update(kind="result", status=status, coverage={"evaluated_scope": evaluated, "limitations": limitations},
                  findings=findings if evaluated else [], measurements=[])
    return result


# ---- artifact upload (llm-19.json) --------------------------------------------------------------

class ArtifactRejected(ValueError):
    """The uploaded llm-19.json cannot be used at all (the artifact parser refuses it)."""


def artifact_inputs(data, name, run):
    """(context, scope, sources, notes) for an uploaded llm-19.json; see README "LLM-19 > Artifact"."""
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list) or not data["servers"]:
        raise ArtifactRejected(f'{name} needs {{"servers": [...]}} with at least one server')
    unknown = sorted(set(data) - {"servers", "settings"})
    if unknown:
        raise ArtifactRejected(f"{name} has unknown top-level fields: {', '.join(unknown)}")
    overrides = data.get("settings") or {}
    if not isinstance(overrides, dict) or set(overrides) - set(ARTIFACT_SETTING_KEYS):
        raise ArtifactRejected(f"settings may only contain {', '.join(ARTIFACT_SETTING_KEYS)}")
    context = dict(REFERENCE_SETTINGS) | overrides
    settings, reason = read_artifact_settings(context)
    if settings is None:
        raise ArtifactRejected(f"invalid settings: {reason}")
    notes, scope, sources, seen = [], [], [], set()
    servers = data["servers"]
    if len(servers) > MAX_SERVERS:
        notes.append(f"only the first {MAX_SERVERS} of {len(servers)} servers in {name} were evaluated")
        servers = servers[:MAX_SERVERS]
    for index, server in enumerate(servers):
        server_id = server.get("server_id") if isinstance(server, dict) else None
        if not isinstance(server_id, str) or not server_id.strip() or len(server_id) > 200:
            notes.append(f"servers[{index}] has no usable server_id (nonempty string, at most 200 characters); skipped")
            continue
        if server_id in seen:
            notes.append(f"duplicate server_id {server_id!r} in servers[{index}]; only the first entry was evaluated")
            continue
        seen.add(server_id)
        scope_id = f"{SCOPE_PREFIX}{server_id}"
        sources.append({"source_id": f"artifact-{index}", "scope_id": scope_id, "kind": ARTIFACT_KIND,
                        "locator": f"{name} from GitHub Actions run {run}: {server_id}", "data": server})
        scope.append(scope_id)
    if not scope:
        raise ArtifactRejected("no usable servers in the artifact: " + "; ".join(notes[:5]))
    return context, scope, sources, notes
