"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";
import {
  CONFIDENCES,
  filterFindings,
  safeUrl,
  sourceLink,
  type FindingFilters,
  type ReportCheck,
  type ReportFinding,
  type ReportModel,
} from "@/lib/report-model";
import { ConfidenceBadge, EvidenceBlock, Pill, cap, card, copyText, fmt, plural, selectClass } from "./parts";

const PAGE = 25;

function FindingCard({ finding, model, isNew, onCopied }: {
  finding: ReportFinding; model: ReportModel; isNew: boolean; onCopied: (ok: boolean) => void;
}) {
  const [copied, setCopied] = useState<null | "ok" | "failed">(null);
  const link = sourceLink(model, finding.file, finding.line);
  const references = finding.references.map(safeUrl).filter((u): u is string => Boolean(u));
  const copy = async () => {
    const ok = await copyText(finding.agentPrompt);
    setCopied(ok ? "ok" : "failed");
    onCopied(ok);
    setTimeout(() => setCopied(null), 2500);
  };
  return (
    <article className="border-t border-line py-4 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-semibold text-ink">{finding.line ? `Line ${finding.line}` : "No line"}</span>
        <ConfidenceBadge confidence={finding.confidence} />
        <Pill mono title="Detector identity: stable when lines move">{finding.identity}</Pill>
        {isNew && <span className="rounded-full bg-accent px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-on-accent">New</span>}
      </div>
      <p className="mt-2 text-sm leading-relaxed text-ink">{finding.summary}</p>
      {finding.evidence.map((item, i) => <EvidenceBlock key={i} item={item} />)}
      <p className="mt-3 text-sm leading-relaxed text-ink-2"><span className="font-semibold text-ink">Recommendation: </span>{finding.recommendation}</p>
      {references.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="References">
          {references.map((url) => (
            <li key={url}><a href={url} target="_blank" rel="noreferrer" className="break-all text-ink-3 underline decoration-line underline-offset-2 hover:text-ink">{url}</a></li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={copy} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent shadow-sm transition-colors hover:bg-accent-hover active:scale-95">
          {copied === "ok" ? "✓ Copied" : "Copy prompt for your coding agent"}
        </button>
        {link && (
          <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-surface-3 hover:text-ink">
            View on GitHub <ExternalLink className="h-3 w-3" />
          </a>
        )}
        <span role="status" className="text-xs text-ink-3">{copied === "failed" ? "Copy failed: open the prompt below and copy it by hand." : ""}</span>
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">Show prompt</summary>
        <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-surface p-3 font-mono text-[11px] leading-relaxed text-ink-2">{finding.agentPrompt}</pre>
      </details>
    </article>
  );
}

function CheckGroup({ check, checkId, findings, model, open, onToggle, newIds, onCopied }: {
  check: ReportCheck | undefined; checkId: string; findings: ReportFinding[]; model: ReportModel;
  open: boolean; onToggle: () => void; newIds: Set<string>; onCopied: (ok: boolean) => void;
}) {
  const [limit, setLimit] = useState(PAGE);
  const shown = findings.slice(0, limit);
  const byFile: [string, ReportFinding[]][] = [];
  for (const f of shown) {
    const file = f.file ?? "(no file reported)";
    const last = byFile[byFile.length - 1];
    if (last && last[0] === file) last[1].push(f);
    else byFile.push([file, [f]]);
  }
  const panelId = `group-${checkId.replace(/[^A-Za-z0-9_-]/g, "_")}`;
  return (
    <section className={card}>
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
          className="flex w-full flex-wrap items-center gap-2 rounded-2xl px-5 py-3.5 text-left hover:bg-surface-3/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-strong"
        >
          <ChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 text-ink-3 transition-transform ${open ? "" : "-rotate-90"}`} />
          <span className="font-mono text-xs font-bold text-ink">{checkId}</span>
          <span className="min-w-0 flex-1 text-sm font-semibold text-ink">{check?.pattern ?? ""}</span>
          <Pill>{plural(findings.length, "finding")}</Pill>
          {check?.owner && <Pill>Owner {check.owner}</Pill>}
          {check?.layer && <Pill>{cap(check.layer)}</Pill>}
          {check && check.scope > 0 && <Pill>{fmt(check.evaluated)}/{fmt(check.scope)} files evaluated</Pill>}
        </button>
      </h3>
      {open && (
        <div id={panelId} className="px-5 pb-4">
          {byFile.map(([file, items], i) => (
            <div key={`${file}-${i}`} className="mt-1">
              <div className="sticky top-0 z-[1] -mx-5 border-y border-line bg-surface-3/95 px-5 py-1.5 font-mono text-xs font-semibold text-ink-2 backdrop-blur">{file}</div>
              {items.map((f) => <FindingCard key={f.id} finding={f} model={model} isNew={newIds.has(f.id)} onCopied={onCopied} />)}
            </div>
          ))}
          {findings.length > limit && (
            <button type="button" onClick={() => setLimit(limit + PAGE)} className="mt-3 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-surface-3 hover:text-ink">
              Show {Math.min(PAGE, findings.length - limit)} more of {fmt(findings.length - limit)} remaining
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export default function ReportFindings({ model, filters, setFilters, onlyNew, setOnlyNew, newIds, hasComparison, onCopied }: {
  model: ReportModel;
  filters: FindingFilters;
  setFilters: (f: FindingFilters) => void;
  onlyNew: boolean;
  setOnlyNew: (v: boolean) => void;
  newIds: Set<string>;
  hasComparison: boolean;
  onCopied: (ok: boolean) => void;
}) {
  const options = useMemo(() => {
    const uniq = (pick: (f: ReportFinding) => string | null) => [...new Set(model.findings.map(pick).filter((v): v is string => Boolean(v)))].sort();
    return {
      layer: uniq((f) => f.layer),
      owner: uniq((f) => f.owner),
      check: uniq((f) => f.checkId),
      confidence: CONFIDENCES.filter((c) => model.findings.some((f) => f.confidence === c)),
    };
  }, [model]);

  const shown = useMemo(() => {
    const matched = filterFindings(model.findings, filters);
    return onlyNew ? matched.filter((f) => newIds.has(f.id)) : matched;
  }, [model, filters, onlyNew, newIds]);

  const groups = useMemo(() => {
    const map = new Map<string, ReportFinding[]>();
    for (const f of shown) {
      const list = map.get(f.checkId) ?? [];
      list.push(f);
      map.set(f.checkId, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (a.file ?? "").localeCompare(b.file ?? "") || (a.line ?? 0) - (b.line ?? 0));
    }
    return [...map].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  }, [shown]);

  const checks = useMemo(() => new Map(model.checks.map((c) => [c.checkId, c])), [model]);
  // Groups the reader toggled; otherwise the first group (or every group when narrowed to one check) is open.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const isOpen = (checkId: string, index: number) => toggled[checkId] ?? (index === 0 || Boolean(filters.check));
  const filtered = Object.values(filters).some(Boolean) || onlyNew;

  const select = (key: keyof FindingFilters, label: string, values: readonly string[], text?: (v: string) => string) => (
    <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
      {label}
      <select value={filters[key]} onChange={(e) => setFilters({ ...filters, [key]: e.target.value })} className={selectClass}>
        <option value="">All</option>
        {values.map((v) => <option key={v} value={v}>{text ? text(v) : v}</option>)}
      </select>
    </label>
  );

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-accent-line bg-accent-soft p-4 text-xs leading-relaxed text-accent-ink">
        <strong className="font-bold">Fixing a finding:</strong> each prompt quotes the exact evidence and asks your coding agent
        (Copilot, Cursor, Claude Code) for the smallest change at that location only, so it does not need to read the whole repository.
        A finding proves a source pattern at the cited line, not its runtime cost.
      </div>

      {model.hub?.truncated && (
        <p className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-ink">
          The findings hub returned {fmt(model.findings.length)} of {fmt(model.hub.findingsTotal)} findings; the rest are not shown.
        </p>
      )}

      <div className={`${card} flex flex-wrap items-end gap-3 p-4`} role="group" aria-label="Filter findings">
        {options.layer.length > 0 && select("layer", "Layer", options.layer, cap)}
        {options.owner.length > 0 && select("owner", "Owner", options.owner, (o) => `Owner ${o}`)}
        {select("check", "Check", options.check)}
        {select("confidence", "Confidence", options.confidence, cap)}
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
          Search
          <input
            type="search"
            value={filters.text}
            onChange={(e) => setFilters({ ...filters, text: e.target.value })}
            placeholder="File, summary, identity or check"
            className={`${selectClass} placeholder:text-ink-4`}
          />
        </label>
        {hasComparison && (
          <label className="flex items-center gap-2 pb-1.5 text-xs font-medium text-ink-2">
            <input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} className="accent-accent-strong" />
            New since previous scan
          </label>
        )}
        {filtered && (
          <button type="button" onClick={() => { setFilters({ layer: "", owner: "", check: "", confidence: "", text: "" }); setOnlyNew(false); }}
            className="pb-1.5 text-xs font-semibold text-accent-strong hover:underline">
            Clear filters
          </button>
        )}
      </div>

      <p className="text-xs text-ink-3" aria-live="polite">
        Showing {fmt(shown.length)} of {fmt(model.findings.length)} findings in {plural(groups.length, "check")}
      </p>

      {groups.length === 0 ? (
        <div className={`${card} p-8 text-center text-sm text-ink-3`}>
          {model.findings.length
            ? "No findings match these filters."
            : "No findings in the evaluated scope. Unavailable, partial and not-applicable checks are not a clean bill of health: see Coverage & provenance."}
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map(([checkId, findings], index) => (
            <CheckGroup
              key={checkId}
              checkId={checkId}
              check={checks.get(checkId)}
              findings={findings}
              model={model}
              open={isOpen(checkId, index)}
              onToggle={() => setToggled({ ...toggled, [checkId]: !isOpen(checkId, index) })}
              newIds={newIds}
              onCopied={onCopied}
            />
          ))}
        </div>
      )}
    </div>
  );
}
