"use client";

import React, { useState } from "react";

interface PipelineNode {
  id: string;
  category: string;
  name: string;
  service: string;
  role: string;
  whyGreen: string;
}

const NODES: PipelineNode[] = [
  {
    id: "edge",
    category: "Edge Ingestion",
    name: "API Gateway (HTTP API)",
    service: "Amazon API Gateway",
    role: "Validates GitHub webhook HMAC signatures and routes presigned S3 upload requests.",
    whyGreen: "Serverless HTTP API consumes up to 70% less baseline compute overhead than REST APIs.",
  },
  {
    id: "queue",
    category: "Async Buffering",
    name: "SQS Scan Queue + DLQ",
    service: "Amazon SQS",
    role: "Absorbs traffic spikes during concurrent git push bursts without dropping jobs.",
    whyGreen: "Decouples spikes from detector Lambdas so execution scales strictly on demand.",
  },
  {
    id: "orchestrator",
    category: "Step Orchestration",
    name: "Express Step Functions",
    service: "AWS Step Functions Express",
    role: "Coordinates the ingest → detect → merge → score → report workflow state machine.",
    whyGreen: "Express workflows run sub-second without polling, minimizing idle state retention.",
  },
  {
    id: "detectors",
    category: "Parallel Analysis",
    name: "4 Specialized Lambdas",
    service: "AWS Lambda (arm64 Graviton)",
    role: "Parallel evaluation: Static AST, S3 Artifact Parser, Telemetry Reader, and Estimate Engine.",
    whyGreen: "Graviton processors provide up to 60% better energy efficiency per compute cycle.",
  },
  {
    id: "hub",
    category: "Findings Bus & Store",
    name: "EventBridge & DynamoDB",
    service: "Amazon EventBridge + DynamoDB",
    role: "Publishes findings to findings-hub bus; persists findings and historical estimates.",
    whyGreen: "On-demand DynamoDB capacity scales to zero with zero idle baseline power draw.",
  },
  {
    id: "alerts",
    category: "Action & Dispatch",
    name: "Amazon SNS Alerts",
    service: "Amazon SNS",
    role: "Sends carbon budget threshold warnings directly to developer Slack channels or webhooks.",
    whyGreen: "Push-based alerts eliminate the need for client-side polling loops.",
  },
];

export default function PipelineVisualizer() {
  const [activeNode, setActiveNode] = useState(NODES[3]);

  return (
    <section id="architecture" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            AWS Single-Project Architecture (eu-north-1)
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            Engineered Serverless. Scale-to-Zero.
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            EcoAudit is deployed in AWS Stockholm (<code className="font-mono text-ink text-sm bg-surface px-1.5 py-0.5 rounded border border-line">eu-north-1</code>), running entirely on renewable energy with a 100% serverless, event-driven topology.
          </p>
        </div>

        {/* Pipeline Architecture Interactive Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start" data-reveal>
          {/* Node Cards List */}
          <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            {NODES.map((node) => {
              const isSelected = node.id === activeNode.id;
              return (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => setActiveNode(node)}
                  className={`text-left p-5 rounded-2xl border transition-all duration-300 relative ${
                    isSelected
                      ? "border-accent-line bg-surface-2 shadow-lg shadow-accent/5 ring-1 ring-accent-line"
                      : "border-line bg-surface hover:bg-surface-2 hover:border-line"
                  }`}
                >
                  <div className="flex items-center justify-between text-[11px] font-semibold text-accent-strong uppercase tracking-wider mb-1.5">
                    <span>{node.category}</span>
                    {isSelected && <span className="h-2 w-2 rounded-full bg-accent-strong animate-ping" />}
                  </div>
                  <div className="text-base font-bold text-ink mb-1">{node.name}</div>
                  <div className="text-xs text-ink-3 font-mono">{node.service}</div>
                </button>
              );
            })}
          </div>

          {/* Active Node Detail Card */}
          <div className="lg:col-span-5 rounded-3xl border border-line bg-surface p-8 backdrop-blur-xl shadow-xl space-y-6">
            <div>
              <span className="inline-block rounded-md bg-accent-soft px-2.5 py-1 text-xs font-semibold text-accent-strong border border-accent-line mb-3">
                {activeNode.category}
              </span>
              <h3 className="text-2xl font-bold text-ink mb-1">{activeNode.name}</h3>
              <p className="text-xs font-mono text-ink-3">{activeNode.service}</p>
            </div>

            <div className="space-y-4 text-xs">
              <div className="rounded-xl border border-line bg-surface-2 p-4">
                <div className="font-bold uppercase tracking-wider text-ink-3 text-[10px] mb-1.5">Pipeline Role</div>
                <div className="text-ink-2 leading-relaxed text-xs">{activeNode.role}</div>
              </div>

              <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                <div className="font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400 text-[10px] mb-1.5 flex items-center gap-1.5">
                  <span>🌱</span> Sustainability Design Choice
                </div>
                <div className="text-ink-2 leading-relaxed text-xs">{activeNode.whyGreen}</div>
              </div>
            </div>

            {/* AWS Footprint Guardrails summary */}
            <div className="pt-4 border-t border-line text-[11px] text-ink-3 space-y-1.5">
              <div className="font-semibold text-ink">Our Own Footprint Guardrails:</div>
              <div>• Hard AWS Budgets cap on project spend</div>
              <div>• Graviton (arm64) Lambdas with 15-minute execution timeouts</div>
              <div>• ECS Fargate Spot overflow (AST parsing only; never execution)</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
