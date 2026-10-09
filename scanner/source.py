"""Resolve a scan target to a local directory and collect its text files within fixed limits.

Repository content is only ever read as text. Nothing here (or anywhere in the scanner) runs,
installs, builds or imports code from the scanned repository.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
SHA = re.compile(r"^[0-9a-f]{40}$")

EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", "third_party",
    "venv", ".venv", "env", "site-packages", "__pycache__", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".nuxt", "coverage", "target",
})
GENERATED_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "composer.lock", "Cargo.lock", "uv.lock",
})
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map")

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_TOTAL_BYTES = 100_000_000
CLONE_TIMEOUT_SECONDS = 180


class TargetError(ValueError):
    """The scan target cannot be resolved (bad URL, clone failure, missing directory)."""


@dataclass
class Target:
    repository_id: str
    root: Path
    commit_sha: str
    commit_source: str  # "git", "content-hash", or (scan API) "github-api" / "tarball-header"
    url: str | None = None


@dataclass
class Limits:
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    def as_context(self) -> dict:
        """The collection settings that decide scope, declared in every detector input."""
        return {"max_files": self.max_files, "max_file_bytes": self.max_file_bytes,
                "excluded_dirs": sorted(EXCLUDED_DIRS)}


@dataclass
class FileSet:
    files: list  # [(repo-relative posix path, text)], sorted by path
    stats: dict = field(default_factory=dict)


def _git(args, cwd=None, timeout=60):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout.strip()


def _head_sha(root: Path) -> str | None:
    try:
        sha = _git(["-c", "core.fsmonitor=false", "rev-parse", "HEAD"], cwd=root)
    except (OSError, subprocess.SubprocessError):
        return None
    return sha if SHA.match(sha) else None


@contextlib.contextmanager
def resolve(target: str):
    """Yield a Target for a local directory or a public GitHub URL (shallow-cloned to a temp dir)."""
    match = GITHUB_URL.match(target)
    if match:
        owner, repo = match.groups()
        url = f"https://github.com/{owner}/{repo}"
        tmp = tempfile.mkdtemp(prefix="scan-")
        try:
            dest = Path(tmp, "repo")
            try:
                # Shallow, single branch, no tags or submodules; HTTPS only. A fresh clone has no hooks.
                _git(["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "clone",
                      "--depth", "1", "--single-branch", "--no-tags", "--quiet", "--", url, str(dest)],
                     timeout=CLONE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                raise TargetError(f"git clone of {url} timed out after {CLONE_TIMEOUT_SECONDS}s") from None
            except subprocess.CalledProcessError as error:
                raise TargetError(f"git clone of {url} failed: {error.stderr.strip()[:300]}") from None
            except OSError as error:
                raise TargetError(f"git is required to clone {url}: {error}") from None
            sha = _head_sha(dest)
            if sha is None:
                raise TargetError(f"could not read the cloned commit of {url}")
            yield Target(f"github:{owner}/{repo}", dest, sha, "git", url)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    if target.startswith(("http://", "https://", "git@")):
        raise TargetError("only public https://github.com/<owner>/<repo> URLs or local directories are supported")
    root = Path(target).expanduser().resolve()
    if not root.is_dir():
        raise TargetError(f"not a directory: {target}")
    sha = _head_sha(root)  # a local working tree may differ from HEAD; content-hash it if not a checkout
    yield Target(f"local:{root.name}", root, sha or "", "git" if sha else "content-hash")


def _read_text(path: Path) -> str | None:
    """File text, or None when it looks binary or is not UTF-8."""
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def collect(root: Path, limits: Limits) -> FileSet:
    """Walk `root` deterministically, keeping text files within the limits; count every skip."""
    skipped = Counter()
    by_ext = Counter()
    files, total, seen = [], 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for name in sorted(dirnames):
            if name in EXCLUDED_DIRS:
                skipped["excluded_dir"] += 1
            elif Path(dirpath, name).is_symlink():
                skipped["symlink"] += 1
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            seen += 1
            path = Path(dirpath, name)
            rel = path.relative_to(root).as_posix()
            if name == ".git":  # a worktree/submodule pointer file
                skipped["excluded_dir"] += 1
                continue
            if path.is_symlink() or not path.is_file():
                skipped["symlink"] += 1
                continue
            if name in GENERATED_NAMES or name.endswith(GENERATED_SUFFIXES):
                skipped["generated"] += 1
                continue
            size = path.stat().st_size
            if size > limits.max_file_bytes:
                skipped["too_large"] += 1
                continue
            if len(files) >= limits.max_files:
                skipped["over_file_cap"] += 1
                continue
            if total + size > limits.max_total_bytes:
                skipped["over_byte_cap"] += 1
                continue
            try:
                text = _read_text(path)
            except OSError:
                skipped["unreadable"] += 1
                continue
            if text is None:
                skipped["binary"] += 1
                continue
            files.append((rel, text))
            total += size
            by_ext[Path(name).suffix.lower() or name] += 1
    files.sort(key=lambda item: item[0])
    stats = {
        "seen": seen, "collected": len(files), "bytes_collected": total,
        "skipped": dict(sorted(skipped.items())),
        "truncated": bool(skipped["over_file_cap"] or skipped["over_byte_cap"]),
        "by_extension": dict(by_ext.most_common(15)),
        "limits": limits.as_context(),
    }
    return FileSet(files, stats)


def content_sha(files) -> str:
    """A 40-hex identity for a directory that is not a Git checkout (contract v1 needs a full SHA)."""
    digest = hashlib.sha1()
    for path, text in files:
        digest.update(path.encode("utf-8") + b"\0" + text.encode("utf-8") + b"\0")
    return digest.hexdigest()
