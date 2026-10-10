"use client";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { pipelineLayout, PIPELINE_EDGES, PIPELINE_STEPS, PIPELINE_ROUTE_LABELS } from "./pipelineModel";

/** The architecture animation is illustrative; status comes from the real scan API. */
export interface PipelineStatus { title: string; detail: string; meta: string }

export default function ScanPipeline({ step, activeSteps = [], repository, status, onBack }: { step: number; activeSteps?: number[]; repository: string; status: PipelineStatus; onBack: () => void }) {
  const [viewport, setViewport] = useState({ width: 1440, height: 1000 });
  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    resize(); window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const { nodes, size, compact } = pipelineLayout(viewport.width, viewport.height);
  const stage = PIPELINE_STEPS[Math.max(step, 0) % PIPELINE_STEPS.length];
  const source = PIPELINE_ROUTE_LABELS[stage.routeIndex];
  const activeStages = activeSteps.map(index => PIPELINE_STEPS[index]);
  return (
    <section className="scan-pipeline" aria-label="Illustrative analysis pipeline">
      <div className="scan-heading">
        <div className="scan-eyebrow">PIPELINE PREVIEW <span>ILLUSTRATIVE</span></div>
        <h1>Browser → Edge → Ingest → Analyze → Report</h1>
        <p>{repository.replace("https://github.com/", "")} <span>· {source}</span></p>
      </div>
      <svg className="scan-connections" width={viewport.width} height={viewport.height} aria-hidden="true">
        <defs>
          <marker id="pipeline-direction" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M1 1L7 4L1 7" fill="none" stroke="#aab8c0" strokeWidth="1" />
          </marker>
        </defs>
        {PIPELINE_EDGES.map(([from, to], i) => {
          const a = nodes.find(node => node.id === from)!, b = nodes.find(node => node.id === to)!;
          const highlighted = activeStages.some(stage => stage.id === from && stage.next === to);
          const bend = Math.abs(a.py - b.py) > 30;
          const d = bend ? `M ${a.px} ${a.py} C ${a.px} ${(a.py + b.py) / 2} ${b.px} ${(a.py + b.py) / 2} ${b.px} ${b.py}` : `M ${a.px} ${a.py} L ${b.px} ${b.py}`;
          return <path key={i} d={d} fill="none" markerEnd="url(#pipeline-direction)" stroke={highlighted ? "rgba(255,255,255,.7)" : "rgba(255,255,255,.12)"} strokeWidth={highlighted ? 1.4 : .8}><title>{`${a.title} → ${b.title}`}</title></path>;
        })}
      </svg>
      {nodes.map(node => (
        <div key={node.id} className={`scan-node-label ${activeStages.some(stage => stage.id === node.id) ? "is-active" : ""}`}
          style={{ left: node.px, top: node.py + size * .9, width: compact ? 90 : 150 }}>
          <strong>{node.title}</strong><span>{node.detail}</span>
          <svg className="scan-fallback-cube" viewBox="0 0 100 100" aria-hidden="true"><path d="M20 30H70V80H20Z M20 30L40 10H90V60L70 80 M70 30L90 10 M20 80L40 60H90 M40 10V60" fill="none" stroke="currentColor" /></svg>
        </div>
      ))}
      <div className="scan-status" role="status" aria-live="polite">
        <span className="scan-status-dot" />
        <div><strong>{status.title}</strong><p>{status.detail}</p></div>
        <span className="scan-step-count">{status.meta}</span>
      </div>
      <button className="scan-back" onClick={onBack}><ArrowLeft size={14} /> Back to repository</button>
      <p className="scan-caption">Architecture preview · client code is never executed here</p>
    </section>
  );
}
