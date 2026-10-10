"""A parsed GitHub Actions workflow: triggers, jobs, steps, plus the helpers the checks share."""
from __future__ import annotations

import re
from dataclasses import dataclass

from owner_c.ci import yamlio

EVIDENCE_MAX_LINES = 8
EVIDENCE_MAX_CHARS = 2000
MAX_COMMAND_CHARS = 4000  # a longer "command" is a pathological continuation chain; some patterns are quadratic
WORKFLOW_PATH = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")


class NotAWorkflow(ValueError):
    """Valid YAML, but no `jobs` mapping: not a GitHub Actions workflow."""


@dataclass(frozen=True)
class Hit:
    """A static pattern match. `anchor` is the semantic identity (no line numbers)."""
    anchor: str
    start: int
    end: int
    summary: str
    confidence: str  # low | medium | high


@dataclass(frozen=True)
class Step:
    data: dict
    span: tuple

    @property
    def uses(self) -> str:
        value = self.data.get("uses")
        return value.strip() if isinstance(value, str) else ""

    @property
    def action(self) -> str:
        """`actions/cache/restore@v4` -> `actions/cache/restore`."""
        return self.uses.split("@")[0]

    @property
    def inputs(self) -> dict:
        value = self.data.get("with")
        return value if isinstance(value, dict) else {}

    @property
    def name(self) -> str:
        value = self.data.get("name")
        return value.strip() if isinstance(value, str) else ""

    def commands(self) -> list:
        """Shell command lines of a `run` step: continuations joined, blanks and comments dropped."""
        script = self.data.get("run")
        if not isinstance(script, str):
            return []
        out, pending = [], ""
        for raw in script.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith("\\") and len(pending) < MAX_COMMAND_CHARS:
                pending += line[:-1].rstrip() + " "
                continue
            out.append((pending + line).strip()[:MAX_COMMAND_CHARS])
            pending = ""
        if pending:
            out.append(pending.strip()[:MAX_COMMAND_CHARS])
        return out


@dataclass(frozen=True)
class Job:
    id: str
    data: dict
    span: tuple

    def steps(self) -> list:
        steps = self.data.get("steps")
        if not isinstance(steps, list):
            return []
        spans = getattr(steps, "spans", [])
        return [Step(s, spans[i]) for i, s in enumerate(steps) if isinstance(s, dict)]

    @property
    def is_reusable_call(self) -> bool:
        return isinstance(self.data.get("uses"), str)

    def runs_on_labels(self) -> list:
        value = self.data.get("runs-on")
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str)]
        if isinstance(value, dict):
            labels = value.get("labels")
            if isinstance(labels, str):
                return [labels]
            if isinstance(labels, list):
                return [v for v in labels if isinstance(v, str)]
        return []

    @property
    def self_hosted(self) -> bool:
        return any(label.strip().lower() == "self-hosted" for label in self.runs_on_labels())

    @property
    def opaque(self) -> bool:
        """Calls a local action or reusable workflow: what it does is not visible in this file."""
        return self.is_reusable_call or any(s.uses.startswith("./") for s in self.steps())


class Workflow:
    def __init__(self, path: str, source: str):
        self.path = path
        self.lines = source.splitlines()
        self.root = yamlio.load(source)
        if not isinstance(self.root.get("jobs"), dict):
            raise NotAWorkflow("no jobs mapping")
        self.on_key = True if True in self.root else "on"  # YAML 1.1 reads a bare `on` as boolean True
        self.name = self.root.get("name") if isinstance(self.root.get("name"), str) else ""

    # -- triggers ---------------------------------------------------------------------------------
    def triggers(self) -> dict:
        """Event name -> its config (None when bare). `on` may be a string, a list or a mapping."""
        value = self.root.get(self.on_key)
        if isinstance(value, str):
            return {value: None}
        if isinstance(value, list):
            return {v: None for v in value if isinstance(v, str)}
        if isinstance(value, dict):
            return dict(value)
        return {}

    def trigger_span(self, event: str) -> tuple:
        """Span of the `event:` entry inside a mapping `on`, else the span of the `on` entry itself."""
        value = self.root.get(self.on_key)
        if isinstance(value, dict) and event in value.spans:
            return value.spans[event]
        return self.root.spans.get(self.on_key, (1, 1))

    # -- jobs -------------------------------------------------------------------------------------
    def jobs(self) -> list:
        jobs = self.root["jobs"]
        return [Job(str(jid), job, jobs.spans[jid]) for jid, job in jobs.items() if isinstance(job, dict)]

    def deploy_like(self) -> bool:
        """Deployment/release workflows deliberately differ from CI (clean builds, no cancellation)."""
        stem = self.path.rsplit("/", 1)[-1]
        if re.search(r"deploy|release|publish", f"{self.name} {stem}", re.I):
            return True
        return any(job.data.get("environment") for job in self.jobs())

    def tag_only(self) -> bool:
        """Triggered only by tag pushes (releases): no pull_request and push has tags but no branches."""
        triggers = self.triggers()
        push = triggers.get("push")
        return ("pull_request" not in triggers and isinstance(push, dict)
                and "tags" in push and "branches" not in push)

    # -- evidence ---------------------------------------------------------------------------------
    def quote(self, start: int, end: int) -> tuple:
        """(line_start, exact complete source lines) capped to a few lines."""
        stop = min(end, start + EVIDENCE_MAX_LINES - 1, len(self.lines))
        quoted, size = [], 0
        for line in self.lines[start - 1:stop]:
            if quoted and size + len(line) > EVIDENCE_MAX_CHARS:  # always keep the first full line
                break
            quoted.append(line)
            size += len(line)
        return start, "\n".join(quoted)


def is_expression(value) -> bool:
    return isinstance(value, str) and "${{" in value


def truthy(value) -> bool:
    """YAML `true`, or a string that is `true` or an expression (cannot be resolved statically)."""
    if isinstance(value, str):
        return value.strip().lower() == "true" or is_expression(value)
    return bool(value)
