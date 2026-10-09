"""Command line for the CI detectors: evaluate a contract input, or scan a local checkout.

    PYTHONPATH=detectors/owner-c python -m owner_c.ci evaluate INPUT.json [-o OUT.json]
    PYTHONPATH=detectors/owner-c python -m owner_c.ci scan DIR [--history FILE ...] [--json] [--checks CI-06 ...]

`scan` reads `DIR/.github/workflows/*.yml|yaml` (parse only, never executed). `--history` takes the JSON written by
`python -m owner_c.ci.collector` and enables the run-history checks. Run from the repository root so
`shared.contracts` is importable: every result is validated against the shared contract (including its evidence
against the input) before it is printed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from owner_c.ci.connector import build_inputs
from owner_c.ci.normalize.github_actions import PROFILER, RawHistoryError, normalize
from owner_c.ci.runner import EvaluationError, evaluate


def _validators():
    try:
        from shared.contracts.validation import ContractError, validate, validate_pair
    except ImportError as error:
        print(f"run from the repository root so 'shared' is importable: {error}", file=sys.stderr)
        raise SystemExit(1)
    return ContractError, validate, validate_pair


def _evaluate_cmd(args) -> int:
    ContractError, validate, validate_pair = _validators()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"invalid input file: {error}", file=sys.stderr)
        return 2
    try:
        validate(payload)
        result = evaluate(payload)
        validate_pair(payload, result)
    except (ContractError, EvaluationError) as error:
        print(f"cannot evaluate: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _git_sha(root: Path) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        return sha if len(sha) == 40 else "0" * 40
    except (OSError, subprocess.CalledProcessError):
        return "0" * 40


def _read_workflows(root: Path):
    folder = root / ".github" / "workflows"
    files = []
    for path in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
        try:
            files.append((path.relative_to(root).as_posix(), path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return files


def _scan_cmd(args) -> int:
    ContractError, _validate, validate_pair = _validators()
    root = args.directory.resolve()
    history = {}
    for item in args.history or []:
        try:
            data = normalize(json.loads(Path(item).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, RawHistoryError) as error:
            print(f"cannot read history {item}: {error}", file=sys.stderr)
            return 2
        history[data["workflow_path"]] = data
    payloads = build_inputs(
        repository_id=args.repository_id or f"local:{root.name}", commit_sha=args.commit or _git_sha(root),
        scan_id=str(uuid.uuid4()), files=_read_workflows(root), artifacts={PROFILER: history} if history else None,
        checks=args.checks)
    results = []
    for payload in payloads:
        result = evaluate(payload)
        try:
            validate_pair(payload, result)
        except ContractError as error:
            print(f"{payload['check_id']}: invalid result: {error}", file=sys.stderr)
            return 1
        results.append(result)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    for result in results:
        cov = result["coverage"]
        print(f"{result['check_id']}: {result['status']} ({len(cov['evaluated_scope'])}/{len(result['scope'])} files, "
              f"{len(result['findings'])} findings)")
        for f in result["findings"]:
            ev = f["evidence"][0]
            where = f"{ev['locator']}:{ev['line_start']}" if "line_start" in ev else f"{ev['locator']} {ev['field']}={ev['value']}"
            print(f"  {where} [{f['confidence']}] {f['identity']} - {f['summary']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="owner_c.ci", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate", help="evaluate a contract v1 input payload")
    ev.add_argument("input", type=Path)
    ev.add_argument("-o", "--output", type=Path)
    ev.set_defaults(func=_evaluate_cmd)
    sc = sub.add_parser("scan", help="scan a local checkout's workflow files (and run history) with the CI checks")
    sc.add_argument("directory", type=Path)
    sc.add_argument("--repository-id")
    sc.add_argument("--commit", help="40-character commit SHA (default: git HEAD of the directory)")
    sc.add_argument("--history", action="append", metavar="FILE", help="collector JSON for one workflow (repeatable)")
    sc.add_argument("--checks", nargs="+", metavar="CI-NN", help="only these checks")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(func=_scan_cmd)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
