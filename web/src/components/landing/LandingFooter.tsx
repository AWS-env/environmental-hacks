"use client";

import React from "react";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";

export default function LandingFooter() {
  return (
    <footer className="border-t border-line/60 bg-page py-16 px-6">
      <div className="mx-auto max-w-7xl space-y-12">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8 pb-12 border-b border-line">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 to-teal-600 shadow-md">
                <svg className="h-5 w-5 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <span className="font-bold text-lg tracking-tight text-ink">EcoAudit</span>
              <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-semibold text-accent-strong border border-accent-line">
                eu-north-1 (Stockholm)
              </span>
            </div>
            <p className="text-xs text-ink-3 max-w-md">
              A Read-Only Software Sustainability & Compute Waste Auditor for AWS Environmental Hacks.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <Magnetic>
              <a
                href="https://github.com/AWS-env/environmental-hacks"
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border border-line bg-surface-2 px-4 py-2 text-xs font-semibold text-ink hover:bg-surface-3 transition-colors"
              >
                <RollText>GitHub Repository</RollText>
              </a>
            </Magnetic>
            <Magnetic>
              <a
                href="#audit"
                className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-on-accent hover:bg-accent-hover transition-colors"
              >
                <RollText>Test Sample Report</RollText>
              </a>
            </Magnetic>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-ink-3">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            <span>Hosted 100% serverless in AWS Stockholm with scale-to-zero compute.</span>
          </div>
          <div>
            <span>Conforms to GSF Software Carbon Intensity (SCI) & ISO 14040/14044 LCA principles.</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
