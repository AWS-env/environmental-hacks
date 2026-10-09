"""INF-09: oversized container images / unneeded packages (static Dockerfile scan).

Detector semantics version 1.0.0. Reads Dockerfiles/Containerfiles as text and flags
instructions that put avoidable bytes into the *shipped* image: the final stage and the
stages it is built FROM. Builder stages that are only used via `COPY --from` are not
shipped, so they are never flagged. Nothing is built, pulled or executed, so the actual
image size is unknown and no measurements are emitted.
"""

from __future__ import annotations

import os
import re
import shlex
import sys

from . import dockerfile, textstatic
from .textstatic import EvaluationError, TextHit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "INF-09"
DETECTOR_VERSION = "1.0.0"
NOQA = ("INF-09", "INF09")
FORMATS = "Dockerfiles (Dockerfile, Dockerfile.*, *.Dockerfile, Containerfile, Containerfile.*, *.Containerfile)"

REFERENCES = (
    "https://docs.docker.com/build/building/best-practices/",
    "https://docs.docker.com/build/building/multi-stage/",
    "https://github.com/hadolint/hadolint/wiki/DL3009",
    "https://github.com/hadolint/hadolint/wiki/DL3015",
    "https://github.com/hadolint/hadolint/wiki/DL3019",
    "https://github.com/hadolint/hadolint/wiki/DL3042",
    "https://docs.npmjs.com/cli/v10/commands/npm-ci",
    "https://hub.docker.com/_/python",
)
RECOMMENDATION = (
    "Keep build tools, package-manager caches and development dependencies out of the final image: use a "
    "slim/alpine/distroless base and a multi-stage build that copies only the runtime artifacts."
)
LIMITATION = (
    "Static Dockerfile scan only: INF-09 never builds or pulls images, so it proves avoidable layer content, not "
    "the image size or how often it is pulled. The last stage is assumed to be the build target (--target is "
    "unknown); development/test/debug images are not evaluated. Base-image contents, pip/npm config files and "
    ".dockerignore are not visible per file, so `COPY . .` without a .dockerignore is not checked."
)

# Official images whose default tags have a much smaller -slim/-alpine variant.
FULL_VARIANT_IMAGES = {"python", "node", "ruby", "perl"}
# Official images that are build toolchains in every variant; flagged only when the image also
# builds the application (a build command below), not when the toolchain is the image's purpose.
TOOLCHAIN_IMAGES = {"golang": "the Go toolchain", "rust": "the Rust toolchain", "maven": "Maven and a JDK",
                    "gradle": "Gradle and a JDK"}
BUILD_COMMAND = re.compile(
    r"\bgo\s+build\b|\bcargo\s+build\b|\bmvnw?\b[^;&|]*\b(package|install|verify)\b|"
    r"\bgradlew?\b[^;&|]*\b(build|assemble|bootJar|installDist|shadowJar)\b"
)
PIP_CONFIG_NO_CACHE = re.compile(r"\bpip[0-9.]* config set [^;&|]*no-cache-dir")
# Final-stage names and file/directory names that mark development, test or debug images.
DEV_NAMES = {"dev", "devel", "develop", "development", "dev-envs", "devenv", "debug", "test", "tests", "testing",
             "local", "devcontainer", "e2e"}
FULL_TAG = re.compile(
    r"^(latest|lts|current|[0-9][0-9.]*(-?rc[0-9]*)?|[a-z]+(-[a-z]+)?)?"
    r"(-(bookworm|bullseye|buster|stretch|trixie|jammy|focal|noble))?$"
)
SMALL_TAG = re.compile(r"slim|alpine|distroless|windowsservercore|nanoserver|scm|curl|minimal")
TOOLCHAIN_PACKAGES = {
    "build-essential", "gcc", "g++", "clang", "make", "cmake", "build-base", "gcc-c++", "autoconf", "automake",
    "libtool", "musl-dev",
}
REMOVE_SUBCOMMANDS = {"purge", "remove", "autoremove", "del", "erase"}
APT = {"apt-get", "apt"}
YUM = {"yum": ("DL3032", "/var/cache/yum"), "dnf": ("DL3040", "/var/cache/dnf"),
       "microdnf": ("DL3041", "/var/cache/yum")}
OPTS_WITH_VALUE = {"-o", "-c", "-t", "--target-release", "--option", "-t", "--virtual", "--repository", "-X",
                   "--setopt", "--enablerepo", "--disablerepo", "--releasever", "--installroot"}

REC_SLIM = "Use the -slim (or -alpine/distroless) variant of the base image, or build in a full image and copy the artifacts into a slim final stage."
REC_TOOLCHAIN_BASE = "Compile in a builder stage and copy only the binary/artifact into a minimal runtime image (distroless, -slim JRE, scratch)."
REC_RECOMMENDS = "Add --no-install-recommends to apt-get install so only the required packages are installed."
REC_APT_LISTS = "Remove the package index in the same RUN: `apt-get update && apt-get install ... && rm -rf /var/lib/apt/lists/*`."
REC_APK = "Use `apk add --no-cache ...` so the package index is not stored in the layer."
REC_YUM = "Run `{tool} clean all` (or remove {cache}) in the same RUN as the install."
REC_PIP = "Use `pip install --no-cache-dir ...` (or set PIP_NO_CACHE_DIR=1) so the wheel cache is not stored in the layer."
REC_NODE = "Install only production dependencies in the final image (`npm ci --omit=dev`, `yarn install --production`, `pnpm install --prod`), building in a separate stage if devDependencies are needed for the build."
REC_BUILD_TOOLS = "Compile in a builder stage and copy the results into the final stage, or remove the build packages in the same RUN (`apk add --virtual .build-deps ... && apk del .build-deps`)."


def _dev_image(locator, ctx):
    final = ctx.final.name
    if final and (final in DEV_NAMES or final.split("-")[0] in DEV_NAMES):
        return f"final stage {final!r} is a development/test target; the production --target is unknown"
    parts = [p.lower() for p in re.split(r"[\\/]", locator)]
    tokens = set(re.split(r"[._-]", parts[-1])) | {p.lstrip(".") for p in parts[:-1]}
    marked = sorted(tokens & DEV_NAMES)
    if marked:
        return f"path marks a development/test image ({marked[0]})"
    return None


def parse(locator, content):
    ctx = dockerfile.parse(locator, content)
    reason = _dev_image(locator, ctx)
    if reason:
        raise textstatic.NotEvaluated(reason + "; INF-09 v1 evaluates shipped images only")
    return ctx


def _image_parts(image):
    """('python', '3.12-slim') for python:3.12-slim / docker.io/library/python:3.12-slim."""
    ref = image.split("@", 1)[0]
    name, tag = ref, ""
    if ":" in ref.rsplit("/", 1)[-1]:
        name, tag = ref.rsplit(":", 1)
    for prefix in ("docker.io/library/", "index.docker.io/library/", "library/", "public.ecr.aws/docker/library/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.lower(), tag.lower()


def _subcommand(argv):
    """(subcommand, operands, options) for package-manager style argv."""
    sub, operands, options, skip = None, [], [], False
    for token in argv[1:]:
        if skip:
            skip = False
            options.append(token)
            continue
        if token.startswith((">", "<")) or token in ("2", "1"):
            break
        if token.startswith("-"):
            options.append(token)
            skip = token in OPTS_WITH_VALUE
            continue
        if sub is None:
            sub = token
        else:
            operands.append(token)
    return sub, operands, options


def _tool(argv):
    """Normalise the program name: `pip`, `npm`, ..., with `python -m pip` mapped to `pip`."""
    name = os.path.basename(argv[0])
    if re.fullmatch(r"python[0-9.]*", name) and len(argv) > 2 and argv[1] == "-m":
        return re.sub(r"[0-9.]+$", "", argv[2]), [argv[2]] + argv[3:]
    return re.sub(r"(?<=pip)[0-9.]+$", "", name), argv


def _earlier(stages, before):
    """Instructions of the shipped chain that run before instruction `before`."""
    for stage in stages:
        for instruction in stage.body:
            if instruction is before:
                return
            yield instruction


def _env_names(stages, before):
    """Names set by ENV/ARG in the shipped chain before instruction `before`."""
    names = {}
    for instruction in _earlier(stages, before):
        if instruction.keyword in ("ENV", "ARG"):
            try:
                tokens = shlex.split(instruction.args)
            except ValueError:
                tokens = instruction.args.split()
            if tokens and "=" not in tokens[0] and instruction.keyword == "ENV":
                names[tokens[0]] = " ".join(tokens[1:])
                continue
            for token in tokens:
                key, _, value = token.partition("=")
                names[key] = value
    return names


def _mounted(run, *paths):
    return any(path in target for target in dockerfile.mount_targets(run) for path in paths)


class _Collector:
    def __init__(self, ctx):
        self.ctx = ctx
        self.hits = []
        self._seen = {}

    def add(self, stage, instruction, rule, summary, confidence, recommendation, codes=(), words=()):
        line = instruction.line
        if words:
            key = (id(instruction), words)
            lines = self.ctx.lines_with(instruction, *words)
            if lines:
                line = lines[min(self._seen.get(key, 0), len(lines) - 1)]
            self._seen[key] = self._seen.get(key, 0) + 1
        self.hits.append(TextHit(
            line=line,
            anchor=f"{stage.label}:{rule}",
            summary=summary,
            confidence=confidence,
            block_line=instruction.line,
            codes=tuple(codes),
            recommendation=recommendation,
        ))


def _check_base_image(ctx, chain, out):
    root = chain[0]
    name, tag = _image_parts(root.image)
    if "$" in root.image:
        return
    final = " (the final image)" if len(chain) == 1 else f" (inherited by final stage {ctx.final.label!r})"
    builds = any(i.keyword == "RUN" and BUILD_COMMAND.search(i.args) for s in chain for i in s.body)
    if name in TOOLCHAIN_IMAGES and builds:
        out.add(root, root.instruction, "toolchain-base-image",
                f"Base image {root.image}{final} builds the application and ships {TOOLCHAIN_IMAGES[name]}, "
                f"which is only needed to build.",
                "medium", REC_TOOLCHAIN_BASE)
    elif name in FULL_VARIANT_IMAGES and not SMALL_TAG.search(tag) and FULL_TAG.match(tag):
        out.add(root, root.instruction, "full-base-image",
                f"Base image {root.image}{final} is the full Debian variant; {name} publishes -slim variants "
                f"that omit compilers and development headers.",
                "medium", REC_SLIM)


def _where(ctx, stage):
    if len(ctx.stages) == 1:
        return "a single-stage build"
    if stage is ctx.final:
        return f"final stage {stage.label!r}"
    return f"stage {stage.label!r} (inherited by final stage {ctx.final.label!r})"


def _check_run(ctx, chain, stage, run, out):
    text = run.text
    removes = any(_subcommand(argv)[0] in REMOVE_SUBCOMMANDS for argv in run.commands
                  if os.path.basename(argv[0]) in APT | {"apk"} | set(YUM))
    where = _where(ctx, stage)
    env = None
    for argv in run.commands:
        tool, args = _tool(argv)
        sub, operands, options = _subcommand(args)
        prog = os.path.basename(argv[0])
        prog = "pip" if tool == "pip" and not prog.startswith("pip") else prog
        if tool in APT and sub == "install":
            recommends_off = "--no-install-recommends" in options or re.search(
                r"Install-Recommends(=|\s+)\"?(false|0)", text, re.I)
            if not recommends_off and not any(
                re.search(r"Install-Recommends\s+\"?(false|0)", earlier.args, re.I)
                for earlier in _earlier(chain, run.instruction)
            ):
                out.add(stage, run.instruction, "apt-install-recommends",
                        f"`{tool} install` in {where} also installs recommended packages "
                        f"(no --no-install-recommends).", "medium", REC_RECOMMENDS, ("DL3015",), (prog, "install"))
        if tool in APT and sub == "update":
            if "/var/lib/apt/lists" not in text and "dist-clean" not in text and not _mounted(run, "/var/lib/apt"):
                out.add(stage, run.instruction, "apt-lists-kept",
                        f"`{tool} update` in {where} leaves the package index "
                        f"(/var/lib/apt/lists) in the layer; it is not removed in the same RUN.",
                        "medium", REC_APT_LISTS, ("DL3009",), (prog, "update"))
        if tool == "apk" and sub == "add":
            if "--no-cache" not in options and "/var/cache/apk" not in text and not _mounted(
                    run, "/var/cache/apk", "/etc/apk/cache"):
                out.add(stage, run.instruction, "apk-cache-kept",
                        f"`apk add` in {where} without --no-cache keeps the package index "
                        f"in the layer.", "medium", REC_APK, ("DL3019",), ("apk", "add"))
        if tool in YUM and sub == "install":
            code, cache = YUM[tool]
            if not re.search(rf"\b{tool}\b[^;&|]*\bclean\s+all", text) and cache not in text and not _mounted(run, cache):
                out.add(stage, run.instruction, f"{tool}-cache-kept",
                        f"`{tool} install` in {where} leaves the package cache ({cache}) "
                        f"in the layer; `{tool} clean all` is not run in the same RUN.",
                        "medium", REC_YUM.format(tool=tool, cache=cache), (code,), (prog, "install"))
        if tool == "pip" and sub == "install":
            if env is None:
                env = _env_names(chain, run.instruction)
            if (
                not any(o.startswith("--no-cache-dir") for o in options)
                and "PIP_NO_CACHE_DIR" not in env and "PIP_NO_CACHE_DIR" not in text
                and ".cache" not in text and not _mounted(run, ".cache")
                and not any(PIP_CONFIG_NO_CACHE.search(i.args) for i in _earlier(chain, run.instruction))
            ):
                out.add(stage, run.instruction, "pip-cache-kept",
                        f"`pip install` in {where} without --no-cache-dir stores the "
                        f"downloaded wheels in the pip cache inside the layer.",
                        "medium", REC_PIP, ("DL3042",), (prog, "install"))
        if tool in ("npm", "yarn", "pnpm") and _installs_dev_dependencies(tool, sub, operands, options):
            if env is None:
                env = _env_names(chain, run.instruction)
            node_env = {"NODE_ENV", "NPM_CONFIG_PRODUCTION", "npm_config_production", "NPM_CONFIG_OMIT",
                        "npm_config_omit", "YARN_PRODUCTION"}
            if not (node_env & set(env)) and not any(name in text for name in node_env) and " prune" not in text:
                verb = sub or "install"
                out.add(stage, run.instruction, "node-dev-dependencies",
                        f"`{tool} {verb}` in {where} installs devDependencies into the final "
                        f"image (no production flag, NODE_ENV or prune).", "low", REC_NODE, (), (prog,))
        if sub in ("install", "add") and (tool in APT or tool == "apk" or tool in YUM) and not removes:
            tools = sorted(set(operands) & TOOLCHAIN_PACKAGES)
            if tools:
                out.add(stage, run.instruction, "build-toolchain",
                        f"Build tools ({', '.join(tools)}) are installed in {where} and not removed in the same "
                        f"RUN, so they ship in the final image.",
                        "medium", REC_BUILD_TOOLS, (), (tools[0],))


def _installs_dev_dependencies(tool, sub, operands, options):
    joined = " ".join(options)
    if tool == "npm":
        if sub not in ("install", "i", "ci", "add") or operands:
            return False
        return not re.search(r"--production|--only[= ]prod|--omit[= ]dev|--omit$|--prod\b", joined) and \
            not ("--omit" in options and "dev" in options)
    if tool == "yarn":
        if sub not in (None, "install") or operands:
            return False
        return "--production" not in options and "--prod" not in options and "--modules-folder" not in joined
    if tool == "pnpm":
        if sub not in ("install", "i") or operands:
            return False
        return not ({"--prod", "-P", "--production"} & set(options))
    return False


def run(ctx):
    out = _Collector(ctx)
    chain = ctx.shipped_chain()
    _check_base_image(ctx, chain, out)
    for stage in chain:
        for instruction in stage.body:
            if instruction.keyword == "RUN":
                _check_run(ctx, chain, stage, dockerfile.read_run(instruction), out)
    return out.hits


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return textstatic.evaluate_text(payload, sys.modules[__name__])
