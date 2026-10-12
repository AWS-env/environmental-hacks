"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { gsap } from "gsap";
import { Activity, ChevronDown, FileSearch, ShieldCheck, GitBranch, Clock3, Leaf, Pause, Play, ScanLine } from "lucide-react";
import ParticleTerrain from "@/components/ParticleTerrain";
import { ResourceChart } from "@/components/ResourceChart";
import { categories, exampleFindings, sampleAudit, workspaces } from "@/lib/overview-model";
import "./overview.css";
function AnimatedValue({ value, decimals = 0 }: { value: number; decimals?: number }) {
  const element = useRef<HTMLSpanElement>(null);
  const displayed = useRef(value);
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const counter = { value: displayed.current };
      const tween = gsap.to(counter, { value, duration: 1.2, ease: "power2.out", onUpdate: () => {
        displayed.current = counter.value;
        node.textContent = counter.value.toFixed(decimals);
      } });
      return () => { tween.kill(); };
    });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      displayed.current = value;
      node.textContent = value.toFixed(decimals);
    }
    return () => media.revert();
  }, [value, decimals]);
  return <span ref={element}>{value.toFixed(decimals)}</span>;
}

export default function OverviewPage() {
  const root = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState(0);
  const [paused, setPaused] = useState(false);
  const [workspace, setWorkspace] = useState(0);
  const [minutes, setMinutes] = useState(15);
  const [showFindings, setShowFindings] = useState(true);
  const [showCandidates, setShowCandidates] = useState(true);
  useEffect(() => {
    if (document.documentElement.hasAttribute("data-route-transition")) return;
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const context = gsap.context(() => gsap.from(".overview-heading, .monitor-panel, .overview-footer", { opacity:0, y:16, duration:.85, stagger:.065, ease:"power3.out", clearProps:"opacity,transform" }), root);
      return () => context.revert();
    });
    return () => media.revert();
  }, []);
  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => { if (!document.hidden) setFrame(value => value+1); }, 2000);
    return () => clearInterval(timer);
  }, [paused]);
  const current = sampleAudit(frame, workspace);
  const history = Array.from({length:64}, (_, i) => sampleAudit(frame-(63-i)*minutes/15,workspace));
  const findings = {label:"Findings",values:history.map(sample=>sample.findings)};
  const candidates = {label:"Candidates",values:history.map(sample=>sample.candidates),muted:true};
  return <><ParticleTerrain /><div className="overview-page" ref={root}>
    <header className="overview-topbar"><Link href="/" className="overview-brand">EcoAudit</Link></header>
    <main className="overview-content">
      <div className="overview-heading"><div><div className="overview-eyebrow">WORKSPACE / OVERVIEW</div><div className="overview-title"><h1>Audit overview</h1><span className={`audit-stream ${paused ? "is-paused" : ""}`}><i />{paused ? "PAUSED" : "READ-ONLY"}</span></div><p>Find the waste. Follow the evidence. Improve with confidence.</p></div><div className="overview-source"><label className="device-select"><GitBranch size={18}/><select aria-label="Repository" value={workspace} onChange={event=>{setWorkspace(Number(event.target.value));setFrame(0);}}>{workspaces.map((name,i)=><option value={i} key={name}>{name}</option>)}</select><ChevronDown size={14}/></label><span>Repository · configuration · existing telemetry</span></div></div>
      <div className="overview-primary">
        <section className="monitor-panel cpu-panel" aria-labelledby="findings-title"><div className="panel-head"><span className="metric-icon"><FileSearch size={24} strokeWidth={1.4}/></span><div><h2 id="findings-title">Open findings</h2><p>Patterns supported by supplied evidence</p></div><div className="metric-value"><strong data-testid="finding-count"><AnimatedValue value={current.findings}/></strong><span>{current.candidates} additional candidates</span></div></div><ResourceChart series={[findings]} frame={frame} minutes={minutes} max={50} suffix=""/><div className="cores-heading"><span>Across the taxonomy</span><span>Findings by category</span></div><div className="core-grid">{categories.map((name,i)=>{const count=Math.floor(current.findings/8)+(i<current.findings%8 ? 1 : 0);return <div className="core-item" key={name} title={`${name}: ${count} findings`}><strong>{count}</strong><div className="core-meter"><span style={{height:`${count/8*100}%`}}/></div><span>{name}</span></div>;})}</div></section>
        <section className="monitor-panel memory-panel" aria-labelledby="coverage-title"><div className="panel-head"><span className="metric-icon"><ShieldCheck size={24} strokeWidth={1.4}/></span><div><h2 id="coverage-title">Evidence coverage</h2><p>Completed / applicable checks</p></div><div className="metric-value"><strong><AnimatedValue value={current.coverage} decimals={1}/><small>%</small></strong><span>{current.completed} of 114 applicable</span></div></div><div className="memory-visual"><div className="memory-ring"><svg viewBox="0 0 140 140" aria-hidden="true"><circle cx="70" cy="70" r="55" fill="none" stroke="white" strokeOpacity=".08" strokeWidth="8"/>{Array.from({length:90},(_,i)=><circle key={i} cx={Number((70+Math.cos(i*.73)*(53+i%7)).toFixed(3))} cy={Number((70+Math.sin(i*.73)*(53+i%7)).toFixed(3))} r={i%3*.35+.3} fill="white" opacity={i%5*.12+.15}/>)}<circle className="memory-ring-fill" cx="70" cy="70" r="55" fill="none" stroke="#edf1f3" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${current.coverage/100*345.58} 345.58`} transform="rotate(-90 70 70)"/></svg><div><strong><AnimatedValue value={current.coverage}/>%</strong><span>Complete</span></div></div><ResourceChart series={[{label:"Coverage",values:history.map(sample=>sample.coverage)}]} frame={frame} minutes={minutes}/></div><div className="memory-breakdown"><span>120 requested · 6 not applicable · {current.errors} errors</span><div className="memory-bar"><i style={{width:`${current.completed/114*100}%`}}/><i style={{width:`${current.partial/114*100}%`}}/></div><div className="memory-legend"><span><i/>Complete <b>{current.completed}</b></span><span><i/>Partial <b>{current.partial}</b></span><span><i/>Unavailable <b>{current.unavailable}</b></span></div></div></section>
      </div>
      <div className="overview-secondary"><section className="monitor-panel mini-metric"><span className="metric-icon"><Clock3 size={24}/></span><div><h2>Scan duration</h2><strong><AnimatedValue value={current.duration} decimals={1}/> s<small>Read-only analysis</small></strong></div><ResourceChart series={[{label:"Seconds",values:history.slice(-24).map(sample=>sample.duration)}]} frame={frame} max={40} suffix=" s" small/></section><section className="monitor-panel mini-metric"><span className="metric-icon"><ScanLine size={24}/></span><div><h2>Evidence needed</h2><strong><AnimatedValue value={current.unavailable}/> checks<small>Missing evidence ≠ passing</small></strong></div></section><section className="monitor-panel mini-metric impact-metric"><span className="metric-icon"><Leaf size={24}/></span><div><h2>Energy / CO₂e</h2><strong>Not quantified<small>Workload, region & model inputs required</small></strong></div></section></div>
      <section className="monitor-panel utilization-panel"><div className="utilization-head"><div><h2>Finding activity</h2><p>Evidence-backed patterns and candidates shown separately</p></div><div className="chart-controls"><button className={showFindings ? "legend-toggle" : "legend-toggle is-off"} aria-pressed={showFindings} onClick={()=>{if(showCandidates||!showFindings)setShowFindings(!showFindings);}}><i/>Findings</button><button className={showCandidates ? "legend-toggle is-muted" : "legend-toggle is-off"} aria-pressed={showCandidates} onClick={()=>{if(showFindings||!showCandidates)setShowCandidates(!showCandidates);}}><i/>Candidates</button><select aria-label="Chart time window" value={minutes} onChange={event=>setMinutes(Number(event.target.value))}>{[5,15,30].map(value=><option value={value} key={value}>Last {value} minutes</option>)}</select><button className="stream-control" onClick={()=>setPaused(!paused)} aria-label={paused ? "Resume updates" : "Pause updates"}>{paused ? <Play size={14}/> : <Pause size={14}/>}</button></div></div><ResourceChart series={[...(showFindings ? [findings] : []),...(showCandidates ? [candidates] : [])]} frame={frame} minutes={minutes} max={50} suffix=""/></section>
      <section className="monitor-panel processes-panel findings-panel"><div className="processes-head"><div><h2>Findings to investigate</h2><p>Check · location · evidence · confidence</p></div><span><Activity size={12}/>Confidence describes detection, not savings</span></div><div className="process-table-scroll"><table><thead><tr><th>Check</th><th>Pattern</th><th>Evidence</th><th>Confidence</th><th>State</th><th>Location</th></tr></thead><tbody>{exampleFindings.map(finding=><tr key={finding.check}><td><FileSearch size={14}/><strong>{finding.check}</strong></td><td title={finding.title}>{finding.title}</td><td>{finding.evidence}</td><td>{finding.confidence}</td><td><span className="process-status"><i/>{finding.state}</span></td><td>{finding.location}</td></tr>)}</tbody></table></div></section>
      <footer className="overview-footer"><span><i/>Read-only · your code never executes here</span><span>Illustrative workspace · generated figures, no device access</span></footer>
    </main>
  </div></>;
}
