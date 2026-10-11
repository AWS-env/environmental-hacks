"use client";

import Link from "next/link";
import type { ScanHistoryEntry } from "@/lib/report-store";
import type { ReportModel } from "@/lib/report-model";
import { SectionTitle, card, fmt, formatDate } from "./parts";

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
    <div className="space-y-4">
      <SectionTitle
        title="Scan history"
        hint="Scans run or opened in this browser, newest first. The list lives only here (local storage); scan-api keeps each report for 7 days, after which the findings-hub copy is shown."
        right={<Link href="/home" className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent hover:bg-accent-hover">Scan a repository</Link>}
      />
      {history.length === 0 ? (
        <div className={`${card} p-8 text-center text-sm text-ink-3`}>
          No scans in this browser yet. Run one from <Link href="/home" className="font-semibold text-accent-strong hover:underline">the home page</Link> and it will be listed here, with the changes between scans of the same repository.
        </div>
      ) : (
        <ul className="space-y-3">
          {history.map((entry, index) => {
            const current = entry.scanId === model.scanId;
            const change = delta(history, index);
            return (
              <li key={entry.scanId} className={`${card} flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between ${current ? "ring-2 ring-accent-strong" : ""}`}>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">{entry.repoId.replace(/^github:/, "")}</span>
                    <span className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-2">{entry.commitSha.slice(0, 7)}</span>
                    {current && <span className="rounded-full bg-accent px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-on-accent">Viewing</span>}
                    {entry.source === "hub" && <span className="rounded-full border border-line px-2 py-0.5 text-[10px] font-semibold text-ink-3">Hub copy</span>}
                  </div>
                  <p className="mt-1 text-xs text-ink-3">
                    {formatDate(entry.scannedAt ?? entry.recordedAt)}
                    {entry.scannerVersion && ` · scanner ${entry.scannerVersion}`}
                    {` · ${fmt(entry.byStatus.completed + entry.byStatus.partial)} of ${fmt(entry.checks)} checks evaluated`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  <div className="text-right">
                    <div className="font-mono text-sm font-bold text-ink">{fmt(entry.findings)} findings</div>
                    <div className="text-[11px] text-ink-3">
                      {fmt(entry.byConfidence.high)} high · {fmt(entry.byConfidence.medium)} medium · {fmt(entry.byConfidence.low)} low
                      {change !== null && ` · ${change === 0 ? "±0" : `${change > 0 ? "+" : "−"}${fmt(Math.abs(change))}`} findings vs previous scan`}
                    </div>
                  </div>
                  {!current && (
                    <button type="button" onClick={() => onOpen(entry.scanId)} className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-surface-3 hover:text-ink">
                      Open
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onForget(entry.scanId)}
                    aria-label={`Remove the ${entry.repoId.replace(/^github:/, "")} scan from this browser's history`}
                    className="rounded-lg px-2 py-1.5 text-xs font-medium text-ink-4 hover:bg-surface-3 hover:text-ink"
                  >
                    Remove
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-ink-3">
        Want a quick look first? <Link href="/reports?sample=1" className="font-semibold text-accent-strong hover:underline">Open the sample report</Link>.
      </p>
    </div>
  );
}
