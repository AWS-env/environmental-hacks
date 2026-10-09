"use client";

import React, { useState } from "react";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";

const READ_ONLY_POLICY_SAMPLE = `{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcoAuditReadOnlyTelemetry",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "xray:GetTraceSummaries",
        "xray:BatchGetTraces",
        "ce:GetCostAndUsage",
        "compute-optimizer:GetEC2InstanceRecommendations"
      ],
      "Resource": "*"
    }
  ]
}`;

export default function ZeroTrustGuarantee() {
  const [showPolicy, setShowPolicy] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyPolicy = () => {
    navigator.clipboard.writeText(READ_ONLY_POLICY_SAMPLE);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section id="zero-trust" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Zero-Trust Architectural Boundary
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            Do not create compute waste to detect compute waste.
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            Most observability tools demand heavy cluster agents, root privileges, or execution of your test suites.
            EcoAudit enforces a strict boundary: we ingest read-only signals, parse ASTs, and never run your code.
          </p>
        </div>

        {/* 3 Pillars Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
          {/* Card 1 */}
          <div
            data-reveal
            className="group relative rounded-2xl border border-line bg-surface p-8 backdrop-blur-md transition-all duration-300 hover:border-accent-line hover:bg-surface-2 hover:-translate-y-1 shadow-sm"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft border border-accent-line text-accent-strong mb-6">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
              </svg>
            </div>
            <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
              Zero Local Footprint
            </div>
            <h3 className="text-xl font-bold text-ink mb-3">No Local Agent or CLI</h3>
            <p className="text-ink-3 text-sm leading-relaxed mb-4">
              Never install or run a daemon on developer laptops or build runners. Traditional APMs drain battery and CPU
              cycles running passive profilers; EcoAudit operates purely via asynchronous webhooks and cloud metrics.
            </p>
            <div className="flex items-center gap-2 text-xs font-medium text-ink-2">
              <span className="text-emerald-500 font-bold">✓</span> 0% developer laptop CPU & memory consumption
            </div>
          </div>

          {/* Card 2 */}
          <div
            data-reveal
            className="group relative rounded-2xl border border-line bg-surface p-8 backdrop-blur-md transition-all duration-300 hover:border-accent-line hover:bg-surface-2 hover:-translate-y-1 shadow-sm"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft border border-accent-line text-accent-strong mb-6">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
              Read-Only Cloud Access
            </div>
            <h3 className="text-xl font-bold text-ink mb-3">STS AssumeRole Read-Only</h3>
            <p className="text-ink-3 text-sm leading-relaxed mb-4">
              Never grant write or mutate permissions to your repositories or AWS account. We authenticate via a scoped
              cross-account IAM role with pure read-only Telemetry and Cost Explorer actions.
            </p>
            <div className="flex items-center gap-2 text-xs font-medium text-ink-2">
              <span className="text-emerald-500 font-bold">✓</span> No credentials stored; temporary STS tokens only
            </div>
          </div>

          {/* Card 3 */}
          <div
            data-reveal
            className="group relative rounded-2xl border border-line bg-surface p-8 backdrop-blur-md transition-all duration-300 hover:border-accent-line hover:bg-surface-2 hover:-translate-y-1 shadow-sm"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft border border-accent-line text-accent-strong mb-6">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </div>
            <div className="text-xs font-semibold uppercase tracking-wider text-accent-strong mb-1">
              Untrusted Code Isolation
            </div>
            <h3 className="text-xl font-bold text-ink mb-3">Zero Code Execution</h3>
            <p className="text-ink-3 text-sm leading-relaxed mb-4">
              We never run your code, execute test suites, or spin up containers with your binaries. Static AST checks use
              Semgrep and Tree-sitter parsers to evaluate syntax trees without executing runtime instructions.
            </p>
            <div className="flex items-center gap-2 text-xs font-medium text-ink-2">
              <span className="text-emerald-500 font-bold">✓</span> Immune to malicious dependencies and code traps
            </div>
          </div>
        </div>

        {/* Interactive IAM Policy Drawer */}
        <div data-reveal className="rounded-2xl border border-line bg-surface-2/60 p-6 backdrop-blur-md">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <h4 className="font-semibold text-ink text-base">Verify Our Least-Privilege IAM Policy</h4>
              <p className="text-xs text-ink-3 mt-0.5">
                Inspect the exact IAM statement you attach to your read-only role. Only read metrics, zero write actions.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Magnetic>
                <button
                  type="button"
                  onClick={() => setShowPolicy((prev) => !prev)}
                  className="rounded-lg border border-line bg-surface-3 px-3.5 py-1.5 text-xs font-medium text-ink hover:bg-surface hover:text-ink-2 transition-colors"
                >
                  <RollText>{showPolicy ? "Hide Policy JSON" : "View Read-Only Policy"}</RollText>
                </button>
              </Magnetic>
              {showPolicy && (
                <Magnetic>
                  <button
                    type="button"
                    onClick={copyPolicy}
                    className="rounded-lg bg-accent px-3.5 py-1.5 text-xs font-semibold text-on-accent hover:bg-accent-hover transition-colors"
                  >
                    <RollText>{copied ? "Copied!" : "Copy JSON"}</RollText>
                  </button>
                </Magnetic>
              )}
            </div>
          </div>

          {showPolicy && (
            <div className="mt-4 overflow-hidden rounded-xl border border-line bg-page/90 p-4 font-mono text-xs text-ink-2">
              <pre className="overflow-x-auto text-[11px] leading-relaxed">{READ_ONLY_POLICY_SAMPLE}</pre>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
