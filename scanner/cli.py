"""Scan a local directory or a public GitHub repository and write report.json.

    python -m scanner scan https://github.com/owner/repo -o report.json
    python -m scanner scan path/to/repo -o dashboard/sample-report.js   # .js: loadable from file://

Run from the repository root (so `shared.contracts` and the detectors are importable).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scanner import source
from scanner.report import build_report

OWNERS = ("A", "B", "C", "D")


def _write(report, path: Path):
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if path.suffix == ".js":  # a <script> the dashboard can load from file:// (fetch cannot)
        text = f"window.SCAN_REPORT = {text};"
    path.write_text(text + "\n", encoding="utf-8")


def _summary(report) -> str:
    s, f = report["summary"], report["files"]
    statuses = ", ".join(f"{v} {k}" for k, v in s["checks_by_status"].items() if v)
    conf = ", ".join(f"{v} {k}" for k, v in s["findings_by_confidence"].items())
    lines = [
        f"{report['repository']['id']} @ {report['repository']['commit_sha'][:12]}",
        f"files: {f['collected']} collected of {f['seen']} seen{' (truncated)' if f['truncated'] else ''}",
        f"checks: {s['checks_total']} ({statuses})",
        f"findings: {s['findings_total']} ({conf})",
    ]
    lines += [f"adapter {a['owner']} {a['status']}: {a['reason']}" for a in report["adapters"] if a["status"] != "ok"]
    lines.append(f"took {report['timings']['total_seconds']}s")
    return "\n".join(lines)


def _scan(args) -> int:
    from scanner.report import default_adapters

    wanted = {o.strip().upper() for o in args.owners.split(",")} if args.owners else set(OWNERS)
    adapters = [a for a in default_adapters() if a.owner in wanted]
    limits = source.Limits(args.max_files, args.max_file_bytes)
    try:
        with source.resolve(args.target) as target:
            fileset = source.collect(target.root, limits)
            report = build_report(target, fileset, adapters)
    except source.TargetError as error:
        print(f"scan failed: {error}", file=sys.stderr)
        return 2
    _write(report, args.output)
    print(_summary(report) + f"\nwrote {args.output}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scanner", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan a directory or public GitHub URL")
    scan.add_argument("target", help="local directory or https://github.com/<owner>/<repo>")
    scan.add_argument("-o", "--output", type=Path, default=Path("report.json"),
                      help="report path (.json, or .js for the dashboard's file:// loader)")
    scan.add_argument("--owners", help="comma-separated owners to run (default: A,B,C,D)")
    scan.add_argument("--max-files", type=int, default=source.DEFAULT_MAX_FILES)
    scan.add_argument("--max-file-bytes", type=int, default=source.DEFAULT_MAX_FILE_BYTES)
    scan.set_defaults(func=_scan)
    args = parser.parse_args(argv)
    return args.func(args)
