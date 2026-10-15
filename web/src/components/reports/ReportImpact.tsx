"use client";

import { Gauge, Leaf, Zap } from "lucide-react";
import type { CiFindingLink, Range } from "@/lib/scan-api";
import { ciOf, measurementsOf, safeUrl, type ReportModel } from "@/lib/report-model";
import { Panel, Tier, cap, fmt, formatDate, formatDuration, formatRange, plural } from "./parts";

/** "Sep 23": the window's first day, for one-line labels. */
const formatDay = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

const FACTOR_LABELS: Record<string, string> = {
  min_watts_per_vcpu: "Min watts per vCPU",
  max_watts_per_vcpu: "Max watts per vCPU",
  pue_low: "PUE, low end",
  pue_high: "PUE, high end",
  grid_low: "Grid factor, low end",
  grid_high: "Grid factor, high end",
  wue_onsite: "On-site WUE (cooling)",
  ewif_low: "Grid water factor, low end",
  ewif_high: "Grid water factor, high end",
};

/** Why a card has no figures: the CI reason when there is one, otherwise the repository-scan default. */
function notQuantifiedReason(model: ReportModel): string {
  const ci = ciOf(model);
  return ci && ci.status !== "not_collected" && ci.reason
    ? `${cap(ci.reason)}. Nothing is estimated in its place.`
    : "A repository scan has no telemetry, so no impact figures are estimated. See below.";
}

/** "after Sep 23" / "since Sep 15": the window's start, for one-line labels. */
function windowLabel(model: ReportModel): string {
  const w = ciOf(model)?.window;
  return w ? `${w.start_exclusive ? "after" : "since"} ${formatDay(w.start)}` : "in the window";
}

/** A range as a KPI value: the numbers large, the unit small, like the other cards' "72 / 130". */
function KpiRange({ range, unit, text, label }: { range: Range; unit: "Wh" | "gCO2e" | "mL"; text?: boolean; label?: string }) {
  const formatted = formatRange(range, unit);
  const split = formatted.indexOf(" ");
  // Long ranges ("1,200–5,800") step down to the cards' text size instead of overflowing a fifth-width card.
  const small = text || split > 9;
  return (
    <div className={`rp-kpi-value whitespace-nowrap${small ? " is-text" : ""}`}>
      {formatted.slice(0, split)}<small>{formatted.slice(split + 1)}{label && ` ${label}`}</small>
    </div>
  );
}

/** A range for a table cell: "–" when the report predates the field (absent), "not estimated" when it is null. */
const cell = (range: Range | null | undefined, unit: "Wh" | "gCO2e" | "mL") => (range === undefined ? "–" : formatRange(range, unit));

/** ", 0.27–1.8 g CO2e" style suffixes, only for fields this report has. */
const extra = (range: Range | null | undefined, unit: "gCO2e" | "mL", label = "") =>
  range === undefined ? "" : `, ${formatRange(range, unit)}${label}`;

/** Overview KPI: modelled energy of the measured CI runs, with the runner time it comes from. */
export function EnergyKpi({ model }: { model: ReportModel }) {
  const ci = ciOf(model);
  const measurements = measurementsOf(model);
  const m = ci?.status === "measured" ? ci.measured : undefined;
  const est = ci?.estimate;
  const partial = m && m.energy_covered_seconds < m.runner_seconds;
  return (
    <section className="rp-panel rp-kpi" aria-label="Energy">
      <div className="rp-kpi-top">
        <span className="rp-icon"><Zap size={18} strokeWidth={1.5} /></span>
        <div><h2>Energy</h2><p>{m ? "CI workload from public GitHub Actions history" : "Energy used by the workload"}</p></div>
      </div>
      {m && m.energy_wh ? (
        <>
          <KpiRange range={m.energy_wh} unit="Wh" />
          <p className="rp-kpi-foot">
            Modelled from {formatDuration(m.runner_seconds)} of runner time measured on {plural(m.runs, "run")}
            {partial && `; covers the ${formatDuration(m.energy_covered_seconds)} on runners with published hardware`}
          </p>
          {est && est.runs > 0 && est.energy_complete && (
            <p className="rp-kpi-foot">
              {fmt(est.runs)} of {plural(est.runs_total, "run")} {windowLabel(model)}: ≈ {formatRange(est.energy_wh, "Wh")} over {formatDuration(est.runner_seconds)}, estimated from {plural(est.measured_runs, "measured run")}
            </p>
          )}
        </>
      ) : m ? (
        <>
          <div className="rp-kpi-value is-text">Not estimated</div>
          <p className="rp-kpi-foot">{formatDuration(m.runner_seconds)} of runner time measured on {plural(m.runs, "run")}, on runners whose hardware is not published (macOS, self-hosted or custom).</p>
        </>
      ) : (
        <>
          <div className="rp-kpi-value is-text">{measurements.length ? plural(measurements.length, "measurement") : "Not quantified"}</div>
          <p className="rp-kpi-foot">{notQuantifiedReason(model)}</p>
        </>
      )}
    </section>
  );
}

/** Overview KPI: CO2e and water, both modelled from the energy under stated region assumptions. */
export function CarbonWaterKpi({ model }: { model: ReportModel }) {
  const ci = ciOf(model);
  const m = ci?.status === "measured" ? ci.measured : undefined;
  const est = ci?.estimate;
  return (
    <section className="rp-panel rp-kpi sm:col-span-2 xl:col-span-1" aria-label="Carbon and water">
      <div className="rp-kpi-top">
        <span className="rp-icon"><Leaf size={18} strokeWidth={1.5} /></span>
        <div><h2>Carbon &amp; water</h2><p>{m?.energy_wh ? "Modelled from the energy, Azure US regions assumed" : "Emissions and water"}</p></div>
      </div>
      {m && m.energy_wh && m.co2e_g ? (
        <>
          <div className="flex flex-col gap-2">
            <KpiRange range={m.co2e_g} unit="gCO2e" text />
            {m.water_ml && <KpiRange range={m.water_ml} unit="mL" text label="water" />}
          </div>
          <p className="rp-kpi-foot">
            {m.water_ml === undefined
              ? "Water is not in this report: it was scanned before water was added. Scan again to see it."
              : `Water: ${formatRange(m.water_ml_onsite, "mL")} used cooling the datacenter, ${formatRange(m.water_ml_offsite, "mL")} consumed at power plants`}
          </p>
          {est && est.runs > 0 && est.energy_complete && (
            <p className="rp-kpi-foot">
              {fmt(est.runs)} of {plural(est.runs_total, "run")} {windowLabel(model)}: ≈ {formatRange(est.co2e_g, "gCO2e")}{extra(est.water_ml, "mL", " water")}, estimated
            </p>
          )}
        </>
      ) : (
        <>
          <div className="rp-kpi-value is-text">{m ? "Not estimated" : "Not quantified"}</div>
          <p className="rp-kpi-foot">{m ? "No energy could be modelled for these runners, so neither can carbon or water." : notQuantifiedReason(model)}</p>
        </>
      )}
    </section>
  );
}

/** Full breakdown: what was measured, what was modelled from it, and how every figure was calculated. */
export function CiImpactPanel({ model }: { model: ReportModel }) {
  const ci = ciOf(model);
  if (!ci || ci.status === "not_collected") return null;
  const { window: w, measured: m, estimate: est, model: mdl } = ci;
  const source = safeUrl(ci.source);
  const hint = ci.status === "measured" ? "Measured runner time; modelled energy, carbon and water. Code findings are not priced here."
    : ci.reason ?? "Not measured";

  return (
    <Panel title="Measured CI workload" icon={<Gauge size={18} strokeWidth={1.5} />} hint={hint} labelledBy="ci-h">
      {ci.status !== "measured" ? (
        <p className="text-[12.5px] leading-relaxed text-[#c3cdd2]">
          {ci.reason ? `${cap(ci.reason)}.` : "CI history was not measured."} No runner time, energy, CO₂e or water is shown in its place.
        </p>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="rp-grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4">
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2"><p className="rp-section-label !mb-0">Measured</p><Tier tier="measured" /></div>
              <p className="font-mono text-xl text-[#f3f6f7]">{formatDuration(m?.runner_seconds)}</p>
              <p className="text-[11px] leading-relaxed text-[#93a0a6]">
                Runner time of {plural(m?.jobs ?? 0, "job")} in {plural(m?.runs ?? 0, "run")}, from each job&apos;s start and end time in the GitHub API.
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2"><p className="rp-section-label !mb-0">Energy</p><Tier tier="modelled" /></div>
              <p className="font-mono text-xl text-[#f3f6f7]">{formatRange(m?.energy_wh, "Wh")}</p>
              <p className="text-[11px] leading-relaxed text-[#93a0a6]">
                {m?.energy_wh
                  ? <>For the same measured runs{m.energy_covered_seconds < m.runner_seconds ? `; covers ${formatDuration(m.energy_covered_seconds)} on runners with published hardware` : ""}.</>
                  : "The measured jobs ran on runners whose hardware is not published (macOS, self-hosted or custom), so no energy is estimated."}
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2"><p className="rp-section-label !mb-0">Carbon &amp; water</p><Tier tier="modelled" /></div>
              <p className="font-mono text-xl text-[#f3f6f7]">{formatRange(m?.co2e_g, "gCO2e")}</p>
              {m?.water_ml !== undefined && <p className="font-mono text-[15px] text-[#dfe6e9]">{formatRange(m.water_ml, "mL")} water</p>}
              <p className="text-[11px] leading-relaxed text-[#93a0a6]">
                {m?.water_ml === undefined ? "Water is not in this report: it was scanned before water was added."
                  : m.water_ml
                    ? <>Water: {formatRange(m.water_ml_onsite, "mL")} used cooling the datacenter, {formatRange(m.water_ml_offsite, "mL")} consumed at power plants.</>
                    : "Follows the energy: not estimated without it."}
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2"><p className="rp-section-label !mb-0">Whole window</p><Tier tier="estimated" /></div>
              <p className="font-mono text-xl text-[#f3f6f7]">{est && est.runs ? formatDuration(est.runner_seconds) : "not estimated"}</p>
              <p className="text-[11px] leading-relaxed text-[#93a0a6]">
                {est && est.runs
                  ? <>{fmt(est.runs)} of {plural(est.runs_total, "run")}, scaled from {plural(est.measured_runs, "measured run")}: ≈ {formatRange(est.energy_wh, "Wh")}, {formatRange(est.co2e_g, "gCO2e")}{extra(est.water_ml, "mL", " water")}{!est.energy_complete && " (energy only for workflows whose measured runs all had published hardware)"}{est.runs_without_sample > 0 && `; ${plural(est.runs_without_sample, "run")} in workflows with no measured run are left out`}.</>
                  : "No workflow had a measured run to scale."}
              </p>
            </div>
          </div>

          {w && (
            <p className="text-[11.5px] leading-relaxed text-[#a9b5bb]">
              Window: {w.start_exclusive ? "after" : "from"} {formatDate(w.start)} to {formatDate(w.end)} · {plural(w.runs_listed, "run")} listed
              {!w.complete && ` (the newest of ${fmt(w.runs_total_since)} in the last ${w.days} days)`}
              {" "}· {plural(ci.requests ?? 0, "unauthenticated GitHub API request")}
            </p>
          )}

          {ci.workflows && ci.workflows.length > 0 && (
            <div className="rp-table-wrap">
              <table className="rp-table">
                <thead><tr><th>Workflow</th><th>Runs</th><th>Measured runs</th><th>Per run</th><th>Window (estimated)</th><th>Energy (estimated)</th><th>CO₂e (estimated)</th><th>Water (estimated)</th></tr></thead>
                <tbody>
                  {ci.workflows.map((row) => (
                    <tr key={row.path}>
                      <td><span className="rp-strong">{row.name}</span><div className="font-mono text-[10.5px] text-[#86939a]">{row.path}</div></td>
                      <td className="whitespace-nowrap font-mono">
                        {fmt(row.executed_runs)}
                        {row.awaiting_approval_runs > 0 && <div className="font-sans text-[10.5px] text-[#86939a]">+{fmt(row.awaiting_approval_runs)} awaiting approval</div>}
                        {row.runs > row.completed_runs && <div className="font-sans text-[10.5px] text-[#86939a]">+{fmt(row.runs - row.completed_runs)} still running</div>}
                      </td>
                      <td className="font-mono">{fmt(row.measured_runs)}</td>
                      <td className="whitespace-nowrap font-mono">{row.mean_seconds_per_run === null ? "–" : formatDuration(row.mean_seconds_per_run)}</td>
                      <td className="whitespace-nowrap font-mono">{row.estimated_seconds === null ? "not estimated" : formatDuration(row.estimated_seconds)}</td>
                      <td className="whitespace-nowrap font-mono">{row.estimated_seconds === null ? "–" : formatRange(row.estimated_energy_wh, "Wh")}</td>
                      <td className="whitespace-nowrap font-mono">{row.estimated_seconds === null ? "–" : cell(row.estimated_co2e_g, "gCO2e")}</td>
                      <td className="whitespace-nowrap font-mono">{row.estimated_seconds === null ? "–" : cell(row.estimated_water_ml, "mL")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {ci.measured_runs && ci.measured_runs.length > 0 && (
            <details>
              <summary className="cursor-pointer select-none text-[12px] text-[#a9b5bb] hover:text-white">The {plural(ci.measured_runs.length, "measured run")}</summary>
              <div className="rp-table-wrap mt-2.5">
                <table className="rp-table">
                  <thead><tr><th>Run</th><th>Workflow</th><th>Event</th><th>Conclusion</th><th>Runner time</th><th>Energy (modelled)</th><th>CO₂e (modelled)</th><th>Water (modelled)</th></tr></thead>
                  <tbody>
                    {ci.measured_runs.map((r) => {
                      const url = safeUrl(r.url);
                      return (
                        <tr key={r.id}>
                          <td className="font-mono">{url ? <a className="rp-link" href={url} target="_blank" rel="noreferrer">#{r.id}</a> : `#${r.id}`}<div className="font-sans text-[10.5px] text-[#86939a]">{formatDate(r.created_at)}</div></td>
                          <td>{r.workflow}</td>
                          <td>{r.event}</td>
                          <td>{r.conclusion ?? "–"}{r.attempts > 1 && <div className="text-[10.5px] text-[#86939a]">{plural(r.attempts, "attempt")}</div>}</td>
                          <td className="whitespace-nowrap font-mono">{formatDuration(r.runner_seconds)}</td>
                          <td className="whitespace-nowrap font-mono">{formatRange(r.energy_wh, "Wh")}</td>
                          <td className="whitespace-nowrap font-mono">{cell(r.co2e_g, "gCO2e")}</td>
                          <td className="whitespace-nowrap font-mono">{cell(r.water_ml, "mL")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          )}

          {mdl && (
            <details>
              <summary className="cursor-pointer select-none text-[12px] text-[#a9b5bb] hover:text-white">How this is calculated</summary>
              <div className="mt-3 flex flex-col gap-4 rounded-[10px] border border-[#ffffff12] bg-[#ffffff04] p-3.5">
                <dl className="rp-dl">
                  <dt>Source</dt>
                  <dd>{source ? <a className="rp-link break-all" href={source} target="_blank" rel="noreferrer">{source}</a> : "GitHub Actions REST API"}<small>Public data, read without credentials. Runs listed in the window; job timings for a sample spread across workflows.</small></dd>
                  <dt>Runner time</dt>
                  <dd>Sum of each job&apos;s completed_at − started_at. Jobs GitHub re-lists from an earlier attempt are counted once; queue time is excluded.</dd>
                  <dt>Runner hardware</dt>
                  <dd>{safeUrl(mdl.runner_specs_source) ? <a className="rp-link" href={mdl.runner_specs_source} target="_blank" rel="noreferrer">GitHub-hosted runners reference</a> : "GitHub-hosted runners reference"}<small>vCPUs per runner label, for standard runners on public repositories.</small></dd>
                  <dt>Energy</dt>
                  <dd className="font-mono text-[11.5px]">{mdl.energy_formula}</dd>
                  <dt>CO₂e</dt>
                  <dd><span className="font-mono text-[11.5px]">{mdl.co2e_formula}</span><small>{mdl.grid_assumption}</small></dd>
                  {mdl.water_formula && (
                    <>
                      <dt>Water</dt>
                      <dd>
                        <span className="font-mono text-[11.5px]">{mdl.water_formula}</span>
                        <small>
                          {mdl.water_assumption}
                          {safeUrl(mdl.water_method_source) && <> Method: <a className="rp-link" href={mdl.water_method_source} target="_blank" rel="noreferrer">Li et al., Making AI Less &quot;Thirsty&quot;</a>.</>}
                        </small>
                      </dd>
                    </>
                  )}
                  {est && <><dt>Whole window</dt><dd>{est.method}</dd></>}
                </dl>
                <div className="rp-table-wrap">
                  <table className="rp-table">
                    <thead><tr><th>Factor</th><th>Value</th><th>Basis</th></tr></thead>
                    <tbody>
                      {Object.entries(mdl.factors).map(([key, f]) => {
                        const link = safeUrl(f.source);
                        return (
                          <tr key={key}>
                            <td className="whitespace-nowrap">{FACTOR_LABELS[key] ?? key}</td>
                            <td className="whitespace-nowrap font-mono">{f.value} {f.unit === "ratio" ? "" : f.unit}</td>
                            <td>{f.note}{link && <> · <a className="rp-link" href={link} target="_blank" rel="noreferrer">source</a></>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div>
                  <p className="rp-section-label">Not included</p>
                  <ul className="list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-[#93a0a6]">{mdl.exclusions.map((x) => <li key={x}>{x}</li>)}</ul>
                </div>
                {ci.limitations && ci.limitations.length > 0 && (
                  <div>
                    <p className="rp-section-label">Limits of this scan</p>
                    <ul className="list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-[#93a0a6]">{ci.limitations.map((x) => <li key={x}>{x}</li>)}</ul>
                  </div>
                )}
              </div>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}

/** A finding's measured CI context: shown only where the report matched it exactly, otherwise "not priced". */
export function FindingImpact({ link, isCi, ciMeasured }: { link: CiFindingLink | null; isCi: boolean; ciMeasured: boolean }) {
  if (!ciMeasured) return null;
  if (!link) {
    return (
      <section>
        <p className="rp-section-label">Impact</p>
        <p className="text-[12px] leading-relaxed text-[#93a0a6]">
          Not priced. {isCi ? "This finding could not be linked to a measured workflow." : "Its runtime cost is not measured by a repository scan; only CI workload is."}
        </p>
      </section>
    );
  }
  const { job, step, step_in_window: spent } = link;
  return (
    <section className="flex flex-col gap-3 rounded-[12px] border border-[#ffffff14] bg-[#ffffff05] p-3.5">
      <div className="flex flex-wrap items-center gap-2"><p className="rp-section-label !mb-0">Measured CI context</p><Tier tier="measured" /></div>
      <dl className="rp-dl">
        <dt>Workflow</dt>
        <dd>
          <span className="font-mono text-[11.5px]">{link.workflow}</span>
          <small>
            {link.match === "none" || !link.executed_runs ? cap(link.note ?? "No runs in the window") + "."
              : `${plural(link.executed_runs, "run")} in the window, ${fmt(link.measured_runs)} measured${link.mean_seconds_per_run !== null ? `, ${formatDuration(link.mean_seconds_per_run)} runner time per run` : ""}.`}
          </small>
        </dd>
        {job && <><dt>Job</dt><dd><span className="font-mono text-[11.5px]">{job.name}</span><small>{formatDuration(job.mean_seconds_per_run)} per run across {plural(job.executions, "job execution")} in {plural(job.measured_runs, "measured run")}.</small></dd></>}
        {step && <><dt>Step</dt><dd><span className="font-mono text-[11.5px]">{step.name}</span><small>{formatDuration(step.mean_seconds_per_run)} per run{step.energy_wh_per_run && ` · ≈ ${formatRange(step.energy_wh_per_run, "Wh")}${extra(step.co2e_g_per_run, "gCO2e")}${extra(step.water_ml_per_run, "mL", " water")}, modelled`}.</small></dd></>}
      </dl>
      {spent && (
        <div className="flex flex-col gap-1.5 border-t border-[#ffffff0f] pt-3">
          <div className="flex items-center gap-2"><p className="rp-section-label !mb-0">What this step spent in the window</p><Tier tier="estimated" /></div>
          <p className="text-[12.5px] leading-relaxed text-[#dfe6e9]">
            {formatDuration(spent.seconds_per_run)} per run × {plural(spent.runs, "run")} = <b className="font-mono">{formatDuration(spent.seconds_in_window)}</b> of runner time
            {spent.energy_wh_in_window && <> · ≈ {formatRange(spent.energy_wh_in_window, "Wh")}{extra(spent.co2e_g_in_window, "gCO2e")}{extra(spent.water_ml_in_window, "mL", " water")}, modelled</>}.
          </p>
          <p className="text-[11px] leading-relaxed text-[#93a0a6]">
            Per-run time is the mean of {plural(spent.measured_runs, "measured run")}. This is what the step cost, not a promised saving. {spent.targets}
          </p>
        </div>
      )}
      {link.note && link.match !== "none" && link.executed_runs > 0 && <p className="text-[11px] leading-relaxed text-[#93a0a6]">{cap(link.note)}.</p>}
    </section>
  );
}
