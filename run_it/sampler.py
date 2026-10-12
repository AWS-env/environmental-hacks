"""Once per interval: whole-Mac watts, per-process measured watts, and energy totals for the watched app."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

import psutil

from run_it import power


class Child:
    """The command run-it launched (run mode), in its own process group so the whole tree is tracked."""

    def __init__(self, argv: list, cwd: str):
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"}
        self.argv = argv
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.pid = self.proc.pid
        self.returncode = None

    def lines(self):
        for raw in iter(self.proc.stdout.readline, b""):
            yield raw.decode(errors="replace").rstrip("\r\n")

    def exited(self) -> bool:
        """True once the child has exited; it stays unreaped (a zombie) so its final energy is readable."""
        if self.returncode is not None:
            return True
        try:
            return os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None
        except ChildProcessError:
            return True

    def reap(self):
        self.returncode = self.proc.wait()

    def stop(self, timeout=3.0):
        """Ask the process group to stop (SIGINT, then SIGTERM, then SIGKILL); leaves the child unreaped."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            if self.exited():
                return
            try:
                os.killpg(self.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not self.exited():
                time.sleep(0.05)


@dataclass
class Proc:
    pid: int
    name: str
    cpu: float
    watts: float | None
    joules: float | None
    rss: int
    in_app: bool


@dataclass
class Sample:
    interval: float
    system_w: float | None  # whole Mac (SMC telemetry, refreshes slowly)
    system_age: float | None  # seconds since the SMC value last changed
    user_w: float | None  # sum of every readable per-process counter (refreshes every interval)
    procs: list
    app_w: float | None
    app_procs: int
    readable: int
    total: int
    query_ms: float
    system_j: float
    user_j: float
    app_j: float
    events: list = field(default_factory=list)


class Sampler:
    def __init__(self, child: Child | None = None):
        self.child = child
        self.seen = {}  # (pid, create_time) -> [energy_nj, cpu_seconds, joules_since_seen]
        self.labels = {}
        self.system_j = 0.0
        self.user_j = 0.0
        self.app_j = 0.0
        self.system_seen = (None, None)  # (last SMC value, when it changed)
        self.measured = False  # any energy counter readable on this machine
        self.root_nj = 0 if child else None  # cumulative since the child started
        self.last = None
        self.me = os.getpid()

    def _label(self, proc, key, name):
        if proc.pid == self.me:
            return "run-it (dashboard + scan)"
        if key not in self.labels:
            try:
                args = [os.path.basename(a) if i == 0 else a for i, a in enumerate(proc.cmdline()[:3])]
                self.labels[key] = " ".join(args) or name
            except (psutil.Error, OSError):
                self.labels[key] = name
        return self.labels[key]

    def _in_app(self, pid):
        if not self.child:
            return False
        try:
            return os.getpgid(pid) == self.child.pid
        except OSError:
            return False

    def _account_root(self, events):
        """The direct child is read even as a zombie, so its last joules count before it is reaped."""
        if not self.child or self.child.returncode is not None:
            return 0.0
        exited = self.child.exited()
        nj = power.process_energy_nj(self.child.pid)
        delta = 0.0
        if nj is not None:
            delta = (nj - self.root_nj) / 1e9 if self.root_nj is not None else 0.0
            self.root_nj = nj
        if exited:
            self.child.reap()
            events.append(("app", f"exited with code {self.child.returncode}"))
        return delta

    def sample(self) -> Sample:
        started = time.perf_counter()
        now = time.monotonic()
        interval = now - self.last if self.last else 0.0
        self.last = now
        events = []
        system_w = power.system_watts()
        app_j = self._account_root(events)
        if system_w is not None and system_w != self.system_seen[0]:
            self.system_seen = (system_w, now)
        user_j = 0.0
        rows, handles, alive, readable, total = [], {}, set(), 0, 0
        root = self.child.pid if self.child else None
        for proc in psutil.process_iter(["name", "cpu_times", "memory_info", "create_time"], ad_value=None):
            info = proc.info
            if proc.pid == 0 or info["create_time"] is None:
                continue
            total += 1
            key = (proc.pid, info["create_time"])
            alive.add(key)
            handles[proc.pid] = (proc, key)
            cpu_s = sum(info["cpu_times"][:2]) if info["cpu_times"] else None
            nj = power.process_energy_nj(proc.pid)
            readable += nj is not None
            in_app = self._in_app(proc.pid)
            prev = self.seen.get(key)
            watts = cpu = None
            joules = prev[2] if prev else None
            if nj is not None:
                # An app process started under run-it: its whole counter is ours, even before first sight.
                base = prev[0] if prev and prev[0] is not None else (0 if in_app else nj)
                delta = max(0, nj - base) / 1e9
                joules = (joules or 0.0) + delta
                if prev and interval > 0:
                    watts = delta / interval
                    user_j += delta
                if in_app and proc.pid != root:
                    app_j += delta
            if prev and interval > 0 and cpu_s is not None and prev[1] is not None:
                cpu = max(0.0, cpu_s - prev[1]) / interval * 100
            self.seen[key] = [nj, cpu_s, joules]
            rows.append(Proc(proc.pid, info["name"] or "?", cpu or 0.0, watts, joules,
                             info["memory_info"].rss if info["memory_info"] else 0, in_app))
        for key in set(self.seen) - alive:
            del self.seen[key]
            self.labels.pop(key, None)

        self.measured = self.measured or readable > 0
        if system_w is not None and interval:
            self.system_j += system_w * interval
        self.app_j += app_j
        self.user_j += user_j
        rows.sort(key=lambda p: (not p.in_app, -(p.watts if p.watts is not None else -1), -p.cpu))
        top = rows[:60]
        for row in top:
            row.name = self._label(*handles[row.pid], row.name)
        app_rows = [p for p in rows if p.in_app]
        app_w = sum(p.watts for p in app_rows if p.watts is not None) if self.child and interval else None
        return Sample(interval, system_w, now - self.system_seen[1] if system_w is not None else None,
                      user_j / interval if interval and readable else None, top, app_w, len(app_rows),
                      readable, total, (time.perf_counter() - started) * 1000, self.system_j, self.user_j,
                      self.app_j, events)
