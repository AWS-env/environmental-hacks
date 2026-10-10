#!/usr/bin/env python3
"""Client-side half of owner C's artifact detectors: run the profiler in the client's own CI, then upload.

    python profile_upload.py --repository-id github:org/repo --commit-sha <40 hex> \
        --artifacts speedscope,memray_stats,lighthouse --python-args "-m pytest tests/" --lighthouse-url http://localhost:8080

Steps: 1. run each requested tool here, on the client's runner (py-spy -> speedscope, memray -> memray_stats,
Lighthouse -> lighthouse); 2. ask `owner-c-presign` for 15-minute PUT URLs; 3. upload repo.zip and every artifact that
was produced; 4. upload manifest.json LAST, which triggers the parser Lambda. A tool that cannot run is skipped with a
warning and its artifact is simply not uploaded (the detector reports `unavailable`, never clean). Presigned URLs are
credentials and are never printed. Nothing uploaded is ever executed by us.

Standard library only, plus the AWS CLI that GitHub-hosted runners already have.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile

PYSPY = "py-spy==0.4.2"
MEMRAY = "memray==1.20.0"
LIGHTHOUSE = "lighthouse@13.5.0"
KNOWN = ("speedscope", "memray_stats", "lighthouse")
MAX_ARTIFACT_BYTES = 50_000_000  # the parser rejects anything larger
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox"}


def note(level: str, message: str) -> None:
    print(f"::{level}::{message}" if os.environ.get("GITHUB_ACTIONS") else f"{level}: {message}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def pip_install(spec: str) -> None:
    run([sys.executable, "-m", "pip", "install", "--quiet", spec])


def installed_tool(name: str) -> str:
    """py-spy is a standalone executable, not a module: find it on PATH or next to the interpreter's scripts."""
    found = shutil.which(name)
    if found:
        return found
    candidate = pathlib.Path(sysconfig.get_path("scripts")) / (name + (".exe" if sys.platform.startswith("win") else ""))
    if candidate.exists():
        return str(candidate)
    raise RuntimeError(f"{name} was installed but cannot be found")


def collect_speedscope(out: pathlib.Path, python_args: list[str], cwd: pathlib.Path) -> None:
    pip_install(PYSPY)
    # --subprocesses so `python -m pytest` workers are sampled too; no --nonblocking (it can give inaccurate results).
    run([installed_tool("py-spy"), "record", "--format", "speedscope", "--subprocesses", "--output", str(out),
         "--", sys.executable, *python_args], cwd=cwd, stdout=subprocess.DEVNULL)


def collect_memray_stats(out: pathlib.Path, python_args: list[str], cwd: pathlib.Path) -> None:
    if sys.platform.startswith("win"):
        raise RuntimeError("memray has no Windows build; use a Linux or macOS runner")
    pip_install(MEMRAY)
    binfile = out.with_suffix(".bin")
    run([sys.executable, "-m", "memray", "run", "--force", "-o", str(binfile), *python_args], cwd=cwd,
        stdout=subprocess.DEVNULL)
    run([sys.executable, "-m", "memray", "stats", "--json", "--force", "-o", str(out), str(binfile)], cwd=cwd,
        stdout=subprocess.DEVNULL)


def collect_lighthouse(out: pathlib.Path, url: str) -> None:
    run(["npx", "--yes", LIGHTHOUSE, url, "--output=json", f"--output-path={out}", "--only-categories=performance",
         "--chrome-flags=--headless=new --no-sandbox", "--quiet"], shell=sys.platform.startswith("win"))


def collect(name: str, out: pathlib.Path, args: argparse.Namespace, repo: pathlib.Path) -> None:
    python_args = shlex.split(args.python_args, posix=not sys.platform.startswith("win"))
    if name in ("speedscope", "memray_stats") and not python_args:
        raise RuntimeError("python-args is empty: name the script or `-m module` to run, e.g. `-m pytest tests/`")
    if name == "speedscope":
        collect_speedscope(out, python_args, repo)
    elif name == "memray_stats":
        collect_memray_stats(out, python_args, repo)
    elif name == "lighthouse":
        if not args.lighthouse_url:
            raise RuntimeError("lighthouse-url is empty: give a reachable preview URL")
        collect_lighthouse(out, args.lighthouse_url)


def usable(path: pathlib.Path) -> str | None:
    """None if the file is a JSON artifact we can upload, else the reason it is not."""
    if not path.is_file() or path.stat().st_size == 0:
        return "the tool produced no output"
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        return f"output is larger than {MAX_ARTIFACT_BYTES} bytes"
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "output is not valid JSON"
    return None


def zip_repo(src: pathlib.Path, dest: pathlib.Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src)
            if not f.is_file() or SKIP_DIRS & set(rel.parts) or f.name == ".env" or f.name.startswith(".env."):
                continue
            z.write(f, "repo/" + rel.as_posix())  # one top folder, like a GitHub archive: the parser drops it


def presign(repository_id: str, sha: str, artifacts: list[str], region: str, work: pathlib.Path) -> dict:
    out = work / "presign.json"
    payload = json.dumps({"repository_id": repository_id, "commit_sha": sha, "artifacts": artifacts})
    run(["aws", "lambda", "invoke", "--function-name", "owner-c-presign", "--region", region,
         "--cli-binary-format", "raw-in-base64-out", "--payload", payload, str(out)], stdout=subprocess.DEVNULL)
    result = json.loads(out.read_text(encoding="utf-8"))
    if "urls" not in result:
        raise RuntimeError(f"presign failed: {result.get('errorMessage', 'no urls returned')}")
    return result["urls"]


def put(url: str, path: pathlib.Path) -> int:
    request = urllib.request.Request(url, data=path.read_bytes(), method="PUT")
    with urllib.request.urlopen(request) as response:  # the URL is a credential: never echo it, even on error
        return response.status


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--repository-id", required=True)
    p.add_argument("--commit-sha", required=True)
    p.add_argument("--artifacts", required=True, help=f"comma list of {', '.join(KNOWN)}")
    p.add_argument("--python-args", default="", help="arguments after `python`, e.g. `-m pytest tests/` or `app.py`")
    p.add_argument("--lighthouse-url", default="")
    p.add_argument("--repo-dir", default=".")
    p.add_argument("--region", default="ap-south-1")
    args = p.parse_args(argv)

    wanted = [a.strip() for a in args.artifacts.split(",") if a.strip()]
    unknown = sorted(set(wanted) - set(KNOWN))
    if unknown or not wanted:
        note("error", f"artifacts must be from {list(KNOWN)}; unknown: {unknown}")
        return 2
    repo = pathlib.Path(args.repo_dir).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        produced: dict[str, pathlib.Path] = {}
        for name in dict.fromkeys(wanted):
            out = work / f"{name}.json"
            try:
                collect(name, out, args, repo)
                reason = usable(out)
            except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
            if reason:
                note("warning", f"{name} skipped: {reason}. No {name} artifact is uploaded; its checks report unavailable.")
            else:
                produced[name] = out
                print(f"collected {name}", flush=True)
        if not produced:
            note("notice", "no artifact could be produced; nothing was uploaded")
            return 0

        zip_repo(repo, work / "repo.zip")
        manifest = work / "manifest.json"
        manifest.write_text(json.dumps({"repository_id": args.repository_id, "commit_sha": args.commit_sha,
                                        "artifacts": list(produced)}), encoding="utf-8")
        try:
            urls = presign(args.repository_id, args.commit_sha, list(produced), args.region, work)
            for name, path in [("repo", work / "repo.zip"), *produced.items(), ("manifest", manifest)]:  # manifest last
                print(f"uploaded {name}: HTTP {put(urls[name]['url'], path)}", flush=True)
        except Exception as exc:  # urllib errors can embed the signed URL; report the type only
            note("error", f"upload failed ({type(exc).__name__}); presigned URLs are not shown")
            return 1
    print("done: the parser Lambda runs from the manifest upload; results are published to the findings bus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
