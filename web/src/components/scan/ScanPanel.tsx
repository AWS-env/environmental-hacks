"use client";

import { AlertTriangle, ArrowLeft, Settings2 } from "lucide-react";
import type { ScanSessionState } from "@/lib/scan-session";
import ScanResults from "./ScanResults";
import { useEscape } from "./ScanProgress";

function repoName(url: string): string {
  return url.replace(/^https:\/\/github\.com\//, "");
}

function Problem({ state }: { state: ScanSessionState }) {
  const kind = state.error?.kind;
  const unconfigured = state.phase === "unconfigured";
  const title = unconfigured ? "Scanning is not configured"
    : kind === "invalid_input" ? "That is not a public GitHub repository URL"
    : kind === "timeout" ? "The scan did not finish in time"
    : kind === "scan_error" ? "The scan failed"
    : kind === "http" ? "scan-api rejected the request"
    : kind === "network" ? "Could not reach scan-api"
    : "Something went wrong";
  const message = unconfigured
    ? "NEXT_PUBLIC_SCAN_API_URL was not set when this site was built, so there is no scan service to call. No results are shown rather than sample data."
    : state.error?.message;
  return (
    <div role="alert" className="mx-auto w-full max-w-[560px] liquid-glass relative rounded-[28px] border bg-black/45 p-6 md:p-8">
      <div className="flex items-start gap-3">
        {unconfigured ? <Settings2 className="mt-0.5 h-5 w-5 shrink-0 text-zinc-300" />
          : <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[#f7c062]" />}
        <div className="min-w-0">
          <h2 className="text-lg font-medium text-white">{title}</h2>
          <p className="mt-2 break-words text-sm leading-relaxed text-zinc-300">{message}</p>
          {state.error?.message.includes("HTTP 429") && (
            <p className="mt-2 text-sm text-zinc-400">scan-api is throttling requests. Wait a minute and try again.</p>
          )}
          <dl className="mt-4 space-y-1 text-xs text-zinc-400">
            <div><dt className="inline">Repository </dt><dd className="inline font-mono text-zinc-300">{state.repoUrl || "(empty)"}</dd></div>
            {state.scanId && <div><dt className="inline">Scan id </dt><dd className="inline font-mono text-zinc-300">{state.scanId}</dd></div>}
          </dl>
        </div>
      </div>
    </div>
  );
}

/** A finished scan: results (`done`) or the reason there are none. In-flight phases use ScanProgress. */
export default function ScanPanel({ state, onClose }: { state: ScanSessionState; onClose: () => void }) {
  useEscape(onClose);
  return (
    <section
      aria-label={`Scan of ${repoName(state.repoUrl)}`}
      className="relative flex flex-1 flex-col pt-4 text-white [text-shadow:0_2px_8px_rgba(0,0,0,.9)]"
    >
      <div className="mx-auto flex w-full max-w-[980px] flex-1 flex-col gap-6">
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={onClose}
            className="liquid-glass-button relative flex items-center gap-1.5 rounded-full border border-white/15 px-3.5 py-1.5 text-xs font-medium text-zinc-100 hover:border-white/30"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to repository
          </button>
          {state.phase === "done" && <span className="text-xs text-zinc-400">Live results</span>}
        </div>
        {state.phase === "done" ? <ScanResults state={state} />
          : <div className="flex flex-1 items-center pb-16"><Problem state={state} /></div>}
      </div>
    </section>
  );
}
