"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Copy, ExternalLink } from "lucide-react";
import {
  CONFIDENCES,
  NO_FILTERS,
  filterFindings,
  safeUrl,
  sourceLink,
  type FindingFilters,
  type ReportFinding,
  type ReportModel,
} from "@/lib/report-model";
import { ConfidenceBadge, ConfidenceDot, EvidenceBlock, StatusBadge, cap, copyText, fmt, layerName, plural } from "./parts";

const PAGE = 60;

function FindingDetail({ finding, model, isNew, onCopied }: {
  finding: ReportFinding; model: ReportModel; isNew: boolean; onCopied: (ok: boolean) => void;
}) {
  const [copied, setCopied] = useState<null | "ok" | "failed">(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const check = model.checks.find((c) => c.checkId === finding.checkId);
  const pattern = finding.pattern ?? check?.pattern ?? null;
  const link = sourceLink(model, finding.file, finding.line);
  const references = finding.references.map(safeUrl).filter((u): u is string => Boolean(u));
  const where = finding.file ? `${finding.file}${finding.line ? `:${finding.line}` : ""}` : "Location not reported";
  const copy = async () => {
    const ok = await copyText(finding.agentPrompt);
    setCopied(ok ? "ok" : "failed");
    onCopied(ok);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 2500);
  };

  return (
    <article className="flex flex-col" aria-labelledby="finding-title">
      <header className="rp-panel-head flex-col items-stretch gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[12px] text-[#f3f6f7]">{finding.checkId}</span>
          <ConfidenceBadge confidence={finding.confidence} />
          {finding.owner && <span className="rp-pill">Owner {finding.owner}</span>}
          {finding.layer && <span className="rp-pill">{layerName(finding.layer)}</span>}
          {isNew && <span className="rp-pill is-new">New</span>}
        </div>
        <h2 id="finding-title" className="text-[15px] font-medium leading-snug tracking-[-0.01em] text-[#f3f6f7]">{pattern ?? finding.checkId}</h2>
        <div className="flex flex-wrap items-center gap-2">
          {link
            ? <a href={link} target="_blank" rel="noreferrer" className="rp-link inline-flex items-center gap-1.5 font-mono text-[11.5px]">{where}<ExternalLink size={12} /></a>
            : <span className="font-mono text-[11.5px] text-[#a9b5bb]">{where}</span>}
        </div>
      </header>

      <div className="flex flex-col gap-5 p-[18px]">
        <section>
          <p className="rp-section-label">What was found</p>
          <p className="text-[13px] leading-relaxed text-[#dfe6e9]">{finding.summary}</p>
        </section>

        {finding.evidence.length > 0 && (
          <section className="flex flex-col gap-2.5">
            <p className="rp-section-label">Evidence</p>
            {finding.evidence.map((item, i) => <EvidenceBlock key={i} item={item} />)}
          </section>
        )}

        <section>
          <p className="rp-section-label">Recommended fix</p>
          <p className="text-[13px] leading-relaxed text-[#c3cdd2]">{finding.recommendation}</p>
        </section>

        <section className="flex flex-wrap items-center gap-2 rounded-[12px] border border-[#ffffff14] bg-[#ffffff05] p-3">
          <button type="button" className="rp-btn rp-btn-primary" onClick={copy}>
            <Copy size={13} />{copied === "ok" ? "Copied" : "Copy prompt for your coding agent"}
          </button>
          {link && <a href={link} target="_blank" rel="noreferrer" className="rp-btn"><ExternalLink size={13} />View on GitHub</a>}
          <span role="status" className="text-[11.5px] text-[#93a0a6]">
            {copied === "failed" ? "Copy failed: open the prompt below and copy it by hand." : "Asks your agent for the smallest change at this location only."}
          </span>
        </section>

        <details className="group">
          <summary className="cursor-pointer select-none text-[12px] text-[#a9b5bb] hover:text-white">Show the prompt</summary>
          <pre className="rp-prompt mt-2.5">{finding.agentPrompt}</pre>
        </details>

        {references.length > 0 && (
          <section>
            <p className="rp-section-label">References</p>
            <ul className="flex flex-col gap-1.5 text-[11.5px]">
              {references.map((url) => <li key={url}><a href={url} target="_blank" rel="noreferrer" className="rp-link break-all">{url}</a></li>)}
            </ul>
          </section>
        )}

        <section className="rounded-[12px] border border-[#ffffff0f] p-3.5">
          <p className="rp-section-label">About this check</p>
          <dl className="rp-dl">
            <dt>Status</dt><dd>{check ? <StatusBadge status={check.status} /> : "–"}</dd>
            <dt>Scope</dt><dd>{check?.scope ? `${fmt(check.evaluated)} of ${fmt(check.scope)} files evaluated` : "–"}</dd>
            <dt>Findings</dt><dd>{plural(model.findings.filter((f) => f.checkId === finding.checkId).length, "finding")} in this report</dd>
            <dt>Identity</dt><dd className="font-mono text-[11px]">{finding.identity}<small>Stable when lines move, so it is how scans are compared</small></dd>
          </dl>
        </section>
      </div>
    </article>
  );
}

export default function ReportFindings({ model, filters, setFilters, onlyNew, setOnlyNew, newIds, hasComparison, selectedId, setSelectedId, onCopied }: {
  model: ReportModel;
  filters: FindingFilters;
  setFilters: (f: FindingFilters) => void;
  onlyNew: boolean;
  setOnlyNew: (v: boolean) => void;
  newIds: Set<string>;
  hasComparison: boolean;
  selectedId: string | null;
  setSelectedId: (id: string) => void;
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

  const selectedIndex = Math.max(0, shown.findIndex((f) => f.id === selectedId));
  const selected = shown[selectedIndex] ?? null;
  const [limit, setLimit] = useState(PAGE);
  const visible = shown.slice(0, Math.max(limit, selectedIndex + 1));
  const listRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const filtered = Object.values(filters).some(Boolean) || onlyNew;

  // Keep the selected row in view, and show the top of each newly selected finding.
  useEffect(() => {
    if (!selected) return;
    listRef.current?.querySelector(`[data-id="${CSS.escape(selected.id)}"]`)?.scrollIntoView({ block: "nearest" });
    detailRef.current?.scrollTo({ top: 0 });
  }, [selected]);

  const onListKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (!step || !shown.length) return;
    event.preventDefault();
    const next = shown[Math.min(shown.length - 1, Math.max(0, selectedIndex + step))];
    setSelectedId(next.id);
    requestAnimationFrame(() => (listRef.current?.querySelector(`[data-id="${CSS.escape(next.id)}"]`) as HTMLElement | null)?.focus());
  };

  const select = (key: keyof FindingFilters, label: string, values: readonly string[], text?: (v: string) => string) => (
    <label className="rp-field">
      {label}
      <select className="rp-select" value={filters[key]} onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}>
        <option value="">All</option>
        {values.map((v) => <option key={v} value={v}>{text ? text(v) : v}</option>)}
      </select>
    </label>
  );

  return (
    <div className="flex flex-col gap-3.5">
      {model.hub?.truncated && (
        <div className="rp-notice is-warn"><span><b>Truncated list.</b> The findings hub returned {fmt(model.findings.length)} of {fmt(model.hub.findingsTotal)} findings; the rest are not shown.</span></div>
      )}

      <section className="rp-panel flex flex-wrap items-end gap-3 px-[18px] py-3.5" role="group" aria-label="Filter findings">
        {options.layer.length > 0 && select("layer", "Layer", options.layer, layerName)}
        {options.owner.length > 0 && select("owner", "Owner", options.owner, (o) => `Owner ${o}`)}
        {select("check", "Check", options.check)}
        {select("confidence", "Confidence", options.confidence, cap)}
        <label className="rp-field min-w-[14rem] flex-1">
          Search
          <input type="search" className="rp-input" value={filters.text} placeholder="File, summary, identity or check"
            onChange={(e) => setFilters({ ...filters, text: e.target.value })} />
        </label>
        {hasComparison && (
          <label className="rp-check pb-2"><input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />New since previous scan</label>
        )}
        {filtered && <button type="button" className="rp-btn rp-btn-ghost" onClick={() => { setFilters(NO_FILTERS); setOnlyNew(false); }}>Clear filters</button>}
      </section>

      {shown.length === 0 ? (
        <section className="rp-panel">
          <p className="rp-empty">
            {model.findings.length ? "No findings match these filters."
              : "No findings in the evaluated scope. Unavailable, partial and not-applicable checks are not a clean bill of health: see Checks."}
          </p>
        </section>
      ) : (
        <div className="rp-split">
          <section className="rp-panel rp-list" aria-label="Findings">
            <div className="rp-panel-head">
              <div><h2>Findings</h2><p aria-live="polite">Showing {fmt(shown.length)} of {fmt(model.findings.length)}</p></div>
              <span className="rp-head-right text-[10.5px] text-[#6f7d83]">↑ ↓ to move</span>
            </div>
            <div ref={listRef} className="rp-list-scroll" role="listbox" aria-label="Findings" onKeyDown={onListKey}>
              {visible.map((f) => (
                <button
                  key={f.id}
                  data-id={f.id}
                  type="button"
                  role="option"
                  aria-selected={selected?.id === f.id}
                  tabIndex={selected?.id === f.id ? 0 : -1}
                  className="rp-row"
                  onClick={() => setSelectedId(f.id)}
                >
                  <span className="rp-row-top">
                    <ConfidenceDot confidence={f.confidence} />
                    <code>{f.checkId}</code>
                    <span className="rp-loc">{f.file ?? "no file"}{f.line ? `:${f.line}` : ""}</span>
                    {newIds.has(f.id) && <span className="rp-pill is-new ml-auto">New</span>}
                  </span>
                  <p>{f.summary}</p>
                </button>
              ))}
              {shown.length > visible.length && (
                <div className="p-2">
                  <button type="button" className="rp-btn w-full justify-center" onClick={() => setLimit(visible.length + PAGE)}>
                    Show {fmt(Math.min(PAGE, shown.length - visible.length))} more of {fmt(shown.length - visible.length)}
                  </button>
                </div>
              )}
            </div>
          </section>
          <section ref={detailRef} className="rp-panel rp-detail" aria-label="Selected finding">
            {selected && <FindingDetail key={selected.id} finding={selected} model={model} isNew={newIds.has(selected.id)} onCopied={onCopied} />}
          </section>
        </div>
      )}
    </div>
  );
}
