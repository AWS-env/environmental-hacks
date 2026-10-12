"use client";

import { useMemo, useState } from "react";
import type { CheckStatus } from "@/lib/scan-api";
import { STATUSES, type ReportModel, type ReportTotals } from "@/lib/report-model";
import { Panel, STATUS_META, StatusBadge, fmt, layerName } from "./parts";

// Needs attention first.
const ORDER: Record<CheckStatus, number> = { error: 0, unavailable: 1, partial: 2, completed: 3, not_applicable: 4 };

export default function ReportChecks({ model, totals, onPickCheck }: {
  model: ReportModel; totals: ReportTotals; onPickCheck: (checkId: string) => void;
}) {
  const [status, setStatus] = useState<CheckStatus | "">("");
  const [query, setQuery] = useState("");
  const hasTaxonomy = model.checks.some((c) => c.pattern);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...model.checks]
      .filter((c) => (!status || c.status === status)
        && (!q || `${c.checkId} ${c.pattern ?? ""} ${c.layer ?? ""}`.toLowerCase().includes(q)))
      .sort((a, b) => ORDER[a.status] - ORDER[b.status] || b.findingCount - a.findingCount || a.checkId.localeCompare(b.checkId));
  }, [model, status, query]);

  return (
    <Panel
      title="Checks"
      hint="Every check that ran and why some could not evaluate anything. Only completed means the whole scope was evaluated."
      labelledBy="checks-h"
      bodyClassName="flex flex-col gap-3 px-3 pb-3 pt-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-2">
        <div className="rp-seg" role="group" aria-label="Filter by status">
          <button type="button" aria-pressed={!status} onClick={() => setStatus("")}>All {fmt(totals.checks)}</button>
          {STATUSES.filter((s) => totals.byStatus[s] > 0).map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{STATUS_META[s].label} {fmt(totals.byStatus[s])}</button>
          ))}
        </div>
        <input type="search" className="rp-input w-full sm:w-64" placeholder="Search checks" aria-label="Search checks" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="rp-table-wrap">
        <table className="rp-table min-w-[60rem]">
          <thead>
            <tr>
              <th>Check</th>
              <th>Status</th>
              <th>Owner</th>
              {hasTaxonomy && <th>Layer</th>}
              <th>Files evaluated</th>
              <th className="text-right">Findings</th>
              <th>Why / limitations</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              // An incomplete check without a scanner-level reason explains itself in its first limitation.
              const why = c.reason ?? (c.status !== "completed" ? c.limitations[0] : null);
              const notes = [...c.limitations, ...c.notes];
              const share = c.scope ? c.evaluated / c.scope : 0;
              return (
                <tr key={c.checkId}>
                  <td className="min-w-[14rem]">
                    <span className="rp-strong font-mono">{c.checkId}</span>
                    {c.pattern && <div className="mt-0.5 text-[11.5px] text-[#a9b5bb]">{c.pattern}</div>}
                  </td>
                  <td>
                    <StatusBadge status={c.status} />
                    {c.decidedBy === "scanner" && <div className="mt-1 whitespace-nowrap text-[10.5px] text-[#6f7d83]">decided by the scanner</div>}
                  </td>
                  <td>{c.owner ?? "–"}</td>
                  {hasTaxonomy && <td>{c.layer ? layerName(c.layer) : "–"}</td>}
                  <td className="min-w-[9rem]">
                    {c.scope ? (
                      <div className="flex flex-col gap-1.5">
                        <span className="font-mono text-[11.5px]">{fmt(c.evaluated)} / {fmt(c.scope)}</span>
                        <span className="rp-meter w-24" aria-hidden="true"><i style={{ width: `${share * 100}%` }} /></span>
                      </div>
                    ) : "–"}
                  </td>
                  <td className="text-right">
                    {c.findingCount > 0
                      ? <button type="button" className="rp-link font-mono" onClick={() => onPickCheck(c.checkId)}>{fmt(c.findingCount)}</button>
                      : <span className="font-mono text-[#6f7d83]">0</span>}
                  </td>
                  <td className="max-w-md">
                    {why && <div className="leading-relaxed">{why}</div>}
                    {notes.length > 0 && (
                      <details>
                        <summary>Limitations ({c.limitationsTotal > c.limitations.length ? `${fmt(c.limitationsTotal)} total, grouped` : notes.length})</summary>
                        <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <p className="rp-empty">No checks match.</p>}
      </div>
    </Panel>
  );
}
