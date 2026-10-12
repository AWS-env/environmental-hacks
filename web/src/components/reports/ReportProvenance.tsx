"use client";

import type { ReactNode } from "react";
import { Cpu, FolderTree, GitCommitHorizontal, ShieldCheck } from "lucide-react";
import { commitLink, type ReportModel } from "@/lib/report-model";
import { Panel, fmt, formatBytes, formatDate } from "./parts";

function Dl({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="rp-dl">{rows.flatMap(([k, v]) => [<dt key={`${k}-t`}>{k}</dt>, <dd key={`${k}-d`}>{v}</dd>])}</dl>;
}

const NOT_IN_COPY = <span className="text-[#6f7d83]">Not recorded in this copy</span>;

export default function ReportProvenance({ model }: { model: ReportModel }) {
  const commit = commitLink(model);
  const { files, timings } = model;
  const source = model.origin === "hub" ? "Findings hub (DynamoDB): detector-validated results only"
    : model.origin === "sample" ? "Bundled sample report.json"
    : model.origin === "file" ? "report.json opened from this computer"
    : "scan-api report.json";

  const scan: [string, ReactNode][] = [
    ["Repository", model.repository.url ? <a className="rp-link" href={model.repository.url} target="_blank" rel="noreferrer">{model.repository.id}</a> : model.repository.id],
    ["Commit", <>
      {commit ? <a className="rp-link font-mono" href={commit} target="_blank" rel="noreferrer">{model.repository.commitSha}</a> : <span className="font-mono">{model.repository.commitSha}</span>}
      <small>{model.repository.commitSource === "git" ? "Git commit" : model.repository.commitSource === "content-hash" ? "Content hash of the scanned files (not a Git commit)" : "Commit source not recorded"}</small>
    </>],
    ["Scanned", model.scannedAt ? formatDate(model.scannedAt) : NOT_IN_COPY],
    ["Scan id", <span key="id" className="font-mono">{model.scanId}</span>],
    ["Scanner", model.scanner ? `${model.scanner.name} ${model.scanner.version}` : NOT_IN_COPY],
    ["Source", source],
    ["Analysis time", timings ? `${timings.total_seconds}s, excluding the repository download` : NOT_IN_COPY],
  ];

  const fileRows: [string, ReactNode][] = files ? [
    ["Collected", `${fmt(files.collected)} of ${fmt(files.seen)} files (${formatBytes(files.bytes_collected)})`],
    ["Skipped", Object.entries(files.skipped ?? {}).filter(([, n]) => n > 0).map(([k, n]) => `${k.replace(/_/g, " ")} ${fmt(n)}`).join(" · ") || "None"],
    ["Truncated", files.truncated ? "Yes: the file or byte cap was reached, so some files were not scanned" : "No"],
    ...(files.limits ? [["Limits", `${fmt(files.limits.max_files)} files, ${formatBytes(files.limits.max_file_bytes)} per file`] as [string, ReactNode]] : []),
    ...(files.by_extension ? [["File types", Object.entries(files.by_extension).slice(0, 10).map(([k, n]) => `${k} ${fmt(n)}`).join(" · ")] as [string, ReactNode]] : []),
    ...(files.limits?.excluded_dirs?.length ? [["Excluded directories", <span key="x" className="font-mono text-[11px] text-[#a9b5bb]">{files.limits.excluded_dirs.join(", ")}</span>] as [string, ReactNode]] : []),
  ] : [];

  return (
    <div className="flex flex-col gap-3.5">
      <div className="rp-grid grid-cols-1 xl:grid-cols-2">
        <Panel title="Scan" hint="What was scanned, when, and by which scanner" icon={<GitCommitHorizontal size={18} strokeWidth={1.5} />} labelledBy="prov-scan-h">
          <Dl rows={scan} />
        </Panel>
        <Panel title="Files" hint="What the scanner read" icon={<FolderTree size={18} strokeWidth={1.5} />} labelledBy="prov-files-h">
          {files ? <Dl rows={fileRows} /> : <p className="text-[12px] text-[#93a0a6]">The findings hub stores check results only, so file counts and limits are not available for this copy.</p>}
        </Panel>
      </div>

      {model.adapters && model.adapters.length > 0 && (
        <Panel title="Detectors" hint="One adapter per detector owner; a failed adapter's checks are missing, not passed" icon={<Cpu size={18} strokeWidth={1.5} />} labelledBy="prov-det-h" bodyClassName="rp-table-wrap px-3 pb-2 pt-1">
          <table className="rp-table">
            <thead><tr><th>Owner</th><th>Adapter</th><th>Status</th><th className="text-right">Checks</th><th className="text-right">Time</th></tr></thead>
            <tbody>
              {model.adapters.map((a) => (
                <tr key={a.owner}>
                  <td className="rp-strong">Owner {a.owner}</td>
                  <td className="font-mono">{a.name}</td>
                  <td>{a.status === "ok" ? <span className="text-[#8fd1a8]">OK</span> : <span className="text-[#f0908a]">{a.status}</span>}{a.reason && <div className="mt-1 text-[11px] text-[#93a0a6]">{a.reason}</div>}</td>
                  <td className="text-right font-mono">{fmt(a.checks.length)}</td>
                  <td className="text-right font-mono">{timings?.adapters[a.owner] !== undefined ? `${timings.adapters[a.owner]}s` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {model.limitations.length > 0 && (
        <Panel title="Limits of this report" hint="Read these before acting on a finding" icon={<ShieldCheck size={18} strokeWidth={1.5} />} labelledBy="prov-limits-h">
          <ul className="flex flex-col gap-2.5">
            {model.limitations.map((text) => (
              <li key={text} className="flex gap-3 text-[12.5px] leading-relaxed text-[#c3cdd2]"><span aria-hidden="true" className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-[#8f9ca2]" />{text}</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
