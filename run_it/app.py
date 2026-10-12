"""The live dashboard: measured power, modelled CO2e/water, the process table, streaming findings and a log."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, RichLog, Sparkline, Static

from run_it import impact, live
from run_it.sampler import Child, Sampler

ACCENT, DIM, WARN, HOT = "#a3e635", "#5f7363", "#fbbf24", "#f87171"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
KIND_STYLE = {"run-it": ACCENT, "scan": "#60a5fa", "find": WARN, "app": "#c084fc", "power": "#2dd4bf",
              "warn": HOT}
CONF_STYLE = {"high": f"bold {HOT}", "medium": WARN, "low": DIM}
HISTORY = 120
TIMELINE = 900  # samples kept for the web dashboard (15 minutes at the default 1 s interval)
CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def _watts(value):
    if value is None:
        return Text("—", style=DIM)
    style = HOT if value >= 5 else WARN if value >= 1 else ""
    return Text(f"{value:6.2f}", style=style, justify="right")


def _mem(rss):
    return f"{rss / 2**30:.1f}G" if rss >= 2**30 else f"{rss / 2**20:.0f}M"


class FindingScreen(ModalScreen):
    BINDINGS = [Binding("escape,q", "dismiss", "close"), Binding("c", "copy", "copy agent prompt")]

    def __init__(self, finding):
        super().__init__()
        self.finding = finding

    def compose(self) -> ComposeResult:
        f = self.finding
        where = f"{f['file']}:{f['line']}" if f["line"] else str(f["file"])
        body = Text()
        body.append(f"{f['check_id']}  ", style=f"bold {ACCENT}")
        body.append(f"{f['confidence']} confidence\n", style=CONF_STYLE[f["confidence"]])
        body.append(f"{f['pattern'] or ''}\n\n", style="bold")
        body.append(f"{where}\n", style="underline")
        body.append(f"{f['summary']}\n\n")
        for item in f["evidence"]:
            label = f"{item['locator']}:{item.get('line_start', '')}"
            body.append(f"── {label}\n", style=DIM)
            body.append(f"{item['value'] if isinstance(item['value'], str) else item['value']}\n", style="#e5e7eb")
        body.append(f"\nfix  {f['recommendation']}\n\n", style=ACCENT)
        body.append("static evidence: proves the pattern at this line, not its runtime cost\n", style=DIM)
        body.append("c copies a ready-made prompt for your coding agent · esc closes", style=DIM)
        with VerticalScroll(id="finding"):
            yield Static(body)

    def on_mount(self):
        self.query_one("#finding").border_title = "finding"

    def action_copy(self):
        try:
            subprocess.run(["pbcopy"], input=self.finding["agent_prompt"].encode(), check=True, timeout=2)
            self.notify("agent prompt copied")
        except (OSError, subprocess.SubprocessError):
            self.notify("pbcopy unavailable", severity="warning")


class RunIt(App):
    TITLE = "run-it"
    CSS = f"""
    Screen {{ background: #0a0d0b; }}
    .box {{ border: round #24322a; border-title-color: {ACCENT}; border-title-style: bold; padding: 0 1;
            background: #0a0d0b; }}
    #top {{ height: 1; padding: 0 1; background: #121a15; }}
    #meters {{ height: 7; }}
    #power {{ width: 3fr; }}
    #impact {{ width: 2fr; }}
    Sparkline {{ height: 1; margin-bottom: 0; }}
    Sparkline > .sparkline--max-color {{ color: {ACCENT}; }}
    Sparkline > .sparkline--min-color {{ color: #1f3b25; }}
    #query {{ height: 1; padding: 0 1; color: {DIM}; }}
    #main {{ height: 1fr; }}
    #procs {{ width: 1fr; }}
    #findings {{ width: 1fr; }}
    DataTable {{ background: #0a0d0b; scrollbar-size: 1 1; height: 1fr; overflow-x: hidden; }}
    DataTable > .datatable--header {{ background: #0a0d0b; color: {DIM}; text-style: bold; }}
    DataTable > .datatable--cursor {{ background: #1c2a20; }}
    #log {{ height: 11; scrollbar-size: 1 1; overflow-x: hidden; }}
    FindingScreen {{ align: center middle; background: #000000 70%; }}
    #finding {{ width: 90%; height: 80%; border: round {ACCENT}; background: #0a0d0b; padding: 1 2; }}
    """
    BINDINGS = [Binding("q", "quit", "quit"), Binding("r", "rescan", "rescan"),
                Binding("f", "focus('findings')", "findings"), Binding("p", "focus('procs')", "processes"),
                Binding("l", "focus('log')", "log")]

    def __init__(self, cwd: str, command: list | None, interval: float, scan: bool, owners=None,
                 port: int | None = live.DEFAULT_PORT):
        super().__init__()
        self.cwd, self.command, self.interval, self.do_scan, self.owners = cwd, command, interval, scan, owners
        self.port = port
        self.live = live.LiveState()
        self.server = None
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.timeline = deque(maxlen=TIMELINE)
        self.app_energy = {}  # (pid, name) -> latest cumulative joules, for the command's processes
        self.child: Child | None = None
        self.sampler: Sampler | None = None
        self.started = time.monotonic()
        self.stopping = False
        self.last: object = None
        self.report = None
        self.findings = {}
        self.scan_state = {}  # owner -> "running" | (status, seconds, findings)
        self.scanning = False
        self.tick = 0
        self.history = {key: [0.0] * HISTORY for key in ("app", "user", "mac")}  # grows in from the right
        self.start_error = None

    # ── layout ─────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Horizontal(id="meters"):
            with Vertical(id="power", classes="box"):
                if self.command:
                    yield Static(id="pw-app")
                    yield Sparkline([0.0] * HISTORY, summary_function=max, id="sp-app")
                yield Static(id="pw-user")
                yield Sparkline([0.0] * HISTORY, summary_function=max, id="sp-user")
                yield Static(id="pw-mac")
                if not self.command:
                    yield Sparkline([0.0] * HISTORY, summary_function=max, id="sp-mac")
            yield Static(id="impact", classes="box")
        yield Static(id="query")
        with Horizontal(id="main"):
            yield DataTable(id="procs", classes="box", cursor_type="row", zebra_stripes=False)
            yield DataTable(id="findings", classes="box", cursor_type="row", zebra_stripes=False)
        yield RichLog(id="log", classes="box", max_lines=2000, wrap=False, markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#power").border_title = "power · measured"
        self.query_one("#impact").border_title = "impact"
        procs = self.query_one("#procs", DataTable)
        procs.border_title = "processes · W from kernel energy counters"
        procs.add_columns("PID", "PROCESS", "CPU%", "W", "J", "MEM")
        findings = self.query_one("#findings", DataTable)
        findings.border_title = "findings · static (not measured)"
        findings.add_columns("CONF", "CHECK", "WHERE", "SUMMARY")
        self.query_one("#log").border_title = "log"

        self.log_line("run-it", f"watching {self.cwd}")
        self.log_line("power", "sources: proc_pid_rusage energy_nj per process (1 s) · ioreg SMC whole Mac")
        self.log_line("power", f"CO₂e {impact.GRID.value} {impact.GRID.unit} ({impact.GRID.source})")
        self.log_line("power", f"water ~{impact.WATER.value} {impact.WATER.unit} (generation water only)")
        if self.command:
            try:
                self.child = Child(self.command, self.cwd)
            except OSError as error:
                self.start_error = f"cannot start {' '.join(self.command)}: {error}"
                self.log_line("warn", self.start_error)
            else:
                self.log_line("app", f"started {' '.join(self.command)} (pid {self.child.pid})")
                threading.Thread(target=self._pump_output, daemon=True).start()
        self.sampler = Sampler(self.child)
        if self.port:
            try:
                self.server = live.serve(self.live, self.port)
                self.log_line("run-it", f"live state on http://127.0.0.1:{self.port}/state · open /environment to watch it")
            except OSError as error:
                self.log_line("warn", f"live state not served (port {self.port}: {error.strerror}); use --port")
        self.sample_loop()
        if self.do_scan:
            self.action_rescan()
        self.set_interval(0.15, self._spin)
        self.render_top()

    # ── logging ────────────────────────────────────────────────────────────
    def log_line(self, kind: str, message: str | Text):
        line = Text(datetime.now().strftime("%H:%M:%S "), style=DIM)
        line.append(f"{kind:<6} ", style=f"bold {KIND_STYLE.get(kind, '')}")
        line.append(message if isinstance(message, Text) else Text(message))
        self.query_one("#log", RichLog).write(line)

    def _pump_output(self):
        for line in self.child.lines():
            try:
                self.call_from_thread(self.log_line, "app", Text.from_ansi(line))
            except RuntimeError:  # dashboard already closed
                return

    # ── sampling ───────────────────────────────────────────────────────────
    @work(thread=True, exclusive=True, group="sampler")
    def sample_loop(self):
        while not self.stopping:
            sample = self.sampler.sample()
            self.call_from_thread(self.show_sample, sample)
            deadline = time.monotonic() + self.interval
            while time.monotonic() < deadline and not self.stopping:
                time.sleep(0.05)
        if self.child and self.child.returncode is None:
            self.call_from_thread(self.log_line, "app", "stopping (SIGINT → SIGTERM → SIGKILL)…")
            self.child.stop()
            self.sampler.sample()  # reads the child's final energy, then reaps it
        self.call_from_thread(self.exit)

    def show_sample(self, s):
        self.last = s
        for kind, message in s.events:
            self.log_line(kind, message)
        if s.interval:
            for key, value in (("app", s.app_w), ("user", s.user_w), ("mac", s.system_w)):
                self.history[key] = (self.history[key] + [value or 0.0])[-HISTORY:]
            for key in self.history:
                for spark in self.query(f"#sp-{key}"):
                    spark.data = self.history[key]
        self.render_power(s)
        self.render_impact(s)
        self.render_procs(s)
        self.render_top()
        self.render_query()
        for p in s.procs:
            if p.in_app and p.joules is not None:
                self.app_energy[(p.pid, p.name)] = p.joules
        if s.interval:
            self.timeline.append({"t": round(time.monotonic() - self.started, 1), "app": s.app_w,
                                  "user": s.user_w, "system": s.system_w})
        self.publish()

    # ── live state for the web dashboard ──────────────────────────────────
    @staticmethod
    def _factor(factor: impact.Factor) -> dict:
        return {"value": factor.value, "unit": factor.unit, "source": factor.source,
                "override": factor.source.endswith("override")}

    def snapshot(self) -> dict:
        """Everything the web dashboard shows, with the same provenance tags as the terminal view."""
        s = self.last
        measured = bool(self.sampler and self.sampler.measured)
        scope = "app" if self.child else "user"
        joules = (s.app_j if self.child else s.user_j) if s else 0.0
        findings = sorted(self.findings.values(), key=lambda f: (CONF_RANK[f["confidence"]], f["check_id"],
                                                                f["file"] or "", f["line"] or 0))
        owners = {owner: state if state == "running" else {"status": state[0], "seconds": state[1], "findings": state[2]}
                  for owner, state in sorted(self.scan_state.items())}
        return {
            "status": "stopping" if self.stopping else "running",
            "session": {
                "id": self.session_id, "started_at": self.started_at,
                "elapsed_s": round(time.monotonic() - self.started, 1), "interval_s": self.interval,
                "mode": "run" if self.command else "watch", "label": self._label(),
                "folder": Path(self.cwd).name, "platform": sys.platform,
            },
            "app": {"pid": self.child.pid, "running": self.child.returncode is None,
                    "exit_code": self.child.returncode, "processes": s.app_procs if s else 0} if self.child else None,
            "measured": measured,
            "power_w": {"app": s.app_w if s else None, "user": s.user_w if s else None,
                        "system": s.system_w if s else None, "system_age_s": s.system_age if s else None},
            "energy_j": {"app": s.app_j if s and self.child else None, "user": s.user_j if s else 0.0,
                         "system": s.system_j if s else 0.0},
            "readable": {"processes": s.readable if s else 0, "total": s.total if s else 0},
            "impact": {"scope": scope, "scope_label": self._label() if self.child else "your processes",
                       "joules": joules, "co2_g": impact.co2_grams(joules), "water_ml": impact.water_ml(joules)}
                      if measured else None,
            "system_impact": {"joules": s.system_j, "co2_g": impact.co2_grams(s.system_j),
                              "water_ml": impact.water_ml(s.system_j)} if s and s.system_j else None,
            "factors": {"grid": self._factor(impact.GRID), "water": self._factor(impact.WATER)},
            "timeline": list(self.timeline),
            "app_breakdown": self._breakdown(),
            "processes": [{"pid": p.pid, "name": p.name, "cpu": round(p.cpu, 1), "watts": p.watts,
                           "joules": p.joules, "rss": p.rss, "in_app": p.in_app} for p in (s.procs[:25] if s else [])],
            "scan": {
                "state": "running" if self.scanning else "done" if self.report else "off" if not self.do_scan else "starting",
                "owners": owners,
                "findings_total": len(findings),
                "by_confidence": {c: n for c, n in Counter(f["confidence"] for f in findings).items()},
                "checks_total": self.report["summary"]["checks_total"] if self.report else None,
                "top": [{"check_id": f["check_id"], "pattern": f["pattern"], "confidence": f["confidence"],
                         "file": f["file"], "line": f["line"], "summary": f["summary"]} for f in findings[:8]],
            },
        }

    def _breakdown(self) -> list:
        """Joules per executable for the launched command, over the session (processes seen in samples).

        Grouped by the first word of the label, so a pool of `node <worker args>` processes is one row."""
        totals = Counter()
        counts = Counter()
        for (_, label), joules in self.app_energy.items():
            name = label.split()[0] if label.strip() else "?"
            totals[name] += joules
            counts[name] += 1
        return [{"name": name, "joules": joules, "processes": counts[name]} for name, joules in totals.most_common(12)]

    def publish(self):
        if self.server is not None:
            self.live.update(self.snapshot())

    def render_power(self, s):
        def line(label, watts, note):
            text = Text(f"{label:<22}", style="bold")
            text.append(f"{watts:7.2f} W  " if watts is not None else "      — W  ",
                        style=ACCENT if watts is not None else DIM)
            text.append(note, style=DIM)
            return text

        if self.child:
            state = "running" if self.child.returncode is None else f"exited {self.child.returncode}"
            self.query_one("#pw-app", Static).update(
                line(self._label()[:21], s.app_w, f"{s.app_procs} procs · {state}"))
        self.query_one("#pw-user", Static).update(
            line("your processes", s.user_w, f"{s.readable}/{s.total} readable"))
        age = f"SMC · updated {s.system_age:.0f}s ago" if s.system_age is not None else "no SMC telemetry"
        self.query_one("#pw-mac", Static).update(line("whole Mac", s.system_w, age))

    def render_impact(self, s):
        joules = s.app_j if self.child else s.user_j
        scope = self._label() if self.child else "your processes"
        text = Text()
        text.append("scope   ", style=DIM)
        text.append(f"{scope}\n", style="bold")
        if not self.sampler.measured:
            text.append("energy  not measurable on this machine", style=DIM)
        else:
            rows = [("energy", impact.fmt_energy(joules), "measured"),
                    ("CO₂e", impact.fmt_mass(impact.co2_grams(joules)), f"× {impact.GRID.value} kg/kWh"),
                    ("water", impact.fmt_volume(impact.water_ml(joules)), f"× ~{impact.WATER.value} L/kWh")]
            for name, value, tag in rows:
                text.append(f"{name:<8}", style=DIM)
                text.append(f"{value:<12}", style=f"bold {ACCENT}")
                text.append(f"{tag}\n", style=DIM)
        if s.system_j:
            text.append(f"whole Mac {impact.fmt_energy(s.system_j)} · "
                        f"{impact.fmt_mass(impact.co2_grams(s.system_j))} CO₂e", style=DIM)
        self.query_one("#impact", Static).update(text)

    def render_procs(self, s):
        table = self.query_one("#procs", DataTable)
        row = table.cursor_row
        table.clear()
        for p in s.procs:
            name = Text(("▶ " if p.in_app else "") + p.name[:34], style=f"bold {ACCENT}" if p.in_app else "")
            joules = Text("—" if p.joules is None else f"{p.joules:,.0f}", style=DIM, justify="right")
            table.add_row(Text(str(p.pid), style=DIM), name, Text(f"{p.cpu:5.1f}", justify="right"),
                          _watts(p.watts), joules, Text(_mem(p.rss), style=DIM, justify="right"))
        if row and row < table.row_count:
            table.move_cursor(row=row, animate=False)

    def _label(self):
        if not self.command:
            return "watch"
        return " ".join([Path(self.command[0]).name, *self.command[1:]])

    def render_top(self):
        text = Text(" run-it ", style=f"bold #0a0d0b on {ACCENT}")
        text.append(f"  {self._short(self.cwd)}  ", style="bold")
        if self.child:
            running = self.child.returncode is None
            text.append("▶ " if running else "■ ", style=ACCENT if running else WARN)
            text.append(f"{self._label()}  pid {self.child.pid}  "
                        f"{'running' if running else f'exited {self.child.returncode}'}", style="")
        elif self.start_error:
            text.append(f"✗ {self.start_error}", style=HOT)
        else:
            text.append("● watching", style=ACCENT)
        text.append(f"   {impact.fmt_duration(time.monotonic() - self.started)}", style=DIM)
        text.append(datetime.now().strftime("   %H:%M:%S"), style=DIM)
        self.query_one("#top", Static).update(text)

    def render_query(self):
        text = Text("⟳ ", style=ACCENT)
        s = self.last
        if s is not None:
            text.append(f"proc_pid_rusage ×{s.readable} · ioreg SMC · {s.query_ms:.0f} ms every {self.interval:g}s",
                        style=DIM)
        if self.scan_state:
            text.append("   scan ", style="bold #60a5fa")
            frame = SPINNER[self.tick % len(SPINNER)]
            for owner, state in sorted(self.scan_state.items()):
                if state == "running":
                    text.append(f"{frame} {owner}  ", style="#60a5fa")
                else:
                    status, seconds, count = state
                    mark, style = ("✓", ACCENT) if status == "ok" else ("✗", HOT)
                    text.append(f"{mark} {owner} {count}f {seconds}s  ", style=style)
        self.query_one("#query", Static).update(text)

    def _spin(self):
        self.tick += 1
        if self.scanning:
            self.render_query()

    @staticmethod
    def _short(path):
        home = str(Path.home())
        return "~" + path[len(home):] if path.startswith(home) else path

    # ── scanning ───────────────────────────────────────────────────────────
    def action_rescan(self):
        if self.scanning:
            return
        self.scanning = True
        self.findings.clear()
        self.query_one("#findings", DataTable).clear()
        self.scan_state = {}
        # A daemon thread, not a Textual worker: those join on exit, so `q` would wait for the scan.
        threading.Thread(target=self.scan_worker, daemon=True).start()

    def scan_worker(self):
        from run_it.scan import scan

        def emit(kind, data):
            try:
                self.call_from_thread(self.on_scan_event, kind, data)
            except RuntimeError:  # dashboard already closed
                pass
        try:
            scan(self.cwd, emit, self.owners)
        except Exception as error:  # a scanner crash must not take the live meters down
            emit("log", f"scan error: {type(error).__name__}: {error}")
        finally:
            try:
                self.call_from_thread(setattr, self, "scanning", False)
            except RuntimeError:
                pass

    def on_scan_event(self, kind, data):
        if kind == "log":
            self.log_line("scan", data)
        elif kind == "running":
            self.scan_state = {name.split(":")[0]: "running" for name in data}
            self.log_line("scan", "running detectors: " + ", ".join(data))
        elif kind == "adapter":
            entry, checks, found = data
            self.scan_state[entry["owner"]] = (entry["status"], entry["seconds"], len(found))
            done = sum(c["status"] == "completed" for c in checks)
            reason = f" · {entry['reason']}" if entry["reason"] else ""
            self.log_line("scan" if entry["status"] == "ok" else "warn",
                          f"owner {entry['owner']} {entry['status']} · {len(checks)} checks ({done} completed) · "
                          f"{len(found)} findings · {entry['seconds']}s{reason}")
            for finding in found:
                self.add_finding(finding)
            self.publish()
        elif kind == "done":
            self.report = data
            unavailable = data["summary"]["checks_by_status"].get("unavailable", 0)
            if unavailable:
                self.log_line("scan", f"{unavailable} checks need telemetry or artifacts a folder scan "
                                      f"cannot collect (unavailable, not passed)")
            self.publish()
        self.render_query()

    def add_finding(self, f):
        self.findings[f["id"]] = f
        where = f"{f['file']}:{f['line']}" if f["line"] else str(f["file"])
        self.query_one("#findings").border_title = f"findings · {len(self.findings)} · static (not measured)"
        self.query_one("#findings", DataTable).add_row(
            Text(f["confidence"][:4].upper(), style=CONF_STYLE[f["confidence"]]),
            Text(f["check_id"], style="bold"), Text(where if len(where) <= 40 else "…" + where[-39:], style="#93c5fd"), f["summary"][:80],
            key=f["id"])
        message = Text(f"{f['confidence']:<6} ", style=CONF_STYLE[f["confidence"]])
        message.append(f"{f['check_id']} ", style="bold")
        message.append(f"{where} ", style="#93c5fd")
        message.append(f"— {f['summary'][:100]}")
        self.log_line("find", message)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "findings" and event.row_key.value in self.findings:
            self.push_screen(FindingScreen(self.findings[event.row_key.value]))

    # ── quit ───────────────────────────────────────────────────────────────
    async def action_quit(self):
        if not self.stopping:
            self.stopping = True  # the sampler stops the child, records its final energy, then exits
