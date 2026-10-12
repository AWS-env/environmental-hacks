"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, ChevronDown, Download, ExternalLink, FileUp, Loader2, Plus, ScrollText } from "lucide-react";
import {
  NO_FILTERS,
  commitLink,
  fromReport,
  isScanReport,
  repoName,
  totals as totalsOf,
  type FindingFilters,
  type ReportModel,
} from "@/lib/report-model";
import {
  ReportLoadError,
  SAMPLE_REPORT_URL,
  compareWithPrevious,
  forgetScan,
  latestScan,
  loadScanReport,
  recordScan,
  useScanHistory,
  type LoadProgress,
  type ScanHistoryEntry,
} from "@/lib/report-store";
import ReportChecks from "./ReportChecks";
import ReportFindings from "./ReportFindings";
import ReportHistory from "./ReportHistory";
import ReportOverview from "./ReportOverview";
import ReportProvenance from "./ReportProvenance";
import { fmt, formatDate } from "./parts";

// Same rule as scan_api/store.py SCAN_ID (scan-api issues UUID4s).
const SCAN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

type Loaded =
  | { key: string; kind: "loading"; progress: LoadProgress | null }
  | { key: string; kind: "ready"; model: ReportModel }
  | { key: string; kind: "error"; title: string; message: string };

// ---------- frame ----------

function exportReport(model: ReportModel) {
  const blob = new Blob([JSON.stringify(model.raw, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${model.origin === "hub" ? "hub-scan" : "report"}-${repoName(model).replace(/[^A-Za-z0-9_.-]+/g, "_")}-${model.repository.commitSha.slice(0, 12)}.json`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** The page inside the sidebar layout: sticky header (actions + tabs), content, footer. */
function Frame({ model, onOpenFile, tabs, pageRef, children }: {
  model: ReportModel | null; onOpenFile?: (file: File) => void; tabs?: ReactNode;
  pageRef?: React.Ref<HTMLDivElement>; children: ReactNode;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <div className="rp-page" ref={pageRef}>
        <header className="rp-header">
          <div className="rp-topbar">
            <div className="flex min-w-0 items-center gap-3">
              <Link href="/" className="rp-brand">EcoAudit</Link>
              <span className="rp-eyebrow hidden sm:inline">/ Reports</span>
            </div>
            <div className="rp-actions">
              {onOpenFile && (
                <>
                  <button type="button" className="rp-btn" onClick={() => fileInput.current?.click()} title="Open a report.json from this computer">
                    <FileUp size={14} /><span className="rp-btn-label">Open report.json</span>
                  </button>
                  <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) onOpenFile(file);
                  }} />
                </>
              )}
              <button type="button" className="rp-btn" disabled={!model} onClick={() => model && exportReport(model)} title="Download the report being viewed">
                <Download size={14} /><span className="rp-btn-label">{model?.origin === "hub" ? "Export hub JSON" : "Export report.json"}</span>
              </button>
              <Link href="/home" className="rp-btn rp-btn-primary"><Plus size={14} /><span className="rp-btn-label">New scan</span></Link>
            </div>
          </div>
          {tabs}
        </header>
        <main className="rp-content">{children}</main>
        <footer className="rp-content rp-footer">
          <span>EcoAudit · read-only software sustainability auditor</span>
          <span>Static analysis only: repository code is never executed. A finding is evidence for a review, not proof of runtime cost.</span>
        </footer>
    </div>
  );
}

function Skeleton({ progress }: { progress: LoadProgress | null }) {
  const text = !progress ? "Loading the report…"
    : progress.kind === "fetching" ? "Fetching the scan from scan-api…"
    : progress.kind === "downloading" ? "Downloading the full report…"
    : progress.kind === "hub" ? "The full report is not available; checking the findings hub for its copy…"
    : `The scan of ${progress.repoUrl.replace(/^https:\/\/github\.com\//, "")} is ${progress.status === "queued" ? "queued" : "still running"}. The report opens here when it finishes; scans usually take a few minutes.`;
  return (
    <div aria-busy="true" className="flex flex-col gap-3.5 pt-6">
      <div role="status" className="rp-notice"><span className="flex items-center gap-3"><Loader2 size={15} className="animate-spin" aria-hidden="true" />{text}</span></div>
      <div className="rp-skeleton h-24" aria-hidden="true" />
      <div className="rp-grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => <div key={i} className="rp-skeleton h-36" />)}
      </div>
      <div className="rp-grid grid-cols-1 xl:grid-cols-[2fr_1fr]" aria-hidden="true">
        <div className="rp-skeleton h-80" /><div className="rp-skeleton h-80" />
      </div>
    </div>
  );
}

/** Server-rendered placeholder while the client reads the URL and loads the report. */
export function ReportsFallback() {
  return <Frame model={null}><Skeleton progress={null} /></Frame>;
}

function LoadError({ title, message, onForget }: { title: string; message: string; onForget?: () => void }) {
  return (
    <div className="pt-10">
      <section role="alert" className="rp-panel mx-auto max-w-2xl p-7">
        <div className="flex items-start gap-4">
          <span className="rp-icon text-[#f7c062]"><AlertTriangle size={18} strokeWidth={1.6} /></span>
          <div className="min-w-0">
            <h1 className="text-[17px] font-medium tracking-[-0.02em] text-[#f3f6f7]">{title}</h1>
            <p className="mt-2 break-words text-[13px] leading-relaxed text-[#b6c2c8]">{message}</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link href="/home" className="rp-btn rp-btn-primary">Scan a repository</Link>
              <Link href="/reports?sample=1" className="rp-btn">Open the sample report</Link>
              {onForget && <button type="button" className="rp-btn rp-btn-ghost" onClick={onForget}>Remove it from this browser&apos;s history</button>}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

// ---------- report ----------

type Tab = "overview" | "findings" | "checks" | "provenance" | "history";
const TABS: readonly Tab[] = ["overview", "findings", "checks", "provenance", "history"];

function Notice({ model, fileName }: { model: ReportModel; fileName: string | null }) {
  if (model.origin === "scan") return null;
  if (model.origin === "sample") {
    return (
      <div className="rp-notice">
        <span><b>Sample report.</b> A real scan of {repoName(model)} by scanner {model.scanner?.version}, run {formatDate(model.scannedAt)} and bundled with this site. Scan a repository to get your own report.</span>
        <Link href="/home" className="rp-btn rp-btn-sm rp-btn-primary">Scan a repository</Link>
      </div>
    );
  }
  if (model.origin === "file") {
    return <div className="rp-notice"><span><b>Opened from {fileName ?? "a file"}.</b> It stays on this computer: it is not uploaded or added to the scan history.</span></div>;
  }
  return (
    <div className="rp-notice is-warn">
      <span><b>Findings-hub copy.</b> The full report is not available: {model.hub?.reason}. The hub keeps only detector-validated check
        results and their findings, so pattern names, file counts, unavailable checks and the impact section are missing.</span>
    </div>
  );
}

function ScanPicker({ model, history, onOpenScan }: { model: ReportModel; history: ScanHistoryEntry[]; onOpenScan: (id: string) => void }) {
  const router = useRouter();
  const known = history.some((e) => e.scanId === model.scanId);
  const value = model.origin === "sample" ? "sample" : known && model.origin !== "file" ? model.scanId : "current";
  return (
    <div className="rp-picker">
      <label>
        <ScrollText size={16} className="shrink-0 text-[#c9d2d6]" aria-hidden="true" />
        <select aria-label="Choose a report" value={value} onChange={(e) => {
          if (e.target.value === "sample") router.push("/reports?sample=1");
          else if (e.target.value !== "current") onOpenScan(e.target.value);
        }}>
          {value === "current" && <option value="current">{repoName(model)} · {model.origin === "file" ? "opened file" : model.repository.commitSha.slice(0, 7)}</option>}
          {history.map((e) => (
            <option key={e.scanId} value={e.scanId}>{e.repoId.replace(/^github:/, "")} · {e.commitSha.slice(0, 7)} · {formatDate(e.scannedAt ?? e.recordedAt)}</option>
          ))}
          <option value="sample">Sample report · fastapi/full-stack-fastapi-template</option>
        </select>
        <ChevronDown size={14} />
      </label>
      <span>{history.length ? `${fmt(history.length)} scan${history.length === 1 ? "" : "s"} in this browser` : "No scans in this browser yet"}</span>
    </div>
  );
}

function ReportView({ model, fileName, alert, onOpenFile, onOpenScan }: {
  model: ReportModel; fileName: string | null; alert?: ReactNode; onOpenFile: (file: File) => void; onOpenScan: (scanId: string) => void;
}) {
  const history = useScanHistory();
  const totals = useMemo(() => totalsOf(model), [model]);
  const comparison = useMemo(() => (model.origin === "scan" || model.origin === "hub" ? compareWithPrevious(model, history) : null), [model, history]);
  const newIds = useMemo(() => new Set(comparison?.added ?? []), [comparison]);
  const [tab, setTab] = useState<Tab>(() => {
    const hash = typeof window === "undefined" ? "" : window.location.hash.slice(1);
    return (TABS as readonly string[]).includes(hash) ? (hash as Tab) : "overview";
  });
  const [filters, setFilters] = useState<FindingFilters>(NO_FILTERS);
  const [onlyNew, setOnlyNew] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const pageRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => { document.title = `${repoName(model)} · Report · EcoAudit`; }, [model]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const go = (next: Tab, focus = false) => {
    setTab(next);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${next}`);
    if (focus) tabRefs.current[next]?.focus();
    // Bring the start of the section up under the sticky header if the reader had scrolled past it.
    const page = pageRef.current, panel = panelRef.current;
    if (page && panel) {
      const header = page.querySelector(".rp-header")?.getBoundingClientRect().height ?? 0;
      const top = panel.getBoundingClientRect().top - page.getBoundingClientRect().top + page.scrollTop - header - 12;
      if (page.scrollTop > top) page.scrollTo({ top });
    }
  };
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.indexOf(tab);
    const next = event.key === "ArrowRight" ? TABS[(i + 1) % TABS.length]
      : event.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length]
      : event.key === "Home" ? TABS[0] : event.key === "End" ? TABS[TABS.length - 1] : null;
    if (next) { event.preventDefault(); go(next, true); }
  };
  const notify = (text: string) => {
    setToast(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2500);
  };
  const openCheck = (check: string) => { setFilters({ ...NO_FILTERS, check }); setOnlyNew(false); setSelectedId(null); go("findings"); };
  const openFinding = (id: string) => { setFilters(NO_FILTERS); setOnlyNew(false); setSelectedId(id); go("findings"); };

  const labels: Record<Tab, [string, number | null]> = {
    overview: ["Overview", null],
    findings: ["Findings", totals.findings],
    checks: ["Checks", totals.checks],
    provenance: ["Provenance", null],
    history: ["History", history.length],
  };
  const commit = commitLink(model);

  const tabs = (
    <div role="tablist" aria-label="Report sections" className="rp-tabs">
      {TABS.map((t) => (
        <button key={t} ref={(el) => { tabRefs.current[t] = el; }} id={`tab-${t}`} role="tab" type="button"
          aria-selected={tab === t} aria-controls={tab === t ? "report-panel" : undefined} tabIndex={tab === t ? 0 : -1}
          className="rp-tab" onClick={() => go(t)} onKeyDown={onTabKey}>
          {labels[t][0]}
          {labels[t][1] !== null && <span className="rp-count">{fmt(labels[t][1])}</span>}
        </button>
      ))}
    </div>
  );

  return (
    <Frame model={model} onOpenFile={onOpenFile} tabs={tabs} pageRef={pageRef}>
      <div className="rp-heading">
        <div className="min-w-0">
          <div className="rp-eyebrow">Compute-waste report · static, read-only scan</div>
          <div className="rp-title">
            <h1>{model.repository.url ? <a href={model.repository.url} target="_blank" rel="noreferrer">{repoName(model)}</a> : repoName(model)}</h1>
            <span className={`rp-source ${model.origin === "scan" ? "is-live" : model.origin === "hub" ? "is-warn" : ""}`}>
              <i />{model.origin === "scan" ? "Live scan" : model.origin === "sample" ? "Sample" : model.origin === "hub" ? "Hub copy" : "Local file"}
            </span>
          </div>
          <div className="rp-meta">
            <span>Commit {commit
              ? <a href={commit} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono"><b>{model.repository.commitSha.slice(0, 12)}</b><ExternalLink size={11} /></a>
              : <b className="font-mono">{model.repository.commitSha.slice(0, 12)}</b>}</span>
            <span>Scanned <b>{model.scannedAt ? formatDate(model.scannedAt) : "not recorded"}</b></span>
            {model.scanner && <span>Scanner <b>{model.scanner.version}</b></span>}
            {model.files && <span><b>{fmt(model.files.collected)}</b> files</span>}
          </div>
        </div>
        <ScanPicker model={model} history={history} onOpenScan={onOpenScan} />
      </div>

      {(alert || model.origin !== "scan") && <div className="mb-3.5 flex flex-col gap-2.5">{alert}<Notice model={model} fileName={fileName} /></div>}

      <div ref={panelRef} role="tabpanel" id="report-panel" aria-labelledby={`tab-${tab}`} tabIndex={-1} className="focus:outline-none">
        {tab === "overview" && (
          <ReportOverview model={model} totals={totals} comparison={comparison} onPickCheck={openCheck} onPickFinding={openFinding}
            onShowNew={() => { setFilters(NO_FILTERS); setOnlyNew(true); setSelectedId(null); go("findings"); }}
            onShowHistory={() => go("history")} />
        )}
        {tab === "findings" && (
          <ReportFindings model={model} filters={filters} setFilters={setFilters} onlyNew={onlyNew} setOnlyNew={setOnlyNew}
            newIds={newIds} hasComparison={Boolean(comparison && comparison.added.length)} selectedId={selectedId} setSelectedId={setSelectedId}
            onCopied={(ok) => notify(ok ? "Prompt copied. Paste it into your coding agent." : "Copy failed: open the prompt and copy it by hand.")} />
        )}
        {tab === "checks" && <ReportChecks model={model} totals={totals} onPickCheck={openCheck} />}
        {tab === "provenance" && <ReportProvenance model={model} />}
        {tab === "history" && <ReportHistory model={model} history={history} onOpen={onOpenScan} onForget={forgetScan} />}
      </div>

      {toast && <div role="status" className="rp-toast">{toast}</div>}
    </Frame>
  );
}

// ---------- source selection ----------

/**
 * Which report to show: `?scan=<id>` (a scan-api scan), `?sample=1` (the bundled sample), a report.json
 * opened from disk, or with no parameters the latest scan seen in this browser, falling back to the sample.
 */
export default function ReportsClient() {
  const router = useRouter();
  const params = useSearchParams();
  const scanParam = params.get("scan");
  const wantsSample = params.has("sample");
  const sourceKey = scanParam !== null ? `scan:${scanParam}` : wantsSample ? "sample" : "latest";
  const invalidScan = scanParam !== null && !SCAN_ID.test(scanParam);

  const history = useScanHistory();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A file opened from disk replaces the report until the reader navigates to another source.
  const [openedFile, setOpened] = useState<{ key: string; model: ReportModel; name: string } | null>(null);
  const opened = openedFile?.key === sourceKey ? openedFile : null;
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    if (invalidScan) return;
    const controller = new AbortController();
    const { signal } = controller;
    const fail = (title: string, message: string) => { if (!signal.aborted) setLoaded({ key: sourceKey, kind: "error", title, message }); };

    if (sourceKey === "latest") {
      const latest = latestScan();
      if (latest) {
        router.replace(`/reports?scan=${encodeURIComponent(latest.scanId)}`);
        return () => controller.abort();
      }
    }

    if (scanParam !== null) {
      loadScanReport(scanParam, signal, (progress) => { if (!signal.aborted) setLoaded({ key: sourceKey, kind: "loading", progress }); })
        .then((model) => {
          if (signal.aborted) return;
          recordScan(model);
          setLoaded({ key: sourceKey, kind: "ready", model });
        })
        .catch((error: unknown) => {
          if (signal.aborted) return;
          if (error instanceof ReportLoadError) fail(error.title, error.message);
          else fail("Could not load the report", error instanceof Error ? error.message : String(error));
        });
    } else {
      fetch(SAMPLE_REPORT_URL, { signal })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json() as Promise<unknown>;
        })
        .then((data) => {
          if (signal.aborted) return;
          if (!isScanReport(data)) throw new Error("the bundled file is not a scanner report.json");
          setLoaded({ key: sourceKey, kind: "ready", model: fromReport(data, "sample") });
        })
        .catch((error: unknown) => {
          if (!signal.aborted) fail("Could not load the sample report", error instanceof Error ? error.message : String(error));
        });
    }
    return () => controller.abort();
  }, [sourceKey, scanParam, invalidScan, router]);

  const openFile = (file: File) => {
    file.text()
      .then((text) => {
        const data: unknown = JSON.parse(text);
        if (!isScanReport(data)) throw new Error("it is not a scanner report.json (missing findings, checks or summary)");
        setFileError(null);
        setOpened({ key: sourceKey, model: fromReport(data, "file"), name: file.name });
      })
      .catch((error: unknown) => setFileError(`Could not open ${file.name}: ${error instanceof Error ? error.message : String(error)}`));
  };

  const openScan = (scanId: string) => {
    setOpened(null);
    router.push(`/reports?scan=${encodeURIComponent(scanId)}`);
  };

  const current = loaded?.key === sourceKey ? loaded : null;
  const fileAlert = fileError ? (
    <div role="alert" className="rp-notice is-error">
      <span>{fileError}</span>
      <button type="button" className="rp-btn rp-btn-sm" onClick={() => setFileError(null)}>Dismiss</button>
    </div>
  ) : null;

  if (opened) {
    return <ReportView key={`file:${opened.name}:${opened.model.scanId}`} model={opened.model} fileName={opened.name} alert={fileAlert} onOpenFile={openFile} onOpenScan={openScan} />;
  }
  if (!invalidScan && current?.kind === "ready") {
    return <ReportView key={`${sourceKey}:${current.model.scanId}`} model={current.model} fileName={null} alert={fileAlert} onOpenFile={openFile} onOpenScan={openScan} />;
  }

  let body: ReactNode;
  if (invalidScan) {
    body = <LoadError title="That is not a scan id" message={`"${scanParam}" is not an id issued by scan-api. Open a report from the home page or the scan history.`} />;
  } else if (current?.kind === "error") {
    const inHistory = scanParam !== null && history.some((e) => e.scanId === scanParam);
    body = <LoadError title={current.title} message={current.message}
      onForget={inHistory && scanParam ? () => { forgetScan(scanParam); router.replace("/reports"); } : undefined} />;
  } else {
    body = <Skeleton progress={current?.kind === "loading" ? current.progress : null} />;
  }
  return <Frame model={null} onOpenFile={openFile}>{fileAlert && <div className="mt-4">{fileAlert}</div>}{body}</Frame>;
}
