"use client";

import React, { useState } from "react";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";

export default function AuditDropzone() {
  const [reportState, setReportState] = useState<"idle" | "loaded">("idle");
  const [fileName, setFileName] = useState<string>("");
  const [dragging, setDragging] = useState(false);

  const handleLoadSample = () => {
    setFileName("sample-ecommerce-api-report.json");
    setReportState("loaded");
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setFileName(file.name);
      setReportState("loaded");
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      setFileName(file.name);
      setReportState("loaded");
    }
  };

  return (
    <section id="audit" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Interactive Audit Report
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            Inspect an Audit Report Locally
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            Drop an exported <code className="font-mono text-ink text-sm bg-surface px-1.5 py-0.5 rounded border border-line">report.json</code> from your CI pipeline or explore our pre-computed e-commerce benchmark audit right in your browser.
          </p>
        </div>

        {reportState === "idle" ? (
          /* Dropzone Box */
          <div
            data-reveal
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            className={`rounded-3xl border-2 border-dashed p-12 text-center transition-all duration-300 ${
              dragging
                ? "border-accent bg-accent-soft/30 scale-[1.01]"
                : "border-line bg-surface hover:border-accent-line hover:bg-surface-2"
            }`}
          >
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-accent-soft border border-accent-line text-accent-strong mb-6">
              <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>

            <h3 className="text-xl font-bold text-ink mb-2">Drop your report.json file here</h3>
            <p className="text-xs text-ink-3 max-w-md mx-auto mb-6">
              All parsing happens client-side in your browser. No files or code are uploaded to external servers.
            </p>

            <div className="flex flex-wrap items-center justify-center gap-4">
              <label className="cursor-pointer">
                <input type="file" accept=".json" onChange={handleFileUpload} className="hidden" />
                <span className="inline-block rounded-xl bg-accent px-5 py-2.5 text-xs font-semibold text-on-accent shadow-md shadow-accent/20 hover:bg-accent-hover transition-colors">
                  Browse Files
                </span>
              </label>

              <Magnetic>
                <button
                  type="button"
                  onClick={handleLoadSample}
                  className="rounded-xl border border-line bg-surface-3 px-5 py-2.5 text-xs font-semibold text-ink hover:bg-surface hover:text-ink-2 transition-colors"
                >
                  <RollText>Load Sample Demo Report</RollText>
                </button>
              </Magnetic>
            </div>
          </div>
        ) : (
          /* Loaded Report Preview */
          <div
            data-reveal
            className="rounded-3xl border border-line bg-surface p-8 md:p-10 backdrop-blur-xl shadow-xl space-y-8"
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-line">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-500 font-bold border border-emerald-500/20">
                  ✓
                </div>
                <div>
                  <div className="font-bold text-ink text-base">{fileName}</div>
                  <div className="text-xs text-ink-3">Report parsed client-side • 3 findings surfaced</div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setReportState("idle")}
                className="text-xs text-ink-3 hover:text-ink transition-colors underline underline-offset-4"
              >
                Reset & Upload Another
              </button>
            </div>

            {/* Quick Metrics Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="rounded-2xl border border-line bg-surface-2 p-5">
                <div className="text-xs text-ink-3 mb-1">Estimated SCI Score</div>
                <div className="text-2xl font-black text-accent-strong font-mono">0.31 g CO₂e</div>
                <div className="text-[11px] text-ink-3 mt-1">Per 100 API Requests</div>
              </div>

              <div className="rounded-2xl border border-line bg-surface-2 p-5">
                <div className="text-xs text-ink-3 mb-1">Avoidable Idle Waste</div>
                <div className="text-2xl font-black text-amber-500 font-mono">38.4%</div>
                <div className="text-[11px] text-ink-3 mt-1">Overprovisioned container buffers</div>
              </div>

              <div className="rounded-2xl border border-line bg-surface-2 p-5">
                <div className="text-xs text-ink-3 mb-1">Confidence Rating</div>
                <div className="text-2xl font-black text-emerald-500 font-mono">High (92%)</div>
                <div className="text-[11px] text-ink-3 mt-1">AST + CloudWatch 30-day telemetry</div>
              </div>
            </div>

            <div className="text-center pt-2">
              <a
                href="#findings-preview"
                className="inline-flex items-center gap-2 text-xs font-semibold text-accent-strong hover:underline"
              >
                Scroll up to inspect interactive findings breakdown and copy AI agent prompt ↗
              </a>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
