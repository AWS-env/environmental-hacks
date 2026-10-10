"use client";

import React, { useState } from "react";

export default function TwoClocksEngine() {
  const [activeTab, setActiveTab] = useState<"push" | "periodic" | "ci">("push");

  return (
    <section id="two-clocks" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Steady-State Mechanics
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            Two Clocks. Zero Wasted Compute.
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            Finding scans run on a fast event-driven clock, while environmental estimates run on a periodic and trigger clock with a strict materiality gate. We never recompute estimates if code deltas didn’t shift your profile.
          </p>
        </div>

        {/* Tab Selection */}
        <div className="flex flex-wrap gap-3 mb-10" data-reveal>
          <button
            type="button"
            onClick={() => setActiveTab("push")}
            className={`flex items-center gap-2.5 rounded-xl px-5 py-3 text-xs font-bold transition-all border ${
              activeTab === "push"
                ? "bg-accent text-on-accent border-accent shadow-md shadow-accent/20"
                : "bg-surface text-ink-2 border-line hover:bg-surface-2 hover:text-ink"
            }`}
          >
            <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
            Clock 1: Every git push (Per-Push Webhook)
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("periodic")}
            className={`flex items-center gap-2.5 rounded-xl px-5 py-3 text-xs font-bold transition-all border ${
              activeTab === "periodic"
                ? "bg-accent text-on-accent border-accent shadow-md shadow-accent/20"
                : "bg-surface text-ink-2 border-line hover:bg-surface-2 hover:text-ink"
            }`}
          >
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            Clock 2: Nightly & Periodic (EventBridge)
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("ci")}
            className={`flex items-center gap-2.5 rounded-xl px-5 py-3 text-xs font-bold transition-all border ${
              activeTab === "ci"
                ? "bg-accent text-on-accent border-accent shadow-md shadow-accent/20"
                : "bg-surface text-ink-2 border-line hover:bg-surface-2 hover:text-ink"
            }`}
          >
            <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
            Optional: 1-Line CI Profiler Collector
          </button>
        </div>

        {/* Content Box */}
        <div
          data-reveal
          className="rounded-3xl border border-line bg-surface p-8 md:p-12 backdrop-blur-xl shadow-xl transition-all duration-300"
        >
          {activeTab === "push" && (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
              <div className="lg:col-span-7 space-y-6">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
                    Event-Driven Scan Loop
                  </div>
                  <h3 className="text-2xl md:text-3xl font-bold text-ink mb-3">
                    Incremental Diff Scanning with Materiality Gate
                  </h3>
                  <p className="text-sm text-ink-3 leading-relaxed">
                    When you push a commit or open a pull request, GitHub sends an HMAC-authenticated webhook to our API Gateway.
                    Step Functions parses only the touched files and dependencies.
                  </p>
                </div>

                <div className="space-y-3 font-mono text-xs">
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">01.</span>
                    <div>
                      <strong className="text-ink font-sans">Delta-Only AST Parsing:</strong> We skip clean files and only evaluate modified syntax blocks via Semgrep and Tree-sitter.
                    </div>
                  </div>
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">02.</span>
                    <div>
                      <strong className="text-ink font-sans">The Materiality Gate:</strong> If your commit touched documentation, assets, or formatting, we skip running emissions engines and report <em>&quot;No material change&quot;</em>.
                    </div>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-5 rounded-2xl border border-line bg-page/90 p-5 font-mono text-xs space-y-3 shadow-inner">
                <div className="text-ink-3 text-[11px] pb-2 border-b border-line flex items-center justify-between">
                  <span>WEBHOOK DISPATCH</span>
                  <span className="text-emerald-500 font-semibold">200 OK (18ms)</span>
                </div>
                <div className="text-ink-2 space-y-1 text-[11px]">
                  <div>→ git push main [commit 7f3a9d]</div>
                  <div>→ SQS scan-queue: enqueued job</div>
                  <div>→ Step Functions: delta AST scan</div>
                  <div className="text-accent-strong">→ Materiality Check: NO_SIGNIFICANT_DELTA</div>
                  <div className="text-ink-3">→ Skipped redundant SCI recalculation</div>
                  <div className="text-emerald-500 font-semibold pt-1">✓ Saved 100% downstream compute</div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "periodic" && (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
              <div className="lg:col-span-7 space-y-6">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
                    Scheduled Baseline Sync
                  </div>
                  <h3 className="text-2xl md:text-3xl font-bold text-ink mb-3">
                    Nightly / Weekly Calibration & Carbon Budget Alerts
                  </h3>
                  <p className="text-sm text-ink-3 leading-relaxed">
                    Code doesn’t change every hour, but traffic and carbon intensity do. EventBridge Scheduler periodically awakens our Estimate Engine to ingest refreshed CloudWatch telemetry and local grid emission factors.
                  </p>
                </div>

                <div className="space-y-3 font-mono text-xs">
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">01.</span>
                    <div>
                      <strong className="text-ink font-sans">Telemetry Baselines:</strong> Refreshes 95th-percentile execution times, invocation counts, and memory watermarks.
                    </div>
                  </div>
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">02.</span>
                    <div>
                      <strong className="text-ink font-sans">Amazon SNS Carbon Budget Alerts:</strong> Dispatches real-time alerts to Slack, webhooks, or email if your service crosses its agreed emissions limit.
                    </div>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-5 rounded-2xl border border-line bg-page/90 p-5 font-mono text-xs space-y-3 shadow-inner">
                <div className="text-ink-3 text-[11px] pb-2 border-b border-line flex items-center justify-between">
                  <span>EVENTBRIDGE CRON</span>
                  <span className="text-cyan-500 font-semibold">0 0 * * * (Nightly)</span>
                </div>
                <div className="text-ink-2 space-y-1 text-[11px]">
                  <div>→ Ingested CloudWatch: 1.2M invocations</div>
                  <div>→ Recomputed SCI: 0.24 g CO₂e / req</div>
                  <div>→ Carbon Budget: 85% of monthly threshold</div>
                  <div className="text-amber-500">⚠ Warning: Approaching carbon limit</div>
                  <div className="text-ink-3">→ SNS Topic: arn:aws:sns:...:budget-alert</div>
                  <div className="text-emerald-500 font-semibold pt-1">✓ Alert dispatched to #devops-alerts</div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "ci" && (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
              <div className="lg:col-span-7 space-y-6">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
                    Optional Deep Profiling
                  </div>
                  <h3 className="text-2xl md:text-3xl font-bold text-ink mb-3">
                    One-Line CI Collector for Runtime Confirmation
                  </h3>
                  <p className="text-sm text-ink-3 leading-relaxed">
                    Want runtime proof without giving us access to run your code? Add one line to your GitHub Actions or test runner. Your runner profiles your code locally and uploads a lightweight JSON artifact to a presigned S3 URL.
                  </p>
                </div>

                <div className="space-y-3 font-mono text-xs">
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">01.</span>
                    <div>
                      <strong className="text-ink font-sans">Your Runner Executes:</strong> Tools like py-spy, memray, Lighthouse, or EXPLAIN dumps execute inside your secure environment.
                    </div>
                  </div>
                  <div className="flex items-start gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
                    <span className="text-accent-strong font-bold">02.</span>
                    <div>
                      <strong className="text-ink font-sans">Presigned S3 Ingest:</strong> Uploads pure JSON metrics. Our Artifact Parser Lambda reads the metrics and marks static findings as <em>Runtime-Confirmed</em>.
                    </div>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-5 rounded-2xl border border-line bg-page/90 p-5 font-mono text-xs space-y-3 shadow-inner">
                <div className="text-ink-3 text-[11px] pb-2 border-b border-line flex items-center justify-between">
                  <span>GITHUB ACTIONS STEP</span>
                  <span className="text-emerald-500 font-semibold">1 Line Addition</span>
                </div>
                <div className="text-ink-2 space-y-2 text-[11px]">
                  <div className="bg-surface p-2.5 rounded border border-line text-accent-strong">
                    - run: npx @ecoaudit/collect --profile
                  </div>
                  <div className="text-ink-3 text-[10px]">
                    # Presigned PUT to S3 artifact bucket<br />
                    # Zero credentials required on your runner<br />
                    # We parse data, never execute code
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
