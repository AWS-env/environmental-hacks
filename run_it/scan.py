"""Scan a folder with the repository scanner, reporting each stage as it happens through `emit(kind, data)`."""
from __future__ import annotations

import time

from scanner import source
from scanner.report import build_report, default_adapters


def scan(path: str, emit, owners=None) -> dict | None:
    """emit kinds: "log" (str), "running" ([owner names]), "adapter" ((entry, checks, findings)), "done" (report)."""
    started = time.monotonic()
    emit("log", f"collecting files in {path}")
    try:
        with source.resolve(path) as target:
            fileset = source.collect(target.root, source.Limits())
            stats = fileset.stats
            skipped = sum(stats["skipped"].values())
            emit("log", f"{stats['collected']} files in scope of {stats['seen']} seen, {skipped} skipped"
                        f"{' (truncated)' if stats['truncated'] else ''} · {target.commit_sha[:12]} "
                        f"({target.commit_source})")
            adapters = [a for a in default_adapters() if not owners or a.owner in owners]
            emit("running", [f"{a.owner}:{a.name}" for a in adapters])
            report = build_report(target, fileset, adapters,
                                  on_adapter=lambda entry, checks, found: emit("adapter", (entry, checks, found)))
    except source.TargetError as error:
        emit("log", f"scan failed: {error}")
        return None
    emit("log", f"scan finished in {time.monotonic() - started:.1f}s · {report['summary']['findings_total']} findings")
    emit("done", report)
    return report
