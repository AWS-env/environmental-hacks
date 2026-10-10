"use client";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { pipelineLayout, PIPELINE_EDGES, PIPELINE_STEPS } from "./pipelineModel";

/** `status` is the real scan state; the moving stages above it are illustrative only. */
export interface PipelineStatus { title: string; detail: string; meta: string }

export default function ScanPipeline({ step, repository, status, onBack }: { step: number; repository: string; status: PipelineStatus; onBack: () => void }) {
  const [viewport, setViewport] = useState({ width: 1440, height: 1000 });
  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    resize(); window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const { nodes, size, compact } = pipelineLayout(viewport.width, viewport.height);
  const stage = PIPELINE_STEPS[Math.max(step, 0) % PIPELINE_STEPS.length];
  const source = ["Repository source", "Collector artifact", "AWS telemetry"][stage.routeIndex];
  return (
    <section className="scan-pipeline" aria-label="Scan in progress (the pipeline diagram is illustrative)">
      <div className="scan-heading">
        <div className="scan-eyebrow">PIPELINE PREVIEW <span>ILLUSTRATIVE</span></div>
        <h1>Commit → Scan → Measure → Optimize</h1>
        <p>{repository.replace("https://github.com/", "")} <span>· {source}</span></p>
      </div>
      <svg className="scan-connections" width={viewport.width} height={viewport.height} aria-hidden="true">
        {PIPELINE_EDGES.map(([from, to], i) => {
          const a = nodes.find(node => node.id === from)!, b = nodes.find(node => node.id === to)!;
          const highlighted = step >= 0 && stage.id === from && stage.next === to;
          const bend = Math.abs(a.py - b.py) > 30;
          const d = bend ? `M ${a.px} ${a.py} C ${a.px} ${(a.py + b.py) / 2} ${b.px} ${(a.py + b.py) / 2} ${b.px} ${b.py}` : `M ${a.px} ${a.py} L ${b.px} ${b.py}`;
          return <path key={i} d={d} fill="none" stroke={highlighted ? "rgba(255,255,255,.7)" : "rgba(255,255,255,.12)"} strokeWidth={highlighted ? 1.4 : .8} />;
        })}
      </svg>
      {nodes.map(node => (
        <div key={node.id} className={`scan-node-label ${step >= 0 && stage.id === node.id ? "is-active" : ""}`}
          style={{ left: node.px, top: node.py + size * .9, width: compact ? 90 : 166 }}>
          <strong>{node.title}</strong><span>{node.detail}</span>
          <svg className="scan-fallback-cube" viewBox="0 0 100 100" aria-hidden="true"><path d="M20 30H70V80H20Z M20 30L40 10H90V60L70 80 M70 30L90 10 M20 80L40 60H90 M40 10V60" fill="none" stroke="currentColor" /></svg>
        </div>
      ))}
      <div className="scan-status" role="status" aria-live="polite">
        <span className="scan-status-dot" />
        <div><strong>{status.title}</strong>
          <p>{status.detail}</p></div>
        <span className="scan-step-count">{status.meta}</span>
      </div>
      <button className="scan-back" onClick={onBack}><ArrowLeft size={14} /> Back to repository</button>
      <p className="scan-caption">Read-only analysis · client code is never executed here</p>
    </section>
  );
}
