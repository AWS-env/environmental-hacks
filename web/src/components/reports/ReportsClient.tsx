"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, ExternalLink, FileUp, Loader2 } from "lucide-react";
import ThemeToggle from "@/components/theme/ThemeToggle";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";
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
} from "@/lib/report-store";
import ReportCoverage from "./ReportCoverage";
import ReportFindings from "./ReportFindings";
import ReportHistory from "./ReportHistory";
import ReportSummary from "./ReportSummary";
import { card, fmt, formatDate, plural } from "./parts";

// Same rule as scan_api/store.py SCAN_ID (scan-api issues UUID4s).
const SCAN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

type Loaded =
  | { key: string; kind: "loading"; progress: LoadProgress | null }
  | { key: string; kind: "ready"; model: ReportModel }
  | { key: string; kind: "error"; title: string; message: string };

// ---------- frame ----------

function Header({ model, onOpenFile }: { model: ReportModel | null; onOpenFile?: (file: File) => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const exportReport = () => {
    if (!model) return;
    const blob = new Blob([JSON.stringify(model.raw, null, 1)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const kind = model.origin === "hub" ? "hub-scan" : "report";
    a.download = `${kind}-${repoName(model).replace(/[^A-Za-z0-9_.-]+/g, "_")}-${model.repository.commitSha.slice(0, 12)}.json`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return (
    <header className="relative z-20 border-b border-line bg-surface-2/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4 md:px-6">
        <Link href="/" className="flex items-center gap-3 transition-opacity hover:opacity-90">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 shadow-lg shadow-emerald-500/20">
            <svg className="h-5 w-5 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </span>
          <span>
            <span className="text-base font-bold tracking-tight text-ink">EcoAudit</span>
            <span className="ml-2 font-mono text-xs text-ink-3">/reports</span>
          </span>
        </Link>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <Link href="/home" className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink">
            New scan
          </Link>
          {onOpenFile && (
            <>
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              >
                <FileUp className="h-3.5 w-3.5" aria-hidden="true" />
                Open report.json
              </button>
              <input
                ref={fileInput}
                type="file"
                accept=".json,application/json"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) onOpenFile(file);
                }}
              />
            </>
          )}
          <ThemeToggle />
          <Magnetic>
            <button
              type="button"
              onClick={exportReport}
              disabled={!model}
              className="group flex items-center gap-2 rounded-lg bg-accent px-3.5 py-1.5 text-xs font-semibold text-on-accent shadow-sm transition-all hover:bg-accent-hover active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RollText>{model?.origin === "hub" ? "Export hub JSON" : "Export report.json"}</RollText>
            </button>
          </Magnetic>
        </div>
      </div>
    </header>
  );
}

function Frame({ model, onOpenFile, children }: { model: ReportModel | null; onOpenFile?: (file: File) => void; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col justify-between bg-page text-ink selection:bg-accent selection:text-on-accent">
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />
      <div aria-hidden="true" className="pointer-events-none fixed -left-40 -top-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div aria-hidden="true" className="pointer-events-none fixed -right-40 top-1/3 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />
      <Header model={model} onOpenFile={onOpenFile} />
      <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-4 py-8 md:px-6">{children}</main>
      <footer className="relative z-10 border-t border-line bg-surface-2/60 py-6 text-xs text-ink-4">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 md:px-6">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink">EcoAudit</span>
            <span aria-hidden="true">•</span>
            <span>Read-only software sustainability auditor</span>
          </div>
          <span className="text-[11px]">Static analysis only: repository code is never executed. Findings are evidence for a review, not proof of runtime cost.</span>
        </div>
      </footer>
    </div>
  );
}

function Skeleton({ progress }: { progress: LoadProgress | null }) {
  const text = !progress ? "Loading the report…"
    : progress.kind === "fetching" ? "Fetching the scan from scan-api…"
    : progress.kind === "downloading" ? "Downloading the full report (large reports come from storage)…"
    : progress.kind === "hub" ? "The full report is not available; checking the findings hub for its copy…"
    : `The scan of ${progress.repoUrl.replace(/^https:\/\/github\.com\//, "")} is ${progress.status === "queued" ? "queued" : "still running"}. This page opens the report when it finishes; scans usually take a few minutes.`;
  return (
    <div aria-busy="true">
      <p role="status" className={`${card} mb-6 flex items-center gap-3 p-4 text-sm text-ink-2`}>
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent-strong" aria-hidden="true" />
        {text}
      </p>
      <div className="animate-pulse space-y-6" aria-hidden="true">
        <div className="h-10 w-2/3 rounded-xl bg-surface-3" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-24 rounded-2xl bg-surface-3" />)}
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="h-72 rounded-2xl bg-surface-3" />
          <div className="h-72 rounded-2xl bg-surface-3" />
        </div>
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
    <div role="alert" className={`${card} mx-auto max-w-2xl p-6 md:p-8`}>
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <div className="min-w-0">
          <h1 className="text-lg font-bold text-ink">{title}</h1>
          <p className="mt-2 break-words text-sm leading-relaxed text-ink-2">{message}</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href="/home" className="rounded-lg bg-accent px-3.5 py-2 text-xs font-semibold text-on-accent hover:bg-accent-hover">Scan a repository</Link>
            <Link href="/reports?sample=1" className="rounded-lg border border-line bg-surface px-3.5 py-2 text-xs font-semibold text-ink-2 hover:bg-surface-3 hover:text-ink">Open the sample report</Link>
            {onForget && (
              <button type="button" onClick={onForget} className="rounded-lg px-3.5 py-2 text-xs font-semibold text-ink-3 hover:bg-surface-3 hover:text-ink">
                Remove it from this browser&apos;s history
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- report ----------

type Tab = "summary" | "findings" | "coverage" | "history";
const TABS: readonly Tab[] = ["summary", "findings", "coverage", "history"];

function Banner({ model, fileName }: { model: ReportModel; fileName: string | null }) {
  if (model.origin === "scan") return null;
  const tone = "mb-6 flex flex-col gap-3 rounded-xl border p-4 text-xs leading-relaxed sm:flex-row sm:items-center sm:justify-between";
  if (model.origin === "sample") {
    return (
      <div className={`${tone} border-accent-line bg-accent-soft text-accent-ink`}>
        <p>
          <strong className="font-bold">Sample report.</strong> A real scan of {repoName(model)} by scanner {model.scanner?.version}
          , run {formatDate(model.scannedAt)} and bundled with this site. Scan a repository to get your own live report.
        </p>
        <Link href="/home" className="shrink-0 self-start rounded-lg bg-accent px-3 py-1.5 font-semibold text-on-accent hover:bg-accent-hover sm:self-auto">Scan a repository</Link>
      </div>
    );
  }
  if (model.origin === "file") {
    return (
      <div className={`${tone} border-line bg-surface-2 text-ink-2`}>
        <p><strong className="font-bold text-ink">Opened from {fileName ?? "a file"}.</strong> The report stays on this computer: it is not uploaded or added to the scan history.</p>
      </div>
    );
  }
  return (
    <div className={`${tone} border-amber-500/40 bg-amber-500/10 text-ink`}>
      <p>
        <strong className="font-bold">Findings-hub copy.</strong> The full report is not available: {model.hub?.reason}. The hub keeps only
        detector-validated check results and their findings, so pattern names, file counts, unavailable checks and the impact section are missing.
      </p>
    </div>
  );
}

function ReportView({ model, fileName, onOpenScan }: { model: ReportModel; fileName: string | null; onOpenScan: (scanId: string) => void }) {
  const history = useScanHistory();
  const totals = useMemo(() => totalsOf(model), [model]);
  const comparison = useMemo(() => (model.origin === "scan" || model.origin === "hub" ? compareWithPrevious(model, history) : null), [model, history]);
  const newIds = useMemo(() => new Set(comparison?.added ?? []), [comparison]);
  const [tab, setTab] = useState<Tab>(() => {
    const hash = typeof window === "undefined" ? "" : window.location.hash.slice(1);
    return (TABS as readonly string[]).includes(hash) ? (hash as Tab) : "summary";
  });
  const [filters, setFilters] = useState<FindingFilters>(NO_FILTERS);
  const [onlyNew, setOnlyNew] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ summary: null, findings: null, coverage: null, history: null });

  useEffect(() => {
    document.title = `${repoName(model)} · Report · EcoAudit`;
  }, [model]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const go = (next: Tab, focus = false) => {
    setTab(next);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${next}`);
    if (focus) tabRefs.current[next]?.focus();
  };
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.indexOf(tab);
    const next = event.key === "ArrowRight" ? TABS[(i + 1) % TABS.length]
      : event.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length]
      : event.key === "Home" ? TABS[0] : event.key === "End" ? TABS[TABS.length - 1] : null;
    if (next) {
      event.preventDefault();
      go(next, true);
    }
  };
  const notify = (text: string) => {
    setToast(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2500);
  };

  const labels: Record<Tab, string> = {
    summary: "Summary",
    findings: `Findings (${fmt(totals.findings)})`,
    coverage: "Coverage & provenance",
    history: `Scan history (${fmt(history.length)})`,
  };
  const commit = commitLink(model);
  const evaluated = totals.byStatus.completed + totals.byStatus.partial;

  return (
    <>
      <Banner model={model} fileName={fileName} />

      <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div className="min-w-0">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong">
            <span className="h-1.5 w-1.5 rounded-full bg-accent-strong" aria-hidden="true" />
            Compute-waste report · static, read-only scan
          </div>
          <h1 className="break-words text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            {model.repository.url ? (
              <a href={model.repository.url} target="_blank" rel="noreferrer" className="decoration-line underline-offset-4 hover:underline">
                {repoName(model)}
              </a>
            ) : repoName(model)}
          </h1>
          <p className="mt-2 max-w-3xl text-xs text-ink-3 md:text-sm">
            {plural(totals.findings, "finding")} from {fmt(evaluated)} evaluated checks
            {model.files && ` across ${fmt(model.files.collected)} files`}. Each one cites the exact source line and comes with a
            prompt for your coding agent.
          </p>
        </div>
        <div className={`${card} shrink-0 p-3.5 text-xs`}>
          <div className="flex items-center justify-between gap-4">
            <span className="font-mono text-[11px] text-ink-3">Commit</span>
            {commit ? (
              <a href={commit} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono font-semibold text-ink hover:underline">
                {model.repository.commitSha.slice(0, 12)} <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
            ) : <span className="font-mono font-semibold text-ink">{model.repository.commitSha.slice(0, 12)}</span>}
          </div>
          <div className="mt-1 flex items-center justify-between gap-4">
            <span className="font-mono text-[11px] text-ink-3">Scanned</span>
            <span className="font-medium text-ink">{model.scannedAt ? formatDate(model.scannedAt) : "not recorded"}</span>
          </div>
          {model.scanner && (
            <div className="mt-1 flex items-center justify-between gap-4">
              <span className="font-mono text-[11px] text-ink-3">Scanner</span>
              <span className="font-medium text-ink">{model.scanner.version}</span>
            </div>
          )}
        </div>
      </div>

      <div role="tablist" aria-label="Report sections" className="mb-6 flex flex-wrap gap-2 border-b border-line pb-3">
        {TABS.map((t) => (
          <button
            key={t}
            ref={(el) => { tabRefs.current[t] = el; }}
            id={`tab-${t}`}
            role="tab"
            type="button"
            aria-selected={tab === t}
            aria-controls={tab === t ? "report-panel" : undefined}
            tabIndex={tab === t ? 0 : -1}
            onClick={() => go(t)}
            onKeyDown={onTabKey}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-strong ${
              tab === t ? "bg-accent text-on-accent shadow-sm" : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
            }`}
          >
            {labels[t]}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="report-panel" aria-labelledby={`tab-${tab}`} tabIndex={0} className="focus-visible:outline-none">
        {tab === "summary" && (
          <ReportSummary
            model={model}
            totals={totals}
            comparison={comparison}
            onPickCheck={(check) => { setFilters({ ...NO_FILTERS, check }); setOnlyNew(false); go("findings"); }}
            onShowNew={() => { setFilters(NO_FILTERS); setOnlyNew(true); go("findings"); }}
            onShowHistory={() => go("history")}
          />
        )}
        {tab === "findings" && (
          <ReportFindings
            model={model}
            filters={filters}
            setFilters={setFilters}
            onlyNew={onlyNew}
            setOnlyNew={setOnlyNew}
            newIds={newIds}
            hasComparison={Boolean(comparison && comparison.added.length)}
            onCopied={(ok) => notify(ok ? "Prompt copied. Paste it into your coding agent." : "Copy failed: open the prompt and copy it by hand.")}
          />
        )}
        {tab === "coverage" && <ReportCoverage model={model} totals={totals} />}
        {tab === "history" && <ReportHistory model={model} history={history} onOpen={onOpenScan} onForget={forgetScan} />}
      </div>

      {toast && (
        <div role="status" className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-xl border border-accent-line bg-surface-3/95 px-4 py-3 text-xs font-medium text-ink shadow-2xl backdrop-blur-md">
          {toast}
        </div>
      )}
    </>
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
        window.scrollTo({ top: 0 });
      })
      .catch((error: unknown) => setFileError(`Could not open ${file.name}: ${error instanceof Error ? error.message : String(error)}`));
  };

  const openScan = (scanId: string) => {
    setOpened(null);
    router.push(`/reports?scan=${encodeURIComponent(scanId)}`);
  };

  const current = loaded?.key === sourceKey ? loaded : null;
  const model = opened?.model ?? (current?.kind === "ready" ? current.model : null);

  let body: ReactNode;
  if (opened) body = <ReportView key={`file:${opened.name}:${opened.model.scanId}`} model={opened.model} fileName={opened.name} onOpenScan={openScan} />;
  else if (invalidScan) body = <LoadError title="That is not a scan id" message={`"${scanParam}" is not an id issued by scan-api. Open a report from the home page or the scan history.`} />;
  else if (current?.kind === "ready") body = <ReportView key={`${sourceKey}:${current.model.scanId}`} model={current.model} fileName={null} onOpenScan={openScan} />;
  else if (current?.kind === "error") {
    const inHistory = scanParam !== null && history.some((e) => e.scanId === scanParam);
    body = <LoadError title={current.title} message={current.message}
      onForget={inHistory && scanParam ? () => { forgetScan(scanParam); router.replace("/reports"); } : undefined} />;
  }
  else body = <Skeleton progress={current?.kind === "loading" ? current.progress : null} />;

  return (
    <Frame model={model} onOpenFile={openFile}>
      {fileError && (
        <p role="alert" className="mb-6 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-ink">
          {fileError}
          <button type="button" onClick={() => setFileError(null)} className="ml-3 font-semibold underline">Dismiss</button>
        </p>
      )}
      {body}
    </Frame>
  );
}
