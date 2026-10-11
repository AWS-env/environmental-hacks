"use client";

import React, { useState } from "react";

interface StepDetail {
  step: number;
  badge: string;
  title: string;
  description: string;
  readData: string[];
  neverRead: string[];
  uiMockTitle: string;
}

const STEPS: StepDetail[] = [
  {
    step: 1,
    badge: "Step 1: Code & Config",
    title: "Connect Git Repository",
    description: "Install our GitHub App or authorize read-only OAuth. We only parse dependency manifests, infrastructure configs, and ASTs.",
    readData: ["package.json / requirements.txt", "Dockerfile / serverless.yml", "Source code AST (Semgrep/Tree-sitter)"],
    neverRead: ["Proprietary training data", "Secrets / .env variables", "Git commit signing keys"],
    uiMockTitle: "GitHub App Read-Only Handshake",
  },
  {
    step: 2,
    badge: "Step 2: Cloud Signals",
    title: "Connect Cloud Telemetry",
    description: "Provide an AWS IAM Role ARN configured with read-only STS AssumeRole. We retrieve historical metrics without running benchmarks.",
    readData: ["CloudWatch metrics (CPU, Memory, Invocations)", "AWS X-Ray latency & downstream trace graphs", "AWS Compute Optimizer rightsizing tips"],
    neverRead: ["S3 bucket raw data contents", "DynamoDB customer table records", "Database passwords & IAM Admin roles"],
    uiMockTitle: "AWS STS AssumeRole Verification",
  },
  {
    step: 3,
    badge: "Step 3: Baseline Context",
    title: "Enter Workload & Functional Unit",
    description: "Tell EcoAudit how your software is deployed so our Software Carbon Intensity (SCI) model calculates a realistic impact range.",
    readData: ["Deployment AWS Region (e.g., eu-north-1)", "Compute architecture (e.g., Lambda arm64 / EC2 c6g)", "Functional unit R (e.g., per 1,000 HTTP requests)"],
    neverRead: ["Credit card or billing tokens", "Internal company financial accounts", "Private end-user identities"],
    uiMockTitle: "SCI Estimation Parameters",
  },
];

export default function SetupJourney() {
  const [activeStep, setActiveStep] = useState(0);
  const current = STEPS[activeStep];

  return (
    <section id="setup-journey" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Zero-Maintenance Onboarding
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            3 Minutes to Setup. Hands-Off Forever.
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            Configure once through our read-only dashboard. Once connected, every <code className="font-mono text-ink text-sm bg-surface px-1.5 py-0.5 rounded border border-line">git push</code> triggers
            lightweight delta scans with zero ongoing operational overhead.
          </p>
        </div>

        {/* Step Selector Tabs */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8" data-reveal>
          {STEPS.map((s, idx) => {
            const isActive = idx === activeStep;
            return (
              <button
                key={s.step}
                type="button"
                onClick={() => setActiveStep(idx)}
                className={`relative flex flex-col text-left p-5 rounded-2xl border transition-all duration-300 ${
                  isActive
                    ? "border-accent-line bg-surface-2 shadow-md shadow-accent/5 ring-1 ring-accent-line"
                    : "border-line bg-surface hover:bg-surface-2 hover:border-line"
                }`}
              >
                <div className="flex items-center justify-between w-full mb-2">
                  <span
                    className={`inline-flex items-center justify-center h-7 w-7 rounded-lg text-xs font-bold ${
                      isActive ? "bg-accent text-on-accent" : "bg-surface-3 text-ink-3"
                    }`}
                  >
                    0{s.step}
                  </span>
                  <span className="text-[11px] font-medium text-ink-3 tracking-wide uppercase">
                    {s.badge.split(":")[0]}
                  </span>
                </div>
                <div className="font-bold text-ink text-base mb-1">{s.title}</div>
                <div className="text-xs text-ink-3 line-clamp-2">{s.description}</div>
              </button>
            );
          })}
        </div>

        {/* Step Deep-Dive Card */}
        <div
          data-reveal
          className="rounded-3xl border border-line bg-surface p-8 md:p-10 backdrop-blur-xl transition-all duration-300"
        >
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            {/* Left Column: Details */}
            <div className="lg:col-span-6 space-y-6">
              <div>
                <span className="inline-block rounded-md bg-accent-soft px-2.5 py-1 text-xs font-semibold text-accent-strong border border-accent-line mb-3">
                  {current.badge}
                </span>
                <h3 className="text-2xl md:text-3xl font-bold text-ink mb-3">{current.title}</h3>
                <p className="text-ink-3 text-sm leading-relaxed">{current.description}</p>
              </div>

              {/* Data Ingestion Transparency */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                  <div className="flex items-center gap-2 text-xs font-bold text-emerald-600 dark:text-emerald-400 mb-2">
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    What We Ingest (Read-Only)
                  </div>
                  <ul className="space-y-1.5 text-xs text-ink-2">
                    {current.readData.map((item) => (
                      <li key={item} className="flex items-start gap-1.5">
                        <span className="text-emerald-500 mt-0.5">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-4">
                  <div className="flex items-center gap-2 text-xs font-bold text-rose-600 dark:text-rose-400 mb-2">
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                    What We Never Touch
                  </div>
                  <ul className="space-y-1.5 text-xs text-ink-2">
                    {current.neverRead.map((item) => (
                      <li key={item} className="flex items-start gap-1.5">
                        <span className="text-rose-500 mt-0.5">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            {/* Right Column: Live Mock UI */}
            <div className="lg:col-span-6">
              <div className="overflow-hidden rounded-2xl border border-line bg-page/95 shadow-xl">
                {/* Mock Window Header */}
                <div className="flex items-center justify-between border-b border-line px-4 py-3 bg-surface/50">
                  <div className="flex items-center gap-2">
                    <span className="h-3 w-3 rounded-full bg-rose-500/80" />
                    <span className="h-3 w-3 rounded-full bg-amber-500/80" />
                    <span className="h-3 w-3 rounded-full bg-emerald-500/80" />
                  </div>
                  <span className="font-mono text-xs text-ink-3 font-medium">{current.uiMockTitle}</span>
                  <span className="h-2 w-2 rounded-full bg-accent-strong animate-ping" />
                </div>

                {/* Mock Body based on step */}
                <div className="p-6 space-y-4 font-mono text-xs text-ink-2">
                  {activeStep === 0 && (
                    <div className="space-y-3">
                      <div className="flex items-center justify-between rounded-lg border border-line bg-surface p-3">
                        <div className="flex items-center gap-3">
                          <div className="h-8 w-8 rounded-lg bg-surface-3 flex items-center justify-center font-bold text-ink">
                            GH
                          </div>
                          <div>
                            <div className="font-semibold text-ink">AWS-env / environmental-hacks</div>
                            <div className="text-[11px] text-ink-3">Branch: main (default)</div>
                          </div>
                        </div>
                        <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-500 border border-emerald-500/20">
                          Connected (RO)
                        </span>
                      </div>
                      <div className="text-[11px] text-ink-3 bg-surface-2 p-3 rounded-lg border border-line leading-relaxed">
                        ✓ Webhook registered: <code>push</code>, <code>pull_request</code><br />
                        ✓ Tree-sitter AST parser initialized<br />
                        ✓ 0 local build scripts or code runners needed
                      </div>
                    </div>
                  )}

                  {activeStep === 1 && (
                    <div className="space-y-3">
                      <div className="rounded-lg border border-line bg-surface p-3 space-y-2">
                        <div className="text-[11px] text-ink-3">Target IAM Role ARN</div>
                        <div className="font-mono text-[11px] text-accent-strong break-all bg-page p-2 rounded border border-line">
                          arn:aws:iam::123456789012:role/EcoAuditReadOnlyRole
                        </div>
                        <div className="flex items-center justify-between text-[11px] pt-1">
                          <span className="text-ink-3">Trust Policy: sts:AssumeRole</span>
                          <span className="text-emerald-500 font-semibold">Active & Validated</span>
                        </div>
                      </div>
                      <div className="text-[11px] text-ink-3 bg-surface-2 p-3 rounded-lg border border-line leading-relaxed">
                        ✓ CloudWatch GetMetricData: Enabled<br />
                        ✓ AWS X-Ray TraceSummaries: Enabled<br />
                        ✓ Compute Optimizer Telemetry: Enabled
                      </div>
                    </div>
                  )}

                  {activeStep === 2 && (
                    <div className="space-y-3">
                      <div className="grid grid-cols-2 gap-2 text-[11px]">
                        <div className="p-2.5 rounded-lg border border-line bg-surface">
                          <div className="text-ink-3 mb-1">Target Region</div>
                          <div className="font-semibold text-ink">eu-north-1 (Stockholm)</div>
                        </div>
                        <div className="p-2.5 rounded-lg border border-line bg-surface">
                          <div className="text-ink-3 mb-1">Workload Profile</div>
                          <div className="font-semibold text-ink">AWS Lambda + API GW</div>
                        </div>
                      </div>
                      <div className="p-3 rounded-lg border border-line bg-surface">
                        <div className="text-ink-3 text-[11px] mb-1">Functional Unit (R)</div>
                        <div className="font-semibold text-accent-strong text-xs">
                          Per 1,000 HTTP API Invocations
                        </div>
                      </div>
                      <div className="text-[11px] text-emerald-500 bg-emerald-500/10 p-2.5 rounded-lg border border-emerald-500/20 text-center font-semibold">
                        Ready: Baseline model calibrated with GSF SCI specification
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
