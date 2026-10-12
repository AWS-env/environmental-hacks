"use client";

import { useMemo, useState } from "react";
import { Activity, FileCode2, Leaf, ListChecks, ScanSearch } from "lucide-react";
import type { Comparison } from "@/lib/report-store";
import { CONFIDENCES, STATUSES, findingsByCheck, measurementsOf, safeUrl, type ReportModel, type ReportTotals } from "@/lib/report-model";
import {
  CONFIDENCE_COLOR,
  ConfidenceDot,
  Panel,
  STATUS_META,
  StackedBar,
  StatusIcon,
  cap,
  fmt,
  layerName,
  formatBytes,
  formatDate,
  plural,
} from "./parts";

const TOP_CHECKS = 10;

function countBy(values: (string | null)[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** `plain`: titles are names, not check ids, so they are not set in mono. */
function Bars({ rows, onPick, label, plain }: { rows: { key: string; title: string; sub?: string | null; value: number }[]; onPick?: (key: string) => void; label: string; plain?: boolean }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="flex flex-col" aria-label={label}>
      {rows.map((row) => {
        const content = (
          <>
            <span className="rp-bar-label"><b className={plain ? "!font-sans !text-[12px]" : undefined}>{row.title}</b>{row.sub && <span>{row.sub}</span>}</span>
            <span className="rp-meter" aria-hidden="true"><i style={{ width: `${Math.max(2, (row.value / max) * 100)}%` }} /></span>
            <span className="rp-bar-value">{fmt(row.value)}</span>
          </>
        );
        return (
          <li key={row.key}>
            {onPick
              ? <button type="button" className="rp-bar-row" onClick={() => onPick(row.key)} title={`${row.title}${row.sub ? `: ${row.sub}` : ""}: ${fmt(row.value)}`}>{content}</button>
              : <div className="rp-bar-row">{content}</div>}
          </li>
        );
      })}
    </ul>
  );
}

export default function ReportOverview({ model, totals, comparison, onPickCheck, onPickFinding, onShowNew, onShowHistory }: {
  model: ReportModel;
  totals: ReportTotals;
  comparison: Comparison | null;
  onPickCheck: (checkId: string) => void;
  onPickFinding: (id: string) => void;
  onShowNew: () => void;
  onShowHistory: () => void;
}) {
  const [allChecks, setAllChecks] = useState(false);
  const byCheck = useMemo(() => findingsByCheck(model), [model]);
  const byLayer = useMemo(() => countBy(model.findings.map((f) => f.layer)), [model]);
  const byOwner = useMemo(() => countBy(model.findings.map((f) => f.owner)), [model]);
  const top = model.findings.slice(0, 6);
  const evaluated = totals.byStatus.completed + totals.byStatus.partial;
  const broken = (model.adapters ?? []).filter((a) => a.status !== "ok");
  const method = model.impact?.planned_methodology;
  const reference = safeUrl(method?.reference);
  const measurements = measurementsOf(model);
  const patternOf = (checkId: string) => model.checks.find((c) => c.checkId === checkId)?.pattern ?? null;

  return (
    <div className="flex flex-col gap-3.5">
      {broken.map((a) => (
        <div key={a.owner} role="alert" className="rp-notice is-warn">
          <span><b>Owner {a.owner} detectors {a.status}.</b> {a.reason} Their checks are not in this report.</span>
        </div>
      ))}

      {/* KPIs */}
      <div className="rp-grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <section className="rp-panel rp-kpi" aria-label="Findings">
          <div className="rp-kpi-top"><span className="rp-icon"><ScanSearch size={18} strokeWidth={1.5} /></span><div><h2>Findings</h2><p>Source patterns at a cited line</p></div></div>
          <div className="rp-kpi-value">{fmt(totals.findings)}</div>
          <StackedBar label="Findings by confidence" parts={CONFIDENCES.map((c) => ({ key: c, label: cap(c), value: totals.byConfidence[c], color: CONFIDENCE_COLOR[c] }))} />
        </section>
        <section className="rp-panel rp-kpi" aria-label="Checks evaluated">
          <div className="rp-kpi-top"><span className="rp-icon"><ListChecks size={18} strokeWidth={1.5} /></span><div><h2>Checks evaluated</h2><p>{model.taxonomyTotal ? `${fmt(totals.checks)} ran of ${fmt(model.taxonomyTotal)} in the taxonomy` : `${fmt(totals.checks)} stored results`}</p></div></div>
          <div className="rp-kpi-value">{fmt(evaluated)}<small>/ {fmt(totals.checks)}</small></div>
          <StackedBar label="Check outcomes" parts={STATUSES.map((s) => ({ key: s, label: STATUS_META[s].label, value: totals.byStatus[s], color: STATUS_META[s].color }))} />
        </section>
        <section className="rp-panel rp-kpi" aria-label="Files scanned">
          <div className="rp-kpi-top"><span className="rp-icon"><FileCode2 size={18} strokeWidth={1.5} /></span><div><h2>Files scanned</h2><p>Read as text, never executed</p></div></div>
          {model.files ? (
            <>
              <div className="rp-kpi-value">{fmt(model.files.collected)}<small>/ {fmt(model.files.seen)}</small></div>
              <p className="rp-kpi-foot">{formatBytes(model.files.bytes_collected)} collected · {model.files.truncated ? "file limits reached, scan truncated" : "nothing truncated"}</p>
            </>
          ) : (
            <>
              <div className="rp-kpi-value is-text">Not recorded</div>
              <p className="rp-kpi-foot">The findings-hub copy has no file statistics.</p>
            </>
          )}
        </section>
        <section className="rp-panel rp-kpi" aria-label="Environmental impact">
          <div className="rp-kpi-top"><span className="rp-icon"><Leaf size={18} strokeWidth={1.5} /></span><div><h2>Environmental impact</h2><p>Energy, emissions and water</p></div></div>
          <div className="rp-kpi-value is-text">{measurements.length ? plural(measurements.length, "measurement") : "Not quantified"}</div>
          <p className="rp-kpi-foot">A repository scan has no telemetry, so no impact figures are estimated. See below.</p>
        </section>
      </div>

      {comparison && (
        <section className="rp-panel" aria-label="Compared with the previous scan">
          <div className="rp-panel-head flex-wrap">
            <span className="rp-icon"><Activity size={18} strokeWidth={1.5} /></span>
            <div className="min-w-0">
              <h2>Since the previous scan</h2>
              <p>Commit <span className="font-mono">{comparison.previous.commitSha.slice(0, 7)}</span>, {formatDate(comparison.previous.scannedAt ?? comparison.previous.recordedAt)}{!comparison.complete && " · partial: a finding list was capped"}</p>
            </div>
            <dl className="ml-auto flex gap-7">
              {([["New", comparison.added.length], ["Resolved", comparison.resolvedCount], ["Unchanged", comparison.unchanged]] as const).map(([k, v]) => (
                <div key={k}><dt className="rp-eyebrow">{k}</dt><dd className="mt-1 font-mono text-xl text-[#f3f6f7]">{fmt(v)}</dd></div>
              ))}
            </dl>
            <div className="flex gap-2">
              {comparison.added.length > 0 && <button type="button" className="rp-btn rp-btn-sm rp-btn-primary" onClick={onShowNew}>Show new findings</button>}
              <button type="button" className="rp-btn rp-btn-sm" onClick={onShowHistory}>History</button>
            </div>
          </div>
        </section>
      )}

      {/* Where the findings are */}
      <div className="rp-grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Panel title="Findings by check" hint="Select a check to open its findings" labelledBy="by-check-h"
          right={byCheck.length > TOP_CHECKS && (
            <button type="button" className="rp-btn rp-btn-sm rp-btn-ghost" onClick={() => setAllChecks(!allChecks)}>
              {allChecks ? `Top ${TOP_CHECKS}` : `All ${byCheck.length}`}
            </button>
          )}>
          {byCheck.length === 0 ? <p className="rp-empty">No findings in the evaluated scope.</p> : (
            <Bars label="Findings by check" onPick={onPickCheck}
              rows={(allChecks ? byCheck : byCheck.slice(0, TOP_CHECKS)).map((r) => ({ key: r.checkId, title: r.checkId, sub: r.pattern, value: r.count }))} />
          )}
        </Panel>
        <div className="flex flex-col gap-3.5">
          <Panel title="By layer" hint="Where in the stack" labelledBy="by-layer-h">
            {byLayer.length ? <Bars label="Findings by layer" rows={byLayer.map(([k, v]) => ({ key: k, title: layerName(k), value: v }))} plain />
              : <p className="text-xs text-[#86939a]">This copy has no layer information.</p>}
          </Panel>
          <Panel title="By detector owner" hint="Which detector family found them" labelledBy="by-owner-h">
            <Bars label="Findings by owner" rows={byOwner.map(([k, v]) => ({ key: k, title: `Owner ${k}`, value: v }))} plain />
          </Panel>
        </div>
      </div>

      {/* Strongest findings */}
      <Panel title="Strongest findings" hint="Highest confidence first. Open one to see its evidence and the fix prompt." labelledBy="top-h" bodyClassName="rp-table-wrap px-3 pb-2 pt-1">
        {top.length === 0 ? <p className="rp-empty">No findings.</p> : (
          <table className="rp-table">
            <thead><tr><th>Confidence</th><th>Check</th><th>Location</th><th>Finding</th></tr></thead>
            <tbody>
              {top.map((f) => (
                <tr key={f.id} className="cursor-pointer" onClick={() => onPickFinding(f.id)}>
                  <td className="whitespace-nowrap"><span className="rp-status"><ConfidenceDot confidence={f.confidence} />{cap(f.confidence)}</span></td>
                  <td className="whitespace-nowrap"><span className="rp-strong font-mono">{f.checkId}</span><div className="text-[11px] text-[#86939a]">{f.pattern ?? patternOf(f.checkId) ?? ""}</div></td>
                  <td className="max-w-[16rem] truncate font-mono text-[11px]">{f.file ?? "–"}{f.line ? `:${f.line}` : ""}</td>
                  <td>
                    <button type="button" className="text-left text-[#dfe6e9] hover:text-white" onClick={(e) => { e.stopPropagation(); onPickFinding(f.id); }}>{f.summary}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* Outcomes + impact */}
      <div className="rp-grid grid-cols-1 xl:grid-cols-2">
        <Panel title="Check outcomes" hint="Only completed means the whole scope was evaluated. Nothing else is a pass." labelledBy="outcomes-h">
          <ul className="flex flex-col gap-3">
            {STATUSES.map((s) => (
              <li key={s} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_2.5rem] items-center gap-4">
                <div className="flex min-w-0 items-center gap-2.5"><StatusIcon status={s} />
                  <div className="min-w-0"><div className="text-[12.5px] text-[#e7edf0]">{STATUS_META[s].label}</div><div className="truncate text-[10.5px] text-[#86939a]">{STATUS_META[s].note}</div></div>
                </div>
                <span className="rp-meter" aria-hidden="true"><i style={{ width: `${totals.checks ? (totals.byStatus[s] / totals.checks) * 100 : 0}%`, background: STATUS_META[s].color }} /></span>
                <span className="text-right font-mono text-[12px] text-[#dfe6e9]">{fmt(totals.byStatus[s])}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Environmental impact" hint={model.impact ? cap(model.impact.status.replace(/_/g, " ")) : "Not in this copy"} labelledBy="impact-h">
          <p className="text-[12.5px] leading-relaxed text-[#c3cdd2]">
            {model.impact?.explanation ?? "The findings hub stores check results and findings only, so this copy has no impact section."}
          </p>
          {method && (
            <div className="mt-4 rounded-[10px] border border-[#ffffff12] bg-[#ffffff04] p-3.5">
              <p className="rp-section-label">Planned methodology</p>
              <p className="text-[12px] text-[#dfe6e9]">{reference ? <a className="rp-link" href={reference} target="_blank" rel="noreferrer">{method.name}</a> : method.name}</p>
              <p className="mt-2 font-mono text-[12px] text-[#f3f6f7]">{method.formula}</p>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-[#93a0a6]">
                {method.inputs_needed.map((input) => <li key={input}>{input}</li>)}
              </ul>
            </div>
          )}
          {measurements.length > 0 && (
            <div className="rp-table-wrap mt-4">
              <table className="rp-table">
                <thead><tr><th>Check</th><th>Metric</th><th>Value</th><th>Basis</th><th>Method</th></tr></thead>
                <tbody>
                  {measurements.map((m, i) => (
                    <tr key={i}><td className="font-mono">{m.check_id}</td><td>{m.metric.replace(/_/g, " ")}</td><td className="font-mono">{m.value.toLocaleString("en-US")} {m.unit}</td><td>{m.basis.replace(/_/g, " ")}</td><td>{m.provenance.method}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
