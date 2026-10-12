"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Activity, Droplets, Factory, Gauge, Loader2, ScanSearch, Terminal, Zap } from "lucide-react";
import { CONFIDENCE_COLOR, ConfidenceDot, Panel, cap, fmt } from "@/components/reports/parts";
import {
  DEFAULT_PORT,
  fmtDuration,
  fmtEnergy,
  fmtMass,
  fmtVolume,
  fmtWatts,
  useRunItLink,
  type RunItState,
} from "@/lib/run-it";
import PowerChart from "./PowerChart";

function Tag({ kind }: { kind: "measured" | "modelled" | "coarse" | "static" }) {
  const tone = kind === "measured" ? "text-[#8fd1a8] border-[#8fd1a840]" : kind === "modelled" ? "text-[#f7c062] border-[#f7c06240]" : "text-[#a9b5bb] border-[#ffffff1f]";
  return <span className={`rounded-full border px-2 py-[1px] text-[9.5px] uppercase tracking-[0.12em] ${tone}`}>{kind}</span>;
}

function Kpi({ icon, title, hint, value, foot, tag }: { icon: ReactNode; title: string; hint: string; value: string; foot: ReactNode; tag: "measured" | "modelled" }) {
  return (
    <section className="rp-panel rp-kpi" aria-label={title}>
      <div className="rp-kpi-top">
        <span className="rp-icon">{icon}</span>
        <div className="min-w-0"><h2>{title}</h2><p>{hint}</p></div>
        <span className="ml-auto self-start"><Tag kind={tag} /></span>
      </div>
      <div className="rp-kpi-value">{value}</div>
      <div className="rp-kpi-foot">{foot}</div>
    </section>
  );
}

function Help({ port }: { port: number }) {
  return (
    <Panel title="Connect run-it" hint="run-it measures your machine; this page only displays what it serves on localhost" icon={<Terminal size={18} strokeWidth={1.5} />} labelledBy="connect-h">
      <ol className="flex flex-col gap-4 text-[12.5px] leading-relaxed text-[#c3cdd2]">
        <li>
          <p className="rp-section-label">1 · Set up once, from the repository root</p>
          <pre className="rp-prompt">{`python3 -m venv .venv
.venv/bin/pip install -r shared/contracts/requirements.txt -r run_it/requirements.txt -r detectors/owner-c/requirements.txt
ln -s "$PWD/scripts/run-it" ~/.local/bin/run-it`}</pre>
        </li>
        <li>
          <p className="rp-section-label">2 · Run it in any project</p>
          <pre className="rp-prompt">{`cd ~/some-project
run-it                  # watch: your processes while you work
run-it npm start        # run: measure this command's whole process tree`}</pre>
        </li>
        <li>
          <p className="rp-section-label">3 · Keep this page open</p>
          <p>It connects to <span className="font-mono text-[#e7edf0]">http://127.0.0.1:{port}/state</span> on its own within a few seconds, and updates every sample.</p>
        </li>
      </ol>
      <ul className="mt-4 flex flex-col gap-1.5 border-t border-[#ffffff0d] pt-3 text-[11.5px] leading-relaxed text-[#93a0a6]">
        <li>Energy is measured on macOS only (kernel energy counters, no sudo). Elsewhere the meters show “—”.</li>
        <li>Chrome and Firefox let this site read localhost; Chrome may ask to allow local network access. Safari blocks it, so use another browser or the local dev site (localhost:3000).</li>
        <li>run-it answers only this site and local dev servers. Add other origins with <span className="font-mono">RUN_IT_ALLOW_ORIGINS</span>; use <span className="font-mono">run-it --port N</span> with <span className="font-mono">?port=N</span> here.</li>
      </ul>
    </Panel>
  );
}

/** Command lines can be long; headings and captions show a short form, with the full text on hover. */
const short = (text: string, max = 44) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

function Dashboard({ state, source }: { state: RunItState; source: "live" | "stopped" | "sample" }) {
  const impact = state.impact;
  const scope = impact?.scope ?? (state.app ? "app" : "user");
  const scopeLabel = scope === "app" ? short(state.session.label) : "Your processes";
  const finished = Boolean(state.app && !state.app.running) || source !== "live";
  const average = impact && state.session.elapsed_s > 0 ? impact.joules / state.session.elapsed_s : null;
  const powerNow = scope === "app" ? state.power_w.app : state.power_w.user;
  const peak = Math.max(0, ...state.timeline.map((p) => (scope === "app" ? p.app : p.user) ?? 0));
  const grid = state.factors.grid, water = state.factors.water;
  const breakdown = state.app_breakdown?.length
    ? state.app_breakdown.map((b) => ({ key: b.name, title: b.name, sub: b.processes > 1 ? `${b.processes} processes` : null, value: b.joules }))
    : state.processes.filter((p) => p.joules !== null).sort((a, b) => (b.joules ?? 0) - (a.joules ?? 0)).slice(0, 8)
      .map((p) => ({ key: String(p.pid), title: p.name, sub: `pid ${p.pid}`, value: p.joules ?? 0 }));
  const maxJ = Math.max(1e-9, ...breakdown.map((b) => b.value));
  const owners = Object.entries(state.scan.owners);

  return (
    <div className="flex flex-col gap-3.5">
      {!state.measured && (
        <div className="rp-notice is-warn"><span><b>Energy is not measurable here.</b> run-it reads macOS kernel energy counters; on {state.session.platform} the meters show “—”. The process table and scan still work.</span></div>
      )}

      <div className="rp-grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        {finished ? (
          <Kpi icon={<Gauge size={18} strokeWidth={1.5} />} title="Peak power" hint={scopeLabel}
            value={fmtWatts(peak)} tag="measured"
            foot={<>Average {fmtWatts(average)} over {fmtDuration(state.session.elapsed_s)} · {state.readable.processes}/{state.readable.total} processes readable</>} />
        ) : (
          <Kpi icon={<Gauge size={18} strokeWidth={1.5} />} title="Power now" hint={scopeLabel}
            value={fmtWatts(powerNow)} tag="measured"
            foot={<>Peak {fmtWatts(peak)} · {state.readable.processes}/{state.readable.total} processes readable</>} />
        )}
        <Kpi icon={<Zap size={18} strokeWidth={1.5} />} title="Energy" hint={`${scopeLabel}, this session`}
          value={impact ? fmtEnergy(impact.joules) : "—"} tag="measured"
          foot={<>{impact ? `${(impact.joules / 3600).toFixed(4)} Wh` : "Not measured on this machine"}{state.system_impact && ` · whole Mac ${fmtEnergy(state.system_impact.joules)}`}</>} />
        <Kpi icon={<Factory size={18} strokeWidth={1.5} />} title="Carbon (CO₂e)" hint="Energy × grid intensity"
          value={impact ? fmtMass(impact.co2_g) : "—"} tag="modelled"
          foot={<>× {grid.value} {grid.unit}{grid.override ? " (override)" : " · India grid average"}</>} />
        <Kpi icon={<Droplets size={18} strokeWidth={1.5} />} title="Water" hint="Energy × generation water"
          value={impact ? fmtVolume(impact.water_ml) : "—"} tag="modelled"
          foot={<>× ~{water.value} {water.unit}{water.override ? " (override)" : " · power-plant water only"}</>} />
      </div>

      <div className="rp-grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Panel title="Power over time" hint={`Every ${state.session.interval_s}s from kernel energy counters; the whole-Mac line is SMC telemetry and refreshes slowly`} icon={<Activity size={18} strokeWidth={1.5} />} labelledBy="power-h">
          <PowerChart points={state.timeline} scope={scope} scopeLabel={scopeLabel} />
        </Panel>
        <Panel title={state.app ? "Where the energy went" : "Top processes by energy"} hint={state.app ? `Processes of ${short(state.session.label, 36)}, grouped by executable` : "Your readable processes, this session"} labelledBy="breakdown-h">
          {breakdown.length === 0 ? <p className="text-[12px] text-[#93a0a6]">No per-process energy yet.</p> : (
            <ul className="flex flex-col">
              {breakdown.map((b) => (
                <li key={b.key} className="rp-bar-row">
                  <span className="rp-bar-label"><b className="!font-sans !text-[12px]">{b.title}</b>{b.sub && <span>{b.sub}</span>}</span>
                  <span className="rp-meter" aria-hidden="true"><i style={{ width: `${Math.max(2, (b.value / maxJ) * 100)}%` }} /></span>
                  <span className="rp-bar-value">{fmtEnergy(b.value)}</span>
                </li>
              ))}
            </ul>
          )}
          {state.app && <p className="mt-3 text-[11px] leading-relaxed text-[#86939a]">Processes that start and exit between two samples still count in the total above, but cannot be named here.</p>}
        </Panel>
      </div>

      {/* The recorded sample keeps no process table (other people's processes are private); the breakdown covers it. */}
      {source !== "sample" && <Panel title="Processes" hint={source === "stopped" ? "As of run-it's last reading" : "Watts from each process's kernel energy counter, refreshed every sample"} labelledBy="procs-h" bodyClassName="rp-table-wrap px-3 pb-2 pt-1">
        {state.processes.length === 0 ? <p className="rp-empty">No processes in this reading.</p> : (
          <table className="rp-table min-w-[44rem]">
            <thead><tr><th>Process</th><th className="text-right">PID</th><th className="text-right">CPU</th><th>Power</th><th className="text-right">Energy</th><th className="text-right">Memory</th></tr></thead>
            <tbody>
              {state.processes.slice(0, 15).map((p) => {
                const share = p.watts && peak ? Math.min(1, p.watts / Math.max(peak, ...state.processes.map((q) => q.watts ?? 0))) : 0;
                return (
                  <tr key={p.pid}>
                    <td className="max-w-[22rem]"><div className="flex items-center gap-2">{p.in_app && <span className="rp-pill is-new">App</span>}<span className="truncate text-[#e7edf0]" title={p.name}>{p.name}</span></div></td>
                    <td className="text-right font-mono">{p.pid}</td>
                    <td className="text-right font-mono">{p.cpu.toFixed(1)}%</td>
                    <td className="min-w-[10rem]"><div className="flex items-center gap-3"><span className="w-14 text-right font-mono">{fmtWatts(p.watts)}</span><span className="rp-meter w-20" aria-hidden="true"><i style={{ width: `${share * 100}%` }} /></span></div></td>
                    <td className="text-right font-mono">{p.joules === null ? "—" : fmtEnergy(p.joules)}</td>
                    <td className="text-right font-mono">{p.rss >= 2 ** 30 ? `${(p.rss / 2 ** 30).toFixed(1)} GB` : `${Math.round(p.rss / 2 ** 20)} MB`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>}

      <div className="rp-grid grid-cols-1 xl:grid-cols-2">
        <Panel title="Where every number comes from" hint="Measured values are read from the machine; modelled values multiply them by a cited factor" labelledBy="prov-h" bodyClassName="rp-table-wrap px-3 pb-3 pt-1">
          <table className="rp-table">
            <thead><tr><th>Number</th><th>Source</th><th>Tag</th></tr></thead>
            <tbody>
              <tr><td className="rp-strong">Per-process W and J</td><td>Kernel energy counter (<span className="font-mono">proc_pid_rusage → ri_energy_nj</span>), sampled every {state.session.interval_s}s, current user&apos;s processes only</td><td><Tag kind="measured" /></td></tr>
              {state.app && <tr><td className="rp-strong">Command W and J</td><td>Sum over the command&apos;s process group, including processes that exit between samples</td><td><Tag kind="measured" /></td></tr>}
              <tr><td className="rp-strong">Whole Mac W</td><td>SMC telemetry via <span className="font-mono">ioreg</span>{state.power_w.system_age_s !== null ? `, last changed ${Math.round(state.power_w.system_age_s)}s before this reading` : ", not available on this machine"}</td><td><Tag kind="coarse" /></td></tr>
              <tr><td className="rp-strong">CO₂e</td><td>Energy × {grid.value} {grid.unit}: {grid.source}</td><td><Tag kind="modelled" /></td></tr>
              <tr><td className="rp-strong">Water</td><td>Energy × ~{water.value} {water.unit}: {water.source}</td><td><Tag kind="modelled" /></td></tr>
              <tr><td className="rp-strong">Not included</td><td>Embodied hardware emissions (the SCI <span className="font-mono">M</span> term) and other users&apos; processes (root daemons, WindowServer)</td><td>–</td></tr>
            </tbody>
          </table>
        </Panel>

        <Panel title="Findings in this folder" hint="run-it's static scan; it never converts a finding into grams" icon={<ScanSearch size={18} strokeWidth={1.5} />} labelledBy="scan-h"
          right={<span className="text-[11px] text-[#93a0a6]">{state.scan.state === "running" ? <span className="inline-flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" />scanning</span> : state.scan.state === "off" ? "scan off (--no-scan)" : state.scan.state === "done" ? "done" : "starting"}</span>}>
          {owners.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-2">
              {owners.map(([owner, s]) => (
                <span key={owner} className="rp-pill">
                  Owner {owner} · {s === "running" ? "running" : `${s.status === "ok" ? "✓" : s.status} ${s.findings} in ${s.seconds}s`}
                </span>
              ))}
            </div>
          )}
          <div className="mb-3 flex items-baseline gap-4">
            <span className="font-mono text-[26px] tracking-[-0.04em] text-[#f3f6f7]">{fmt(state.scan.findings_total)}</span>
            <span className="rp-legend">
              {(["high", "medium", "low"] as const).map((c) => (
                <span key={c}><span className="rp-dot" style={{ background: CONFIDENCE_COLOR[c] }} aria-hidden="true" />{cap(c)} <b>{fmt(state.scan.by_confidence[c] ?? 0)}</b></span>
              ))}
            </span>
          </div>
          {state.scan.top.length > 0 ? (
            <ul className="flex flex-col divide-y divide-[#ffffff0a]">
              {state.scan.top.slice(0, 5).map((f, i) => (
                <li key={i} className="py-2.5">
                  <div className="flex items-center gap-2 text-[11px]"><ConfidenceDot confidence={f.confidence} /><span className="font-mono text-[#e7edf0]">{f.check_id}</span><span className="truncate font-mono text-[#86939a]">{f.file}{f.line ? `:${f.line}` : ""}</span></div>
                  <p className="mt-1 text-[12px] leading-snug text-[#b9c4c9]">{f.summary}</p>
                </li>
              ))}
            </ul>
          ) : <p className="text-[12px] text-[#93a0a6]">{state.scan.state === "done" ? "No findings in this folder." : "Findings appear here as each detector finishes."}</p>}
          <p className="mt-3 text-[11.5px] text-[#93a0a6]">Scanned a GitHub repository? Its full report with evidence and fix prompts is in <Link href="/reports" className="rp-link">Carbon reports</Link>.</p>
        </Panel>
      </div>
    </div>
  );
}

export default function EnvironmentClient() {
  const params = useSearchParams();
  const requested = Number(params.get("port"));
  const port = Number.isInteger(requested) && requested > 0 && requested < 65536 ? requested : DEFAULT_PORT;
  const link = useRunItLink(port);
  const [showHelp, setShowHelp] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  const source: "live" | "stopped" | "sample" = link.status === "live" ? "live" : link.status === "stopped" ? "stopped" : "sample";
  const state = source === "sample" ? link.sample : link.live;
  useEffect(() => { document.title = "Environment · EcoAudit"; }, []);

  const pill = source === "live" ? <span className="rp-source is-live"><i />Live · run-it connected</span>
    : source === "stopped" ? <span className="rp-source is-warn"><i />run-it stopped</span>
    : <span className="rp-source"><i />Recorded session</span>;

  return (
    <div className="rp-page">
      <header className="rp-header">
        <div className="rp-topbar">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/" className="rp-brand">EcoAudit</Link>
            <span className="rp-eyebrow hidden sm:inline">/ Environment</span>
          </div>
          <div className="rp-actions">
            {pill}
            <button type="button" className="rp-btn" aria-expanded={showHelp} onClick={() => setShowHelp(!showHelp)}>
              <Terminal size={14} /><span className="rp-btn-label">{showHelp ? "Hide setup" : "Connect run-it"}</span>
            </button>
          </div>
        </div>
      </header>
      <main className="rp-content">
        <div className="rp-heading">
          <div className="min-w-0">
            <div className="rp-eyebrow">Live footprint · measured on the machine running run-it</div>
            <div className="rp-title">
              <h1 className="line-clamp-2" title={state?.session.label}>{state ? (state.session.mode === "run" ? short(state.session.label, 80) : "Watching your processes") : "Environmental footprint"}</h1>
              {state && <span className="rp-source"><i />{state.session.mode === "run" ? "run mode" : "watch mode"}</span>}
            </div>
            {state && (
              <div className="rp-meta">
                <span>Folder <b className="font-mono">{state.session.folder}</b></span>
                <span>Duration <b>{fmtDuration(state.session.elapsed_s)}</b></span>
                {state.app && <span>Command <b>{state.app.running ? `running · pid ${state.app.pid}` : `exited with code ${state.app.exit_code}`}</b></span>}
                <span>Started <b>{new Date(state.session.started_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</b></span>
              </div>
            )}
          </div>
        </div>

        <div className="mb-3.5 flex flex-col gap-2.5">
          {source === "sample" && (
            <div className="rp-notice">
              <span><b>Recorded session.</b> run-it is not running on this machine, so this is a real session recorded on a Mac: <span className="font-mono">run-it npm run build</span> on this site&apos;s web app. Start run-it and this page switches to your live readings.</span>
              {!showHelp && <button type="button" className="rp-btn rp-btn-sm rp-btn-primary" onClick={() => setShowHelp(true)}>Connect run-it</button>}
            </div>
          )}
          {source === "stopped" && link.seenAt && (
            <div className="rp-notice is-warn"><span><b>run-it stopped.</b> Showing its last reading, {fmtDuration((now - link.seenAt) / 1000)} ago. This page reconnects when run-it starts again.</span></div>
          )}
          {link.sampleError && source === "sample" && <div className="rp-notice is-error"><span>Could not load the recorded session: {link.sampleError}</span></div>}
        </div>

        {showHelp && <div className="mb-3.5"><Help port={port} /></div>}
        {state ? <Dashboard state={state} source={source} /> : (
          <div className="flex flex-col gap-3.5 pt-2" aria-busy="true">
            <div role="status" className="rp-notice"><span className="flex items-center gap-3"><Loader2 size={15} className="animate-spin" aria-hidden="true" />Looking for run-it on 127.0.0.1:{port}…</span></div>
            <div className="rp-grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4" aria-hidden="true">{[0, 1, 2, 3].map((i) => <div key={i} className="rp-skeleton h-36" />)}</div>
            <div className="rp-skeleton h-72" aria-hidden="true" />
          </div>
        )}
      </main>
      <footer className="rp-content rp-footer">
        <span>EcoAudit · run-it live footprint</span>
        <span>Readings stay on your machine: this page reads run-it over localhost and sends nothing anywhere.</span>
      </footer>
    </div>
  );
}
