"""Worker Lambda (invoked asynchronously by the API): download a public GitHub repo and scan it.

Lambda has no git binary, so the repository arrives as a codeload.github.com tarball, bounded in
size and time and extracted with path checks; links, devices and FIFOs are never extracted. The
scanner only reads files as text: nothing from the repository is executed, installed or imported.
Every failure ends in status `error`; the API covers the cases the worker cannot (timeout, OOM).
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import time
import traceback
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scan_api import hub, store
from scanner import source
from scanner.core import AdapterUnavailable
from scanner.report import build_report, default_adapters

GITHUB_API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
HEADERS = {"User-Agent": "environmental-hacks-scan-api"}
MAX_TARBALL_BYTES = 50_000_000
MAX_EXTRACTED_BYTES = 1_000_000_000  # stays well inside the 2 GB ephemeral storage set in the template
MAX_MEMBERS = 100_000
DOWNLOAD_SECONDS = 120
# Owners A and B run on Node.js, which the python3.12 Lambda runtime does not include.
LAMBDA_OWNERS = {"C", "D"}
NODE_REASON = "needs Node.js, which the scan API's Python Lambda runtime does not include; run the CLI locally"


class ScanError(Exception):
    """A failure whose message is safe and useful to show to the user."""


class NodeOnly:
    def __init__(self, adapter):
        self.owner, self.name = adapter.owner, adapter.name

    def run(self, ctx):
        raise AdapterUnavailable(NODE_REASON)


def resolve_sha(owner: str, repo: str) -> str | None:
    """HEAD commit SHA from the GitHub API, or None when rate-limited or unreachable (scan still runs)."""
    request = Request(f"{GITHUB_API}/repos/{owner}/{repo}/commits/HEAD",
                      headers={**HEADERS, "Accept": "application/vnd.github.sha"})
    try:
        with urlopen(request, timeout=15) as response:
            sha = response.read(100).decode("ascii", "replace").strip()
    except HTTPError as error:
        if error.code in (404, 451):
            raise ScanError("repository not found or not public") from None
        if error.code == 409:
            raise ScanError("repository is empty") from None
        return None  # 403/429 rate limit or 5xx
    except (URLError, OSError):
        return None
    return sha if source.SHA.match(sha) else None


def download(url: str, path: Path) -> None:
    started, total = time.monotonic(), 0
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=30) as response, path.open("wb") as out:
            if int(response.headers.get("Content-Length") or 0) > MAX_TARBALL_BYTES:
                raise ScanError(f"repository archive is larger than {MAX_TARBALL_BYTES // 1_000_000} MB")
            while chunk := response.read(1 << 16):
                total += len(chunk)
                if total > MAX_TARBALL_BYTES:
                    raise ScanError(f"repository archive is larger than {MAX_TARBALL_BYTES // 1_000_000} MB")
                if time.monotonic() - started > DOWNLOAD_SECONDS:
                    raise ScanError(f"repository download took longer than {DOWNLOAD_SECONDS}s")
                out.write(chunk)
    except HTTPError as error:
        if error.code == 404:
            raise ScanError("repository not found or not public") from None
        raise ScanError(f"repository download failed (HTTP {error.code})") from None
    except (URLError, OSError) as error:
        raise ScanError(f"repository download failed: {error}") from None


def safe_extract(tarball: Path, dest: Path) -> tuple[Path, str | None]:
    """Extract regular files and directories only; reject absolute or `..` paths and oversized archives.

    Returns the repository root (GitHub wraps it in one top-level directory) and the commit SHA that
    `git archive` records in the pax global header, if present.
    """
    members, total = [], 0
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            for count, member in enumerate(tar, 1):
                name = PurePosixPath(member.name)
                if count > MAX_MEMBERS:
                    raise ScanError(f"repository archive has more than {MAX_MEMBERS} entries")
                if name.is_absolute() or ".." in name.parts:
                    raise ScanError(f"unsafe path in repository archive: {member.name[:200]}")
                if member.isfile():
                    total += member.size
                    if total > MAX_EXTRACTED_BYTES:
                        raise ScanError(f"repository is larger than {MAX_EXTRACTED_BYTES // 1_000_000} MB unpacked")
                elif not member.isdir():
                    continue  # symlinks, hardlinks, devices, FIFOs: never extracted (the scanner skips links)
                members.append(member)
            tar.extractall(dest, members=members, filter="data")
            header_sha = tar.pax_headers.get("comment", "")
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ScanError(f"could not unpack the repository archive: {error}") from None
    tops = list(dest.iterdir()) if dest.exists() else []
    root = tops[0] if len(tops) == 1 and tops[0].is_dir() else dest
    return root, header_sha if source.SHA.match(header_sha) else None


def run_scan(scan_id: str, owner: str, repo: str, workdir: Path, results: list | None = None) -> dict:
    sha = resolve_sha(owner, repo)
    tarball = workdir / "repo.tar.gz"
    # Download the exact commit the API resolved, so the report's SHA matches the scanned files.
    download(f"{CODELOAD}/{owner}/{repo}/tar.gz/{sha or 'HEAD'}", tarball)
    root, header_sha = safe_extract(tarball, workdir / "src")
    tarball.unlink()
    commit, commit_source = ((sha, "github-api") if sha else (header_sha, "tarball-header") if header_sha
                             else ("", "content-hash"))
    target = source.Target(f"github:{owner}/{repo}", root, commit, commit_source, f"https://github.com/{owner}/{repo}")
    adapters = [a if a.owner in LAMBDA_OWNERS else NodeOnly(a) for a in default_adapters()]
    return build_report(target, source.collect(root, source.Limits()), adapters, scan_id=scan_id, results=results)


def publish_to_hub(scan_id: str, results: list) -> dict | None:
    """Best-effort: the report is already in S3, so a hub failure is recorded and the scan stays done."""
    bus = hub.bus_name()
    if not bus:
        return None
    try:
        summary = hub.publish(results, bus)
    except Exception as error:  # botocore ClientError etc.
        print(f"scan {scan_id} hub publish failed: {type(error).__name__}: {error}")
        return {"bus": bus, "error": f"publish failed ({type(error).__name__})"}
    print(f"scan {scan_id} hub: {json.dumps(summary)}")
    return summary


def handler(event, context=None):
    scan_id, repo_url = event.get("scan_id", ""), event.get("repo_url", "")
    created_at = event.get("created_at") or store.now()
    if not store.SCAN_ID.match(scan_id):
        print(f"ignoring event without a valid scan_id: {event!r:.300}")
        return
    workdir = Path(tempfile.mkdtemp(prefix="scan-"))  # /tmp survives warm starts: always cleaned below
    try:
        try:
            owner, repo = store.parse_repo_url(repo_url)
        except ValueError as error:
            raise ScanError(str(error)) from None
        store.put_status(scan_id, "running", repo_url, created_at)
        started = time.monotonic()
        results = []
        report = run_scan(scan_id, owner, repo, workdir, results)
        size = store.put_json(scan_id, "report.json", report)
        store.put_status(scan_id, "done", repo_url, created_at, report_bytes=size, hub=publish_to_hub(scan_id, results))
        print(f"scan {scan_id} {owner}/{repo} done in {time.monotonic() - started:.1f}s, report {size} bytes")
    except Exception as error:
        if isinstance(error, ScanError):
            message = str(error)
            print(f"scan {scan_id} failed: {message}")
        else:
            message = f"internal error ({type(error).__name__})"
            traceback.print_exc()
        try:
            store.put_status(scan_id, "error", repo_url, created_at, error=message)
        except Exception:
            traceback.print_exc()  # the API reports a stale `running` scan as error
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
