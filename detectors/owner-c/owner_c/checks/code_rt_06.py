"""CODE-RT.6: outdated runtime / interpreter version (Node.js, Python, AWS Lambda runtimes).

Reads version declarations from common config files and compares them, as of a declared
`reference_date`, with a bundled dated support table (config/runtime_support.json).
"""
import datetime
import json
import os
import pathlib
import re

KEY = "CODE-RT.6"
LANGUAGE = "config"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
SETTINGS = {"reference_date": "date", "warn_days": (0, 3650)}  # warn_days: days before EOL to warn
REFS = [
    "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html",
    "https://nodejs.org/en/about/previous-releases",
    "https://devguide.python.org/versions/",
    "https://endoflife.date/",
]
RECOMMENDATION = ("Upgrade to a currently supported LTS runtime (for example Node.js 22/24 or Python 3.12+) "
                  "after testing; unsupported runtimes stop receiving security and performance fixes.")
LIMITATION = ("Static scan of version declarations against a bundled support table (see "
              "config/runtime_support.json for provenance and retrieval date). Versions set through variables, "
              "aliases (lts/*, latest) or files not scanned are not resolved, and an old runtime is not proof of "
              "measured inefficiency.")

_TABLE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "config" / "runtime_support.json").read_text())
_NAMES = {"node": "Node.js", "python": "Python"}
_BASENAMES = {".nvmrc", ".node-version", ".python-version", "runtime.txt", "pipfile", ".tool-versions", "package.json",
              "serverless.yml", "serverless.yaml", "template.yml", "template.yaml", "template.json"}


def accepts(path: str) -> bool:
    base = os.path.basename(path).lower()
    p = path.replace("\\", "/").lower()
    return (base in _BASENAMES or base.startswith("dockerfile") or base.endswith(".dockerfile")
            or base.endswith(".tf") or (".github/workflows/" in p and base.endswith((".yml", ".yaml"))))


# ---- support table ---------------------------------------------------------------
def _date(text):
    return datetime.date.fromisoformat(text) if text else None


def _status(end, reference, warn_days):
    if end is None:
        return None
    if end <= reference:
        return "ended", end
    if (end - reference).days <= warn_days:
        return "soon", end
    return None


def _upstream(runtime, cycle, reference, warn_days):
    row = _TABLE[runtime].get(cycle)
    return _status(_date(row["eol"]), reference, warn_days) if row else None


def _lambda(identifier, reference, warn_days):
    return _status(_date(_TABLE["lambda"].get(identifier)), reference, warn_days)


# ---- extraction: yield (lineno, kind, key, setting, exact) ------------------------
_VERSION = re.compile(r"(\d+)(?:\.(\d+))?")


def _node(value):
    m = re.match(r"^v?(\d+)", value.strip())
    return m.group(1) if m else None


def _py(value):
    m = re.match(r"^(\d+)\.(\d+)", value.strip())
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _docker(line):
    m = re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", line, re.I)
    if not m or ":" not in m.group(1):
        return None
    image, tag = m.group(1).rsplit(":", 1)
    base = image.split("/")[-1].lower()
    lam = image.lower().startswith("public.ecr.aws/lambda/")
    if base in ("node", "nodejs") and _node(tag):
        major = _node(tag)
        return ("lambda", f"nodejs{major}.x") if lam else ("node", major)
    if base == "python" and _py(tag):
        cycle = _py(tag)
        return ("lambda", f"python{cycle}") if lam else ("python", cycle)
    return None


def _extract(ctx):
    base = os.path.basename(ctx.path).lower()
    pending = None  # (kind, setting) for a YAML key whose list items follow on later lines
    in_engines = False
    for n, raw in enumerate(ctx.lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if base in (".nvmrc", ".node-version") and _node(line):
            yield n, "node", _node(line), "node-version-file", True
        elif base == ".python-version" and _py(line):
            yield n, "python", _py(line), "python-version-file", True
        elif base == "runtime.txt" and line.lower().startswith("python-") and _py(line[7:]):
            yield n, "python", _py(line[7:]), "runtime.txt", True
        elif base == "pipfile" and re.match(r'^python_(full_)?version\s*=\s*"([\d.]+)"', line):
            cycle = _py(re.match(r'^python_(?:full_)?version\s*=\s*"([\d.]+)"', line).group(1))
            if cycle:
                yield n, "python", cycle, "pipfile", True
        elif base == ".tool-versions":
            m = re.match(r"^(nodejs|node|python)\s+(\S+)", line)
            if m:
                key = _node(m.group(2)) if m.group(1) != "python" else _py(m.group(2))
                if key:
                    yield n, ("python" if m.group(1) == "python" else "node"), key, "tool-versions", True
        elif base == "package.json":
            in_engines = in_engines or line.startswith('"engines"')
            m = re.match(r'^"node"\s*:\s*"([^"]+)"', line)
            if in_engines and m:
                spec = m.group(1).strip()
                major = _node(re.sub(r"^[\s^~=><v]+", "", spec))
                if major:
                    exact = not re.search(r">|<|\|\|", spec)
                    yield n, "node", major, "engines.node", exact
        elif base.startswith("dockerfile") or base.endswith(".dockerfile"):
            found = _docker(line)
            if found:
                yield n, found[0], found[1], "from-image", True
        else:
            m = re.search(r"""\bruntime\s*[:=]\s*["']?(nodejs\d+\.x|python\d+\.\d+)["']?""", line, re.I)
            if m:
                yield n, "lambda", m.group(1).lower(), "lambda-runtime", True
                continue
            if ".github/workflows/" in ctx.path.replace("\\", "/").lower():
                m = re.match(r"^(?:-\s*)?(node|python)-version\s*:\s*(.*)$", line)
                if m:
                    kind, value = m.group(1), m.group(2).strip()
                    if value and "${{" not in value:
                        for token in re.findall(r"\d+(?:\.[\dx]+)*", value):
                            key = _node(token) if kind == "node" else _py(token)
                            if key:
                                yield n, kind, key, f"setup-{kind}", True
                        pending = None
                    else:
                        pending = (kind, f"setup-{kind}") if not value else None
                    continue
                if pending and line.startswith("- "):
                    token = re.search(r"\d+(?:\.[\dx]+)*", line)
                    if token:
                        key = _node(token.group(0)) if pending[0] == "node" else _py(token.group(0))
                        if key:
                            yield n, pending[0], key, pending[1], True
                    continue
                pending = None


def run(ctx, settings):
    reference, warn = settings["reference_date"], settings["warn_days"]
    out = []
    for lineno, kind, key, setting, exact in _extract(ctx):
        if kind == "lambda":
            state = _lambda(key, reference, warn)
            label = f"Lambda runtime {key}"
            verb = {"ended": "was deprecated by AWS on", "soon": "will be deprecated by AWS on"}
        else:
            state = _upstream(kind, key, reference, warn)
            label = f"{_NAMES[kind]} {key}"
            verb = {"ended": "reached end of life on", "soon": "reaches end of life on"}
        if state is None:
            continue
        phase, when = state
        confidence = ("high" if exact else "low") if phase == "ended" else "medium"
        if not exact:
            confidence = "low"
        suffix = " (lower bound of the declared range)" if not exact else ""
        out.append(ctx.hit(lineno, f"{kind}:{setting}",
                           f"{label} {verb[phase]} {when.isoformat()} (as of {reference.isoformat()}){suffix}.",
                           confidence))
    return out
