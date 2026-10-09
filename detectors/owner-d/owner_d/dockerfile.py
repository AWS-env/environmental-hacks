"""Minimal, dependency-free Dockerfile reader for static checks.

Reads text only: nothing is built, pulled or executed. It understands parser directives
(`# escape=`), line continuations, comment lines inside continuations, heredocs
(`RUN <<EOF`), `FROM ... AS name` stages, global `ARG` defaults and the shell commands of
`RUN` instructions (split on `&&`, `||`, `;`, `|`). Anything it cannot read raises
`textstatic.ParseError` so the file is reported as not evaluated rather than clean.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field

from .textstatic import ParseError, Unsupported

INSTRUCTIONS = {
    "FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV", "ADD", "COPY", "ENTRYPOINT",
    "VOLUME", "USER", "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL", "HEALTHCHECK", "SHELL",
}
_DIRECTIVE = re.compile(r"^#\s*([A-Za-z]+)\s*=\s*(\S+)\s*$")
_HEREDOC = re.compile(r"<<(-?)([\"']?)([A-Za-z_][A-Za-z0-9_]*)\2")
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
_PREFIXES = {"sudo", "command", "exec", "time", "nice", "env", "then", "do", "else", "{", "!", "nohup"}


def is_dockerfile(locator):
    name = os.path.basename(locator)
    lower = name.lower()
    return (
        lower in ("dockerfile", "containerfile")
        or lower.startswith(("dockerfile.", "containerfile."))
        or lower.endswith((".dockerfile", ".containerfile"))
    )


@dataclass
class Instruction:
    keyword: str
    args: str          # arguments with continuations joined (heredoc bodies appended)
    lines: list        # physical line numbers (1-based) that make up the instruction

    @property
    def line(self):
        return self.lines[0]


@dataclass
class Stage:
    index: int
    image: str         # image reference as written (variables resolved where possible)
    name: str | None
    instruction: Instruction
    body: list = field(default_factory=list)
    parent: "Stage | None" = None

    @property
    def label(self):
        return self.name or f"stage-{self.index}"


@dataclass
class Run:
    instruction: Instruction
    flags: list        # --mount=..., --network=... etc.
    text: str          # shell text
    commands: list     # list of argv lists


class Dockerfile:
    def __init__(self, locator, content):
        self.locator = locator
        self.lines = content.splitlines()
        self.escape = "\\"
        self.instructions = self._read_instructions()
        self.global_args, self.stages = self._read_stages()

    # -- reading --------------------------------------------------------------------------

    def _read_directives(self):
        index = 0
        while index < len(self.lines):
            match = _DIRECTIVE.match(self.lines[index].strip())
            if not match:
                break
            if match.group(1).lower() == "escape":
                if match.group(2) not in ("\\", "`"):
                    raise ParseError("invalid escape directive")
                self.escape = match.group(2)
            index += 1
        return index

    def _read_instructions(self):
        index = self._read_directives()
        instructions = []
        lines = self.lines
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith("#"):
                index += 1
                continue
            numbers, parts = [], []
            while True:
                text = lines[index].rstrip()
                numbers.append(index + 1)
                index += 1
                if text.endswith(self.escape):
                    parts.append(text[:-1])
                    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
                        index += 1
                    if index < len(lines):
                        continue
                else:
                    parts.append(text)
                break
            logical = " ".join(part.strip() for part in parts).strip()
            keyword, args = (logical.split(None, 1) + [""])[:2]
            keyword = keyword.upper()
            if keyword not in INSTRUCTIONS:
                raise ParseError(f"unknown instruction on line {numbers[0]}")
            for match in _HEREDOC.finditer(args) if keyword in ("RUN", "COPY", "ADD") else ():
                strip_tabs, delimiter = match.group(1) == "-", match.group(3)
                body = []
                while True:
                    if index >= len(lines):
                        raise ParseError(f"unterminated heredoc starting on line {numbers[0]}")
                    raw = lines[index]
                    numbers.append(index + 1)
                    index += 1
                    if (raw.lstrip("\t") if strip_tabs else raw).rstrip() == delimiter:
                        break
                    body.append(raw)
                args = args + "\n" + "\n".join(body)
            instructions.append(Instruction(keyword, args.strip(), numbers))
        return instructions

    def _read_stages(self):
        global_args, stages = {}, []
        for instruction in self.instructions:
            if instruction.keyword == "FROM":
                stages.append(self._stage(instruction, len(stages), global_args, stages))
            elif not stages:
                if instruction.keyword != "ARG":
                    raise ParseError(f"{instruction.keyword} before the first FROM")
                name, _, default = instruction.args.partition("=")
                global_args[name.strip()] = default.strip().strip("\"'") if default else None
            else:
                stages[-1].body.append(instruction)
        if not stages:
            raise ParseError("no FROM instruction")
        return global_args, stages

    def _stage(self, instruction, index, global_args, stages):
        tokens = [t for t in instruction.args.split() if not t.startswith("--")]
        if not tokens:
            raise ParseError(f"FROM without an image on line {instruction.line}")
        image = self.substitute(tokens[0], global_args)
        name = tokens[2] if len(tokens) >= 3 and tokens[1].lower() == "as" else None
        stage = Stage(index, image, name.lower() if name else None, instruction)
        stage.parent = next((s for s in reversed(stages) if s.name and s.name == image.lower()), None)
        return stage

    @staticmethod
    def substitute(text, values):
        def replace(match):
            value = values.get(match.group(1) or match.group(2))
            return value if value else match.group(0)
        return _VAR.sub(replace, text)

    # -- queries --------------------------------------------------------------------------

    @property
    def final(self):
        return self.stages[-1]

    def shipped_chain(self):
        """The final stage and the stages it is built FROM (their layers ship in the image)."""
        chain, stage = [], self.final
        while stage is not None and stage not in chain:
            chain.append(stage)
            stage = stage.parent
        return list(reversed(chain))

    def lines_with(self, instruction, *words):
        """Physical lines of the instruction containing all words (else the first word)."""
        def has(text, word):
            return re.search(rf"(?<![\w.-]){re.escape(word)}(?![\w-])", text) is not None
        for candidates in (words, words[:1]):
            found = [n for n in instruction.lines if all(has(self.lines[n - 1], w) for w in candidates)]
            if found:
                return found
        return []


def parse(locator, content):
    if not is_dockerfile(locator):
        raise Unsupported(locator)
    return Dockerfile(locator, content)


# -- RUN shell commands -------------------------------------------------------------------


def _split_flags(args):
    flags = []
    rest = args
    while rest.startswith("--"):
        flag, _, rest = rest.partition(" ")
        flags.append(flag)
        rest = rest.lstrip()
    return flags, rest


def _tokens(text):
    text = re.sub(r"\\\n", " ", text).replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to whitespace tokens
        return text.split()


def commands(text):
    """Split shell text into simple commands (argv lists), dropping env/sudo prefixes."""
    result, current = [], []
    for token in _tokens(text) + [";"]:
        if token in _SEPARATORS:
            while current and (current[0] in _PREFIXES or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", current[0])):
                current.pop(0)
            if current:
                result.append(current)
            current = []
        else:
            current.append(token)
    return result


def read_run(instruction):
    flags, rest = _split_flags(instruction.args)
    if rest.startswith("["):
        try:
            argv = json.loads(rest.split("\n", 1)[0])
            if isinstance(argv, list) and all(isinstance(a, str) for a in argv):
                if len(argv) >= 3 and argv[1] == "-c":
                    rest = argv[2]
                else:
                    return Run(instruction, flags, " ".join(argv), [argv] if argv else [])
        except json.JSONDecodeError:
            pass  # Docker treats invalid JSON as shell form
    return Run(instruction, flags, rest, commands(rest))


def mount_targets(run):
    """Targets of `--mount=type=cache|tmpfs` flags: their contents never reach the layer."""
    targets = []
    for flag in run.flags:
        if not flag.startswith("--mount="):
            continue
        options = dict(
            part.split("=", 1) if "=" in part else (part, "")
            for part in flag[len("--mount="):].split(",")
        )
        if options.get("type") in ("cache", "tmpfs"):
            targets.append(options.get("target") or options.get("dst") or options.get("destination") or "")
    return targets
