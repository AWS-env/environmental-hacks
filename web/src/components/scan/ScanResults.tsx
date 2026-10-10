"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Database, ExternalLink, Info } from "lucide-react";
import type { CheckStatus, Confidence, Evidence, ScanReport } from "@/lib/scan-api";
import type { HubState, ScanSessionState } from "@/lib/scan-session";
import { checksFromHub, checksFromReport, sourceLink, type CheckView, type FindingView } from "@/lib/scan-view";

const STATUS_STYLE: Record<CheckStatus, { label: string; className: string }> = {
  completed: { label: "Completed", className: "border-emerald-300/30 text-emerald-200" },
  partial: { label: "Partial", className: "border-amber-300/30 text-amber-200" },
  error: { label: "Error", className: "border-rose-300/40 text-rose-200" },
  unavailable: { label: "Unavailable", className: "border-white/15 text-zinc-300" },
  not_applicable: { label: "Not applicable", className: "border-white/10 text-zinc-400" },
};

const CONFIDENCE_STYLE: Record<Confidence, string> = {
  high: "border-[#f7c062]/50 text-[#f7c062]",
  medium: "border-amber-200/30 text-amber-100",
  low: "border-white/15 text-zinc-300",
};

const card = "liquid-glass relative rounded-[22px] border bg-black/45";

function Pill({ className, children }: { className: string; children: ReactNode }) {
  return <span className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>{children}</span>;
}

function Stat({ label, value, detail }: { label: string; value: number | string; detail?: string }) {
  return (
    <div className={`${card} px-4 py-3`}>
      <p className="text-xs text-zinc-400">{label}</p>
      <p className="mt-1 text-2xl font-medium tabular-nums text-white">{value}</p>
      {detail && <p className="mt-0.5 text-[11px] text-zinc-400">{detail}</p>}
    </div>
  );
}

function EvidenceBlock({ item }: { item: Evidence }) {
  const text = typeof item.value === "string" ? item.value : JSON.stringify(item.value, null, 2);
  const label = item.line_start ? `${item.locator}:${item.line_start}` : item.field ? `${item.locator} [${item.field}]` : item.locator;
  const lines = text.split("\n");
  return (
    <figure className="mt-2 overflow-hidden rounded-xl border border-white/10 bg-black/50">
      <figcaption className="border-b border-white/10 px-3 py-1.5 font-mono text-[11px] text-zinc-400">{label}</figcaption>
      <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-relaxed text-zinc-200">
        {lines.map((line, i) => (
          <div key={i} className="flex gap-3">
            {item.line_start ? <span className="select-none text-right tabular-nums text-zinc-500 min-w-[2.5ch]">{item.line_start + i}</span> : null}
            <code className="whitespace-pre">{line || " "}</code>
          </div>
        ))}
      </pre>
    </figure>
  );
}

function Finding({ finding, report }: { finding: FindingView; report?: ScanReport }) {
  const where = finding.file ? `${finding.file}${finding.line ? `:${finding.line}` : ""}` : "location not reported";
  const link = report ? sourceLink(report, finding.file, finding.line) : null;
  return (
    <li className="border-t border-white/[0.07] py-4 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <Pill className={CONFIDENCE_STYLE[finding.confidence]}>{finding.confidence} confidence</Pill>
        {link ? (
          <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-zinc-200 underline decoration-white/20 underline-offset-2 hover:text-white">
            {where}<ExternalLink className="h-3 w-3" />
          </a>
        ) : <span className="font-mono text-xs text-zinc-300">{where}</span>}
      </div>
      <p className="mt-2 text-sm leading-relaxed text-zinc-100">{finding.summary}</p>
      {finding.evidence.map((item, i) => <EvidenceBlock key={i} item={item} />)}
      <p className="mt-3 text-sm leading-relaxed text-zinc-300"><span className="text-zinc-400">Recommendation: </span>{finding.recommendation}</p>
      {finding.references.length > 0 && (
        <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {finding.references.map((url) => (
            <a key={url} href={url} target="_blank" rel="noreferrer" className="break-all text-zinc-400 underline decoration-white/15 underline-offset-2 hover:text-zinc-200">{url}</a>
          ))}
        </p>
      )}
    </li>
  );
}

function Check({ check, report }: { check: CheckView; report?: ScanReport }) {
  const style = STATUS_STYLE[check.status];
  const notes = [check.reason, ...check.limitations].filter((note): note is string => Boolean(note));
  return (
    <li className={`${card} px-4 py-3 md:px-5`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Pill className={style.className}>{style.label}</Pill>
        <span className="font-mono text-xs text-zinc-300">{check.checkId}</span>
        <span className="min-w-0 flex-1 text-sm text-white">{check.pattern ?? ""}</span>
        <span className={`text-xs tabular-nums ${check.findings.length ? "text-[#f7c062]" : "text-zinc-400"}`}>
          {check.findings.length} finding{check.findings.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-400">
        {[check.layer && `layer ${check.layer}`, check.owner && `owner ${check.owner}`,
          check.scope ? `evaluated ${check.evaluated} of ${check.scope} in scope` : null].filter(Boolean).join(" · ")}
      </p>
      {notes.length > 0 && (
        <details className="mt-2 text-xs text-zinc-400">
          <summary className="cursor-pointer select-none text-zinc-300 hover:text-white">
            {check.reason ? "Why" : "Limitations"}{check.limitationsTotal > check.limitations.length ? ` (${check.limitationsTotal} total, grouped)` : ""}
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 leading-relaxed">
            {notes.map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        </details>
      )}
      {check.findings.length > 0 && (
        <ul className="mt-2">
          {check.findings.map((finding) => <Finding key={finding.id} finding={finding} report={report} />)}
        </ul>
      )}
    </li>
  );
}

function HubLine({ hub, report }: { hub: HubState; report?: ScanReport }) {
  const expected = report?.checks.filter((c) => c.status_source === "detector").length;
  let text: string;
  if (hub.kind === "unconfigured") text = "Findings hub not configured (NEXT_PUBLIC_HUB_API_URL is unset); showing the scan report only.";
  else if (hub.kind === "pending" || hub.kind === "loading") text = "Checking the findings hub for the persisted copy...";
  else if (hub.kind === "missing") text = "The findings hub has no persisted copy of this scan yet.";
  else if (hub.kind === "error") text = `Findings hub unavailable: ${hub.message}`;
  else {
    const { results, findings_total: total, truncated } = hub.scan;
    const unverified = results.some((r) => r.evidence !== "verified");
    text = `Findings hub: ${results.length}${expected !== undefined ? ` of ${expected}` : ""} validated check results persisted, `
      + `${total} finding${total === 1 ? "" : "s"}${truncated ? " (list truncated)" : ""}${unverified ? ", stored as shape-validated" : ""}.`;
    if (expected !== undefined && results.length < expected) text += " The rest may still be arriving.";
  }
  return (
    <p className="flex items-start gap-2 text-xs text-zinc-400">
      <Database className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{text}</span>
    </p>
  );
}

export default function ScanResults({ state }: { state: ScanSessionState }) {
  const { report, hub } = state;
  const hubScan = hub.kind === "found" ? hub.scan : undefined;
  const checks = useMemo(() => (report ? checksFromReport(report) : hubScan ? checksFromHub(hubScan) : []), [report, hubScan]);
  const withFindings = checks.filter((c) => c.findings.length > 0);
  const [filter, setFilter] = useState<"findings" | "all">(withFindings.length ? "findings" : "all");
  const shown = filter === "findings" ? withFindings : checks;

  const counts = checks.reduce((acc, c) => ({ ...acc, [c.status]: (acc[c.status] ?? 0) + 1 }), {} as Partial<Record<CheckStatus, number>>);
  const findings = checks.flatMap((c) => c.findings);
  const byConfidence = (level: Confidence) => findings.filter((f) => f.confidence === level).length;
  const commit = report?.repository.commit_sha ?? hubScan?.commit_sha ?? "";
  const repoUrl = report?.repository.url ?? state.repoUrl;

  return (
    <div className="flex flex-col gap-5 pb-10">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-medium tracking-tight text-white">
          <a href={repoUrl} target="_blank" rel="noreferrer" className="hover:underline decoration-white/30 underline-offset-4">
            {repoUrl.replace(/^https:\/\/github\.com\//, "")}
          </a>
        </h2>
        <p className="text-xs text-zinc-400">
          {[commit && `commit ${commit.slice(0, 12)}`,
            report && `scanned ${new Date(report.scanned_at).toLocaleString()}`,
            report && `${report.files.collected} files${report.files.truncated ? " (file limits reached, scan truncated)" : ""}`,
            report && `scanner ${report.scanner.version}`,
            `scan ${state.scanId}`].filter(Boolean).join(" · ")}
        </p>
      </header>

      {!report && (
        <p role="status" className={`${card} flex items-start gap-2 px-4 py-3 text-sm text-zinc-200`}>
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-[#f7c062]" />
          The scan report could not be loaded ({state.error?.message ?? "unknown error"}), so these results come from the
          findings hub. It holds only detector-validated checks, without taxonomy names or the checks that were unavailable.
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Findings" value={findings.length} detail={`${byConfidence("high")} high · ${byConfidence("medium")} medium · ${byConfidence("low")} low`} />
        <Stat label="Checks completed" value={(counts.completed ?? 0) + (counts.partial ?? 0)}
          detail={`${counts.partial ?? 0} partial · ${counts.error ?? 0} error`} />
        <Stat label="Unavailable" value={counts.unavailable ?? 0} detail="need telemetry, artifacts or a runtime" />
        <Stat label="Not applicable" value={counts.not_applicable ?? 0} detail="no files this check examines" />
      </div>

      {report && (
        <div className="space-y-1.5 text-xs text-zinc-400">
          <p>{report.summary.checks_total} of {report.summary.taxonomy_checks_total} taxonomy checks ran. Unavailable and not-applicable checks are not passes.</p>
          {report.adapters.filter((a) => a.status !== "ok").map((a) => (
            <p key={a.name}>Owner {a.owner} checks did not run ({a.status}): {a.reason}</p>
          ))}
        </div>
      )}
      <HubLine hub={hub} report={report} />

      <div className="flex items-center gap-2" role="group" aria-label="Filter checks">
        {([["findings", `With findings (${withFindings.length})`], ["all", `All checks (${checks.length})`]] as const).map(([key, label]) => (
          <button
            key={key}
            aria-pressed={filter === key}
            onClick={() => setFilter(key)}
            className={`liquid-glass-button relative rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors ${
              filter === key ? "border-white/40 text-white" : "border-white/10 text-zinc-400 hover:text-white"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <p className={`${card} px-5 py-6 text-sm text-zinc-300`}>
          No check reported a finding. That covers only the {counts.completed ?? 0} checks that completed; it is not a clean bill of health.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {shown.map((check) => <Check key={check.checkId} check={check} report={report} />)}
        </ul>
      )}

      {report && (
        <footer className="space-y-2 border-t border-white/[0.06] pt-4 text-xs leading-relaxed text-zinc-400">
          <p><span className="text-zinc-300">Impact: </span>{report.impact.explanation}</p>
          <ul className="list-disc space-y-1 pl-5">
            {report.limitations.map((text, i) => <li key={i}>{text}</li>)}
          </ul>
        </footer>
      )}
    </div>
  );
}
