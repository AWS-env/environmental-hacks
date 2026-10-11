"use client";

import { useState } from "react";
import type { Comparison } from "@/lib/report-store";
import {
  CONFIDENCES,
  STATUSES,
  findingsByCheck,
  measurementsOf,
  safeUrl,
  type ReportModel,
  type ReportTotals,
} from "@/lib/report-model";
import { CONFIDENCE_SWATCH, STATUS_META, SectionTitle, StatusIcon, Tile, cap, card, fmt, formatDate, plural } from "./parts";

const TOP_CHECKS = 12;

function FindingsByCheck({ model, onPick }: { model: ReportModel; onPick: (checkId: string) => void }) {
  const rows = findingsByCheck(model);
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, TOP_CHECKS);
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <section className={`${card} p-5`} aria-labelledby="by-check-h">
      <h2 id="by-check-h" className="text-base font-bold text-ink">Findings by check</h2>
      <p className="mt-0.5 text-xs text-ink-3">Select a check to see its findings.</p>
      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-ink-3">No findings in the evaluated scope.</p>
      ) : (
        <ul className="mt-4 space-y-0.5">
          {shown.map((row) => (
            <li key={row.checkId}>
              <button
                type="button"
                onClick={() => onPick(row.checkId)}
                title={`${row.checkId}${row.pattern ? `: ${row.pattern}` : ""}: ${plural(row.count, "finding")}`}
                className="group grid w-full grid-cols-[7.5rem_1fr_3.5rem] items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-strong"
              >
                <span className="truncate font-mono text-xs font-semibold text-ink">{row.checkId}</span>
                <span className="relative block h-2.5 rounded-full bg-surface-3" aria-hidden="true">
                  <span
                    className="absolute inset-y-0 left-0 rounded-full bg-accent-strong transition-opacity group-hover:opacity-80"
                    style={{ width: `${Math.max(2, (row.count / max) * 100)}%` }}
                  />
                </span>
                <span className="text-right font-mono text-xs tabular-nums text-ink-2">{fmt(row.count)}</span>
                {row.pattern && <span className="col-span-3 -mt-1 truncate text-[11px] text-ink-3">{row.pattern}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {rows.length > TOP_CHECKS && (
        <button type="button" onClick={() => setAll(!all)} className="mt-3 text-xs font-semibold text-accent-strong hover:underline">
          {all ? `Show top ${TOP_CHECKS}` : `Show all ${rows.length} checks with findings`}
        </button>
      )}
    </section>
  );
}

function Impact({ model }: { model: ReportModel }) {
  const impact = model.impact;
  const measurements = measurementsOf(model);
  const method = impact?.planned_methodology;
  const reference = safeUrl(method?.reference);
  return (
    <section className={`${card} p-5`} aria-labelledby="impact-h">
      <div className="flex items-center justify-between gap-2">
        <h2 id="impact-h" className="text-base font-bold text-ink">Environmental impact</h2>
        <span className="rounded-full border border-line bg-surface px-2 py-0.5 text-[11px] font-semibold text-ink-2">
          {impact?.status === "not_quantified" || !impact ? "Not quantified" : cap(impact.status.replace(/_/g, " "))}
        </span>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">
        {impact?.explanation ?? "This copy of the scan comes from the findings hub, which stores check results and findings only. It has no impact section."}
      </p>
      {method && (
        <div className="mt-4 rounded-xl border border-line bg-surface p-3.5 text-xs text-ink-2">
          <p>
            <span className="font-semibold text-ink">Planned methodology: </span>
            {reference ? <a href={reference} target="_blank" rel="noreferrer" className="text-accent-strong underline-offset-2 hover:underline">{method.name}</a> : method.name}
          </p>
          <p className="mt-1 font-mono text-ink">{method.formula}</p>
          <ul className="mt-2 list-disc space-y-0.5 pl-4 text-ink-3">
            {method.inputs_needed.map((input) => <li key={input}>{input}</li>)}
          </ul>
        </div>
      )}
      {measurements.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="mb-1 text-left font-semibold text-ink">Detector measurements</caption>
            <thead className="text-ink-3">
              <tr className="border-b border-line">
                {["Check", "Metric", "Value", "Basis", "Window", "Method"].map((h) => <th key={h} scope="col" className="px-2 py-1.5 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-line text-ink-2">
              {measurements.map((m, i) => (
                <tr key={i}>
                  <td className="px-2 py-1.5 font-mono">{m.check_id}</td>
                  <td className="px-2 py-1.5">{m.metric.replace(/_/g, " ")}</td>
                  <td className="px-2 py-1.5 font-mono tabular-nums">{m.value.toLocaleString("en-US")} {m.unit}</td>
                  <td className="px-2 py-1.5">{m.basis.replace(/_/g, " ")}</td>
                  <td className="px-2 py-1.5">{formatDate(m.window.start)} to {formatDate(m.window.end)}</td>
                  <td className="px-2 py-1.5">{m.provenance.method}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ComparisonCard({ comparison, onShowNew, onShowHistory }: {
  comparison: Comparison; onShowNew: () => void; onShowHistory: () => void;
}) {
  const { previous, added, resolvedCount, unchanged, complete } = comparison;
  return (
    <section className={`${card} flex flex-col gap-3 p-5 md:flex-row md:items-center md:justify-between`} aria-label="Compared with the previous scan">
      <div>
        <h2 className="text-base font-bold text-ink">Since the previous scan</h2>
        <p className="mt-0.5 text-xs text-ink-3">
          Commit <span className="font-mono">{previous.commitSha.slice(0, 7)}</span>, scanned {formatDate(previous.scannedAt ?? previous.recordedAt)}
          {!complete && " · partial comparison: one of the finding lists was capped"}
        </p>
      </div>
      <dl className="flex flex-wrap gap-5 text-sm">
        <div><dt className="text-[11px] uppercase tracking-wider text-ink-3">New</dt><dd className="font-mono text-xl font-bold text-ink">{fmt(added.length)}</dd></div>
        <div><dt className="text-[11px] uppercase tracking-wider text-ink-3">Resolved</dt><dd className="font-mono text-xl font-bold text-ink">{fmt(resolvedCount)}</dd></div>
        <div><dt className="text-[11px] uppercase tracking-wider text-ink-3">Unchanged</dt><dd className="font-mono text-xl font-bold text-ink">{fmt(unchanged)}</dd></div>
      </dl>
      <div className="flex gap-2">
        {added.length > 0 && <button type="button" onClick={onShowNew} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent hover:bg-accent-hover">Show new findings</button>}
        <button type="button" onClick={onShowHistory} className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-surface-3 hover:text-ink">Scan history</button>
      </div>
    </section>
  );
}

export default function ReportSummary({ model, totals, comparison, onPickCheck, onShowNew, onShowHistory }: {
  model: ReportModel;
  totals: ReportTotals;
  comparison: Comparison | null;
  onPickCheck: (checkId: string) => void;
  onShowNew: () => void;
  onShowHistory: () => void;
}) {
  const broken = (model.adapters ?? []).filter((a) => a.status !== "ok");
  const ran = model.taxonomyTotal ? `${fmt(totals.checks)} of ${fmt(model.taxonomyTotal)} taxonomy checks ran` : `${plural(totals.checks, "check result")} stored`;
  return (
    <div className="space-y-6">
      <section aria-label="Findings">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Tile hero label="Findings" value={totals.findings} sub={ran} />
          {CONFIDENCES.map((c) => (
            <Tile key={c} label={`${cap(c)} confidence`} value={totals.byConfidence[c]}
              lead={<span aria-hidden="true" className={`h-2 w-2 rounded-sm ${CONFIDENCE_SWATCH[c]}`} />} />
          ))}
        </div>
      </section>

      <section aria-label="Check outcomes">
        <SectionTitle title="Check outcomes" hint="Only completed means the whole scope was evaluated. Unavailable and not applicable are not passes." />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {STATUSES.map((s) => (
            <Tile key={s} label={STATUS_META[s].label} value={totals.byStatus[s]} sub={STATUS_META[s].note} lead={<StatusIcon status={s} />} />
          ))}
        </div>
      </section>

      {broken.length > 0 && (
        <div role="alert" className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-xs text-ink">
          {broken.map((a) => (
            <p key={a.owner}><strong>Owner {a.owner} detectors {a.status}:</strong> {a.reason} Their checks are not in this report.</p>
          ))}
        </div>
      )}

      {comparison && <ComparisonCard comparison={comparison} onShowNew={onShowNew} onShowHistory={onShowHistory} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <FindingsByCheck model={model} onPick={onPickCheck} />
        <Impact model={model} />
      </div>
    </div>
  );
}
