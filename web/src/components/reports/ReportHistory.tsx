"use client";

import Link from "next/link";
import { History } from "lucide-react";
import type { ScanHistoryEntry } from "@/lib/report-store";
import type { ReportModel } from "@/lib/report-model";
import { CONFIDENCE_COLOR, Panel, fmt, formatDate } from "./parts";

/** Change in findings against the next-older scan of the same repository in `entries` (newest first). */
function delta(entries: ScanHistoryEntry[], index: number): number | null {
  const entry = entries[index];
  const older = entries.slice(index + 1).find((e) => e.repoId === entry.repoId);
  return older ? entry.findings - older.findings : null;
}

export default function ReportHistory({ model, history, onOpen, onForget }: {
  model: ReportModel;
  history: ScanHistoryEntry[];
  onOpen: (scanId: string) => void;
  onForget: (scanId: string) => void;
}) {
  return (
    <Panel
      title="Scan history"
      hint="Scans run or opened in this browser, newest first. Stored only here; scan-api keeps each report for 7 days, after which the findings-hub copy is shown."
      icon={<History size={18} strokeWidth={1.5} />}
      labelledBy="history-h"
      right={<Link href="/home" className="rp-btn rp-btn-sm rp-btn-primary">New scan</Link>}
      bodyClassName="rp-table-wrap px-3 pb-2 pt-1"
    >
      {history.length === 0 ? (
        <p className="rp-empty">
          No scans in this browser yet. Run one from <Link href="/home" className="rp-link">the home page</Link>; it will be listed here,
          with the changes between scans of the same repository. Meanwhile, <Link href="/reports?sample=1" className="rp-link">open the sample report</Link>.
        </p>
      ) : (
        <table className="rp-table min-w-[52rem]">
          <thead>
            <tr><th>Repository</th><th>Commit</th><th>Scanned</th><th className="text-right">Findings</th><th>Confidence</th><th className="text-right">Checks evaluated</th><th className="text-right">Change</th><th /></tr>
          </thead>
          <tbody>
            {history.map((entry, index) => {
              const current = entry.scanId === model.scanId;
              const change = delta(history, index);
              return (
                <tr key={entry.scanId} className={current ? "is-current" : undefined}>
                  <td>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rp-strong">{entry.repoId.replace(/^github:/, "")}</span>
                      {current && <span className="rp-pill is-new">Viewing</span>}
                      {entry.source === "hub" && <span className="rp-pill">Hub copy</span>}
                    </div>
                    {entry.scannerVersion && <div className="mt-0.5 text-[10.5px] text-[#6f7d83]">scanner {entry.scannerVersion}</div>}
                  </td>
                  <td className="font-mono">{entry.commitSha.slice(0, 7)}</td>
                  <td className="whitespace-nowrap">{formatDate(entry.scannedAt ?? entry.recordedAt)}</td>
                  <td className="rp-strong text-right font-mono">{fmt(entry.findings)}</td>
                  <td className="whitespace-nowrap text-[11px]">
                    {(["high", "medium", "low"] as const).map((c) => (
                      <span key={c} className="mr-3 inline-flex items-center gap-1.5"><span className="rp-dot" style={{ background: CONFIDENCE_COLOR[c] }} aria-hidden="true" />{fmt(entry.byConfidence[c])} {c}</span>
                    ))}
                  </td>
                  <td className="text-right font-mono">{fmt(entry.byStatus.completed + entry.byStatus.partial)} / {fmt(entry.checks)}</td>
                  <td className="text-right font-mono">{change === null ? <span className="text-[#6f7d83]">–</span> : change === 0 ? "±0" : `${change > 0 ? "+" : "−"}${fmt(Math.abs(change))}`}</td>
                  <td className="whitespace-nowrap text-right">
                    {!current && <button type="button" className="rp-btn rp-btn-sm" onClick={() => onOpen(entry.scanId)}>Open</button>}
                    <button type="button" className="rp-btn rp-btn-sm rp-btn-ghost" onClick={() => onForget(entry.scanId)}
                      aria-label={`Remove the ${entry.repoId.replace(/^github:/, "")} scan from this browser's history`}>Remove</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
