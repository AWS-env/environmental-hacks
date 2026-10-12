"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy, ExternalLink, Lightbulb, Sparkles } from "lucide-react";
import type { Evidence } from "@/lib/scan-api";
import { highlightLine, languageOf, type TokenKind } from "@/lib/highlight";
import { evidenceLabel, evidenceText } from "@/lib/agent-prompt";

/**
 * One result (a finding, or a check without findings) on the post-analysis view. Details on the left;
 * an optional inset panel (recommendation or notes) on the right from lg up, stacked below on smaller screens.
 * Pass `aside={null}` and the card is a single column, so a result with nothing to recommend has no empty panel.
 */
export function ResultCard({ heading = 3, marker, badge, location, locationHref, title, children, aside }: {
  heading?: 3 | 4;
  marker: ReactNode;
  badge: ReactNode;
  location?: string | null;
  locationHref?: string | null;
  title: string;
  children?: ReactNode;
  aside: ReactNode | null;
}) {
  const Heading = heading === 4 ? "h4" : "h3";
  return (
    <article className={`liquid-glass scan-card scan-result relative rounded-[22px] border p-5 md:p-6 ${aside ? "lg:grid lg:grid-cols-[minmax(0,65fr)_minmax(0,35fr)] lg:gap-6" : ""}`}>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2.5">
          {marker}
          {badge}
          {location && (locationHref ? (
            <a href={locationHref} title={location} target="_blank" rel="noreferrer"
              className="group/loc inline-flex min-w-0 items-center gap-1 font-mono text-xs text-zinc-400 transition-colors hover:text-zinc-100">
              <span className="truncate">{location}</span>
              <ExternalLink className="h-3 w-3 shrink-0 opacity-60 group-hover/loc:opacity-100" />
            </a>
          ) : <span title={location} className="min-w-0 truncate font-mono text-xs text-zinc-400">{location}</span>)}
        </div>
        <Heading className="mt-3 text-[15px] font-semibold leading-snug tracking-tight text-white">{title}</Heading>
        {children}
      </div>
      {aside && <div className="mt-5 min-w-0 lg:mt-0">{aside}</div>}
    </article>
  );
}

/** The "01" circle that numbers findings within a check. */
export function IndexMarker({ n }: { n: number }) {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/15 bg-white/[0.03] font-mono text-[11px] tabular-nums text-zinc-300">
      {String(n).padStart(2, "0")}
    </span>
  );
}

/** A small status dot in the colour of the result's state (same palette as the badges). */
export function DotMarker({ className }: { className: string }) {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/10">
      <span className={`h-1.5 w-1.5 rounded-full ${className}`} />
    </span>
  );
}

export function Badge({ className, children }: { className: string; children: ReactNode }) {
  return <span className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>{children}</span>;
}

/** Copies text, with a fallback for browsers that refuse the async clipboard; `copied` stays true briefly. */
function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = useCallback((text: string) => {
    const done = () => {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1800);
    };
    const fallback = () => {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.cssText = "position:fixed;opacity:0;pointer-events:none";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      if (ok) done();
    };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }, []);
  return [copied, copy];
}

const TOKEN_CLASS: Record<TokenKind, string> = {
  plain: "",
  keyword: "text-[#b9c7ee]",
  string: "text-[#a9d4b2]",
  number: "text-[#f0c27b]",
  comment: "italic text-zinc-500",
};

/** Code evidence: label bar with a copy button, line-number gutter, highlighted code that scrolls sideways. */
export function CodeEvidence({ item }: { item: Evidence }) {
  const [copied, copy] = useCopy();
  const text = evidenceText(item);
  // Shown without the indentation every line shares, so a nested snippet starts at the gutter;
  // the copy button and the agent prompt keep the exact text.
  const raw = text.split("\n");
  const indent = Math.min(...raw.filter((l) => l.trim()).map((l) => l.match(/^[ \t]*/)![0].length), Infinity);
  const lines = Number.isFinite(indent) && indent > 0 ? raw.map((l) => l.slice(indent)) : raw;
  const lang = item.kind === "static" ? languageOf(item.locator) : "json";
  return (
    <figure className="mt-4 overflow-hidden rounded-xl border border-white/[0.08] bg-black/45">
      <figcaption className="flex items-center gap-3 border-b border-white/[0.07] py-1.5 pl-3 pr-1.5">
        <span title={evidenceLabel(item)} className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500">{evidenceLabel(item)}</span>
        <button type="button" onClick={() => copy(text)} aria-label={copied ? "Code copied" : "Copy code"}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200">
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-300" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </figcaption>
      <pre className="overflow-x-auto py-2.5 font-mono text-xs leading-relaxed text-zinc-200">
        <code className="grid min-w-max grid-cols-[auto_1fr]">
          {lines.map((line, i) => (
            <span key={i} className="contents">
              <span className="select-none border-r border-white/[0.06] pl-3 pr-3 text-right tabular-nums text-zinc-600">
                {item.line_start ? item.line_start + i : i + 1}
              </span>
              <span className="whitespace-pre pl-3 pr-4">
                {highlightLine(line, lang).map((t, j) => t.kind === "plain" ? t.text : <span key={j} className={TOKEN_CLASS[t.kind]}>{t.text}</span>)}
                {line ? null : " "}
              </span>
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}

/** "arxiv.org/abs/2108.04639" for a reference link; the full URL stays in href and title. */
export function referenceLabel(url: string): string {
  try {
    const { hostname, pathname } = new URL(url);
    const label = hostname.replace(/^www\./, "") + pathname.replace(/\/$/, "");
    return label.length > 42 ? `${label.slice(0, 40)}…` : label;
  } catch {
    return url;
  }
}

/** The inset panel on a finding: what to do, a prompt for a coding agent, and the sources. */
export function RecommendationPanel({ recommendation, references, prompt }: { recommendation: string; references: string[]; prompt: string }) {
  const [copied, copy] = useCopy();
  return (
    <aside className="flex h-full flex-col rounded-2xl border border-white/[0.07] bg-white/[0.025] p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <Lightbulb className="h-3.5 w-3.5 shrink-0 text-[#f7c062]" />
        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-400 sm:tracking-[0.2em]">Recommendation</span>
        <button type="button" onClick={() => copy(prompt)}
          aria-label={copied ? "Prompt copied" : "Copy a prompt for a coding agent"}
          className={`ml-auto inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
            copied ? "border-emerald-300/30 text-emerald-200" : "border-white/12 text-zinc-300 hover:border-white/25 hover:text-white"}`}>
          {copied ? <Check className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
          <span aria-live="polite">{copied ? "Copied" : "Copy prompt"}</span>
        </button>
      </div>
      {recommendation && <p className="mt-3 text-[13px] leading-relaxed text-zinc-200">{recommendation}</p>}
      {references.length > 0 && <div aria-hidden="true" className="min-h-4 flex-1" />}
      {references.length > 0 && (
        <ul className="flex flex-col gap-1.5 border-t border-white/[0.06] pt-3">
          {references.map((url) => (
            <li key={url} className="min-w-0">
              <a href={url} title={url} target="_blank" rel="noreferrer"
                className="group/ref flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-400 transition-colors hover:text-zinc-100">
                <ExternalLink className="h-3 w-3 shrink-0 opacity-60 group-hover/ref:opacity-100" />
                <span className="truncate">{referenceLabel(url)}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

/** The inset panel on a check without findings: why it has that status, and its limitations. */
export function NotesPanel({ label, notes, total }: { label: string; notes: string[]; total: number }) {
  const [open, setOpen] = useState(false);
  const shown = open ? notes : notes.slice(0, 3);
  return (
    <aside className="h-full rounded-2xl border border-white/[0.07] bg-white/[0.025] p-4">
      <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-zinc-400">{label}</span>
      <ul className="mt-2.5 list-disc space-y-1.5 pl-4 text-xs leading-relaxed text-zinc-300 marker:text-zinc-600">
        {shown.map((note, i) => <li key={i}>{note}</li>)}
      </ul>
      {(notes.length > 3 || total > notes.length) && (
        <p className="mt-2.5 text-[11px] text-zinc-500">
          {notes.length > 3 && (
            <button type="button" onClick={() => setOpen(!open)} className="text-zinc-300 underline decoration-white/20 underline-offset-2 hover:text-white">
              {open ? "Show fewer" : `Show all ${notes.length}`}
            </button>
          )}
          {total > notes.length && <span>{notes.length > 3 ? " · " : ""}{total} in total, grouped</span>}
        </p>
      )}
    </aside>
  );
}
