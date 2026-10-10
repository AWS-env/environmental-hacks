"use client";

import { useEffect, useRef, useState } from "react";
import { gsap } from "gsap";
import { Activity, ArrowDown, ArrowUp, Bell, Box, ChevronDown, Cpu, Database, MemoryStick, Monitor, Pause, Play, Thermometer, Globe, Code2, MessageSquare, Terminal, Folder } from "lucide-react";
import ParticleTerrain from "@/components/ParticleTerrain";
import { ResourceChart, sampleResource } from "@/components/ResourceChart";
import "./overview.css";

const devices = ["DEMO-WORKSTATION", "DEMO-LAPTOP", "DEMO-CI-RUNNER"];
const processes = [
  {name:"chrome.exe", description:"Google Chrome · 18 processes", icon:Globe, share:.44, color:"#c8d7a7"},
  {name:"Code.exe", description:"Visual Studio Code", icon:Code2, share:.21, color:"#85b7ce"},
  {name:"Discord.exe", description:"Discord", icon:MessageSquare, share:.09, color:"#a6a4db"},
  {name:"node.exe", description:"Local development server", icon:Terminal, share:.06, color:"#a7c7a2"},
  {name:"explorer.exe", description:"Windows Explorer", icon:Folder, share:.04, color:"#d6c088"},
];

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
  const [device, setDevice] = useState(0);
  const [minutes, setMinutes] = useState(15);
  const [showCPU, setShowCPU] = useState(true);
  const [showMemory, setShowMemory] = useState(true);
  useEffect(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const context = gsap.context(() => {
        gsap.from(".overview-heading, .monitor-panel, .overview-footer", {
          opacity: 0, y: 16, duration: .85, stagger: .065, ease: "power3.out", clearProps: "opacity,transform",
        });
      }, root);
      return () => context.revert();
    });
    return () => media.revert();
  }, []);
  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => { if (!document.hidden) setFrame(value => value+1+Math.floor(Math.random()*3)); }, 2000);
    return () => clearInterval(timer);
  }, [paused]);
  const current = sampleResource(frame, device);
  const previous = sampleResource(frame-4, device);
  const cpu = Math.round(current.cpu), memory = Math.round(current.memory);
  const used = current.memory/100*16, cached = Math.min((16-used)*.65, 3.2), available = 16-used-cached;
  const history = Array.from({length:64}, (_, i) => sampleResource(frame-(63-i)*minutes/15,device));
  const cpuSeries = {label:"CPU", values:history.map(sample=>sample.cpu)};
  const memorySeries = {label:"Memory", values:history.map(sample=>sample.memory), muted:true};
  const diff = Math.round(current.cpu-previous.cpu);
  const spark = (field:"cpu"|"memory"|"temperature"|"processes") => [{label:field, values:history.slice(-24).map(sample=>field==="memory" ? 100-sample[field] : field==="processes" ? sample[field]-140 : sample[field])}];
  const trendIcon = diff < 0 ? <ArrowDown size={13} /> : <ArrowUp size={13} />;
  return (
    <>
      <ParticleTerrain />
      <div className="overview-page" ref={root}>
        <header className="overview-topbar"><a href="/home" className="overview-brand">Kimi</a><div className="overview-account"><span className="workspace-pill"><i />Personal<ChevronDown size={12} /></span><span className="overview-bell" aria-label="Notifications"><Bell size={15} /></span><span className="overview-avatar">S</span></div></header>
        <main className="overview-content">
          <div className="overview-heading">
            <div><div className="overview-eyebrow">WORKSPACE / OVERVIEW</div><div className="overview-title"><h1>Resource Monitor</h1><span className={`demo-stream ${paused ? "is-paused" : ""}`}><i />{paused ? "PAUSED" : "DEMO STREAM"}</span></div><p>A little signal in the noise. Generated data, beautifully in motion.</p></div>
            <div className="overview-source"><label className="device-select"><Monitor size={18} /><select aria-label="Demo device" value={device} onChange={event=>{setDevice(Number(event.target.value));setFrame(0);}}>{devices.map((name,i)=><option value={i} key={name}>{name}</option>)}</select><ChevronDown size={14} /></label><span>Generated metrics · refreshes every 2 seconds</span></div>
          </div>
          <div className="overview-primary">
            <section className="monitor-panel cpu-panel" aria-labelledby="cpu-title">
              <div className="panel-head"><span className="metric-icon"><Cpu size={25} strokeWidth={1.4} /></span><div><h2 id="cpu-title">CPU Usage</h2><p>Total processor utilization</p></div><div className="metric-value"><strong data-testid="cpu-value"><AnimatedValue value={cpu} /><small>%</small></strong><span>{trendIcon}{Math.abs(diff)}% from last minute</span></div></div>
              <ResourceChart series={[cpuSeries]} frame={frame} minutes={minutes} />
              <div className="cores-heading"><span>Per-core usage</span><span>8 logical processors</span></div>
              <div className="core-grid">{Array.from({length:8}, (_,i)=>{const value=Math.max(1,Math.min(100,Math.round(cpu+Math.sin(frame*.31+i*2.1)*9)));return <div className="core-item" key={i}><strong>{value}%</strong><div className="core-meter"><span style={{height:`${value}%`}} /></div><span>C{i}</span></div>;})}</div>
            </section>
            <section className="monitor-panel memory-panel" aria-labelledby="memory-title">
              <div className="panel-head"><span className="metric-icon"><MemoryStick size={25} strokeWidth={1.4} /></span><div><h2 id="memory-title">Memory Usage</h2><p>System memory utilization</p></div><div className="metric-value"><strong><AnimatedValue value={memory} /><small>%</small></strong><span>{used.toFixed(1)} GB / 16 GB</span></div></div>
              <div className="memory-visual"><div className="memory-ring"><svg viewBox="0 0 140 140" aria-hidden="true"><circle cx="70" cy="70" r="55" fill="none" stroke="white" strokeOpacity=".08" strokeWidth="8" />{Array.from({length:90},(_,i)=><circle key={i} cx={Number((70+Math.cos(i*.73)*(53+i%7)).toFixed(3))} cy={Number((70+Math.sin(i*.73)*(53+i%7)).toFixed(3))} r={i%3*.35+.3} fill="white" opacity={i%5*.12+.15} />)}<circle className="memory-ring-fill" cx="70" cy="70" r="55" fill="none" stroke="#edf1f3" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${current.memory/100*345.58} 345.58`} transform="rotate(-90 70 70)" /></svg><div><strong><AnimatedValue value={memory} />%</strong><span>Used</span></div></div><ResourceChart series={[{label:"Memory",values:history.map(sample=>sample.memory/100*16)}]} frame={frame} minutes={minutes} max={16} suffix=" GB" /></div>
              <div className="memory-breakdown"><span>Memory breakdown</span><div className="memory-bar"><i style={{width:`${current.memory}%`}} /><i style={{width:`${cached/16*100}%`}} /></div><div className="memory-legend"><span><i />Used <b>{used.toFixed(1)} GB</b></span><span><i />Cached <b>{cached.toFixed(1)} GB</b></span><span><i />Available <b>{available.toFixed(1)} GB</b></span></div></div>
            </section>
          </div>
          <div className="overview-secondary">
            <section className="monitor-panel mini-metric"><span className="metric-icon"><Thermometer size={24} strokeWidth={1.4} /></span><div><h2>CPU Temperature</h2><strong><AnimatedValue value={current.temperature} />°C <small>Within normal range</small></strong></div><ResourceChart series={spark("temperature")} frame={frame} max={100} small /></section>
            <section className="monitor-panel mini-metric"><span className="metric-icon"><Box size={24} strokeWidth={1.4} /></span><div><h2>Active Processes</h2><strong><AnimatedValue value={current.processes} /> <small>Across this device</small></strong></div><ResourceChart series={spark("processes")} frame={frame} max={80} small /></section>
            <section className="monitor-panel mini-metric"><span className="metric-icon"><Database size={24} strokeWidth={1.4} /></span><div><h2>Memory Available</h2><strong><AnimatedValue value={available} decimals={1} /> GB <small>{Math.round(available/16*100)}% free</small></strong></div><ResourceChart series={spark("memory")} frame={frame} max={100} small /></section>
          </div>
          <section className="monitor-panel utilization-panel">
            <div className="utilization-head"><div><h2>Resource utilization</h2><p>CPU and memory usage over time</p></div><div className="chart-controls"><button className={showCPU ? "legend-toggle" : "legend-toggle is-off"} aria-pressed={showCPU} onClick={()=>{if(showMemory||!showCPU)setShowCPU(!showCPU);}}><i />CPU Usage</button><button className={showMemory ? "legend-toggle is-muted" : "legend-toggle is-off"} aria-pressed={showMemory} onClick={()=>{if(showCPU||!showMemory)setShowMemory(!showMemory);}}><i />Memory Usage</button><select aria-label="Chart time window" value={minutes} onChange={event=>setMinutes(Number(event.target.value))}>{[5,15,30].map(value=><option value={value} key={value}>Last {value} minutes</option>)}</select><button className="stream-control" onClick={()=>setPaused(!paused)} aria-label={paused ? "Resume demo stream" : "Pause demo stream"}>{paused ? <Play size={14} /> : <Pause size={14} />}</button></div></div>
            <ResourceChart series={[...(showCPU ? [cpuSeries] : []),...(showMemory ? [memorySeries] : [])]} frame={frame} minutes={minutes} />
          </section>
          <section className="monitor-panel processes-panel"><div className="processes-head"><div><h2>Running processes</h2><p>Top processes by resource usage</p></div><span><Activity size={12} />{paused ? "Stream paused" : "Updating every 2s"}</span></div><div className="process-table-scroll"><table><thead><tr><th>Process name</th><th>CPU</th><th>Memory</th><th>Status</th><th>Uptime</th><th>Description</th></tr></thead><tbody>{processes.map((process,i)=>{const value=current.cpu*process.share;const Icon=process.icon;return <tr key={process.name}><td><Icon size={16} style={{color:process.color}} /><strong>{process.name}</strong></td><td><span>{value.toFixed(1)}%</span><div className="table-meter"><i style={{width:`${value*2}%`}} /></div></td><td>{(used*process.share*.65).toFixed(2)} GB</td><td><span className="process-status"><i />Running</span></td><td className="uptime">00:{String(12+i*3+Math.floor(frame/30)).padStart(2,"0")}:{String(frame*2%60).padStart(2,"0")}</td><td>{process.description}</td></tr>;})}</tbody></table></div></section>
          <footer className="overview-footer"><span><i />All systems looking good</span><span>Simulated device data · no local system access</span></footer>
        </main>
      </div>
    </>
  );
}




