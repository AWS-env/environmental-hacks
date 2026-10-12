"""Live energy, carbon and water dashboard for the current folder.

    run-it                    watch: scan this folder, show live power for your processes
    run-it npm start          run: launch the command and measure its whole process tree
    run-it -- python app.py   use -- when the command has options of its own
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from run_it import impact, live

MEASURED_BY = "macOS per-process energy counters"


def _findings(report) -> str | None:
    if not report:
        return None
    s = report["summary"]
    return f"{s['findings_total']} ({s['findings_by_confidence']['high']} high)"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run-it", description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between samples (default 1)")
    parser.add_argument("--no-scan", action="store_true", help="live meters only, skip the findings scan")
    parser.add_argument("--owners", help="comma-separated scanner owners to run (default: all)")
    parser.add_argument("--port", type=int, default=live.DEFAULT_PORT,
                        help=f"serve live state for the web dashboard on 127.0.0.1:PORT (default {live.DEFAULT_PORT})")
    parser.add_argument("--no-serve", action="store_true", help="do not serve live state for the web dashboard")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command to launch and measure")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    owners = {o.strip().upper() for o in args.owners.split(",")} if args.owners else None

    from run_it.app import RunIt  # textual import is slow; keep --help instant

    app = RunIt(os.getcwd(), command or None, max(0.25, args.interval), not args.no_scan, owners,
                None if args.no_serve else args.port)
    app.run()
    if app.start_error:
        print(f"run-it: {app.start_error}", file=sys.stderr)
        return 127
    sampler = app.sampler
    if sampler is None:
        return 1
    seconds = time.monotonic() - app.started
    if app.child:
        joules, label = sampler.app_j, app._label()
    else:
        joules, label = sampler.user_j, "watch · your processes"
    print(impact.receipt(label, seconds, joules if sampler.measured else None, MEASURED_BY, _findings(app.report)))
    if sampler.system_j:
        print(f"  {'whole Mac':<8} {impact.fmt_energy(sampler.system_j):<20} [measured: SMC telemetry, coarse]")
    code = app.child.returncode if app.child else 0
    code = code if code and code > 0 else 0  # negative: run-it stopped it with a signal on quit
    if app.scanning:
        # The scanner's thread pool is joined at interpreter exit, so an unfinished scan would hold the
        # terminal until every detector finished. Stop its subprocesses and leave now.
        import psutil
        for proc in psutil.Process().children(recursive=True):
            try:
                proc.terminate()
            except psutil.Error:
                pass
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code
