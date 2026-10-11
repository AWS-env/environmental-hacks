"use client";

import { useMemo, useState, type ReactNode } from "react";
import type { CheckStatus } from "@/lib/scan-api";
import { STATUSES, commitLink, type ReportModel, type ReportTotals } from "@/lib/report-model";
import { STATUS_META, SectionTitle, StatusBadge, card, chipClass, fmt, formatBytes, formatDate } from "./parts";

// Needs attention first.
const ORDER: Record<CheckStatus, number> = { error: 0, unavailable: 1, partial: 2, completed: 3, not_applicable: 4 };

function Dl({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-xs">
      {rows.map(([k, v]) => [
        <dt key={`${k}-t`} className="text-ink-3">{k}</dt>,
        <dd key={`${k}-d`} className="min-w-0 break-words text-ink">{v}</dd>,
      ])}
    </dl>
  );
}

function Provenance({ model }: { model: ReportModel }) {
  const commit = commitLink(model);
  const sha = model.repository.commitSha;
  const commitSource = model.repository.commitSource === "git" ? "Git commit"
    : model.repository.commitSource === "content-hash" ? "content hash of the scanned files (not a Git commit)"
    : model.repository.commitSource ?? "not recorded in this copy";
  const timings = model.timings;
  const rows: [string, ReactNode][] = [
    ["Repository", model.repository.url
      ? <a href={model.repository.url} target="_blank" rel="noreferrer" className="text-accent-strong hover:underline">{model.repository.id}</a>
      : model.repository.id],
    ["Commit", <>{commit ? <a href={commit} target="_blank" rel="noreferrer" className="font-mono text-accent-strong hover:underline">{sha}</a> : <span className="font-mono">{sha}</span>}<span className="block text-ink-3">{commitSource}</span></>],
    ["Scanned", model.scannedAt ? formatDate(model.scannedAt) : "not recorded in this copy"],
    ["Scan id", <span key="id" className="font-mono">{model.scanId}</span>],
    ["Scanner", model.scanner ? `${model.scanner.name} ${model.scanner.version}` : "not recorded in this copy"],
    ["Source", model.origin === "hub" ? "Findings hub (DynamoDB): detector-validated results only"
      : model.origin === "sample" ? "Bundled sample report.json"
      : model.origin === "file" ? "report.json opened from this computer"
      : "scan-api report.json"],
  ];
  if (timings) {
    rows.push(["Scan time", `${timings.total_seconds}s of analysis (excludes downloading the repository); `
      + Object.entries(timings.adapters).map(([owner, s]) => `owner ${owner} ${s}s`).join(", ")]);
  }
  return (
    <section className={`${card} p-5`} aria-labelledby="prov-h">
      <h3 id="prov-h" className="mb-3 text-sm font-bold text-ink">Provenance</h3>
      <Dl rows={rows} />
    </section>
  );
}

function Files({ files }: { files: NonNullable<ReportModel["files"]> }) {
  const skipped = Object.entries(files.skipped ?? {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `${k.replace(/_/g, " ")} ${fmt(n)}`).join(", ") || "none";
  const types = Object.entries(files.by_extension ?? {}).slice(0, 10).map(([k, n]) => `${k} ${fmt(n)}`).join(", ");
  const rows: [string, ReactNode][] = [
    ["Collected", `${fmt(files.collected)} of ${fmt(files.seen)} files seen (${formatBytes(files.bytes_collected)})`],
    ["Skipped", skipped],
    ["Truncated", files.truncated ? "Yes: the file or byte cap was reached, so some files were not scanned" : "No"],
  ];
  if (files.limits) rows.push(["Limits", `${fmt(files.limits.max_files)} files, ${formatBytes(files.limits.max_file_bytes)} per file`]);
  if (files.limits?.excluded_dirs?.length) rows.push(["Excluded directories", files.limits.excluded_dirs.join(", ")]);
  if (types) rows.push(["Top file types", types]);
  return (
    <section className={`${card} p-5`} aria-labelledby="files-h">
      <h3 id="files-h" className="mb-3 text-sm font-bold text-ink">Files</h3>
      <Dl rows={rows} />
    </section>
  );
}

function Adapters({ model }: { model: ReportModel }) {
  const adapters = model.adapters ?? [];
  return (
    <section className={`${card} p-5`} aria-labelledby="adapters-h">
      <h3 id="adapters-h" className="mb-3 text-sm font-bold text-ink">Detectors</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-ink-3">
            <tr className="border-b border-line">
              {["Owner", "Adapter", "Status", "Checks", "Time"].map((h) => <th key={h} scope="col" className="px-2 py-1.5 font-semibold">{h}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-line text-ink-2">
            {adapters.map((a) => (
              <tr key={a.owner}>
                <td className="px-2 py-2 font-semibold text-ink">{a.owner}</td>
                <td className="px-2 py-2 font-mono">{a.name}</td>
                <td className="px-2 py-2">
                  {a.status === "ok" ? "OK" : <span className="font-semibold text-rose-700 dark:text-rose-300">{a.status}</span>}
                  {a.reason && <div className="mt-0.5 text-ink-3">{a.reason}</div>}
                </td>
                <td className="px-2 py-2 font-mono tabular-nums">{fmt(a.checks.length)}</td>
                <td className="px-2 py-2 font-mono tabular-nums">{model.timings?.adapters[a.owner] !== undefined ? `${model.timings.adapters[a.owner]}s` : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ChecksTable({ model, totals }: { model: ReportModel; totals: ReportTotals }) {
  const [status, setStatus] = useState<CheckStatus | "">("");
  const rows = useMemo(() => [...model.checks]
    .filter((c) => !status || c.status === status)
    .sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.checkId.localeCompare(b.checkId)), [model, status]);
  const hasTaxonomy = model.checks.some((c) => c.pattern);
  return (
    <section aria-labelledby="checks-h">
      <SectionTitle
        title="Checks"
        hint="Every check that ran, and why some could not evaluate anything. Only completed means the whole scope was evaluated; nothing else is a pass."
      />
      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Filter checks by status">
        <button type="button" aria-pressed={!status} onClick={() => setStatus("")} className={chipClass(!status)}>All ({fmt(totals.checks)})</button>
        {STATUSES.filter((s) => totals.byStatus[s] > 0).map((s) => (
          <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)} className={chipClass(status === s)}>
            {STATUS_META[s].label} ({fmt(totals.byStatus[s])})
          </button>
        ))}
      </div>
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full min-w-[56rem] text-left text-xs">
          <thead className="text-ink-3">
            <tr className="border-b border-line">
              {["Check", ...(hasTaxonomy ? ["Pattern"] : []), "Owner", ...(hasTaxonomy ? ["Layer"] : []), "Status", "Files evaluated", "Findings", "Why / limitations"]
                .map((h) => <th key={h} scope="col" className="whitespace-nowrap px-3 py-2.5 font-semibold uppercase tracking-wider">{h}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-line text-ink-2">
            {rows.map((c) => {
              // An incomplete check without a scanner-level reason explains itself in its first limitation.
              const why = c.reason ?? (c.status !== "completed" ? c.limitations[0] : null);
              const notes = [...c.limitations, ...c.notes];
              return (
                <tr key={c.checkId} className="align-top">
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono font-semibold text-ink">{c.checkId}</td>
                  {hasTaxonomy && <td className="px-3 py-2.5 text-ink">{c.pattern ?? ""}</td>}
                  <td className="px-3 py-2.5">{c.owner ?? "–"}</td>
                  {hasTaxonomy && <td className="px-3 py-2.5">{c.layer ?? ""}</td>}
                  <td className="px-3 py-2.5">
                    <StatusBadge status={c.status} />
                    {c.decidedBy === "scanner" && <div className="mt-0.5 text-[11px] text-ink-4">decided by the scanner</div>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">{c.scope ? `${fmt(c.evaluated)} / ${fmt(c.scope)}` : "–"}</td>
                  <td className="px-3 py-2.5 font-mono tabular-nums">{fmt(c.findingCount)}</td>
                  <td className="max-w-md px-3 py-2.5">
                    {why && <div>{why}</div>}
                    {notes.length > 0 && (
                      <details className="mt-1">
                        <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">
                          Limitations ({c.limitationsTotal > c.limitations.length ? `${fmt(c.limitationsTotal)} total, grouped` : notes.length})
                        </summary>
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-3">
                          {notes.map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ReportCoverage({ model, totals }: { model: ReportModel; totals: ReportTotals }) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Provenance model={model} />
        {model.files ? <Files files={model.files} /> : (
          <section className={`${card} p-5 text-xs text-ink-3`}>
            <h3 className="mb-2 text-sm font-bold text-ink">Files</h3>
            The findings hub stores check results only, so file counts and limits are not available for this copy.
          </section>
        )}
      </div>
      {model.adapters && model.adapters.length > 0 && <Adapters model={model} />}
      <ChecksTable model={model} totals={totals} />
      {model.limitations.length > 0 && (
        <section className={`${card} p-5`} aria-labelledby="limits-h">
          <h3 id="limits-h" className="mb-2 text-sm font-bold text-ink">Limits of this report</h3>
          <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-ink-2">
            {model.limitations.map((text) => <li key={text}>{text}</li>)}
          </ul>
        </section>
      )}
    </div>
  );
}
