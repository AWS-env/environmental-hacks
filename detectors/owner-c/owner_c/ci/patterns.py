"""Command patterns shared by several CI checks."""
import re

# A command that runs a test suite.
TEST_COMMAND = re.compile(
    r"\b(?:pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)|npm\s+(?:run\s+)?test|npm\s+t|"
    r"yarn\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|go\s+test|mvnw?\b.*\b(?:test|verify)|"
    r"gradlew?\b.*\btest|cargo\s+test|jest|vitest|rspec|dotnet\s+test|make\s+test|tox|nox|ctest)(?![\w-])")
# Test selection by what changed: the suite is already incremental.
TEST_SELECTION = re.compile(
    r"--onlyChanged|--changedSince|--testmon|nx\s+affected|--affected\b|--since\b|\bbazel\s+test\b")
# A command that compiles or packages: such a job is not "light".
BUILD_COMMAND = re.compile(
    r"\b(?:npm\s+run\s+build|yarn\s+build|pnpm\s+(?:run\s+)?build|mvnw?\b|gradlew?\b|cargo\s+(?:build|check|clippy|doc|nextest)|go\s+build|"
    r"make\b|docker\s+(?:buildx\s+)?build|tsc\b|webpack\b|vite\s+build|dotnet\s+build)")
# Installing a tool is not running it (`pip install pytest`).
_INSTALL_COMMAND = re.compile(
    r"\b(?:pip3?|conda|mamba|micromamba|apt(?:-get)?|apk|brew|npm|yarn|pnpm|gem|cargo|go)\s+"
    r"(?:install|uninstall|add|remove|i|get)\b|\buv\s+(?:pip\s+(?:un)?install|add|sync)\b|\bpoetry\s+(?:install|add)\b")
# A condition that depends on what changed or on another job's output: the step or job is gated.
# An opt-in label (`github.event.pull_request.labels`, `github.event.label`) also means the job does not run on every change.
GATING_CONDITION = re.compile(r"\bchanged?\b|\bchanges\b|paths-filter|\bneeds\.|\bsteps\.|\blabels?\b")


# A command that only prints or inspects (`python -c "...pytest..."`, `echo`, `grep pytest`) does not run the suite.
NOT_A_RUN = re.compile(r"^\s*(?:echo|printf|cat|grep|python3?\s+-c|pip3?\s+(?:download|show|list|freeze))(?![\w-])")


def runs_tests(command: str) -> bool:
    return bool(TEST_COMMAND.search(command)) and not _INSTALL_COMMAND.search(command) and not NOT_A_RUN.search(command)


def runs_build(command: str) -> bool:
    return bool(BUILD_COMMAND.search(command)) and not _INSTALL_COMMAND.search(command)


def is_gated(data) -> bool:
    """A job or step whose `if` depends on changed files, other jobs' outputs or an opt-in label."""
    condition = data.get("if") if isinstance(data, dict) else None
    return isinstance(condition, str) and bool(GATING_CONDITION.search(condition))
